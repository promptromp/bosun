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
    CREDIT,
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
OTHER_SESSION = "00000000-0000-0000-0000-00000000000b"  # a second window's
THIRD_SESSION = "00000000-0000-0000-0000-00000000000c"  # and a third's


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
# An API key whose organization has no credit that covers Claude Code (2.1.308; the text is
# "Credit balance is too low").
NO_CREDIT: dict[str, Any] = {"error": "billing_error", "apiErrorStatus": 400}
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
            USAGE_LIMIT: [
                SPEND_LIMIT,
                {"apiError": "usage_limit_reached"},
                {"error": "billing_error", "apiError": "usage_limit_reached"},
            ],
            CREDIT: [NO_CREDIT, {"error": "billing_error"}],
            RATE_LIMIT: [{"error": "rate_limit"}, {"apiErrorStatus": 429}],
            OVERLOADED: [OVERLOAD],
            NETWORK: [NO_RESPONSE, DROPPED],
            OTHER: [
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
            (NO_CREDIT, CREDIT),
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

    def test_a_row_of_credit_errors_gives_since(self) -> None:
        self.add(prompt(0), reply(1))
        for minutes in (30, 60, 90):
            self.add(prompt(minutes), error_reply(minutes, "Credit balance is too low", **NO_CREDIT))
        error = claude_state.last_api_error(self.transcript)
        assert error is not None
        self.assertEqual(
            (error.category, error.source, error.code), (CREDIT, STRUCTURED, "billing_error/400")
        )
        self.assertEqual((error.since, error.at), (T0 + timedelta(minutes=30), T0 + timedelta(minutes=90)))

    def test_a_row_broken_elsewhere_starts_after_that_moment(self) -> None:
        for minutes in (30, 60, 90):
            self.add(prompt(minutes), error_reply(minutes, "Credit balance is too low", **NO_CREDIT))
        error = claude_state.last_api_error(self.transcript, after=T0 + timedelta(minutes=45))
        self.assertEqual(error.since if error else None, T0 + timedelta(minutes=60))
        error = claude_state.last_api_error(self.transcript, after=T0 + timedelta(minutes=95))
        self.assertEqual(error.since if error else None, T0 + timedelta(minutes=30))  # after the whole row

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

    def test_an_api_key_helper_is_the_one_setting_it_keeps(self) -> None:
        # No settings files, so no allow rules or hooks of mine; but a helper that signs Claude Code in
        # with an API key is how it authenticates: without it the check said a working sign-in needs a
        # login. The helper is a command that prints a key, not a key.
        helper = {"apiKeyHelper": "cat ~/.key"}  # pragma: allowlist secret gitleaks:allow
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            with unittest.mock.patch.object(claude_state, "SETTINGS_FILE", settings):
                self.assertNotIn("--settings", claude_state.probe_command("claude"))
                settings.write_text(json.dumps({**helper, "hooks": {"Stop": []}}))
                command = claude_state.probe_command("claude")
        self.assertEqual(command[command.index("--setting-sources") + 1], "")
        self.assertEqual(json.loads(command[command.index("--settings") + 1]), helper)


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


class WindowCase(TmpCase):
    """`login.py window` for the orchestrator, over time."""

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
            record=lambda pid: {"cwd": WORKDIR, "sessionId": OTHER_SESSION if str(pid) == "102" else SESSION},
            sleep=lambda _seconds: None,
        )

    def window(self, *options: str) -> tuple[str, str]:
        args = login.build_parser().parse_args(["window", "orch", "101", *options])
        verdict, change = login.check_window(self.env(), args, "")
        return verdict.state, change

    def login_error(self, minutes: float) -> None:
        self.add(prompt(minutes), error_reply(minutes, "Login expired · Please run /login", **EXPIRED))

    def credit_error(self, minutes: float) -> None:
        self.add(prompt(minutes), error_reply(minutes, "Credit balance is too low", **NO_CREDIT))


