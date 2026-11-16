"""Unit tests for lib/redact.py: every way a secret is commonly written down is redacted, and
ordinary prose that only mentions a secret word is left alone. All values are made up, and built
from pieces so that no scanner reads them as real."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
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
        text = f'{{"private_key": "{marker("BEGIN", "")}\\n{self.BODY}\\n{self.BODY[:10]}'
        self.assertEqual(redact(text), '{"private_key": "<key>')


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
        ):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
