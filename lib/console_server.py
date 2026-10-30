#!/usr/bin/env python3
"""The console's HTTP server (`bosun console`): the static page from console/ and a read-only
JSON API over the state directory. Design: docs/design/console.md.

Security (the ledger holds colleagues' names and untrusted Slack text):
- Listens on 127.0.0.1 only; any other address is refused.
- A random token, in the printed URL, is exchanged for an HttpOnly, SameSite=Strict cookie (the
  redirect drops it from the address bar). /api/* answers 401 without the cookie, so other local
  processes and pages cannot read the ledger by finding the port. The token is kept across
  restarts in a private file (lib/console_token.py), so an open tab stays signed in;
  `--new-token` rotates it, and BOSUN_CONSOLE_PERSIST_TOKEN=0 makes it new per start.
- The Host header must be 127.0.0.1:<port> or localhost:<port> (403 otherwise: DNS rebinding).
- GET and HEAD only (405 otherwise), with one exception: POST /api/knowledge/review, a review
  decision on the knowledge base (local data, never outward-facing; design decision 2 of
  docs/design/knowledge.md). It needs the cookie, an Origin header naming this server (and, if
  the browser sends Sec-Fetch-Site, same-origin), a JSON body, and the page's CSRF token in the
  X-Bosun-CSRF header: a random value per start, different from the cookie's, that only
  GET /api/knowledge/review returns (so only a page that can read this server's API knows it).
- Every response sends a strict CSP (no inline script or style), nosniff and no-referrer; API
  responses are not cached.
- Files come from a fixed allowlist read at start; no part of a request path reaches the
  filesystem, so traversal and unknown paths are 404.
- The API only reads: /api/version, /api/items (filters in the query), /api/items/<id> (the id
  URL-encoded: ids hold ':', '/' and '#'; an unknown one is 404), /api/needs-you, /api/sessions
  and /api/health (lib/console_live.py: the readers `bosun sessions --json` and the watchdog
  use, never keys sent to a pane, never Claude started), /api/stats?days=7|30|90 (the ledger and
  bosun's own state files, lib/console_state.py), /api/impact (the snapshot `bosun impact
  collect` wrote, lib/impact.py; never GitHub itself), /api/learn and /api/learn/report/<key>
  (learning's runs, items and findings, and a run's report.md), /api/library and
  /api/library/<key> (the editorial files, config masked), /api/knowledge (?q=&type=),
  /api/knowledge/entity/<key>, /api/knowledge/review, /api/knowledge/briefing and
  /api/knowledge/constellation (lib/console_kb.py: the knowledge base, never its restricted
  identifiers). A file or an entity is named only by an opaque key looked up
  in a list built for the request (lib/console_files.py, lib/console_knowledge.py); an unknown key
  is 404. A query it cannot use is 400.
Only refusals are logged (method, path without the query or an item id, status), never ledger
content.
"""

from __future__ import annotations

import argparse
import hmac
import http
import json
import os
import secrets
import signal
import socketserver
import sys
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

import console_data
import console_files
import console_kb
import console_kg_sources
import console_knowledge
import console_learn
import console_ledger
import console_library
import console_live
import console_sessions
import console_state
import console_stats
import console_token
import impact
import impact_learning
import impact_overhead
import impact_tasks
import tickets
import workstreams
from knowledge_model import KnowledgeError


BIND_HOST = "127.0.0.1"
LOCAL_NAMES = ("127.0.0.1", "localhost")
DEFAULT_PORT = 8765
STATIC_DIR = Path(__file__).resolve().parent.parent / "console"
PERSIST_SETTING = "BOSUN_CONSOLE_PERSIST_TOKEN"
QUERY_KEY = "t"  # the printed URL's query parameter that carries the token
ITEM_PREFIX = "/api/items/"  # + the item's id, URL-encoded
LIBRARY_PREFIX = "/api/library/"  # + a file's opaque key
REPORT_PREFIX = "/api/learn/report/"  # + a learning report's opaque key
ENTITY_PREFIX = "/api/knowledge/entity/"  # + an entity's opaque key
REVIEW_PATH = "/api/knowledge/review"  # GET: today's batch; POST: a review decision
BRIEFING_PATH = "/api/knowledge/briefing"  # the Knowledge Graph page's Briefing tab
CONSTELLATION_PATH = "/api/knowledge/constellation"  # its Constellation tab
WRITE_PATHS = (REVIEW_PATH,)  # the only paths that take a POST
TOKEN_BYTES = 32
COOKIE_PREFIX = "bosun_console_"  # + the port: cookies are not kept apart by port
ALLOWED_METHODS = ("GET", "HEAD")
WRITE_METHOD = "POST"
CSRF_HEADER = "X-Bosun-CSRF"
SAME_ORIGIN = "same-origin"  # the only Sec-Fetch-Site a write may come with
JSON_MEDIA = "application/json"
MAX_BODY_BYTES = 64 * 1024
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
)
JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"


