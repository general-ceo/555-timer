"""CLARUS two-factor login in the browser.

Same flow as clarus_login.py, but the username/password form and the keypad
progress are on a local web page instead of the terminal.

    python clarus_web.py                  serve on http://127.0.0.1:8000
    python clarus_web.py --http-port 8080 use another web port
    python clarus_web.py --port COM3      use a specific serial port
    python clarus_web.py --show-code      also show the code on the page (testing only)

Add users with: python clarus_login.py --add-user
"""
from __future__ import annotations

import argparse
import html
import json
import math
import secrets
import sys
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from clarus_login import (CODE_DIGITS, LOCKOUT_S, MAX_CODE_FAILURES, MAX_PASSWORD_FAILURES, USERS_FILE,
                          DeviceError, check_password, load_users, open_device, second_factor)

COOKIE = "clarus_session"
MAX_FORM_BYTES = 4096


class Attempt:
    """One login's progress. Written by the board thread, read by the web pages."""

    def __init__(self, user: str):
        self.user = user
        self.stage = "code"   # code -> granted | denied
        self.round = 0        # which code (1..MAX_CODE_FAILURES)
        self.typed = ""
        self.code = None      # only set with --show-code
        self.notice = ""      # "Wrong code." / "Time is up." from the last round
        self.message = ""     # final result

    def status(self) -> dict:
        return {"stage": self.stage, "round": self.round, "rounds": MAX_CODE_FAILURES, "typed": self.typed,
                "code": self.code, "notice": self.notice, "message": self.message}


class WebUI:
    """Feeds second_factor's progress into an Attempt instead of printing it."""

    def __init__(self, attempt: Attempt):
        self.attempt = attempt

    def prompt(self, code):
        self.attempt.round += 1
        self.attempt.typed = ""
        self.attempt.code = code

    def typed(self, digits):
        self.attempt.typed = digits

    def end_typing(self):
        pass

    def say(self, text):
        self.attempt.notice = text


class App:
    """Login state for the one CLARUS board. Only one code step can use the board at a time."""

    def __init__(self, open_dev, users_path: Path = USERS_FILE, show_code: bool = False, sleep=time.sleep):
        self.open_dev = open_dev
        self.users_path = users_path
        self.show_code = show_code
        self.sleep = sleep
        self.dev = None
        self.board = threading.Lock()   # held while a code step is running
        self.lock = threading.Lock()    # guards the fields below
        self.sessions: dict[str, Attempt] = {}
        self.password_failures = 0
        self.locked_until = 0.0

    def has_users(self) -> bool:
        return bool(load_users(self.users_path))

    def locked_for(self) -> int:
        with self.lock:
            return max(0, math.ceil(self.locked_until - time.monotonic()))

    def lock_out(self) -> None:
        with self.lock:
            self.locked_until = time.monotonic() + LOCKOUT_S

    def connect(self) -> bool:
        """Find the board if it isn't connected yet. False if it can't be found."""
        if self.dev is None:
            try:
                self.dev = self.open_dev()
            except DeviceError:
                return False
        return True

    def login(self, name: str, password: str) -> tuple[str | None, str | None]:
        """Check factor 1 and start factor 2. Returns (session token, None) or (None, error)."""
        wait = self.locked_for()
        if wait:
            return None, f"Locked. Try again in {wait} seconds."
        if not check_password(load_users(self.users_path), name, password):
            with self.lock:
                self.password_failures += 1
                if self.password_failures < MAX_PASSWORD_FAILURES:
                    return None, "Wrong username or password."
                self.password_failures = 0
            self.lock_out()
            return None, f"Too many failed attempts. Locked for {LOCKOUT_S} seconds."
        with self.lock:
            self.password_failures = 0

        if not self.board.acquire(blocking=False):
            return None, "The CLARUS board is busy with another login. Try again shortly."
        if not self.connect():
            self.board.release()
            return None, "CLARUS hardware not found. Access denied."
        attempt = Attempt(name)
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = attempt
        threading.Thread(target=self._code_step, args=(attempt,), daemon=True).start()
        return token, None

    def _code_step(self, attempt: Attempt) -> None:
        granted = False
        lost_board = False
        message = "Access denied."
        try:
            granted = second_factor(self.dev, show_code=self.show_code, sleep=self.sleep, ui=WebUI(attempt))
            if granted:
                message = f"Access granted. Welcome, {attempt.user}."
            else:
                self.lock_out()
                message = f"Too many failed codes. Locked for {LOCKOUT_S} seconds."
        except DeviceError as e:
            lost_board = True
            message = f"Lost contact with the CLARUS hardware ({e}). Access denied."
        except Exception as e:  # never leave the page hanging or the board locked
            lost_board = True
            message = f"Unexpected error ({e}). Access denied."
        finally:
            if not granted:
                try:
                    self.dev.send("CANCEL")
                except DeviceError:
                    lost_board = True
            if lost_board:
                self.dev.close()
                self.dev = None  # look for the board again on the next login
            attempt.code = None
            attempt.message = message
            attempt.stage = "granted" if granted else "denied"
            self.board.release()

    def session(self, token: str | None) -> Attempt | None:
        with self.lock:
            return self.sessions.get(token) if token else None

    def end_session(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token, None)


