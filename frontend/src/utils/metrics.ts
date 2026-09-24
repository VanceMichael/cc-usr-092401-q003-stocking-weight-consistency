/**
 * 投苗计量口径的前端镜像，唯一版本以后端 GET /api/metrics/stocking 为准。
 *
 * 本文件只用于表单即时预览与本地拦截；总重量的权威值永远来自服务端
 * （后端按同一公式派生），因此即使两端浮点实现有差异也不会污染数据。
 */

import api from '../services/api';

export interface MetricsDescriptor {
  version: string;
  units: Record<string, string>;
  precision: {
    weight_per_unit_decimals: number;
    total_weight_decimals: number;
    survival_rate_decimals: number;
    rounding: string;
    grams_per_kilogram: number;
  };
  ranges: {
    quantity: { min: number; max: number };
    weight_per_unit_grams: { min: number; max: number };
    harvest_weight_per_unit_grams: { min: number; max: number };
  };
  tolerance: { total_weight_consistency_kg: number };
  formulas: Record<string, string>;
}

/** 与 backend/app/services/metrics.py 中 METRICS_VERSION 保持同版本的回退值。 */
export const FALLBACK_METRICS: MetricsDescriptor = {
  version: '2026-09-24.1',
  units: {
    quantity: '尾（整数）',
    weight_per_unit: '克/尾',
    total_weight: '公斤（千克，由明细派生）',
  },
  precision: {
    weight_per_unit_decimals: 2,
    total_weight_decimals: 3,
    survival_rate_decimals: 2,
    rounding: 'ROUND_HALF_UP',
    grams_per_kilogram: 1000,
  },
  ranges: {
    quantity: { min: 1, max: 100_000_000 },
    weight_per_unit_grams: { min: 0.01, max: 5000 },
    harvest_weight_per_unit_grams: { min: 0.01, max: 20000 },
  },
  tolerance: { total_weight_consistency_kg: 0.005 },
  formulas: {
    total_weight_kg: 'round(quantity * round(weight_per_unit_g, 2) / 1000, 3)',
  },
};

let cached: MetricsDescriptor | null = null;
let inflight: Promise<MetricsDescriptor> | null = null;

