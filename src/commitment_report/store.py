"""版本化证据图与评审工作流。

设计要点
========
* **追加式事件溯源**：所有变更（登记材料/指标、报送、退回、重新提交、
  章节锁定、版本确认）都是不可变事件；当前状态由事件归并得到。
* **修订不可变**：部门修订总是生成新记录（``supersedes`` 指向前序），
  旧记录原样保留，因此晚到的部门修订无法改变已对外确认的旧版本。
* **章节分阶段锁定**：锁定章节拒绝新报送，直到解锁；确认版本后章节
  仍可继续为下一版开放，但已确认快照永不变更。
* **可追溯**：每个汇总值都带 ``contributing_submission_ids``，
  每条报送带 ``evidence_ids``，可一路回溯到具体材料。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from .aggregation import AggregateLine, derive_aggregates
from .contracts import (
    Boundary,
    ChapterEvent,
    ChapterState,
    Evidence,
    IndicatorDefinition,
    Issue,
    ReportStatus,
    ReportingStatus,
    ReviewDecision,
    ReviewEvent,
    SubmissionRecord,
    SubmissionState,
    Target,
)
from .validation import ValidationEngine


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------- 输入校验

class WorkflowError(ValueError):
    """业务规则冲突（如章节已锁定、重复修订等）。"""


# --------------------------------------------------------------------- 报告快照

class ReportSnapshot:
    """某一版本号下不可变的报告数据包。"""

    def __init__(self, version: int, created_at: datetime, created_by: str,
                 status: ReportStatus, aggregates: list[AggregateLine],
                 submissions: list[SubmissionRecord], issues: list[Issue],
                 chapter_states: dict, indicators: dict, targets: list[Target],
                 evidence: dict, boundaries: dict,
                 country: str = "UNSPECIFIED") -> None:
        self.version = version
        self.created_at = created_at
        self.created_by = created_by
        self.status = status
        self.aggregates = aggregates
        self.submissions = submissions
        self.issues = issues
        self.chapter_states = chapter_states
        self.indicators = indicators
        self.targets = targets
        self.evidence = evidence
        self.boundaries = boundaries
        self.country = country


# --------------------------------------------------------------------- 主编排服务

class ReportService:
    def __init__(self, country: str = "UNSPECIFIED") -> None:
        self.country = country
        # 登记表
        self._indicators: dict[str, IndicatorDefinition] = {}
        self._evidence: dict[str, Evidence] = {}
        self._boundaries: dict[str, Boundary] = {}
        self._targets: dict[str, Target] = {}
        # 报送
        self._submissions: dict[str, SubmissionRecord] = {}
        self._latest_active: dict[tuple[str, str, int], str] = {}
        # 章节（按指标码划分章节）
        self._chapters: dict[str, ChapterState] = {}
        self._chapter_events: dict[str, list[ChapterEvent]] = {}
        # 版本
        self._snapshots: dict[int, ReportSnapshot] = {}
        self._next_version = 1
        self._event_log: list[dict] = []

    # ================================================================= 登记

    def register_evidence(self, evidence: Evidence) -> Evidence:
        if evidence.id in self._evidence:
            raise WorkflowError(f"材料 {evidence.id} 已存在")
        self._evidence[evidence.id] = evidence
        self._log("evidence_registered", {"evidence_id": evidence.id})
        return evidence

    def register_boundary(self, boundary: Boundary) -> Boundary:
        if boundary.id in self._boundaries:
            raise WorkflowError(f"边界 {boundary.id} 已存在")
        self._boundaries[boundary.id] = boundary
        self._log("boundary_registered",
                  {"boundary_id": boundary.id, "revision": boundary.revision})
        return boundary

    def register_indicator(self, ind: IndicatorDefinition) -> IndicatorDefinition:
        if ind.code in self._indicators:
            raise WorkflowError(f"指标 {ind.code} 已存在")
        self._indicators[ind.code] = ind
        self._chapters.setdefault(ind.code, ChapterState.OPEN)
        self._chapter_events.setdefault(ind.code, [])
        self._log("indicator_registered", {"indicator_code": ind.code})
        return ind

    def register_target(self, target: Target) -> Target:
        if target.indicator_code not in self._indicators:
            raise WorkflowError(f"目标引用了未登记指标 {target.indicator_code}")
        if target.evidence_id and target.evidence_id not in self._evidence:
            raise WorkflowError(f"目标引用了未登记材料 {target.evidence_id}")
        self._targets[target.id] = target
        self._log("target_registered", {"target_id": target.id})
        return target

    # ================================================================= 章节锁定

    def lock_chapter(self, indicator_code: str, actor: str,
                     comment: str = "") -> None:
        self._require_indicator(indicator_code)
        if self._chapters[indicator_code] == ChapterState.LOCKED:
            raise WorkflowError(f"章节 {indicator_code} 已处于锁定状态")
        self._chapters[indicator_code] = ChapterState.LOCKED
        self._chapter_events[indicator_code].append(
            ChapterEvent(_now(), actor, "lock", comment))
        self._log("chapter_locked",
                  {"indicator_code": indicator_code, "actor": actor})

    def unlock_chapter(self, indicator_code: str, actor: str,
                       comment: str = "") -> None:
        self._require_indicator(indicator_code)
        if self._chapters[indicator_code] == ChapterState.OPEN:
            raise WorkflowError(f"章节 {indicator_code} 已处于开放状态")
        self._chapters[indicator_code] = ChapterState.OPEN
        self._chapter_events[indicator_code].append(
            ChapterEvent(_now(), actor, "unlock", comment))
        self._log("chapter_unlocked",
                  {"indicator_code": indicator_code, "actor": actor})

    def chapter_events(self, indicator_code: str) -> list[ChapterEvent]:
        self._require_indicator(indicator_code)
        return list(self._chapter_events[indicator_code])

    # ================================================================= 报送 / 修订

    def submit(self, indicator_code: str, sector: str, period_year: int,
               value, *, unit: str | None = None, coverage: tuple[str, ...] = (),
               evidence_ids: tuple[str, ...] = (), estimated: bool = False,
               driver_value=None, submitted_by: str = "unknown",
               supersedes: str | None = None, comment: str = "",
               boundary_id: str | None = None,
               boundary_revision: str | None = None,
               methodology: str | None = None) -> SubmissionRecord:
        """提交或重新提交一个部门指标。

        ``value=None`` 表示 **未报告**；``Decimal('0')`` 表示 **报告为零**。
        ``supersedes`` 用于被退回后的重新提交或部门修订。
        ``boundary_id``/``methodology`` 允许政策调整后的修订声明新口径，
        校验引擎会判定它与其他分项是否兼容。
        """
        ind = self._require_indicator(indicator_code)
        if self._chapters[indicator_code] == ChapterState.LOCKED:
            raise WorkflowError(
                f"章节 {indicator_code} 已锁定，拒收 {sector} 的报送；"
                "如需修订请先解锁或在新版本中处理")
        if not coverage:
            coverage = (sector,)

        eff_boundary_id = boundary_id or ind.boundary_id
        if eff_boundary_id and eff_boundary_id not in self._boundaries:
            raise WorkflowError(f"引用了未登记边界 {eff_boundary_id}")
        eff_boundary_revision = (
            boundary_revision
            if boundary_revision is not None
            else (self._boundaries[eff_boundary_id].revision
                  if eff_boundary_id in self._boundaries
                  else ind.boundary_revision))
        eff_methodology = methodology or ind.methodology

        # 重新提交必须指向真实前序，且前序确属同一指标/部门/期次
        if supersedes:
            prior = self._submissions.get(supersedes)
            if prior is None:
                raise WorkflowError(f"前序报送 {supersedes} 不存在")
            if (prior.indicator_code, prior.sector, prior.period_year) != (
                    indicator_code, sector, period_year):
                raise WorkflowError("重新提交必须与前序报送的指标、部门、期次一致")
            if prior.state != SubmissionState.RETURNED:
                raise WorkflowError(
                    f"前序 {supersedes} 状态为 {prior.state.value}，"
                    "只有被退回的报送才能重新提交；修订已接受内容请先退回")
            revision_no = prior.revision_no + 1
        else:
            active = self._latest(indicator_code, sector, period_year)
            if active is not None:
                if active.state == SubmissionState.RETURNED:
                    raise WorkflowError(
                        f"{sector} 的 {indicator_code}/{period_year} 报送 "
                        f"{active.id} 已被退回，须以 supersedes 指向它重新提交，"
                        "不得另起新件")
                raise WorkflowError(
                    f"{sector} 的 {indicator_code}/{period_year} 已有在途或已接受"
                    f"报送 {active.id}，修订需声明 supersedes 并先退回")
            revision_no = 1

        status = (ReportingStatus.NOT_REPORTED if value is None
                  else ReportingStatus.REPORTED)
        rec = SubmissionRecord(
            id=_uid("sub"),
            indicator_code=indicator_code,
            sector=sector,
            period_year=period_year,
            reporting_status=status,
            value=None if value is None else Decimal(str(value)),
            unit=unit or ind.unit,
            value_kind=ind.value_kind,
            baseline_year=ind.baseline_year,
            boundary_id=eff_boundary_id,
            boundary_revision=eff_boundary_revision,
            methodology=eff_methodology,
            coverage=tuple(coverage),
            evidence_ids=tuple(evidence_ids),
            estimated=estimated,
            driver_value=(None if driver_value is None
                          else Decimal(str(driver_value))),
            driver_unit=ind.driver_unit,
            submitted_by=submitted_by,
            submitted_at=_now(),
            reporting_date=_now().date(),
            revision_no=revision_no,
            supersedes=supersedes,
            state=SubmissionState.SUBMITTED,
            reviews=((ReviewEvent(_now(), submitted_by, ReviewDecision.APPROVE,
                                  comment),) if comment else ()))
        self._submissions[rec.id] = rec
        self._latest_active[(indicator_code, sector, period_year)] = rec.id
        self._log("submission_received",
                  {"submission_id": rec.id, "indicator_code": indicator_code,
                   "sector": sector, "revision_no": revision_no,
                   "supersedes": supersedes,
                   "status": status.value})
        return rec

    def review(self, submission_id: str, reviewer: str,
               decision: ReviewDecision, comment: str = "") -> SubmissionRecord:
        """记录审阅意见；退回后该报送可被重新提交。"""
        rec = self._submissions.get(submission_id)
        if rec is None:
            raise WorkflowError(f"报送 {submission_id} 不存在")
        event = ReviewEvent(_now(), reviewer, decision, comment)
        if decision == ReviewDecision.RETURN:
            new_state = SubmissionState.RETURNED
        else:
            if rec.state == SubmissionState.ACCEPTED:
                raise WorkflowError(f"报送 {submission_id} 已接受，不能重复批准")
            new_state = SubmissionState.ACCEPTED
        updated = replace(rec, state=new_state, reviews=rec.reviews + (event,))
        self._submissions[submission_id] = updated
        self._log("review", {"submission_id": submission_id,
                             "decision": decision.value, "reviewer": reviewer})
        return updated

    # ================================================================= 校验/汇总

    def _validator(self) -> ValidationEngine:
        return ValidationEngine(self._indicators, self._evidence, self._boundaries)

    def current_issues(self) -> list[Issue]:
        """对现行修订（待审或已接受，不含已被取代/已退回件）做全量校验。"""
        return self._validator().validate_set(self._current_revisions())

    def current_aggregates(self) -> list[AggregateLine]:
        return derive_aggregates(
            self._indicators, self._active_records(),
            self._validator(), self._boundaries)

    # ================================================================= 版本

    def confirm_version(self, created_by: str,
                        comment: str = "") -> ReportSnapshot:
        """冻结当前状态为一个对外确认版本；之后的修订不影响该快照。"""
        version = self._next_version
        self._next_version += 1
        snap = self._snapshot(version, ReportStatus.CONFIRMED, created_by)
        self._snapshots[version] = snap
        self._log("version_confirmed",
                  {"version": version, "actor": created_by, "comment": comment})
        return snap

    def export_version(self, version: int) -> dict:
        """导出指定版本的完整报告数据包（JSON 友好）。"""
        return _snapshot_to_dict(self._snapshots[version])

    def diff_versions(self, base: int, head: int) -> dict:
        """两个已确认版本之间的差异摘要（按 指标+期次 比较）。"""
        a = self._snapshots[base]
        b = self._snapshots[head]
        a_agg = {(x.indicator_code, x.period_year): x for x in a.aggregates}
        b_agg = {(x.indicator_code, x.period_year): x for x in b.aggregates}
        changed, added, removed = [], [], []
        for key in sorted(set(a_agg) | set(b_agg)):
            if key not in a_agg:
                added.append({"indicator_code": key[0], "period_year": key[1]})
            elif key not in b_agg:
                removed.append({"indicator_code": key[0], "period_year": key[1]})
            else:
                x, y = a_agg[key], b_agg[key]
                if (x.value, x.derived, x.method, x.component_count,
                        x.not_reported_sectors) != (
                        y.value, y.derived, y.method, y.component_count,
                        y.not_reported_sectors):
                    changed.append({
                        "indicator_code": key[0], "period_year": key[1],
                        "base_value": _num(x.value),
                        "head_value": _num(y.value),
                        "base_derived": x.derived,
                        "head_derived": y.derived,
                        "method_base": x.method,
                        "method_head": y.method,
                        "base_component_ids": list(x.contributing_submission_ids),
                        "head_component_ids": list(y.contributing_submission_ids),
                        "newly_reported_sectors":
                            sorted(set(x.not_reported_sectors)
                                   - set(y.not_reported_sectors)),
                        "newly_missing_sectors":
                            sorted(set(y.not_reported_sectors)
                                   - set(x.not_reported_sectors)),
                    })
        return {
            "country": self.country,
            "base_version": base,
            "head_version": head,
            "base_confirmed_at": a.created_at.isoformat(),
            "head_confirmed_at": b.created_at.isoformat(),
            "indicators_added": added,
            "indicators_removed": removed,
            "indicators_changed": changed,
            "submissions_added": sorted(
                {s.id for s in b.submissions} - {s.id for s in a.submissions}),
            "submissions_removed": sorted(
                {s.id for s in a.submissions} - {s.id for s in b.submissions}),
        }

    def export_current(self) -> dict:
        """导出尚未确认的当前工作态数据包（草稿）。"""
        return _snapshot_to_dict(
            self._snapshot(0, ReportStatus.DRAFT, "system"))

    def event_log(self) -> list[dict]:
        return list(self._event_log)

    # ================================================================= 内部

    def _require_indicator(self, code: str) -> IndicatorDefinition:
        ind = self._indicators.get(code)
        if ind is None:
            raise WorkflowError(f"未登记指标 {code}")
        return ind

    def _latest(self, indicator_code, sector, period_year):
        sid = self._latest_active.get((indicator_code, sector, period_year))
        return self._submissions.get(sid) if sid else None

    def _active_records(self) -> list[SubmissionRecord]:
        """现行有效报送：每个 指标/部门/期次 的最新修订（已接受才参与汇总）。"""
        out = []
        for sid in self._latest_active.values():
            rec = self._submissions[sid]
            # 只有通过审阅（接受）的分项才能进入国家汇总
            if rec.state == SubmissionState.ACCEPTED:
                out.append(rec)
        return out

    def _current_revisions(self) -> list[SubmissionRecord]:
        """每个 指标/部门/期次 的最新修订（含待审件，供校验与审阅）。"""
        return [self._submissions[sid] for sid in self._latest_active.values()]

    def _snapshot(self, version: int, status: ReportStatus,
                  created_by: str) -> ReportSnapshot:
        # 快照固化当时的全部报送与登记表，供导出/差异长期使用
        return ReportSnapshot(
            version=version,
            created_at=_now(),
            created_by=created_by,
            status=status,
            aggregates=self.current_aggregates(),
            submissions=list(self._submissions.values()),
            issues=self.current_issues(),
            chapter_states=dict(self._chapters),
            indicators=dict(self._indicators),
            targets=list(self._targets.values()),
            evidence=dict(self._evidence),
            boundaries=dict(self._boundaries),
            country=self.country)

    def _log(self, kind: str, payload: dict) -> None:
        self._event_log.append({"at": _now().isoformat(),
                                "kind": kind, **payload})


# --------------------------------------------------------------------- 序列化

def _num(v):
    return None if v is None else str(v)


def _snapshot_to_dict(snap: ReportSnapshot) -> dict:
    return {
        "country": snap.country,
        "version": snap.version,
        "status": snap.status.value,
        "confirmed_at": snap.created_at.isoformat(),
        "confirmed_by": snap.created_by,
        "indicators": [
            {"code": i.code, "name": i.name, "unit": i.unit,
             "value_kind": i.value_kind.value, "baseline_year": i.baseline_year,
             "boundary_id": i.boundary_id,
             "boundary_revision": i.boundary_revision,
             "methodology": i.methodology, "driver_unit": i.driver_unit,
             "sectors": list(i.sectors)}
            for i in snap.indicators.values()],
        "targets": [
            {"id": t.id, "country": t.country, "indicator_code": t.indicator_code,
             "target_year": t.target_year, "value": str(t.value),
             "unit": t.unit, "baseline_year": t.baseline_year,
             "description": t.description, "evidence_id": t.evidence_id}
            for t in snap.targets],
        "submissions": [_submission_dict(s) for s in snap.submissions],
        "aggregates": [
            {"indicator_code": a.indicator_code, "period_year": a.period_year,
             "derived": a.derived,
             "value": _num(a.value), "unit": a.unit, "method": a.method,
             "component_count": a.component_count,
             "contributing_submission_ids": list(a.contributing_submission_ids),
             "zero_reported_sectors": list(a.zero_reported_sectors),
             "not_reported_sectors": list(a.not_reported_sectors),
             "blocking_issues": [_issue_dict(i) for i in a.blocking_issues],
             "notes": list(a.notes)}
            for a in snap.aggregates],
        "issues": [_issue_dict(i) for i in snap.issues],
        "chapters": [
            {"indicator_code": k, "state": v.value}
            for k, v in sorted(snap.chapter_states.items())],
        "evidence": [
            {"id": e.id, "title": e.title, "source": e.source,
             "published_on": e.published_on.isoformat() if e.published_on else None,
             "uri": e.uri, "sha256": e.sha256}
            for e in snap.evidence.values()],
        "boundaries": [
            {"id": b.id, "revision": b.revision, "name": b.name,
             "gases": list(b.gases),
             "equivalence_key": b.equivalence_key}
            for b in snap.boundaries.values()],
    }


def _submission_dict(s: SubmissionRecord) -> dict:
    return {
        "id": s.id, "indicator_code": s.indicator_code, "sector": s.sector,
        "period_year": s.period_year,
        "reporting_status": s.reporting_status.value,
        "value": _num(s.value), "unit": s.unit,
        "value_kind": s.value_kind.value, "baseline_year": s.baseline_year,
        "boundary_id": s.boundary_id, "boundary_revision": s.boundary_revision,
        "methodology": s.methodology, "coverage": list(s.coverage),
        "evidence_ids": list(s.evidence_ids), "estimated": s.estimated,
        "driver_value": _num(s.driver_value), "driver_unit": s.driver_unit,
        "submitted_by": s.submitted_by,
        "submitted_at": s.submitted_at.isoformat(),
        "reporting_date": s.reporting_date.isoformat(),
        "revision_no": s.revision_no, "supersedes": s.supersedes,
        "state": s.state.value,
        "reviews": [{"at": r.at.isoformat(), "reviewer": r.reviewer,
                     "decision": r.decision.value, "comment": r.comment}
                    for r in s.reviews],
    }


def _issue_dict(i: Issue) -> dict:
    return {"code": i.code, "level": i.level.value, "message": i.message,
            "subject": i.subject, "context": i.context}
