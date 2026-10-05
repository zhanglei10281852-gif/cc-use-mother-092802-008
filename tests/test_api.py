import json
import threading
import unittest
import urllib.error
import urllib.request

import support
from commitment_report.api import serve


class ApiTests(unittest.TestCase):
    """端到端：报送 → 审阅锁定 → 确认版本 → 晚到修订 → 导出与差异。"""

    @classmethod
    def setUpClass(cls):
        cls.server = serve(support.make_service(), "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_full_compilation_flow(self):
        # 1. 国家汇总预览：工业未报告 → partial，未按零处理
        status, agg = self.call("POST", "/aggregation/national", {})
        self.assertEqual(status, 200)
        self.assertEqual(agg["status"], "partial")
        self.assertEqual(agg["reported_subtotal"], "150.5")
        self.assertEqual(agg["missing"], ["IND-INDUSTRY"])

        # 2. 章节流转：提交 → 审阅意见 → 退回 → 重新提交 → 锁定
        self.assertEqual(self.call("POST", "/chapters", {
            "chapter_id": "CH-ENERGY", "title": "能源部门进展",
            "stage": "sectoral", "indicator_ids": ["IND-ENERGY"],
        })[0], 201)
        self.assertEqual(self.call("POST", "/chapters/CH-ENERGY/submit")[0], 200)
        self.assertEqual(self.call("POST", "/chapters/CH-ENERGY/start-review")[0], 200)
        status, comment = self.call("POST", "/chapters/CH-ENERGY/comments", {
            "author": "审阅人甲", "body": "煤炭分项需注明来源页码",
        })
        self.assertEqual(status, 201)
        self.assertEqual(self.call("POST", "/chapters/CH-ENERGY/return")[0], 200)
        status, chapter = self.call("POST", "/chapters/CH-ENERGY/submit")
        self.assertEqual(chapter["round"], 2)
        self.assertEqual(self.call("POST", "/chapters/CH-ENERGY/lock")[0], 200)
        # 锁定后拒绝修改
        self.assertEqual(
            self.call("POST", "/chapters/CH-ENERGY/comments",
                      {"author": "x", "body": "y"})[0],
            409,
        )

        # 3. 确认报告 v1
        self.assertEqual(self.call("POST", "/reports", {
            "report_id": "RPT-1", "title": "2026 年进展报告",
        })[0], 201)
        status, snapshot = self.call("POST", "/reports/RPT-1/confirm")
        self.assertEqual(status, 201)
        self.assertEqual(snapshot["version"], 1)
        status, export_v1 = self.call("GET", "/reports/RPT-1/versions/1/export")
        self.assertEqual(export_v1["national_aggregate"]["status"], "partial")

        # 4. 晚到的部门修订：工业补报为零、能源修订数值
        status, _ = self.call("POST", "/indicators/IND-INDUSTRY/revisions",
                              {"value": 0, "evidence": ["SRC-ENERGY"]})
        self.assertEqual(status, 201)
        self.call("POST", "/indicators/IND-ENERGY/revisions", {"value": "101.5"})
        self.call("POST", "/reports/RPT-1/confirm")

        # 5. 旧版本导出不变，新版本汇总完成且零被计入
        status, export_v1_again = self.call("GET", "/reports/RPT-1/versions/1/export")
        self.assertEqual(export_v1_again, export_v1)
        status, export_v2 = self.call("GET", "/reports/RPT-1/versions/2/export")
        self.assertEqual(export_v2["national_aggregate"]["status"], "complete")
        self.assertEqual(export_v2["national_aggregate"]["value"], "151.5")
        v2_industry = next(
            i for i in export_v2["indicators"] if i["indicator_id"] == "IND-INDUSTRY"
        )
        self.assertEqual(v2_industry["value"]["kind"], "reported")
        self.assertEqual(v2_industry["value"]["amount"], "0")

        # 6. 差异摘要与溯源
        status, diff = self.call("GET", "/reports/RPT-1/diff?from=1&to=2")
        self.assertEqual(status, 200)
        self.assertIn("IND-INDUSTRY", diff["indicators"]["changed"])
        self.assertEqual(
            diff["national_aggregate"]["status"], {"old": "partial", "new": "complete"}
        )
        provenance = export_v2["national_aggregate"]["provenance"]
        self.assertIn(["SRC-ENERGY", 1, "能源统计年鉴2025"], provenance["sources"])

    def test_unknown_routes_and_entities(self):
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("GET", "/indicators/NOPE")[0], 404)
        self.assertEqual(self.call("GET", "/reports/NOPE/versions/1/export")[0], 404)


if __name__ == "__main__":
    unittest.main()
