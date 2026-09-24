"""投苗记录领域服务：事实快照、审计事件、锁定判定、批次汇总。

路由（stocking）、周期分析（analysis）与历史数据修复（repair_stocking）
都通过本模块读写投苗事实，保证列表、追溯、分析三处引用同一份汇总口径：

* 有效投苗尾数/总重量只统计 ``status='active'`` 的记录；
* 任何更正都写追加式审计事件并推进 version；
* 已参与周期分析（批次已出塘或已产生出塘销售）的记录禁止就地改写，
  只能通过"补苗冲销"（新增方向相反的更正记录）纠正。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models import Batch, HarvestSale, StockingRecord, StockingRecordEvent
from .metrics import METRICS_VERSION

#: 更正/撤销原因允许的最大长度。
MAX_REASON_LENGTH = 500


class StockingConflict(Exception):
    """并发更正冲突（版本号不匹配）或记录已锁定。HTTP 层映射为 409。"""


def _snapshot(record: StockingRecord) -> dict:
    return {
        "id": record.id,
        "batch_id": record.batch_id,
        "species": record.species,
        "quantity": record.quantity,
        "weight_per_unit": record.weight_per_unit,
        "total_weight": record.total_weight,
        "source": record.source,
        "batch_number": record.batch_number,
        "notes": record.notes,
        "status": record.status,
        "version": record.version,
    }


def normalize_reason(reason: Optional[str], *, field: str = "原因") -> str:
    if reason is None or not str(reason).strip():
        raise ValueError(f"{field}必填，更正与撤销必须留痕")
    text = str(reason).strip()
    if len(text) > MAX_REASON_LENGTH:
        raise ValueError(f"{field}不能超过 {MAX_REASON_LENGTH} 个字")
    return text


def append_event(db: Session, record: StockingRecord, event_type: str,
                 previous: Optional[dict], new: Optional[dict],
                 *, reason: Optional[str] = None, operator: Optional[str] = None,
                 from_version: Optional[int] = None,
                 to_version: Optional[int] = None,
                 metrics_version: str = METRICS_VERSION) -> StockingRecordEvent:
    event = StockingRecordEvent(
        record_id=record.id,
        event_type=event_type,
        operator=operator,
        reason=reason,
        previous_value=json.dumps(previous, ensure_ascii=False, default=str)
        if previous is not None else None,
        new_value=json.dumps(new, ensure_ascii=False, default=str)
        if new is not None else None,
        from_version=from_version if from_version is not None else record.version,
        to_version=to_version if to_version is not None else record.version,
        metrics_version=metrics_version,
    )
    db.add(event)
    return event


def is_locked(db: Session, record: StockingRecord) -> bool:
    """记录是否已参与周期分析（事实锁定，禁止就地改写/物理删除）。"""
    batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if batch is not None and (
        batch.status in ("harvested", "closed") or batch.actual_harvest_date
    ):
        return True
    sale_exists = db.query(HarvestSale.id).filter(
        HarvestSale.batch_id == record.batch_id
    ).first()
    return sale_exists is not None


def active_records(db: Session, batch_id: int) -> list[StockingRecord]:
    return db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch_id,
        StockingRecord.status == "active",
    ).order_by(StockingRecord.created_at, StockingRecord.id).all()


def summarize(records: Iterable[StockingRecord]) -> dict:
    """按统一口径汇总投苗事实；列表/追溯/分析共用。

    只统计 active 记录。total_weight 以各记录派生值为准求和后四舍五入，
    缺失单重的历史记录不计重量但计尾数（同时由诊断/修复流程标记）。
    """
    from .metrics import TOTAL_WEIGHT_DECIMALS, round_half_up

    total_quantity = 0
    total_weight = 0.0
    missing_weight = 0
    for r in records:
        if r.status != "active":
            continue
        if isinstance(r.quantity, int) and r.quantity > 0:
            total_quantity += r.quantity
        if r.total_weight is not None:
            total_weight += float(r.total_weight)
        elif r.weight_per_unit is not None and isinstance(r.quantity, int):
            total_weight += r.quantity * float(r.weight_per_unit) / 1000.0
        else:
            missing_weight += 1
    return {
        "quantity": total_quantity,
        "total_weight_kg": float(round_half_up(total_weight, TOTAL_WEIGHT_DECIMALS)),
        "records_missing_weight": missing_weight,
        "metrics_version": METRICS_VERSION,
    }


def batch_totals(db: Session, batch_id: int) -> dict:
    return summarize(active_records(db, batch_id))


def events_for_record(db: Session, record_id: int) -> list[StockingRecordEvent]:
    return db.query(StockingRecordEvent).filter(
        StockingRecordEvent.record_id == record_id
    ).order_by(StockingRecordEvent.id).all()


def serialize_event(event: StockingRecordEvent) -> dict:
    return {
        "id": event.id,
        "record_id": event.record_id,
        "event_type": event.event_type,
        "event_at": event.event_at,
        "operator": event.operator,
        "reason": event.reason,
        "previous_value": json.loads(event.previous_value)
        if event.previous_value else None,
        "new_value": json.loads(event.new_value) if event.new_value else None,
        "from_version": event.from_version,
        "to_version": event.to_version,
        "metrics_version": event.metrics_version,
    }
