import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from commitment_report.contracts import IndicatorDefinition, SectorSubmission


class CommitmentContractTests(unittest.TestCase):
    def test_submission_distinguishes_missing_value(self):
        value = SectorSubmission("transport", "share", date(2026, 6, 30), None, ())
        self.assertIsNone(value.value)

    def test_indicator_has_boundary_revision(self):
        item = IndicatorDefinition("renewable_share", "%", 2020, "scope-3")
        self.assertEqual(item.boundary_revision, "scope-3")


if __name__ == "__main__":
    unittest.main()
