"""周期分析与批次追溯测试：成活率按出塘均重真实口径计算，三处汇总一致。"""

import unittest

from fastapi.testclient import TestClient

from tests._app_factory import build_fresh_app, seed_pond_and_batch


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.app, _ = build_fresh_app()
        self.client = TestClient(self.app)
        self.batch_id = seed_pond_and_batch(self.client)

    def _stock(self, quantity, wpu, key):
        r = self.client.post(
            "/api/stocking-records/",
            json={"batch_id": self.batch_id, "species": "草鱼",
                  "quantity": quantity, "weight_per_unit": wpu},
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_survival_rate_null_without_harvest_weight_per_unit(self):
        self._stock(1200, 2.345, "s1")
        a = self.client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertEqual(a["initial_quantity"], 1200)
        self.assertEqual(a["initial_weight_kg"], 2.82)
        self.assertIsNone(a["survival_rate"])
        self.assertTrue(a["survival_rate_note"])

    def test_survival_rate_from_real_harvest_weight_per_unit(self):
        self._stock(1200, 2.345, "s1")
        r = self.client.post(
            "/api/harvest-sales/",
            json={"batch_id": self.batch_id, "sale_date": "2026-09-01",
                  "weight": 600, "weight_per_unit": 600, "unit_price": 20},
        )
        self.assertEqual(r.status_code, 200, r.text)
        a = self.client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertEqual(a["estimated_survival_count"], 1000)
        self.assertEqual(a["survival_rate"], 83.33)
        self.assertEqual(a["harvest_weight_per_unit_g"], 600.0)
        self.assertEqual(a["harvest_weight"], 600)

    def test_weighted_harvest_weight_per_unit(self):
        self._stock(10000, 5, "s1")
        # 两次出塘：400kg@500g 与 200kg@800g，加权均重 = (400*500+200*800)/600 = 600g
        for weight, wpu in ((400, 500), (200, 800)):
            r = self.client.post(
                "/api/harvest-sales/",
                json={"batch_id": self.batch_id, "sale_date": "2026-09-01",
                      "weight": weight, "weight_per_unit": wpu, "unit_price": 20},
            )
            self.assertEqual(r.status_code, 200, r.text)
        a = self.client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertEqual(a["harvest_weight_per_unit_g"], 600.0)
        self.assertEqual(a["estimated_survival_count"], 1000)  # 600000g/600g

    def test_list_totals_trace_and_analysis_share_one_totals_view(self):
        self._stock(1000, 2.345, "s1")
        self._stock(500, 1.2, "s2")
        # 撤销一条，不应计入任何一处
        voided = self._stock(999, 9, "s3")
        self.client.post(f"/api/stocking-records/{voided['id']}/void/",
                         json={"reason": "误录"})

        totals = self.client.get(
            f"/api/stocking-records/totals/{self.batch_id}/").json()
        analysis = self.client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        trace = self.client.get(
            f"/api/analysis/traceability/{self.batch_id}/").json()

        self.assertEqual(totals["quantity"], 1500)
        self.assertEqual(analysis["initial_quantity"], 1500)
        active_trace = [r for r in trace["stocking_records"] if r["status"] == "active"]
        self.assertEqual(sum(r["quantity"] for r in active_trace), 1500)
        # 追溯记录携带计量字段与口径版本
        row = next(r for r in active_trace if r["quantity"] == 1000)
        self.assertEqual(row["weight_per_unit"], 2.35)
        self.assertEqual(row["total_weight_kg"], 2.35)
        self.assertTrue(row["metrics_version"])
        # 同一口径版本贯穿
        self.assertEqual(totals["metrics_version"], analysis["metrics_version"])
        self.assertEqual(row["metrics_version"], analysis["metrics_version"])

    def test_invalid_harvest_weight_per_unit_rejected(self):
        for bad in (-1, 0, "nan", 999999):
            r = self.client.post(
                "/api/harvest-sales/",
                json={"batch_id": self.batch_id, "sale_date": "2026-09-01",
                      "weight": 100, "weight_per_unit": bad, "unit_price": 20},
            )
            self.assertIn(r.status_code, (400, 422), bad)


if __name__ == "__main__":
    unittest.main()
