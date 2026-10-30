"""Unit tests for the console's Learning and Library views: lib/console_learn.py (runs, `learn:`
items, findings remembered), lib/console_library.py (masking, front matter, effective task types,
the models) and lib/console_files.py (which files may be shown: the roots, the exclusions, symlink
escapes, the report allowlist). Fictional data: acme's invoice exporter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import console_files
import console_learn
import console_library
import console_state
import delegate
import learn
from console_library import MASK, WEBHOOK_MASK


REPO = Path(__file__).resolve().parents[2]
# Fake values, made up here so no line of this file looks like a secret to the scanners.
# shaped like a GitHub token, which redact.py removes
SECRET_VALUE = "ghp_" + "0" * 36  # pragma: allowlist secret
FAKE_KEY = "abc" + "123def"
FAKE_PASSWORD = "hunter2" * 2  # pragma: allowlist secret
ENV_TEXT = f"""# acme site settings
BOSUN_SESSION_NAME="acme"
BOSUN_SLACK_BOT_TOKEN="xoxb-not-a-real-one"
export ACME_API_KEY={FAKE_KEY}
BOSUN_DB_PASSWORD='{FAKE_PASSWORD}'
BOSUN_ALERT_WEBHOOK=https://hooks.example.com/services/T000/B000/XXXX
BOSUN_NOTES_URL=https://example.com/acme/notes
BOSUN_GH_TOKEN_FILE=
BOSUN_REPOS_DIR="$HOME/Development"
OTHER={SECRET_VALUE}
"""


class MaskingTestCase(unittest.TestCase):
    def test_secret_named_keys_and_webhooks_are_masked(self) -> None:
        shown = console_library.mask_config(ENV_TEXT)
        for secret in (
            "xoxb-not-a-real-one",
            FAKE_KEY,
            FAKE_PASSWORD,
            "hooks.example.com",
            SECRET_VALUE,
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, shown)
        lines = shown.splitlines()
        self.assertIn(f'BOSUN_SLACK_BOT_TOKEN="{MASK}"', lines)
        self.assertIn(f"export ACME_API_KEY={MASK}", lines)
        self.assertIn(f"BOSUN_DB_PASSWORD='{MASK}'", lines)
        self.assertIn(f"BOSUN_ALERT_WEBHOOK={WEBHOOK_MASK}", lines)
        # what is no secret stays readable: other values, an empty secret, comments
        for kept in (
            'BOSUN_SESSION_NAME="acme"',
            "BOSUN_NOTES_URL=https://example.com/acme/notes",
            "BOSUN_GH_TOKEN_FILE=",
            'BOSUN_REPOS_DIR="$HOME/Development"',
            "# acme site settings",
        ):
            self.assertIn(kept, lines)

    def test_json_and_yaml_keys(self) -> None:
        settings = {"env": {"ACME_TOKEN": FAKE_KEY, "MODE": "fast"}, "apiKey": FAKE_PASSWORD}
        text = json.dumps(settings, indent=2)
        shown = console_library.mask_config(text)
        self.assertIn(f'"ACME_TOKEN": "{MASK}",', shown)
        self.assertIn(f'"apiKey": "{MASK}"', shown)
        self.assertIn('"MODE": "fast"', shown)
        yaml = "sessions:\n  - name: exporter\n    secret_key: s3cr3t-value\n    dir: ~/repos/exporter\n"
        shown = console_library.mask_config(yaml)
        self.assertIn(f"    secret_key: {MASK}", shown)
        self.assertIn("  - name: exporter", shown)
        self.assertIn("keys:", console_library.mask_config("keys:\n  - a\n"))  # a block, not a value

    def test_what_gets_masked_where(self) -> None:
        self.assertEqual(console_library.shown_text(ENV_TEXT, "env", "site")[1], True)
        policy = "Ask dana@example.com first.\n"
        self.assertEqual(
            console_library.shown_text(policy, "markdown", "site"), ("Ask <email> first.\n", True)
        )
        self.assertEqual(console_library.shown_text(policy, "markdown", "repo"), (policy, False))


class FrontMatterTestCase(unittest.TestCase):
    def test_front_matter(self) -> None:
        fields, body = console_library.front_matter(
            "---\ntitle: Review\nmay: comment: on PRs\n  nested: no\n---\n\n# Body\n"
        )
        self.assertEqual(fields, [["title", "Review"], ["may", "comment: on PRs"]])
        self.assertEqual(body, "# Body\n")

    def test_no_or_unclosed_front_matter(self) -> None:
        for text in ("# Just a body\n", "---\ntitle: never closed\n", "", "text\n---\nkey: value\n---\n"):
            with self.subTest(text=text):
                self.assertEqual(console_library.front_matter(text), ([], text))

    def test_every_repo_task_file_parses(self) -> None:
        for path in sorted((REPO / "orchestrator" / "tasks").glob("*.md")):
            with self.subTest(path=path.name):
                fields, body = console_library.front_matter(path.read_text())
                if not path.name.startswith("_"):
                    self.assertEqual(dict(fields), delegate.split_front_matter(path)[0])
                self.assertTrue(body.strip())


def write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class CatalogTestCase(unittest.TestCase):
    """A fictional repo and config dir in a temp folder."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.repo, self.config, self.outside = base / "repo", base / "config", base / "outside"
        orchestrator = self.repo / "orchestrator"
        for name in ("policy.md", "plugin/commands/chores.md", "learn.md", "slack-watch.md", "settings.json"):
            write(orchestrator / name)
        for name in ("_common.md", "review.md", "new-work.md", "review.md.bak", "old.bak.md"):
            write(orchestrator / "tasks" / name, "---\ntitle: A task\n---\nbody\n")
        for name in ("policy.md", "bosun.env", "sessions.yml", "forbidden-terms.txt", "policy.md.bak-1"):
            write(self.config / name)
        for name in ("review.md", "_common.md", "triage.md"):
            write(self.config / "tasks" / name, "---\ntitle: Site task\n---\nbody\n")
        write(self.outside / "escape.md", "outside")
        os.symlink(self.outside / "escape.md", self.config / "tasks" / "escape.md")
        self.memory = base / "projects" / "exporter" / "memory"
        write(self.memory / "MEMORY.md")
        write(self.memory / "notes" / "deploys.md")
        write(self.memory / "notes.txt")
        os.symlink(self.outside / "escape.md", self.memory / "linked.md")
        os.symlink(self.outside, self.memory / "linked-folder")

    def settings(self, **extra: Any) -> console_files.Settings:
        defaults = {
            "config_dir": self.config,
            "sessions_file": self.config / "sessions.yml",
            "repo": self.repo,
        }
        return console_files.Settings(**{**defaults, **extra})

    def names(self, settings: console_files.Settings) -> dict[str, str]:
        return {f"{e.root}:{e.name}": e.badge for e in console_files.catalog(settings)}

    def test_the_roots_and_what_is_never_listed(self) -> None:
        with mock.patch.object(console_files, "ROOT", self.repo):
            names = self.names(self.settings())
        self.assertEqual(
            sorted(names),
            sorted(
                [
                    "repo:orchestrator/policy.md",
                    "repo:orchestrator/plugin/commands/chores.md",
                    "repo:orchestrator/learn.md",
                    "repo:orchestrator/slack-watch.md",
                    "repo:orchestrator/tasks/_common.md",
                    "repo:orchestrator/tasks/new-work.md",
                    "repo:orchestrator/tasks/review.md",
                    "repo:orchestrator/settings.json",
                    "site:policy.md",
                    "site:bosun.env",
                    "site:sessions.yml",
                    "site:tasks/_common.md",
                    "site:tasks/review.md",
                    "site:tasks/triage.md",
                ]
            ),
        )

    def test_effective_badges_follow_delegate(self) -> None:
        names = self.names(self.settings())
        self.assertEqual(names["repo:orchestrator/tasks/review.md"], "replaced by site")
        self.assertEqual(names["repo:orchestrator/tasks/new-work.md"], "effective")
        self.assertEqual(names["site:tasks/review.md"], "effective")
        self.assertEqual(names["site:tasks/triage.md"], "effective")
        self.assertEqual(names["repo:orchestrator/tasks/_common.md"], "added to every task")
        self.assertEqual(names["site:tasks/_common.md"], "added to every task")
        files = delegate.task_files((self.repo / "orchestrator" / "tasks", self.config / "tasks"))
        self.assertEqual(files["review"], self.config / "tasks" / "review.md")
        self.assertNotIn("_common", files)

    def test_site_settings_replace_the_repo_settings(self) -> None:
        site_settings = write(
            self.config / "orchestrator-settings.json", '{"env": {"ACME_TOKEN": "abc-123"}}'
        )
        names = self.names(self.settings(orchestrator_settings=site_settings))
        self.assertNotIn("repo:orchestrator/settings.json", names)
        self.assertEqual(names["site:orchestrator-settings.json"], "in use")

    def test_memory_only_when_listed_and_no_escapes(self) -> None:
        self.assertFalse(any(n.startswith("memory:") for n in self.names(self.settings())))
        names = self.names(self.settings(memory_dirs=(self.memory,)))
        self.assertEqual(
            sorted(n for n in names if n.startswith("memory:")),
            ["memory:MEMORY.md", "memory:notes/deploys.md"],
        )

    def test_settings_from_env(self) -> None:
        env = {
            "BOSUN_CONFIG_DIR": str(self.config),
            "BOSUN_CONSOLE_MEMORY_DIRS": f"{self.memory}:{self.outside}:",
        }
        settings = console_files.Settings.from_env(env)
        self.assertEqual(settings.memory_dirs, (self.memory, self.outside))
        self.assertIsNone(settings.sessions_file)
        self.assertEqual(console_files.Settings.from_env({}).memory_dirs, ())

    def test_keys_are_opaque_and_stable(self) -> None:
        first = console_files.catalog(self.settings())
        second = console_files.catalog(self.settings())
        self.assertEqual([e.key for e in first], [e.key for e in second])
        for entry in first:
            self.assertTrue(console_library.valid_key(entry.key))
            self.assertNotIn(entry.path.name, entry.key)
        made_up = "0123456789abcdef"  # gitleaks:allow (a made-up key)
        for bad in ("../etc/passwd", made_up.upper(), "0123", made_up + "0"):
            self.assertFalse(console_library.valid_key(bad))

    def test_the_file_model_masks_config(self) -> None:
        write(self.config / "bosun.env", ENV_TEXT)
        env = next(e for e in console_files.catalog(self.settings()) if e.name == "bosun.env")
        text, error = console_files.read(env.path)
        model = console_library.file_model(env, text, error)
        self.assertTrue(model["redacted"])
        self.assertNotIn("hunter2hunter2", json.dumps(model))
        self.assertNotIn("path", model)

    def test_a_large_file_is_listed_but_not_read(self) -> None:
        big = write(self.config / "policy.md", "x" * (console_files.MAX_READ_BYTES + 1))
        self.assertEqual(console_files.read(big), (None, console_files.TOO_LARGE))
        self.assertEqual(console_files.read(self.config / "missing.md"), (None, console_files.UNREADABLE))

    def test_the_library_model(self) -> None:
        entries = console_files.catalog(self.settings())
        texts = {e.key: console_files.read(e.path)[0] for e in entries}
        model = console_library.library_model(entries, texts, memory_on=False)
        groups = [g["name"] for g in model["groups"]]
        self.assertEqual(groups[:3], ["Orchestrator", "Prompts", "Task types"])
        review = next(e for g in model["groups"] for e in g["entries"] if e["name"] == "tasks/review.md")
        self.assertEqual(
            (review["title"], review["badge"], review["kind"]), ("Site task", "effective", "markdown")
        )


