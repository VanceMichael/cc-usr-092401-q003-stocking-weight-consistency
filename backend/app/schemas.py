from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List
from datetime import date, datetime
import json
import math

class PondBase(BaseModel):
    name: str
    area: float
    water_depth: float
    species: Optional[str] = None
    status: Optional[str] = "active"

class PondCreate(PondBase):
    pass

class PondUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None
    species: Optional[str] = None
    status: Optional[str] = None

class PondResponse(PondBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class BatchBase(BaseModel):
    batch_number: str
    pond_id: int
    species: str
    stocking_date: date
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = "active"

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    pond_id: Optional[int] = None
    species: Optional[str] = None
    stocking_date: Optional[date] = None
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = None

class BatchResponse(BatchBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class StockingRecordBase(BaseModel):
    batch_id: int
    species: str = Field(min_length=1, max_length=100)
    quantity: int = Field(description="尾数(尾)，正整数")
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = Field(
        default=None, description="每尾克重(克/尾)，正数；范围由服务层按口径版本校验"
    )
    # total_weight 故意不在录入模型中开放：总重量只能由明细派生
    notes: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def _quantity_finite(cls, v):
        if v is None:
            return v
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError("尾数必须是整数")
        return v

    @field_validator("weight_per_unit")
    @classmethod
    def _weight_finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("每尾克重必须是有限数字")
        return v


class StockingRecordCreate(StockingRecordBase):
    pass


class StockingRecordUpdate(BaseModel):
    """更正请求：必须携带 expected_version（乐观锁）与 reason（留痕）。"""
    quantity: Optional[int] = None
    species: Optional[str] = Field(default=None, min_length=1, max_length=100)
    weight_per_unit: Optional[float] = None
    source: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None
    expected_version: int = Field(description="更正前记录版本号，用于并发冲突检测")
    reason: str = Field(min_length=1, max_length=500, description="更正原因（必填）")

    @field_validator("quantity")
    @classmethod
    def _quantity_finite(cls, v):
        if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
            raise ValueError("尾数必须是整数")
        return v

    @field_validator("weight_per_unit")
    @classmethod
    def _weight_finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("每尾克重必须是有限数字")
        return v


class StockingRecordVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=500, description="撤销原因（必填）")


class StockingRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime
    status: str
    version: int
    voided_at: Optional[datetime] = None
    voided_reason: Optional[str] = None
    correction_reason: Optional[str] = None
    metrics_version: Optional[str] = None


class StockingRecordEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_id: int
    event_type: str
    event_at: datetime
    operator: Optional[str] = None
    reason: Optional[str] = None
    previous_value: Optional[dict] = None
    new_value: Optional[dict] = None
    from_version: Optional[int] = None
    to_version: Optional[int] = None
    metrics_version: Optional[str] = None

    @field_validator("previous_value", "new_value", mode="before")
    @classmethod
    def _parse_json_snapshot(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class StockingTotals(BaseModel):
    quantity: int
    total_weight_kg: float
    records_missing_weight: int = 0
    metrics_version: str

class FeedingRecordBase(BaseModel):
    batch_id: int
    feeding_date: date
    feed_type: str
    feed_quantity: float
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordCreate(FeedingRecordBase):
    pass

class FeedingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    feeding_date: Optional[date] = None
    feed_type: Optional[str] = None
    feed_quantity: Optional[float] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordResponse(FeedingRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class WaterQualityRecordBase(BaseModel):
    batch_id: int
    record_date: date
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordCreate(WaterQualityRecordBase):
    pass

class WaterQualityRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    record_date: Optional[date] = None
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordResponse(WaterQualityRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class MedicationRecordBase(BaseModel):
    batch_id: int
    medication_date: date
    drug_name: str
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = "kg"
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordCreate(MedicationRecordBase):
    pass

class MedicationRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    medication_date: Optional[date] = None
    drug_name: Optional[str] = None
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = None
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordResponse(MedicationRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class CostRecordBase(BaseModel):
    batch_id: int
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordCreate(CostRecordBase):
    pass

class CostRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    cost_date: Optional[date] = None
    cost_type: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordResponse(CostRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class HarvestSaleBase(BaseModel):
    batch_id: int
    sale_date: date
    weight: float
    weight_per_unit: Optional[float] = Field(
        default=None, description="出塘均重(克/尾)，用于按实际口径反推存活尾数"
    )
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("weight", "unit_price")
    @classmethod
    def _positive_finite(cls, v):
        if not math.isfinite(v) or v <= 0:
            raise ValueError("重量与单价必须是大于 0 的有限数字")
        return v

    @field_validator("weight_per_unit")
    @classmethod
    def _wpu_finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("出塘均重必须是有限数字")
        return v

class HarvestSaleCreate(HarvestSaleBase):
    pass

class HarvestSaleUpdate(BaseModel):
    batch_id: Optional[int] = None
    sale_date: Optional[date] = None
    weight: Optional[float] = None
    weight_per_unit: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("weight", "unit_price")
    @classmethod
    def _positive_finite(cls, v):
        if v is not None and (not math.isfinite(v) or v <= 0):
            raise ValueError("重量与单价必须是大于 0 的有限数字")
        return v

    @field_validator("weight_per_unit")
    @classmethod
    def _wpu_finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("出塘均重必须是有限数字")
        return v

class HarvestSaleResponse(HarvestSaleBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class CostSummaryItem(BaseModel):
    type: str
    amount: float

class FeedingSummaryItem(BaseModel):
    feed_type: str
    total_quantity: float
    feeding_count: int

class CultureCycleAnalysis(BaseModel):
    batch_number: str
    pond_name: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    days_cultured: Optional[int] = None
    initial_quantity: int
    initial_weight_kg: Optional[float] = None
    harvest_weight: float
    harvest_weight_per_unit_g: Optional[float] = None
    estimated_survival_count: Optional[int] = None
    survival_rate: Optional[float] = None
    survival_rate_note: Optional[str] = None
    feed_total: float
    feed_conversion_ratio: float
    area: float
    yield_per_mu: float
    total_cost: float
    total_revenue: float
    profit: float
    metrics_version: Optional[str] = None
    cost_summary: Optional[dict] = None
    feeding_summary: Optional[dict] = None

class StockingRecordTrace(BaseModel):
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    stocking_date: Optional[date] = None
    weight_per_unit: Optional[float] = None
    total_weight_kg: Optional[float] = None
    status: str = "active"
    version: int = 1
    metrics_version: Optional[str] = None

class FeedingRecordTrace(BaseModel):
    feeding_date: date
    feed_type: str
    quantity: float
    unit: Optional[str] = None

class WaterQualityRecordTrace(BaseModel):
    record_date: date
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None

class MedicationRecordTrace(BaseModel):
    medication_date: date
    medication_name: str
    dosage: Optional[float] = None
    unit: Optional[str] = None

class CostRecordTrace(BaseModel):
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None

class HarvestSaleTrace(BaseModel):
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None

class BatchInfo(BaseModel):
    batch_number: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    status: str
    pond_id: Optional[int] = None

class PondInfo(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None

class BatchTraceability(BaseModel):
    batch: BatchInfo
    pond_info: PondInfo
    stocking_records: List[StockingRecordTrace] = []
    feeding_records: List[FeedingRecordTrace] = []
    water_quality_records: List[WaterQualityRecordTrace] = []
    medication_records: List[MedicationRecordTrace] = []
    cost_records: List[CostRecordTrace] = []
    harvest_sales: List[HarvestSaleTrace] = []
