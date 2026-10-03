"""端到端领域测试：口径校验、汇总推导、工作流与版本不可变。"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from commitment_report import (
    Boundary,
    ChapterState,
    Evidence,
    IndicatorDefinition,
    IssueLevel,
    ReportService,
    ReportingStatus,
    ReviewDecision,
    SubmissionState,
    Target,
    ValueKind,
    WorkflowError,
)


def build_service():
    svc = ReportService(country="ATLANTIS")
    svc.register_evidence(Evidence("EV-STAT-2024", "国家能源统计年鉴2024",
                                   source="统计局",
                                   published_on=date(2025, 3, 1)))
    svc.register_evidence(Evidence("EV-TRANS-2024", "交通排放核算表",
                                   source="交通部"))
    svc.register_evidence(Evidence("EV-IND-2024", "工业普查能源卷",
                                   source="工业部"))
    svc.register_boundary(Boundary("B-TERR", "rev-2019", "领土边界 rev2019",
                                   equivalence_key="territory-v1"))
    return svc


def emissions_indicator():
    return IndicatorDefinition(
        code="co2_energy", unit="MtCO2e", baseline_year=2015,
        boundary_revision="rev-2019", name="能源相关二氧化碳排放",
        value_kind=ValueKind.ABSOLUTE, boundary_id="B-TERR",
        sectors=("energy", "transport", "industry"))


def accept(svc, rec):
    return svc.review(rec.id, "editor-1", ReviewDecision.APPROVE, "核对一致")


class CaliberTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        self.svc.register_indicator(emissions_indicator())

    def test_report_zero_distinct_from_not_reported(self):
        rec = self.svc.submit(
            "co2_energy", "energy", 2024, Decimal("0"),
            evidence_ids=("EV-STAT-2024",))
        self.assertEqual(rec.reporting_status, ReportingStatus.REPORTED)
        self.assertEqual(rec.value, Decimal("0"))
        missing = self.svc.submit(
            "co2_energy", "transport", 2024, None, evidence_ids=())
        self.assertEqual(missing.reporting_status, ReportingStatus.NOT_REPORTED)
        self.assertIsNone(missing.value)
        accept(self.svc, rec)
        accept(self.svc, missing)
        agg = {a.indicator_code: a for a in self.svc.current_aggregates()}
        line = agg["co2_energy"]
        self.assertEqual(line.zero_reported_sectors, ("energy",))
        self.assertIn("transport", line.not_reported_sectors)
        self.assertIn("industry", line.not_reported_sectors)
        # 未报告部门没有被当零处理：汇总只来自“报告为零”的能源部门
        self.assertTrue(line.derived)
        self.assertEqual(line.value, Decimal("0"))
        self.assertEqual(line.method, "sum")

    def test_unit_mismatch_blocks_national_total(self):
        # 交通部门错报为强度单位 tCO2/万公里
        bad = self.svc.submit(
            "co2_energy", "transport", 2024, Decimal("12"),
            unit="tCO2/vkm", evidence_ids=("EV-TRANS-2024",))
        energy = self.svc.submit(
            "co2_energy", "energy", 2024, Decimal("100"),
            evidence_ids=("EV-STAT-2024",))
        accept(self.svc, bad)
        accept(self.svc, energy)
        issues = {i.code for i in self.svc.current_issues()}
        self.assertIn("unit_mismatch", issues)
        line = next(a for a in self.svc.current_aggregates()
                    if a.indicator_code == "co2_energy")
        self.assertFalse(line.derived)
        self.assertEqual(line.method, "blocked")
        self.assertIsNone(line.value)

    def test_overlapping_coverage_is_detected(self):
        a = self.svc.submit("co2_energy", "energy", 2024, Decimal("60"),
                            coverage=("energy", "aviation"),
                            evidence_ids=("EV-STAT-2024",))
        b = self.svc.submit("co2_energy", "transport", 2024, Decimal("30"),
                            coverage=("transport", "aviation"),
                            evidence_ids=("EV-TRANS-2024",))
        accept(self.svc, a)
        accept(self.svc, b)
        overlap = [i for i in self.svc.current_issues() if i.code == "overlap"]
        self.assertEqual(len(overlap), 1)
        self.assertEqual(overlap[0].context["atom"], "aviation")

    def test_incompatible_boundaries_block_total(self):
        # 工业部门政策调整后改报新边界 rev-2024，与其他分项不兼容
        self.svc.register_boundary(Boundary(
            "B-TERR24", "rev-2024", "领土边界 rev2024"))
        a = self.svc.submit("co2_energy", "energy", 2024, Decimal("100"),
                            evidence_ids=("EV-STAT-2024",))
        b = self.svc.submit("co2_energy", "industry", 2024, Decimal("40"),
                            evidence_ids=("EV-IND-2024",),
                            boundary_id="B-TERR24", boundary_revision="rev-2024")
        accept(self.svc, a)
        accept(self.svc, b)
        codes = {i.code for i in self.svc.current_issues()}
        self.assertIn("boundary_mismatch", codes)
        line = next(x for x in self.svc.current_aggregates()
                    if x.indicator_code == "co2_energy")
        self.assertFalse(line.derived)

    def test_equivalent_boundary_revision_is_compatible(self):
        # 同一等价组内的边界修订允许汇总
        self.svc.register_boundary(Boundary(
            "B-TERR-FIX", "rev-2020", "技术勘误",
            equivalence_key="territory-v1"))
        a = self.svc.submit("co2_energy", "energy", 2024, Decimal("100"),
                            evidence_ids=("EV-STAT-2024",))
        b = self.svc.submit("co2_energy", "industry", 2024, Decimal("40"),
                            evidence_ids=("EV-IND-2024",),
                            boundary_id="B-TERR-FIX",
                            boundary_revision="rev-2020")
        accept(self.svc, a)
        accept(self.svc, b)
        self.assertFalse(any(i.code == "boundary_mismatch"
                             for i in self.svc.current_issues()))

    def test_estimated_value_without_source_is_error(self):
        rec = self.svc.submit("co2_energy", "industry", 2024, Decimal("55"),
                              estimated=True, evidence_ids=())
        accept(self.svc, rec)
        codes = {i.code: i.level for i in self.svc.current_issues()}
        self.assertEqual(codes.get("estimated_without_source"),
                         IssueLevel.ERROR)
        self.assertEqual(codes.get("missing_source"), IssueLevel.ERROR)

    def test_compatible_absolute_components_sum(self):
        for sector, ev, val in (
                ("energy", "EV-STAT-2024", "100"),
                ("transport", "EV-TRANS-2024", "30"),
                ("industry", "EV-IND-2024", "40")):
            accept(self.svc, self.svc.submit(
                "co2_energy", sector, 2024, Decimal(val), evidence_ids=(ev,)))
        line = next(a for a in self.svc.current_aggregates()
                    if a.indicator_code == "co2_energy")
        self.assertTrue(line.derived)
        self.assertEqual(line.value, Decimal("170"))
        self.assertEqual(line.component_count, 3)
        self.assertEqual(line.not_reported_sectors, ())


class IntensityTests(unittest.TestCase):
    def test_intensity_uses_driver_weighted_average(self):
        svc = build_service()
        svc.register_indicator(IndicatorDefinition(
            code="grid_ci", unit="tCO2/MWh", baseline_year=2015,
            boundary_revision="rev-2019", name="电网排放因子",
            value_kind=ValueKind.INTENSITY, boundary_id="B-TERR",
            sectors=("coal", "gas", "renewable"), driver_unit="MWh"))
        # 两个区域分项：强度不得算术平均，必须按发电量加权
        a = svc.submit("grid_ci", "coal", 2024, Decimal("0.80"),
                       driver_value=Decimal("100"),
                       evidence_ids=("EV-STAT-2024",))
        b = svc.submit("grid_ci", "gas", 2024, Decimal("0.40"),
                       driver_value=Decimal("300"),
                       evidence_ids=("EV-TRANS-2024",))
        accept(svc, a)
        accept(svc, b)
        line = next(x for x in svc.current_aggregates()
                    if x.indicator_code == "grid_ci")
        self.assertEqual(line.method, "driver_weighted_average")
        # (0.8*100 + 0.4*300) / 400 = 0.5
        self.assertEqual(line.value, Decimal("0.5"))
        self.assertIn("renewable", line.not_reported_sectors)

    def test_intensity_without_driver_is_blocked(self):
        svc = build_service()
        svc.register_indicator(IndicatorDefinition(
            code="grid_ci", unit="tCO2/MWh", baseline_year=2015,
            boundary_revision="rev-2019",
            value_kind=ValueKind.INTENSITY, boundary_id="B-TERR",
            sectors=("coal",), driver_unit="MWh"))
        rec = svc.submit("grid_ci", "coal", 2024, Decimal("0.8"),
                         evidence_ids=("EV-STAT-2024",))
        accept(svc, rec)
        self.assertIn("missing_driver",
                      {i.code for i in svc.current_issues()})
        self.assertFalse(next(iter(svc.current_aggregates())).derived)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        self.svc.register_indicator(emissions_indicator())

    def test_locked_chapter_rejects_submission(self):
        self.svc.lock_chapter("co2_energy", "lead-1", "章节定稿")
        with self.assertRaises(WorkflowError):
            self.svc.submit("co2_energy", "energy", 2024, Decimal("100"),
                            evidence_ids=("EV-STAT-2024",))
        events = self.svc.chapter_events("co2_energy")
        self.assertEqual(events[0].action, "lock")
        self.svc.unlock_chapter("co2_energy", "lead-1", "接收修订")
        rec = self.svc.submit("co2_energy", "energy", 2024, Decimal("100"),
                              evidence_ids=("EV-STAT-2024",))
        self.assertEqual(rec.state, SubmissionState.SUBMITTED)

    def test_return_then_resubmit_keeps_chain(self):
        first = self.svc.submit("co2_energy", "energy", 2024, Decimal("10"),
                                evidence_ids=("EV-STAT-2024",),
                                submitted_by="energy-bureau")
        ret = self.svc.review(first.id, "editor-2", ReviewDecision.RETURN,
                              "数值疑似漏了供热，退回核对")
        self.assertEqual(ret.state, SubmissionState.RETURNED)
        # 不能直接提交新件（必须挂回被退回件）
        with self.assertRaises(WorkflowError):
            self.svc.submit("co2_energy", "energy", 2024, Decimal("110"),
                            evidence_ids=("EV-STAT-2024",))
        second = self.svc.submit(
            "co2_energy", "energy", 2024, Decimal("110"),
            evidence_ids=("EV-STAT-2024",), supersedes=first.id,
            submitted_by="energy-bureau")
        self.assertEqual(second.revision_no, 2)
        self.assertEqual(second.supersedes, first.id)
        accepted = self.svc.review(second.id, "editor-2",
                                   ReviewDecision.APPROVE, "更正后接受")
        self.assertEqual(accepted.state, SubmissionState.ACCEPTED)
        # 退回件仍然原样可查，审阅意见保留
        self.assertEqual(
            self.svc._submissions[first.id].reviews[-1].decision,
            ReviewDecision.RETURN)

    def test_only_accepted_components_enter_aggregate(self):
        rec = self.svc.submit("co2_energy", "energy", 2024, Decimal("100"),
                              evidence_ids=("EV-STAT-2024",))
        # 待审件不参与汇总
        line = next(iter(self.svc.current_aggregates()))
        self.assertFalse(line.derived)
        accept(self.svc, rec)
        self.assertTrue(next(iter(self.svc.current_aggregates())).derived)


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        self.svc.register_indicator(emissions_indicator())
        self.svc.register_target(Target(
            "T-1", "ATLANTIS", "co2_energy", 2030, Decimal("120"),
            "MtCO2e", 2015, "相对2015年下降目标", "EV-STAT-2024"))

    def _submit_all(self, energy="100", transport="30", industry="40"):
        for sector, ev, val in (
                ("energy", "EV-STAT-2024", energy),
                ("transport", "EV-TRANS-2024", transport),
                ("industry", "EV-IND-2024", industry)):
            accept(self.svc, self.svc.submit(
                "co2_energy", sector, 2024, Decimal(val), evidence_ids=(ev,)))

    def test_confirmed_version_is_immutable_to_late_revision(self):
        self._submit_all()
        v1 = self.svc.confirm_version("secretariat", "对外确认v1")
        self.assertEqual(v1.status.value, "confirmed")
        pkg1 = self.svc.export_version(1)
        agg1 = next(a for a in pkg1["aggregates"]
                    if a["indicator_code"] == "co2_energy")
        self.assertEqual(agg1["value"], "170")

        # 晚到的交通部门修订：退回旧件 -> 解锁 -> 重新提交
        old_transport = next(
            r for r in self.svc._submissions.values()
            if r.sector == "transport" and r.state == SubmissionState.ACCEPTED)
        self.svc.review(old_transport.id, "editor-3", ReviewDecision.RETURN,
                        "源数据更新")
        revised = self.svc.submit(
            "co2_energy", "transport", 2024, Decimal("35"),
            evidence_ids=("EV-TRANS-2024",), supersedes=old_transport.id)
        accept(self.svc, revised)

        # 已确认的 v1 数据包不变
        pkg1_again = self.svc.export_version(1)
        self.assertEqual(pkg1, pkg1_again)
        agg1b = next(a for a in pkg1_again["aggregates"]
                     if a["indicator_code"] == "co2_energy")
        self.assertEqual(agg1b["value"], "170")

        # 新工作态与 v2 反映修订
        v2 = self.svc.confirm_version("secretariat", "对外确认v2")
        pkg2 = self.svc.export_version(2)
        agg2 = next(a for a in pkg2["aggregates"]
                    if a["indicator_code"] == "co2_energy")
        self.assertEqual(agg2["value"], "175")

        # 差异摘要
        diff = self.svc.diff_versions(1, 2)
        change = next(c for c in diff["indicators_changed"]
                      if c["indicator_code"] == "co2_energy")
        self.assertEqual(change["base_value"], "170")
        self.assertEqual(change["head_value"], "175")
        self.assertTrue(any(sid.startswith("sub_")
                            for sid in change["head_component_ids"]))

    def test_aggregate_is_traceable_to_evidence(self):
        self._submit_all()
        self.svc.confirm_version("secretariat")
        pkg = self.svc.export_version(1)
        agg = next(a for a in pkg["aggregates"]
                   if a["indicator_code"] == "co2_energy")
        sub_by_id = {s["id"]: s for s in pkg["submissions"]}
        # 汇总 -> 分项报送 -> 来源材料 全链路可追
        self.assertEqual(set(agg["contributing_submission_ids"]),
                         set(sub_by_id) & set(agg["contributing_submission_ids"]))
        for sid in agg["contributing_submission_ids"]:
            self.assertTrue(sub_by_id[sid]["evidence_ids"])
        evidence_ids = {e for sid in agg["contributing_submission_ids"]
                        for e in sub_by_id[sid]["evidence_ids"]}
        registered = {e["id"] for e in pkg["evidence"]}
        self.assertTrue(evidence_ids <= registered)

    def test_not_reported_sector_appears_in_package(self):
        # 工业部门未报
        accept(self.svc, self.svc.submit(
            "co2_energy", "energy", 2024, Decimal("100"),
            evidence_ids=("EV-STAT-2024",)))
        accept(self.svc, self.svc.submit(
            "co2_energy", "transport", 2024, Decimal("30"),
            evidence_ids=("EV-TRANS-2024",)))
        self.svc.confirm_version("secretariat")
        pkg = self.svc.export_version(1)
        agg = next(a for a in pkg["aggregates"]
                   if a["indicator_code"] == "co2_energy")
        self.assertEqual(agg["value"], "130")
        self.assertEqual(agg["not_reported_sectors"], ["industry"])
        self.assertEqual(agg["zero_reported_sectors"], [])


if __name__ == "__main__":
    unittest.main()
