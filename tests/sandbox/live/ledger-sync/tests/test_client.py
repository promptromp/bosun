from __future__ import annotations

import unittest

from sync.client import LedgerClient, LedgerError


class FakeLedger:
    """Answers with each of <statuses> in turn, then the last one for ever."""

    def __init__(self, *statuses: int, headers: dict[str, str] | None = None) -> None:
        self.statuses = list(statuses)
        self.headers = headers or {}
        self.calls = 0

    def __call__(self, path: str, payload: dict) -> tuple[int, dict[str, str], dict]:
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return status, self.headers, {"ok": status < 300}


class ClientTest(unittest.TestCase):
    def test_a_good_answer_is_returned(self) -> None:
        self.assertEqual(
            LedgerClient(FakeLedger(200), sleep=lambda s: None).post("/entries", {}), {"ok": True}
        )

    def test_a_429_is_retried_with_backoff(self) -> None:
        waits: list[float] = []
        ledger = FakeLedger(429, 429, 200)
        LedgerClient(ledger, sleep=waits.append).post("/entries", {})
        self.assertEqual(waits, [1.0, 2.0])
        self.assertEqual(ledger.calls, 3)

    def test_retry_after_is_obeyed(self) -> None:
        waits: list[float] = []
        LedgerClient(FakeLedger(429, 200, headers={"Retry-After": "7"}), sleep=waits.append).post(
            "/entries", {}
        )
        self.assertEqual(waits, [7.0])

    def test_it_gives_up_after_its_retries(self) -> None:
        with self.assertRaises(LedgerError):
            LedgerClient(FakeLedger(429), sleep=lambda s: None, retries=2).post("/entries", {})

    def test_another_error_is_not_retried(self) -> None:
        ledger = FakeLedger(500)
        with self.assertRaises(LedgerError):
            LedgerClient(ledger, sleep=lambda s: None).post("/entries", {})
        self.assertEqual(ledger.calls, 1)


if __name__ == "__main__":
    unittest.main()