class ConsoleError(Exception):
    """A refused start (address, port); main prints it and exits 1."""


# ---- request checks (pure) ----------------------------------------------------------------------


@dataclass(frozen=True)
class Asset:
    file: str
    content_type: str


# Request path -> file in console/. The only files the server ever serves.
STATIC: dict[str, Asset] = {
    "/": Asset("index.html", "text/html; charset=utf-8"),
    "/index.html": Asset("index.html", "text/html; charset=utf-8"),
    "/app.js": Asset("app.js", "text/javascript; charset=utf-8"),
    "/app.css": Asset("app.css", "text/css; charset=utf-8"),
}
NO_CONTENT_PATHS = ("/favicon.ico",)  # browsers ask; 204 keeps it out of the refusal log


def check_bind_host(host: str) -> None:
    if host != BIND_HOST:
        raise ConsoleError(f"the console only listens on {BIND_HOST}, not {host}")


def host_allowed(host_header: str | None, port: int) -> bool:
    """Whether a request's Host header names this server: 127.0.0.1:<port> or localhost:<port>."""
    if not host_header:
        return False
    return host_header.strip().lower() in {f"{name}:{port}" for name in LOCAL_NAMES}


def token_matches(given: str | None, token: str) -> bool:
    return bool(given) and hmac.compare_digest(str(given).encode(), token.encode())


def cookie_name(port: int) -> str:
    return f"{COOKIE_PREFIX}{port}"


def cookie_token(cookie_header: str | None, name: str) -> str | None:
    """The value of cookie <name> in a Cookie header, None if absent or unparseable."""
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(cookie_header)
    except CookieError:
        return None
    morsel = jar.get(name)
    return morsel.value if morsel else None


def session_cookie(name: str, token: str) -> str:
    return f"{name}={token}; Path=/; HttpOnly; SameSite=Strict"


def query_token(query: str) -> str | None:
    values = parse_qs(query).get(QUERY_KEY)
    return values[0] if values else None


def logged_path(request_path: str) -> str:
    """A refused request's path as logged: without the query, and an item's id masked (ids name
    repositories and Slack channels)."""
    path = urlsplit(request_path).path
    return f"{ITEM_PREFIX}<id>" if path.startswith(ITEM_PREFIX) else path


def static_asset(path: str) -> Asset | None:
    """The allowlisted asset for an exact request path (no normalising: '/../x' is just unknown)."""
    return STATIC.get(path)


WRITE_HEADERS = ("Origin", "Sec-Fetch-Site", "Content-Type", CSRF_HEADER)


def origin_allowed(origin: str | None, port: int) -> bool:
    """Whether an Origin header names this server (a write without one is refused)."""
    if not origin:
        return False
    return origin.strip().lower() in {f"http://{name}:{port}" for name in LOCAL_NAMES}


def write_refusal(headers: Mapping[str, str | None], port: int, csrf: str) -> tuple[int, str] | None:
    """Why a write request (already past the Host and cookie checks) is refused, or None: it must
    come from this server's own page (Origin, Sec-Fetch-Site), carry JSON (a cross-site form
    cannot), and the page's CSRF token in its header (a cross-site page can neither read it nor
    set the header without a preflight this server never answers)."""
    if not origin_allowed(headers.get("Origin"), port):
        return http.HTTPStatus.FORBIDDEN, "cross-origin write refused"
    site = headers.get("Sec-Fetch-Site")
    if site is not None and site != SAME_ORIGIN:
        return http.HTTPStatus.FORBIDDEN, "cross-site write refused"
    media = (headers.get("Content-Type") or "").partition(";")[0].strip().lower()
    if media != JSON_MEDIA:
        return http.HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "a decision is sent as application/json"
    if not token_matches(headers.get(CSRF_HEADER), csrf):
        return http.HTTPStatus.FORBIDDEN, "missing or wrong CSRF token: reload the page"
    return None


