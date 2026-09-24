from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional
import json

from ..database import get_db
from ..models import Batch, StockingRecord
from ..schemas import (
    StockingRecordCreate, StockingRecordUpdate, StockingRecordResponse,
    StockingRecordVoid, StockingRecordEventResponse, StockingTotals,
)
from ..services import stocking as svc
from ..services.metrics import (
    METRICS_VERSION, MetricsError, derive_total_weight_kg,
)

router = APIRouter(
    prefix="/api/stocking-records",
    tags=["投苗记录"]
)

#: 幂等键最大长度，与模型列宽一致。
IDEMPOTENCY_KEY_MAX = 100


def _get_record_or_404(db: Session, record_id: int) -> StockingRecord:
    record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    return record


def _derive_or_400(quantity, weight_per_unit) -> float:
    try:
        return derive_total_weight_kg(quantity, weight_per_unit)
    except MetricsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _canonical_weight(weight_per_unit) -> float:
    """把每尾克重规范化到口径精度后再落库。"""
    from ..services.metrics import WEIGHT_PER_UNIT_DECIMALS, round_half_up
    try:
        return float(round_half_up(weight_per_unit, WEIGHT_PER_UNIT_DECIMALS))
    except MetricsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _clean_species(species: str) -> str:
    cleaned = species.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="品种不能为空")
    return cleaned


@router.post("/", response_model=StockingRecordResponse)
def create_stocking_record(
    record: StockingRecordCreate,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_operator: Optional[str] = Header(default=None, alias="X-Operator"),
):
    """录入投苗（含分批补苗）。

    * 总重量由服务端按口径版本由 尾数×每尾克重 派生，请求体中的
      total_weight 一律忽略/拒绝；
    * 必须提供每尾克重与正整数尾数，禁止负数、非有限数及超范围值；
    * 同一 Idempotency-Key 的重复提交/并发重试只产生一次增量，
      重放返回首次创建的记录。
    """
    db_batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    key = None
    if idempotency_key is not None:
        key = idempotency_key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="Idempotency-Key 不能为空")
        if len(key) > IDEMPOTENCY_KEY_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Idempotency-Key 不能超过 {IDEMPOTENCY_KEY_MAX} 个字符",
            )
        existing = db.query(StockingRecord).filter(
            StockingRecord.idempotency_key == key
        ).first()
        if existing:
            # 重复提交：不产生第二次增量，直接回放首次结果
            return existing

    total_weight = _derive_or_400(record.quantity, record.weight_per_unit)
    weight_per_unit = _canonical_weight(record.weight_per_unit)

    new_record = StockingRecord(
        batch_id=record.batch_id,
        species=_clean_species(record.species),
        quantity=record.quantity,
        source=record.source,
        batch_number=record.batch_number,
        weight_per_unit=weight_per_unit,
        total_weight=total_weight,
        notes=record.notes,
        status="active",
        version=1,
        idempotency_key=key,
        metrics_version=METRICS_VERSION,
    )
    db.add(new_record)
    try:
        db.commit()
    except IntegrityError:
        # 并发情况下另一请求已用同一幂等键落库
        db.rollback()
        if key:
            existing = db.query(StockingRecord).filter(
                StockingRecord.idempotency_key == key
            ).first()
            if existing:
                return existing
        raise HTTPException(status_code=409, detail="提交冲突，请重试")
    db.refresh(new_record)

    svc.append_event(
        db, new_record, "created", None, svc._snapshot(new_record),
        operator=x_operator, from_version=0, to_version=1,
    )
    db.commit()
    return new_record


@router.get("/", response_model=List[StockingRecordResponse])
def get_stocking_records(
    skip: int = 0,
    limit: int = Query(default=100, le=1000),
    batch_id: int = None,
    status: str = Query(default="all", pattern="^(active|voided|all)$"),
    include_voided: bool = False,
    db: Session = Depends(get_db),
):
    query = db.query(StockingRecord)
    if batch_id:
        query = query.filter(StockingRecord.batch_id == batch_id)
    if include_voided:
        status = "all"
    if status != "all":
        query = query.filter(StockingRecord.status == status)
    records = query.order_by(
        StockingRecord.created_at.desc(), StockingRecord.id.desc()
    ).offset(skip).limit(limit).all()
    return records


