/**
 * 投苗计量口径（前端单一事实源），与后端 app/stocking_policy.py 同版本。
 * 投苗表单、列表、追溯、分析页一律引用本模块，不得在组件内自行换算。
 */

export const POLICY_VERSION = 'stocking-measure-v1';

export const WEIGHT_PER_UNIT_PRECISION_GRAMS = 0.01;
export const TOTAL_WEIGHT_PRECISION_KG = 0.001;
export const MIN_QUANTITY = 1;
export const MAX_QUANTITY = 1_000_000_000;
export const MIN_WEIGHT_PER_UNIT_G = 0.01;
export const MAX_WEIGHT_PER_UNIT_G = 10_000;
/** 后端容差约 1.5 克；前端展示按 1 克精度派生即可 */
export const TOTAL_WEIGHT_TOLERANCE_KG = 0.0015;

export class MeasurementError extends Error {}

/** 拒绝 NaN/Infinity 以及空串等非有限输入 */
export function finiteNumber(value: unknown): number {
  if (value === null || value === undefined || value === '') {
    throw new MeasurementError('不能为空');
  }
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) {
    throw new MeasurementError('必须是有限数值，禁止 NaN/Infinity');
  }
  return n;
}

export function validateQuantity(value: unknown): number {
  const n = finiteNumber(value);
  if (!Number.isInteger(n) || n < MIN_QUANTITY) {
    throw new MeasurementError('尾数必须为大于 0 的正整数，禁止零或负数');
  }
  if (n > MAX_QUANTITY) {
    throw new MeasurementError(`尾数超出合理范围（上限 ${MAX_QUANTITY.toLocaleString()} 尾），请核对单位`);
  }
  return n;
}

/**
 * 把输入归整为"百分之一克"整数（ROUND_HALF_UP），避免浮点误差。
 * 返回 null 表示为空。
 */
function weightPerUnitHundredths(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const raw = typeof value === 'string' ? value.trim() : String(value);
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(raw)) {
    throw new MeasurementError('每尾克重必须是有限数值');
  }
  const negative = raw.startsWith('-');
  const unsigned = raw.replace(/^[+-]/, '');
  const [intPart = '', fracPart = ''] = unsigned.split('.');
  const frac2 = (fracPart + '00').slice(0, 2);
  const rest = fracPart.slice(2);
  let hundredths = (parseInt(intPart || '0', 10) || 0) * 100 + parseInt(frac2 || '0', 10);
  if (rest !== '' && parseInt(rest.charAt(0), 10) >= 5) hundredths += 1;
  if (negative) hundredths = -hundredths;
  return hundredths;
}

/** 校验每尾克重并按 0.01 克 ROUND_HALF_UP 归整（单位：克/尾） */
export function normalizeWeightPerUnit(value: unknown): number | null {
  const hundredths = weightPerUnitHundredths(value);
  if (hundredths === null) return null;
  if (hundredths <= 0) {
    throw new MeasurementError('每尾克重必须大于 0，禁止零或负数');
  }
  const wpu = hundredths / 100;
  if (wpu < MIN_WEIGHT_PER_UNIT_G) {
    throw new MeasurementError(`每尾克重低于最小可记录值 ${MIN_WEIGHT_PER_UNIT_G} 克，请核对单位`);
  }
  if (wpu > MAX_WEIGHT_PER_UNIT_G) {
    throw new MeasurementError(
      `每尾克重 ${wpu} 克超出合理范围（上限 ${MAX_WEIGHT_PER_UNIT_G} 克），请确认录入单位是克而非公斤`,
    );
  }
  return wpu;
}

/**
 * 由有效明细派生总重量（公斤），精度 0.001 公斤（1 克），ROUND_HALF_UP。
 * total_weight(kg) = quantity(尾) * wpu(克/尾) / 1000
 */
export function deriveTotalWeight(quantity: number, weightPerUnitGrams: number): number {
  // wpu 已为两位小数；用百分之一克整数运算，保证四舍五入到 1 克
  const hundredths = Math.round(weightPerUnitGrams * 100);
  const gramsRounded = Math.round((quantity * hundredths) / 100); // 1 克精度（ROUND_HALF_UP）
  return gramsRounded / 1000;
}

export interface MeasuredInputs {
  quantity: number;
  weightPerUnit: number | null;
  totalWeight: number | null;
}

/** 表单/提交统一校验，返回归一化结果；totalWeight 只能由明细派生 */
export function measureInputs(input: {
  quantity: unknown;
  weight_per_unit: unknown;
  total_weight?: unknown;
}): MeasuredInputs {
  const quantity = validateQuantity(input.quantity);
  const wpu = normalizeWeightPerUnit(input.weight_per_unit);
  if (wpu === null) {
    if (input.total_weight !== undefined && input.total_weight !== null && input.total_weight !== '') {
      throw new MeasurementError('未填写每尾克重时，总重量无法由明细派生，请先填写单重');
    }
    return { quantity, weightPerUnit: null, totalWeight: null };
  }
  const derived = deriveTotalWeight(quantity, wpu);
  if (derived <= 0) {
    throw new MeasurementError('派生总重量低于最小记录精度 0.001 公斤，请核对尾数或单重');
  }
  if (
    input.total_weight !== undefined &&
    input.total_weight !== null &&
    input.total_weight !== ''
  ) {
    const claimed = finiteNumber(input.total_weight);
    if (claimed <= 0) {
      throw new MeasurementError('总重量必须大于 0，禁止零或负数');
    }
    if (Math.abs(claimed - derived) > TOTAL_WEIGHT_TOLERANCE_KG) {
      throw new MeasurementError(
        `总重量 ${claimed} 公斤与明细不符：${quantity} 尾 × ${wpu} 克/尾 应为 ${derived.toFixed(3)} 公斤`,
      );
    }
  }
  return { quantity, weightPerUnit: wpu, totalWeight: derived };
}

/** 前端实时预览（不校验手填总重），供只读派生框显示 */
export function previewTotalWeight(quantityInput: unknown, wpuInput: unknown): number | null {
  try {
    const quantity = validateQuantity(quantityInput);
    const wpu = normalizeWeightPerUnit(wpuInput);
    if (wpu === null) return null;
    return deriveTotalWeight(quantity, wpu);
  } catch {
    return null;
  }
}

/** 生成幂等令牌（防重复提交、并发双发） */
export function newClientToken(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `tok-${Date.now()}-${Math.random().toString(36).slice(2, 10)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function formatWeightKg(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  return value.toFixed(3);
}
