"""报送值三态语义：严格区分“未报告”和“报告为零”。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class ValueKind(Enum):
    REPORTED = "reported"
    NOT_REPORTED = "not_reported"


@dataclass(frozen=True)
class ReportedValue:
    """部门报送值。

    ``REPORTED`` 且 ``amount == 0`` 表示“报告为零”，是有效数据；
    ``NOT_REPORTED`` 表示部门尚未报送，汇总时不得按零处理。
    """

    kind: ValueKind
    amount: Decimal | None = None

    def __post_init__(self) -> None:
        if self.kind is ValueKind.REPORTED and self.amount is None:
            raise ValueError("已报告值必须携带数值")
        if self.kind is ValueKind.NOT_REPORTED and self.amount is not None:
            raise ValueError("未报告值不得携带数值")

    @classmethod
    def of(cls, amount: Decimal | int | str) -> "ReportedValue":
        return cls(ValueKind.REPORTED, Decimal(str(amount)))

    @classmethod
    def not_reported(cls) -> "ReportedValue":
        return cls(ValueKind.NOT_REPORTED, None)

    @property
    def is_reported(self) -> bool:
        return self.kind is ValueKind.REPORTED

    @property
    def is_zero(self) -> bool:
        return self.is_reported and self.amount == 0
