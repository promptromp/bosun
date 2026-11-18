"""The smoke suite makes no timing assumptions: a check never asserts how long something took and never
races a fixed sleep. It synchronises on what the code says or writes (a log line, a file, a lock's state, an
exit), and a `wait_for` timeout is only a failsafe against a hang. Timing logic itself (polls, deadlines,
intervals) is the unit tests', with an injected clock. This holds the suite to three shapes that broke it on
CI's slower runners:

- elapsed-time arithmetic, `$((SECONDS - start))`, which a check then compares;
- a lock holder that lets go after a fixed time, `run_locked.py <lock> <wait> <message> -- sleep <n>`, which a
  slow run outlives (tests/smoke.sh has hold_lock and release_lock: the test lets go);
- a background actor timed by a sleep, `( sleep <n>; act ) &`, which a slow run reaches too early or too
  late (trigger it on what the code says instead: `wait_for 30 grep -q "Waiting for ..." out`).

What the harness itself needs is in ALLOWED, each with why.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "tests" / "smoke.sh"

ELAPSED = re.compile(r"\bSECONDS\s*-")
TIMED_HOLDER = re.compile(r"run_locked\.py\b.*\s--\s+sleep\s+[0-9]")
TIMED_ACTOR = re.compile(r"\(\s*sleep\s+[0-9.]+\s*(?:;|&&|$)")
SHAPES = (
    (ELAPSED, "elapsed-time arithmetic: synchronise on what the code says or writes, not on the time taken"),
    (TIMED_HOLDER, "a lock holder that lets go after a fixed time: hold_lock/release_lock"),
    (TIMED_ACTOR, "a background actor timed by a sleep: trigger it on the code's own signal (wait_for ...)"),
)
# The harness's own lines, each matched by the pattern and the text the line holds.
ALLOWED = (
    # section_done records how long a section took (tests/.smoke-cache/durations, what --shard balances by):
    # a measurement for the weights, never a check.
    (ELAPSED, "$((SECONDS - SECTION_STARTED))"),
)


def timing_assumptions(text: str) -> list[str]:
    """Each line of <text> in one of SHAPES that ALLOWED does not explain, with what to do instead. A subshell
    opened alone on its line counts with the line after it, so `(` then `sleep 1` is found as `( sleep 1`."""
    found = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("#"):
            continue
        code = f"( {lines[number].strip()}" if line.strip() == "(" and number < len(lines) else line
        for shape, instead in SHAPES:
            allowed = any(shape is pattern and held in line for pattern, held in ALLOWED)
            if shape.search(code) and not allowed:
                found.append(f"{number}: {code.strip()} -- {instead}")
    return found


class TimingTest(unittest.TestCase):
    def test_the_suite_makes_no_timing_assumptions(self) -> None:
        self.assertEqual(timing_assumptions(SUITE.read_text()), [])

    def test_each_shape_is_found_and_what_is_allowed_is_not(self) -> None:
        for line in (
            "check \"fast\" bash -c '[ $((SECONDS - start)) -lt 5 ]'",
            "took=$((SECONDS-started))",
            'python3 "$ROOT/lib/run_locked.py" "$STATE/windows.lock" 10 busy -- sleep 8 &',
            "(sleep 1; agent_status idle) &",
            "( sleep 2 && touch done ) &",
            "(\n  sleep 1\n  agent_status idle\n) &",
        ):
            with self.subTest(line=line):
                self.assertEqual(len(timing_assumptions(line)), 1, line)
        for line in (
            'cache_update "$FILE" "$SECTION" "$SECTION $((SECONDS - SECTION_STARTED))"',
            "  local deadline=$((SECONDS + $1))",
            '  wait_for 30 grep -q "Waiting for 1 busy window" "$WORK/quit.err"',
            'hold_lock "$BOSUN_STATE_DIR/windows.lock" quit',
            "    sleep 0.2",
            "# a comment may say (sleep 1; act) & or $((SECONDS - x)) as an example of what not to do",
        ):
            with self.subTest(line=line):
                self.assertEqual(timing_assumptions(line), [], line)


if __name__ == "__main__":
    unittest.main()
