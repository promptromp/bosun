import unittest

from exporter.credit_notes import credit_note, next_number


def an_invoice(number: str = "ACME-0001") -> dict:
    return {
        "number": number,
        "customer": "ACME",
        "kind": "invoice",
        "status": "issued",
        "lines": [{"price": 10.0, "quantity": 2}, {"price": 5.0, "quantity": 1}],
    }


class CreditNotesTest(unittest.TestCase):
    def test_amounts_are_negative(self) -> None:
        note = credit_note(an_invoice(), ["ACME-0001"])
        self.assertEqual([line["price"] for line in note["lines"]], [-10.0, -5.0])
        self.assertEqual(note["credits"], "ACME-0001")

    def test_it_takes_the_next_number_of_the_customers_sequence(self) -> None:
        self.assertEqual(credit_note(an_invoice(), ["ACME-0001"])["number"], "ACME-0002")

    def test_each_customer_has_a_sequence_of_its_own(self) -> None:
        self.assertEqual(next_number("GLOBEX", ["ACME-0001", "ACME-0002"]), "GLOBEX-0001")
        self.assertEqual(next_number("ACME", ["ACME-0001", "GLOBEX-0001", "ACME-0002"]), "ACME-0003")


if __name__ == "__main__":
    unittest.main()
