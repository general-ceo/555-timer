"""Tests for clarus_login.py using a fake board (no hardware needed).

Run: python -m unittest test_clarus_login.py -v
"""
import contextlib
import io
import json
import secrets
import tempfile
import unittest
from pathlib import Path

import clarus_login as cl


class FakeDevice:
    """Scripted board replies; records what the laptop sends."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, line):
        self.sent.append(line)

    def read_line(self, timeout_s):
        return self.replies.pop(0) if self.replies else None

    def discard_input(self):
        pass


class FakeSerial:
    """Fake serial port that returns one byte chunk per read."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.written = b""

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def codes(*values):
    it = iter(values)
    return lambda: next(it)


def run_quietly(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def no_sleep(_seconds):
    pass


class SecondFactorTests(unittest.TestCase):
    def factor(self, dev, *code_values):
        return run_quietly(cl.second_factor, dev, sleep=no_sleep, make_code=codes(*code_values))

    def test_live_typing_is_shown_and_is_not_a_failure(self):
        dev = FakeDevice(["STARTED", "TYPED 4", "TYPED 48", "TYPED", "TYPED 4", "TYPED 48",
                          "TYPED 482", "TYPED 4821", "ENTRY 4821"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            granted = cl.second_factor(dev, sleep=no_sleep, make_code=codes("4821"))
        self.assertTrue(granted)
        self.assertEqual(dev.sent, ["CODE 4821", "OK"])
        self.assertIn("Typed: 4 8 _ _", out.getvalue())
        self.assertIn("Typed: 4 8 2 1", out.getvalue())

    def test_malformed_typed_message_counts_as_wrong(self):
        dev = FakeDevice(["STARTED", "TYPED 48215", "STARTED", "TYPED x", "STARTED", "TIMEOUT"])
        self.assertFalse(self.factor(dev, "4821", "4821", "4821"))
        self.assertEqual(dev.sent, ["CODE 4821", "BAD", "CODE 4821", "BAD", "CODE 4821"])

    def test_silence_after_typing_means_fail_closed(self):
        with self.assertRaises(cl.DeviceError):
            self.factor(FakeDevice(["STARTED", "TYPED 4", "TYPED 48"]), "4821")

    def test_right_code_is_granted(self):
        dev = FakeDevice(["STARTED", "ENTRY 4821"])
        self.assertTrue(self.factor(dev, "4821"))
        self.assertEqual(dev.sent, ["CODE 4821", "OK"])

    def test_wrong_code_then_new_code(self):
        dev = FakeDevice(["STARTED", "ENTRY 9999", "STARTED", "ENTRY 2222"])
        self.assertTrue(self.factor(dev, "1111", "2222"))
        self.assertEqual(dev.sent, ["CODE 1111", "BAD", "CODE 2222", "OK"])

    def test_old_code_does_not_work_for_the_next_attempt(self):
        dev = FakeDevice(["STARTED", "TIMEOUT", "STARTED", "ENTRY 1111", "STARTED", "ENTRY 3333"])
        self.assertTrue(self.factor(dev, "1111", "2222", "3333"))
        self.assertEqual(dev.sent, ["CODE 1111", "CODE 2222", "BAD", "CODE 3333", "OK"])

    def test_three_failures_mean_no_access(self):
        dev = FakeDevice(["STARTED", "TIMEOUT", "STARTED", "ENTRY 0000", "STARTED", "TIMEOUT"])
        self.assertFalse(self.factor(dev, "1234", "5678", "9012"))
        self.assertNotIn("OK", dev.sent)

    def test_malformed_entries_are_wrong(self):
        for bad in ["ENTRY 48211", "ENTRY 482", "ENTRY 48a1", "ENTRY  4821", "entry 4821",
                    "ENTRY 4821#", " ", "", "ERROR UNKNOWN"]:
            with self.subTest(reply=bad):
                dev = FakeDevice(["STARTED", bad, "STARTED", "TIMEOUT", "STARTED", "TIMEOUT"])
                self.assertFalse(self.factor(dev, "4821", "4821", "4821"))
                self.assertNotIn("OK", dev.sent)

    def test_leading_zero_codes_work(self):
        dev = FakeDevice(["STARTED", "ENTRY 0042"])
        self.assertTrue(self.factor(dev, "0042"))

    def test_no_started_means_fail_closed(self):
        with self.assertRaises(cl.DeviceError):
            self.factor(FakeDevice([]), "4821")
        with self.assertRaises(cl.DeviceError):
            self.factor(FakeDevice(["ERROR TIMER"]), "4821")

    def test_silence_after_the_code_means_fail_closed(self):
        dev = FakeDevice(["STARTED"])  # then nothing
        with self.assertRaises(cl.DeviceError):
            self.factor(dev, "4821")
        self.assertNotIn("OK", dev.sent)


class CodeTests(unittest.TestCase):
    def test_codes_are_always_four_digits(self):
        for _ in range(2000):
            self.assertRegex(cl.new_code(), r"^\d{4}$")

    def test_codes_vary(self):
        self.assertGreater(len({cl.new_code() for _ in range(200)}), 150)


class PasswordTests(unittest.TestCase):
    def setUp(self):
        salt = secrets.token_bytes(16)
        self.users = {"logan": {"salt": salt.hex(), "hash": cl.hash_password("correct horse", salt).hex()}}

    def test_right_and_wrong_passwords(self):
        self.assertTrue(cl.check_password(self.users, "logan", "correct horse"))
        self.assertFalse(cl.check_password(self.users, "logan", "wrong"))
        self.assertFalse(cl.check_password(self.users, "nobody", "correct horse"))

    def test_lockout_after_repeated_failures(self):
        names = iter(["logan"] * 4)
        passwords = iter(["a", "b", "c", "correct horse"])
        sleeps = []
        user = run_quietly(cl.password_step, self.users, read_input=lambda _p: next(names),
                           read_secret=lambda _p: next(passwords), sleep=sleeps.append)
        self.assertEqual(user, "logan")
        self.assertEqual(sleeps, [cl.LOCKOUT_S])

    def test_users_file_never_holds_the_password(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "users.json"
            path.write_text(json.dumps(self.users), encoding="utf-8")
            self.assertNotIn("correct horse", path.read_text())
            self.assertTrue(cl.check_password(cl.load_users(path), "logan", "correct horse"))


class DeviceLineTests(unittest.TestCase):
    def test_lines_split_across_reads(self):
        dev = cl.Device(FakeSerial([b"STAR", b"TED\r\nENTRY 12", b"34\n"]))
        self.assertEqual(dev.read_line(1), "STARTED")
        self.assertEqual(dev.read_line(1), "ENTRY 1234")

    def test_overlong_line_is_refused(self):
        dev = cl.Device(FakeSerial([b"ENTRY " + b"1" * 100 + b"\n"]))
        self.assertEqual(dev.read_line(1), "")

    def test_flood_without_newline_is_refused(self):
        dev = cl.Device(FakeSerial([b"x" * 500]))
        self.assertEqual(dev.read_line(1), "")

    def test_timeout_returns_none(self):
        dev = cl.Device(FakeSerial([]))
        self.assertIsNone(dev.read_line(0.2))

    def test_send_adds_newline(self):
        ser = FakeSerial([])
        cl.Device(ser).send("CODE 0042")
        self.assertEqual(ser.written, b"CODE 0042\n")


if __name__ == "__main__":
    unittest.main()
