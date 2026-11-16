import unittest

from renderer.vat_breakdown import vat_breakdown


class VatBreakdownTest(unittest.TestCase):
    def test_groups_by_rate(self) -> None:
        lines = [
            {"price": 10.0, "quantity": 2, "vat_rate": 0.19},
            {"price": 5.0, "quantity": 1, "vat_rate": 0.19},
            {"price": 8.0, "quantity": 1, "vat_rate": 0.07},
        ]
        self.assertEqual(
            vat_breakdown(lines),
            {"0.07": {"net": "8.00", "vat": "0.56"}, "0.19": {"net": "25.00", "vat": "4.75"}},
        )

    def test_vat_rounds_per_line(self) -> None:
        lines = [{"price": 0.33, "quantity": 1, "vat_rate": 0.19}] * 2
        self.assertEqual(vat_breakdown(lines)["0.19"]["vat"], "0.12")


if __name__ == "__main__":
    unittest.main()