def body_length(value: str | None) -> tuple[int, str] | int:
    """The request body's length from Content-Length, or (status, why) when it cannot be read."""
    if value is None:
        return http.HTTPStatus.LENGTH_REQUIRED, "Content-Length required"
    if not value.strip().isdigit():
        return http.HTTPStatus.BAD_REQUEST, "bad Content-Length"
    length = int(value)
    if length > MAX_BODY_BYTES:
        return http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"a decision is at most {MAX_BODY_BYTES} bytes"
    return length


def load_assets(directory: Path) -> dict[str, bytes]:
    """Every allowlisted file's bytes, read once at start (a missing one fails the start)."""
    files = {asset.file for asset in STATIC.values()}
    try:
        return {name: (directory / name).read_bytes() for name in files}
    except OSError as exc:
        raise ConsoleError(f"cannot read the console's page: {exc}") from exc


# ---- server ------------------------------------------------------------------------------------


class LiveSource(Protocol):
    """Where the live facts come from (console_live.LiveCache; a fixed one in the tests)."""

    def get(self) -> console_live.Facts: ...


@dataclass
class App:
    """What every request needs: the token, the port bound, the page, the ledger snapshot and the
    live facts (sessions and health, collected only when a request needs them), where the Library
    and the knowledge base are, and the CSRF token review decisions carry."""

    token: str
    assets: dict[str, bytes]
    ledger: console_data.SnapshotCache
    live: LiveSource
    port: int = 0
    library: console_files.Settings = field(default_factory=console_files.Settings)  # no site, no memory
    knowledge: console_kb.Settings = field(default_factory=console_kb.Settings)  # none
    owned: frozenset[str] = frozenset()  # BOSUN_TICKET_OWNED_PROJECTS; empty: every project
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(TOKEN_BYTES))

    @property
    def state_dir(self) -> Path:
        return self.ledger.path.parent

    def write(self, path: str, body: bytes) -> dict[str, Any]:
        """A write request's answer (only REVIEW_PATH takes one); QueryError for a malformed body,
        console_knowledge.ReviewError for a refused or failed decision."""
        if path != REVIEW_PATH:
            raise console_knowledge.ReviewError(http.HTTPStatus.NOT_FOUND, "not found")
        return console_kb.decide(self.knowledge, body, self.ledger.get().ledger)

    def api(self, path: str, query: str) -> Any | None:
        """The model for an API path, None if there is none (404); QueryError for a bad query."""
        now = datetime.now(UTC)
        if path.startswith(ITEM_PREFIX):
            data = self.ledger.get().ledger
            return console_ledger.item_model(
                data, unquote(path.removeprefix(ITEM_PREFIX)), now, ws_rules(self), self.owned
            )
        for prefix, by_key in KEY_ROUTES.items():
            if path.startswith(prefix):
                key = path.removeprefix(prefix)
                return by_key(self, key) if console_library.valid_key(key) else None
        route = API_ROUTES.get(path)
        return route(self, parse_qs(query), now) if route else None


def ws_rules(app: App) -> workstreams.RuleSet:
    """The site's workstream rules (workstreams.json in the config dir), as `ledger` reads them;
    a file it cannot read leaves the rules out and says so in their problems."""
    items = app.ledger.get().ledger.get("items") or {}
    config = app.library.config_dir
    path = config / workstreams.RULES_FILE if config else None
    if not path or not path.is_file():
        return workstreams.parse_rules(items, None)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        found = workstreams.parse_rules(items, None)
        found.problems.append(f"{workstreams.RULES_FILE}: cannot read it ({type(exc).__name__})")
        return found
    return workstreams.parse_rules(items, data)


def needs_you(app: App, now: datetime) -> dict[str, Any]:
    windows = console_sessions.waiting_windows(app.live.get(), now)
    return console_ledger.needs_you_model(app.ledger.get().ledger, windows, app.owned)


