"""The ledger API client: a call that is told to slow down (429) waits and tries again."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


# A transport is one HTTP call: (path, payload) -> (status, headers, body).
Transport = Callable[[str, dict[str, Any]], tuple[int, dict[str, str], dict[str, Any]]]


class LedgerError(Exception):
    """The ledger refused a call, or kept asking us to wait."""


class LedgerClient:
    def __init__(
        self,
        transport: Transport,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 5,
        backoff: float = 1.0,
    ) -> None:
        self.transport = transport
        self.sleep = sleep
        self.retries = retries
        self.backoff = backoff

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Post <payload>; on a 429, wait (`Retry-After`, else 1, 2, 4 ... seconds) and try again."""
        for attempt in range(self.retries + 1):
            status, headers, body = self.transport(path, payload)
            if status < 300:
                return body
            if status != 429:
                raise LedgerError(f"{path}: the ledger answered {status}")
            if attempt < self.retries:
                self.sleep(float(headers.get("Retry-After", self.backoff * 2**attempt)))
        raise LedgerError(f"{path}: still rate limited after {self.retries} retries")
