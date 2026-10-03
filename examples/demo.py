"""端到端示例：能源/交通/工业三部门先后报送、退回重报、版本确认。

运行：PYTHONPATH=src python3 examples/demo.py
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from commitment_report import (
    Boundary, Evidence, IndicatorDefinition, ReportService, ReviewDecision,
    Target, ValueKind,
)


def main() -> None:
    svc = ReportService(country="ATLANTIS")

    # 1) 证据图：来源材料、统计边界、指标口径、目标
    svc.register_evidence(Evidence("EV-EN-2024", "能源统计年鉴", "统计局",
                                   published_on=date(2025, 3, 1)))
    svc.register_evidence(Evidence("EV-TR-2024", "交通排放核算表", "交通部"))
    svc.register_evidence(Evidence("EV-IN-2024", "工业能源普查", "工业部"))
    svc.register_boundary(Boundary("B-TERR", "rev-2019", "领土边界"))
    svc.register_indicator(IndicatorDefinition(
        code="co2_energy", name="能源相关二氧化碳排放", unit="MtCO2e",
        baseline_year=2015, boundary_revision="rev-2019",
        value_kind=ValueKind.ABSOLUTE, boundary_id="B-TERR",
        sectors=("energy", "transport", "industry")))
    svc.register_target(Target(
        "T1", "ATLANTIS", "co2_energy", 2030, Decimal("120"), "MtCO2e",
        2015, "相对2015年下降", "EV-EN-2024"))

    def submit_accept(sector, value, evidence, **kw):
        rec = svc.submit("co2_energy", sector, 2024, value,
                         evidence_ids=(evidence,), **kw)
        svc.review(rec.id, "editor", ReviewDecision.APPROVE, "核对一致")
        return rec

    # 2) 能源、交通先到；工业未到
    submit_accept("energy", Decimal("100"), "EV-EN-2024")
    submit_accept("transport", Decimal("30"), "EV-TR-2024")
    print("== 工业未到时的汇总 ==")
    line = next(a for a in svc.current_aggregates()
                if a.indicator_code == "co2_energy" and a.period_year == 2024)
    print(f"  value={line.value} {line.unit} method={line.method} "
          f"未报告={line.not_reported_sectors}")

    # 3) 工业迟到，且错报为强度单位 -> 被检测并阻断总数
    bad = svc.submit("co2_energy", "industry", 2024, Decimal("0.4"),
                     unit="tCO2/kWh", evidence_ids=("EV-IN-2024",))
    svc.review(bad.id, "editor", ReviewDecision.RETURN, "单位与口径不符，退回")
    print("\n== 错误单位触发的问题 ==")
    for i in svc.current_issues():
        print(f"  [{i.level.value}] {i.code}: {i.message}")

    # 4) 工业重新提交正确口径值
    good = svc.submit("co2_energy", "industry", 2024, Decimal("40"),
                      evidence_ids=("EV-IN-2024",), supersedes=bad.id)
    svc.review(good.id, "editor", ReviewDecision.APPROVE, "更正后接受")
    svc.lock_chapter("co2_energy", "lead", "v1 章节定稿")
    svc.confirm_version("secretariat", "对外确认 v1")
    line = next(a for a in svc.export_version(1)["aggregates"]
                if a["indicator_code"] == "co2_energy")
    print(f"\n== 已确认 v1 ==\n  国家汇总={line['value']} {line['unit']}，"
          f"来自 {line['component_count']} 个兼容分项")

    # 5) v1 确认后，交通部门晚到修订（先解锁、退回旧件、重新提交）
    svc.unlock_chapter("co2_energy", "lead", "接收交通修订")
    old = next(r for r in svc._submissions.values()
               if r.sector == "transport" and r.period_year == 2024
               and r.state.value == "accepted")
    svc.review(old.id, "editor", ReviewDecision.RETURN, "源数据更新")
    rev = svc.submit("co2_energy", "transport", 2024, Decimal("35"),
                     evidence_ids=("EV-TR-2024",), supersedes=old.id)
    svc.review(rev.id, "editor", ReviewDecision.APPROVE, "修订接受")
    svc.confirm_version("secretariat", "对外确认 v2")

    print("\n== 版本差异 v1 -> v2 ==")
    print(json.dumps(svc.diff_versions(1, 2), ensure_ascii=False, indent=2,
                     default=str))
    v1 = svc.export_version(1)["aggregates"]
    print("== 旧版本 v1 数据包保持不变 ==")
    print("  v1 value =", next(a["value"] for a in v1
          if a["indicator_code"] == "co2_energy"))


if __name__ == "__main__":
    main()
