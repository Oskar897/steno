"""HTTP server for the UI: stdlib only, loopback only, token on every API call.

Threat model: other web pages in the user's browser and other local users must not be able to
drive Steno (it records audio and exposes transcripts and voice-profile operations).

- binds 127.0.0.1 only, on a random port unless one is requested;
- ``Host`` must be ``127.0.0.1:<port>`` or ``localhost:<port>`` (DNS rebinding), and an
  ``Origin`` header, when sent, must be one of those origins;
- every ``/api/*`` request needs the per-launch token in ``X-Steno-Token`` (the page itself
  is opened with ``/?token=...``; the script keeps it in sessionStorage);
- no CORS headers are ever sent, POST bodies must be ``application/json`` (forces a preflight
  that is never granted), and a strict Content-Security-Policy forbids anything but own files.

Live updates use Server-Sent Events (``GET /api/events``, read with ``fetch`` so the token
travels in a header, not in a URL).
"""

from __future__ import annotations

import hmac
import json
import logging
import queue
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from steno.config import Config
from steno.ui.api import ApiError, UiApp

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Steno-Token"
MAX_BODY = 64 * 1024
STATIC_TYPES = {
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "mini.js": "text/javascript; charset=utf-8",
    "mini.css": "text/css; charset=utf-8",
}
PAGES = {"/": "index.html", "/mini": "mini.html"}  # the mini strip of the desktop app
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _static(name: str) -> bytes:
    return (resources.files("steno.ui") / "static" / name).read_bytes()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets another process bind the same port: never enable it.
    allow_reuse_address = False


