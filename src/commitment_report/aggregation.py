"""国家汇总推导。

铁律：国家汇总只能由口径兼容的分项推导，且按期次（年份）分别推导。
* 实物量（absolute）：单位相同、边界兼容时直接相加；
* 强度/占比（intensity/share）：只能按驱动量做加权平均，禁止算术平均；
* “未报告”的部门不参与计算，并在结果中显式列出，绝不按零处理；
* “报告为零”是合法报送值，正常参与计算。

只要存在任何阻断性问题（错误级），该指标该期次不产出数值汇总，只产出缺口说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .contracts import Issue, IssueLevel, ReportingStatus, SubmissionRecord
from .validation import ValidationEngine


@dataclass(frozen=True)
class AggregateLine:
    indicator_code: str
    period_year: int
    derived: bool
    value: Decimal | None
    unit: str | None
    method: str
    component_count: int
    contributing_submission_ids: tuple[str, ...]
    zero_reported_sectors: tuple[str, ...]
    not_reported_sectors: tuple[str, ...]
    blocking_issues: tuple[Issue, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = ()


def derive_aggregates(
    indicators: dict,
    records: list[SubmissionRecord],
    validator: ValidationEngine,
    boundaries: dict,
) -> list[AggregateLine]:
    all_issues = validator.validate_set(records)
    lines: list[AggregateLine] = []

    # 指标 -> 期次 -> 报送
    grouped: dict[str, dict[int, list[SubmissionRecord]]] = {}
    for rec in records:
        grouped.setdefault(rec.indicator_code, {}).setdefault(
            rec.period_year, []).append(rec)

    for code, ind in indicators.items():
        periods = grouped.get(code, {})
        if not periods:
            lines.append(AggregateLine(
                code, 0, False, None, ind.unit, "none", 0, (), (),
                tuple(ind.sectors),
                notes=("无任何分项报送",)))
            continue

        for year in sorted(periods):
            recs = periods[year]
            ids = {r.id for r in recs}
            blocking = tuple(i for i in all_issues
                             if i.level == IssueLevel.ERROR
                             and (i.subject == f"indicator:{code}@{year}"
                                  or (i.context.get("indicator_code") == code
                                      and i.context.get("period_year") == year)
                                  or i.subject in
                                      {f"submission:{sid}" for sid in ids}))
            zero_sectors = tuple(sorted(
                r.sector for r in recs
                if r.reporting_status == ReportingStatus.REPORTED
                and r.value == Decimal("0")))
            # 未报告 = 该期次没有“已报告”值的部门，
            # 既包括从未报送的部门，也包括显式声明“未报告(null)”的部门
            sectors_with_value = {
                r.sector for r in recs
                if r.reporting_status == ReportingStatus.REPORTED}
            not_reported = tuple(sorted(
                s for s in ind.sectors if s not in sectors_with_value))

            if blocking:
                lines.append(AggregateLine(
                    code, year, False, None, ind.unit, "blocked", len(recs),
                    (), zero_sectors, not_reported, blocking_issues=blocking,
                    notes=("存在口径错误，汇总被阻断，禁止把不兼容数值直接相加",)))
                continue

            reported = [r for r in recs
                        if r.reporting_status == ReportingStatus.REPORTED]
            if not reported:
                lines.append(AggregateLine(
                    code, year, False, None, ind.unit, "all_not_reported", 0,
                    (), zero_sectors, not_reported,
                    notes=("所有分项均为未报告，无法推导汇总",)))
                continue

            ids = tuple(r.id for r in reported)
            if ind.value_kind.value == "absolute":
                total = sum((r.value for r in reported), Decimal("0"))
                method = "sum"
            else:
                # 驱动量加权：Σ(value×driver)/Σdriver。
                # 到这里所有记录均带兼容驱动量（否则 missing_driver 已阻断）。
                weight = sum((r.driver_value for r in reported), Decimal("0"))
                if weight == 0:
                    lines.append(AggregateLine(
                        code, year, False, None, ind.unit, "zero_driver",
                        len(reported), ids, zero_sectors, not_reported,
                        notes=("驱动量合计为零，无法做加权推导",)))
                    continue
                total = (
                    sum((r.value * r.driver_value for r in reported), Decimal("0"))
                    / weight
                )
                method = "driver_weighted_average"

            notes: list[str] = []
            if not_reported:
                notes.append(
                    f"以下部门未报告、未纳入推导（不等于零）：{', '.join(not_reported)}")
            lines.append(AggregateLine(
                code, year, True, total, ind.unit, method, len(reported), ids,
                zero_sectors, not_reported, notes=tuple(notes)))

    return lines
