"""指标口径、目标与来源材料契约。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class IndicatorDefinition:
    code: str
    unit: str
    baseline_year: int
    boundary_revision: str


@dataclass(frozen=True)
class SectorSubmission:
    sector: str
    indicator_code: str
    reporting_date: date
    value: Decimal | None
    evidence_ids: tuple[str, ...]
