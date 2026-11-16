import unittest

from exporter import as_csv


class CsvExportTest(unittest.TestCase):
    def test_csv(self) -> None:
        self.assertEqual(as_csv([{"price": 10.0, "quantity": 3}]), "10.0,3")


if __name__ == "__main__":
    unittest.main()
