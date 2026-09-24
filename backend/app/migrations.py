"""启动时幂等迁移。

SQLite 上没有成熟的 Alembic 迁移链，系统要求在既有数据库文件上
``Base.metadata.create_all`` 之后，用 ``ALTER TABLE`` / ``CREATE INDEX``
幂等地补齐新列与索引。可重复执行：列已存在则跳过，已有的行由列默认值
自动回填 status='active'、version=1。

历史计量数据（总重量与明细不符、非有限数等）不在此处静默改写，
统一交给 ``app.scripts.repair_stocking`` 诊断与修复，并写审计事件。
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# 表名 -> [(列名, 列 DDL 片段)]
ADDED_COLUMNS = {
    "stocking_records": [
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("idempotency_key", "VARCHAR(100)"),
        ("voided_at", "DATETIME"),
        ("voided_reason", "TEXT"),
        ("corrected_from", "TEXT"),
        ("correction_reason", "TEXT"),
        ("metrics_version", "VARCHAR(30)"),
    ],
    "harvest_sales": [
        ("weight_per_unit", "FLOAT"),
    ],
}

ADDED_INDEXES = [
    ("ix_stocking_records_idempotency_key",
     "stocking_records", ["idempotency_key"], True),
    ("ix_stocking_records_batch_status",
     "stocking_records", ["batch_id", "status"], False),
]


def _existing_columns(conn, table_name: str) -> set:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table_name},
    ).first()
    return row is not None


def run_migrations(engine: Engine) -> None:
    """幂等执行所有结构迁移；任何环境下重复调用结果一致。"""
    with engine.begin() as conn:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())

        for table_name, columns in ADDED_COLUMNS.items():
            if table_name not in existing_tables:
                continue  # create_all 已按新模型建表
            present = _existing_columns(conn, table_name)
            for column_name, ddl in columns:
                if column_name in present:
                    continue
                conn.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")
                )
                logger.info("迁移: %s 增加列 %s", table_name, column_name)

        for index_name, table_name, columns, unique in ADDED_INDEXES:
            if table_name not in existing_tables:
                continue
            unique_kw = "UNIQUE" if unique else ""
            cols = ", ".join(columns)
            # SQLite 中唯一索引允许多个 NULL，幂等键去重不受历史空值影响
            conn.execute(text(
                f"CREATE {unique_kw} INDEX IF NOT EXISTS {index_name} "
                f"ON {table_name} ({cols})"
            ))
