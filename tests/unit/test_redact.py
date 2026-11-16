"""Unit tests for lib/redact.py: every way a secret is commonly written down is redacted, and
ordinary prose that only mentions a secret word is left alone. All values are made up, and built
from pieces so that no scanner reads them as real."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import claude_state  # noqa: E402
import redact as redact_module  # noqa: E402
from redact import redact  # noqa: E402


FAKE = "a1B2c3D4" * 4  # a made-up value, 32 characters


class SecretNamesTest(unittest.TestCase):
    def check(self, raw: str, redacted: str) -> None:
        self.assertEqual(redact(raw), redacted)

    def test_a_quoted_value(self) -> None:
        self.check(f'export JIRA_API_TOKEN="{FAKE}"', 'export JIRA_API_TOKEN="<redacted>"')

    def test_a_single_quoted_value(self) -> None:
        self.check(f"export GITHUB_TOKEN='{FAKE}'", "export GITHUB_TOKEN='<redacted>'")

    def test_a_yaml_quoted_value_with_spaces(self) -> None:
        self.check('password: "correct horse battery"', 'password: "<redacted>"')  # pragma: allowlist secret

    def test_json(self) -> None:
        made_up = '{"password": "hunter2hunter2", "user": "x"}'  # pragma: allowlist secret
        self.check(made_up, '{"password": "<redacted>", "user": "x"}')
        self.check('{"password":"hunter2hunter2"}', '{"password":"<redacted>"}')  # pragma: allowlist secret

    def test_a_json_value_with_an_escaped_quote(self) -> None:
        self.check('{"token": "ab\\"cdefgh"}', '{"token": "<redacted>"}')

    def test_a_name_that_ends_in_another_key(self) -> None:
        self.check(f"AWS_SECRET_ACCESS_KEY={FAKE}", "AWS_SECRET_ACCESS_KEY=<redacted>")

    def test_the_aws_credentials_file(self) -> None:
        self.check(f"aws_secret_access_key = {FAKE}", "aws_secret_access_key = <redacted>")

    def test_a_secret_key_holding_a_token(self) -> None:
        self.check(f"STRIPE_SECRET_KEY=sk_live_{FAKE}", "STRIPE_SECRET_KEY=<redacted>")

    def test_a_private_key_name(self) -> None:
        self.check(f"DEPLOY_PRIVATE_KEY={FAKE}", "DEPLOY_PRIVATE_KEY=<redacted>")

    def test_camel_case_and_bare_names(self) -> None:
        self.check(f"apiKey: {FAKE}", "apiKey: <redacted>")
        self.check(f"PASSWORD={FAKE}", "PASSWORD=<redacted>")
        self.check(f"db_passwd={FAKE}", "db_passwd=<redacted>")
        self.check(f"x-access-key: {FAKE}", "x-access-key: <redacted>")

    def test_an_empty_value_does_not_take_the_next_line(self) -> None:
        self.check('GH_TOKEN_FILE=\nREPOS_DIR="$HOME/src"', 'GH_TOKEN_FILE=\nREPOS_DIR="$HOME/src"')

    def test_a_quoted_value_cut_short_ends_with_its_line_or_text(self) -> None:
        self.check(f'export GH_TOKEN="{FAKE}', 'export GH_TOKEN="<redacted>')
        self.check(f'TOKEN="{FAKE}\nnext line', 'TOKEN="<redacted>\nnext line')

    def test_a_bare_value_leaves_the_punctuation_that_closes_it(self) -> None:
        self.check('{"token": 1234567890}', '{"token": <redacted>}')
        self.check("`TOKEN=abc123`", "`TOKEN=<redacted>`")
        self.check("TOKEN=abc123, next", "TOKEN=<redacted>, next")
        self.check("[token=abc123]", "[token=<redacted>]")

    def test_a_bare_value_that_looks_like_a_secret(self) -> None:
        self.check("password: hunter2hunter2", "password: <redacted>")  # pragma: allowlist secret
        passphrase = "correct" + "horse" + "battery" + "staple" + "ok"  # 25 letters, no digit
        self.check(f"password: {passphrase}", "password: <redacted>")
        self.check("Cookie: session=abc123def", "Cookie: <redacted>")

    def test_a_password_in_a_connection_url(self) -> None:
        self.check(
            f"postgres://admin:{FAKE}@localhost:5432/app", "postgres://admin:<redacted>@localhost:5432/app"
        )
        self.check(f"redis://:{FAKE}@cache.internal:6379", "redis://:<redacted>@cache.internal:6379")


class SerializedToolCallsTest(unittest.TestCase):
    """What lib/claude_state.py keeps of a tool call: its input as JSON, cut at 200 characters, so a
    quoted value is escaped and may be cut short."""

    def test_a_write_of_an_env_file(self) -> None:
        summary = claude_state.tool_summary(
            "Write", {"file_path": "/Users/me/src/app/.env", "content": f'export GH_TOKEN="{FAKE}"\nX=1\n'}
        )
        self.assertIn(FAKE, summary)
        self.assertEqual(
            redact(summary),
            '{"content": "export GH_TOKEN=\\"<redacted>\\"\\nX=1\\n", "file_path": "/Users/me/src/app/.env"}',
        )

    def test_an_edit_of_a_json_config(self) -> None:
        config = '{"password": "hunter2hunter2", "user": "x"}'  # pragma: allowlist secret
        summary = claude_state.tool_summary(
            "Edit", {"file_path": "/Users/me/src/app/conf.json", "old_string": "{}", "new_string": config}
        )
        self.assertIn("hunter2hunter2", summary)
        self.assertIn('\\"password\\": \\"<redacted>\\", \\"user\\"', redact(summary))
        self.assertNotIn("hunter2hunter2", redact(summary))

    def test_a_value_the_summary_cut_short(self) -> None:
        content = "x: " + "y" * 150 + f'\npassword: "{FAKE}"'
        summary = claude_state.tool_summary("Write", {"file_path": "/Users/me/a.yml", "content": content})
        self.assertTrue(summary.endswith("..."))
        self.assertTrue(redact(summary).endswith('password: \\"<redacted>'))


class SecretNameTest(unittest.TestCase):
    """A secret word counts when it is a whole part of the name, split at _ - . and camelCase."""

    def test_names_whose_part_is_a_secret_word(self) -> None:
        for name in (
            "GH_TOKEN",
            "access_token",
            "AWS_SECRET_ACCESS_KEY",
            "apiKey",
            "APIKey",
            "accessToken",
            "x-api-key",
            "spring.datasource.password",
            "_authToken",
            "apikey",
            "Cookie",
        ):
            with self.subTest(name=name):
                self.assertTrue(redact_module.secret_name(name))
                self.assertEqual(redact(f"{name}={FAKE}"), f"{name}=<redacted>")

    def test_names_that_only_hold_a_secret_word_inside_a_part(self) -> None:
        for name in ("input_tokens", "max_tokens", "TOKENS", "tokenizer", "secretary", "dbpasswords"):
            with self.subTest(name=name):
                self.assertFalse(redact_module.secret_name(name))
                self.assertEqual(redact(f"{name}={FAKE}"), f"{name}={FAKE}")


class TokenPrefixesTest(unittest.TestCase):
    def test_tokens_without_a_name(self) -> None:
        tokens = {
            "stripe live": "sk" + "_live_" + FAKE,
            "stripe restricted": "rk" + "_test_" + FAKE,
            "stripe webhook": "wh" + "sec_" + FAKE,
            "slack app": "xa" + "pp-1-A0000000000-" + "0" * 13,
            "slack refresh": "xo" + "xe-1-" + FAKE,
            "slack rotated": "xo" + "xe.xoxp-1-" + FAKE,
            "slack bot": "xo" + "xb-0000-" + FAKE,
            "atlassian": "ATA" + "TT3x" + FAKE,
            "gitlab": "gl" + "pat-" + FAKE,
            "npm": "np" + "m_" + FAKE + "0000",
            "google": "AI" + "za" + FAKE + "000",
            "github": "gh" + "p_" + FAKE,
        }
        for kind, token in tokens.items():
            with self.subTest(kind=kind):
                self.assertEqual(redact(f"use {token} now"), "use <token> now")


def marker(edge: str, kind: str = "RSA ") -> str:
    """A PEM marker, built from pieces so that the private-key hook does not take this file for one."""
    return f"-----{edge} {kind}PRIVATE" + " KEY-----"


class PrivateKeysTest(unittest.TestCase):
    BODY = "MIIEowIBAAKCAQEA" + "Ab+/" * 16

    def test_a_whole_key(self) -> None:
        text = f"key: {marker('BEGIN')}\n{self.BODY}\n{marker('END')} done"
        self.assertEqual(redact(text), "key: <key> done")

    def test_a_key_cut_short_on_one_line(self) -> None:
        text = f"here is the deploy key: {marker('BEGIN')} {self.BODY} {self.BODY[:20]}..."
        self.assertEqual(redact(text), "here is the deploy key: <key>...")

    def test_a_key_cut_short_over_lines_stops_at_a_blank_line(self) -> None:
        text = f"key:\n{marker('BEGIN', 'OPENSSH ')}\n{self.BODY}\n{self.BODY[:20]}\n\nthanks"
        self.assertEqual(redact(text), "key:\n<key>\n\nthanks")

    def test_a_key_cut_short_in_json(self) -> None:
        text = f'{{"private_key": "{marker("BEGIN", "")}\\n{self.BODY}\\n{self.BODY[:20]}'
        self.assertEqual(redact(text), '{"private_key": "<key>')

    def test_an_encrypted_key_cut_short_loses_its_headers_and_body(self) -> None:
        headers = "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,00000000\n\n"
        self.assertEqual(redact(f"{marker('BEGIN')}\n{headers}{self.BODY}\n{self.BODY}"), "<key>")

    def test_prose_after_a_marker_with_no_key_stays(self) -> None:
        text = f"the file starts with {marker('BEGIN')} and thanks for the help"
        self.assertEqual(redact(text), "the file starts with <key> and thanks for the help")

    def test_a_pgp_key_block(self) -> None:
        block = "PGP PRIVATE" + " KEY BLOCK-----"
        self.assertEqual(redact(f"-----BEGIN {block}\n{self.BODY}\n-----END {block}"), "<key>")


class ProseTest(unittest.TestCase):
    def test_prose_that_mentions_a_secret_word_is_left_alone(self) -> None:
        for text in (
            "the token budget is large",
            "a secret santa at the office",
            "Tokens: 1200",
            "rotate the password before Friday",
            "the api key lives in the keychain",
            "PWD=/Users/me/src",
            "sha " + "a" * 40,
            "Password: required for staging",
            "access token: rotate weekly",
            "secretary: Danielle",
            "lookup(max_tokens=LOOKUP_TOKENS)",
            '{"token": null}',
            "GITHUB_TOKEN=$(gh auth token)",
            "password: required.",
            "Is a token: needed?",
            "input_tokens=123456 output_tokens=2345",
            "total_tokens: 1234567",
            "max_tokens: 100000",
            "tokenizer=gpt2-tiktoken",
        ):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)


class TimingTest(unittest.TestCase):
    """Every rule takes time in proportion to its text: 100 KB built to make a regex backtrack (long
    runs of name characters, a secret word over and over, many markers or @ signs) stays far under
    the bound, which is generous so that a loaded machine cannot fail it (each took 1-50 ms)."""

    SIZE = 100_000
    BOUND_SECONDS = 0.5

    def adversarial(self) -> dict[str, str]:
        n = self.SIZE
        begin = marker("BEGIN")
        return {
            "a=": "a=" * (n // 2),
            "one long word": "a" * n,
            "base64": "Ab+/" * (n // 4),
            "a marker and a body": f"{begin}\n" + "MIIEowIBAAKCAQEA\n" * (n // 17),
            "a marker and spaced words": f"{begin} " + "MIIE owIB " * (n // 10),
            "many markers": f"{begin} x " * (n // 34),
            "token over and over": "token" * (n // 5),
            "token over and over, then =": "token" * (n // 5) + "=abc123",
            "secret_ over and over": "secret_" * (n // 7),
            "x_api_key_ over and over": "x_api_key_" * (n // 10),
            "access-key- over and over": "access-key-" * (n // 11),
            "password= over and over": "password=" * (n // 9),
            'password=" over and over': 'password="' * (n // 10),
            "an unclosed quote": 'password: "' + "a" * n,
            "backslashes": 'password: "' + "\\" * n,
            "escaped quotes": 'token=\\"' * (n // 9),
            "quotes": '"' * n,
            "a long query": "https://x.example.com/p?" + "a=b&" * (n // 4),
            "many URLs": "https://x.example.com/p?a " * (n // 26),
            "URLs with no space between them": "http://a/?x" * (n // 11),
            "URLs with a user and no space between them": "http://u:" * (n // 9),
            "queries with no space between them": "https://h/p?a=b&" * (n // 16),
            "a long local part": "a" * n + "@",
            "dots before an @": "a." * (n // 2) + "@",
            "many @": "a@" * (n // 2),
            "many connection URLs": "postgres://u:" * (n // 13),
            "bearer": "bearer " * (n // 7),
        }

    def test_every_rule_is_linear(self) -> None:
        for name, text in self.adversarial().items():
            for pattern, replacement in redact_module.REDACTIONS:
                with self.subTest(text=name, rule=pattern.pattern[:60]):
                    start = time.process_time()
                    pattern.sub(replacement, text)
                    self.assertLess(time.process_time() - start, self.BOUND_SECONDS)


if __name__ == "__main__":
    unittest.main()
