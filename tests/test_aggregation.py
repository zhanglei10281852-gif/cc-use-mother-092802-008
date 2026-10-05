import unittest
from decimal import Decimal

import support
from commitment_report.aggregation import AggregateStatus
from commitment_report.values import ReportedValue


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.svc = support.make_service()

    def test_partial_when_sector_missing(self):
        result = self.svc.preview_aggregate()
        self.assertEqual(result.status, AggregateStatus.PARTIAL)
        self.assertIsNone(result.value)  # 未凑齐不得给出全量数
        self.assertEqual(result.reported_subtotal, Decimal("150.5"))
        self.assertEqual(result.missing, ("IND-INDUSTRY",))

    def test_zero_is_counted_not_treated_as_missing(self):
        self.svc.revise_indicator("IND-INDUSTRY", value=ReportedValue.of(0))
        result = self.svc.preview_aggregate()
        self.assertEqual(result.status, AggregateStatus.COMPLETE)
        self.assertEqual(result.value, Decimal("150.5"))
        self.assertEqual(result.missing, ())

    def test_complete_sum_when_all_reported(self):
        self.svc.revise_indicator("IND-INDUSTRY", value=ReportedValue.of("80"))
        result = self.svc.preview_aggregate()
        self.assertEqual(result.status, AggregateStatus.COMPLETE)
        self.assertEqual(result.value, Decimal("230.5"))

    def test_incompatible_boundary_blocks_aggregation(self):
        self.svc.revise_indicator(
            "IND-INDUSTRY",
            value=ReportedValue.of("80"),
            boundary_revision="B-2025",
            evidence=["SRC-ENERGY"],
        )
        result = self.svc.preview_aggregate()
        self.assertEqual(result.status, AggregateStatus.BLOCKED)
        self.assertIsNone(result.value)
        self.assertIsNone(result.reported_subtotal)

    def test_overlap_blocks_aggregation(self):
        self.svc.submit_indicator(
            indicator_id="IND-ENERGY-DUP",
            sector="energy",
            metric="co2_emission",
            quantity_kind="physical",
            unit="MtCO2",
            baseline_year=2020,
            boundary_revision="B-2020",
            coverage=["coal"],
            value=ReportedValue.of("30"),
            evidence=["SRC-ENERGY"],
        )
        result = self.svc.preview_aggregate()
        self.assertEqual(result.status, AggregateStatus.BLOCKED)

    def test_provenance_traces_to_source_materials(self):
        self.svc.revise_indicator(
            "IND-INDUSTRY", value=ReportedValue.of("80"), evidence=["SRC-ENERGY"]
        )
        result = self.svc.preview_aggregate()
        source_refs = {(sid, ver) for sid, ver, _ in result.provenance.sources}
        self.assertEqual(source_refs, {("SRC-ENERGY", 1), ("SRC-TRANSPORT", 1)})
        method_refs = {mid for mid, _, _ in result.provenance.methods}
        self.assertEqual(method_refs, {"M-IPCC"})


if __name__ == "__main__":
    unittest.main()