def stats(app: App, params: console_data.Params, now: datetime) -> dict[str, Any]:
    days = console_stats.parse_days(params)  # a bad period is refused before anything is read
    own = console_state.read_own(app.state_dir)
    local = now.astimezone()
    data = app.ledger.get().ledger
    return console_stats.stats_model(data, own, days, local, local.tzinfo or UTC, ws_rules(app))


def impact_view(app: App, _params: console_data.Params, now: datetime) -> dict[str, Any]:
    """The Impact view, from the snapshot `bosun impact collect` wrote (never GitHub itself),
    with its Overhead panel (lib/impact_overhead.py), Cost per task panel (lib/impact_tasks.py) and
    learning panel (lib/impact_learning.py)."""
    zone = now.astimezone().tzinfo or UTC
    snapshot = console_state.read_impact(app.state_dir)
    model = impact.impact_model(snapshot, now, zone)
    if not model.get("exists"):
        return model
    return {
        **model,
        "overhead": impact_overhead.from_snapshot(snapshot, now, zone),
        "tasks": impact_tasks.from_snapshot(snapshot),
        "learning": learning_panel(app, now),
    }


def learning_panel(app: App, now: datetime) -> dict[str, Any]:
    """The Impact view's learning panel: learning's runs and reports, its `learn:` items, and the
    knowledge base's growth."""
    files = console_state.read_learn(app.state_dir)
    return impact_learning.learning_model(
        files.runs,
        console_state.report_texts(files),
        console_learn.learn_items(app.ledger.get().ledger),
        console_kb.growth(app.knowledge, now),
    )


def learn_view(app: App, _params: console_data.Params, _now: datetime) -> dict[str, Any]:
    files = console_state.read_learn(app.state_dir)
    keys = console_files.report_keys(console_files.learn_reports(files))
    return console_learn.learn_model(files, app.ledger.get().ledger, keys)


def learn_report(app: App, key: str) -> dict[str, Any] | None:
    """A run's report.md, by its key: only reports under the learn state folder have one."""
    files = console_state.read_learn(app.state_dir)
    path = console_files.learn_reports(files).get(key)
    if path is None:
        return None
    text, error = console_files.read(path)
    run = next((r for r in files.runs if str(r.get("run_dir")) == str(path.parent)), {})
    return {
        "key": key,
        "started": run.get("started"),
        "dry_run": bool(run.get("dry_run")),
        "body": text or "",
        "error": error or None,
    }


def library_view(app: App, _params: console_data.Params, _now: datetime) -> dict[str, Any]:
    entries = console_files.catalog(app.library)
    texts = {e.key: console_files.read(e.path)[0] for e in entries if e.kind == console_library.MARKDOWN}
    return console_library.library_model(entries, texts, bool(app.library.memory_dirs))


def library_file(app: App, key: str) -> dict[str, Any] | None:
    """One Library file, by its key in the list built now; None (404) for any other key."""
    entry = next((e for e in console_files.catalog(app.library) if e.key == key), None)
    if entry is None:
        return None
    text, error = console_files.read(entry.path)
    return console_library.file_model(entry, text, error)


def knowledge_entity(app: App, key: str) -> dict[str, Any] | None:
    """One entity's page, by its key; None (404) for a key no entity has."""
    page = console_kb.entity(app.knowledge, key, app.ledger.get().ledger, datetime.now(UTC))
    return console_kg_sources.label_sources(page, console_files.catalog(app.library)) if page else None


def knowledge_review(app: App, _params: console_data.Params, now: datetime) -> dict[str, Any]:
    """Today's review batch, and the CSRF token a decision on it must carry. Reading it changes
    nothing, whatever the query says (a GET never writes)."""
    model = console_kb.review(app.knowledge, app.ledger.get().ledger, now)
    return {**console_kg_sources.label_sources(model, console_files.catalog(app.library)), "csrf": app.csrf}


