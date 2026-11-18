"""lib/wait_knobs.py: a test-only wait from the environment, or its default for anything but a finite
number above 0."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import wait_knobs


KNOB = "BOSUN_ACME_POLL_SECONDS"


class SecondsTest(unittest.TestCase):
    def seconds(self, value: str | None) -> float:
        with mock.patch.dict(os.environ):
            os.environ.pop(KNOB, None)
            if value is not None:
                os.environ[KNOB] = value
            return wait_knobs.seconds(KNOB, 5)

    def test_a_positive_number_is_the_wait(self) -> None:
        self.assertEqual(self.seconds("0.5"), 0.5)
        self.assertEqual(self.seconds("12"), 12)

    def test_unset_or_empty_is_the_default(self) -> None:
        self.assertEqual(self.seconds(None), 5)
        self.assertEqual(self.seconds(""), 5)

    def test_anything_but_a_finite_number_above_zero_is_the_default(self) -> None:
        for value in ("soon", "0", "0.0", "-1", "-0.5", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                self.assertEqual(self.seconds(value), 5)


if __name__ == "__main__":
    unittest.main()
