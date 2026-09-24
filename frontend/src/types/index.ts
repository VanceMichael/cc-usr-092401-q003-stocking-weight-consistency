export interface Pond {
  id: number;
  name: string;
  area: number;
  water_depth: number;
  species?: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Batch {
  id: number;
  batch_number: string;
  pond_id: number;
  species: string;
  stocking_date: string;
  estimated_harvest_date?: string;
  actual_harvest_date?: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface StockingRecord {
  id: number;
  batch_id: number;
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  weight_per_unit?: number;
  /** 总重量（公斤），永远由后端按尾数×每尾克重派生，前端只读 */
  total_weight?: number;
  notes?: string;
  created_at: string;
  status: 'active' | 'voided' | string;
  version: number;
  voided_at?: string;
  voided_reason?: string;
  correction_reason?: string;
  metrics_version?: string;
}

export interface StockingRecordEvent {
  id: number;
  record_id: number;
  event_type: 'created' | 'corrected' | 'voided' | 'repaired' | string;
  event_at: string;
  operator?: string;
  reason?: string;
  previous_value?: Record<string, unknown> | null;
  new_value?: Record<string, unknown> | null;
  from_version?: number;
  to_version?: number;
  metrics_version?: string;
}

export interface StockingTotals {
  quantity: number;
  total_weight_kg: number;
  records_missing_weight: number;
  metrics_version: string;
}

/** 投苗更正请求：必须带版本号与原因。 */
export interface StockingCorrection {
  quantity?: number;
  species?: string;
  weight_per_unit?: number;
  source?: string;
  batch_number?: string;
  notes?: string;
  expected_version: number;
  reason: string;
}

export interface FeedingRecord {
  id: number;
  batch_id: number;
  feeding_date: string;
  feed_type: string;
  feed_quantity: number;
  feeding_time?: string;
  weather?: string;
  water_temperature?: number;
  notes?: string;
  created_at: string;
}

export interface WaterQualityRecord {
  id: number;
  batch_id: number;
  record_date: string;
  record_time?: string;
  water_temperature?: number;
  ph_value?: number;
  dissolved_oxygen?: number;
  ammonia_nitrogen?: number;
  nitrite?: number;
  transparency?: number;
  notes?: string;
  created_at: string;
}

export interface MedicationRecord {
  id: number;
  batch_id: number;
  medication_date: string;
  drug_name: string;
  drug_type?: string;
  dosage?: number;
  dosage_unit: string;
  administration_method?: string;
  purpose?: string;
  manufacturer?: string;
  batch_number?: string;
  notes?: string;
  created_at: string;
}

export interface CostRecord {
  id: number;
  batch_id: number;
  cost_date: string;
  cost_type: string;
  amount: number;
  description?: string;
  quantity?: number;
  unit?: string;
  unit_price?: number;
  notes?: string;
  created_at: string;
}

export interface HarvestSale {
  id: number;
  batch_id: number;
  sale_date: string;
  weight: number;
  /** 出塘均重（克/尾），用于按真实口径反推存活尾数/成活率 */
  weight_per_unit?: number;
  unit_price: number;
  total_amount?: number;
  buyer?: string;
  batch_number?: string;
  quality_grade?: string;
  notes?: string;
  created_at: string;
}

export interface CostSummary {
  feed_cost: number;
  medicine_cost: number;
  labor_cost: number;
  electricity_cost: number;
  other_cost: number;
  total_cost: number;
}

export interface FeedingSummary {
  total_feed_weight: number;
  feeding_count: number;
  avg_daily_feed: number;
}

export interface CultureCycleAnalysis {
  batch_number: string;
  pond_name: string;
  species: string;
  stocking_date: string;
  harvest_date?: string;
  days_cultured?: number;
  initial_quantity: number;
  initial_weight_kg?: number;
  harvest_weight: number;
  harvest_weight_per_unit_g?: number | null;
  estimated_survival_count?: number | null;
  survival_rate: number | null;
  survival_rate_note?: string | null;
  feed_total: number;
  feed_conversion_ratio: number;
  area: number;
  yield_per_mu: number;
  total_cost: number;
  total_revenue: number;
  profit: number;
  metrics_version?: string;
  cost_summary?: CostSummary;
  feeding_summary?: FeedingSummary;
}

export interface ApiResponse<T> {
  data?: T;
  message?: string;
}

export interface StockingRecordTrace {
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  stocking_date?: string;
  weight_per_unit?: number;
  total_weight_kg?: number;
  status: string;
  version: number;
  metrics_version?: string;
}

export interface FeedingRecordTrace {
  feeding_date: string;
  feed_type: string;
  quantity: number;
  unit?: string;
}

export interface WaterQualityRecordTrace {
  record_date: string;
  water_temperature?: number;
  ph_value?: number;
  dissolved_oxygen?: number;
}

export interface MedicationRecordTrace {
  medication_date: string;
  medication_name: string;
  dosage?: number;
  unit?: string;
}

export interface CostRecordTrace {
  cost_date: string;
  cost_type: string;
  amount: number;
  description?: string;
}

export interface HarvestSaleTrace {
  sale_date: string;
  weight: number;
  unit_price: number;
  total_amount?: number;
  buyer?: string;
}

export interface BatchInfo {
  batch_number: string;
  species: string;
  stocking_date: string;
  harvest_date?: string;
  status: string;
  pond_id?: number;
}

export interface PondInfo {
  name?: string;
  area?: number;
  water_depth?: number;
}

export interface BatchTraceability {
  batch: BatchInfo;
  pond_info: PondInfo;
  stocking_records: StockingRecordTrace[];
  feeding_records: FeedingRecordTrace[];
  water_quality_records: WaterQualityRecordTrace[];
  medication_records: MedicationRecordTrace[];
  cost_records: CostRecordTrace[];
  harvest_sales: HarvestSaleTrace[];
}
