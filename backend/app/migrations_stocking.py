"""幂等数据库迁移（SQLite 安全，可重复执行）。

项目使用 ``Base.metadata.create_all`` 自动建表，不会修改已存在的表结构。
本模块在启动时以 PRAGMA + ALTER TABLE 方式补齐投苗口径修复所需列与索引，
并回填历史数据；所有步骤都先检查现状，重复执行不产生副作用。
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from sqlalchemy import inspect, text

from .database import engine, Base
from .models import StockingRecord, StockingRecordRevision
from .stocking_policy import POLICY_VERSION

# 新增列定义: 列名 -> (列类型DDL, 回填值)
_NEW_COLUMNS = {
    "status": ("VARCHAR(20) NOT NULL DEFAULT 'active'", "active"),
    "record_type": ("VARCHAR(20) NOT NULL DEFAULT 'initial'", "initial"),
    "revision": ("INTEGER NOT NULL DEFAULT 1", 1),
    "supersedes_id": ("INTEGER", None),
    "root_id": ("INTEGER", None),
    "client_token": ("VARCHAR(64)", None),
    "void_reason": ("TEXT", None),
    "correct_reason": ("TEXT", None),
    "policy_version": ("VARCHAR(64)", None),
    "updated_at": ("DATETIME", None),
}


def _existing_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _existing_indexes(conn, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA index_list({table})")}


def run_migrations() -> None:
    """启动入口：建表 + 加列 + 回填 + 索引 + 历史留痕，全程幂等。"""
    # 只新建尚不存在的表（StockingRecordRevision 等），不影响既有表
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "stocking_records" not in tables:
        return  # 全新库由 create_all 建好，无需迁移

    with engine.begin() as conn:
        existing = _existing_columns(conn, "stocking_records")
        for column, (ddl, _default) in _NEW_COLUMNS.items():
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE stocking_records ADD COLUMN {column} {ddl}"
                )

        # 回填历史行（DEFAULT 已覆盖加列时的值，这里对显式 NULL 再兜底一次）
        conn.exec_driver_sql(
            "UPDATE stocking_records SET status = 'active' "
            "WHERE status IS NULL OR status = ''"
        )
        conn.exec_driver_sql(
            "UPDATE stocking_records SET record_type = 'initial' "
            "WHERE record_type IS NULL OR record_type = ''"
        )
        conn.exec_driver_sql(
            "UPDATE stocking_records SET revision = 1 WHERE revision IS NULL"
        )
        # 历史记录各自作为自己的事实根
        conn.exec_driver_sql(
            "UPDATE stocking_records SET root_id = id WHERE root_id IS NULL"
        )
        conn.exec_driver_sql(
            f"UPDATE stocking_records SET policy_version = '{POLICY_VERSION}' "
            "WHERE policy_version IS NULL OR policy_version = ''"
        )

        # 幂等令牌唯一索引（SQLite 中 NULL 互不冲突，不影响无令牌旧数据）
        indexes = _existing_indexes(conn, "stocking_records")
        if "ix_stocking_records_client_token" not in indexes:
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX ix_stocking_records_client_token "
                "ON stocking_records(client_token) WHERE client_token IS NOT NULL"
            )

        # 同一逻辑事实(root_id)同时只允许一个 active 版本：
        # 并发更正时后提交者会被该索引拒绝，避免产生两个新版本（两次增量）
        if "ix_stocking_records_active_root" not in indexes:
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX ix_stocking_records_active_root "
                "ON stocking_records(root_id) WHERE status = 'active'"
            )

        _seed_legacy_revisions(conn)


def _clean(value):
    """把 NaN/Infinity 转成 None，保证快照是合法 JSON。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _snapshot(row) -> str:
    return json.dumps(
        {
            "id": row[0],
            "batch_id": row[1],
            "species": row[2],
            "quantity": row[3],
            "source": row[4],
            "batch_number": row[5],
            "weight_per_unit": _clean(row[6]),
            "total_weight": _clean(row[7]),
            "notes": row[8],
            "status": row[9],
            "record_type": row[10],
            "revision": row[11],
        },
        ensure_ascii=False,
        allow_nan=False,
    )


def _seed_legacy_revisions(conn) -> None:
    """为迁移前就存在、尚无留痕的记录补一条 create 留痕（可重复执行）。"""
    legacy_rows = conn.execute(
        text(
            "SELECT s.id, s.batch_id, s.species, s.quantity, s.source, "
            "s.batch_number, s.weight_per_unit, s.total_weight, s.notes, "
            "s.status, s.record_type, s.revision, s.root_id "
            "FROM stocking_records s "
            "WHERE NOT EXISTS (SELECT 1 FROM stocking_record_revisions r "
            "WHERE r.record_id = s.id)"
        )
    ).all()

    for row in legacy_rows:
        record_id, root_id = row[0], row[12]
        now = datetime.utcnow().isoformat(sep=" ")
        conn.execute(
            text(
                "INSERT INTO stocking_record_revisions "
                "(root_record_id, sequence, action, record_id, before_data, "
                "after_data, reason, created_at) "
                "VALUES (:root, 1, 'create', :rid, NULL, :after, :reason, :ts)"
            ),
            {
                "root": root_id if root_id is not None else record_id,
                "rid": record_id,
                "after": _snapshot(row),
                "reason": "历史数据迁移补录（系统升级时已存在的投苗记录）",
                "ts": now,
            },
        )
