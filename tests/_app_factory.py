"""测试辅助：为每个用例集装载指向独立临时 SQLite 的全新后端应用。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_fresh_app():
    """返回 (app, db_path)，应用绑定一个全新的临时数据库。"""
    from fastapi.testclient import TestClient  # noqa: F401  确保依赖存在

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)  # create_all 自己创建
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    # 清掉已缓存的后端模块，使 database engine/main 按新 URL 重新初始化
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]

    from backend.app.main import app  # noqa: WPS433
    return app, db_path


def seed_pond_and_batch(client, batch_number="B-001", stocking_date="2026-03-01"):
    assert client.post(
        "/api/ponds/",
        json={"name": f"P-{batch_number}", "area": 10, "water_depth": 2, "species": "草鱼"},
    ).status_code == 200
    ponds = client.get("/api/ponds/").json()
    pond_id = ponds[-1]["id"]
    r = client.post(
        "/api/batches/",
        json={"batch_number": batch_number, "pond_id": pond_id,
              "species": "草鱼", "stocking_date": stocking_date},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]
