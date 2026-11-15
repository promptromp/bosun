import unittest

from sync.mapping import to_entry


class MappingTest(unittest.TestCase):
    def test_an_invoice_is_whole_cents_on_the_account_of_its_country(self) -> None:
        invoice = {"number": "ACME-0001", "country": "DE", "lines": [{"price": 10.5, "quantity": 2}]}
        self.assertEqual(
            to_entry(invoice), {"reference": "ACME-0001", "account": "4000", "cents": 2100, "memo": "invoice"}
        )

    def test_a_credit_note_is_negative_and_says_what_it_credits(self) -> None:
        note = {
            "number": "ACME-0002",
            "country": "FR",
            "kind": "credit_note",
            "credits": "ACME-0001",
            "lines": [{"price": -10.0, "quantity": 1}],
        }
        entry = to_entry(note)
        self.assertEqual(entry["cents"], -1000)
        self.assertEqual(entry["memo"], "credit for ACME-0001")


if __name__ == "__main__":
    unittest.main()
