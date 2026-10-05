import unittest
from decimal import Decimal

import support  # noqa: F401  (设置 sys.path)
from commitment_report.values import ReportedValue, ValueKind


class ReportedValueTests(unittest.TestCase):
    def test_reported_zero_is_valid_data(self):
        value = ReportedValue.of(0)
        self.assertTrue(value.is_reported)
        self.assertTrue(value.is_zero)
        self.assertEqual(value.amount, Decimal("0"))

    def test_not_reported_is_distinct_from_zero(self):
        missing = ReportedValue.not_reported()
        self.assertFalse(missing.is_reported)
        self.assertFalse(missing.is_zero)
        self.assertNotEqual(missing, ReportedValue.of(0))

    def test_reported_requires_amount(self):
        with self.assertRaises(ValueError):
            ReportedValue(ValueKind.REPORTED, None)

    def test_not_reported_forbids_amount(self):
        with self.assertRaises(ValueError):
            ReportedValue(ValueKind.NOT_REPORTED, Decimal("1"))


if __name__ == "__main__":
    unittest.main()