class GateTest(WindowCase):
    """The check-in loop's view."""

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


class OtherWindowsTest(WindowCase):
    """--also: the other windows' replies count for the gate too, as the watchdog reads them."""

    def test_a_reply_in_another_window_lets_the_gate_go(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window(), ("needs-login", "new"))
        write(claude_state.transcript_path(WORKDIR, OTHER_SESSION), prompt(90, "after /login"), reply(91))
        self.assertEqual(self.window(), ("needs-login", "same"))  # its own replies alone
        self.assertEqual(self.window("--also", "alpha=102"), ("ok", "recovered"))

    def test_the_window_itself_among_them_is_read_once(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window("--also", "orch=101"), ("needs-login", "new"))

    def test_a_malformed_value_is_a_usage_error(self) -> None:
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as raised:
            login.build_parser().parse_args(["window", "orch", "101", "--also", "alpha"])
        self.assertEqual(raised.exception.code, 2)


class UnreadableTranscriptTest(WindowCase):
    """A transcript that is found but cannot be read (no permission, or gone since): never a crash."""

    def unreadable(self, path: Path) -> None:
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)

    def test_another_windows_counts_for_nothing(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window(), ("needs-login", "new"))
        other = write(claude_state.transcript_path(WORKDIR, OTHER_SESSION), prompt(90), reply(91))
        self.unreadable(other)
        self.assertEqual(self.window("--also", "alpha=102"), ("needs-login", "same"))
        verdicts = login.window_verdicts({"orch": self.transcript, "alpha": other}, None)
        self.assertEqual(verdicts["alpha"], login.UNREADABLE)
        self.assertEqual(verdicts["orch"].state, "needs-login")

    def test_its_own_is_unknown(self) -> None:
        self.login_error(60)
        self.unreadable(self.transcript)
        self.assertEqual(self.window("--after", "0", "--wait", "1"), ("unknown", "-"))

    def test_one_gone_after_it_was_found_is_unknown(self) -> None:
        with mock.patch.object(claude_state, "last_api_error", side_effect=FileNotFoundError):
            self.assertEqual(self.window(), ("unknown", "-"))