class _Handler(BaseHTTPRequestHandler):
    server_version = "Steno"
    sys_version = ""
    ui: UiServer  # set on the subclass made by UiServer

    # -- plumbing -----------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug("%s %s", self.address_string(), format % args)  # never at WARNING: has tokens

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _guard(self) -> bool:
        """Host / Origin checks for every request. Sends the error and returns False if bad."""
        host = (self.headers.get("Host") or "").lower()
        if host not in self.ui.allowed_hosts:
            self._error(403, "bad Host header")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in self.ui.allowed_origins:
            self._error(403, "bad Origin")
            return False
        return True

    def _token_ok(self, supplied: str | None) -> bool:
        return supplied is not None and hmac.compare_digest(
            supplied.encode("utf-8"), self.ui.token.encode("utf-8"))

    def _read_body(self) -> dict[str, Any] | None:
        """JSON object body ({} when empty). Sends the error and returns None if bad."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._error(415, "Content-Type must be application/json")
            return None
        raw = self._raw_body
        if raw is None:
            self._error(413, "request body too large")
            return None
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._error(400, "body is not valid JSON")
            return None
        if not isinstance(data, dict):
            self._error(400, "body must be a JSON object")
            return None
        return data

    # -- verbs --------------------------------------------------------------------

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, POST, DELETE")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PUT = do_PATCH = do_OPTIONS = _method_not_allowed

    def _consume_body(self) -> bytes | None:
        """Read the whole request body up front, so early rejections (401/403/415) never
        leave unread bytes on the socket; Windows then aborts the connection before the
        client sees the response. None (and close the connection) if too large or bad."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def _handle(self, method: str) -> None:
        self._raw_body = self._consume_body()
        if not self._guard():
            return
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if not path.startswith("/api/"):
                if method in ("GET", "HEAD"):
                    self._serve_static(path, parse_qs(parts.query))
                else:
                    self._error(405, "method not allowed")
                return
            if not self._token_ok(self.headers.get(TOKEN_HEADER)):
                self._error(401, "missing or wrong token")
                return
            if path == "/api/events" and method == "GET":
                self._events()
                return
            body: dict[str, Any] = {}
            if method == "POST":
                parsed = self._read_body()
                if parsed is None:
                    return
                body = parsed
            self._json(200, self._route(method, path, parse_qs(parts.query), body))
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            log.exception("Unhandled error in %s %s", method, path)
            self._error(500, f"internal error: {type(exc).__name__}: {exc}")

    # -- routes -------------------------------------------------------------------

    def _route(self, method: str, path: str, query: dict[str, list[str]], body: dict) -> Any:
        app = self.ui.app
        segs = path.strip("/").split("/")[1:]  # drop "api"
        n = len(segs)
        if method == "GET":
            if segs == ["status"]:
                return app.status()
            if segs == ["sessions"]:
                return app.sessions()
            if n == 2 and segs[0] == "sessions":
                return app.session(segs[1], (query.get("view") or [None])[0])
            if n == 3 and segs[0] == "sessions" and segs[2] == "participants":
                return app.participants_get(segs[1])
            if segs == ["jobs"]:
                return app.job_list()
            if segs == ["people"]:
                return app.people()
            if segs == ["people", "enroll"]:
                return app.enroll_status()
            if segs == ["projects"]:
                return app.projects_list()
            if segs == ["settings"]:
                return app.settings_get()
            if segs == ["llm", "status"]:
                return app.llm_status(refresh=(query.get("refresh") or ["0"])[0] in ("1", "true"))
            if n == 3 and segs[0] == "projects" and segs[2] == "meetings":
                return app.project_meetings(segs[1])
            if n == 4 and segs[0] == "sessions" and segs[2] == "doc":
                return app.session_doc(segs[1], segs[3])
        elif method == "POST":
            if segs == ["record", "start"]:
                return app.record_start(body)
            if segs == ["record", "stop"]:
                return app.record_stop()
            if segs == ["record", "pause"]:
                return app.record_pause()
            if segs == ["record", "resume"]:
                return app.record_resume()
            if segs == ["record", "auto_title"]:
                return app.record_auto_title(body)
            if segs == ["llm", "login"]:
                return app.llm_login()
            if segs == ["llm", "install"]:
                return app.llm_install_page()
            if segs == ["projects"]:
                return app.project_add(body)
            if n == 3 and segs[0] == "projects" and segs[2] == "update":
                return app.project_update(segs[1], body)
            if n == 3 and segs[0] == "projects" and segs[2] == "subfolders":
                return app.subfolder_add(segs[1], body)
            if n == 4 and segs[0] == "projects" and segs[2:] == ["subfolders", "rename"]:
                return app.subfolder_rename(segs[1], body)
            if n == 4 and segs[0] == "projects" and segs[2:] == ["subfolders", "folder"]:
                return app.subfolder_folder(segs[1], body)
            if n == 3 and segs[0] == "projects" and segs[2] == "types":
                return app.project_types(segs[1], body)
            if n == 4 and segs[0] == "projects" and segs[2:] == ["types", "delete"]:
                return app.type_delete(segs[1], body)
            if n == 3 and segs[0] == "projects" and segs[2] == "folder":
                return app.project_check_folder(segs[1], body)
            if n == 3 and segs[0] == "projects" and segs[2] == "delete":
                return app.project_delete(segs[1], body)
            if segs == ["inbox", "delete"]:
                return app.inbox_delete()
            if segs == ["settings"]:
                return app.settings_set(body)
            if n == 3 and segs[0] == "meetings" and segs[2] == "rename":
                return app.meeting_rename(segs[1], body)
            if n == 3 and segs[0] == "meetings" and segs[2] == "save":
                return app.meeting_save(segs[1], body)
            if n == 3 and segs[0] == "meetings" and segs[2] == "move":
                return app.meeting_move(segs[1], body)
            if n == 3 and segs[0] == "meetings" and segs[2] == "archive":
                return app.meeting_archive(segs[1], body)
            if n == 3 and segs[0] == "sessions" and segs[2] == "name":
                return app.name_speaker(segs[1], body)
            if n == 3 and segs[0] == "sessions" and segs[2] == "jobs":
                return app.start_job(segs[1], body)
            if n == 3 and segs[0] == "sessions" and segs[2] == "participants":
                return app.participants_set(segs[1], body)
            if segs == ["people", "enroll"]:
                return app.enroll_start(body)
            if segs == ["people", "enroll", "cancel"]:
                return app.enroll_cancel()
            if n == 3 and segs[0] == "people" and segs[2] == "rename":
                return app.person_rename(segs[1], body)
        elif method == "DELETE":
            if n == 2 and segs[0] == "people":
                return app.person_delete(segs[1])
            if n == 2 and segs[0] == "projects":
                return app.project_remove(segs[1])
            if n == 2 and segs[0] == "meetings":
                return app.meeting_delete(segs[1])
        raise ApiError(404, "no such endpoint")

    # -- static -------------------------------------------------------------------

    def _serve_static(self, path: str, query: dict[str, list[str]]) -> None:
        if path in PAGES:
            if not self._token_ok((query.get("token") or [None])[0]):
                self._send(403, b"Open the address printed by `steno ui` (it includes a "
                                b"one-time access token).\n", "text/plain; charset=utf-8")
                return
            self._send(200, _static(PAGES[path]), "text/html; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif path.startswith("/static/") and path[len("/static/"):] in STATIC_TYPES:
            name = path[len("/static/"):]
            self._send(200, _static(name), STATIC_TYPES[name])
        else:
            self._error(404, "not found")

    # -- server-sent events -------------------------------------------------------

    def _events(self) -> None:
        app = self.ui.app
        q = app.bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(name: str, data: Any) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        try:
            emit("hello", app.status())
            last_tick = time.monotonic()
            while not self.ui.stopping.is_set():
                interval = 0.25 if app.recorder.active else 2.0
                try:
                    name, data = q.get(timeout=interval)
                    emit(name, data)
                except queue.Empty:
                    pass
                if time.monotonic() - last_tick >= interval:  # live meters + keep-alive
                    last_tick = time.monotonic()
                    running = app.jobs.running()
                    emit("status", {"recorder": app.recorder.snapshot(),
                                    "job": running.as_dict() if running else None})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            app.bus.unsubscribe(q)


class UiServer:
    """The app, its token and the listening socket. ``start()`` serves on a thread."""

    def __init__(self, app: UiApp, *, port: int = 0, token: str | None = None):
        self.app = app
        self.token = token or secrets.token_urlsafe(24)
        self.stopping = threading.Event()
        handler = type("Handler", (_Handler,), {"ui": self})
        self._httpd = _Server(("127.0.0.1", port), handler)
        self.port: int = self._httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    @property
    def mini_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mini?token={self.token}"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.2},
            name="ui-http", daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._httpd.serve_forever(poll_interval=0.2)

    def close(self) -> None:
        self.stopping.set()
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join(timeout=5)
        self._httpd.server_close()


def run_ui(cfg: Config, *, port: int = 0, open_browser: bool = True) -> int:
    """Run the UI until Ctrl+C. Stops a running recording cleanly on the way out."""
    import sys

    app = UiApp(cfg)
    try:
        server = UiServer(app, port=port)
    except OSError as exc:
        print(f"error: cannot listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1
    print(f"Steno UI: {server.url}", flush=True)
    print("Open that address (it carries a private access token). Ctrl+C to quit.",
          file=sys.stderr, flush=True)
    if open_browser:
        try:
            webbrowser.open(server.url)
        except Exception as exc:  # no browser: the printed address still works
            print(f"(could not open a browser: {exc})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        if app.recorder.active:
            print("Stopping the recording and transcribing what is pending "
                  "(Ctrl+C again to skip) ...", file=sys.stderr, flush=True)
            try:
                app.recorder.shutdown(drain=True)
            except KeyboardInterrupt:
                app.recorder.shutdown(drain=False)
        server.close()
    return 0
