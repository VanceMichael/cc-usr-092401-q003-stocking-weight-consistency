from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date
from decimal import Decimal, localcontext
from ..database import get_db
from ..models import Batch, Pond, StockingRecord, FeedingRecord, CostRecord, HarvestSale, WaterQualityRecord, MedicationRecord
from ..schemas import CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo
from ..services import stocking as stocking_svc
from ..services.metrics import (
    GRAMS_PER_KILOGRAM, METRICS_VERSION, MetricsError,
    estimate_survival_count, round_half_up,
)

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)


def _harvest_weight_per_unit(db: Session, batch_id: int):
    """按各次出塘重量加权得出塘均重（克/尾）。

    只有登记了 weight_per_unit 的出塘记录参与加权；若全部缺失则返回
    None —— 此时成活率不可计算，绝不再用固定假设值反推。
    """
    rows = db.query(HarvestSale.weight, HarvestSale.weight_per_unit).filter(
        HarvestSale.batch_id == batch_id,
        HarvestSale.weight_per_unit.isnot(None),
    ).all()
    total_weight = Decimal("0")
    weighted = Decimal("0")
    for weight_kg, wpu_g in rows:
        w = Decimal(str(weight_kg))
        u = Decimal(str(wpu_g))
        if not w.is_finite() or w <= 0 or not u.is_finite() or u <= 0:
            continue
        weighted += w * u
        total_weight += w
    if total_weight <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return float(round_half_up(weighted / total_weight, 2))

