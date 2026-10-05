import unittest

import support
from commitment_report.values import ReportedValue


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.svc = support.make_service()

    def codes(self, findings):
        return {f.code for f in findings}

    def test_estimate_without_source_is_flagged(self):
        self.svc.submit_indicator(
            indicator_id="IND-ESTIMATE",
            sector="industry",
            metric="co2_emission",
            quantity_kind="physical",
            unit="MtCO2",
            baseline_year=2020,
            boundary_revision="B-2020",
            coverage=["chemicals"],
            value=ReportedValue.of("12"),
            is_estimate=True,  # 估算值但未给来源
        )
        self.assertIn("ESTIMATE_WITHOUT_SOURCE", self.codes(self.svc.validate()))

    def test_estimate_with_source_passes(self):
        self.svc.register_source(
            "SRC-EST", "测算底稿", "工业司", support.date(2026, 5, 1)
        )
        self.svc.submit_indicator(
            indicator_id="IND-ESTIMATE",
            sector="industry",
            metric="co2_emission",
            quantity_kind="physical",
            unit="MtCO2",
            baseline_year=2020,
            boundary_revision="B-2020",
            coverage=["chemicals"],
            value=ReportedValue.of("12"),
            is_estimate=True,
            evidence=["SRC-EST"],
        )
        self.assertNotIn("ESTIMATE_WITHOUT_SOURCE", self.codes(self.svc.validate()))

    def test_overlap_detected_on_shared_coverage(self):
        self.svc.submit_indicator(
            indicator_id="IND-ENERGY-DUP",
            sector="energy",
            metric="co2_emission",
            quantity_kind="physical",
            unit="MtCO2",
            baseline_year=2020,
            boundary_revision="B-2020",
            coverage=["coal"],  # 与 IND-ENERGY 的 coal 重叠
            value=ReportedValue.of("30"),
            evidence=["SRC-ENERGY"],
        )
        findings = self.svc.validate()
        self.assertIn("INDICATOR_OVERLAP", self.codes(findings))
        overlap = [f for f in findings if f.code == "INDICATOR_OVERLAP"][0]
        self.assertIn("IND-ENERGY", overlap.subjects[0])

    def test_boundary_revision_mismatch_detected(self):
        # 政策调整：工业司改用新统计边界报送
        self.svc.revise_indicator(
            "IND-INDUSTRY",
            value=ReportedValue.of("80"),
            boundary_revision="B-2025",
            evidence=["SRC-ENERGY"],
        )
        self.assertIn("INCOMPATIBLE_DEFINITION", self.codes(self.svc.validate()))

    def test_intensity_and_physical_not_addable(self):
        self.svc.submit_indicator(
            indicator_id="IND-INTENSITY",
            sector="industry",
            metric="co2_emission",
            quantity_kind="intensity",  # 强度指标
            unit="tCO2/万元",
            baseline_year=2020,
            boundary_revision="B-2020",
            coverage=["textile"],
            value=ReportedValue.of("1.2"),
            evidence=["SRC-ENERGY"],
        )
        self.assertIn("INCOMPATIBLE_DEFINITION", self.codes(self.svc.validate()))

    def test_unknown_source_reference_rejected(self):
        with self.assertRaises(KeyError):
            self.svc.submit_indicator(
                indicator_id="IND-BAD",
                sector="energy",
                metric="co2_emission",
                quantity_kind="physical",
                unit="MtCO2",
                baseline_year=2020,
                boundary_revision="B-2020",
                evidence=["SRC-NOPE"],
            )


if __name__ == "__main__":
    unittest.main()
