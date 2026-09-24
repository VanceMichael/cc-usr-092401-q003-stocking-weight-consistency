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

export type StockingRecordStatus = 'active' | 'superseded' | 'void';
export type StockingRecordType = 'initial' | 'supplement';

export interface StockingRecord {
  id: number;
  batch_id: number;
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  weight_per_unit?: number;
  total_weight?: number;
  notes?: string;
  record_type: StockingRecordType;
  status: StockingRecordStatus;
  revision: number;
  supersedes_id?: number;
  root_id?: number;
  void_reason?: string;
  correct_reason?: string;
  policy_version?: string;
  created_at: string;
  updated_at?: string;
}

/** 创建投苗记录入参；client_token 用于防重复提交 */
export interface StockingRecordCreateInput {
  batch_id: number;
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  weight_per_unit?: number;
  total_weight?: number;
  notes?: string;
  record_type: StockingRecordType;
  client_token?: string;
}

export interface StockingRecordCorrectInput {
  batch_id: number;
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  weight_per_unit?: number;
  total_weight?: number;
  notes?: string;
  record_type: StockingRecordType;
  reason: string;
  expected_revision?: number;
}

export interface StockingRecordRevision {
  id: number;
  root_record_id: number;
  sequence: number;
  action: 'create' | 'correct' | 'void' | 'repair';
  record_id: number;
  before_data?: string;
  after_data?: string;
  reason?: string;
  created_at: string;
}

export interface StockingPolicy {
  version: string;
  weight_per_unit: {
    unit: string;
    precision_grams: string;
    rounding: string;
    min_grams: string;
    max_grams: string;
  };
  total_weight: {
    unit: string;
    precision_kg: string;
    formula: string;
    derived_only: boolean;
  };
  quantity: { unit: string; min: number; max: number };
  survival: { estimate_formula: string; avg_weight_per_unit: string };
}

export interface StockingDiagnosticsIssue {
  record_id: number;
  batch_id: number;
  status: string;
  code: string;
  message: string;
  repairable: boolean;
}

export interface StockingDiagnosticsReport {
  policy_version: string;
  mode: 'diagnose' | 'fix';
  scanned: number;
  issues: StockingDiagnosticsIssue[];
  fixed: Array<{
    record_id: number;
    batch_id: number;
    code: string;
    old_total_weight?: number;
    new_total_weight: number;
    reason?: string;
  }>;
  needs_manual: Array<{ record_id: number; batch_id: number; code: string; message: string }>;
  summary: {
    issue_count: number;
    fixed_count: number;
    needs_manual_count: number;
    clean: boolean;
  };
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
  initial_weight: number;
  avg_weight_per_unit?: number;
  harvest_weight: number;
  survival_quantity?: number;
  survival_rate?: number;
  survival_estimable: boolean;
  survival_note?: string;
  feed_total: number;
  feed_conversion_ratio: number;
  area: number;
  yield_per_mu: number;
  total_cost: number;
  total_revenue: number;
  profit: number;
  policy_version?: string;
  cost_summary?: CostSummary;
  feeding_summary?: FeedingSummary;
}

export interface ApiResponse<T> {
  data?: T;
  message?: string;
}

export interface StockingRecordTrace {
  id?: number;
  species: string;
  quantity: number;
  weight_per_unit?: number;
  total_weight?: number;
  source?: string;
  batch_number?: string;
  record_type?: string;
  status?: string;
  revision?: number;
  stocking_date?: string;
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