# ---------- pages ----------

STYLE = """
:root { --bg:#f4f5f7; --card:#fff; --text:#1d2330; --muted:#5d6675; --line:#d6dae1; --accent:#2557d6;
        --good:#1d7a3e; --bad:#b3261e; --warn:#8a5a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --card:#1e232b; --text:#e6e9ee; --muted:#9aa3b2; --line:#343b47; --accent:#7ea2ff;
          --good:#5cc28a; --bad:#ff8a80; --warn:#f0c060; } }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
       background:var(--bg); color:var(--text); font:16px/1.5 system-ui, sans-serif; }
main { width:100%; max-width:360px; background:var(--card); border:1px solid var(--line);
       border-radius:12px; padding:28px; }
h1 { font-size:22px; margin:0 0 4px; }
p { margin:0 0 16px; color:var(--muted); }
label { display:block; font-size:14px; margin:12px 0 4px; }
input { width:100%; padding:10px 12px; font:inherit; color:inherit; background:var(--bg);
        border:1px solid var(--line); border-radius:8px; }
button, .button { display:block; width:100%; margin-top:20px; padding:11px; font:inherit; font-weight:600;
       text-align:center; text-decoration:none; color:#fff; background:var(--accent); border:0;
       border-radius:8px; cursor:pointer; }
.error { color:var(--bad); } .good { color:var(--good); } .warn { color:var(--warn); }
.slots { display:flex; gap:10px; justify-content:center; margin:20px 0; }
.slots span { width:52px; height:60px; display:grid; place-items:center; font-size:28px;
       font-variant-numeric:tabular-nums; border:2px solid var(--line); border-radius:8px; }
.slots span.on { border-color:var(--accent); }
.center { text-align:center; }
"""


def page(title: str, body: str, script: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}</style></head>
<body><main>{body}</main>{f"<script>{script}</script>" if script else ""}</body></html>""".encode("utf-8")


def login_page(app: App, error: str = "", name: str = "") -> bytes:
    if not app.has_users():
        return page("CLARUS Login", "<h1>CLARUS</h1><p class='error'>No users yet. Run "
                    "<code>python clarus_login.py --add-user</code>, then reload this page.</p>")
    msg = f"<p class='error' role='alert'>{html.escape(error)}</p>" if error else ""
    return page("CLARUS Login", f"""
<h1>CLARUS</h1><p>Sign in, then confirm with the code on the board.</p>{msg}
<form method="post" action="/login">
  <label for="u">Username</label>
  <input id="u" name="username" autocomplete="username" value="{html.escape(name)}" required autofocus>
  <label for="p">Password</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button>Sign in</button>
</form>""")


VERIFY_SCRIPT = """
const slots = document.querySelectorAll('.slots span');
const $ = id => document.getElementById(id);
async function poll() {
  let s;
  try { s = await (await fetch('/status', {cache: 'no-store'})).json(); }
  catch (e) { $('notice').textContent = 'Lost connection to the laptop.'; return; }
  if (s.stage === 'none') { location = '/'; return; }
  slots.forEach((el, i) => { el.textContent = s.typed[i] || ''; el.classList.toggle('on', i < s.typed.length); });
  $('round').textContent = s.round ? `Code ${s.round} of ${s.rounds}` : 'Sending code to the board…';
  $('notice').textContent = s.notice;
  $('code').textContent = s.code ? `Test mode: the code is ${s.code}` : '';
  if (s.stage === 'granted') { location = '/welcome'; return; }
  if (s.stage === 'denied') {
    $('result').textContent = s.message; $('back').hidden = false; $('help').hidden = true; return;
  }
  setTimeout(poll, 250);
}
poll();
"""


def verify_page() -> bytes:
    slots = "<span></span>" * CODE_DIGITS
    return page("CLARUS Code", f"""