@router.get("/totals/{batch_id}/", response_model=StockingTotals)
def get_batch_stocking_totals(batch_id: int, db: Session = Depends(get_db)):
    """投苗列表/追溯/分析共用的批次投苗汇总（同一口径版本）。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return svc.batch_totals(db, batch_id)


@router.get("/{record_id}/", response_model=StockingRecordResponse)
def get_stocking_record(record_id: int, db: Session = Depends(get_db)):
    return _get_record_or_404(db, record_id)


@router.get("/{record_id}/events/", response_model=List[StockingRecordEventResponse])
def get_stocking_record_events(record_id: int, db: Session = Depends(get_db)):
    _get_record_or_404(db, record_id)
    return svc.events_for_record(db, record_id)


@router.put("/{record_id}/", response_model=StockingRecordResponse)
def correct_stocking_record(
    record_id: int,
    correction: StockingRecordUpdate,
    db: Session = Depends(get_db),
    x_operator: Optional[str] = Header(default=None, alias="X-Operator"),
):
    """更正投苗记录。

    必须携带 expected_version（乐观锁）与 reason（留痕）。原值以审计
    事件与 corrected_from 快照保留，version 递增。重复/并发更正若版本
    不匹配返回 409，不会覆盖彼此，也不会产生第二次增量。
    已参与周期分析（批次已出塘或已登记出塘销售）的事实锁定，拒绝改写。
    """
    db_record = _get_record_or_404(db, record_id)
    if db_record.status == "voided":
        raise HTTPException(status_code=409, detail="记录已撤销，不能更正；请新增补苗记录")
    if db_record.version != correction.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "记录已被他人更正（版本冲突），请刷新后基于最新版本重试",
                "current_version": db_record.version,
            },
        )
    if svc.is_locked(db, db_record):
        raise HTTPException(
            status_code=409,
            detail="该批次已进入/完成周期分析，投苗事实已锁定；"
                   "请勿改写，若需补苗请新增一条投苗记录",
        )

    try:
        reason = svc.normalize_reason(correction.reason, field="更正原因")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    previous = svc._snapshot(db_record)

    quantity = correction.quantity if correction.quantity is not None else db_record.quantity
    weight_per_unit = (
        correction.weight_per_unit if correction.weight_per_unit is not None
        else db_record.weight_per_unit
    )
    total_weight = _derive_or_400(quantity, weight_per_unit)
    weight_per_unit = _canonical_weight(weight_per_unit)

    new_version = previous["version"] + 1
    corrected_from = json.dumps(previous, ensure_ascii=False, default=str)
    new_values = {
        "quantity": quantity,
        "weight_per_unit": weight_per_unit,
        "total_weight": total_weight,
        "species": _clean_species(correction.species) if correction.species is not None
        else db_record.species,
        "source": correction.source if correction.source is not None
        else db_record.source,
        "batch_number": correction.batch_number if correction.batch_number is not None
        else db_record.batch_number,
        "notes": correction.notes if correction.notes is not None
        else db_record.notes,
        "version": new_version,
        "correction_reason": reason,
        "corrected_from": corrected_from,
        "metrics_version": METRICS_VERSION,
    }

    # CAS：条件更新，防止两个并发请求同时通过上面的版本检查。
    # 先不改动 ORM 对象，避免 autoflush 绕过版本过滤。
    updated = db.query(StockingRecord).filter(
        StockingRecord.id == record_id,
        StockingRecord.version == correction.expected_version,
    ).update(new_values, synchronize_session=False)
    if updated == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发更正冲突，请刷新后重试")

    db.refresh(db_record)
    svc.append_event(
        db, db_record, "corrected", previous, svc._snapshot(db_record),
        reason=reason, operator=x_operator,
        from_version=previous["version"], to_version=new_version,
    )
    db.commit()
    db.refresh(db_record)
    return db_record


@router.post("/{record_id}/void/", response_model=StockingRecordResponse)
def void_stocking_record(
    record_id: int,
    payload: StockingRecordVoid,
    db: Session = Depends(get_db),
    x_operator: Optional[str] = Header(default=None, alias="X-Operator"),
):
    """撤销投苗记录（软撤销）：保留原值与撤销原因，不再计入任何汇总。

    已参与周期分析的事实锁定，不能撤销；物理删除接口已禁用。
    """
    db_record = _get_record_or_404(db, record_id)
    if db_record.status == "voided":
        raise HTTPException(status_code=409, detail="记录已撤销，请勿重复操作")
    if svc.is_locked(db, db_record):
        raise HTTPException(
            status_code=409,
            detail="该批次已进入/完成周期分析，投苗事实已锁定，不能撤销；"
                   "如确需调整请新增补苗记录并在备注中说明",
        )
    try:
        reason = svc.normalize_reason(payload.reason, field="撤销原因")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from datetime import datetime
    previous = svc._snapshot(db_record)
    voided_at = datetime.utcnow()
    new_version = previous["version"] + 1

    # 先执行条件 UPDATE（status='active'），避免先改 ORM 对象导致
    # autoflush 使条件更新匹配 0 行；同时挡住并发重复撤销。
    updated = db.query(StockingRecord).filter(
        StockingRecord.id == record_id,
        StockingRecord.status == "active",
    ).update({
        "status": "voided",
        "voided_at": voided_at,
        "voided_reason": reason,
        "version": new_version,
    }, synchronize_session=False)
    if updated == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发撤销冲突，请刷新后重试")

    db.refresh(db_record)
    svc.append_event(
        db, db_record, "voided", previous, svc._snapshot(db_record),
        reason=reason, operator=x_operator,
        from_version=previous["version"], to_version=new_version,
    )
    db.commit()
    db.refresh(db_record)
    return db_record


@router.delete("/{record_id}/")
def delete_stocking_record(record_id: int, db: Session = Depends(get_db)):
    """物理删除已禁用：投苗事实只能软撤销并留痕。"""
    db_record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    raise HTTPException(
        status_code=405,
        detail="不允许物理删除投苗记录；请使用 POST .../void/ 撤销并填写原因",
    )
