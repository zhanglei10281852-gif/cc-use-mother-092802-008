"""国家汇总：只能由口径兼容的分项推导，未报告不按零处理。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .graph import EvidenceGraph
from .model import IndicatorVersion, QuantityKind
from .validation import Finding, Severity, validate_indicator, validate_set
from .values import ReportedValue


class AggregateStatus(Enum):
    COMPLETE = "complete"  # 全部分项已报告且口径兼容
    PARTIAL = "partial"  # 存在未报告分项，仅给出已报小计
    NOT_REPORTED = "not_reported"  # 全部分项均未报告
    BLOCKED = "blocked"  # 存在口径冲突或重叠，禁止汇总


@dataclass(frozen=True)
class AggregateComponent:
    indicator_id: str
    version: int
    sector: str
    value: ReportedValue


@dataclass(frozen=True)
class Provenance:
    """汇总溯源：精确到版本的来源材料与测算方法。"""

    sources: tuple[tuple[str, int, str], ...]  # (source_id, version, title)
    methods: tuple[tuple[str, int, str], ...]  # (method_id, version, name)


@dataclass(frozen=True)
class AggregateResult:
    status: AggregateStatus
    unit: str | None
    quantity_kind: QuantityKind | None
    boundary_revision: str | None
    baseline_year: int | None
    value: Decimal | None  # 仅 COMPLETE 时有值
    reported_subtotal: Decimal | None  # PARTIAL 时的已报小计（不得冒充全量）
    components: tuple[AggregateComponent, ...]
    missing: tuple[str, ...]  # 未报告的指标 id
    findings: tuple[Finding, ...]
    provenance: Provenance


def aggregate_national(
    indicators: list[IndicatorVersion], graph: EvidenceGraph
) -> AggregateResult:
    """由兼容分项推导国家汇总。

    - 存在 ERROR 级发现（重叠、口径不一致、无来源估算）→ BLOCKED，不出数；
    - 部分分项未报告 → PARTIAL，只给已报小计并列出缺口；
    - 报告为零是有效数值，正常计入。
    """
    findings: list[Finding] = []
    for indicator in indicators:
        findings.extend(validate_indicator(indicator))
    findings.extend(validate_set(indicators))

    components = tuple(
        AggregateComponent(i.indicator_id, i.version, i.sector, i.value)
        for i in indicators
    )
    missing = tuple(sorted(i.indicator_id for i in indicators if not i.value.is_reported))
    provenance = _collect_provenance(indicators, graph)

    if not indicators:
        return AggregateResult(
            status=AggregateStatus.NOT_REPORTED,
            unit=None, quantity_kind=None, boundary_revision=None, baseline_year=None,
            value=None, reported_subtotal=None,
            components=components, missing=missing,
            findings=tuple(findings), provenance=provenance,
        )

    has_error = any(f.severity is Severity.ERROR for f in findings)
    compatible = not any(f.code in {"INCOMPATIBLE_DEFINITION", "INDICATOR_OVERLAP"} for f in findings)
    if compatible:
        unit, kind, boundary, baseline = indicators[0].signature()
        quantity_kind: QuantityKind | None = QuantityKind(kind)
    else:
        unit = boundary = None
        quantity_kind = None
        baseline = None

    if has_error:
        return AggregateResult(
            status=AggregateStatus.BLOCKED,
            unit=unit, quantity_kind=quantity_kind,
            boundary_revision=boundary, baseline_year=baseline,
            value=None, reported_subtotal=None,
            components=components, missing=missing,
            findings=tuple(findings), provenance=provenance,
        )

    reported = [i for i in indicators if i.value.is_reported]
    subtotal = sum((i.value.amount for i in reported), Decimal("0"))

    if not reported:
        status = AggregateStatus.NOT_REPORTED
        return AggregateResult(
            status=status, unit=unit, quantity_kind=quantity_kind,
            boundary_revision=boundary, baseline_year=baseline,
            value=None, reported_subtotal=None,
            components=components, missing=missing,
            findings=tuple(findings), provenance=provenance,
        )

    if missing:
        return AggregateResult(
            status=AggregateStatus.PARTIAL,
            unit=unit, quantity_kind=quantity_kind,
            boundary_revision=boundary, baseline_year=baseline,
            value=None, reported_subtotal=subtotal,
            components=components, missing=missing,
            findings=tuple(findings), provenance=provenance,
        )

    return AggregateResult(
        status=AggregateStatus.COMPLETE,
        unit=unit, quantity_kind=quantity_kind,
        boundary_revision=boundary, baseline_year=baseline,
        value=subtotal, reported_subtotal=subtotal,
        components=components, missing=missing,
        findings=tuple(findings), provenance=provenance,
    )


def _collect_provenance(
    indicators: list[IndicatorVersion], graph: EvidenceGraph
) -> Provenance:
    sources: dict[tuple[str, int], str] = {}
    methods: dict[tuple[str, int], str] = {}
    for indicator in indicators:
        found_sources, found_methods = graph.provenance_of(
            indicator.indicator_id, indicator.version
        )
        for source in found_sources:
            sources[(source.source_id, source.version)] = source.title
        for method in found_methods:
            methods[(method.method_id, method.version)] = method.name
    return Provenance(
        sources=tuple((sid, ver, sources[(sid, ver)]) for sid, ver in sorted(sources)),
        methods=tuple((mid, ver, methods[(mid, ver)]) for mid, ver in sorted(methods)),
    )
