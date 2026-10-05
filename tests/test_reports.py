import unittest
from decimal import Decimal

import support
from commitment_report.aggregation import AggregateStatus
from commitment_report.values import ReportedValue


class ReportVersionTests(unittest.TestCase):
    def setUp(self):
        self.svc = support.make_service()
        self.svc.define_target(
            "NDC-1", "2030 年前碳达峰", "co2_emission", "physical",
            "MtCO2", 2020, "B-2020", 2030, "230",
            indicator_ids=["IND-ENERGY", "IND-TRANSPORT", "IND-INDUSTRY"],
        )
        self.svc.create_chapter("CH-ENERGY", "能源部门进展", "sectoral", ["IND-ENERGY"])
        self.svc.submit_chapter("CH-ENERGY")
        self.svc.create_report("RPT-2026", "2026 年进展报告")

    def test_confirmed_version_is_immutable_against_late_revisions(self):
        svc = self.svc
        svc.confirm_report("RPT-2026")
        export_before = svc.export_report("RPT-2026", 1)

        # 晚到的部门修订：工业补报、能源改数、来源材料出新版
        svc.revise_indicator("IND-INDUSTRY", value=ReportedValue.of("80"),
                             evidence=["SRC-ENERGY"])
        svc.revise_indicator("IND-ENERGY", value=ReportedValue.of("101.5"))
        svc.register_source("SRC-ENERGY", "能源统计年鉴2025（修订版）",
                            "能源司", support.date(2026, 8, 1))
        svc.confirm_report("RPT-2026")

        export_after = svc.export_report("RPT-2026", 1)
        self.assertEqual(export_before, export_after)  # 已确认版本不受影响
        v1_industry = next(
            i for i in export_after["indicators"] if i["indicator_id"] == "IND-INDUSTRY"
        )
        self.assertEqual(v1_industry["value"]["kind"], "not_reported")
        self.assertEqual(
            export_after["national_aggregate"]["status"], "partial"
        )

    def test_diff_summary_between_versions(self):
        svc = self.svc
        svc.confirm_report("RPT-2026")
        svc.revise_indicator("IND-INDUSTRY", value=ReportedValue.of("80"),
                             evidence=["SRC-ENERGY"])
        svc.revise_indicator("IND-ENERGY", value=ReportedValue.of("101.5"))
        svc.confirm_report("RPT-2026")

        diff = svc.diff_reports("RPT-2026", 1, 2)
        changed = diff["indicators"]["changed"]
        self.assertIn("IND-INDUSTRY", changed)
        self.assertEqual(
            changed["IND-INDUSTRY"]["value"]["old"]["kind"], "not_reported"
        )
        self.assertEqual(
            changed["IND-INDUSTRY"]["value"]["new"]["amount"], "80"
        )
        self.assertEqual(changed["IND-ENERGY"]["value"]["new"]["amount"], "101.5")

        aggregate = diff["national_aggregate"]
        self.assertEqual(aggregate["status"], {"old": "partial", "new": "complete"})
        self.assertEqual(aggregate["value"]["old"], None)
        self.assertEqual(aggregate["value"]["new"], "231.5")

    def test_export_traces_aggregate_to_source_materials(self):
        svc = self.svc
        svc.revise_indicator("IND-INDUSTRY", value=ReportedValue.of("80"),
                             evidence=["SRC-ENERGY"])
        svc.confirm_report("RPT-2026")
        package = svc.export_report("RPT-2026", 1)

        provenance = package["national_aggregate"]["provenance"]
        self.assertIn(("SRC-ENERGY", 1, "能源统计年鉴2025"),
                      [tuple(s) for s in provenance["sources"]])
        self.assertEqual(package["manifest"]["version"], 1)
        self.assertIn("content_sha256", package["manifest"])
        # 数据包内的来源材料清单与溯源一致
        self.assertEqual(
            {s["source_id"] for s in package["sources"]},
            {"SRC-ENERGY", "SRC-TRANSPORT"},
        )

    def test_modified_aggregate_uses_pinned_source_version(self):
        svc = self.svc
        svc.confirm_report("RPT-2026")  # v1：引用 SRC-ENERGY@1
        # 来源材料修订 + 指标改用新版材料
        svc.register_source("SRC-ENERGY", "能源统计年鉴2025（修订版）",
                            "能源司", support.date(2026, 8, 1))
        svc.revise_indicator("IND-ENERGY", value=ReportedValue.of("101.5"),
                             evidence=["SRC-ENERGY@2"])
        svc.confirm_report("RPT-2026")  # v2

        v1_sources = {(s["source_id"], s["version"])
                      for s in svc.export_report("RPT-2026", 1)["sources"]}
        v2_sources = {(s["source_id"], s["version"])
                      for s in svc.export_report("RPT-2026", 2)["sources"]}
        self.assertIn(("SRC-ENERGY", 1), v1_sources)
        self.assertNotIn(("SRC-ENERGY", 2), v1_sources)
        self.assertIn(("SRC-ENERGY", 2), v2_sources)

    def test_aggregate_in_snapshot_only_from_compatible_parts(self):
        svc = self.svc
        svc.revise_indicator("IND-INDUSTRY", value=ReportedValue.of("80"),
                             boundary_revision="B-2025", evidence=["SRC-ENERGY"])
        snapshot = svc.confirm_report("RPT-2026")
        self.assertEqual(snapshot.aggregate.status, AggregateStatus.BLOCKED)
        self.assertIsNone(snapshot.aggregate.value)


if __name__ == "__main__":
    unittest.main()