KEY_ROUTES: dict[str, Callable[[App, str], dict[str, Any] | None]] = {
    LIBRARY_PREFIX: library_file,
    REPORT_PREFIX: learn_report,
    ENTITY_PREFIX: knowledge_entity,
}
Route = Callable[[App, console_data.Params, datetime], Any]
API_ROUTES: dict[str, Route] = {
    "/api/version": lambda app, _params, _now: {
        **console_data.version_model(app.ledger.get()),
        "knowledge": console_kb.version(app.knowledge),
    },
    "/api/items": lambda app, params, now: console_ledger.items_model(
        app.ledger.get().ledger, console_ledger.Query.parse(params, now), ws_rules(app), app.owned
    ),
    "/api/needs-you": lambda app, _params, now: needs_you(app, now),
    "/api/sessions": lambda app, _params, now: console_sessions.sessions_model(
        app.live.get(), app.ledger.get().ledger, now
    ),
    "/api/health": lambda app, _params, now: console_sessions.health_model(
        app.live.get(), app.ledger.get().ledger, now
    ),
    "/api/stats": stats,
    "/api/impact": impact_view,
    "/api/learn": learn_view,
    "/api/library": library_view,
    "/api/knowledge": lambda app, params, now: console_kb.overview(
        app.knowledge, app.ledger.get().ledger, params, now
    ),
    REVIEW_PATH: knowledge_review,
    BRIEFING_PATH: lambda app, _params, now: console_kb.briefing(app.knowledge, now),
    CONSTELLATION_PATH: lambda app, _params, now: console_kb.constellation(app.knowledge, now),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "bosun-console"
    app: App  # set on the subclass made by make_server

    def version_string(self) -> str:
        return self.server_version  # no Python version in the Server header

    def dispatch(self) -> None:
        if not host_allowed(self.headers.get("Host"), self.app.port):
            self.reply(http.HTTPStatus.FORBIDDEN, b"wrong Host\n")
            return
        url = urlsplit(self.path)
        if self.command == WRITE_METHOD and url.path in WRITE_PATHS:
            self.serve_write(url.path)
            return
        if self.command not in ALLOWED_METHODS:
            self.reply(http.HTTPStatus.METHOD_NOT_ALLOWED, b"GET only\n", [("Allow", "GET, HEAD")])
            return
        if url.path.startswith("/api/"):
            self.serve_api(url.path, url.query)
        else:
            self.serve_static(url.path, query_token(url.query))

    # Every standard method goes through dispatch, so a write method gets 405 (methods HTTP does not
    # define get the stdlib's 501), except a POST to one of WRITE_PATHS.
    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = dispatch

    def authorised(self) -> bool:
        given = cookie_token(self.headers.get("Cookie"), cookie_name(self.app.port))
        return token_matches(given, self.app.token)

    def serve_write(self, path: str) -> None:
        """A review decision: the cookie, then the origin, media type and CSRF checks, then the
        body (read only once everything else passed)."""
        if not self.authorised():
            self.reply_json(http.HTTPStatus.UNAUTHORIZED, {"error": "unauthorised"})
            return
        headers = {name: self.headers.get(name) for name in WRITE_HEADERS}
        refusal = write_refusal(headers, self.app.port, self.app.csrf)
        length = body_length(self.headers.get("Content-Length")) if refusal is None else 0
        refusal = refusal or (length if isinstance(length, tuple) else None)
        if refusal is not None:
            self.close_connection = True  # an unread body must not be taken for the next request
            self.reply_json(http.HTTPStatus(refusal[0]), {"error": refusal[1]})
            return
        body = self.rfile.read(int(length)) if isinstance(length, int) else b""
        try:
            self.reply_json(http.HTTPStatus.OK, self.app.write(path, body))
        except console_data.QueryError as exc:
            self.reply_json(http.HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except console_knowledge.ReviewError as exc:
            self.reply_json(http.HTTPStatus(exc.status), {"error": str(exc)})

    def serve_api(self, path: str, query: str) -> None:
        if not self.authorised():
            self.reply_json(http.HTTPStatus.UNAUTHORIZED, {"error": "unauthorised"})
            return
        try:
            model = self.app.api(path, query)
        except console_data.QueryError as exc:
            self.reply_json(http.HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if model is None:
            self.reply_json(http.HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self.reply_json(http.HTTPStatus.OK, model)

    def reply_json(self, status: http.HTTPStatus, model: Any) -> None:
        body = json.dumps(model).encode() + b"\n"
        self.reply(status, body, [("Cache-Control", "no-store")], JSON_TYPE)

    def serve_static(self, path: str, token: str | None) -> None:
        if path in NO_CONTENT_PATHS:
            self.reply(http.HTTPStatus.NO_CONTENT, b"")
            return
        asset = static_asset(path)
        if asset is None:
            self.reply(http.HTTPStatus.NOT_FOUND, b"not found\n")
            return
        if token is not None:
            self.exchange_token(path, token)
            return
        self.reply(
            http.HTTPStatus.OK,
            self.app.assets[asset.file],
            [("Cache-Control", "no-cache")],
            asset.content_type,
        )

    def exchange_token(self, path: str, token: str) -> None:
        """The printed URL's token becomes the cookie; the redirect drops it from the address bar."""
        if not token_matches(token, self.app.token):
            self.reply(http.HTTPStatus.UNAUTHORIZED, b"wrong token: open the URL bosun console printed\n")
            return
        cookie = session_cookie(cookie_name(self.app.port), self.app.token)
        headers = [("Set-Cookie", cookie), ("Location", path), ("Cache-Control", "no-store")]
        self.reply(http.HTTPStatus.SEE_OTHER, b"", headers)

    def reply(
        self,
        status: http.HTTPStatus,
        body: bytes,
        headers: list[tuple[str, str]] | None = None,
        content_type: str = TEXT_TYPE,
    ) -> None:
        self.send_response(status)
        for name, value in [*SECURITY_HEADERS, *(headers or [])]:
            self.send_header(name, value)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """The stdlib's own refusals (malformed request, line too long), with our headers and text."""
        self.close_connection = True
        self.reply(http.HTTPStatus(code), f"{message or http.HTTPStatus(code).phrase}\n".encode())

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if isinstance(code, int) and code >= http.HTTPStatus.BAD_REQUEST:
            path = logged_path(self.path) if hasattr(self, "path") else "-"
            self.log_message("%s %s %s", self.command or "-", path, code)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  # the stdlib's name
        stamp = datetime.now().strftime("%F %T")
        sys.stderr.write(f"{stamp} [console] {format % args}\n")


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer without its reverse DNS lookup of the bind address (socket.getfqdn),
    which only names the server and can hold up the start for many seconds where DNS is slow."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = BIND_HOST
        self.server_port = self.server_address[1]


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    check_bind_host(host)
    handler = type("BoundHandler", (Handler,), {"app": app})
    try:
        server = Server((host, port), handler)
    except OSError as exc:
        hint = "use --port N, or --port 0 for a free one" if port else ""
        raise ConsoleError(f"cannot listen on {host}:{port} ({exc.strerror}); {hint}".rstrip("; ")) from exc
    app.port = server.server_address[1]
    return server


def knowledge_settings() -> console_kb.Settings:
    try:
        return console_kb.Settings.from_env(os.environ)
    except KnowledgeError as exc:
        raise ConsoleError(str(exc)) from exc


def new_app(state_dir: Path, rotate: bool = False) -> App:
    persist = console_token.persist_setting(os.environ.get(PERSIST_SETTING))
    return App(
        token=console_token.console_token(state_dir, persist, rotate),
        assets=load_assets(STATIC_DIR),
        ledger=console_data.SnapshotCache(state_dir / console_data.LEDGER_FILE),
        live=console_live.live_cache(os.environ),
        library=console_files.Settings.from_env(os.environ),
        knowledge=knowledge_settings(),
        owned=tickets.parse_owned(os.environ.get(tickets.OWNED_SETTING, "")),
    )


def state_dir() -> Path:
    value = os.environ.get("BOSUN_STATE_DIR")
    if not value:
        raise ConsoleError("BOSUN_STATE_DIR is not set (run it as bosun console)")
    return Path(value)


def stop_on_term(_signum: int, _frame: FrameType | None) -> None:
    """SIGTERM stops the server like Ctrl-C; a second one while stopping is ignored."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt


def serve(server: ThreadingHTTPServer, url: str, open_browser: bool) -> None:
    print(f"bosun console: {url}", flush=True)
    print("Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    signal.signal(signal.SIGTERM, stop_on_term)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbosun console: stopped", flush=True)
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun console", description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=BIND_HOST)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--new-token", action="store_true", help="replace the stored access token")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = new_app(state_dir(), rotate=args.new_token)
        server = make_server(app, args.host, args.port)
    except ConsoleError as exc:
        print(f"bosun console: {exc}", file=sys.stderr)
        return 1
    serve(server, f"http://{BIND_HOST}:{app.port}/?{QUERY_KEY}={app.token}", args.open)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
