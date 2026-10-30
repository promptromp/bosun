"""Unit tests for lib/orphans.py: the helpers a pane's shell leaves behind, and the launch shells'
leftovers `bosun orphans` lists, from fake `ps` listings (nothing here looks at, or signals,
a real process except one sleep it starts itself). Fictional paths: acme's checkout in /Users/me."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import orphans
from orphans import Proc


BOSUN = "/Users/me/Development/bosun/bin/bosun"
WRAPPERS = [f"/bin/zsh -lic {BOSUN} launch ", f"/bin/zsh -lic {BOSUN} checkin --loop "]
LAUNCH = f"/bin/zsh -lic {BOSUN} launch exporter ; exec /bin/zsh -l"
CHECKIN = f"/bin/zsh -lic {BOSUN} checkin --loop ; exec /bin/zsh -l"
FALLBACK = "/bin/zsh -l"
TMUX_SERVER = f"tmux -L bosun new-session -d -s acme -- /bin/zsh -lic {BOSUN} launch orchestrator"

# The tmux server (its command line holds the launch text), two live panes (a launch shell running
# Claude, a fallback shell), their helpers on their terminals (two still under the subshell that
# forks them off), a dead pane's leftovers (no terminal
# any more), one that has a child, another checkout's, and a terminal you work in with its helper.
PS = f"""\
  900     1 ??       {TMUX_SERVER}
 1001   900 ttys001  {LAUNCH}
 1002  1001 ttys001  claude --model opus --name exporter
 1003     1 ttys001  {LAUNCH}
 1004     1 ttys001  {LAUNCH}
 1005     1 ttys002  {LAUNCH}
 1006  1001 ttys001  {LAUNCH}
 1007  1006 ttys001  {LAUNCH}
 1101   900 ttys003  {FALLBACK}
 1102     1 ttys003  {FALLBACK}
 1201     1 ??       {LAUNCH}
 1202     1 ??       {CHECKIN}
 1203     1 ??       {LAUNCH}
 1204  1203 ??       sleep 5
 1205     1 ??       /bin/zsh -lic /Users/me/other-checkout/bin/bosun launch exporter ; exec /bin/zsh -l
 1301   800 ttys009  /bin/zsh -l
 1302     1 ttys009  /bin/zsh -l
bad line
"""


class ParseTest(unittest.TestCase):
    def test_rows_with_their_command_lines(self) -> None:
        procs = orphans.parse_ps(PS)
        self.assertEqual(len(procs), 17)
        self.assertEqual(procs[0], Proc(900, 1, "??", TMUX_SERVER))
        self.assertEqual(procs[2].command, "claude --model opus --name exporter")


class HelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.procs = orphans.parse_ps(PS)

    def pids(self, found: list[Proc]) -> list[int]:
        return [p.pid for p in found]

    def test_a_launch_shells_helpers_are_its_forks_on_its_terminal(self) -> None:
        self.assertEqual(self.pids(orphans.helpers(self.procs, 1001, WRAPPERS)), [1003, 1004, 1006, 1007])

    def test_a_fallback_shell_that_goes_has_its_own(self) -> None:
        self.assertEqual(self.pids(orphans.helpers(self.procs, 1101)), [1102])

    def test_a_shell_you_work_in_keeps_its_helpers_when_only_wrappers_count(self) -> None:
        self.assertEqual(orphans.helpers(self.procs, 1301, WRAPPERS), [])

    def test_unknown_pids_and_shells_without_a_terminal_have_none(self) -> None:
        self.assertEqual(orphans.helpers(self.procs, 4242), [])
        self.assertEqual(orphans.helpers(self.procs, 1201), [])


class LaunchShellsTest(unittest.TestCase):
    def test_only_childless_launch_wrapper_forks_adopted_by_launchd_never_tmux(self) -> None:
        found = orphans.launch_shells(orphans.parse_ps(PS), WRAPPERS)
        self.assertEqual([p.pid for p in found], [1003, 1004, 1005, 1201, 1202])

    def test_nothing_without_a_prefix(self) -> None:
        self.assertEqual(orphans.launch_shells(orphans.parse_ps(PS), []), [])

    def test_a_tmux_server_adopted_by_launchd_is_never_one(self) -> None:
        self.assertFalse(orphans.is_tmux(LAUNCH))
        self.assertTrue(orphans.is_tmux(TMUX_SERVER))
        self.assertTrue(orphans.is_tmux("/opt/homebrew/bin/tmux -L bosun"))


def start_sleeper(case: unittest.TestCase) -> subprocess.Popen[bytes]:
    sleeper = subprocess.Popen(["sleep", "30"])

    def end() -> None:
        sleeper.kill()
        sleeper.wait()

    case.addCleanup(end)
    # Popen returns before the child has exec'd sleep, and until then ps shows the parent's command.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        now = orphans.parse_ps(orphans.read_ps("-p", str(sleeper.pid)))
        if now and now[0].command == "sleep 30":
            break
        time.sleep(0.01)
    return sleeper


class StopTest(unittest.TestCase):
    def test_only_a_pid_that_still_runs_the_same_process_is_signalled(self) -> None:
        sleeper = start_sleeper(self)
        mine = Proc(sleeper.pid, 1, "??", "sleep 30")
        gone = Proc(sleeper.pid + 100000, 1, "??", "sleep 30")
        changed = Proc(4243, 1, "??", "zsh")
        stopped = orphans.stop([mine, gone, changed], check=lambda p: p is not changed)
        self.assertEqual(stopped, [mine])
        self.assertEqual(sleeper.wait(timeout=5), -15)

    def test_unchanged_rechecks_the_live_process(self) -> None:
        sleeper = start_sleeper(self)
        live = orphans.parse_ps(orphans.read_ps("-p", str(sleeper.pid)))[0]
        self.assertTrue(orphans.unchanged(live))
        self.assertFalse(orphans.unchanged(Proc(live.pid, live.ppid, live.tty, "something else")))


class MainTest(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str]:
        with tempfile.NamedTemporaryFile("w", suffix=".ps") as listing:
            listing.write(PS)
            listing.flush()
            out = io.StringIO()
            with redirect_stdout(out):
                code = orphans.main([*args, "--ps", listing.name])
        return code, out.getvalue()

    def test_lists_pid_and_command(self) -> None:
        code, out = self.run_main("launch-shells", *[f"--wrapper={w}" for w in WRAPPERS])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], f"1003\t{LAUNCH}")
        self.assertEqual(len(out.splitlines()), 5)

    def test_helpers_of_a_pid(self) -> None:
        self.assertEqual(self.run_main("helpers", "1101"), (0, f"1102\t{FALLBACK}\n"))

    def test_launch_shells_needs_a_prefix(self) -> None:
        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(orphans.main(["launch-shells"]), orphans.USAGE)


if __name__ == "__main__":
    unittest.main()
