"""证据图节点：目标、指标、来源材料与测算方法。

所有节点都是不可变版本：部门修订时追加新版本，旧版本保持不变，
已确认的报告版本因此不会被晚到的修订污染。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from .values import ReportedValue


class QuantityKind(Enum):
    PHYSICAL = "physical"  # 实物量
    INTENSITY = "intensity"  # 强度指标


@dataclass(frozen=True)
class SourceMaterial:
    """来源材料（统计公报、部门台账、测算底稿等）。"""

    source_id: str
    version: int
    title: str
    publisher: str
    published_on: date
    uri: str | None = None


@dataclass(frozen=True)
class Methodology:
    """测算方法。"""

    method_id: str
    version: int
    name: str
    description: str = ""


@dataclass(frozen=True)
class IndicatorVersion:
    """分部门指标的一个版本。

    ``boundary_revision`` 标识统计边界口径（随政策调整而变化）；
    ``coverage`` 为覆盖的子行业集合，用于重叠检测；
    ``evidence`` 为已固定到具体版本的来源材料引用 ``(source_id, version)``。
    """

    indicator_id: str
    version: int
    sector: str
    metric: str
    quantity_kind: QuantityKind
    unit: str
    baseline_year: int
    boundary_revision: str
    coverage: frozenset[str]
    value: ReportedValue
    is_estimate: bool
    method_id: str | None
    evidence: tuple[tuple[str, int], ...]
    submitted_by: str
    submitted_at: datetime
    note: str = ""

    def signature(self) -> tuple[str, str, str, int]:
        """可比性签名：单位、量纲、边界口径、基准年完全一致才可相加。"""
        return (self.unit, self.quantity_kind.value, self.boundary_revision, self.baseline_year)


@dataclass(frozen=True)
class Target:
    """国家自主贡献目标。"""

    target_id: str
    version: int
    title: str
    metric: str
    quantity_kind: QuantityKind
    unit: str
    baseline_year: int
    boundary_revision: str
    target_year: int
    target_value: Decimal
    indicator_ids: tuple[str, ...]
