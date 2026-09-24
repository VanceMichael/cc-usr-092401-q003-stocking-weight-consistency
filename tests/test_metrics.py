"""计量口径单元测试：克/公斤/尾的精度、舍入、范围与成活率反推。"""

import unittest
from decimal import Decimal

from backend.app.services import metrics as m


class MetricsTest(unittest.TestCase):

    def test_total_weight_derivation_and_half_up_rounding(self):
        # 1000 尾 × 2.345 克：克重先按 ROUND_HALF_UP 到 2.35，总重 2.350 公斤
        self.assertEqual(m.derive_total_weight_kg(1000, "2.345"), 2.35)
        # 0.335 克 -> 0.34 克；3 尾 = 1.02 克 -> 0.001 公斤（克级）
        self.assertEqual(m.derive_total_weight_kg(3, "0.335"), 0.001)
        # 2.005 克 -> 2.01 克；7 尾 = 14.07 克 -> 0.014 公斤
        self.assertEqual(m.derive_total_weight_kg(7, "2.005"), 0.014)
        # 整数大尾数
        self.assertEqual(m.derive_total_weight_kg(50000, 10), 500.0)

    def test_round_half_up_is_not_bankers(self):
        self.assertEqual(m.round_half_up("2.345", 2), Decimal("2.35"))
        self.assertEqual(m.round_half_up("2.355", 2), Decimal("2.36"))
        self.assertEqual(m.round_half_up("0.0005", 3), Decimal("0.001"))

    def test_quantity_validation(self):
        self.assertEqual(m.validate_quantity(1), 1)
        self.assertEqual(m.validate_quantity("1200"), 1200)
        for bad in (-1, 0, "-5", 1.5, "1.5", float("nan"), float("inf"),
                    10 ** 9, True, None, "abc"):
            with self.assertRaises(m.MetricsError):
                m.validate_quantity(bad)

    def test_weight_per_unit_validation(self):
        self.assertEqual(m.validate_weight_per_unit("2.5"), Decimal("2.50"))
        for bad in (0, -0.1, "-1", float("nan"), float("inf"),
                    float("-inf"), 0.001, 99999, None, "x"):
            with self.assertRaises(m.MetricsError):
                m.validate_weight_per_unit(bad)

    def test_consistency_tolerance(self):
        # 容差 ±5 克：4 克差异视为相符，6 克视为不符
        self.assertTrue(m.weights_consistent(2.354, 1000, 2.35))
        self.assertFalse(m.weights_consistent(2.356, 1000, 2.35))
        self.assertFalse(m.weights_consistent(None, 1000, 2.35))
        self.assertFalse(m.weights_consistent(2.35, 1000, None))

    def test_survival_rate_uses_real_harvest_weight_per_unit(self):
        # 600 公斤 ÷ 600 克/尾 = 1000 尾；投苗 1200 -> 83.33%
        self.assertEqual(m.estimate_survival_count(600, 600, 1200), 1000)
        self.assertEqual(m.survival_rate(600, 600, 1200), 83.33)
        # 反推尾数不得超过投苗尾数（口径异常时夹断）
        self.assertEqual(m.estimate_survival_count(6000, 600, 1200), 1200)
        # 缺少出塘均重时明确返回 None，禁止假设值
        self.assertIsNone(m.survival_rate(600, None, 1200))
        with self.assertRaises(m.MetricsError):
            m.estimate_survival_count(600, None)
        for bad in (0, -1, float("nan"), 999999):
            with self.assertRaises(m.MetricsError):
                m.estimate_survival_count(600, bad)

    def test_descriptor_is_versioned(self):
        d = m.metrics_descriptor()
        self.assertTrue(d["version"])
        self.assertEqual(d["precision"]["rounding"], "ROUND_HALF_UP")
        self.assertEqual(d["precision"]["grams_per_kilogram"], 1000)
        self.assertEqual(d["precision"]["total_weight_decimals"], 3)


if __name__ == "__main__":
    unittest.main()
