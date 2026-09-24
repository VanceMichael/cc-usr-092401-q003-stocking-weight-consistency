"""投苗计量口径（单一事实源）。

投苗列表、批次追溯、成活率分析、前端表单与诊断脚本必须共同引用本模块，
不得在各处自行换算或写死单重。

口径 v1
--------
* 尾数 ``quantity``：正整数，单位"尾"。
* 每尾克重 ``weight_per_unit``：单位"克/尾"，保留 2 位小数（0.01 克），
  使用 ROUND_HALF_UP（四舍五入），取值范围 (0, 10000] 克。
* 总重量 ``total_weight``：单位"公斤"，只能由明细派生：

      total_weight(公斤) = quantity(尾) * weight_per_unit(克/尾) / 1000

  保留 3 位小数（1 克精度，0.001 公斤），ROUND_HALF_UP。
* 所有金额/重量字段禁止负数、零（按字段语义）、NaN、±Infinity。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

#: 口径版本。规则发生变化时必须升版本，前端与 API 返回都会携带该值。
POLICY_VERSION = "stocking-measure-v1"

#: 克 -> 公斤
GRAMS_PER_KILOGRAM = Decimal("1000")

#: 每尾克重精度（0.01 克）
WEIGHT_PER_UNIT_QUANTUM = Decimal("0.01")
#: 总重量精度（0.001 公斤 = 1 克）
TOTAL_WEIGHT_QUANTUM = Decimal("0.001")

#: 合理范围（含上限、不含下限零）
MIN_QUANTITY = 1
MAX_QUANTITY = 1_000_000_000  # 单批投苗 10 亿尾上限，拦截录入错误
MIN_WEIGHT_PER_UNIT_G = Decimal("0.01")     # 0.01 克（虾苗等极小个体也可覆盖）
MAX_WEIGHT_PER_UNIT_G = Decimal("10000")    # 10 公斤/尾，拦截把公斤当克录入
MAX_TOTAL_WEIGHT_KG = Decimal("100000000")  # 单条 1 亿公斤上限

#: 后端接受的总重与派生日算值之间的最大偏差（公斤，约 1 克）
TOTAL_WEIGHT_TOLERANCE_KG = Decimal("0.0015")

#: 成活率展示精度
SURVIVAL_RATE_QUANTUM = Decimal("0.01")


class MeasurementError(ValueError):
    """计量数据违反口径时抛出，detail 面向调用方（可直接展示给用户）。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def to_decimal(value: Any, field: str) -> Decimal:
    """把输入安全转换为有限 Decimal；拒绝 NaN/Infinity 与不可解析值。"""
    if value is None or value == "":
        raise MeasurementError(f"{field}不能为空")
    # bool 是 int 的子类，语义上不是重量
    if isinstance(value, bool):
        raise MeasurementError(f"{field}必须是有限数值")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise MeasurementError(f"{field}必须是有限数值，禁止 NaN/Infinity")
        value = repr(value)  # 用最短往返表示，避免二进制噪声
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MeasurementError(f"{field}必须是有限数值")
    if not d.is_finite():
        raise MeasurementError(f"{field}必须是有限数值，禁止 NaN/Infinity")
    return d


def validate_quantity(value: Any) -> int:
    """校验并返回正整数尾数。"""
    if isinstance(value, bool):
        raise MeasurementError("尾数必须是正整数")
    if isinstance(value, int):
        q = value
    else:
        d = to_decimal(value, "尾数")
        if d != d.to_integral_value():
            raise MeasurementError("尾数必须是正整数")
        q = int(d)
    if q < MIN_QUANTITY:
        raise MeasurementError("尾数必须为大于 0 的正整数，禁止零或负数")
    if q > MAX_QUANTITY:
        raise MeasurementError(f"尾数超出合理范围（上限 {MAX_QUANTITY:,} 尾），请核对单位")
    return q


def normalize_weight_per_unit(value: Any) -> Decimal:
    """校验每尾克重并按 0.01 克、ROUND_HALF_UP 归整。"""
    d = to_decimal(value, "每尾克重")
    if d <= 0:
        raise MeasurementError("每尾克重必须大于 0，禁止零或负数")
    if d < MIN_WEIGHT_PER_UNIT_G:
        raise MeasurementError(
            f"每尾克重低于最小可记录值 {MIN_WEIGHT_PER_UNIT_G} 克，请核对单位"
        )
    if d > MAX_WEIGHT_PER_UNIT_G:
        raise MeasurementError(
            f"每尾克重 {d} 克超出合理范围（上限 {MAX_WEIGHT_PER_UNIT_G} 克），"
            "请确认录入单位是克而非公斤"
        )
    return d.quantize(WEIGHT_PER_UNIT_QUANTUM, rounding=ROUND_HALF_UP)


