"""旧库结构迁移与历史数据诊断/修复的可重复性测试。"""

import json
import tempfile
import unittest

from sqlalchemy import create_engine, text

from tests._app_factory import build_fresh_app  # noqa: F401  (保证 sys.path)


def build_legacy_database(db_url: str):
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE ponds (
            id INTEGER PRIMARY KEY, name VARCHAR(100) UNIQUE, area FLOAT NOT NULL,
            water_depth FLOAT NOT NULL, species VARCHAR(100), status VARCHAR(20),
            created_at DATETIME, updated_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE batches (
            id INTEGER PRIMARY KEY, batch_number VARCHAR(50) UNIQUE, pond_id INTEGER,
            species VARCHAR(100) NOT NULL, stocking_date DATE,
            estimated_harvest_date DATE, actual_harvest_date DATE,
            status VARCHAR(20), created_at DATETIME, updated_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE stocking_records (
            id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL,
            species VARCHAR(100) NOT NULL, quantity INTEGER NOT NULL,
            source VARCHAR(200), batch_number VARCHAR(50),
            weight_per_unit FLOAT, total_weight FLOAT, notes TEXT,
            created_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE harvest_sales (
            id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL,
            sale_date DATE NOT NULL, weight FLOAT NOT NULL,
            unit_price FLOAT NOT NULL, total_amount FLOAT,
            buyer VARCHAR(200), batch_number VARCHAR(50),
            quality_grade VARCHAR(50), notes TEXT, created_at DATETIME)"""))
        conn.execute(text(
            "INSERT INTO ponds VALUES (1,'P1',10,2,'草鱼','active',NULL,NULL)"))
        conn.execute(text(
            "INSERT INTO batches VALUES (1,'B1',1,'草鱼','2026-03-01',NULL,NULL,"
            "'active',NULL,NULL)"))
        # 总重量与明细不符
        conn.execute(text(
            "INSERT INTO stocking_records (id,batch_id,species,quantity,"
            "weight_per_unit,total_weight,created_at) VALUES "
            "(1,1,'草鱼',1000,2.345,999.0,'2026-03-01 00:00:00')"))
        # 缺每尾克重、总重量，无法安全派生 -> 只能人工
        conn.execute(text(
            "INSERT INTO stocking_records (id,batch_id,species,quantity,"
            "weight_per_unit,total_weight,created_at) VALUES "
            "(2,1,'草鱼',500,NULL,NULL,'2026-03-02 00:00:00')"))
        # 一致数据
        conn.execute(text(
            "INSERT INTO stocking_records (id,batch_id,species,quantity,"
            "weight_per_unit,total_weight,created_at) VALUES "
            "(3,1,'草鱼',300,1.5,0.45,'2026-03-03 00:00:00')"))
        # 旧出塘记录（迁移后 weight_per_unit 为 NULL）
        conn.execute(text(
            "INSERT INTO harvest_sales (id,batch_id,sale_date,weight,"
            "unit_price,created_at) VALUES "
            "(1,1,'2026-09-01',600,20,'2026-09-01 00:00:00')"))
    return engine


class MigrationAndRepairTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        import os
        os.close(fd)
        self.db_url = f"sqlite:///{self.db_path}"

    def _start_app(self):
        import os
        os.environ["DATABASE_URL"] = self.db_url
        import sys
        for name in list(sys.modules):
            if name == "backend" or name.startswith("backend."):
                del sys.modules[name]
        from fastapi.testclient import TestClient
        from backend.app.main import app
        return TestClient(app)

    def test_full_diagnose_repair_idempotency_and_migration(self):
        build_legacy_database(self.db_url)
        client = self._start_app()

        # --- 迁移幂等加列并回填默认值 ---
        with create_engine(self.db_url).begin() as conn:
            cols = {r[1] for r in conn.execute(
                text("PRAGMA table_info(stocking_records)"))}
        self.assertLessEqual(
            {"status", "version", "idempotency_key", "voided_reason",
             "metrics_version"},
            cols,
        )
        with create_engine(self.db_url).begin() as conn:
            self.assertEqual(
                conn.execute(text(
                    "SELECT status, version FROM stocking_records WHERE id=1"
                )).first(),
                ("active", 1),
            )

        # --- 诊断 ---
        diag = client.get("/api/diagnostics/stocking").json()
        by_record = {}
        for issue in diag["issues"]:
            by_record.setdefault(issue["record_id"], set()).add(issue["code"])
        self.assertIn("TOTAL_WEIGHT_MISMATCH", by_record[1])
        self.assertIn("MISSING_WEIGHT_PER_UNIT", by_record[2])
        self.assertTrue(any(
            i["code"] == "MISSING_HARVEST_WEIGHT_PER_UNIT"
            for i in diag["harvest_warnings"]
        ))

        # --- dry-run 不改库 ---
        dry = client.post("/api/diagnostics/stocking/repair",
                          json={"apply": False}).json()
        self.assertEqual(dry["mode"], "dry_run")
        self.assertGreaterEqual(dry["action_count"], 1)
        with create_engine(self.db_url).begin() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT total_weight FROM stocking_records WHERE id=1"
            )).first()[0], 999.0)

        # --- apply：总重量按明细重算，缺明细的只报告不改 ---
        report = client.post("/api/diagnostics/stocking/repair",
                             json={"apply": True}).json()
        self.assertEqual(report["mode"], "apply")
        with create_engine(self.db_url).begin() as conn:
            fixed = conn.execute(text(
                "SELECT total_weight, metrics_version FROM stocking_records "
                "WHERE id=1")).first()
            untouched = conn.execute(text(
                "SELECT total_weight FROM stocking_records WHERE id=2")).first()[0]
        self.assertEqual(fixed[0], 2.35)
        self.assertTrue(fixed[1])
        self.assertIsNone(untouched)
        unresolved = {u["record_id"] for u in report["unresolved"]}
        self.assertIn(2, unresolved)

        # --- 审计事件保留原值 ---
        from backend.app.database import SessionLocal
        from backend.app.models import StockingRecordEvent
        db = SessionLocal()
        try:
            events = db.query(StockingRecordEvent).filter_by(
                record_id=1, event_type="repaired").all()
            self.assertEqual(len(events), 1)
            self.assertEqual(
                json.loads(events[0].previous_value)["total_weight"], 999.0)
            self.assertEqual(
                json.loads(events[0].new_value)["total_weight"], 2.35)
            first_run_count = db.query(StockingRecordEvent).filter_by(
                event_type="repaired").count()
        finally:
            db.close()

        # --- 重复执行：零动作、零新增审计 ---
        again = client.post("/api/diagnostics/stocking/repair",
                            json={"apply": True}).json()
        self.assertEqual(again["action_count"], 0)
        db = SessionLocal()
        try:
            self.assertEqual(
                db.query(StockingRecordEvent).filter_by(
                    event_type="repaired").count(),
                first_run_count,
            )
        finally:
            db.close()

        # --- 迁移函数本身可重复执行 ---
        from backend.app.database import engine
        from backend.app.migrations import run_migrations
        run_migrations(engine)
        run_migrations(engine)

        # --- 修复后分析口径正常；缺出塘均重时存活率为 null ---
        analysis = client.get("/api/analysis/cycle/1/").json()
        self.assertEqual(analysis["initial_quantity"], 1800)
        self.assertIsNone(analysis["survival_rate"])
        self.assertTrue(analysis["survival_rate_note"])


if __name__ == "__main__":
    unittest.main()
