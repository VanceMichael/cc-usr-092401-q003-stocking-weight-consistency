"""投苗记录的领域服务：批次锁定判定、快照与修订留痕。

路由、诊断脚本共用，保证"哪些事实已参与周期分析、如何留痕"只有一处实现。
"""

from __future__ import annotations

import json
import math
from typing import Optional

from sqlalchemy.orm import Session

from .models import Batch, HarvestSale, StockingRecord, StockingRecordRevision


def batch_is_locked(db: Session, batch_id: int) -> tuple[bool, str]:
    """批次是否已参与周期分析。

    判定（任一满足即锁定，其投苗事实禁止直接改写/删除）：
    * 批次已登记实际收获日期；
    * 批次状态为 harvested（已收获）或 closed（已关闭）；
    * 批次已有任意出塘销售记录（周期分析的成活率/产量已依赖该数据）。

    返回 (是否锁定, 原因说明)。
    """
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if batch is None:
        return False, ""
    if batch.actual_harvest_date is not None:
        return True, f"批次 {batch.batch_number} 已登记实际收获日期，周期分析已成事实"
    if (batch.status or "").lower() in ("harvested", "closed"):
        return True, f"批次 {batch.batch_number} 状态为 {batch.status}，已进入周期分析"
    sale_exists = (
        db.query(HarvestSale.id).filter(HarvestSale.batch_id == batch_id).first()
    )
    if sale_exists:
        return True, f"批次 {batch.batch_number} 已有出塘销售记录，成活率分析已引用"
    return False, ""


def _json_clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def snapshot_record(record: StockingRecord) -> dict:
    """记录的完整只读快照（用于留痕）。"""
    return {
        "id": record.id,
        "batch_id": record.batch_id,
        "species": record.species,
        "quantity": record.quantity,
        "source": record.source,
        "batch_number": record.batch_number,
        "weight_per_unit": _json_clean(record.weight_per_unit),
        "total_weight": _json_clean(record.total_weight),
        "notes": record.notes,
        "status": record.status,
        "record_type": record.record_type,
        "revision": record.revision,
        "supersedes_id": record.supersedes_id,
        "root_id": record.root_id,
        "void_reason": record.void_reason,
        "correct_reason": record.correct_reason,
        "policy_version": record.policy_version,
    }


def _next_sequence(db: Session, root_id: int) -> int:
    last = (
        db.query(StockingRecordRevision)
        .filter(StockingRecordRevision.root_record_id == root_id)
        .order_by(StockingRecordRevision.sequence.desc())
        .first()
    )
    return (last.sequence + 1) if last else 1


def append_revision(
    db: Session,
    *,
    root_id: int,
    action: str,
    record_id: int,
    before: Optional[dict],
    after: Optional[dict],
    reason: Optional[str],
) -> StockingRecordRevision:
    """向修订台账追加一行。台账只追加，永不修改、永不删除。"""
    revision = StockingRecordRevision(
        root_record_id=root_id,
        sequence=_next_sequence(db, root_id),
        action=action,
        record_id=record_id,
        before_data=json.dumps(before, ensure_ascii=False, allow_nan=False)
        if before is not None
        else None,
        after_data=json.dumps(after, ensure_ascii=False, allow_nan=False)
        if after is not None
        else None,
        reason=reason,
    )
    db.add(revision)
    return revision
