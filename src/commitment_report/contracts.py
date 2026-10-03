"""指标口径、目标与来源材料契约。

约束：
* 报送值显式区分 ``REPORTED``（含报告为零）与 ``NOT_REPORTED``（未报告）；
* 指标定义固化单位、基准年、边界修订与测算方法；
* 所有冻结的数据类均可直接序列化为报告数据包。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class ValueKind(str, Enum):
    """指标取值类型：实物量可加总，强度/占比只能按驱动量加权。"""

    ABSOLUTE = "absolute"
    INTENSITY = "intensity"
    SHARE = "share"


class ReportingStatus(str, Enum):
    """数值报送状态。``None`` 表示未报告，``Decimal('0')`` 表示报告为零。"""

    REPORTED = "reported"
    NOT_REPORTED = "not_reported"


class SubmissionState(str, Enum):
    """报送件评审流转状态。"""

    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    RETURNED = "returned"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    RETURN = "return"


class IssueLevel(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ChapterState(str, Enum):
    OPEN = "open"
    LOCKED = "locked"


class ReportStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class IndicatorDefinition:
    code: str
    unit: str
    baseline_year: int
    boundary_revision: str
    name: str = ""
    value_kind: ValueKind = ValueKind.ABSOLUTE
    boundary_id: str | None = None
    #: 期望覆盖的原子部门标签，用于发现“未报告”的分项
    sectors: tuple[str, ...] = ()
    methodology: str = "default"
    #: 强度/占比指标做加权汇总时所需的驱动量单位（如 PJ、MWh）
    driver_unit: str | None = None


@dataclass(frozen=True)
class SectorSubmission:
    """部门报送载荷（保持与既有报送格式兼容）。"""

    sector: str
    indicator_code: str
    reporting_date: date
    value: Decimal | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Evidence:
    """来源材料。数字必须能回溯到具体材料。"""

    id: str
    title: str
    source: str = ""
    published_on: date | None = None
    uri: str = ""
    sha256: str = ""


@dataclass(frozen=True)
class Boundary:
    """统计边界。不同修订之间只有显式等价组才视为兼容。"""

    id: str
    revision: str
    name: str = ""
    gases: tuple[str, ...] = ("CO2", "CH4", "N2O")
    equivalence_key: str | None = None


@dataclass(frozen=True)
class Target:
    id: str
    country: str
    indicator_code: str
    target_year: int
    value: Decimal
    unit: str
    baseline_year: int
    description: str = ""
    evidence_id: str | None = None


@dataclass(frozen=True)
class ReviewEvent:
    at: datetime
    reviewer: str
    decision: ReviewDecision
    comment: str = ""


@dataclass(frozen=True)
class Issue:
    """校验发现：口径重叠、单位/边界不兼容、缺少来源等。"""

    code: str
    level: IssueLevel
    message: str
    subject: str = ""
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SubmissionRecord:
    """不可变的报送记录；修订会生成新 id 的新记录，旧记录原样保留。"""

    id: str
    indicator_code: str
    sector: str
    period_year: int
    reporting_status: ReportingStatus
    value: Decimal | None
    unit: str
    value_kind: ValueKind
    baseline_year: int
    boundary_id: str | None
    boundary_revision: str
    methodology: str
    coverage: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    estimated: bool
    driver_value: Decimal | None
    driver_unit: str | None
    submitted_by: str
    submitted_at: datetime
    reporting_date: date
    revision_no: int = 1
    supersedes: str | None = None
    state: SubmissionState = SubmissionState.SUBMITTED
    reviews: tuple[ReviewEvent, ...] = ()


@dataclass(frozen=True)
class ChapterEvent:
    at: datetime
    actor: str
    action: str
    comment: str = ""
