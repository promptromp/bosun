"""Run with: python3 -m unittest"""

import unittest

from exporter import as_csv, invoice_total


class ExporterTest(unittest.TestCase):
    def test_one_of_each(self) -> None:
        self.assertEqual(invoice_total([{"price": 10.0, "quantity": 1}], 0.0), 10.0)

    def test_tax_is_added(self) -> None:
        self.assertEqual(invoice_total([{"price": 10.0, "quantity": 1}], 0.2), 12.0)

    def test_quantity_counts(self) -> None:
        self.assertEqual(invoice_total([{"price": 10.0, "quantity": 3}], 0.0), 30.0)

    def test_csv(self) -> None:
        self.assertEqual(as_csv([{"price": 10.0, "quantity": 3}]), "10.0,3")


if __name__ == "__main__":
    unittest.main()
