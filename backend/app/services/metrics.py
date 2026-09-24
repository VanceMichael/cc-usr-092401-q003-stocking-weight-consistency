"""投苗计量口径的唯一事实源（single source of truth）。

采购录入、投苗列表、批次追溯、周期分析、历史数据诊断/修复以及前端表单
必须共用本模块定义的单位、精度、舍入与合理范围规则，任何地方都不得再
自行硬编码换算常量（例如成活率分析中曾经固定使用的 0.5 公斤/尾）。

口径版本号 ``METRICS_VERSION`` 会通过 ``GET /api/metrics/stocking`` 暴露，
前端启动时读取并与本地镜像常量比对，不一致时以后端为准，保证各处引用
同一版本。

计量约定
--------
* 尾数 quantity：整数，单位"尾"，不可拆分。
* 每尾克重 weight_per_unit：单位"克/尾"，录入精度 0.01 克（厘克级）。
* 总重量 total_weight：单位"公斤（千克）"，由明细派生，存储/展示精度
  0.001 公斤（即克级），API 不接受客户端直接写入。
* 克与公斤严格按 1 公斤 = 1000 克换算。
* 舍入统一采用四舍五入（ROUND_HALF_UP），避免 Python 默认银行家舍入
  与采购计量习惯不一致。
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Optional

#: 口径版本。调整任何精度/范围规则时必须升版本（日期 + 序号）。
METRICS_VERSION = "2026-09-24.1"

# ---------------------------------------------------------------------------
# 单位换算
# ---------------------------------------------------------------------------
GRAMS_PER_KILOGRAM = 1000

# ---------------------------------------------------------------------------
# 精度（小数位）
# ---------------------------------------------------------------------------
#: 每尾克重保留 2 位小数（0.01 克）。
WEIGHT_PER_UNIT_DECIMALS = 2
#: 总重量（公斤）保留 3 位小数，即精确到克。
TOTAL_WEIGHT_DECIMALS = 3
#: 成活率百分比保留 2 位小数。
SURVIVAL_RATE_DECIMALS = 2

# ---------------------------------------------------------------------------
# 合理范围（用于拒绝明显错误的录入，而不是业务上限）
# ---------------------------------------------------------------------------
#: 单条投苗记录尾数下限/上限（1 尾 ~ 1 亿尾）。
MIN_QUANTITY = 1
MAX_QUANTITY = 100_000_000
#: 每尾克重下限/上限（0.01 克初孵仔鱼 ~ 5000 克大规格鱼种）。
MIN_WEIGHT_PER_UNIT_G = Decimal("0.01")
MAX_WEIGHT_PER_UNIT_G = Decimal("5000")
#: 出塘均重（克/尾）的合理范围，用于成活率反推的入参校验。
MIN_HARVEST_WEIGHT_PER_UNIT_G = Decimal("0.01")
MAX_HARVEST_WEIGHT_PER_UNIT_G = Decimal("20000")

#: 判定"存储总重量与派生总重量相符"的容差（公斤），即 ±5 克。
#: 历史数据可能以更粗精度录入，诊断时容差内视为相符；修复则统一重算
#: 到 :data:`TOTAL_WEIGHT_DECIMALS` 位。
DERIVED_TOLERANCE_KG = Decimal("0.005")


class MetricsError(ValueError):
    """计量口径校验错误，message 为可直接展示给录入人员的中文说明。"""


def _round(value: Decimal, decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def round_half_up(value, decimals: int) -> Decimal:
    """按 ROUND_HALF_UP 把数值舍入到指定小数位。"""
    return _round(Decimal(str(value)), decimals)


def validate_quantity(quantity) -> int:
    """校验尾数：必须是有限正整数且在合理范围内。"""
    if isinstance(quantity, bool):  # bool 是 int 的子类，显式拒绝
        raise MetricsError("尾数必须是正整数")
    if isinstance(quantity, float):
        if not math.isfinite(quantity) or not float(quantity).is_integer():
            raise MetricsError("尾数必须是正整数")
        quantity = int(quantity)
    if not isinstance(quantity, int):
        try:
            quantity = int(str(quantity))
        except (TypeError, ValueError):
            raise MetricsError("尾数必须是正整数")
    if quantity < MIN_QUANTITY:
        raise MetricsError("尾数必须大于 0，禁止录入负数或零")
    if quantity > MAX_QUANTITY:
        raise MetricsError(
            f"尾数 {quantity} 超出合理范围（{MIN_QUANTITY}~{MAX_QUANTITY} 尾），"
            "请分多条记录录入"
        )
    return quantity


def validate_weight_per_unit(weight_g, *, field: str = "每尾克重") -> Decimal:
    """校验每尾克重：有限、正数、范围内，返回规范化后的 Decimal。"""
    if weight_g is None:
        raise MetricsError(f"{field}为必填项（克/尾）")
    if isinstance(weight_g, bool):
        raise MetricsError(f"{field}必须是正数")
    try:
        value = Decimal(str(weight_g))
    except (TypeError, ValueError, ArithmeticError):
        raise MetricsError(f"{field}必须是有限数字")
    if not value.is_finite():
        raise MetricsError(f"{field}必须是有限数字，禁止 NaN/Infinity")
    if value <= 0:
        raise MetricsError(f"{field}必须大于 0，禁止录入负数或零")
    if value < MIN_WEIGHT_PER_UNIT_G or value > MAX_WEIGHT_PER_UNIT_G:
        raise MetricsError(
            f"{field} {value} 克超出合理范围"
            f"（{MIN_WEIGHT_PER_UNIT_G}~{MAX_WEIGHT_PER_UNIT_G} 克/尾）"
        )
    return _round(value, WEIGHT_PER_UNIT_DECIMALS)


def derive_total_weight_kg(quantity, weight_g) -> float:
    """由 尾数 × 每尾克重 派生总重量（公斤）。

    公式::

        total_weight(kg) = round( quantity * round(weight_g, 2) / 1000 , 3)

    这是系统内唯一允许的总重量来源；输入非法时抛 :class:`MetricsError`。
    """
    qty = validate_quantity(quantity)
    unit_g = validate_weight_per_unit(weight_g)
    with localcontext() as ctx:
        ctx.prec = 28
        total_g = Decimal(qty) * unit_g
        total_kg = total_g / Decimal(GRAMS_PER_KILOGRAM)
        return float(_round(total_kg, TOTAL_WEIGHT_DECIMALS))


def weights_consistent(stored_kg, quantity, weight_g, *,
                       tolerance: Decimal = DERIVED_TOLERANCE_KG) -> bool:
    """判断已存储的总重量（公斤）是否与明细派生值相符。"""
    if stored_kg is None:
        return False
    try:
        stored = Decimal(str(stored_kg))
        if not stored.is_finite():
            return False
        derived = Decimal(str(derive_total_weight_kg(quantity, weight_g)))
    except MetricsError:
        return False
    return abs(stored - derived) <= tolerance


def validate_harvest_weight_per_unit(weight_g) -> Decimal:
    """校验出塘均重（克/尾），用于成活率反推。"""
    try:
        value = Decimal(str(weight_g))
    except (TypeError, ValueError, ArithmeticError):
        raise MetricsError("出塘均重必须是有限数字")
    if not value.is_finite() or value <= 0:
        raise MetricsError("出塘均重必须是大于 0 的有限数字")
    if value < MIN_HARVEST_WEIGHT_PER_UNIT_G or value > MAX_HARVEST_WEIGHT_PER_UNIT_G:
        raise MetricsError(
            f"出塘均重 {value} 克超出合理范围"
            f"（{MIN_HARVEST_WEIGHT_PER_UNIT_G}~{MAX_HARVEST_WEIGHT_PER_UNIT_G} 克/尾）"
        )
    return _round(value, WEIGHT_PER_UNIT_DECIMALS)


def estimate_survival_count(harvest_weight_kg, harvest_weight_per_unit_g,
                            initial_quantity: Optional[int] = None) -> int:
    """按出塘重量与出塘均重反推存活尾数。

    :param harvest_weight_kg: 出塘总重量（公斤）
    :param harvest_weight_per_unit_g: 出塘平均每尾重量（克/尾），由出塘
        记录提供；不得再使用任何固定假设值。
    :param initial_quantity: 可选投苗尾数，用于把估算结果夹在
        ``[0, 投苗尾数]`` 区间（重量口径异常时不至于反推出超过投苗量）。
    """
    if harvest_weight_per_unit_g is None:
        raise MetricsError("缺少出塘均重（克/尾），无法反推存活尾数")
    weight_kg_dec = Decimal(str(harvest_weight_kg))
    if not weight_kg_dec.is_finite() or weight_kg_dec < 0:
        raise MetricsError("出塘重量必须是不小于 0 的有限数字")
    unit_g = validate_harvest_weight_per_unit(harvest_weight_per_unit_g)
    with localcontext() as ctx:
        ctx.prec = 28
        total_g = weight_kg_dec * Decimal(GRAMS_PER_KILOGRAM)
        count = _round(total_g / unit_g, 0)
    result = int(count)
    if result < 0:
        result = 0
    if initial_quantity is not None and result > initial_quantity:
        result = initial_quantity
    return result


def survival_rate(harvest_weight_kg, harvest_weight_per_unit_g,
                  initial_quantity) -> Optional[float]:
    """计算成活率百分比；缺出塘均重或投苗尾数时返回 None（不臆造）。"""
    initial_quantity = validate_quantity(initial_quantity)
    if harvest_weight_per_unit_g is None:
        return None
    survived = estimate_survival_count(
        harvest_weight_kg, harvest_weight_per_unit_g, initial_quantity
    )
    rate = (Decimal(survived) / Decimal(initial_quantity)) * Decimal(100)
    return float(_round(rate, SURVIVAL_RATE_DECIMALS))


def metrics_descriptor() -> dict:
    """返回口径描述符，供 /api/metrics/stocking 与前端核对。"""
    return {
        "version": METRICS_VERSION,
        "units": {
            "quantity": "尾（整数）",
            "weight_per_unit": "克/尾",
            "total_weight": "公斤（千克，由明细派生）",
        },
        "precision": {
            "weight_per_unit_decimals": WEIGHT_PER_UNIT_DECIMALS,
            "total_weight_decimals": TOTAL_WEIGHT_DECIMALS,
            "survival_rate_decimals": SURVIVAL_RATE_DECIMALS,
            "rounding": "ROUND_HALF_UP",
            "grams_per_kilogram": GRAMS_PER_KILOGRAM,
        },
        "ranges": {
            "quantity": {"min": MIN_QUANTITY, "max": MAX_QUANTITY},
            "weight_per_unit_grams": {
                "min": float(MIN_WEIGHT_PER_UNIT_G),
                "max": float(MAX_WEIGHT_PER_UNIT_G),
            },
            "harvest_weight_per_unit_grams": {
                "min": float(MIN_HARVEST_WEIGHT_PER_UNIT_G),
                "max": float(MAX_HARVEST_WEIGHT_PER_UNIT_G),
            },
        },
        "tolerance": {
            "total_weight_consistency_kg": float(DERIVED_TOLERANCE_KG),
        },
        "formulas": {
            "total_weight_kg": "round(quantity * round(weight_per_unit_g, 2) / 1000, 3)",
            "survival_count": "round(harvest_weight_kg * 1000 / harvest_weight_per_unit_g)",
            "survival_rate_percent": "round(survival_count / initial_quantity * 100, 2)",
        },
    }
