from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from typing import List, Optional
import time

from ..database import get_db
from ..models import Batch, StockingRecord, StockingRecordRevision
from ..schemas import (
    StockingRecordCreate,
    StockingRecordCorrect,
    StockingRecordVoid,
    StockingRecordResponse,
    StockingRecordRevisionResponse,
)
from ..stocking_policy import (
    MeasurementError,
    POLICY_VERSION,
    normalize_weight_per_unit,
    policy_info,
    validate_quantity,
    validate_total_weight,
)
from ..stocking_service import append_revision, batch_is_locked, snapshot_record
from ..stocking_diagnostics import diagnose

router = APIRouter(
    prefix="/api/stocking-records",
    tags=["投苗记录"]
)


def _get_batch_or_404(db: Session, batch_id: int) -> Batch:
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return db_batch


def _measure(quantity, weight_per_unit, total_weight):
    """统一计量入口：校验并返回 (尾数, 每尾克重Decimal, 总重Decimal)。"""
    try:
        qty = validate_quantity(quantity)
        wpu = (
            normalize_weight_per_unit(weight_per_unit)
            if weight_per_unit is not None
            else None
        )
        if wpu is None:
            # 允许历史兼容：无单重时总重不能由明细派生，故禁止录入总重
            if total_weight not in (None, ""):
                raise MeasurementError("未填写每尾克重时，总重量无法由明细派生，请先填写单重")
            total = None
        else:
            total = validate_total_weight(total_weight, qty, wpu)
        return qty, wpu, total
    except MeasurementError as exc:
        raise HTTPException(status_code=422, detail=exc.detail)


@router.get("/policy/")
def get_policy():
    """返回当前计量口径（前端据此渲染精度/范围/提示，保证同版本）。"""
    return policy_info()


def _find_by_token(db: Session, token: str) -> Optional[StockingRecord]:
    return (
        db.query(StockingRecord)
        .filter(StockingRecord.client_token == token)
        .first()
    )


@router.post("/", response_model=StockingRecordResponse)
def create_stocking_record(record: StockingRecordCreate, db: Session = Depends(get_db)):
    _get_batch_or_404(db, record.batch_id)

    # 已参与周期分析的批次不得再追加投苗事实（否则初始尾数被悄悄改写）
    locked, why = batch_is_locked(db, record.batch_id)
    if locked:
        raise HTTPException(status_code=409, detail=f"批次已锁定，禁止新增投苗记录：{why}")

    # 幂等：同一 client_token 的重复提交/并发双发只落一条，不产生两次增量
    if record.client_token:
        existing = _find_by_token(db, record.client_token)
        if existing:
            return existing

    qty, wpu, total = _measure(record.quantity, record.weight_per_unit, record.total_weight)

    new_record = StockingRecord(
        batch_id=record.batch_id,
        species=record.species.strip(),
        quantity=qty,
        source=record.source,
        batch_number=record.batch_number,
        weight_per_unit=float(wpu) if wpu is not None else None,
        total_weight=float(total) if total is not None else None,
        notes=record.notes,
        record_type=record.record_type,
        status="active",
        revision=1,
        client_token=record.client_token or None,
        policy_version=POLICY_VERSION,
    )

    # 并发双发可能撞上 SQLite 写锁或唯一索引：短重试，先提交者胜出，
    # 后到者按 client_token 取回同一条记录，绝不产生两次增量。
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            db.add(new_record)
            db.flush()
            new_record.root_id = new_record.id
            db.flush()
            append_revision(
                db,
                root_id=new_record.id,
                action="create",
                record_id=new_record.id,
                before=None,
                after=snapshot_record(new_record),
                reason=None,
            )
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if record.client_token:
                existing = _find_by_token(db, record.client_token)
                if existing:
                    return existing
            raise HTTPException(
                status_code=409, detail="提交冲突，请刷新后重试（重复提交已拦截）"
            )
        except OperationalError as exc:  # SQLite "database is locked"
            db.rollback()
            last_error = exc
            if record.client_token and "locked" in str(exc).lower() and attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                existing = _find_by_token(db, record.client_token)
                if existing:
                    return existing
                continue
            raise HTTPException(
                status_code=503,
                detail="数据库正忙，请重试；使用相同幂等令牌不会产生重复记录",
            )
    else:  # pragma: no cover - 重试耗尽的兜底
        raise HTTPException(status_code=503, detail=f"提交失败，请重试：{last_error}")

    db.refresh(new_record)
    return new_record


@router.get("/", response_model=List[StockingRecordResponse])
def get_stocking_records(
    skip: int = 0,
    limit: int = 100,
    batch_id: Optional[int] = None,
    include_history: bool = False,
    db: Session = Depends(get_db),
):
    """投苗列表。默认仅返回当前有效版本（active）；include_history 返回全部版本。"""
    query = db.query(StockingRecord)
    if not include_history:
        query = query.filter(StockingRecord.status == "active")
    if batch_id:
        query = query.filter(StockingRecord.batch_id == batch_id)
    records = query.order_by(StockingRecord.id.asc()).offset(skip).limit(limit).all()
    return records


@router.get("/diagnostics/")
def run_diagnostics(db: Session = Depends(get_db)):
    """只读诊断历史投苗数据，可重复执行，不修改任何数据。"""
    return diagnose(db, fix=False)


@router.post("/diagnostics/repair/")
def repair_stocking_data(db: Session = Depends(get_db)):
    """按明细重算总重量并写修复留痕；可重复执行，二次运行不会再产生改动。"""
    return diagnose(db, fix=True)


