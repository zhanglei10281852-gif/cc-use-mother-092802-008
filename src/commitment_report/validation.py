"""口径校验引擎：发现重叠、不兼容与缺来源的估算值。

规则总览：
* 同一指标下被接受的报送若覆盖原子部门出现交集 -> ``overlap``；
* 报送单位与指标定义不一致 -> ``unit_mismatch``；
* 指标之间汇总时边界修订不同且不在同一等价组 -> ``boundary_mismatch``；
* 标记为估算但没有任何来源材料 -> ``estimated_without_source``；
* 报送缺少来源（非估算也告警）-> ``missing_source``；
* 报送使用了未知指标 / 未知材料 / 未知边界 -> 对应错误；
* 强度/占比指标的报送若缺少驱动量 -> ``missing_driver``（无法参与汇总）。
"""

from __future__ import annotations

from .contracts import (
    Boundary,
    Evidence,
    Issue,
    IssueLevel,
    SubmissionRecord,
)


class ValidationEngine:
    def __init__(
        self,
        indicators: dict,
        evidence: dict[str, Evidence],
        boundaries: dict[str, Boundary],
    ) -> None:
        self._indicators = indicators
        self._evidence = evidence
        self._boundaries = boundaries

    # -- 单条报送 -------------------------------------------------------

    def validate_submission(self, rec: SubmissionRecord) -> list[Issue]:
        issues: list[Issue] = []
        subject = f"submission:{rec.id}"

        ind = self._indicators.get(rec.indicator_code)
        if ind is None:
            issues.append(Issue(
                "unknown_indicator", IssueLevel.ERROR,
                f"报送引用了未登记指标 {rec.indicator_code}", subject,
                {"indicator_code": rec.indicator_code}))
            return issues

        if rec.unit != ind.unit:
            issues.append(Issue(
                "unit_mismatch", IssueLevel.ERROR,
                f"{rec.sector} 报送单位 {rec.unit} 与指标 {ind.code} 的口径单位 "
                f"{ind.unit} 不一致，禁止并入同一总数",
                subject, {"submitted_unit": rec.unit, "expected_unit": ind.unit}))

        if rec.baseline_year != ind.baseline_year:
            issues.append(Issue(
                "baseline_mismatch", IssueLevel.ERROR,
                f"{rec.sector} 基准年 {rec.baseline_year} 与指标基准年 "
                f"{ind.baseline_year} 不一致",
                subject,
                {"submitted_baseline": rec.baseline_year,
                 "expected_baseline": ind.baseline_year}))

        boundary = self._boundaries.get(rec.boundary_id) if rec.boundary_id else None
        if rec.boundary_id and boundary is None:
            issues.append(Issue(
                "unknown_boundary", IssueLevel.ERROR,
                f"报送引用了未登记边界 {rec.boundary_id}",
                subject, {"boundary_id": rec.boundary_id}))
        elif boundary and boundary.revision != rec.boundary_revision:
            issues.append(Issue(
                "boundary_revision_conflict", IssueLevel.ERROR,
                f"报送声明边界修订 {rec.boundary_revision}，"
                f"但边界 {boundary.id} 登记修订为 {boundary.revision}",
                subject,
                {"submitted_revision": rec.boundary_revision,
                 "registered_revision": boundary.revision}))

        known = [e for e in rec.evidence_ids if e in self._evidence]
        if not rec.evidence_ids:
            issues.append(Issue(
                "missing_source",
                IssueLevel.ERROR if rec.estimated else IssueLevel.WARNING,
                f"{rec.sector} 的 {ind.code} 报送缺少来源材料，不能进入可追溯汇总",
                subject, {"estimated": rec.estimated}))
        else:
            for ev_id in rec.evidence_ids:
                if ev_id not in self._evidence:
                    issues.append(Issue(
                        "unknown_evidence", IssueLevel.ERROR,
                        f"报送引用了未登记材料 {ev_id}",
                        subject, {"evidence_id": ev_id}))
        if rec.estimated and not known:
            issues.append(Issue(
                "estimated_without_source", IssueLevel.ERROR,
                f"{rec.sector} 的估算值没有任何已登记来源支撑",
                subject))

        if ind.value_kind in ("intensity", "share") and rec.reporting_status.value == "reported":
            if rec.driver_value is None or rec.driver_unit is None:
                issues.append(Issue(
                    "missing_driver", IssueLevel.ERROR,
                    f"{rec.sector} 的 {ind.value_kind} 指标缺少加权驱动量，"
                    "无法与其他分项推导国家汇总",
                    subject, {"value_kind": str(ind.value_kind)}))
            elif ind.driver_unit and rec.driver_unit != ind.driver_unit:
                issues.append(Issue(
                    "driver_unit_mismatch", IssueLevel.ERROR,
                    f"{rec.sector} 驱动量单位 {rec.driver_unit} 与指标要求 "
                    f"{ind.driver_unit} 不一致",
                    subject,
                    {"submitted": rec.driver_unit, "expected": ind.driver_unit}))

        return issues

    # -- 整批（同指标同期次下的分项） ----------------------------------

    def validate_set(self, records: list[SubmissionRecord]) -> list[Issue]:
        issues: list[Issue] = []
        # (指标, 期次) -> 报送
        grouped: dict[tuple[str, int], list[SubmissionRecord]] = {}
        for rec in records:
            issues.extend(self.validate_submission(rec))
            grouped.setdefault((rec.indicator_code, rec.period_year),
                               []).append(rec)

        for (code, year), recs in grouped.items():
            ind = self._indicators.get(code)
            if ind is None:
                continue
            group_subject = f"indicator:{code}@{year}"
            # 覆盖重叠：同期次两个分项声称覆盖同一原子部门
            seen: dict[str, str] = {}
            for rec in recs:
                for atom in rec.coverage:
                    if atom in seen and seen[atom] != rec.sector:
                        issues.append(Issue(
                            "overlap", IssueLevel.ERROR,
                            f"指标 {code}（{year} 年）的 {atom} 同时被 "
                            f"{seen[atom]} 与 {rec.sector} 覆盖，"
                            "直接相加将重复计算",
                            group_subject,
                            {"atom": atom, "period_year": year,
                             "indicator_code": code,
                             "sectors": [seen[atom], rec.sector]}))
                    seen.setdefault(atom, rec.sector)

            # 边界兼容：同期次边界必须相同或属于同一等价组
            keys = set()
            for rec in recs:
                b = self._boundaries.get(rec.boundary_id) if rec.boundary_id else None
                if b is None:
                    continue
                keys.add(b.equivalence_key or f"id:{b.id}@{b.revision}")
            if len(keys) > 1:
                issues.append(Issue(
                    "boundary_mismatch", IssueLevel.ERROR,
                    f"指标 {code}（{year} 年）的分项来自不兼容统计边界 "
                    f"{sorted(keys)}，国家汇总只能由兼容分项推导",
                    group_subject,
                    {"boundary_keys": sorted(keys), "period_year": year,
                     "indicator_code": code}))

            # 方法学不一致：允许，但提示汇总需谨慎
            methods = {r.methodology for r in recs}
            if len(methods) > 1:
                issues.append(Issue(
                    "mixed_methodology", IssueLevel.WARNING,
                    f"指标 {code}（{year} 年）混用测算方法 {sorted(methods)}",
                    group_subject,
                    {"methodologies": sorted(methods), "period_year": year,
                     "indicator_code": code}))

            # 未报告分项：指标声明了应有部门，但该期次没有任何记录
            reported_sectors = {r.sector for r in recs}
            for expected in ind.sectors:
                if expected not in reported_sectors:
                    issues.append(Issue(
                        "not_reported", IssueLevel.INFO,
                        f"指标 {code}（{year} 年）的 {expected} 部门尚未报送"
                        "（区别于报告为零）",
                        group_subject,
                        {"sector": expected, "period_year": year,
                         "indicator_code": code}))

        return issues