def learn_item(
    item_id: str, state: str, history: list[dict[str, Any]], proposal: str | None = None
) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": "learning",
        "state": state,
        "title": "Retry a failed check-in paste",
        "created_at": history[0]["at"],
        "updated_at": history[-1]["at"],
        "fields": {
            "key": "checkin-paste-retry",
            "category": "mishap",
            "target": "policy",
            "run": "2026-10-19T18:30:00+00:00",
        },
        "proposal": {"action": proposal, "proposed_at": history[0]["at"]} if proposal else None,
        "history": history,
    }


ACTION = (
    "[learn: policy] Retry a failed check-in paste\nChange: a rule appended to the site policy:\n"
    "Retry the paste once before reporting it failed.\nWhy: two check-ins were lost\n"
    'Evidence: log checkin-2026-10-19.log 10:00: "paste failed"\n'
    "If yes: bosun learn apply 'learn:2026-10-19/1'"
)


class LearnTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name)
        self.learn_dir = self.state_dir / "learn"
        self.run_dir = self.learn_dir / "runs" / "20261019-183000"
        write(self.run_dir / "report.md", "# Learning report\n")
        write(self.learn_dir / "dry-runs" / "20261020-090000" / "report.md", "# Dry\n\n- **One** (low)\n")
        state = {
            "runs": [
                {
                    "started": "2026-10-19T18:30:00+00:00",
                    "outcome": "ok",
                    "findings": 2,
                    "cost": 0.8,
                    "proposals": ["learn:2026-10-19/1"],
                    "run_dir": str(self.run_dir),
                },
                {"started": "2026-10-18T18:30:00+00:00", "outcome": "failed", "error": "timeout"},
                {
                    "started": "2026-10-17T18:30:00+00:00",
                    "outcome": "ok",
                    "run_dir": str(self.state_dir / "elsewhere"),
                },
            ],
            "findings": {
                "checkin-paste-retry": {
                    "title": "Retry",
                    "category": "mishap",
                    "first_seen": "2026-10-17T18:30:00+00:00",
                    "last_seen": "2026-10-19T18:30:00+00:00",
                    "times_seen": 3,
                    "status": "applied",
                },
                "slow-review": {
                    "title": "Slow review",
                    "category": "stall",
                    "first_seen": "2026-10-19T18:30:00+00:00",
                    "last_seen": "2026-10-19T18:30:00+00:00",
                    "status": "reported",
                },
            },
            "proposals": {
                "learn:2026-10-19/1": {
                    "key": "checkin-paste-retry",
                    "target": "policy",
                    "text": "Retry the paste once before reporting it failed.",
                }
            },
        }
        write(self.learn_dir / "state.json", json.dumps(state))

    def model(self, ledger: dict[str, Any]) -> dict[str, Any]:
        files = console_state.read_learn(self.state_dir)
        reports = console_files.learn_reports(files)
        return console_learn.learn_model(files, ledger, console_files.report_keys(reports))

    def test_runs_and_their_reports(self) -> None:
        files = console_state.read_learn(self.state_dir)
        reports = console_files.learn_reports(files)
        self.assertEqual(
            sorted(p.parent.name for p in reports.values()), ["20261019-183000", "20261020-090000"]
        )
        runs = self.model({"items": {}})["runs"]
        self.assertEqual([r["dry_run"] for r in runs], [True, False, False, False])
        self.assertEqual(runs[0]["findings"], 1)
        self.assertIsNotNone(runs[1]["report"])
        self.assertEqual(runs[2]["report_note"], "no report")  # failed
        self.assertEqual(runs[3]["report_note"], console_learn.OUTSIDE)
        self.assertTrue(all(console_library.valid_key(r["report"]) for r in runs if r["report"]))

    def test_a_pending_and_an_applied_item(self) -> None:
        created = {"at": "2026-10-19T18:31:00+00:00", "event": "created", "state": "proposed"}
        proposed = {"at": "2026-10-19T18:31:00+00:00", "event": "proposed", "note": ACTION}
        applied = {
            "at": "2026-10-20T09:00:00+00:00",
            "event": "transition",
            "from": "proposed",
            "to": "applied",
            "note": "applied: appended to /Users/me/.config/bosun/policy.md",
        }
        ledger = {
            "items": {
                "learn:2026-10-19/1": learn_item(
                    "learn:2026-10-19/1", "applied", [created, proposed, applied]
                ),
                "learn:2026-10-20/1": learn_item(
                    "learn:2026-10-20/1", "proposed", [{**created, "at": "2026-10-20T18:31:00+00:00"}], ACTION
                ),
                "task:2026-10-20/1": {"id": "task:2026-10-20/1", "kind": "task"},
            }
        }
        model = self.model(ledger)
        newest, older = model["items"]
        self.assertEqual(newest["id"], "learn:2026-10-20/1")
        self.assertTrue(newest["pending"])
        self.assertEqual(newest["evidence"], 'log checkin-2026-10-19.log 10:00: "paste failed"')
        self.assertEqual(newest["why"], "two check-ins were lost")
        self.assertIn("Retry the paste once", newest["change_text"])
        self.assertEqual(older["settled"]["state"], "applied")
        self.assertIn("policy.md", older["settled"]["note"])
        self.assertEqual(older["change"]["target"], "policy")
        self.assertEqual(older["evidence"], newest["evidence"])  # from the history once settled

    def test_findings_remembered(self) -> None:
        model = self.model({"items": {}})
        self.assertEqual([f["key"] for f in model["findings"]], ["checkin-paste-retry", "slow-review"])
        self.assertEqual(model["findings"][1]["times_seen"], 1)
        self.assertEqual(model["statuses"], {"applied": 1, "reported": 1})

    def test_no_learn_state(self) -> None:
        files = console_state.read_learn(self.state_dir / "nowhere")
        model = console_learn.learn_model(files, {}, {})
        self.assertEqual(
            (model["runs"], model["items"], model["findings"], model["error"]), ([], [], [], None)
        )

    def test_an_unreadable_state_still_lists_dry_runs(self) -> None:
        write(self.learn_dir / "state.json", "{broken")
        model = self.model({"items": {}})
        self.assertIn("unreadable", model["error"])
        self.assertEqual([r["dry_run"] for r in model["runs"]], [True])
        self.assertIsInstance(learn.State(), learn.State)


if __name__ == "__main__":
    unittest.main()
