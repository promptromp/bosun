"""Unit tests for expired-login detection: what lib/claude_state.py reads (error replies in
transcripts, `claude auth status`, the login check request, the text fallback) and how
lib/login.py combines it (verdicts, the check-in gate, recovery). Fictional transcripts only."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import agent_files  # noqa: E402
import claude_state  # noqa: E402
import login  # noqa: E402
from claude_state import (  # noqa: E402
    AUTH,
    NETWORK,
    OTHER,
    OVERLOADED,
    PATTERN,
    RATE_LIMIT,
    STRUCTURED,
    USAGE_LIMIT,
)


T0 = datetime(2026, 1, 10, 17, 0, tzinfo=UTC)  # a Saturday
WORKDIR = "/work/invoice-exporter"
SESSION = "00000000-0000-0000-0000-00000000000a"


def stamp(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def prompt(minutes: float, text: str = "/bosun:chores check-in") -> dict[str, Any]:
    return {"type": "user", "timestamp": stamp(minutes), "message": {"content": text}}


def reply(minutes: float, text: str = "Checked in: nothing new.", **extra: Any) -> dict[str, Any]:
    message = {"model": "claude-model", "content": [{"type": "text", "text": text}]}
    return {"type": "assistant", "timestamp": stamp(minutes), "message": message, **extra}


def error_reply(minutes: float, text: str, **fields: Any) -> dict[str, Any]:
    """A failed request, as Claude Code writes it: a synthetic reply flagged as an API error."""
    message = {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}
    entry = {"type": "assistant", "timestamp": stamp(minutes), "isApiErrorMessage": True, "message": message}
    return {**entry, **fields}


# The error shapes seen in real transcripts (texts reworded; only the fields matter).
REVOKED: dict[str, Any] = {"error": "authentication_failed", "apiErrorStatus": 401}
EXPIRED: dict[str, Any] = {"error": "authentication_failed"}
SPEND_LIMIT: dict[str, Any] = {
    "error": "rate_limit",
    "apiError": "usage_limit_reached",
    "apiErrorStatus": 429,
}
OVERLOAD: dict[str, Any] = {"error": "server_error", "apiErrorStatus": 529}
NO_RESPONSE: dict[str, Any] = {"error": "server_error", "apiError": "no_response"}
DROPPED: dict[str, Any] = {"error": "server_error"}


def write(path: Path, *entries: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patch = mock.patch.object(claude_state, "PROJECTS_DIR", self.root / "projects")
        patch.start()
        self.addCleanup(patch.stop)
        self.transcript = claude_state.transcript_path(WORKDIR, SESSION)

    def add(self, *entries: dict[str, Any]) -> Path:
        return write(self.transcript, *entries)


class ErrorCategoryTest(unittest.TestCase):
    def test_each_shape_has_its_category(self) -> None:
        shapes: dict[str, list[dict[str, Any]]] = {
            AUTH: [
                REVOKED,
                EXPIRED,
                {"apiErrorStatus": 401},
                {"error": "x", "apiError": "org_disabled_credential"},
            ],
            USAGE_LIMIT: [SPEND_LIMIT, {"apiError": "usage_limit_reached"}],
            RATE_LIMIT: [{"error": "rate_limit"}, {"apiErrorStatus": 429}],
            OVERLOADED: [OVERLOAD],
            NETWORK: [NO_RESPONSE, DROPPED],
            OTHER: [
                {"error": "billing_error"},
                {"error": "invalid_request", "apiErrorStatus": 400},
                {"error": "server_error", "apiErrorStatus": 500},
                {"error": "unknown"},
            ],
        }
        for category, fields_list in shapes.items():
            for fields in fields_list:
                with self.subTest(fields=fields):
                    found = claude_state.error_category(
                        fields.get("error"), fields.get("apiError"), fields.get("apiErrorStatus")
                    )
                    self.assertEqual(found, category)

    def test_no_structured_fields_means_no_category(self) -> None:
        self.assertIsNone(claude_state.error_category(None, "", None))
        self.assertIsNone(claude_state.error_category({"odd": 1}, ["x"], True))


class LastApiErrorTest(TmpCase):
    def test_a_normal_latest_reply_is_healthy(self) -> None:
        self.add(prompt(0), reply(1))
        self.assertIsNone(claude_state.last_api_error(self.transcript))

    def test_an_empty_transcript_is_healthy(self) -> None:
        self.add({"type": "custom-title", "customTitle": "orch"})
        self.assertIsNone(claude_state.last_api_error(self.transcript))

    def test_the_incident_shape_revoked_then_expired(self) -> None:
        self.add(prompt(0), reply(1), prompt(60))
        self.add(error_reply(60, "OAuth token revoked · Please run /login", **REVOKED))
        for hour in range(2, 6):
            self.add(
                prompt(60 * hour), error_reply(60 * hour, "Login expired · Please run /login", **EXPIRED)
            )
        error = claude_state.last_api_error(self.transcript)
        assert error is not None
        self.assertEqual((error.category, error.source), (AUTH, STRUCTURED))
        self.assertEqual(error.since, T0 + timedelta(minutes=60))
        self.assertEqual(error.at, T0 + timedelta(minutes=300))
        self.assertEqual(error.code, "authentication_failed")

    def test_a_normal_reply_ends_the_outage(self) -> None:
        self.add(prompt(0), error_reply(0, "Login expired", **EXPIRED), prompt(30, "hello again"), reply(31))
        self.assertIsNone(claude_state.last_api_error(self.transcript))

    def test_each_category_from_a_transcript(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            (SPEND_LIMIT, USAGE_LIMIT),
            (OVERLOAD, OVERLOADED),
            (NO_RESPONSE, NETWORK),
            (DROPPED, NETWORK),
        ]
        for fields, category in cases:
            with self.subTest(category=category):
                self.transcript.unlink(missing_ok=True)
                self.add(prompt(0), reply(1), prompt(2), error_reply(3, "API Error: something", **fields))
                error = claude_state.last_api_error(self.transcript)
                assert error is not None
                self.assertEqual(error.category, category)

    def test_since_restarts_when_the_category_changes(self) -> None:
        self.add(error_reply(0, "API Error: no response", **NO_RESPONSE))
        self.add(error_reply(10, "Login expired", **EXPIRED), error_reply(20, "Login expired", **EXPIRED))
        error = claude_state.last_api_error(self.transcript)
        assert error is not None
        self.assertEqual((error.category, error.since), (AUTH, T0 + timedelta(minutes=10)))

    def test_a_subagent_entry_does_not_count(self) -> None:
        self.add(error_reply(0, "Login expired", **EXPIRED), reply(5, isSidechain=True))
        error = claude_state.last_api_error(self.transcript)
        self.assertEqual(error.category if error else None, AUTH)

    def test_a_normal_reply_quoting_the_error_text_is_not_an_error(self) -> None:
        self.add(reply(0, "The session said 'Login expired · Please run /login' yesterday; it works now."))
        self.assertIsNone(claude_state.last_api_error(self.transcript))

    def test_an_error_without_fields_falls_back_to_its_text(self) -> None:
        self.add(error_reply(0, "Your OAuth token has expired. Please run /login"))
        error = claude_state.last_api_error(self.transcript)
        assert error is not None
        self.assertEqual((error.category, error.source), (AUTH, PATTERN))
        self.transcript.unlink()
        self.add(error_reply(0, "API Error: the request was too large"))
        error = claude_state.last_api_error(self.transcript)
        self.assertEqual((error.category, error.source) if error else None, (OTHER, PATTERN))

    def test_a_long_transcript_is_read_from_the_end_across_chunks(self) -> None:
        self.add(prompt(0), reply(1))
        self.add(*[reply(2, "x" * 500) for _ in range(40)])
        self.add(*[error_reply(10 + n, "Login expired", **EXPIRED) for n in range(30)])
        with mock.patch.object(agent_files, "TAIL_CHUNK_BYTES", 1024):
            error = claude_state.last_api_error(self.transcript)
        assert error is not None
        self.assertEqual(error.since, T0 + timedelta(minutes=10))

    def test_damaged_lines_are_skipped(self) -> None:
        self.add(error_reply(0, "Login expired", **EXPIRED))
        with self.transcript.open("a") as fh:
            fh.write('{"type": "assistant", "broken\n')
        error = claude_state.last_api_error(self.transcript)
        self.assertEqual(error.category if error else None, AUTH)

    def test_the_command_prints_json_or_exits_3(self) -> None:
        self.add(error_reply(0, "Login expired", **EXPIRED))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(claude_state.main(["api-error", str(self.transcript)]), 0)
        self.assertEqual(json.loads(out.getvalue())["category"], AUTH)
        self.add(reply(5))
        self.assertEqual(claude_state.main(["api-error", str(self.transcript)]), agent_files.NOT_FOUND)


class AuthStatusTest(unittest.TestCase):
    def test_logged_in(self) -> None:
        status = claude_state.parse_auth_status('{"loggedIn": true, "authMethod": "claude.ai"}')
        self.assertEqual(status, claude_state.AuthStatus(True, "claude.ai"))

    def test_logged_out(self) -> None:
        status = claude_state.parse_auth_status('{"loggedIn": false, "authMethod": "none"}')
        self.assertIs(status.logged_in, False)

    def test_malformed(self) -> None:
        for text in ["", "2.1.0 (Claude Code)", "[]", '{"authMethod": "x"}', '{"loggedIn": "yes"}']:
            with self.subTest(text=text):
                self.assertIsNone(claude_state.parse_auth_status(text).logged_in)

    def test_a_missing_binary_is_unknown(self) -> None:
        self.assertIsNone(claude_state.auth_status("/nonexistent/claude").logged_in)


class FallbackPatternTest(unittest.TestCase):
    AUTH_ERRORS = (
        "Login expired · Please run /login",
        "OAuth token revoked · Please run /login",
        "Not logged in · Please run /login",
        "Invalid API key · Fix external API key",
        "API Error: 401 Unauthorized",
        "Error: your access token has expired, log in again",
        "Authentication failed: invalid credentials",
        "login required",
        "Unauthorized (401): the oauth token is invalid",
    )
    NOT_AUTH_ERRORS = (
        "Rate limit reached · resets 5pm",
        "API Error: 529 Overloaded. This is a server-side issue.",
        "API Error: No response from API (waited 3m)",
        "I fixed the login form: invalid emails are now refused.",
        "Run `make login-page` to rebuild the login page.",
        "The session expired before the export finished; I restarted it.",
        "Here is the fix:\n```python\ndef login(user):\n    raise AuthError('authentication failed')\n```",
        "Please run /login " + "and then describe the export in detail. " * 10,
        "",
    )

    def test_auth_error_wordings_match(self) -> None:
        for text in self.AUTH_ERRORS:
            with self.subTest(text=text):
                self.assertTrue(claude_state.looks_like_auth_error(text))

    def test_other_text_does_not_match(self) -> None:
        for text in self.NOT_AUTH_ERRORS:
            with self.subTest(text=text):
                self.assertFalse(claude_state.looks_like_auth_error(text))

    def test_the_pane_fallback_reads_the_last_lines(self) -> None:
        pane = "\n".join(
            [
                *["older output"] * 20,
                "> /bosun:chores check-in",
                "  ⎿  Login expired · Please run /login",
                "",
            ]
        )
        self.assertTrue(claude_state.pane_auth_error(pane))
        self.assertFalse(claude_state.pane_auth_error(pane + "\n".join(["fresh output"] * 15)))
        self.assertFalse(claude_state.pane_auth_error("> fix the login form\n⏺ Done: the login form works."))


def stream(*entries: dict[str, Any]) -> list[str]:
    return [json.dumps({"type": "system", "subtype": "init"}), *(json.dumps(e) for e in entries)]


def result(is_error: bool, text: str = "OK", status: int | None = None) -> dict[str, Any]:
    return {
        "type": "result",
        "is_error": is_error,
        "result": text,
        "api_error_status": status,
        "total_cost_usd": 0.001,
    }


class ProbeTest(unittest.TestCase):
    def test_a_normal_answer_is_ok(self) -> None:
        probe = claude_state.parse_probe(stream(reply(0, "OK"), result(False)))
        self.assertEqual((probe.outcome, probe.cost_usd), ("ok", 0.001))

    def test_not_logged_in(self) -> None:
        failed = {"type": "assistant", "error": "authentication_failed", "message": {"content": []}}
        probe = claude_state.parse_probe(stream(failed, result(True, "Not logged in")))
        self.assertEqual((probe.outcome, probe.source), (AUTH, STRUCTURED))

    def test_the_status_comes_from_the_result(self) -> None:
        failed = {"type": "assistant", "error": "authentication_failed", "message": {"content": []}}
        probe = claude_state.parse_probe(stream(failed, result(True, "Invalid API key", 401)))
        self.assertEqual(probe.code, "authentication_failed/401")

    def test_a_result_with_only_a_status(self) -> None:
        self.assertEqual(claude_state.parse_probe(stream(result(True, "x", 401))).outcome, AUTH)
        self.assertEqual(claude_state.parse_probe(stream(result(True, "x", 529))).outcome, OVERLOADED)

    def test_a_result_with_only_text_falls_back_to_the_pattern(self) -> None:
        probe = claude_state.parse_probe(stream(result(True, "OAuth token revoked · Please run /login")))
        self.assertEqual((probe.outcome, probe.source), (AUTH, PATTERN))

    def test_no_result_is_unknown_even_if_the_output_mentions_logging_in(self) -> None:
        probe = claude_state.parse_probe(
            ["Settings could not be loaded. Run `claude auth login` or check the network."]
        )
        self.assertEqual(probe.outcome, "unknown")

    def test_the_request_is_small_and_isolated(self) -> None:
        command = claude_state.probe_command("claude")
        for flag in ("--no-session-persistence", "--strict-mcp-config", "--max-budget-usd", "--tools"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--model") + 1], claude_state.PROBE_MODEL)


class FakeClaude:
    """`claude auth status` and the login check request, as an Env sees them."""

    def __init__(self) -> None:
        self.logged_in: bool | None = True
        self.probe_outcome = "ok"
        self.probes = 0

    def auth_status(self, _bin: str) -> claude_state.AuthStatus:
        return claude_state.AuthStatus(self.logged_in, "claude.ai")

    def probe(self, _bin: str) -> claude_state.ProbeResult:
        self.probes += 1
        return claude_state.ProbeResult(self.probe_outcome, code="fake")


class GateTest(TmpCase):
    """The check-in loop's view: `login.py window` for the orchestrator, over time."""

    def setUp(self) -> None:
        super().setUp()
        self.claude = FakeClaude()
        self.now = T0 + timedelta(hours=10)
        self.add(prompt(0), reply(1))

    def env(self) -> login.Env:
        return login.Env(
            self.root / "state",
            "claude",
            now=lambda: self.now,
            auth_status=self.claude.auth_status,
            probe=self.claude.probe,
            record=lambda _pid: {"cwd": WORKDIR, "sessionId": SESSION},
            sleep=lambda _seconds: None,
        )

    def window(self, *options: str) -> tuple[str, str]:
        args = login.build_parser().parse_args(["window", "orch", "101", *options])
        verdict, change = login.check_window(self.env(), args, "")
        return verdict.state, change

    def login_error(self, minutes: float) -> None:
        self.add(prompt(minutes), error_reply(minutes, "Login expired · Please run /login", **EXPIRED))

    def test_healthy(self) -> None:
        self.assertEqual(self.window(), ("ok", "-"))

    def test_an_auth_error_stops_check_ins_once_then_stays_quiet(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window(), ("needs-login", "new"))
        self.login_error(120)
        self.assertEqual(self.window(), ("needs-login", "same"))
        self.assertEqual(
            json.loads((self.root / "state" / login.STATE_FILE).read_text())["gated"],
            {"orch": stamp(60).replace("Z", "+00:00")},
        )

    def test_a_normal_reply_resumes_them(self) -> None:
        self.login_error(60)
        self.window()
        self.add(prompt(90, "are you there?"), reply(91))
        self.assertEqual(self.window(), ("ok", "recovered"))
        self.assertEqual(self.window(), ("ok", "-"))

    def test_a_login_check_that_works_resumes_them(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window("--probe"), ("ok", "-"))
        self.assertEqual(self.claude.probes, 1)

    def test_a_login_check_that_fails_keeps_them_stopped_and_is_not_repeated_too_soon(self) -> None:
        self.login_error(60)
        self.claude.probe_outcome = AUTH
        self.assertEqual(self.window("--probe"), ("needs-login", "new"))
        self.now += timedelta(minutes=10)
        self.assertEqual(self.window("--probe"), ("needs-login", "same"))
        self.assertEqual(self.claude.probes, 1)
        self.now += timedelta(minutes=login.PROBE_EVERY_MINUTES)
        self.claude.probe_outcome = "ok"
        self.assertEqual(self.window("--probe"), ("ok", "recovered"))
        self.assertEqual(self.claude.probes, 2)

    def test_no_login_check_while_auth_status_says_logged_out(self) -> None:
        self.login_error(60)
        self.claude.logged_in = False
        self.assertEqual(self.window("--probe"), ("needs-login", "new"))
        self.assertEqual(self.claude.probes, 0)

    def test_a_retried_check_in_that_fails_again_stops_them_again(self) -> None:
        self.login_error(60)
        self.window("--probe")  # the check works: resume
        self.login_error(self.minutes_from_t0(self.now) + 1)
        self.now += timedelta(minutes=2)
        self.assertEqual(self.window(), ("needs-login", "new"))

    def minutes_from_t0(self, moment: datetime) -> float:
        return (moment - T0).total_seconds() / 60

    def test_after_a_delivery_the_reply_to_it_decides(self) -> None:
        sent_at = (T0 + timedelta(minutes=59)).timestamp()
        self.login_error(60)
        self.assertEqual(self.window("--after", str(sent_at), "--wait", "0"), ("needs-login", "new"))

    def test_a_dry_run_records_nothing(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window("--dry-run", "--probe"), ("needs-login", "new"))
        self.assertFalse((self.root / "state" / login.STATE_FILE).exists())
        self.assertEqual(self.claude.probes, 0)

    def test_without_a_transcript_the_pane_is_read(self) -> None:
        self.transcript.unlink()
        args = login.build_parser().parse_args(["window", "orch", "101"])
        verdict, _ = login.check_window(self.env(), args, "> hi\n  ⎿  Not logged in · Please run /login")
        self.assertEqual(verdict.state, "needs-login")
        self.assertIn("pattern fallback", verdict.detail)
        verdict, _ = login.check_window(self.env(), args, "> hi\n⏺ Hello!")
        self.assertEqual(verdict.state, "unknown")


class GlobalTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.claude = FakeClaude()
        self.env = login.Env(
            Path(tmp.name),
            "claude",
            now=lambda: T0,
            auth_status=self.claude.auth_status,
            probe=self.claude.probe,
            record=lambda _pid: None,
        )

    def test_logged_in_and_the_check_works(self) -> None:
        self.assertEqual(login.check_global(self.env, probe_now=True).state, "ok")
        self.assertEqual(self.claude.probes, 1)

    def test_logged_out_needs_no_request(self) -> None:
        self.claude.logged_in = False
        self.assertEqual(login.check_global(self.env, probe_now=True).state, "needs-login")
        self.assertEqual(self.claude.probes, 0)

    def test_a_revoked_token_shows_only_in_the_request(self) -> None:
        self.claude.probe_outcome = AUTH
        self.assertEqual(login.check_global(self.env, probe_now=True).state, "needs-login")
        self.assertEqual(login.check_global(self.env, probe_now=False).state, "needs-login")

    def test_no_answer_is_unknown(self) -> None:
        self.claude.probe_outcome = NETWORK
        self.assertEqual(login.check_global(self.env, probe_now=True).state, "unknown")
        self.claude.logged_in = None
        self.assertEqual(login.check_global(self.env, probe_now=False).state, "unknown")

    def test_status_lists_the_login_and_each_window(self) -> None:
        text = login.render_status(self.env, [("orch", "101")])
        self.assertTrue(text.startswith("login"))
        self.assertIn("orch", text)


if __name__ == "__main__":
    unittest.main()
