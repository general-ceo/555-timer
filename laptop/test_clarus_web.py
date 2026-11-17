"""Tests for clarus_web.py: real HTTP requests against a fake board (no hardware needed).

Run: python -m unittest test_clarus_web.py -v
"""
import http.cookiejar
import json
import secrets
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

import clarus_login as cl
import clarus_web as web


class FakeBoard:
    """Answers each CODE with whatever `answer(code)` returns: a list of replies."""

    def __init__(self, answer):
        self.answer = answer
        self.sent = []
        self.replies = []
        self.closed = False

    def send(self, line):
        self.sent.append(line)
        if line.startswith("CODE "):
            self.replies = ["STARTED"] + self.answer(line[5:])

    def read_line(self, timeout_s):
        if self.replies:
            reply = self.replies.pop(0)
            return reply() if callable(reply) else reply
        return None

    def discard_input(self):
        pass

    def close(self):
        self.closed = True


def types_it_right(code):
    return [f"TYPED {code[:2]}", f"ENTRY {code}"]


def types_it_wrong(code):
    return ["ENTRY 0000" if code != "0000" else "ENTRY 1111"]


class QuietHandler(web.Handler):
    def log_message(self, fmt, *args):
        pass


class WebLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users_path = Path(self.tmp.name) / "users.json"
        salt = secrets.token_bytes(16)
        users = {"logan": {"salt": salt.hex(), "hash": cl.hash_password("correct horse", salt).hex()}}
        self.users_path.write_text(json.dumps(users), encoding="utf-8")
        self.boards = []
        self.board_answer = types_it_right

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        self.tmp.cleanup()

    def start(self, board_present=True):
        def open_dev():
            if not board_present:
                raise cl.DeviceError("CLARUS hardware not found")
            board = FakeBoard(self.board_answer)
            self.boards.append(board)
            return board

        self.app = web.App(open_dev, users_path=self.users_path, sleep=lambda _s: None)
        self.server = web.make_server(self.app, 0)
        self.server.RequestHandlerClass = QuietHandler
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path):
        with self.browser.open(self.base + path) as r:
            return r.geturl(), r.read().decode()

    def sign_in(self, name="logan", password="correct horse"):
        data = urllib.parse.urlencode({"username": name, "password": password}).encode()
        with self.browser.open(self.base + "/login", data) as r:
            return r.geturl(), r.read().decode()

    def final_status(self):
        for _ in range(200):
            status = json.loads(self.get("/status")[1])
            if status["stage"] != "code":
                return status
            time.sleep(0.01)
        self.fail("code step never finished")

    def test_right_code_grants_access(self):
        self.start()
        url, _ = self.sign_in()
        self.assertFalse(url.endswith("/login"))  # /verify, or /welcome if the fake board was quick
        self.assertEqual(self.final_status()["stage"], "granted")
        url, body = self.get("/")
        self.assertTrue(url.endswith("/welcome"))
        self.assertIn("Welcome, logan.", body)
        self.assertEqual(self.boards[0].sent[-1], "OK")

    def test_sign_out_ends_the_session(self):
        self.start()
        self.sign_in()
        self.final_status()
        with self.browser.open(self.base + "/logout", b"") as r:
            self.assertTrue(r.geturl().endswith("/"))
        url, body = self.get("/welcome")
        self.assertTrue(url.endswith("/"))
        self.assertIn("Sign in", body)

    def test_welcome_needs_a_session(self):
        self.start()
        url, _ = self.get("/welcome")
        self.assertTrue(url.endswith("/"))

    def test_welcome_needs_the_code_too(self):
        self.board_answer = lambda code: ["TIMEOUT"]
        self.start()
        self.sign_in()
        self.assertEqual(self.final_status()["stage"], "denied")
        url, _ = self.get("/welcome")
        self.assertTrue(url.endswith("/"))

    def test_three_wrong_passwords_lock_sign_in(self):
        self.start()
        for _ in range(2):
            self.assertIn("Wrong username or password.", self.sign_in(password="nope")[1])
        self.assertIn("Locked for 60 seconds", self.sign_in(password="nope")[1])
        url, body = self.sign_in()  # right password, still locked
        self.assertTrue(url.endswith("/login"))
        self.assertIn("Locked. Try again in", body)
        self.assertEqual(self.boards, [])  # the board was never asked for a code

    def test_three_wrong_codes_deny_and_lock(self):
        self.board_answer = types_it_wrong
        self.start()
        self.sign_in()
        status = self.final_status()
        self.assertEqual(status["stage"], "denied")
        self.assertIn("Too many failed codes", status["message"])
        sent = self.boards[0].sent
        self.assertEqual(sent.count("BAD"), 3)
        self.assertNotIn("OK", sent)
        self.assertEqual(sent[-1], "CANCEL")
        self.assertIn("Locked. Try again in", self.sign_in()[1])

    def test_missing_board_means_access_denied(self):
        self.start(board_present=False)
        url, body = self.sign_in()
        self.assertTrue(url.endswith("/login"))
        self.assertIn("CLARUS hardware not found. Access denied.", body)

    def test_silent_board_denies_and_is_looked_for_again(self):
        self.board_answer = lambda code: []  # STARTED, then nothing
        self.start()
        self.sign_in()
        status = self.final_status()
        self.assertEqual(status["stage"], "denied")
        self.assertIn("Lost contact", status["message"])
        self.assertTrue(self.boards[0].closed)
        self.assertIsNone(self.app.dev)
        self.board_answer = types_it_right
        self.get("/")
        self.sign_in()
        self.assertEqual(self.final_status()["stage"], "granted")
        self.assertEqual(len(self.boards), 2)

    def test_second_login_waits_while_board_is_busy(self):
        release = threading.Event()
        self.board_answer = lambda code: [lambda: release.wait(5) and f"ENTRY {code}"]
        self.start()
        self.sign_in()
        other = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        data = urllib.parse.urlencode({"username": "logan", "password": "correct horse"}).encode()
        with other.open(self.base + "/login", data) as r:
            self.assertIn("busy", r.read().decode())
        release.set()
        self.assertEqual(self.final_status()["stage"], "granted")

    def test_code_is_hidden_unless_show_code(self):
        release = threading.Event()
        self.board_answer = lambda code: [lambda: release.wait(5) and f"ENTRY {code}"]
        self.start()
        self.sign_in()
        time.sleep(0.05)
        self.assertIsNone(json.loads(self.get("/status")[1])["code"])
        release.set()

    def test_cross_site_post_is_refused(self):
        self.start()
        req = urllib.request.Request(self.base + "/login", b"username=logan&password=x",
                                     headers={"Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.browser.open(req)
        self.assertEqual(e.exception.code, 403)

    def test_no_users_message(self):
        self.users_path.write_text("{}", encoding="utf-8")
        self.start()
        self.assertIn("--add-user", self.get("/")[1])


if __name__ == "__main__":
    unittest.main()
