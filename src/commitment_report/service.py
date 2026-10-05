"""应用服务门面：登记、校验、汇总、章节流转与报告版本。"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from decimal import Decimal

from .aggregation import AggregateResult, aggregate_national
from .errors import ConflictError, NotFoundError
from .graph import EvidenceGraph
from .model import (
    IndicatorVersion,
    Methodology,
    QuantityKind,
    SourceMaterial,
    Target,
)
from .reports import (
    ReportRegistry,
    ReportSnapshot,
    diff_snapshots,
    export_package,
)
from .validation import Finding, validate_indicator, validate_set
from .values import ReportedValue
from .workflow import Chapter, ChapterStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CommitmentService:
    def __init__(self, clock=_utcnow) -> None:
        self.graph = EvidenceGraph()
        self.chapters = ChapterStore()
        self._reports = ReportRegistry()
        self._report_scopes: dict[str, dict] = {}
        self._clock = clock
        self._lock = threading.RLock()

    # -- 来源材料与测算方法 ---------------------------------------------------

    def register_source(
        self,
        source_id: str,
        title: str,
        publisher: str,
        published_on: date,
        uri: str | None = None,
    ) -> SourceMaterial:
        with self._lock:
            version = self._next_version("source", source_id)
            node = SourceMaterial(source_id, version, title, publisher, published_on, uri)
            self.graph.add_source(node)
            return node

    def register_method(
        self, method_id: str, name: str, description: str = ""
    ) -> Methodology:
        with self._lock:
            version = self._next_version("method", method_id)
            node = Methodology(method_id, version, name, description)
            self.graph.add_method(node)
            return node

    def _next_version(self, kind: str, entity_id: str) -> int:
        try:
            return self.graph.latest_version(kind, entity_id) + 1
        except NotFoundError:
            return 1

    # -- 指标 -----------------------------------------------------------------

    def _resolve_evidence(self, evidence: list[str]) -> tuple[tuple[str, int], ...]:
        """把 ``["SRC-1", "SRC-2@3"]`` 形式的引用固定到具体版本。"""
        resolved: list[tuple[str, int]] = []
        for ref in evidence:
            if "@" in ref:
                source_id, _, ver = ref.partition("@")
                version = int(ver)
            else:
                source_id, version = ref, self.graph.latest_version("source", ref)
            resolved.append((source_id, version))
        return tuple(resolved)

    def submit_indicator(
        self,
        indicator_id: str,
        sector: str,
        metric: str,
        quantity_kind: str,
        unit: str,
        baseline_year: int,
        boundary_revision: str,
        coverage: list[str] | tuple[str, ...] = (),
        value: ReportedValue | None = None,
        is_estimate: bool = False,
        method_id: str | None = None,
        evidence: list[str] | tuple[str, ...] = (),
        submitted_by: str = "",
        note: str = "",
    ) -> IndicatorVersion:
        """登记指标首个版本；value 缺省表示“未报告”。"""
        with self._lock:
            node = IndicatorVersion(
                indicator_id=indicator_id,
                version=1,
                sector=sector,
                metric=metric,
                quantity_kind=QuantityKind(quantity_kind),
                unit=unit,
                baseline_year=baseline_year,
                boundary_revision=boundary_revision,
                coverage=frozenset(coverage),
                value=value if value is not None else ReportedValue.not_reported(),
                is_estimate=is_estimate,
                method_id=method_id,
                evidence=self._resolve_evidence(list(evidence)),
                submitted_by=submitted_by,
                submitted_at=self._clock(),
                note=note,
            )
            self.graph.add_indicator(node)
            return node

    def revise_indicator(self, indicator_id: str, **changes) -> IndicatorVersion:
        """部门修订：以前一版本为底稿追加新版本，旧版本保持不变。

        可改字段：value / is_estimate / evidence / unit / boundary_revision /
        baseline_year / coverage / method_id / note。未给出的字段继承上一版本。
        """
        with self._lock:
            prev: IndicatorVersion = self.graph.get("indicator", indicator_id)
            evidence = changes.pop("evidence", None)
            node = IndicatorVersion(
                indicator_id=indicator_id,
                version=prev.version + 1,
                sector=prev.sector,
                metric=prev.metric,
                quantity_kind=prev.quantity_kind,
                unit=changes.pop("unit", prev.unit),
                baseline_year=changes.pop("baseline_year", prev.baseline_year),
                boundary_revision=changes.pop(
                    "boundary_revision", prev.boundary_revision
                ),
                coverage=(
                    frozenset(changes.pop("coverage"))
                    if "coverage" in changes
                    else prev.coverage
                ),
                value=changes.pop("value", prev.value),
                is_estimate=changes.pop("is_estimate", prev.is_estimate),
                method_id=changes.pop("method_id", prev.method_id),
                evidence=(
                    self._resolve_evidence(list(evidence))
                    if evidence is not None
                    else prev.evidence
                ),
                submitted_by=changes.pop("submitted_by", prev.submitted_by),
                submitted_at=self._clock(),
                note=changes.pop("note", prev.note),
            )
            if changes:
                raise ValueError(f"未知修订字段: {sorted(changes)}")
            self.graph.add_indicator(node)
            return node

    def get_indicator(self, indicator_id: str, version: int | None = None) -> IndicatorVersion:
        return self.graph.get("indicator", indicator_id, version)

    # -- 目标 -----------------------------------------------------------------

    def define_target(
        self,
        target_id: str,
        title: str,
        metric: str,
        quantity_kind: str,
        unit: str,
        baseline_year: int,
        boundary_revision: str,
        target_year: int,
        target_value: Decimal | str | int,
        indicator_ids: list[str] | tuple[str, ...] = (),
    ) -> Target:
        with self._lock:
            node = Target(
                target_id=target_id,
                version=self._next_version("target", target_id),
                title=title,
                metric=metric,
                quantity_kind=QuantityKind(quantity_kind),
                unit=unit,
                baseline_year=baseline_year,
                boundary_revision=boundary_revision,
                target_year=target_year,
                target_value=Decimal(str(target_value)),
                indicator_ids=tuple(indicator_ids),
            )
            self.graph.add_target(node)
            return node

    # -- 章节流转 -------------------------------------------------------------

    def create_chapter(
        self, chapter_id: str, title: str, stage: str, indicator_ids=()
    ) -> Chapter:
        return self.chapters.create(chapter_id, title, stage, tuple(indicator_ids))

    def submit_chapter(self, chapter_id: str) -> Chapter:
        return self.chapters.submit(chapter_id)

    def start_review(self, chapter_id: str) -> Chapter:
        return self.chapters.start_review(chapter_id)

    def add_review_comment(self, chapter_id: str, author: str, body: str):
        return self.chapters.add_comment(chapter_id, author, body, self._clock())

    def return_chapter(self, chapter_id: str) -> Chapter:
        return self.chapters.return_chapter(chapter_id)

    def lock_chapter(self, chapter_id: str) -> Chapter:
        return self.chapters.lock(chapter_id)

    def lock_stage(self, stage: str) -> list[Chapter]:
        return self.chapters.lock_stage(stage)

    # -- 校验与汇总 -------------------------------------------------------------

    def _latest_indicators(self, indicator_ids: list[str] | None) -> list[IndicatorVersion]:
        if indicator_ids is None:
            return self.graph.iter_latest("indicator")
        return [self.graph.get("indicator", iid) for iid in indicator_ids]

    def validate(self, indicator_ids: list[str] | None = None) -> list[Finding]:
        indicators = self._latest_indicators(indicator_ids)
        findings: list[Finding] = []
        for indicator in indicators:
            findings.extend(validate_indicator(indicator))
        findings.extend(validate_set(indicators))
        return findings

    def preview_aggregate(self, indicator_ids: list[str] | None = None) -> AggregateResult:
        return aggregate_national(self._latest_indicators(indicator_ids), self.graph)

    # -- 报告版本 ---------------------------------------------------------------

    def create_report(
        self,
        report_id: str,
        title: str,
        indicator_ids: list[str] | None = None,
        chapter_ids: list[str] | None = None,
    ) -> dict:
        with self._lock:
            if report_id in self._report_scopes:
                raise ConflictError(f"报告已存在: {report_id}")
            self._report_scopes[report_id] = {
                "title": title,
                "indicator_ids": list(indicator_ids)
                if indicator_ids is not None
                else self.graph.entity_ids("indicator"),
                "chapter_ids": list(chapter_ids)
                if chapter_ids is not None
                else [c.chapter_id for c in self.chapters.all()],
            }
            return {"report_id": report_id, "title": title, "versions": []}

    def confirm_report(self, report_id: str) -> ReportSnapshot:
        """对外确认：冻结当前口径与数据为不可变版本。"""
        with self._lock:
            scope = self._report_scopes.get(report_id)
            if scope is None:
                raise NotFoundError(f"报告不存在: {report_id}")
            indicators = tuple(self._latest_indicators(scope["indicator_ids"]))
            aggregate = aggregate_national(list(indicators), self.graph)

            sources: dict[tuple[str, int], SourceMaterial] = {}
            methods: dict[tuple[str, int], Methodology] = {}
            for indicator in indicators:
                found_sources, found_methods = self.graph.provenance_of(
                    indicator.indicator_id, indicator.version
                )
                for source in found_sources:
                    sources[(source.source_id, source.version)] = source
                for method in found_methods:
                    methods[(method.method_id, method.version)] = method

            scope_indicators = set(scope["indicator_ids"])
            targets = tuple(
                t
                for t in self.graph.iter_latest("target")
                if set(t.indicator_ids) & scope_indicators
            )
            chapters = tuple(
                self.chapters.get(cid) for cid in scope["chapter_ids"]
            )
            snapshot = ReportSnapshot(
                report_id=report_id,
                version=len(self._reports.versions(report_id)) + 1,
                confirmed_at=self._clock(),
                indicators=indicators,
                targets=targets,
                sources=tuple(sources[k] for k in sorted(sources)),
                methods=tuple(methods[k] for k in sorted(methods)),
                chapters=chapters,
                aggregate=aggregate,
                findings=aggregate.findings,
            )
            self._reports.add(snapshot)
            return snapshot

    def export_report(self, report_id: str, version: int) -> dict:
        return export_package(self._reports.get(report_id, version))

    def diff_reports(self, report_id: str, from_version: int, to_version: int) -> dict:
        old = self._reports.get(report_id, from_version)
        new = self._reports.get(report_id, to_version)
        return diff_snapshots(old, new)

    def list_report_versions(self, report_id: str) -> list[int]:
        return self._reports.versions(report_id)
