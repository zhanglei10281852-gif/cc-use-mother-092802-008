"""口径校验：重叠、单位/边界/基准年兼容性、无来源估算。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .model import IndicatorVersion


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    subjects: tuple[str, ...] = ()


def _label(indicator: IndicatorVersion) -> str:
    return f"{indicator.indicator_id}@v{indicator.version}"


def validate_indicator(indicator: IndicatorVersion) -> list[Finding]:
    """单指标校验：估算值必须注明来源材料。"""
    findings: list[Finding] = []
    if indicator.is_estimate and not indicator.evidence:
        findings.append(
            Finding(
                code="ESTIMATE_WITHOUT_SOURCE",
                severity=Severity.ERROR,
                message=f"指标 {_label(indicator)} 为估算值但未提供来源材料",
                subjects=(_label(indicator),),
            )
        )
    return findings


def validate_set(indicators: list[IndicatorVersion]) -> list[Finding]:
    """集合校验：指标重叠与口径兼容性（单位、量纲、边界、基准年）。"""
    findings: list[Finding] = []
    findings.extend(_check_overlaps(indicators))
    findings.extend(_check_compatibility(indicators))
    return findings


def _check_overlaps(indicators: list[IndicatorVersion]) -> list[Finding]:
    """同一指标口径下覆盖范围相交的两个已报分项会被重复计入。"""
    findings: list[Finding] = []
    reported = [i for i in indicators if i.value.is_reported]
    for pos, left in enumerate(reported):
        for right in reported[pos + 1 :]:
            if left.indicator_id == right.indicator_id:
                continue
            if left.metric != right.metric:
                continue
            shared = sorted(left.coverage & right.coverage)
            if shared:
                findings.append(
                    Finding(
                        code="INDICATOR_OVERLAP",
                        severity=Severity.ERROR,
                        message=(
                            f"指标 {_label(left)} 与 {_label(right)} 在子行业 "
                            f"{shared} 上重叠，直接相加会重复计算"
                        ),
                        subjects=(_label(left), _label(right)),
                    )
                )
    return findings


def _check_compatibility(indicators: list[IndicatorVersion]) -> list[Finding]:
    """与多数派口径签名不一致的分项不得进入同一汇总。"""
    findings: list[Finding] = []
    if len(indicators) < 2:
        return findings
    signatures = Counter(i.signature() for i in indicators)
    reference, _ = signatures.most_common(1)[0]
    ref_unit, ref_kind, ref_boundary, ref_baseline = reference
    for indicator in indicators:
        unit, kind, boundary, baseline = indicator.signature()
        problems: list[str] = []
        if unit != ref_unit:
            problems.append(f"单位 {unit} ≠ {ref_unit}")
        if kind != ref_kind:
            problems.append(f"量纲 {kind} ≠ {ref_kind}（实物量与强度不可相加）")
        if boundary != ref_boundary:
            problems.append(f"统计边界 {boundary} ≠ {ref_boundary}")
        if baseline != ref_baseline:
            problems.append(f"基准年 {baseline} ≠ {ref_baseline}")
        if problems:
            findings.append(
                Finding(
                    code="INCOMPATIBLE_DEFINITION",
                    severity=Severity.ERROR,
                    message=(
                        f"指标 {_label(indicator)} 口径与汇总基准不一致："
                        + "；".join(problems)
                    ),
                    subjects=(_label(indicator),),
                )
            )
    return findings
