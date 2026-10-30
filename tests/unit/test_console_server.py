"""Unit tests for the console's server (lib/console_server.py): the request checks (bind address,
Host header, token and cookie, the static allowlist) and, against a real server on a free port in
a thread, what each kind of request gets: the token exchange, 401, 403, 405, 404 and the headers.
Fictional data: acme's invoice exporter (tests/fixtures/console/acme-ledger.json)."""

from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import quote


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_data
import console_files
import console_kb
import console_knowledge
import console_live
import console_server
import knowledge
from console_server import ConsoleError


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
KG_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "acme-import.jsonl"
PORT = 8765
POLL_SECONDS = 0.05  # shutdown() waits up to one poll
TOKEN = "fixture-token-0000"  # noqa: S105  # a test value, not a secret
CSRF = "fixture-csrf-0000"  # a test value, not a secret
TASK = "task:2026-10-20/1"  # the fixture's delegated task
SETTINGS = console_live.Settings(state_dir=Path("/state"), log_dir=Path("/state/logs"), session="acme")
WINDOWS = [
    {"name": "exporter", "kind": "session", "dir": "/Users/me/repos/exporter", "model": "opus", "effort": "-",
     "window": True, "claude": {"pid": 4102, "status": "waiting", "statusUpdatedAt": 1700000000000}},
]  # fmt: skip


class FixedLive:
    """Live facts that never change (the console_live.LiveCache interface), counting the reads."""

    def __init__(self) -> None:
        self.reads = 0

    def get(self) -> console_live.Facts:
        self.reads += 1
        return console_live.Facts(at=datetime.now(UTC), settings=SETTINGS, windows=WINDOWS)


class ChecksTestCase(unittest.TestCase):
    def test_binds_only_loopback(self) -> None:
        console_server.check_bind_host("127.0.0.1")
        for host in ("0.0.0.0", "", "::", "::1", "localhost", "192.168.1.10"):  # noqa: S104  # refused
            with self.subTest(host=host), self.assertRaises(ConsoleError):
                console_server.check_bind_host(host)

    def test_host_header(self) -> None:
        for host in ("127.0.0.1:8765", "localhost:8765", "LOCALHOST:8765"):
            with self.subTest(host=host):
                self.assertTrue(console_server.host_allowed(host, PORT))
        for wrong in (
            None,
            "",
            "127.0.0.1",
            "127.0.0.1:8766",
            "evil.example.com:8765",
            "127.0.0.1.example.com:8765",
        ):
            with self.subTest(host=wrong):
                self.assertFalse(console_server.host_allowed(wrong, PORT))

    def test_token(self) -> None:
        self.assertTrue(console_server.token_matches(TOKEN, TOKEN))
        for given in (None, "", TOKEN + "x", TOKEN[:-1]):
            with self.subTest(given=given):
                self.assertFalse(console_server.token_matches(given, TOKEN))

    def test_cookie(self) -> None:
        name = console_server.cookie_name(PORT)
        self.assertEqual(name, "bosun_console_8765")
        self.assertEqual(console_server.cookie_token(f"other=1; {name}={TOKEN}", name), TOKEN)
        self.assertIsNone(console_server.cookie_token(f"bosun_console_9999={TOKEN}", name))
        self.assertIsNone(console_server.cookie_token(None, name))
        self.assertIsNone(console_server.cookie_token('"', name))

    def test_session_cookie_is_httponly_and_strict(self) -> None:
        cookie = console_server.session_cookie("c", TOKEN)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_query_token(self) -> None:
        self.assertEqual(console_server.query_token(f"t={TOKEN}"), TOKEN)
        self.assertIsNone(console_server.query_token("x=1"))

    def test_the_log_shows_no_query_or_item_id(self) -> None:
        self.assertEqual(console_server.logged_path("/api/version?t=secret"), "/api/version")
        self.assertEqual(console_server.logged_path("/api/items?q=dana"), "/api/items")
        self.assertEqual(
            console_server.logged_path("/api/items/mypr%3Aacme%2Fexporter%23102"), "/api/items/<id>"
        )

    def test_static_allowlist_is_exact(self) -> None:
        self.assertEqual(console_server.static_asset("/").file, "index.html")  # type: ignore[union-attr]
        self.assertEqual(console_server.static_asset("/app.js").file, "app.js")  # type: ignore[union-attr]
        for path in (
            "/../lib/ledger.py",
            "/%2e%2e/lib/ledger.py",
            "/app.js/..",
            "//app.js",
            "/console/app.js",
            "/lib/console_server.py",
        ):
            with self.subTest(path=path):
                self.assertIsNone(console_server.static_asset(path))

    def test_every_allowlisted_file_exists(self) -> None:
        assets = console_server.load_assets(console_server.STATIC_DIR)
        self.assertEqual(set(assets), {asset.file for asset in console_server.STATIC.values()})

    def test_a_missing_page_fails_the_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ConsoleError):
            console_server.load_assets(Path(tmp))


