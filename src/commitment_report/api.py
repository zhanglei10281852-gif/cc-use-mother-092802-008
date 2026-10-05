"""基于标准库的 HTTP API。

运行：``PYTHONPATH=src python -m commitment_report --port 8080``
"""

from __future__ import annotations

import json
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .errors import ConflictError, LockedError, NotFoundError
from .serialize import to_jsonable
from .service import CommitmentService
from .values import ReportedValue


def _parse_value(payload: dict, default):
    """请求体中的报送值：缺省用 default，null 表示未报告，其余为数值。"""
    if "value" not in payload:
        return default
    raw = payload["value"]
    if raw is None:
        return ReportedValue.not_reported()
    return ReportedValue.of(raw)


def _require(payload: dict, *names: str) -> list:
    missing = [n for n in names if n not in payload]
    if missing:
        raise ValueError(f"缺少必填字段: {missing}")
    return [payload[n] for n in names]


# -- 路由处理 -----------------------------------------------------------------


def _post_sources(service, match, body, query):
    source_id, title, publisher, published_on = _require(
        body, "source_id", "title", "publisher", "published_on"
    )
    return 201, service.register_source(
        source_id, title, publisher, date.fromisoformat(published_on), body.get("uri")
    )


def _post_methods(service, match, body, query):
    method_id, name = _require(body, "method_id", "name")
    return 201, service.register_method(method_id, name, body.get("description", ""))


def _post_indicators(service, match, body, query):
    indicator_id, sector, metric, quantity_kind, unit, baseline_year, boundary = _require(
        body, "indicator_id", "sector", "metric", "quantity_kind",
        "unit", "baseline_year", "boundary_revision",
    )
    return 201, service.submit_indicator(
        indicator_id=indicator_id,
        sector=sector,
        metric=metric,
        quantity_kind=quantity_kind,
        unit=unit,
        baseline_year=int(baseline_year),
        boundary_revision=boundary,
        coverage=body.get("coverage", ()),
        value=_parse_value(body, None),
        is_estimate=bool(body.get("is_estimate", False)),
        method_id=body.get("method_id"),
        evidence=body.get("evidence", ()),
        submitted_by=body.get("submitted_by", ""),
        note=body.get("note", ""),
    )


def _post_indicator_revision(service, match, body, query):
    changes: dict = {}
    for field in ("unit", "boundary_revision", "method_id", "note", "submitted_by"):
        if field in body:
            changes[field] = body[field]
    for field in ("baseline_year",):
        if field in body:
            changes[field] = int(body[field])
    for field in ("coverage", "evidence"):
        if field in body:
            changes[field] = list(body[field])
    if "is_estimate" in body:
        changes["is_estimate"] = bool(body["is_estimate"])
    sentinel = object()
    value = _parse_value(body, sentinel)
    if value is not sentinel:
        changes["value"] = value
    return 201, service.revise_indicator(match.group(1), **changes)


def _get_indicator(service, match, body, query):
    version = query.get("version", [None])[0]
    return 200, service.get_indicator(
        match.group(1), int(version) if version is not None else None
    )


def _post_targets(service, match, body, query):
    (target_id, title, metric, quantity_kind, unit, baseline_year,
     boundary, target_year, target_value) = _require(
        body, "target_id", "title", "metric", "quantity_kind", "unit",
        "baseline_year", "boundary_revision", "target_year", "target_value",
    )
    return 201, service.define_target(
        target_id, title, metric, quantity_kind, unit, int(baseline_year),
        boundary, int(target_year), target_value, body.get("indicator_ids", ()),
    )


def _post_chapters(service, match, body, query):
    chapter_id, title, stage = _require(body, "chapter_id", "title", "stage")
    return 201, service.create_chapter(
        chapter_id, title, stage, body.get("indicator_ids", ())
    )


def _chapter_action(action):
    def handler(service, match, body, query):
        return 200, getattr(service, action)(match.group(1))

    return handler


def _post_comment(service, match, body, query):
    author, text = _require(body, "author", "body")
    return 201, service.add_review_comment(match.group(1), author, text)


def _lock_stage(service, match, body, query):
    return 200, service.lock_stage(match.group(1))


def _get_chapters(service, match, body, query):
    return 200, service.chapters.all()


