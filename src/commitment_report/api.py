"""HTTP API（仅依赖标准库）。

路由
----
登记：
* ``POST /evidence`` ``POST /boundaries`` ``POST /indicators`` ``POST /targets``

章节工作流：
* ``POST /chapters/<code>/lock`` ``POST /chapters/<code>/unlock``
* ``GET  /chapters/<code>/events``

报送与审阅：
* ``POST /submissions``                 提交/重新提交（``supersedes`` 指向退回件）
* ``POST /submissions/<id>/review``     记录意见并批准/退回
* ``GET  /submissions``

校验与汇总：
* ``GET /issues``                       当前口径问题清单
* ``GET /aggregates``                   由兼容分项推导出的国家汇总

版本：
* ``POST /versions/confirm``            冻结并对外确认当前版本
* ``GET  /versions``                    已确认版本清单
* ``GET  /versions/<n>``                导出指定版本报告数据包
* ``GET  /versions/<a>/diff/<b>``       版本差异摘要
* ``GET  /current``                     当前草稿数据包
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import (
    Boundary,
    Evidence,
    IndicatorDefinition,
    ReviewDecision,
    Target,
    ValueKind,
)
from .store import ReportService, WorkflowError, _snapshot_to_dict


# --------------------------------------------------------------------- 解码辅助

def _decimal(v):
    return None if v is None else Decimal(str(v))


def _date(v):
    return None if not v else date.fromisoformat(v)


def _indicator(payload: dict) -> IndicatorDefinition:
    return IndicatorDefinition(
        code=payload["code"],
        unit=payload["unit"],
        baseline_year=int(payload["baseline_year"]),
        boundary_revision=payload.get("boundary_revision", ""),
        name=payload.get("name", ""),
        value_kind=ValueKind(payload.get("value_kind", "absolute")),
        boundary_id=payload.get("boundary_id"),
        sectors=tuple(payload.get("sectors", ())),
        methodology=payload.get("methodology", "default"),
        driver_unit=payload.get("driver_unit"))


def _evidence(payload: dict) -> Evidence:
    return Evidence(
        id=payload["id"], title=payload["title"],
        source=payload.get("source", ""),
        published_on=_date(payload.get("published_on")),
        uri=payload.get("uri", ""), sha256=payload.get("sha256", ""))


def _boundary(payload: dict) -> Boundary:
    return Boundary(
        id=payload["id"], revision=payload["revision"],
        name=payload.get("name", ""),
        gases=tuple(payload.get("gases", ("CO2", "CH4", "N2O"))),
        equivalence_key=payload.get("equivalence_key"))


def _target(payload: dict) -> Target:
    return Target(
        id=payload["id"], country=payload["country"],
        indicator_code=payload["indicator_code"],
        target_year=int(payload["target_year"]),
        value=Decimal(str(payload["value"])),
        unit=payload["unit"], baseline_year=int(payload["baseline_year"]),
        description=payload.get("description", ""),
        evidence_id=payload.get("evidence_id"))


# --------------------------------------------------------------------- HTTP 处理器

def make_handler(service: ReportService):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "CommitmentReport/1.0"

        def log_message(self, fmt, *args):  # 静默；测试环境不打印访问日志
            return

        # -- 工具 ------------------------------------------------------

        def _send(self, status: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False, default=_json_default,
                              indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw, parse_float=Decimal)

        # -- 路由 ------------------------------------------------------

        def do_GET(self):
            path = self.path.rstrip("/") or "/"
            try:
                if path == "/":
                    return self._send(200, {"service": "commitment-report",
                                            "docs": __doc__})
                if path == "/current":
                    with lock:
                        return self._send(200, service.export_current())
                if path == "/aggregates":
                    with lock:
                        return self._send(200, {
                            "aggregates": [
                                _agg(a) for a in service.current_aggregates()]})
                if path == "/issues":
                    from .store import _issue_dict
                    with lock:
                        return self._send(200, {
                            "issues": [_issue_dict(i)
                                       for i in service.current_issues()]})
                if path == "/submissions":
                    from .store import _submission_dict
                    with lock:
                        recs = sorted(service._submissions.values(),
                                      key=lambda r: (r.indicator_code, r.sector,
                                                     r.period_year, r.revision_no))
                        return self._send(200, {
                            "submissions": [_submission_dict(r) for r in recs]})
                if path == "/versions":
                    with lock:
                        return self._send(200, {
                            "versions": [_version_meta(service._snapshots[v])
                                         for v in sorted(service._snapshots)]})
                if path.startswith("/versions/"):
                    return self._version_get(path[len("/versions/"):])
                if path.startswith("/chapters/") and path.endswith("/events"):
                    code = path[len("/chapters/"):-len("/events")]
                    with lock:
                        evs = service.chapter_events(code)
                    return self._send(200, {"events": [
                        {"at": e.at.isoformat(), "actor": e.actor,
                         "action": e.action, "comment": e.comment}
                        for e in evs]})
                self._send(404, {"error": "not_found", "path": path})
            except WorkflowError as exc:
                self._send(409, {"error": "workflow_rule", "detail": str(exc)})
            except KeyError as exc:
                self._send(404, {"error": "not_found", "detail": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "bad_request", "detail": str(exc)})

        def do_POST(self):
            path = self.path.rstrip("/") or "/"
            try:
                payload = self._read_json()
                with lock:
                    return self._dispatch_post(path, payload)
            except WorkflowError as exc:
                self._send(409, {"error": "workflow_rule", "detail": str(exc)})
            except KeyError as exc:
                self._send(400, {"error": "missing_field", "detail": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "bad_request", "detail": str(exc)})

        def _dispatch_post(self, path: str, payload: dict):
            from .store import _submission_dict

            if path == "/evidence":
                rec = service.register_evidence(_evidence(payload))
                return self._send(201, {"id": rec.id})
            if path == "/boundaries":
                rec = service.register_boundary(_boundary(payload))
                return self._send(201, {"id": rec.id})
            if path == "/indicators":
                rec = service.register_indicator(_indicator(payload))
                return self._send(201, {"code": rec.code})
            if path == "/targets":
                rec = service.register_target(_target(payload))
                return self._send(201, {"id": rec.id})

            if path.startswith("/chapters/"):
                rest = path[len("/chapters/"):]
                if rest.endswith("/lock"):
                    code = rest[:-len("/lock")]
                    service.lock_chapter(code, payload.get("actor", "unknown"),
                                         payload.get("comment", ""))
                    return self._send(200, {"indicator_code": code,
                                            "state": "locked"})
                if rest.endswith("/unlock"):
                    code = rest[:-len("/unlock")]
                    service.unlock_chapter(code, payload.get("actor", "unknown"),
                                           payload.get("comment", ""))
                    return self._send(200, {"indicator_code": code,
                                            "state": "open"})

            if path == "/submissions":
                rec = service.submit(
                    payload["indicator_code"], payload["sector"],
                    int(payload["period_year"]),
                    payload.get("value"),
                    unit=payload.get("unit"),
                    coverage=tuple(payload.get("coverage", ())),
                    evidence_ids=tuple(payload.get("evidence_ids", ())),
                    estimated=bool(payload.get("estimated", False)),
                    driver_value=payload.get("driver_value"),
                    submitted_by=payload.get("submitted_by", "unknown"),
                    supersedes=payload.get("supersedes"),
                    comment=payload.get("comment", ""),
                    boundary_id=payload.get("boundary_id"),
                    boundary_revision=payload.get("boundary_revision"),
                    methodology=payload.get("methodology"))
                code = 201 if rec.revision_no == 1 else 200
                return self._send(code, _submission_dict(rec))

            if path.startswith("/submissions/") and path.endswith("/review"):
                sid = path[len("/submissions/"):-len("/review")]
                rec = service.review(
                    sid, payload.get("reviewer", "unknown"),
                    ReviewDecision(payload.get("decision", "approve")),
                    payload.get("comment", ""))
                return self._send(200, _submission_dict(rec))

            if path == "/versions/confirm":
                snap = service.confirm_version(
                    payload.get("actor", "unknown"),
                    payload.get("comment", ""))
                return self._send(201, _version_meta(snap))

            self._send(404, {"error": "not_found", "path": path})

        # -- 版本读取 ---------------------------------------------------

        def _version_get(self, rest: str):
            if "/" in rest:
                a, tail = rest.split("/", 1)
                if tail.startswith("diff/"):
                    b = tail[len("diff/"):]
                    with lock:
                        return self._send(200,
                                          service.diff_versions(int(a), int(b)))
            with lock:
                return self._send(200, service.export_version(int(rest)))

    return Handler


def _agg(a):
    return {
        "indicator_code": a.indicator_code, "period_year": a.period_year,
        "derived": a.derived,
        "value": None if a.value is None else str(a.value),
        "unit": a.unit, "method": a.method,
        "component_count": a.component_count,
        "contributing_submission_ids": list(a.contributing_submission_ids),
        "zero_reported_sectors": list(a.zero_reported_sectors),
        "not_reported_sectors": list(a.not_reported_sectors),
        "notes": list(a.notes)}


def _version_meta(snap) -> dict:
    return {"version": snap.version, "status": snap.status.value,
            "confirmed_at": snap.created_at.isoformat(),
            "confirmed_by": snap.created_by,
            "indicator_count": len(snap.indicators),
            "submission_count": len(snap.submissions),
            "derived_aggregate_count":
                sum(1 for a in snap.aggregates if a.derived)}


def _json_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


# --------------------------------------------------------------------- 启动入口

def build_server(service: ReportService | None = None,
                 host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    service = service or ReportService()
    return ThreadingHTTPServer((host, port), make_handler(service))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="气候承诺进度汇编服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = build_server(host=args.host, port=args.port)
    print(f"承诺进度汇编服务监听 http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
