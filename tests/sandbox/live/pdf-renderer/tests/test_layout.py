import unittest

from renderer import render
from renderer.layout import WIDTH, line_row


class LayoutTest(unittest.TestCase):
    def test_a_row_is_as_wide_as_the_page(self) -> None:
        self.assertEqual(len(line_row("Consulting", "120.00")), WIDTH)

    def test_a_long_description_is_cut_to_fit(self) -> None:
        self.assertEqual(len(line_row("x" * 100, "1.00")), WIDTH)

    def test_render_shows_the_lines_and_the_breakdown(self) -> None:
        invoice = {
            "number": "ACME-0001",
            "customer": "ACME",
            "lines": [{"description": "Widgets", "price": 10.0, "quantity": 2, "vat_rate": 0.19}],
        }
        page = render(invoice)
        self.assertIn("Invoice ACME-0001", page)
        self.assertIn("2 x Widgets", page)
        self.assertIn("VAT 19% on 20.00", page)


if __name__ == "__main__":
    unittest.main()
