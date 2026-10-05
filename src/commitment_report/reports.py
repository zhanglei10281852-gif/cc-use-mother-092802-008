"""报告版本：确认即冻结的不可变快照、数据包导出与版本差异摘要。

确认（confirm）时把当前证据图中相关节点的**具体版本**复制进快照，
之后部门再提交修订只会产生新的指标版本，已确认快照不受任何影响。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .aggregation import AggregateResult
from .errors import NotFoundError
from .model import IndicatorVersion, Methodology, SourceMaterial, Target
from .serialize import to_jsonable
from .validation import Finding
from .workflow import Chapter


@dataclass(frozen=True)
class ReportSnapshot:
    """一次对外确认所冻结的全部内容。"""

    report_id: str
    version: int
    confirmed_at: datetime
    indicators: tuple[IndicatorVersion, ...]
    targets: tuple[Target, ...]
    sources: tuple[SourceMaterial, ...]
    methods: tuple[Methodology, ...]
    chapters: tuple[Chapter, ...]
    aggregate: AggregateResult | None
    findings: tuple[Finding, ...]


def export_package(snapshot: ReportSnapshot) -> dict:
    """导出指定版本的数据包（含清单与内容摘要哈希）。"""
    body = {
        "report_id": snapshot.report_id,
        "version": snapshot.version,
        "confirmed_at": to_jsonable(snapshot.confirmed_at),
        "targets": to_jsonable(snapshot.targets),
        "indicators": to_jsonable(snapshot.indicators),
        "chapters": to_jsonable(snapshot.chapters),
        "national_aggregate": to_jsonable(snapshot.aggregate),
        "validation": {"findings": to_jsonable(snapshot.findings)},
        "sources": to_jsonable(snapshot.sources),
        "methods": to_jsonable(snapshot.methods),
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "manifest": {
            "report_id": snapshot.report_id,
            "version": snapshot.version,
            "confirmed_at": body["confirmed_at"],
            "indicator_count": len(snapshot.indicators),
            "source_count": len(snapshot.sources),
            "finding_count": len(snapshot.findings),
            "content_sha256": digest,
        },
        **body,
    }


def _indicator_change(old: IndicatorVersion, new: IndicatorVersion) -> dict:
    changes: dict[str, dict] = {"version": {"old": old.version, "new": new.version}}
    for field, label in (
        ("unit", "unit"),
        ("boundary_revision", "boundary_revision"),
        ("baseline_year", "baseline_year"),
        ("quantity_kind", "quantity_kind"),
    ):
        old_val = to_jsonable(getattr(old, field))
        new_val = to_jsonable(getattr(new, field))
        if old_val != new_val:
            changes[label] = {"old": old_val, "new": new_val}
    old_amount = old.value.amount if old.value.is_reported else None
    new_amount = new.value.amount if new.value.is_reported else None
    if old.value.kind != new.value.kind or old_amount != new_amount:
        changes["value"] = {
            "old": to_jsonable(old.value),
            "new": to_jsonable(new.value),
        }
    if old.evidence != new.evidence:
        changes["evidence"] = {
            "old": to_jsonable(old.evidence),
            "new": to_jsonable(new.evidence),
        }
    return changes


def diff_snapshots(old: ReportSnapshot, new: ReportSnapshot) -> dict:
    """两个已确认版本之间的差异摘要。"""
    old_ind = {i.indicator_id: i for i in old.indicators}
    new_ind = {i.indicator_id: i for i in new.indicators}
    added = sorted(set(new_ind) - set(old_ind))
    removed = sorted(set(old_ind) - set(new_ind))
    changed = {
        iid: _indicator_change(old_ind[iid], new_ind[iid])
        for iid in sorted(set(old_ind) & set(new_ind))
        if old_ind[iid] != new_ind[iid]
    }

    old_ch = {c.chapter_id: c for c in old.chapters}
    new_ch = {c.chapter_id: c for c in new.chapters}
    chapter_changes = {
        cid: {
            "state": {"old": old_ch[cid].state.value, "new": new_ch[cid].state.value},
            "round": {"old": old_ch[cid].round, "new": new_ch[cid].round},
        }
        for cid in sorted(set(old_ch) & set(new_ch))
        if old_ch[cid].state != new_ch[cid].state or old_ch[cid].round != new_ch[cid].round
    }

    aggregate_diff: dict = {}
    if old.aggregate is not None or new.aggregate is not None:
        old_val = old.aggregate.value if old.aggregate else None
        new_val = new.aggregate.value if new.aggregate else None
        aggregate_diff = {
            "status": {
                "old": old.aggregate.status.value if old.aggregate else None,
                "new": new.aggregate.status.value if new.aggregate else None,
            },
            "value": {"old": to_jsonable(old_val), "new": to_jsonable(new_val)},
            "delta": to_jsonable(
                (new_val - old_val)
                if old_val is not None and new_val is not None
                else None
            ),
            "missing": {
                "old": to_jsonable(old.aggregate.missing) if old.aggregate else None,
                "new": to_jsonable(new.aggregate.missing) if new.aggregate else None,
            },
        }

    old_sources = {(s.source_id, s.version) for s in old.sources}
    new_sources = {(s.source_id, s.version) for s in new.sources}

    return {
        "report_id": new.report_id,
        "from_version": old.version,
        "to_version": new.version,
        "indicators": {"added": added, "removed": removed, "changed": changed},
        "chapters": {"changed": chapter_changes},
        "national_aggregate": aggregate_diff,
        "sources": {
            "added": to_jsonable(sorted(new_sources - old_sources)),
            "removed": to_jsonable(sorted(old_sources - new_sources)),
        },
        "findings": {
            "old_count": len(old.findings),
            "new_count": len(new.findings),
        },
    }


class ReportRegistry:
    """按报告 id 保存已确认快照；快照只增不改。"""

    def __init__(self) -> None:
        self._snapshots: dict[str, list[ReportSnapshot]] = {}

    def add(self, snapshot: ReportSnapshot) -> None:
        versions = self._snapshots.setdefault(snapshot.report_id, [])
        expected = len(versions) + 1
        if snapshot.version != expected:
            raise ValueError(f"报告版本应为 {expected}，收到 {snapshot.version}")
        versions.append(snapshot)

    def get(self, report_id: str, version: int) -> ReportSnapshot:
        versions = self._snapshots.get(report_id)
        if not versions or not 1 <= version <= len(versions):
            raise NotFoundError(f"报告版本不存在: {report_id}@v{version}")
        return versions[version - 1]

    def versions(self, report_id: str) -> list[int]:
        return [s.version for s in self._snapshots.get(report_id, [])]
