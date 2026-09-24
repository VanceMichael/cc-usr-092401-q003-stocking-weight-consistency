"""投苗计量口径单元测试：精度、舍入、非法值与派生规则。"""

import math
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

from app.stocking_policy import (  # noqa: E402
    POLICY_VERSION,
    MeasurementError,
    derive_total_weight,
    estimate_survival_quantity,
    normalize_weight_per_unit,
    policy_info,
    survival_rate,
    validate_quantity,
    validate_total_weight,
    weighted_avg_weight_per_unit,
)


class QuantityValidationTest(unittest.TestCase):
    def test_accepts_positive_integer(self):
        self.assertEqual(validate_quantity(100), 100)
        self.assertEqual(validate_number_string("100"), 100)

    def test_rejects_non_positive_or_non_integer(self):
        for bad in (0, -1, -100):
            with self.assertRaises(MeasurementError):
                validate_quantity(bad)
        with self.assertRaises(MeasurementError):
            validate_quantity(1.5)
        with self.assertRaises(MeasurementError):
            validate_quantity(True)

    def test_rejects_non_finite(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "abc"):
            with self.assertRaises(MeasurementError):
                validate_quantity(bad)

    def test_rejects_out_of_range(self):
        with self.assertRaises(MeasurementError):
            validate_quantity(10_000_000_000)


def validate_number_string(s):
    return validate_quantity(s)


class WeightPerUnitValidationTest(unittest.TestCase):
    def test_rounds_half_up_to_centigram(self):
        self.assertEqual(normalize_weight_per_unit("0.125"), Decimal("0.13"))
        self.assertEqual(normalize_weight_per_unit("0.124"), Decimal("0.12"))
        self.assertEqual(normalize_weight_per_unit(2.005), Decimal("2.01"))

    def test_rejects_non_finite_and_non_positive(self):
        for bad in (0, -0.1, float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(MeasurementError):
                normalize_weight_per_unit(bad)

    def test_rejects_unreasonable_range(self):
        with self.assertRaises(MeasurementError):
            normalize_weight_per_unit(50_000)  # 50 公斤/尾，单位录错
        with self.assertRaises(MeasurementError):
            normalize_weight_per_unit(0.001)


class TotalWeightDerivationTest(unittest.TestCase):
    def test_formula_grams_to_kilograms(self):
        # 1000 尾 * 0.5 克 / 1000 = 0.500 公斤
        self.assertEqual(derive_total_weight(1000, Decimal("0.5")), Decimal("0.500"))

    def test_rounding_to_one_gram_half_up(self):
        # 3 尾 * 0.333 克 = 0.999 克 -> 0.001 公斤
        self.assertEqual(derive_total_weight(3, Decimal("0.333")), Decimal("0.001"))
        # 3 尾 * 0.166 克 = 0.498 克 -> 0.000（不足 0.5 克）-> 拒绝，避免出现 0 总重
        with self.assertRaises(MeasurementError):
            derive_total_weight(3, Decimal("0.166"))

    def test_supplied_total_must_match_derived(self):
        with self.assertRaises(MeasurementError):
            validate_total_weight(999, 1000, Decimal("0.5"))
        # 容差内（约 1 克）视为一致，但落库仍用派生日算值
        self.assertEqual(
            validate_total_weight(0.5009, 1000, Decimal("0.5")), Decimal("0.500")
        )
        # 不传总重时直接派生
        self.assertEqual(validate_total_weight(None, 1000, Decimal("0.5")), Decimal("0.500"))

    def test_negative_supplied_total_rejected(self):
        with self.assertRaises(MeasurementError):
            validate_total_weight(-1, 1000, Decimal("0.5"))


class SurvivalEstimationTest(unittest.TestCase):
    def test_uses_actual_weighted_unit_weight(self):
        avg = weighted_avg_weight_per_unit([(1000, Decimal("0.5")), (1000, Decimal("1.5"))])
        self.assertEqual(avg, Decimal("1.00"))
        # 400 公斤 / 1 克 = 400000 尾（旧代码按 0.5 公斤/尾会算成 800 尾）
        qty = estimate_survival_quantity(400, avg)
        self.assertEqual(qty, 400_000)
        self.assertEqual(survival_rate(1200, qty), 100.0)

    def test_floor_keeps_estimate_conservative(self):
        avg = weighted_avg_weight_per_unit([(100, Decimal("10"))])
        # 0.999 公斤 = 999 克 / 10 克 = 99.9 -> 99 尾
        self.assertEqual(estimate_survival_quantity(0.999, avg), 99)

    def test_missing_unit_weight_not_estimable(self):
        self.assertIsNone(estimate_survival_quantity(100, None))
        self.assertIsNone(survival_rate(100, None))

    def test_rate_capped_at_100(self):
        self.assertEqual(survival_rate(10, 1000), 100.0)

    def test_policy_version_exposed(self):
        info = policy_info()
        self.assertEqual(info["version"], POLICY_VERSION)
        self.assertTrue(info["total_weight"]["derived_only"])
        self.assertTrue(math.isfinite(float(info["weight_per_unit"]["max_grams"])))


if __name__ == "__main__":
    unittest.main()
