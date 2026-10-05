"""测试共享支撑：构造带固定时钟的服务与基础数据。"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from commitment_report.service import CommitmentService  # noqa: E402
from commitment_report.values import ReportedValue  # noqa: E402

FIXED_NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def make_service() -> CommitmentService:
    """能源、交通已报送（实物量），工业未报告。"""
    svc = CommitmentService(clock=lambda: FIXED_NOW)
    svc.register_source("SRC-ENERGY", "能源统计年鉴2025", "能源司", date(2026, 3, 1))
    svc.register_source("SRC-TRANSPORT", "交通运输统计公报", "运输司", date(2026, 4, 1))
    svc.register_method("M-IPCC", "IPCC 2006 清单方法")
    svc.submit_indicator(
        indicator_id="IND-ENERGY",
        sector="energy",
        metric="co2_emission",
        quantity_kind="physical",
        unit="MtCO2",
        baseline_year=2020,
        boundary_revision="B-2020",
        coverage=["coal", "gas"],
        value=ReportedValue.of("100.5"),
        method_id="M-IPCC",
        evidence=["SRC-ENERGY"],
        submitted_by="能源司",
    )
    svc.submit_indicator(
        indicator_id="IND-TRANSPORT",
        sector="transport",
        metric="co2_emission",
        quantity_kind="physical",
        unit="MtCO2",
        baseline_year=2020,
        boundary_revision="B-2020",
        coverage=["road"],
        value=ReportedValue.of("50"),
        method_id="M-IPCC",
        evidence=["SRC-TRANSPORT"],
        submitted_by="运输司",
    )
    svc.submit_indicator(
        indicator_id="IND-INDUSTRY",
        sector="industry",
        metric="co2_emission",
        quantity_kind="physical",
        unit="MtCO2",
        baseline_year=2020,
        boundary_revision="B-2020",
        coverage=["steel", "cement"],
        submitted_by="工业司",
    )  # 未报告
    return svc
