"""投苗记录 API 端到端测试：派生、校验、幂等、更正/撤销、锁定、审计。"""

import unittest

from fastapi.testclient import TestClient

from tests._app_factory import build_fresh_app, seed_pond_and_batch


class StockingApiTest(unittest.TestCase):
    def setUp(self):
        self.app, self.db_path = build_fresh_app()
        self.client = TestClient(self.app)
        self.batch_id = seed_pond_and_batch(self.client)

    # ---- 派生 ----------------------------------------------------------
    def test_total_weight_is_server_derived(self):
        r = self.client.post(
            "/api/stocking-records/",
            json={"batch_id": self.batch_id, "species": "草鱼",
                  "quantity": 1000, "weight_per_unit": 2.345},
            headers={"Idempotency-Key": "k1"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        rec = r.json()
        self.assertEqual(rec["weight_per_unit"], 2.35)
        self.assertEqual(rec["total_weight"], 2.35)
        self.assertEqual(rec["status"], "active")
        self.assertEqual(rec["version"], 1)
        self.assertTrue(rec["metrics_version"])

    def test_client_supplied_total_weight_is_not_accepted_as_field(self):
        # 模型中已无 total_weight 入参；即便多传也被 pydantic 忽略，
        # 落库值只能是派生值。
        r = self.client.post(
            "/api/stocking-records/",
            json={"batch_id": self.batch_id, "species": "草鱼",
                  "quantity": 100, "weight_per_unit": 10, "total_weight": 999},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["total_weight"], 1.0)

    # ---- 非法值 --------------------------------------------------------
    def test_invalid_values_rejected(self):
        base = {"batch_id": self.batch_id, "species": "草鱼"}
        for payload in [
            {**base, "quantity": -1, "weight_per_unit": 2.0},
            {**base, "quantity": 0, "weight_per_unit": 2.0},
            {**base, "quantity": 100, "weight_per_unit": -1},
            {**base, "quantity": 100, "weight_per_unit": 0},
            {**base, "quantity": 100, "weight_per_unit": "nan"},
            {**base, "quantity": 100, "weight_per_unit": "Infinity"},
            {**base, "quantity": 100, "weight_per_unit": 99999},
            {**base, "quantity": 2.5, "weight_per_unit": 1.0},
            {**base, "quantity": 10 ** 9, "weight_per_unit": 1.0},
        ]:
            r = self.client.post("/api/stocking-records/", json=payload)
            self.assertIn(r.status_code, (400, 422), payload)

    def test_missing_weight_per_unit_rejected(self):
        r = self.client.post(
            "/api/stocking-records/",
            json={"batch_id": self.batch_id, "species": "草鱼", "quantity": 100},
        )
        self.assertEqual(r.status_code, 422)

    # ---- 幂等 ----------------------------------------------------------
    def test_duplicate_submit_with_same_idempotency_key_is_one_increment(self):
        payload = {"batch_id": self.batch_id, "species": "草鱼",
                   "quantity": 100, "weight_per_unit": 2.0}
        r1 = self.client.post("/api/stocking-records/", json=payload,
                              headers={"Idempotency-Key": "dup"})
        r2 = self.client.post("/api/stocking-records/", json=payload,
                              headers={"Idempotency-Key": "dup"})
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        records = self.client.get(
            f"/api/stocking-records/?batch_id={self.batch_id}").json()
        self.assertEqual(len(records), 1)

    def test_different_keys_create_separate_restocking_records(self):
        for key in ("a", "b"):
            r = self.client.post(
                "/api/stocking-records/",
                json={"batch_id": self.batch_id, "species": "草鱼",
                      "quantity": 100, "weight_per_unit": 2.0},
                headers={"Idempotency-Key": key},
            )
            self.assertEqual(r.status_code, 200)
        totals = self.client.get(
            f"/api/stocking-records/totals/{self.batch_id}/").json()
        self.assertEqual(totals["quantity"], 200)
        self.assertEqual(totals["total_weight_kg"], 0.4)

    # ---- 更正 ----------------------------------------------------------
    def _create(self):
        return self.client.post(
            "/api/stocking-records/",
            json={"batch_id": self.batch_id, "species": "草鱼",
                  "quantity": 1000, "weight_per_unit": 2.345},
            headers={"Idempotency-Key": "corr"},
        ).json()

    def test_correction_requires_reason_and_version(self):
        rec = self._create()
        r = self.client.put(
            f"/api/stocking-records/{rec['id']}/",
            json={"quantity": 1200, "expected_version": rec["version"]},
        )
        self.assertEqual(r.status_code, 422)

    def test_stale_version_correction_conflicts(self):
        rec = self._create()
        r = self.client.put(
            f"/api/stocking-records/{rec['id']}/",
            json={"quantity": 1200, "weight_per_unit": 2.345,
                  "expected_version": 999, "reason": "x"},
        )
        self.assertEqual(r.status_code, 409)

    def test_successful_correction_audits_and_rederives(self):
        rec = self._create()
        r = self.client.put(
            f"/api/stocking-records/{rec['id']}/",
            json={"quantity": 1200, "weight_per_unit": 2.345,
                  "expected_version": 1, "reason": "到场复核尾数"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        updated = r.json()
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["quantity"], 1200)
        self.assertEqual(updated["total_weight"], 2.82)
        events = self.client.get(
            f"/api/stocking-records/{rec['id']}/events/").json()
        self.assertEqual([e["event_type"] for e in events],
                         ["created", "corrected"])
        self.assertEqual(events[1]["previous_value"]["quantity"], 1000)
        self.assertEqual(events[1]["new_value"]["quantity"], 1200)
        self.assertEqual(events[1]["reason"], "到场复核尾数")

    def test_second_concurrent_correction_same_version_rejected(self):
        rec = self._create()
        ok = self.client.put(
            f"/api/stocking-records/{rec['id']}/",
            json={"quantity": 1100, "weight_per_unit": 2.345,
                  "expected_version": 1, "reason": "第一次更正"},
        )
        self.assertEqual(ok.status_code, 200)
        race = self.client.put(
            f"/api/stocking-records/{rec['id']}/",
            json={"quantity": 1300, "weight_per_unit": 2.345,
                  "expected_version": 1, "reason": "并发更正"},
        )
        self.assertEqual(race.status_code, 409)
        # 失败的并发更正不得产生第二次增量
        self.assertEqual(
            self.client.get(f"/api/stocking-records/{rec['id']}/").json()["quantity"],
            1100,
        )

    # ---- 撤销 ----------------------------------------------------------
    def test_void_is_soft_and_requires_reason(self):
        rec = self._create()
        self.assertEqual(
            self.client.post(f"/api/stocking-records/{rec['id']}/void/",
                             json={"reason": ""}).status_code,
            422,
        )
        r = self.client.post(f"/api/stocking-records/{rec['id']}/void/",
                             json={"reason": "重复录入"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "voided")
        self.assertTrue(r.json()["voided_reason"])
        # 重复撤销 409
        self.assertEqual(
            self.client.post(f"/api/stocking-records/{rec['id']}/void/",
                             json={"reason": "再撤"}).status_code,
            409,
        )
        # 已撤销记录不能更正
        self.assertEqual(
            self.client.put(
                f"/api/stocking-records/{rec['id']}/",
                json={"quantity": 1, "weight_per_unit": 1,
                      "expected_version": 2, "reason": "x"},
            ).status_code,
            409,
        )
        # 不再计入汇总，但仍在列表（含已撤销）中可查
        totals = self.client.get(
            f"/api/stocking-records/totals/{self.batch_id}/").json()
        self.assertEqual(totals["quantity"], 0)
        listed = self.client.get(
            f"/api/stocking-records/?batch_id={self.batch_id}&include_voided=true"
        ).json()
        self.assertEqual(len(listed), 1)
        events = self.client.get(
            f"/api/stocking-records/{rec['id']}/events/").json()
        self.assertEqual(events[-1]["event_type"], "voided")

    def test_physical_delete_disabled(self):
        rec = self._create()
        self.assertEqual(
            self.client.delete(f"/api/stocking-records/{rec['id']}/").status_code,
            405,
        )

    # ---- 锁定 ----------------------------------------------------------
    def test_record_participating_in_analysis_is_locked(self):
        rec = self._create()
        r = self.client.post(
            "/api/harvest-sales/",
            json={"batch_id": self.batch_id, "sale_date": "2026-09-01",
                  "weight": 500, "weight_per_unit": 600, "unit_price": 20},
        )
        self.assertEqual(r.status_code, 200, r.text)
        corr = self.client.put(
            f"/api/stocking-records/{rec['id']}/",
            json={"quantity": 999, "weight_per_unit": 2.345,
                  "expected_version": 1, "reason": "想改"},
        )
        self.assertEqual(corr.status_code, 409)
        void = self.client.post(
            f"/api/stocking-records/{rec['id']}/void/",
            json={"reason": "想撤"},
        )
        self.assertEqual(void.status_code, 409)
        # 补苗仍然允许（新事实，不改写旧事实）
        add = self.client.post(
            "/api/stocking-records/",
            json={"batch_id": self.batch_id, "species": "草鱼",
                  "quantity": 50, "weight_per_unit": 3},
            headers={"Idempotency-Key": "supplement"},
        )
        self.assertEqual(add.status_code, 200)


if __name__ == "__main__":
    unittest.main()