@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    
    # 投苗口径与列表、追溯共用同一汇总：只统计未撤销的有效投苗记录
    stocking_totals = stocking_svc.batch_totals(db, batch.id)
    initial_quantity = stocking_totals["quantity"]
    initial_weight_kg = stocking_totals["total_weight_kg"]

    harvest_weight = db.query(func.sum(HarvestSale.weight)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0
    
    feed_total = db.query(func.sum(FeedingRecord.feed_quantity)).filter(
        FeedingRecord.batch_id == batch.id
    ).scalar() or 0
    
    total_cost = db.query(func.sum(CostRecord.amount)).filter(
        CostRecord.batch_id == batch.id
    ).scalar() or 0
    
    total_revenue = db.query(func.sum(HarvestSale.total_amount)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0
    
    harvest_date = batch.actual_harvest_date
    days_cultured = None
    if harvest_date:
        days_cultured = (harvest_date - batch.stocking_date).days
    
    # 成活率：仅依据出塘登记的实际均重（克/尾）反推，禁止固定假设值。
    # 未登记出塘均重或没有有效投苗尾数时返回 null 并附说明。
    harvest_wpu_g = _harvest_weight_per_unit(db, batch.id)
    survival_rate = None
    estimated_survival = None
    survival_note = None
    if initial_quantity <= 0:
        survival_note = "没有有效投苗记录，无法计算成活率"
    elif harvest_weight <= 0:
        survival_note = "尚未登记出塘重量，成活率待出塘后计算"
    elif harvest_wpu_g is None:
        survival_note = "出塘记录未填写出塘均重(克/尾)，无法反推存活尾数"
    else:
        try:
            estimated_survival = estimate_survival_count(
                harvest_weight, harvest_wpu_g, initial_quantity
            )
            survival_rate = float(round_half_up(
                Decimal(estimated_survival) / Decimal(initial_quantity) * 100,
                2,
            ))
        except MetricsError as exc:
            survival_note = f"成活率计算失败：{exc}"
    
    feed_conversion_ratio = 0.0
    if harvest_weight > 0 and feed_total > 0:
        feed_conversion_ratio = float(round_half_up(feed_total / harvest_weight, 2))

    yield_per_mu = 0.0
    if pond and pond.area > 0:
        yield_per_mu = float(round_half_up(harvest_weight / pond.area, 2))
    
    profit = total_revenue - total_cost
    
    costs = db.query(
        CostRecord.cost_type,
        func.sum(CostRecord.amount).label('total')
    ).filter(
        CostRecord.batch_id == batch.id
    ).group_by(CostRecord.cost_type).all()
    
    cost_breakdown = {c.cost_type: c.total for c in costs}
    
    known_types = ['feed', 'medicine', 'labor', 'electricity']
    other_cost = sum(
        amount for cost_type, amount in cost_breakdown.items() 
        if cost_type not in known_types
    )
    
    cost_summary_dict = {
        "feed_cost": cost_breakdown.get('feed', 0),
        "medicine_cost": cost_breakdown.get('medicine', 0),
        "labor_cost": cost_breakdown.get('labor', 0),
        "electricity_cost": cost_breakdown.get('electricity', 0),
        "other_cost": other_cost,
        "total_cost": total_cost
    }
    
    feeding_summary_dict = db.query(
        FeedingRecord.feed_type,
        func.sum(FeedingRecord.feed_quantity).label('total_quantity'),
        func.count(FeedingRecord.id).label('feeding_count')
    ).filter(
        FeedingRecord.batch_id == batch.id
    ).group_by(FeedingRecord.feed_type).all()
    
    total_feed_weight = feed_total
    feeding_count = sum(f.feeding_count for f in feeding_summary_dict)
    avg_daily_feed = 0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = total_feed_weight / days_cultured
    
    feeding_summary_result = {
        "total_feed_weight": total_feed_weight,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed
    }
    
    return CultureCycleAnalysis(
        batch_number=batch.batch_number,
        pond_name=pond.name if pond else "未知",
        species=batch.species,
        stocking_date=batch.stocking_date,
        harvest_date=harvest_date,
        days_cultured=days_cultured,
        initial_quantity=initial_quantity,
        initial_weight_kg=initial_weight_kg,
        harvest_weight=harvest_weight,
        harvest_weight_per_unit_g=harvest_wpu_g,
        estimated_survival_count=estimated_survival,
        survival_rate=survival_rate,
        survival_rate_note=survival_note,
        feed_total=feed_total,
        feed_conversion_ratio=feed_conversion_ratio,
        area=pond.area if pond else 0,
        yield_per_mu=yield_per_mu,
        total_cost=total_cost,
        total_revenue=total_revenue,
        profit=profit,
        metrics_version=METRICS_VERSION,
        cost_summary=cost_summary_dict,
        feeding_summary=feeding_summary_result
    )

@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    
    stocking_records = db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch.id
    ).order_by(StockingRecord.created_at, StockingRecord.id).all()
    
    feeding_records = db.query(FeedingRecord).filter(
        FeedingRecord.batch_id == batch.id
    ).all()
    
    water_quality_records = db.query(WaterQualityRecord).filter(
        WaterQualityRecord.batch_id == batch.id
    ).all()
    
    medication_records = db.query(MedicationRecord).filter(
        MedicationRecord.batch_id == batch.id
    ).all()
    
    cost_records = db.query(CostRecord).filter(
        CostRecord.batch_id == batch.id
    ).all()
    
    harvest_sales = db.query(HarvestSale).filter(
        HarvestSale.batch_id == batch.id
    ).all()
    
    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id
        ),
        pond_info=PondInfo(
            name=pond.name if pond else None,
            area=pond.area if pond else None,
            water_depth=pond.water_depth if pond else None
        ),
        stocking_records=[
            {
                "species": r.species,
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if hasattr(r, 'created_at') and r.created_at else None,
                "weight_per_unit": r.weight_per_unit,
                "total_weight_kg": r.total_weight,
                "status": r.status or "active",
                "version": r.version or 1,
                "metrics_version": r.metrics_version,
            } for r in stocking_records
        ],
        feeding_records=[
            {
                "feeding_date": r.feeding_date,
                "feed_type": r.feed_type,
                "quantity": r.feed_quantity,
                "unit": "kg"
            } for r in feeding_records
        ],
        water_quality_records=[
            {
                "record_date": r.record_date,
                "water_temperature": r.water_temperature,
                "ph_value": r.ph_value,
                "dissolved_oxygen": r.dissolved_oxygen
            } for r in water_quality_records
        ],
        medication_records=[
            {
                "medication_date": r.medication_date,
                "medication_name": r.drug_name,
                "dosage": r.dosage,
                "unit": r.dosage_unit
            } for r in medication_records
        ],
        cost_records=[
            {
                "cost_date": r.cost_date,
                "cost_type": r.cost_type,
                "amount": r.amount,
                "description": r.description
            } for r in cost_records
        ],
        harvest_sales=[
            {
                "sale_date": r.sale_date,
                "weight": r.weight,
                "unit_price": r.unit_price,
                "total_amount": r.total_amount,
                "buyer": r.buyer
            } for r in harvest_sales
        ]
    )

@router.get("/trace-by-number/{batch_number}/", response_model=BatchTraceability)
def trace_by_batch_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"批次号 {batch_number} 不存在")
    return batch_traceability(batch.id, db)
