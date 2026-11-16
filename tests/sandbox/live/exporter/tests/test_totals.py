"""Run with: python3 -m unittest"""

import unittest

from exporter import invoice_total


class TotalsTest(unittest.TestCase):
    def test_one_of_each(self) -> None:
        self.assertEqual(invoice_total([{"price": 10.0, "quantity": 1}], 0.0), 10.0)

    def test_tax_is_added(self) -> None:
        self.assertEqual(invoice_total([{"price": 10.0, "quantity": 1}], 0.2), 12.0)

    def test_quantity_counts(self) -> None:
        self.assertEqual(invoice_total([{"price": 10.0, "quantity": 3}], 0.0), 30.0)

    def test_tax_rounds_per_line(self) -> None:
        # 0.33 plus 19% is 0.3927, so 0.39 on each of the two lines: 0.78, where rounding once gives 0.79.
        lines = [{"price": 0.33, "quantity": 1}, {"price": 0.33, "quantity": 1}]
        self.assertEqual(invoice_total(lines, 0.19), 0.78)


if __name__ == "__main__":
    unittest.main()
