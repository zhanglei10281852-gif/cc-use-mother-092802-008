"""HTTP API 冒烟与语义测试（标准库 http.client 直连本机端口）。"""

import json
import sys
import threading
import unittest
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from commitment_report.api import build_server
from commitment_report.store import ReportService


def seeded_service():
    # 先用领域 API 种入基础口径，HTTP 层走完整流程
    from commitment_report import Boundary, Evidence, IndicatorDefinition, ValueKind
    svc = ReportService(country="ATLANTIS")
    svc.register_evidence(Evidence("EV-1", "统计年鉴", source="统计局"))
    svc.register_boundary(Boundary("B1", "rev-1", "领土边界"))
    svc.register_indicator(IndicatorDefinition(
        code="co2", unit="MtCO2e", baseline_year=2015,
        boundary_revision="rev-1", value_kind=ValueKind.ABSOLUTE,
        boundary_id="B1", sectors=("energy", "transport")))
    return svc


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = build_server(seeded_service(), host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, payload=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode()
        conn.close()
        data = json.loads(raw) if raw else {}
        return resp.status, data

    def test_full_lifecycle_and_version_export(self):
        # 1. 能源部门报送并接受
        status, data = self.call("POST", "/submissions", {
            "indicator_code": "co2", "sector": "energy",
            "period_year": 2024, "value": "100",
            "evidence_ids": ["EV-1"], "submitted_by": "energy"})
        self.assertEqual(status, 201)
        sid = data["id"]
        self.assertEqual(data["reporting_status"], "reported")
        status, _ = self.call("POST", f"/submissions/{sid}/review",
                              {"reviewer": "ed", "decision": "approve",
                               "comment": "ok"})
        self.assertEqual(status, 200)

        # 2. 交通未报告（value 为 null）
        status, data = self.call("POST", "/submissions", {
            "indicator_code": "co2", "sector": "transport",
            "period_year": 2024, "value": None})
        self.assertEqual(status, 201)
        self.assertEqual(data["reporting_status"], "not_reported")
        status, _ = self.call("POST", f"/submissions/{data['id']}/review",
                              {"reviewer": "ed", "decision": "approve"})
        self.assertEqual(status, 200)

        # 3. 校验：交通缺来源是 warning；未报告区别于零
        status, data = self.call("GET", "/issues")
        self.assertEqual(status, 200)
        self.assertTrue(any(i["code"] == "missing_source"
                            for i in data["issues"]))

        # 4. 汇总可推导，交通列在未报告
        status, data = self.call("GET", "/aggregates")
        agg = data["aggregates"][0]
        self.assertTrue(agg["derived"])
        self.assertEqual(agg["value"], "100")
        self.assertEqual(agg["not_reported_sectors"], ["transport"])

        # 5. 锁定章节后报送被拒
        status, _ = self.call("POST", "/chapters/co2/lock",
                              {"actor": "lead", "comment": "定稿"})
        self.assertEqual(status, 200)
        status, data = self.call("POST", "/submissions", {
            "indicator_code": "co2", "sector": "energy",
            "period_year": 2025, "value": "1"})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "workflow_rule")
        status, _ = self.call("POST", "/chapters/co2/unlock",
                              {"actor": "lead"})
        self.assertEqual(status, 200)

        # 6. 确认 v1 并导出
        status, meta = self.call("POST", "/versions/confirm",
                                 {"actor": "sec", "comment": "v1"})
        self.assertEqual(status, 201)
        self.assertEqual(meta["version"], 1)
        status, pkg = self.call("GET", "/versions/1")
        self.assertEqual(status, 200)
        self.assertEqual(pkg["status"], "confirmed")
        self.assertEqual(pkg["country"], "ATLANTIS")
        self.assertEqual(pkg["version"], 1)
        self.assertEqual(len(pkg["indicators"]), 1)
        self.assertTrue(all("evidence_ids" in s for s in pkg["submissions"]))

        # 7. 导出不存在版本 -> 404
        status, data = self.call("GET", "/versions/99")
        self.assertEqual(status, 404)

        # 8. 单位不兼容报送被识别（能源新期次错报单位）
        status, data = self.call("POST", "/submissions", {
            "indicator_code": "co2", "sector": "energy",
            "period_year": 2025, "value": "1.2", "unit": "tCO2/GDP"})
        self.assertEqual(status, 201)
        bad_id = data["id"]
        self.call("POST", f"/submissions/{bad_id}/review",
                  {"reviewer": "ed", "decision": "approve"})
        status, data = self.call("GET", "/issues")
        self.assertTrue(any(i["code"] == "unit_mismatch"
                            for i in data["issues"]))

        # 9. 退回 -> 重新提交（revision 链）
        status, data = self.call("POST", f"/submissions/{bad_id}/review",
                                 {"reviewer": "ed", "decision": "return",
                                  "comment": "单位错误"})
        # 已批准件可被退回
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "returned")
        status, data = self.call("POST", "/submissions", {
            "indicator_code": "co2", "sector": "energy",
            "period_year": 2025, "value": "105", "unit": "MtCO2e",
            "evidence_ids": ["EV-1"], "supersedes": bad_id})
        self.assertEqual(status, 200)
        self.assertEqual(data["revision_no"], 2)

        # 10. v2 与差异摘要
        self.call("POST", f"/submissions/{data['id']}/review",
                  {"reviewer": "ed", "decision": "approve"})
        self.call("POST", "/versions/confirm", {"actor": "sec"})
        status, diff = self.call("GET", "/versions/1/diff/2")
        self.assertEqual(status, 200)
        self.assertEqual(diff["base_version"], 1)
        self.assertEqual(diff["head_version"], 2)
        self.assertTrue(diff["submissions_added"])

        # v1 数据包仍然不变
        status, pkg1 = self.call("GET", "/versions/1")
        self.assertEqual(
            [a for a in pkg1["aggregates"] if a["indicator_code"] == "co2"][0]["value"],
            "100")


if __name__ == "__main__":
    unittest.main()