/** 获取后端口径描述符；失败时回退到同版本镜像，不阻塞录入。 */
export function loadMetrics(force = false): Promise<MetricsDescriptor> {
  if (!force && cached) return Promise.resolve(cached);
  if (!force && inflight) return inflight;
  inflight = api
    .get<MetricsDescriptor>('/metrics/stocking')
    .then((res) => {
      cached = res.data;
      return cached;
    })
    .catch(() => {
      cached = FALLBACK_METRICS;
      return cached;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function getCachedMetrics(): MetricsDescriptor {
  return cached ?? FALLBACK_METRICS;
}

/**
 * ROUND_HALF_UP（四舍五入）。基于十进制字符串实现，避开 JS 默认
 * Math.round 在 2.345 这类二进制浮点边界上的误差。
 */
export function roundHalfUp(value: number, decimals: number): number {
  if (!Number.isFinite(value)) return NaN;
  if (decimals < 0) decimals = 0;
  const sign = value < 0 ? -1 : 1;
  const s = Math.abs(value).toFixed(decimals + 8).replace(/\.?0+$/, '');
  const [intPartRaw, fracPartRaw = ''] = s.split('.');
  const frac = fracPartRaw.padEnd(decimals + 1, '0');
  const kept = frac.slice(0, decimals);
  const nextDigit = Number(frac[decimals]);
  let intPart = intPartRaw.replace(/^0+(?=\d)/, '') || '0';
  let keptFrac = kept;
  if (nextDigit >= 5) {
    // 在保留精度上 +1，用整数进位避免浮点
    const keptInt = BigInt(intPart + keptFrac || '0') + 1n;
    const merged = keptInt.toString().padStart(decimals + 1, '0');
    intPart = merged.slice(0, merged.length - decimals);
    keptFrac = merged.slice(merged.length - decimals);
  }
  const result = decimals === 0
    ? Number(`${sign === -1 ? '-' : ''}${intPart}`)
    : Number(`${sign === -1 ? '-' : ''}${intPart}.${keptFrac}`);
  return result;
}

export interface MetricValidation {
  ok: boolean;
  message?: string;
}

export function validateQuantity(raw: number | string): MetricValidation {
  if (typeof raw === 'string') {
    const t = raw.trim();
    if (!/^\d+$/.test(t)) return { ok: false, message: '尾数必须是正整数' };
    raw = Number(t);
  }
  if (!Number.isFinite(raw)) return { ok: false, message: '尾数必须是有限数字' };
  if (!Number.isInteger(raw)) return { ok: false, message: '尾数必须是正整数' };
  const { min, max } = getCachedMetrics().ranges.quantity;
  if (raw < min) return { ok: false, message: '尾数必须大于 0，禁止负数或零' };
  if (raw > max) return { ok: false, message: `尾数超出合理范围（${min}~${max} 尾），请分批录入` };
  return { ok: true };
}

export function validateWeightPerUnit(raw: number | string): MetricValidation {
  if (typeof raw === 'string') {
    const t = raw.trim();
    if (t === '') return { ok: false, message: '请填写每尾克重（克/尾）' };
    raw = Number(t);
  }
  if (!Number.isFinite(raw)) return { ok: false, message: '每尾克重必须是有限数字' };
  if (raw <= 0) return { ok: false, message: '每尾克重必须大于 0，禁止负数或零' };
  const { min, max } = getCachedMetrics().ranges.weight_per_unit_grams;
  if (raw < min || raw > max) {
    return { ok: false, message: `每尾克重超出合理范围（${min}~${max} 克/尾）` };
  }
  return { ok: true };
}

export function validateHarvestWeightPerUnit(raw: number | string | undefined | null): MetricValidation {
  if (raw === undefined || raw === null || raw === '') {
    return { ok: false, message: '请填写出塘均重（克/尾），否则无法计算成活率' };
  }
  if (typeof raw === 'string') raw = Number(raw.trim());
  if (!Number.isFinite(raw)) return { ok: false, message: '出塘均重必须是有限数字' };
  if (raw <= 0) return { ok: false, message: '出塘均重必须大于 0' };
  const { min, max } = getCachedMetrics().ranges.harvest_weight_per_unit_grams;
  if (raw < min || raw > max) {
    return { ok: false, message: `出塘均重超出合理范围（${min}~${max} 克/尾）` };
  }
  return { ok: true };
}

/**
 * 前端预览版派生：total_weight(kg) = round(qty × round(wpu_g,2) / 1000, 3)。
 * 全程整数（厘克）运算，保证克级精度与后端一致。
 */
export function deriveTotalWeightKg(quantity: number, weightPerUnitGrams: number): number | null {
  const q = validateQuantity(quantity);
  const w = validateWeightPerUnit(weightPerUnitGrams);
  if (!q.ok || !w.ok) return null;
  // 先用十进制字符串四舍五入到 0.01 克（与后端 Decimal ROUND_HALF_UP 一致，
  // 避开 2.345*100=234.4999… 的二进制浮点误差），再做整数克级运算。
  const wpuRounded = roundHalfUp(Number(weightPerUnitGrams), 2);
  const wpuCents = BigInt(Math.round(wpuRounded * 100)); // 0.01 克
  const qty = BigInt(quantity);
  const grams = (qty * wpuCents + 50n) / 100n; // 乘积单位为 0.01 克，四舍五入到克
  return Number(grams) / 1000;
}

export function formatKg(value: number | null | undefined, decimals = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return roundHalfUp(value, decimals).toFixed(decimals);
}

export function formatGrams(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return roundHalfUp(value, 2).toFixed(2);
}

/** 生成幂等键：同一表单会话内重复点击提交只会产生一次增量。 */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