class RunningServer(unittest.TestCase):
    """A real server on 127.0.0.1 and a free port, in a thread."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.live = FixedLive()
        self.state = Path(tmp.name)
        shutil.copy(FIXTURE, self.state / console_data.LEDGER_FILE)
        self.app = app = console_server.App(
            token=TOKEN,
            assets=console_server.load_assets(console_server.STATIC_DIR),
            ledger=console_data.SnapshotCache(Path(tmp.name) / console_data.LEDGER_FILE),
            live=self.live,
        )
        quiet = mock.patch.object(console_server.Handler, "log_message")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = console_server.make_server(app, "127.0.0.1", 0)
        self.port = app.port
        thread = threading.Thread(target=self.server.serve_forever, args=(POLL_SECONDS,), daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(
        self,
        path: str,
        method: str = "GET",
        host: str | None = None,
        cookie: str | None = None,
        **extra: Any,
    ) -> http.client.HTTPResponse:
        """A request; <extra>: more headers (headers=) and a body (body=)."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        headers = {"Host": host or f"127.0.0.1:{self.port}", **extra.get("headers", {})}
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, body=extra.get("body"), headers=headers)
        return connection.getresponse()

    def signed_in(self) -> str:
        return f"{console_server.cookie_name(self.port)}={TOKEN}"

    def get_json(self, path: str) -> Any:
        response = self.request(path, cookie=self.signed_in())
        self.assertEqual(response.status, 200, path)
        self.assertEqual(response.getheader("Content-Type"), console_server.JSON_TYPE)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        return json.loads(response.read())