class NoCreditTest(WindowCase):
    """A window whose every reply is "Credit balance too low": its own state, never gated."""

    def test_its_verdict_says_since_when(self) -> None:
        self.credit_error(60)
        self.credit_error(120)
        verdict = login.window_verdicts({"orch": self.transcript}, None)["orch"]
        since = T0 + timedelta(minutes=60)
        self.assertEqual((verdict.state, verdict.since), (login.NO_CREDIT, since))
        self.assertEqual(
            verdict.detail,
            f"every reply since {login.shown(since)} is 'credit balance too low' (billing_error/400)",
        )

    def test_a_normal_reply_ends_it(self) -> None:
        self.credit_error(60)
        self.add(prompt(90, "carry on"), reply(91))
        self.assertEqual(login.window_verdicts({"orch": self.transcript}, None)["orch"].state, login.OK)

    def test_check_ins_are_not_held_back(self) -> None:
        self.credit_error(60)
        self.assertEqual(self.window("--probe"), ("no-credit", "-"))
        self.assertEqual(self.claude.probes, 0)
        self.assertEqual(json.loads((self.root / "state" / login.STATE_FILE).read_text())["gated"], {})

    def test_a_window_gated_on_its_login_is_gated_no_more(self) -> None:
        self.login_error(60)
        self.assertEqual(self.window(), ("needs-login", "new"))
        self.credit_error(90)  # a /login with an API key whose organization has no credit
        self.assertEqual(self.window(), ("no-credit", "recovered"))
        self.assertEqual(self.window(), ("no-credit", "-"))

    def test_a_login_check_that_works_does_not_end_it(self) -> None:
        failed = login.Probe(T0 + timedelta(minutes=30), AUTH, STRUCTURED, "authentication_failed")
        (self.root / "state").mkdir()
        login.State(probe=failed.to_json()).save(self.root / "state")
        self.credit_error(60)
        self.assertEqual(self.window("--probe"), ("no-credit", "-"))
        self.assertEqual(self.claude.probes, 1)

    def test_the_command_has_an_exit_code_of_its_own(self) -> None:
        self.credit_error(60)
        args = login.build_parser().parse_args(["window", "orch", "101"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = login.run(args, self.env())
        self.assertEqual(code, 4)
        self.assertTrue(out.getvalue().startswith("no-credit - every reply since "))
        self.assertEqual(len(set(login.EXIT_CODES.values())), len(login.EXIT_CODES))
        self.assertNotIn(2, login.EXIT_CODES.values())  # 2 is a usage error

    def test_status_shows_it(self) -> None:
        self.credit_error(60)
        lines = login.render_status(self.env(), [("orch", "101")]).splitlines()
        self.assertRegex(
            lines[1],
            r"^orch +no-credit +every reply since .+ is 'credit balance too low' \(billing_error/400\)$",
        )


class LastAnswerTest(TmpCase):
    def test_the_latest_reply_the_api_gave(self) -> None:
        self.add(prompt(0), reply(1), prompt(30), error_reply(30, "Login expired", **EXPIRED))
        self.assertEqual(claude_state.last_answer_at(self.transcript), T0 + timedelta(minutes=1))

    def test_claude_codes_own_notices_are_no_answer(self) -> None:
        notice = reply(5, "No response requested.")
        notice["message"]["model"] = claude_state.SYNTHETIC_MODEL
        self.add(prompt(0), reply(1), notice)
        self.assertEqual(claude_state.last_answer_at(self.transcript), T0 + timedelta(minutes=1))

    def test_none_without_one(self) -> None:
        self.add(prompt(0), error_reply(0, "Credit balance is too low", **NO_CREDIT))
        self.assertIsNone(claude_state.last_answer_at(self.transcript))


class AcrossWindowsTest(TmpCase):
    """The login and the credit are the Mac's: what one window shows clears the others."""

    def setUp(self) -> None:
        super().setUp()
        self.other = claude_state.transcript_path(WORKDIR, OTHER_SESSION)

    def verdicts(self, probe: login.Probe | None = None) -> dict[str, login.Verdict]:
        return login.window_verdicts({"orch": self.transcript, "alpha": self.other, "beta": None}, probe)

    def login_errors_overnight(self) -> None:
        self.add(prompt(0), reply(1), prompt(60), error_reply(60, "Login expired", **EXPIRED))
        write(self.other, prompt(0), reply(2), prompt(70), error_reply(70, "Login expired", **EXPIRED))

    def test_a_login_check_that_worked_clears_an_idle_window_too(self) -> None:
        self.login_errors_overnight()
        worked = login.Probe(T0 + timedelta(hours=10), claude_state.PROBE_OK)
        verdicts = self.verdicts(worked)
        self.assertEqual([v.state for v in verdicts.values()], ["ok", "ok", "unknown"])
        self.assertEqual(
            verdicts["alpha"].detail,
            f"a login check at {login.shown(worked.at)} worked, after its last login error",
        )

    def test_a_reply_in_another_window_clears_it(self) -> None:
        self.login_errors_overnight()
        self.add(prompt(600, "carry on"), reply(601))
        alpha = self.verdicts()["alpha"]
        self.assertEqual(alpha.state, "ok")
        reply_at = login.shown(T0 + timedelta(minutes=601))
        self.assertEqual(alpha.detail, f"orch got a reply at {reply_at}, after its last login error")

    def test_a_later_login_error_anywhere_keeps_them_all(self) -> None:
        self.login_errors_overnight()
        self.add(prompt(600, "carry on"), reply(601))
        self.add(prompt(620), error_reply(620, "Login expired", **EXPIRED))
        worked = login.Probe(T0 + timedelta(minutes=610), claude_state.PROBE_OK)
        verdicts = self.verdicts(worked)
        self.assertEqual((verdicts["orch"].state, verdicts["alpha"].state), ("needs-login", "needs-login"))

    def test_a_reply_in_another_window_shows_that_credit_is_back(self) -> None:
        self.add(prompt(0), reply(1), prompt(60), error_reply(60, "Credit balance is too low", **NO_CREDIT))
        write(self.other, prompt(90), reply(91))
        orch = self.verdicts()["orch"]
        self.assertEqual(orch.state, "ok")
        reply_at = login.shown(T0 + timedelta(minutes=91))
        expected = f"alpha got a reply at {reply_at}, after its last 'credit balance too low'"
        self.assertEqual(orch.detail, expected)

    def test_a_login_check_that_worked_says_nothing_of_credit(self) -> None:
        self.add(prompt(0), reply(1), prompt(60), error_reply(60, "Credit balance is too low", **NO_CREDIT))
        write(self.other, prompt(0), reply(2))
        worked = login.Probe(T0 + timedelta(hours=10), claude_state.PROBE_OK)
        self.assertEqual(self.verdicts(worked)["orch"].state, "no-credit")

    def test_a_notice_in_another_window_is_no_reply(self) -> None:
        self.login_errors_overnight()
        notice = reply(600, "No response requested.")
        notice["message"]["model"] = claude_state.SYNTHETIC_MODEL
        write(self.other, notice)
        self.assertEqual(self.verdicts()["orch"].state, "needs-login")

    def test_a_row_starts_again_after_a_reply_in_another_window(self) -> None:
        for fields, text in ((NO_CREDIT, "Credit balance is too low"), (EXPIRED, "Login expired")):
            with self.subTest(text=text):
                self.transcript.unlink(missing_ok=True)
                self.other.unlink(missing_ok=True)
                self.add(prompt(0), reply(1), prompt(60), error_reply(60, text, **fields))
                write(self.other, prompt(90), reply(91))  # it worked again here
                self.add(prompt(120), error_reply(120, text, **fields))  # and failed again there
                orch = self.verdicts()["orch"]
                self.assertNotEqual(orch.state, "ok")
                self.assertEqual(orch.since, T0 + timedelta(minutes=120))
                self.assertIn(f"since {login.shown(T0 + timedelta(minutes=120))}", orch.detail)

    def test_a_sign_after_a_whole_row_keeps_its_since(self) -> None:
        # alpha's reply came after orch's errors, but beta failed after it: orch is not cleared, and its
        # row (and so the watchdog's key for it) stays as it was.
        third = claude_state.transcript_path(WORKDIR, THIRD_SESSION)
        self.add(prompt(0), reply(1))
        self.add(*(error_reply(m, "Credit balance is too low", **NO_CREDIT) for m in (60, 70)))
        write(self.other, prompt(90), reply(91))
        write(third, prompt(100), error_reply(100, "Credit balance is too low", **NO_CREDIT))
        orch = login.window_verdicts({"orch": self.transcript, "alpha": self.other, "beta": third}, None)[
            "orch"
        ]
        self.assertEqual((orch.state, orch.since), ("no-credit", T0 + timedelta(minutes=60)))

    def test_status_reads_every_window(self) -> None:
        self.login_errors_overnight()
        self.add(prompt(600, "carry on"), reply(601))
        sessions = {"101": SESSION, "102": OTHER_SESSION}
        env = login.Env(
            self.root / "state",
            "claude",
            auth_status=lambda _bin: claude_state.AuthStatus(True, "claude.ai"),
            record=lambda pid: {"cwd": WORKDIR, "sessionId": sessions[str(pid)]},
        )
        lines = login.render_status(env, [("orch", "101"), ("alpha", "102")]).splitlines()
        self.assertRegex(lines[2], r"^alpha +ok +orch got a reply at ")


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
