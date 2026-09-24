import axios from 'axios';
import type {
  Pond, Batch, StockingRecord, StockingCorrection, StockingRecordEvent,
  StockingTotals, FeedingRecord, WaterQualityRecord,
  MedicationRecord, CostRecord, HarvestSale, CultureCycleAnalysis,
  CostSummary, FeedingSummary, BatchTraceability
} from '../types';

const API_BASE_URL = '/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export function extractApiError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((d: { msg?: string; message?: string }) =>
        d?.msg || d?.message || '参数有误').join('；');
    }
    if (detail && typeof detail === 'object' && detail.message) {
      return String(detail.message);
    }
    return error.response?.statusText || '请求失败';
  }
  return '网络异常，请稍后重试';
}

export const pondApi = {
  getAll: () => api.get<Pond[]>('/ponds/'),
  getById: (id: number) => api.get<Pond>(`/ponds/${id}/`),
  create: (data: Omit<Pond, 'id' | 'created_at' | 'updated_at'>) => 
    api.post<Pond>('/ponds/', data),
  update: (id: number, data: Partial<Pond>) => 
    api.put<Pond>(`/ponds/${id}/`, data),
  delete: (id: number) => api.delete(`/ponds/${id}/`),
};

export const batchApi = {
  getAll: () => api.get<Batch[]>('/batches/'),
  getById: (id: number) => api.get<Batch>(`/batches/${id}/`),
  getByNumber: (batchNumber: string) => 
    api.get<Batch>(`/batches/by-number/${batchNumber}/`),
  create: (data: Omit<Batch, 'id' | 'created_at' | 'updated_at'>) => 
    api.post<Batch>('/batches/', data),
  update: (id: number, data: Partial<Batch>) => 
    api.put<Batch>(`/batches/${id}/`, data),
  delete: (id: number) => api.delete(`/batches/${id}/`),
};

/** 投苗录入载荷：不含 total_weight（服务端派生），也不含状态/版本字段。 */
export type StockingRecordPayload = {
  batch_id: number;
  species: string;
  quantity: number;
  weight_per_unit: number;
  source?: string;
  batch_number?: string;
  notes?: string;
};

export const stockingRecordApi = {
  getAll: (batchId?: number, includeVoided = false) =>
    api.get<StockingRecord[]>('/stocking-records/', {
      params: {
        batch_id: batchId,
        ...(includeVoided ? { include_voided: true } : {}),
      },
    }),
  getById: (id: number) => api.get<StockingRecord>(`/stocking-records/${id}/`),
  getTotals: (batchId: number) =>
    api.get<StockingTotals>(`/stocking-records/totals/${batchId}/`),
  getEvents: (id: number) =>
    api.get<StockingRecordEvent[]>(`/stocking-records/${id}/events/`),
  create: (data: StockingRecordPayload, idempotencyKey: string) =>
    api.post<StockingRecord>('/stocking-records/', data, {
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
  /** 更正：必须携带 expected_version 与 reason，409 表示版本冲突。 */
  correct: (id: number, data: StockingCorrection) =>
    api.put<StockingRecord>(`/stocking-records/${id}/`, data),
  /** 软撤销：保留原值与原因。 */
  void: (id: number, reason: string) =>
    api.post<StockingRecord>(`/stocking-records/${id}/void/`, { reason }),
};

export const feedingRecordApi = {
  getAll: (batchId?: number) => 
    api.get<FeedingRecord[]>('/feeding-records/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<FeedingRecord>(`/feeding-records/${id}/`),
  create: (data: Omit<FeedingRecord, 'id' | 'created_at'>) => 
    api.post<FeedingRecord>('/feeding-records/', data),
  update: (id: number, data: Partial<FeedingRecord>) => 
    api.put<FeedingRecord>(`/feeding-records/${id}/`, data),
  delete: (id: number) => api.delete(`/feeding-records/${id}/`),
};

export const waterQualityRecordApi = {
  getAll: (batchId?: number) => 
    api.get<WaterQualityRecord[]>('/water-quality-records/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<WaterQualityRecord>(`/water-quality-records/${id}/`),
  create: (data: Omit<WaterQualityRecord, 'id' | 'created_at'>) => 
    api.post<WaterQualityRecord>('/water-quality-records/', data),
  update: (id: number, data: Partial<WaterQualityRecord>) => 
    api.put<WaterQualityRecord>(`/water-quality-records/${id}/`, data),
  delete: (id: number) => api.delete(`/water-quality-records/${id}/`),
};

export const medicationRecordApi = {
  getAll: (batchId?: number) => 
    api.get<MedicationRecord[]>('/medication-records/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<MedicationRecord>(`/medication-records/${id}/`),
  create: (data: Omit<MedicationRecord, 'id' | 'created_at'>) => 
    api.post<MedicationRecord>('/medication-records/', data),
  update: (id: number, data: Partial<MedicationRecord>) => 
    api.put<MedicationRecord>(`/medication-records/${id}/`, data),
  delete: (id: number) => api.delete(`/medication-records/${id}/`),
};

export const costRecordApi = {
  getAll: (batchId?: number, costType?: string) => 
    api.get<CostRecord[]>('/cost-records/', { 
      params: { batch_id: batchId, cost_type: costType } 
    }),
  getById: (id: number) => api.get<CostRecord>(`/cost-records/${id}/`),
  create: (data: Omit<CostRecord, 'id' | 'created_at'>) => 
    api.post<CostRecord>('/cost-records/', data),
  update: (id: number, data: Partial<CostRecord>) => 
    api.put<CostRecord>(`/cost-records/${id}/`, data),
  delete: (id: number) => api.delete(`/cost-records/${id}/`),
};

export const harvestSaleApi = {
  getAll: (batchId?: number) => 
    api.get<HarvestSale[]>('/harvest-sales/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<HarvestSale>(`/harvest-sales/${id}/`),
  create: (data: Omit<HarvestSale, 'id' | 'created_at'>) => 
    api.post<HarvestSale>('/harvest-sales/', data),
  update: (id: number, data: Partial<HarvestSale>) => 
    api.put<HarvestSale>(`/harvest-sales/${id}/`, data),
  delete: (id: number) => api.delete(`/harvest-sales/${id}/`),
};

export const analysisApi = {
  analyzeCycle: (batchId: number) => 
    api.get<CultureCycleAnalysis>(`/analysis/cycle/${batchId}/`),
  batchTraceability: (batchId: number) => 
    api.get<BatchTraceability>(`/analysis/traceability/${batchId}/`),
  traceByBatchNumber: (batchNumber: string) => 
    api.get<BatchTraceability>(`/analysis/trace-by-number/${batchNumber}/`),
};

export default api;