class ServerTestCase(RunningServer):
    def test_the_start_asks_dns_nothing(self) -> None:
        with mock.patch("socket.getfqdn", side_effect=AssertionError("asked DNS")):
            server = console_server.make_server(self.app, "127.0.0.1", 0)
        server.server_close()
        self.assertEqual(server.server_name, "127.0.0.1")

    def test_the_page_has_the_security_headers(self) -> None:
        response = self.request("/")
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy", ""))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn(b"/app.js", response.read())

    def test_the_token_becomes_a_cookie_and_leaves_the_url(self) -> None:
        response = self.request(f"/?t={TOKEN}")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/")
        self.assertEqual(
            response.getheader("Set-Cookie"),
            console_server.session_cookie(console_server.cookie_name(self.port), TOKEN),
        )

    def test_a_wrong_token_is_refused(self) -> None:
        response = self.request("/?t=guess")
        self.assertEqual(response.status, 401)
        self.assertIsNone(response.getheader("Set-Cookie"))

    def test_api_needs_the_cookie(self) -> None:
        self.assertEqual(self.request("/api/version").status, 401)
        self.assertEqual(
            self.request("/api/version", cookie=f"{console_server.cookie_name(self.port)}=guess").status, 401
        )

    def test_version(self) -> None:
        response = self.request("/api/version", cookie=self.signed_in())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(json.loads(response.read())["ledger"]["items"], 19)

    def test_the_ledger_endpoints_need_the_cookie(self) -> None:
        for path in (
            "/api/items",
            "/api/items?group=all",
            f"/api/items/{quote(TASK, safe='')}",
            "/api/needs-you",
            "/api/sessions",
            "/api/health",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path).status, 401)
        self.assertEqual(self.live.reads, 0)  # refused before anything is collected

    def test_items(self) -> None:
        self.assertEqual(self.get_json("/api/items?group=all")["count"], 19)
        model = self.get_json("/api/items?kind=outreach&group=all&q=credit+notes")
        self.assertEqual([row["id"] for row in model["rows"]], [f"out:{TASK}/1"])
        titles = {row["id"]: row["title"] for row in self.get_json("/api/items")["rows"]}
        self.assertEqual(titles["mypr:acme/exporter#103"], "Header fix <script>alert(1)</script>")

    def test_a_query_it_cannot_use_is_400(self) -> None:
        for query in ("kind=bug", "group=mine", "since=soon", "sort=title"):
            with self.subTest(query=query):
                response = self.request(f"/api/items?{query}", cookie=self.signed_in())
                self.assertEqual(response.status, 400)
                self.assertIn("error", json.loads(response.read()))

    def test_stats(self) -> None:
        model = self.get_json("/api/stats")
        self.assertEqual(model["days"], 7)
        self.assertEqual(model["periods"], [7, 30, 90])
        self.assertEqual(len(model["throughput"]["days"]), 7)
        self.assertEqual(self.get_json("/api/stats?days=90")["days"], 90)
        self.assertEqual(self.request("/api/stats").status, 401)

    def test_impact_has_the_learning_panel(self) -> None:
        self.assertFalse(self.get_json("/api/impact")["exists"])  # no snapshot yet
        snapshot = {
            "start": "2026-10-12",
            "baseline_from": "2026-09-01",
            "activity": [],
            "prs": [],
            "reviews": [],
        }
        (self.state / "impact").mkdir()
        (self.state / "impact" / "snapshot.json").write_text(json.dumps(snapshot))
        model = self.get_json("/api/impact")
        self.assertEqual(model["learning"]["runs"]["runs"], 0)
        self.assertFalse(model["learning"]["knowledge"]["exists"])  # no knowledge base configured
        self.assertIsNone(model["leverage"]["baseline"]["estimate"])

    def test_stats_takes_only_its_periods(self) -> None:
        for query in ("days=1", "days=365", "days=week", "days=7&days=30", "days=-7"):
            with self.subTest(query=query):
                response = self.request(f"/api/stats?{query}", cookie=self.signed_in())
                self.assertEqual(response.status, 400)
                self.assertIn("days must be one of 7, 30, 90", json.loads(response.read())["error"])

    def site(self) -> Path:
        """A fictional config dir with a secret in bosun.env, used by the app's Library."""
        config = self.state / "config"
        (config / "tasks").mkdir(parents=True)
        (config / "bosun.env").write_text('ACME_API_TOKEN="not-a-real-token-value"\nBOSUN_DAYS="MTWRF"\n')
        (config / "tasks" / "review.md").write_text("---\ntitle: Site review\n---\nbody\n")
        (config / "forbidden-terms.txt").write_text("acme\n")
        self.app.library = console_files.Settings(config_dir=config)
        return config

    def test_library(self) -> None:
        self.site()
        model = self.get_json("/api/library")
        entries = {e["name"]: e for g in model["groups"] for e in g["entries"]}
        self.assertIn("orchestrator/policy.md", entries)
        self.assertNotIn("forbidden-terms.txt", entries)
        self.assertEqual(entries["orchestrator/tasks/review.md"]["badge"], "replaced by site")
        env = self.get_json(f"/api/library/{entries['bosun.env']['key']}")
        self.assertNotIn("not-a-real-token-value", json.dumps(env))
        self.assertIn('BOSUN_DAYS="MTWRF"', env["body"])
        policy = self.get_json(f"/api/library/{entries['orchestrator/policy.md']['key']}")
        self.assertEqual(policy["kind"], "markdown")
        self.assertNotIn("path", policy)

    def test_files_only_by_a_known_key(self) -> None:
        self.site()
        for path in (
            "/api/library/0123456789abcdef",
            "/api/library/..%2F..%2Fetc%2Fpasswd",
            "/api/library/orchestrator/policy.md",
            "/api/library/",
            "/api/learn/report/0123456789abcdef",
            "/api/learn/report/..%2Fstate.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, cookie=self.signed_in()).status, 404)
        for path in ("/api/library", "/api/learn", "/api/library/0123456789abcdef"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path).status, 401)

    def test_learn_and_its_reports(self) -> None:
        run_dir = self.state / "learn" / "runs" / "20261020-183000"
        run_dir.mkdir(parents=True)
        (run_dir / "report.md").write_text("# Learning report\n")
        run = {"started": "2026-10-20T18:30:00+00:00", "outcome": "ok", "run_dir": str(run_dir)}
        (self.state / "learn" / "state.json").write_text(json.dumps({"runs": [run]}))
        model = self.get_json("/api/learn")
        self.assertEqual([i["id"] for i in model["items"]], ["learn:2026-10-20/1"])
        key = model["runs"][0]["report"]
        report = self.get_json(f"/api/learn/report/{key}")
        self.assertEqual(report["body"], "# Learning report\n")

    def test_one_item_by_its_encoded_id(self) -> None:
        for item_id in (
            TASK,
            "mypr:acme/exporter#102",
            "slack:C0000000001/1700000001.000001",
            f"out:{TASK}/1",
        ):
            with self.subTest(item=item_id):
                self.assertEqual(self.get_json(f"/api/items/{quote(item_id, safe='')}")["id"], item_id)
        # ':' and '/' need no encoding; '#' does (it starts the fragment)
        self.assertEqual(self.get_json(f"/api/items/{TASK}")["id"], TASK)

    def test_an_unknown_item_is_404(self) -> None:
        for path in (
            "/api/items/mypr%3Aacme%2Fexporter%23999",
            "/api/items/",
            "/api/items/mypr:acme/exporter#102",
        ):
            with self.subTest(path=path):
                response = self.request(path, cookie=self.signed_in())
                self.assertEqual(response.status, 404)
                self.assertEqual(json.loads(response.read()), {"error": "not found"})

    def test_needs_you(self) -> None:
        model = self.get_json("/api/needs-you")
        self.assertEqual(model["count"], 6 + len(model["windows"]))
        self.assertEqual([o["id"] for o in model["outreach"]], ["out:mypr:acme/exporter#102/1"])
        self.assertEqual([w["name"] for w in model["windows"]], ["exporter"])

    def test_sessions(self) -> None:
        model = self.get_json("/api/sessions")
        self.assertEqual([w["name"] for w in model["windows"]], ["exporter"])
        card = model["windows"][0]
        self.assertEqual((card["state"], card["active"]), ("waiting", TASK))
        self.assertEqual(card["attach"], "tmux attach -t acme:exporter")

    def test_health(self) -> None:
        model = self.get_json("/api/health")
        self.assertEqual(set(model["flags"]), {"events", "slack_watch", "learn"})
        for part in ("watchdog", "morning", "chores", "events", "slack_watch", "login", "left_out"):
            self.assertIn(part, model)

    def test_the_api_takes_no_writes(self) -> None:
        for path in ("/api/items", f"/api/items/{quote(TASK, safe='')}", "/api/needs-you", "/api/sessions"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method="POST", cookie=self.signed_in()).status, 405)

    def test_unknown_api_path(self) -> None:
        self.assertEqual(self.request("/api/nothing", cookie=self.signed_in()).status, 404)

    def test_a_wrong_host_is_forbidden(self) -> None:
        for host in ("evil.example.com", f"evil.example.com:{self.port}", "127.0.0.1:1"):
            with self.subTest(host=host):
                self.assertEqual(self.request("/", host=host).status, 403)

    def test_only_get_and_head(self) -> None:
        self.assertEqual(self.request("/app.css", method="HEAD").status, 200)
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                response = self.request("/api/version", method=method, cookie=self.signed_in())
                self.assertEqual(response.status, 405)
                self.assertEqual(response.getheader("Allow"), "GET, HEAD")

    def test_traversal_and_unknown_paths_are_404(self) -> None:
        for path in (
            "/../lib/ledger.py",
            "/%2e%2e/%2e%2e/etc/passwd",
            "/app.js/../../ledger.json",
            "/ledger.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path).status, 404)


class WriteChecksTestCase(unittest.TestCase):
    """The pure checks a review decision passes before its body is read."""

    def good(self, **changes: str | None) -> dict[str, str | None]:
        return {
            "Origin": f"http://127.0.0.1:{PORT}",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json; charset=utf-8",
            console_server.CSRF_HEADER: CSRF,
            **changes,
        }

    def test_a_write_from_this_page_passes(self) -> None:
        self.assertIsNone(console_server.write_refusal(self.good(), PORT, CSRF))
        self.assertIsNone(console_server.write_refusal(self.good(**{"Sec-Fetch-Site": None}), PORT, CSRF))
        self.assertIsNone(
            console_server.write_refusal(self.good(Origin=f"http://localhost:{PORT}"), PORT, CSRF)
        )

    def test_anything_else_is_refused(self) -> None:
        for changes, status in (
            ({"Origin": None}, 403),
            ({"Origin": "null"}, 403),
            ({"Origin": f"http://evil.example.com:{PORT}"}, 403),
            ({"Origin": f"http://127.0.0.1:{PORT + 1}"}, 403),
            ({"Origin": f"https://127.0.0.1:{PORT}"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Sec-Fetch-Site": "same-site"}, 403),
            ({"Content-Type": "text/plain"}, 415),
            ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
            ({"Content-Type": None}, 415),
            ({console_server.CSRF_HEADER: None}, 403),
            ({console_server.CSRF_HEADER: TOKEN}, 403),  # the cookie's token is not the CSRF token
            ({console_server.CSRF_HEADER: CSRF + "x"}, 403),
        ):
            with self.subTest(changes=changes):
                refusal = console_server.write_refusal(self.good(**changes), PORT, CSRF)
                self.assertEqual(refusal[0] if refusal else None, status)

    def test_body_length(self) -> None:
        self.assertEqual(console_server.body_length("12"), 12)
        self.assertEqual(console_server.body_length(None)[0], 411)  # type: ignore[index]
        self.assertEqual(console_server.body_length("-1")[0], 400)  # type: ignore[index]
        self.assertEqual(console_server.body_length("ten")[0], 400)  # type: ignore[index]
        too_big = str(console_server.MAX_BODY_BYTES + 1)
        self.assertEqual(console_server.body_length(too_big)[0], 413)  # type: ignore[index]


class KnowledgeServerTestCase(RunningServer):
    """The Knowledge endpoints over the fixture knowledge base, and the one write."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.state / "config" / "knowledge"
        knowledge.configure(self.root, git=False)
        args = knowledge.build_parser().parse_args(["import", str(KG_FIXTURE), "--apply", "--auto-confirm"])
        args.func(args)
        self.app.knowledge = console_kb.Settings(root=self.root, git=False)
        self.app.csrf = CSRF

    def facts_log(self) -> str:
        return (self.root / "facts.jsonl").read_text()

    def waiting(self) -> list[str]:
        model = self.get_json(console_server.REVIEW_PATH)
        return sorted(console_knowledge.batch_fact_ids(model), key=lambda i: int(i[1:]))

    def post(self, body: Any, cookie: str | None = "", **headers: str) -> http.client.HTTPResponse:
        sent = {
            "Origin": f"http://127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            console_server.CSRF_HEADER: CSRF,
            **headers,
        }
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        signed = self.signed_in() if cookie == "" else cookie
        return self.request(console_server.REVIEW_PATH, "POST", cookie=signed, headers=sent, body=data)

    def test_the_overview_entity_and_review(self) -> None:
        overview = self.get_json("/api/knowledge")
        self.assertEqual(overview["totals"]["entities"], 15)
        self.assertEqual([c["title"] for c in overview["cards"]], ["People", "Process", "Product", "Systems"])
        self.assertEqual(overview["review"]["groups"], 3)
        found = self.get_json("/api/knowledge?q=release&type=process")["search"]["entities"]
        self.assertEqual([e["id"] for e in found], ["process/release"])
        page = self.get_json(f"{console_server.ENTITY_PREFIX}{found[0]['key']}")
        self.assertEqual(len(page["flow"]["steps"]), 3)
        review = self.get_json(console_server.REVIEW_PATH)
        self.assertEqual((review["csrf"], review["summary"]["facts"]), (CSRF, 14))

    def test_they_need_the_cookie(self) -> None:
        key = console_knowledge.entity_key("process/release")
        for path in ("/api/knowledge", console_server.REVIEW_PATH, f"{console_server.ENTITY_PREFIX}{key}"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path).status, 401)
        self.assertEqual(
            self.post({"action": "confirm", "facts": self.waiting()[:1]}, cookie=None).status, 401
        )

    def test_entities_only_by_a_known_key(self) -> None:
        for path in (
            "0123456789abcdef",
            "process/release",
            "process%2Frelease",
            "..%2F..%2Fentities.jsonl",
            "",
            console_knowledge.entity_key("process/release") + "0",
        ):
            with self.subTest(path=path):
                response = self.request(f"{console_server.ENTITY_PREFIX}{path}", cookie=self.signed_in())
                self.assertEqual(response.status, 404)

    def test_a_bad_query_is_400(self) -> None:
        for query in ("type=planet", "q=a&q=b"):
            with self.subTest(query=query):
                self.assertEqual(self.request(f"/api/knowledge?{query}", cookie=self.signed_in()).status, 400)

    def test_a_get_never_writes(self) -> None:
        before = self.facts_log()
        fact = self.waiting()[0]
        for query in (f"action=confirm&facts={fact}", f"confirm={fact}"):
            self.get_json(f"{console_server.REVIEW_PATH}?{query}")
        self.assertEqual(self.facts_log(), before)
        self.assertEqual(self.request("/api/knowledge", "POST", cookie=self.signed_in()).status, 405)
        self.assertEqual(self.request(console_server.REVIEW_PATH, "PUT", cookie=self.signed_in()).status, 405)

    def test_a_confirm_round_trip(self) -> None:
        fact = self.waiting()[0]
        response = self.post({"action": "confirm", "facts": [fact]})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        result = json.loads(response.read())
        self.assertEqual((result["confirmed"], result["messages"]), (1, [f"ok: {fact} confirmed"]))
        self.assertNotIn(fact, self.waiting())
        stored = knowledge.load(knowledge.paths()).facts[fact]
        self.assertEqual((stored["status"], stored["reviewed_by"]["ref"]), ("confirmed", "console"))

    def test_version_has_the_knowledge_stamp_and_a_decision_changes_it(self) -> None:
        before = self.get_json("/api/version")
        self.assertTrue(before["knowledge"]["exists"])
        self.assertEqual(self.post({"action": "confirm", "facts": self.waiting()[:1]}).status, 200)
        after = self.get_json("/api/version")
        self.assertNotEqual(after["knowledge"]["stamp"], before["knowledge"]["stamp"])
        self.assertEqual(after["stamp"], before["stamp"])  # the ledger's own stamp did not move

    def test_defer_changes_nothing(self) -> None:
        before = self.facts_log()
        fact = self.waiting()[0]
        result = json.loads(self.post({"action": "defer", "facts": [fact]}).read())
        self.assertEqual(result["left_proposed"], 1)
        self.assertEqual(self.facts_log(), before)

    def test_forged_or_malformed_writes_are_refused_and_change_nothing(self) -> None:
        before = self.facts_log()
        decision = {"action": "confirm", "facts": self.waiting()[:1]}
        for headers, status in (
            ({"Origin": f"http://evil.example.com:{self.port}"}, 403),
            ({"Origin": ""}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({console_server.CSRF_HEADER: "guess"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(decision, **headers).status, status)
        for body, status in (
            (b"{not json", 400),
            ({"action": "approve", "facts": ["f1"]}, 400),
            ({"action": "confirm", "facts": ["f1"]}, 409),  # confirmed already: not waiting in the batch
            (b"x" * (console_server.MAX_BODY_BYTES + 1), 413),
        ):
            with self.subTest(body=str(body)[:40]):
                self.assertEqual(self.post(body).status, status)
        self.assertEqual(
            self.request(console_server.REVIEW_PATH, "POST", host="evil.example.com").status, 403
        )
        self.assertEqual(self.facts_log(), before)

    def test_the_briefing_and_the_constellation(self) -> None:
        briefing = self.get_json(console_server.BRIEFING_PATH)
        self.assertEqual(briefing["totals"]["facts"], 20)
        self.assertEqual(
            [c["card"] for c in briefing["coverage"]], ["people", "process", "product", "systems"]
        )
        self.assertFalse(briefing["rediscovery"]["measured"])
        self.assertNotIn("csrf", briefing)  # only the review model carries the token
        stars = self.get_json(console_server.CONSTELLATION_PATH)
        self.assertEqual((len(stars["nodes"]), stars["more"]), (15, 0))
        self.assertTrue(all(0 <= e["from"] < 15 and 0 <= e["to"] < 15 for e in stars["edges"]))

    def test_the_briefing_and_the_constellation_are_read_only(self) -> None:
        before = self.facts_log()
        for path in (console_server.BRIEFING_PATH, console_server.CONSTELLATION_PATH):
            with self.subTest(path=path):
                self.assertEqual(self.request(path).status, 401)
                self.assertEqual(self.request(path, "POST", cookie=self.signed_in()).status, 405)
                self.get_json(f"{path}?action=confirm&facts=f1")
        self.assertEqual(self.facts_log(), before)

    def test_without_a_knowledge_base(self) -> None:
        self.app.knowledge = console_kb.Settings(root=self.state / "nothing", git=False)
        self.assertFalse(self.get_json("/api/knowledge")["exists"])
        self.assertFalse(self.get_json(console_server.BRIEFING_PATH)["exists"])
        self.assertFalse(self.get_json(console_server.CONSTELLATION_PATH)["exists"])
        self.assertFalse(self.get_json(console_server.REVIEW_PATH)["exists"])
        self.assertEqual(self.post({"action": "confirm", "facts": ["f1"]}).status, 404)
        self.assertFalse((self.state / "nothing").exists())


if __name__ == "__main__":
    unittest.main()
