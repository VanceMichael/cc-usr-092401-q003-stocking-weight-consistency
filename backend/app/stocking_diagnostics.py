"""历史投苗数据的可重复诊断与修复。

用法（在 backend/ 目录）::

    python -m app.stocking_diagnostics            # 只诊断，不改数据
    python -m app.stocking_diagnostics --fix      # 按明细重算总重量并写修复留痕

诊断与修复是幂等的：修复只改动"总重量"这一派生字段（必要时归整每尾克重
精度），再次执行不会重复修改、也不会重复追加留痕。
尾数/单重为负数或非有限数等无法安全自动修复的问题列入 needs_manual。
"""

from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import StockingRecord
from . import stocking_policy as policy
from .stocking_policy import (
    MeasurementError,
    POLICY_VERSION,
    TOTAL_WEIGHT_TOLERANCE_KG,
)
from .stocking_service import append_revision, snapshot_record


def _finite(value) -> bool:
    return value is None or (isinstance(value, (int, float)) and math.isfinite(float(value)))


def diagnose(db: Session, fix: bool = False) -> dict:
    """扫描全部投苗版本，返回结构化报告；fix=True 时执行可安全自动修复项。"""
    records = (
        db.query(StockingRecord).order_by(StockingRecord.id.asc()).all()
    )

    report = {
        "policy_version": POLICY_VERSION,
        "mode": "fix" if fix else "diagnose",
        "scanned": len(records),
        "issues": [],
        "fixed": [],
        "needs_manual": [],
        "summary": {},
    }

    for rec in records:
        prefix = f"投苗记录 id={rec.id}（批次 {rec.batch_id}）"
        active = rec.status == "active"

        # 1) 非有限数 / 负数 —— 无法安全自动修复
        fatal = False
        if not _finite(rec.quantity) or (rec.quantity is not None and rec.quantity <= 0):
            report["issues"].append(_issue(rec, "invalid_quantity",
                                           f"{prefix} 尾数非法：{rec.quantity!r}",
                                           repairable=False))
            report["needs_manual"].append(_manual(rec, "invalid_quantity",
                                                  "尾数为空、零、负或非有限数，需人工核实"))
            fatal = True
        if rec.weight_per_unit is not None and (
            not _finite(rec.weight_per_unit) or rec.weight_per_unit <= 0
        ):
            report["issues"].append(_issue(rec, "invalid_weight_per_unit",
                                           f"{prefix} 每尾克重非法：{rec.weight_per_unit!r}",
                                           repairable=False))
            report["needs_manual"].append(_manual(rec, "invalid_weight_per_unit",
                                                  "每尾克重为空外的零、负或非有限数，需人工核实"))
            fatal = True
        if rec.total_weight is not None and (
            not _finite(rec.total_weight) or rec.total_weight < 0
        ):
            if fatal:
                # 明细本身已坏，总重量无法据此重算
                report["issues"].append(_issue(rec, "invalid_total_weight",
                                               f"{prefix} 总重量非法：{rec.total_weight!r}",
                                               repairable=False))
                report["needs_manual"].append(_manual(rec, "invalid_total_weight",
                                                      "总重量非法且明细无法支撑重算"))
            # 明细有效时交给第 4 步按重算处理，避免重复登记

        if fatal:
            continue

        # 2) 缺少每尾克重 —— 无法派生总重量
        if rec.weight_per_unit is None:
            report["issues"].append(_issue(
                rec, "missing_weight_per_unit",
                f"{prefix} 缺少每尾克重，总重量无法由明细派生",
                repairable=False))
            report["needs_manual"].append(_manual(rec, "missing_weight_per_unit",
                                                  "补录每尾克重（克/尾）后才能重算总重量"))
            continue

        # 3) 明细超出合理范围 —— 需人工确认（可能单位录错）
        try:
            quantity = policy.validate_quantity(rec.quantity)
            wpu = policy.normalize_weight_per_unit(rec.weight_per_unit)
            derived = policy.derive_total_weight(quantity, wpu)
        except MeasurementError as exc:
            report["issues"].append(_issue(rec, "out_of_range", f"{prefix} {exc.detail}"))
            report["needs_manual"].append(_manual(rec, "out_of_range", exc.detail))
            continue

        # 4) 总重量缺失或与明细不符（含非有限数）—— 可自动修复
        needs_repair = False
        reason_detail = ""
        if rec.total_weight is None:
            needs_repair = True
            reason_detail = "缺少总重量"
        elif not _finite(rec.total_weight):
            needs_repair = True
            reason_detail = f"总重量非有限数 {rec.total_weight!r}"
        else:
            diff = abs(Decimal(str(rec.total_weight)) - derived)
            if diff > TOTAL_WEIGHT_TOLERANCE_KG:
                needs_repair = True
                reason_detail = (
                    f"总重量 {rec.total_weight} 公斤与明细派生值 {derived} 公斤不符"
                )

        wpu_drift = (
            Decimal(str(rec.weight_per_unit))
            .quantize(Decimal("0.01")) != Decimal(str(rec.weight_per_unit))
        )

        if needs_repair or (active and wpu_drift):
            report["issues"].append(_issue(
                rec, "total_weight_mismatch",
                f"{prefix} {reason_detail or '每尾克重精度超过0.01克'}，"
                f"应为 {derived} 公斤",
                repairable=True))

            if fix and active:
                before = snapshot_record(rec)
                old_total = rec.total_weight
                if wpu_drift:
                    rec.weight_per_unit = float(wpu)
                rec.total_weight = float(derived)
                rec.policy_version = POLICY_VERSION
                db.flush()
                after = snapshot_record(rec)
                append_revision(
                    db,
                    root_id=rec.root_id or rec.id,
                    action="repair",
                    record_id=rec.id,
                    before=before,
                    after=after,
                    reason=(
                        f"历史数据修复：{reason_detail or '归整每尾克重精度'}；"
                        f"按 {rec.quantity} 尾 × {wpu} 克/尾 重算总重量，"
                        f"{old_total} -> {derived} 公斤"
                    ),
                )
                report["fixed"].append({
                    "record_id": rec.id,
                    "batch_id": rec.batch_id,
                    "code": "total_weight_mismatch",
                    "old_total_weight": _safe_float(old_total),
                    "new_total_weight": float(derived),
                    "reason": reason_detail,
                })
            elif fix and not active:
                report["needs_manual"].append(_manual(
                    rec, "total_weight_mismatch",
                    "非当前有效版本（已更正/撤销），保留历史原值不做自动改动"))

    if fix:
        db.commit()

    report["summary"] = {
        "issue_count": len(report["issues"]),
        "fixed_count": len(report["fixed"]),
        "needs_manual_count": len(report["needs_manual"]),
        "clean": len(report["issues"]) == 0,
    }
    return report


def _issue(rec, code: str, message: str, repairable: bool = True) -> dict:
    return {
        "record_id": rec.id,
        "batch_id": rec.batch_id,
        "status": rec.status,
        "code": code,
        "message": message,
        "repairable": repairable,
    }


def _manual(rec, code: str, message: str) -> dict:
    return {"record_id": rec.id, "batch_id": rec.batch_id, "code": code, "message": message}


def _safe_float(value):
    if value is None:
        return None
    return float(value) if math.isfinite(float(value)) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="投苗计量数据诊断/修复")
    parser.add_argument("--fix", action="store_true", help="执行可安全自动修复项并重算总重量")
    args = parser.parse_args()

    # CLI 独立运行（未导入 app.main）时，先确保库结构已迁移到当前版本
    from .migrations_stocking import run_migrations
    run_migrations()

    db = SessionLocal()
    try:
        report = diagnose(db, fix=args.fix)
    finally:
        db.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 存在无法自动修复的问题时退出码为 2，便于运维脚本发现
    return 2 if report["summary"]["needs_manual_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