@router.get("/{record_id}/", response_model=StockingRecordResponse)
def get_stocking_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    return record


@router.get("/{record_id}/revisions/", response_model=List[StockingRecordRevisionResponse])
def get_revisions(record_id: int, db: Session = Depends(get_db)):
    record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    root_id = record.root_id or record.id
    from ..models import StockingRecordRevision
    rows = (
        db.query(StockingRecordRevision)
        .filter(StockingRecordRevision.root_record_id == root_id)
        .order_by(StockingRecordRevision.sequence.asc())
        .all()
    )
    return rows


@router.post("/{record_id}/correct/", response_model=StockingRecordResponse)
def correct_stocking_record(
    record_id: int, payload: StockingRecordCorrect, db: Session = Depends(get_db)
):
    """更正投苗事实：旧版本标记 superseded 并保留原值与原因，生成新版本。"""
    old = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not old:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    if old.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"该记录状态为 {old.status}，不是当前有效版本，不能再更正；请基于最新版本操作",
        )

    # 乐观锁：并发更正时后提交者必须基于最新版本
    if payload.expected_revision is not None and payload.expected_revision != old.revision:
        raise HTTPException(
            status_code=409,
            detail=f"版本已变化（期望 v{payload.expected_revision}，当前 v{old.revision}），请刷新后重试",
        )

    _get_batch_or_404(db, payload.batch_id)
    # 旧批次与目标批次任一已锁定，都不允许更正
    locked, why = batch_is_locked(db, old.batch_id)
    if locked:
        raise HTTPException(status_code=409, detail=f"投苗事实已参与周期分析，禁止更正：{why}")
    if payload.batch_id != old.batch_id:
        locked, why = batch_is_locked(db, payload.batch_id)
        if locked:
            raise HTTPException(status_code=409, detail=f"目标批次已锁定，禁止移入：{why}")

    qty, wpu, total = _measure(payload.quantity, payload.weight_per_unit, payload.total_weight)

    before = snapshot_record(old)
    root_id = old.root_id or old.id

    # 原子状态迁移：仅当仍是 active 时才把旧版本置为 superseded。
    # 并发更正时只会有一个事务影响 1 行，另一个得到 0 行 -> 409，绝不产生两个新版本。
    result = db.execute(
        update(StockingRecord)
        .where(StockingRecord.id == record_id, StockingRecord.status == "active")
        .values(status="superseded")
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发更正冲突，该事实已有新版本，请刷新后重试")

    new_record = StockingRecord(
        batch_id=payload.batch_id,
        species=payload.species.strip(),
        quantity=qty,
        source=payload.source,
        batch_number=payload.batch_number,
        weight_per_unit=float(wpu) if wpu is not None else None,
        total_weight=float(total) if total is not None else None,
        notes=payload.notes,
        record_type=payload.record_type,
        status="active",
        revision=old.revision + 1,
        supersedes_id=old.id,
        root_id=root_id,
        correct_reason=payload.reason,
        policy_version=POLICY_VERSION,
    )
    db.add(new_record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发更正冲突，该事实已有新版本，请刷新后重试")

    append_revision(
        db,
        root_id=root_id,
        action="correct",
        record_id=new_record.id,
        before=before,
        after=snapshot_record(new_record),
        reason=payload.reason,
    )
    try:
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(status_code=409, detail="并发更正冲突，请刷新后重试")
    db.refresh(new_record)
    return new_record


@router.post("/{record_id}/void/", response_model=StockingRecordResponse)
def void_stocking_record(
    record_id: int, payload: StockingRecordVoid, db: Session = Depends(get_db)
):
    """撤销投苗记录（软删）：原值保留并记录原因，不参与后续统计。"""
    record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    if record.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"该记录状态为 {record.status}，不能撤销；只有当前有效版本可撤销",
        )

    locked, why = batch_is_locked(db, record.batch_id)
    if locked:
        raise HTTPException(status_code=409, detail=f"投苗事实已参与周期分析，禁止撤销：{why}")

    before = snapshot_record(record)
    root_id = record.root_id or record.id

    # 原子状态迁移，避免并发撤销/更正产生两次副作用
    result = db.execute(
        update(StockingRecord)
        .where(StockingRecord.id == record_id, StockingRecord.status == "active")
        .values(status="void", void_reason=payload.reason)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发操作冲突，请刷新后重试")

    db.refresh(record)
    append_revision(
        db,
        root_id=root_id,
        action="void",
        record_id=record_id,
        before=before,
        after=snapshot_record(record),
        reason=payload.reason,
    )
    try:
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(status_code=409, detail="并发操作冲突，请刷新后重试")
    db.refresh(record)
    return record


def _legacy_write_disabled(action: str, alternative: str):
    raise HTTPException(
        status_code=410,
        detail=(
            f"投苗记录已改为版本化管理，{action} 会直接抹掉/改写已发生的事实，已停用。"
            f"请使用 {alternative}（原值与原因将完整留痕）。"
        ),
    )


@router.put("/{record_id}/", status_code=410, include_in_schema=False)
def legacy_update_disabled(record_id: int):
    _legacy_write_disabled("直接修改(PUT)", f"POST /api/stocking-records/{record_id}/correct/")


@router.delete("/{record_id}/", status_code=410, include_in_schema=False)
def legacy_delete_disabled(record_id: int):
    _legacy_write_disabled("物理删除(DELETE)", f"POST /api/stocking-records/{record_id}/void/")
