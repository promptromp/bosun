"""The smoke suite makes no timing assumptions: a check never asserts how long something took and never
races a fixed sleep. It synchronises on what the code says or writes (a log line, a file, a lock's state, an
exit), and a `wait_for` timeout is only a failsafe against a hang. Timing logic itself (polls, deadlines,
intervals) is the unit tests', with an injected clock.

This test holds tests/smoke.sh to the common shapes of a timing assumption, line by line (a subshell opened
alone on its line is read with the line after it). It cannot prove a check free of timing: a race can take a
shape no pattern here knows. What it finds:

- elapsed time measured: `$((SECONDS - start))`, `$(date +%s) - $start`, `time.time() -`, `monotonic() -`;
- a holder that lets go after a fixed time: `run_locked.py <lock> <wait> <msg> -- sleep <n>`, a Python
  `time.sleep(<n>)` of a second or more (a lock holder, as it always was here); tests/smoke.sh has hold_lock
  and release_lock, which the test lets go;
- a fixed sleep followed by what it times, on one line: `sleep <n>; kill ...`, `( sleep <n>; act ) &`,
  `{ sleep <n>; act; } &`, `bash -c 'sleep <n>; act'` (a poll's `sleep <n>; done` is none of these);
- a sleep of a variable's time (`sleep "$wait"`), and a short sleep typed into a pane as the command it waits
  on (a fake Claude is `sleep 600`, which stands for a process, not for a time).

What the harness itself needs, and the few places that are justified, are in ALLOWED, each with why.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "tests" / "smoke.sh"

# A sleep shorter than a minute (a longer one stands for a process: a fake Claude, a helper), never the path
# /bin/sleep.
SLEEP = r"(?<![/\w])sleep\s+"
SHORT = r"[0-5]?[0-9](?:\.[0-9]+)?(?![0-9.])"
SHAPES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "elapsed",
        re.compile(
            r"\bSECONDS\s*-"
            r"|\$\(date \+%s\)\s*-\s*\$?\{?(?:start|started|begin|began|before|then|t0|since)\b"
            r"|\btime\.(?:time|monotonic|perf_counter)\(\)\s*-"
        ),
        "elapsed time measured: wait on what the code says or writes, not on how long it took",
    ),
    (
        "timed holder",
        re.compile(r"run_locked\.py\b.*\s--\s+sleep\s+[0-9]|\btime\.sleep\(\s*[1-9]"),
        "a holder that lets go after a fixed time: hold_lock/release_lock",
    ),
    (
        "timed actor",
        re.compile(
            rf"{SLEEP}{SHORT}\s*(?:;(?!;)|&&)\s*(?!done\b)[^\s;&)}}]"
            rf"|\(\s*sleep\s+{SHORT}\s*(?:;|&&|$)"
            rf"|\{{\s*sleep\s+{SHORT}"
            rf"|\bbash -c ['\"]\s*sleep\s+{SHORT}"
        ),
        "a fixed sleep timing what follows it: trigger it on the code's own signal (wait_for ...)",
    ),
    (
        "variable sleep",
        re.compile(rf"{SLEEP}\"?\\?\$\{{?[A-Za-z_]"),
        "a sleep of a variable's time, which a test sets: wait on what it stands for instead",
    ),
)
TYPED_SLEEP = re.compile(r"send-keys\b.*[\"']sleep\s+([0-9.]+)")
SHORTEST_PROCESS = 60  # a `sleep` typed into a pane this long or longer stands for a process, not for a time

# (shape, text the line holds, why). Each is the harness's own, or a place where the sleep can only make a
# check weaker on a slow machine, never fail it falsely, and no signal of the code's own is there to wait on.
ALLOWED: tuple[tuple[str, str, str], ...] = (
    (
        "elapsed",
        "$((SECONDS - SECTION_STARTED))",
        "section_done records how long a section took, for the shard weights: a measurement, never a check",
    ),
    (
        "elapsed",
        "round(time.monotonic() - t, 1)",
        "status-json prints how long status --json took as a note line, which no check reads",
    ),
    (
        "timed holder",
        "time.sleep(2)",
        (
            "uninstall's late hook outlasts uninstall's first look: uninstall says nothing while it waits, "
            "so on a slow machine the order check can only pass without proving the wait, never fail"
        ),
    ),
    (
        "timed actor",
        "run_with_timeout 1 sleep 30; [ $? -eq 143 ]",
        "the command a timeout stops, not a timer: had it run to its end, it would exit 0",
    ),
    (
        "timed actor",
        "sleep 1; exit 0",
        "the same late hook's agent, which ends a second after its window stops, as Claude Code's does",
    ),
)
# A line that is only `sleep <n>` and the line after it a kill: timed the same way, across two lines.
SLEEP_ALONE = re.compile(r"^\s*sleep\s+[0-9.]+\s*$")
KILL_NEXT = re.compile(r"^\s*kill\b")
ALLOWED_SLEEP_THEN_KILL = (
    # doctor cut short, then a second TERM a moment later "while it stops them": if the TERM comes once doctor
    # is gone, kill fails and the check passes without proving the second TERM is ignored: no false failure.
    'kill -TERM "$hung_doctor"',
)


def allowed(shape: str, line: str) -> bool:
    return any(shape == name and held in line for name, held, _ in ALLOWED)


def shape_findings(number: int, line: str, following: str) -> list[str]:
    """What line <number> holds of SHAPES, a subshell opened alone on it read with the line after it."""
    code = f"( {following.strip()}" if line.strip() == "(" else line
    return [
        f"{number}: {code.strip()} -- {instead}"
        for shape, pattern, instead in SHAPES
        if pattern.search(code) and not allowed(shape, line)
    ]


def pane_and_kill_findings(number: int, line: str, following: str) -> list[str]:
    """A short sleep typed into a pane on line <number>, or a `sleep <n>` alone on it before a kill."""
    found = []
    typed = TYPED_SLEEP.search(line)
    if typed and float(typed.group(1)) < SHORTEST_PROCESS:
        found.append(f"{number}: {line.strip()} -- a short sleep typed into a pane: let the test end it")
    timed_kill = SLEEP_ALONE.match(line) and KILL_NEXT.match(following)
    if timed_kill and not any(held in following for held in ALLOWED_SLEEP_THEN_KILL):
        found.append(f"{number}: {line.strip()} / {following.strip()} -- a fixed sleep timing a kill")
    return found


def timing_assumptions(text: str) -> list[str]:
    """Each line of <text> in one of the shapes that ALLOWED does not explain, with what to do instead."""
    lines = text.splitlines()
    found = []
    for number, line in enumerate(lines, start=1):
        if not line.lstrip().startswith("#"):
            following = lines[number] if number < len(lines) else ""
            found += shape_findings(number, line, following) + pane_and_kill_findings(number, line, following)
    return found


class TimingTest(unittest.TestCase):
    def test_the_suite_makes_none_of_these_timing_assumptions(self) -> None:
        self.assertEqual(timing_assumptions(SUITE.read_text()), [])

    def test_each_shape_is_found(self) -> None:
        """Each shape this PR took out of the suite, as it was, and the variants the review named."""
        for text in (
            "check \"fast\" bash -c '[ $((SECONDS - start)) -lt 5 ]'",
            "took=$((SECONDS-started))",
            "waited=$(( $(date +%s) - start ))",
            'python3 -c "import sys, time; sys.exit(time.time() - float(sys.argv[1]) > 1.0)" "$start"',
            "t = time.monotonic(); r = run(); print(time.monotonic() - t)",
            'python3 "$ROOT/lib/run_locked.py" "$STATE/windows.lock" 10 busy -- sleep 8 &',
            'python3 -c "import fcntl; fcntl.flock(f, fcntl.LOCK_EX); time.sleep(60)" &',
            'time.sleep(60)\' "$STATE/update.lock" >held.out &',
            "(sleep 1; agent_status idle) &",
            "( sleep 2 && touch done ) &",
            "(\n  sleep 1\n  agent_status idle\n) &",
            "{ sleep 1; act; } &",
            "bash -c 'sleep 1; act' &",
            "  sleep 2; kill $pid 2>/dev/null; pkill -P $pid 2>/dev/null; wait $pid 2>/dev/null",
            '  sleep 0.5; "$1" ledger said | grep -q "control typed by me"',
            '    sleep "\\${FAKE_SSO_WAIT:-0}"',
            'tmux -L "$SOCKET" send-keys -t "=smoke:gamma" "sleep 2" Enter',
            'sleep 0.1\nkill -TERM "$pid"',
        ):
            with self.subTest(text=text):
                self.assertEqual(len(timing_assumptions(text)), 1, text)

    def test_what_is_no_timing_assumption_is_not_found(self) -> None:
        for text in (
            'cache_update "$FILE" "$SECTION" "$SECTION $((SECONDS - SECTION_STARTED))"',
            "  local deadline=$((SECONDS + $1))",
            "  printf '{\"statusUpdatedAt\":%d}' $((($(date +%s) - $4) * 1000))",
            '  wait_for 30 grep -q "Waiting for 1 busy window" "$WORK/quit.err"',
            'hold_lock "$BOSUN_STATE_DIR/windows.lock" quit',
            "    sleep 0.2",
            '  for _ in $(seq 1 3000); do [ -e "$1" ] || exit 0; sleep 0.1; done',
            '  auth) echo $$ >>"$WORK/hang-claude.pids"; exec sleep 60 ;;',
            'tmux -L "$SOCKET" send-keys -t "$window_id" "sleep 600" Enter',
            "# a comment may show (sleep 1; act) & or $((SECONDS - x)) as what not to do",
        ):
            with self.subTest(text=text):
                self.assertEqual(timing_assumptions(text), [], text)


if __name__ == "__main__":
    unittest.main()
