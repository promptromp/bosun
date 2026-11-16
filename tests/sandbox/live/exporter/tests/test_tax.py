import unittest

from exporter.tax import rate_for, round_tax


class TaxTest(unittest.TestCase):
    def test_rate_by_country(self) -> None:
        self.assertEqual(rate_for("DE"), 0.19)
        self.assertEqual(rate_for("nl"), 0.21)

    def test_a_country_without_a_rate_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            rate_for("ZZ")

    def test_rounds_half_up(self) -> None:
        self.assertEqual(round_tax("0.125"), "0.13")
        self.assertEqual(round_tax("0.124"), "0.12")


if __name__ == "__main__":
    unittest.main()