def derive_total_weight(quantity: int, weight_per_unit_g: Decimal) -> Decimal:
    """由有效明细派生总重量（公斤），保留 3 位小数（1 克），ROUND_HALF_UP。

    >>> derive_total_weight(1000, Decimal('0.5'))
    Decimal('0.500')
    """
    total = (Decimal(quantity) * weight_per_unit_g) / GRAMS_PER_KILOGRAM
    if total <= 0:
        raise MeasurementError("派生总重量必须大于 0")
    rounded = total.quantize(TOTAL_WEIGHT_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise MeasurementError(
            "派生总重量低于最小可记录值 0.001 公斤（1 克），请核对尾数与每尾克重"
        )
    if total > MAX_TOTAL_WEIGHT_KG:
        raise MeasurementError(
            f"派生总重量 {total} 公斤超出合理范围（上限 {MAX_TOTAL_WEIGHT_KG} 公斤）"
        )
    return rounded


def validate_total_weight(
    supplied: Optional[Any],
    quantity: int,
    weight_per_unit_g: Decimal,
) -> Decimal:
    """总重量只能由有效明细派生。

    * 调用方不传总重：直接返回派生日算值；
    * 调用方传了总重：必须是有限数且与派生日算值在 1 克容差内一致，
      否则拒绝（采购录入与系统口径不符时不得落库）。
    """
    derived = derive_total_weight(quantity, weight_per_unit_g)
    if supplied is None or supplied == "":
        return derived
    claimed = to_decimal(supplied, "总重量")
    if claimed <= 0:
        raise MeasurementError("总重量必须大于 0，禁止零或负数")
    if abs(claimed - derived) > TOTAL_WEIGHT_TOLERANCE_KG:
        raise MeasurementError(
            f"总重量 {claimed} 公斤与明细不符：{quantity} 尾 × "
            f"{weight_per_unit_g} 克/尾 应为 {derived} 公斤（总重量只能由明细派生，"
            "请勿手工录入不一致的总重量）"
        )
    return derived


def weighted_avg_weight_per_unit(rows: list[tuple[int, Any]]) -> Optional[Decimal]:
    """按尾数加权计算平均每尾克重。

    ``rows`` 为 ``(quantity, weight_per_unit_g)``；全部缺失单重时返回 None。
    """
    total_q = 0
    total_g = Decimal("0")
    for quantity, wpu in rows:
        if wpu is None:
            continue
        d = wpu if isinstance(wpu, Decimal) else to_decimal(wpu, "每尾克重")
        total_q += int(quantity)
        total_g += Decimal(int(quantity)) * d
    if total_q <= 0:
        return None
    return (total_g / Decimal(total_q)).quantize(
        WEIGHT_PER_UNIT_QUANTUM, rounding=ROUND_HALF_UP
    )


def estimate_survival_quantity(
    harvest_weight_kg: Any,
    avg_weight_per_unit_g: Optional[Decimal],
) -> Optional[int]:
    """由出塘重量与投苗实际加权单重反推存活尾数（尾），向下取整（保守计数）。

    历史代码写死 0.5 公斤/尾是错误口径；这里统一使用投苗明细的实际单重。
    返回 None 表示无法估算（没有出塘重量或缺单重）。
    """
    if harvest_weight_kg is None:
        return None
    w = to_decimal(harvest_weight_kg, "出塘重量")
    if w <= 0:
        return None
    if avg_weight_per_unit_g is None or avg_weight_per_unit_g <= 0:
        return None
    grams = w * GRAMS_PER_KILOGRAM
    return int((grams / avg_weight_per_unit_g).to_integral_value(rounding="ROUND_FLOOR"))


def survival_rate(initial_quantity: int, survival_quantity: Optional[int]) -> Optional[float]:
    """成活率百分比，保留 2 位小数；无法估算时返回 None。"""
    if not survival_quantity or initial_quantity <= 0:
        return None
    rate = Decimal(survival_quantity) * Decimal(100) / Decimal(initial_quantity)
    rate = rate.quantize(SURVIVAL_RATE_QUANTUM, rounding=ROUND_HALF_UP)
    # 估算存活尾数不应超过投苗总尾数
    if rate > Decimal("100"):
        rate = Decimal("100.00")
    return float(rate)


def policy_info() -> dict:
    """返回口径说明，供 /api/stocking-records/policy/ 与前端对齐。"""
    return {
        "version": POLICY_VERSION,
        "weight_per_unit": {
            "unit": "克/尾",
            "precision_grams": "0.01",
            "rounding": "ROUND_HALF_UP",
            "min_grams": str(MIN_WEIGHT_PER_UNIT_G),
            "max_grams": str(MAX_WEIGHT_PER_UNIT_G),
        },
        "total_weight": {
            "unit": "公斤",
            "precision_kg": "0.001",
            "formula": "total_weight_kg = quantity * weight_per_unit_g / 1000",
            "derived_only": True,
        },
        "quantity": {"unit": "尾", "min": MIN_QUANTITY, "max": MAX_QUANTITY},
        "survival": {
            "estimate_formula": "survival_qty = floor(harvest_weight_kg*1000 / avg_weight_per_unit_g)",
            "avg_weight_per_unit": "投苗记录按尾数加权，不再使用固定 0.5 公斤/尾",
        },
    }
