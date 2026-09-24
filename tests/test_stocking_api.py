"""投苗记录 API 集成测试：幂等、更正留痕、撤销、锁定、分析口径一致。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402
from app.stocking_policy import POLICY_VERSION  # noqa: E402


class StockingApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    def setUp(self):
        # 每个用例独立批次，互不干扰
        r = self.client.post(
            "/api/ponds/",
            json={"name": f"P-{self._testMethodName}", "area": 5, "water_depth": 2, "species": "草鱼"},
        )
        self.pond_id = r.json()["id"]
        r = self.client.post(
            "/api/batches/",
            json={
                "batch_number": f"B-{self._testMethodName}",
                "pond_id": self.pond_id,
                "species": "草鱼",
                "stocking_date": "2026-03-01",
            },
        )
        self.batch_id = r.json()["id"]

    def _create(self, **overrides):
        payload = {
            "batch_id": self.batch_id,
            "species": "草鱼",
            "quantity": 1000,
            "weight_per_unit": 0.5,
            "record_type": "initial",
        }
        payload.update(overrides)
        return self.client.post("/api/stocking-records/", json=payload)

    # ---------- 校验与派生 ----------

    def test_total_weight_is_derived_and_mismatch_rejected(self):
        r = self._create(total_weight=999)
        self.assertEqual(r.status_code, 422)
        r = self._create()
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.json()["total_weight"], 0.5, places=3)
        self.assertEqual(r.json()["policy_version"], POLICY_VERSION)
        self.assertEqual(r.json()["revision"], 1)
        self.assertEqual(r.json()["status"], "active")

    def test_invalid_values_rejected(self):
        self.assertEqual(self._create(quantity=-10).status_code, 422)
        self.assertEqual(self._create(quantity=0).status_code, 422)
        self.assertEqual(self._create(quantity=1.5).status_code, 422)
        self.assertEqual(self._create(weight_per_unit=0).status_code, 422)
        self.assertEqual(self._create(weight_per_unit=-1).status_code, 422)
        self.assertEqual(self._create(weight_per_unit=99999).status_code, 422)
        self.assertEqual(self._create(weight_per_unit=float("nan")).status_code, 422)
        self.assertEqual(self._create(weight_per_unit=float("inf")).status_code, 422)
        self.assertEqual(self._create(total_weight=-1).status_code, 422)

    def test_total_weight_without_unit_weight_rejected(self):
        r = self._create(weight_per_unit=None, total_weight=12)
        self.assertEqual(r.status_code, 422)

    # ---------- 幂等 ----------

    def test_duplicate_submit_with_same_token_creates_once(self):
        token = "token-dup-001"
        r1 = self._create(quantity=200, weight_per_unit=1.0, client_token=token)
        r2 = self._create(quantity=200, weight_per_unit=1.0, client_token=token)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        listing = self.client.get(
            "/api/stocking-records/", params={"batch_id": self.batch_id}
        ).json()
        self.assertEqual(len(listing), 1)

    # ---------- 更正留痕 ----------

    def test_correction_creates_new_version_and_keeps_original(self):
        rid = self._create().json()["id"]
        # 缺原因拒绝
        r = self.client.post(
            f"/api/stocking-records/{rid}/correct/",
            json={
                "batch_id": self.batch_id, "species": "草鱼",
                "quantity": 1000, "weight_per_unit": 0.6,
                "record_type": "initial",
            },
        )
        self.assertEqual(r.status_code, 422)

        r = self.client.post(
            f"/api/stocking-records/{rid}/correct/",
            json={
                "batch_id": self.batch_id, "species": "草鱼",
                "quantity": 1000, "weight_per_unit": 0.6,
                "record_type": "initial", "reason": "采购单复核单重录错",
                "expected_revision": 1,
            },
        )
        self.assertEqual(r.status_code, 200)
        new = r.json()
        self.assertEqual(new["revision"], 2)
        self.assertEqual(new["supersedes_id"], rid)
        self.assertEqual(new["root_id"], rid)
        self.assertAlmostEqual(new["total_weight"], 0.6, places=3)

        # 列表只剩新版本（active）
        listing = self.client.get(
            "/api/stocking-records/", params={"batch_id": self.batch_id}
        ).json()
        self.assertEqual([x["id"] for x in listing], [new["id"]])

        # 旧版本仍可读取且为 superseded
        old = self.client.get(f"/api/stocking-records/{rid}/").json()
        self.assertEqual(old["status"], "superseded")
        self.assertAlmostEqual(old["total_weight"], 0.5, places=3)

        # 留痕：create -> correct
        revs = self.client.get(f"/api/stocking-records/{rid}/revisions/").json()
        self.assertEqual([x["action"] for x in revs], ["create", "correct"])
        self.assertEqual(revs[1]["reason"], "采购单复核单重录错")
        self.assertIn("total_weight", revs[1]["before_data"])

        # 已被替代的旧版本不能再次更正
        again = self.client.post(
            f"/api/stocking-records/{rid}/correct/",
            json={
                "batch_id": self.batch_id, "species": "草鱼", "quantity": 1,
                "weight_per_unit": 1, "record_type": "initial", "reason": "x",
            },
        )
        self.assertEqual(again.status_code, 409)

    def test_optimistic_lock_conflict_returns_409(self):
        rid = self._create().json()["id"]
        self.client.post(
            f"/api/stocking-records/{rid}/correct/",
            json={
                "batch_id": self.batch_id, "species": "草鱼",
                "quantity": 1000, "weight_per_unit": 0.7,
                "record_type": "initial", "reason": "第一次",
            },
        )
        # 另一会话仍以 v1 提交 -> 冲突
        stale = self.client.post(
            f"/api/stocking-records/{rid}/correct/",
            json={
                "batch_id": self.batch_id, "species": "草鱼",
                "quantity": 1000, "weight_per_unit": 0.8,
                "record_type": "initial", "reason": "过期版本",
                "expected_revision": 1,
            },
        )
        self.assertEqual(stale.status_code, 409)

    # ---------- 撤销 ----------

    def test_void_is_soft_delete_with_reason_and_ledger(self):
        rid = self._create(quantity=300).json()["id"]
        # 缺原因
        self.assertEqual(
            self.client.post(f"/api/stocking-records/{rid}/void/", json={}).status_code,
            422,
        )
        r = self.client.post(
            f"/api/stocking-records/{rid}/void/", json={"reason": "重复录入撤销"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "void")

        listing = self.client.get(
            "/api/stocking-records/", params={"batch_id": self.batch_id}
        ).json()
        self.assertEqual(listing, [])

        # 撤销后不能再撤销
        self.assertEqual(
            self.client.post(f"/api/stocking-records/{rid}/void/", json={"reason": "x"}).status_code,
            409,
        )
        revs = self.client.get(f"/api/stocking-records/{rid}/revisions/").json()
        self.assertEqual([x["action"] for x in revs], ["create", "void"])

    def test_legacy_put_and_delete_disabled(self):
        rid = self._create().json()["id"]
        self.assertEqual(
            self.client.put(f"/api/stocking-records/{rid}/", json={"quantity": 1}).status_code,
            410,
        )
        self.assertEqual(
            self.client.delete(f"/api/stocking-records/{rid}/").status_code, 410
        )

    # ---------- 锁定：参与周期分析后不可改写 ----------

    def test_locked_batch_rejects_new_correction_and_void(self):
        rid = self._create().json()["id"]
        self.client.post(
            "/api/harvest-sales/",
            json={
                "batch_id": self.batch_id, "sale_date": "2026-09-01",
                "weight": 400, "unit_price": 20,
            },
        )
        self.assertEqual(
            self._create(quantity=50, weight_per_unit=2).status_code, 409
        )
        self.assertEqual(
            self.client.post(
                f"/api/stocking-records/{rid}/correct/",
                json={
                    "batch_id": self.batch_id, "species": "草鱼", "quantity": 1,
                    "weight_per_unit": 1, "record_type": "initial", "reason": "x",
                },
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(f"/api/stocking-records/{rid}/void/", json={"reason": "x"}).status_code,
            409,
        )

    # ---------- 分析与列表/追溯同一版本、同一单重 ----------

    def test_analysis_uses_active_version_and_actual_unit_weight(self):
        rid = self._create(quantity=1000, weight_per_unit=0.5).json()["id"]
        self.client.post(
            f"/api/stocking-records/{rid}/correct/",
            json={
                "batch_id": self.batch_id, "species": "草鱼",
                "quantity": 1000, "weight_per_unit": 1.0,
                "record_type": "initial", "reason": "更正为实际单重",
            },
        )
        self.client.post(
            "/api/harvest-sales/",
            json={
                "batch_id": self.batch_id, "sale_date": "2026-09-01",
                "weight": 400, "unit_price": 20,
            },
        )
        a = self.client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        # 旧的 0.5 克版本不计入；新单重 1 克 -> 400kg = 400000 尾
        self.assertEqual(a["initial_quantity"], 1000)
        self.assertAlmostEqual(a["initial_weight"], 1.0, places=3)
        self.assertEqual(a["avg_weight_per_unit"], 1.0)
        self.assertEqual(a["survival_quantity"], 400_000)
        self.assertEqual(a["survival_rate"], 100.0)
        self.assertTrue(a["survival_estimable"])

        trace = self.client.get(
            f"/api/analysis/traceability/{self.batch_id}/"
        ).json()
        self.assertEqual(len(trace["stocking_records"]), 1)
        row = trace["stocking_records"][0]
        self.assertEqual(row["revision"], 2)
        self.assertEqual(row["weight_per_unit"], 1.0)
        self.assertAlmostEqual(row["total_weight"], 1.0, places=3)

    def test_supplement_records_accumulate_in_analysis(self):
        self._create(quantity=1000, weight_per_unit=1.0, record_type="initial")
        self._create(quantity=500, weight_per_unit=2.0, record_type="supplement")
        self.client.post(
            "/api/harvest-sales/",
            json={
                "batch_id": self.batch_id, "sale_date": "2026-09-01",
                "weight": 10, "unit_price": 20,
            },
        )
        a = self.client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertEqual(a["initial_quantity"], 1500)
        # 加权单重 = (1000*1 + 500*2)/1500 = 1.33 克
        self.assertAlmostEqual(a["avg_weight_per_unit"], 1.33, places=2)
        # 10kg = 10000g / 1.33g = 7518 尾
        self.assertEqual(a["survival_quantity"], 7518)

    # ---------- 诊断/修复 ----------

    def test_diagnostics_and_repair_endpoints_repeatable(self):
        r = self._create()
        self.assertEqual(r.status_code, 200)
        clean = self.client.get("/api/stocking-records/diagnostics/").json()
        self.assertTrue(clean["summary"]["clean"])

        # 直接把总重改成不一致的"历史脏数据"
        db = SessionLocal()
        rec = db.query(models.StockingRecord).filter(
            models.StockingRecord.batch_id == self.batch_id
        ).first()
        rec.total_weight = 999.0
        db.commit()
        db.close()

        diag = self.client.get("/api/stocking-records/diagnostics/").json()
        self.assertGreaterEqual(diag["summary"]["issue_count"], 1)

        fixed1 = self.client.post("/api/stocking-records/diagnostics/repair/").json()
        self.assertEqual(fixed1["summary"]["fixed_count"], 1)
        fixed2 = self.client.post("/api/stocking-records/diagnostics/repair/").json()
        self.assertEqual(fixed2["summary"]["fixed_count"], 0)  # 幂等：二次无改动

        # 修复写了留痕
        revs = self.client.get(
            f"/api/stocking-records/{r.json()['id']}/revisions/"
        ).json()
        self.assertIn("repair", [x["action"] for x in revs])

    def test_policy_endpoint_returns_version(self):
        info = self.client.get("/api/stocking-records/policy/").json()
        self.assertEqual(info["version"], POLICY_VERSION)
        self.assertTrue(info["total_weight"]["derived_only"])


if __name__ == "__main__":
    unittest.main()
