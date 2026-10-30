"""The awake window: scheduled days (BOSUN_DAYS, pmset's letters) from BOSUN_START_TIME to
BOSUN_AWAKE_UNTIL, in local time (naive datetimes). Shared by the watchdog and the Slack
watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


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