def _get_validation(service, match, body, query):
    return 200, {"findings": service.validate()}


def _post_aggregate(service, match, body, query):
    return 200, service.preview_aggregate(body.get("indicator_ids"))


def _post_reports(service, match, body, query):
    report_id, title = _require(body, "report_id", "title")
    return 201, service.create_report(
        report_id, title, body.get("indicator_ids"), body.get("chapter_ids")
    )


def _confirm_report(service, match, body, query):
    return 201, service.confirm_report(match.group(1))


def _list_versions(service, match, body, query):
    return 200, {"report_id": match.group(1), "versions": service.list_report_versions(match.group(1))}


def _export_report(service, match, body, query):
    return 200, service.export_report(match.group(1), int(match.group(2)))


def _diff_report(service, match, body, query):
    try:
        from_v = int(query["from"][0])
        to_v = int(query["to"][0])
    except (KeyError, IndexError, ValueError):
        raise ValueError("差异摘要需要查询参数 from 与 to（版本号）")
    return 200, service.diff_reports(match.group(1), from_v, to_v)


ROUTES = [
    ("POST", re.compile(r"^/sources$"), _post_sources),
    ("POST", re.compile(r"^/methods$"), _post_methods),
    ("POST", re.compile(r"^/indicators$"), _post_indicators),
    ("POST", re.compile(r"^/indicators/([^/]+)/revisions$"), _post_indicator_revision),
    ("GET", re.compile(r"^/indicators/([^/]+)$"), _get_indicator),
    ("POST", re.compile(r"^/targets$"), _post_targets),
    ("POST", re.compile(r"^/chapters$"), _post_chapters),
    ("GET", re.compile(r"^/chapters$"), _get_chapters),
    ("POST", re.compile(r"^/chapters/([^/]+)/submit$"), _chapter_action("submit_chapter")),
    ("POST", re.compile(r"^/chapters/([^/]+)/start-review$"), _chapter_action("start_review")),
    ("POST", re.compile(r"^/chapters/([^/]+)/comments$"), _post_comment),
    ("POST", re.compile(r"^/chapters/([^/]+)/return$"), _chapter_action("return_chapter")),
    ("POST", re.compile(r"^/chapters/([^/]+)/lock$"), _chapter_action("lock_chapter")),
    ("POST", re.compile(r"^/stages/([^/]+)/lock$"), _lock_stage),
    ("GET", re.compile(r"^/validation$"), _get_validation),
    ("POST", re.compile(r"^/aggregation/national$"), _post_aggregate),
    ("POST", re.compile(r"^/reports$"), _post_reports),
    ("POST", re.compile(r"^/reports/([^/]+)/confirm$"), _confirm_report),
    ("GET", re.compile(r"^/reports/([^/]+)/versions$"), _list_versions),
    ("GET", re.compile(r"^/reports/([^/]+)/versions/(\d+)/export$"), _export_report),
    ("GET", re.compile(r"^/reports/([^/]+)/diff$"), _diff_report),
]


def make_handler(service: CommitmentService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            body: dict = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except json.JSONDecodeError:
                        return self._respond(400, {"error": "请求体不是合法 JSON"})
            for route_method, pattern, handler in ROUTES:
                if route_method != method:
                    continue
                match = pattern.match(parsed.path)
                if not match:
                    continue
                try:
                    status, result = handler(service, match, body, query)
                except NotFoundError as exc:
                    return self._respond(404, {"error": str(exc.args[0] if exc.args else exc)})
                except LockedError as exc:
                    return self._respond(409, {"error": str(exc)})
                except ConflictError as exc:
                    return self._respond(409, {"error": str(exc)})
                except (ValueError, KeyError) as exc:
                    return self._respond(400, {"error": str(exc)})
                return self._respond(status, result)
            self._respond(404, {"error": f"路由不存在: {method} {parsed.path}"})

        def _respond(self, status: int, payload) -> None:
            data = json.dumps(
                to_jsonable(payload), ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, *args) -> None:  # 静默访问日志
            pass

    return Handler


def serve(service: CommitmentService, host: str = "127.0.0.1", port: int = 8080):
    server = ThreadingHTTPServer((host, port), make_handler(service))
    return server
