"""The awake window: scheduled days (BOSUN_DAYS, pmset's letters) from BOSUN_START_TIME to
BOSUN_AWAKE_UNTIL, in local time (naive datetimes). Shared by the watchdog, the Slack watcher, the
status and `bosun morning --now`, which asks its command line:

    awake.py remaining --days MTWRF --start 08:00 --until 20:00

prints the whole seconds left in the window that now is in, and exits 0; outside every window it prints
nothing and exits 3 (OUTSIDE: not 1, which is what Python exits with after a crash, so that a caller never
takes a crash for "outside"); settings that do not parse are one line on stderr and exit 2."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import NoReturn


OUTSIDE = 3  # the exit status of `remaining` outside every window
DAY_LETTERS = "MTWRFSU"  # pmset's letters, Monday first like date.weekday()


def parse_hhmm(value: str) -> time:
    """HH:MM as a time; ValueError otherwise."""
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class AwakeWindow:
    days: str
    start: time
    until: time

    def scheduled(self, day: date) -> bool:
        return DAY_LETTERS[day.weekday()] in self.days

    def bounds(self, day: date) -> tuple[datetime, datetime]:
        """The window that starts on <day>. An AWAKE_UNTIL at or before the start time is on the
        next day: 00:00 is midnight, 01:00 is one in the morning."""
        begin = datetime.combine(day, self.start)
        if self.until > self.start:
            return begin, datetime.combine(day, self.until)
        return begin, datetime.combine(day + timedelta(days=1), self.until)

    def current(self, now: datetime) -> tuple[datetime, datetime] | None:
        """The window <now> is in: today's, or yesterday's when it runs past midnight."""
        for day in (now.date(), now.date() - timedelta(days=1)):
            begin, end = self.bounds(day)
            if self.scheduled(day) and begin <= now < end:
                return begin, end
        return None

    def contains(self, now: datetime) -> bool:
        return self.current(now) is not None


class SettingsError(Exception):
    """The command line or a setting is wrong; the message is the one line to show."""


class OneLineParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SettingsError(message)


def window_from_settings(days: str, start: str, until: str) -> AwakeWindow:
    """The window the three settings name; SettingsError (the message names the setting) if one is wrong."""
    unknown = sorted(set(days) - set(DAY_LETTERS))
    if unknown:
        raise SettingsError(f"BOSUN_DAYS has {''.join(unknown)!r}; the letters are {DAY_LETTERS}")
    times = []
    for name, value in (("BOSUN_START_TIME", start), ("BOSUN_AWAKE_UNTIL", until)):
        try:
            times.append(parse_hhmm(value))
        except ValueError as exc:
            raise SettingsError(f"{name} must be HH:MM, got {value!r}") from exc
    return AwakeWindow(days, times[0], times[1])


def remaining_seconds(window: AwakeWindow, now: datetime) -> int | None:
    """The whole seconds (a part of one counts) left in the window <now> is in; None outside every one."""
    current = window.current(now)
    return None if current is None else math.ceil((current[1] - now).total_seconds())


def main(argv: Sequence[str], now: datetime | None = None) -> int:
    parser = OneLineParser(prog="awake.py", allow_abbrev=False)
    parser.add_argument("command", choices=("remaining",))
    parser.add_argument("--days", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--until", required=True)
    try:
        args = parser.parse_args(argv)
        window = window_from_settings(args.days, args.start, args.until)
    except SettingsError as exc:
        print(f"awake.py: {exc}", file=sys.stderr)
        return 2
    seconds = remaining_seconds(window, now or datetime.now())
    if seconds is None:
        return OUTSIDE
    print(seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