<h1>Check the board</h1>
<p id="help">Type the code shown on the CLARUS display on the keypad, then press <b>#</b>.
<b>*</b> clears. Do it before the timer runs out.</p>
<div class="slots" aria-live="polite">{slots}</div>
<p id="round" class="center"></p>
<p id="notice" class="center warn" role="status"></p>
<p id="code" class="center"></p>
<p id="result" class="center error" role="alert"></p>
<a id="back" class="button" href="/" hidden>Back to sign in</a>""", VERIFY_SCRIPT)


def welcome_page(attempt: Attempt) -> bytes:
    return page("CLARUS Welcome", f"""
<h1 class="good">Access granted</h1><p>Welcome, {html.escape(attempt.user)}.</p>
<form method="post" action="/logout"><button>Sign out</button></form>""")


# ---------- web server ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "CLARUS"
    sys_version = ""

    @property
    def app(self) -> App:
        return self.server.app

    def token(self) -> str | None:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar[COOKIE].value if COOKIE in jar else None

    def send(self, status: int, body: bytes = b"", kind: str = "text/html; charset=utf-8",
             location: str | None = None, cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; "
                         "frame-ancestors 'none'")
        if location:
            self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, to: str, cookie: str | None = None) -> None:
        self.send(303, location=to, cookie=cookie)

    def do_GET(self):
        token = self.token()
        attempt = self.app.session(token)
        path = self.path.split("?", 1)[0]
        if path == "/":
            if attempt and attempt.stage == "granted":
                return self.redirect("/welcome")
            if attempt and attempt.stage == "code":
                return self.redirect("/verify")
            self.app.end_session(token)  # finished (denied) attempts start over
            return self.send(200, login_page(self.app))
        if path == "/verify":
            if not attempt:
                return self.redirect("/")
            if attempt.stage == "granted":
                return self.redirect("/welcome")
            return self.send(200, verify_page())
        if path == "/status":
            status = attempt.status() if attempt else {"stage": "none"}
            return self.send(200, json.dumps(status).encode(), "application/json")
        if path == "/welcome":
            if not attempt or attempt.stage != "granted":
                return self.redirect("/")
            return self.send(200, welcome_page(attempt))
        self.send(404, page("Not found", "<h1>Not found</h1><a class='button' href='/'>Sign in</a>"))

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{self.headers.get('Host')}", "null"):
            return self.send(403, page("Forbidden", "<h1>Forbidden</h1>"))
        path = self.path.split("?", 1)[0]
        if path == "/login":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_FORM_BYTES:
                return self.send(413, page("Too large", "<h1>Request too large</h1>"))
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            name = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]
            self.app.end_session(self.token())
            token, error = self.app.login(name, password)
            if error:
                return self.send(200, login_page(self.app, error, name))
            return self.redirect("/verify", cookie=f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict")
        if path == "/logout":
            self.app.end_session(self.token())
            return self.redirect("/", cookie=f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        self.send(404, page("Not found", "<h1>Not found</h1>"))

    def log_message(self, fmt, *args):
        if not self.path.startswith("/status"):  # the page polls this several times a second
            super().log_message(fmt, *args)


def make_server(app: App, port: int = 8000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # localhost only
    server.app = app
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CLARUS two-factor login (web page)")
    parser.add_argument("--http-port", type=int, default=8000, help="web page port (default 8000)")
    parser.add_argument("--port", help="serial port, e.g. COM3 (default: search all ports)")
    parser.add_argument("--show-code", action="store_true", help="also show the code on the page (testing only)")
    args = parser.parse_args(argv)

    app = App(lambda: open_device(args.port), show_code=args.show_code)
    if not app.has_users():
        print("No users yet. Run: python clarus_login.py --add-user")
    print("Looking for the CLARUS board...")
    print("CLARUS board found." if app.connect() else "CLARUS board not found. Will look again on each sign-in.")

    server = make_server(app, args.http_port)
    print(f"Open http://127.0.0.1:{args.http_port} in a browser. Ctrl+C stops the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        if app.dev is not None:
            try:
                app.dev.send("CANCEL")
            except DeviceError:
                pass
            app.dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
