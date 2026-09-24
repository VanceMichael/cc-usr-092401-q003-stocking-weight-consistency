"""历史投苗数据的可重复诊断与修复。

用法（仓库根目录）::

    # 只诊断，不写库
    python -m backend.app.scripts.repair_stocking
    # 应用修复（修复动作幂等，可重复执行，二次执行零变更）
    python -m backend.app.scripts.repair_stocking --apply

也可通过 API 调用：

* ``GET  /api/diagnostics/stocking``         只读诊断报告
* ``POST /api/diagnostics/stocking/repair``  默认 dry_run，{"apply": true} 落库

修复原则
--------
#. 只做**确定性**修复：由有效明细重新派生总重量、规范整数尾数、回填
   status/version/口径版本；
#. 绝不臆造数据：缺少每尾克重、尾数非法等无法安全派生的记录只报告、
   不修改，由人工通过"补录/冲销记录"处理；
#. 每次实际修复写一条 ``repaired`` 审计事件（原值/新值/原因/口径版本），
   重复执行时因为问题已消失而不会再产生事件；
#. 已撤销（voided）记录属于保留事实，诊断覆盖但不自动改写。
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models import HarvestSale, StockingRecord
from ..services import stocking as svc
from ..services.metrics import (
    METRICS_VERSION, WEIGHT_PER_UNIT_DECIMALS,
    derive_total_weight_kg, round_half_up, validate_harvest_weight_per_unit,
    weights_consistent,
)

REPAIR_REASON = "历史数据口径修复：按统一计量口径重新派生总重量/回填口径版本"


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------

def _is_finite_number(value) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(str(value)))
    except (TypeError, ValueError):
        return False


def _diagnose_record(r: StockingRecord) -> list[dict]:
    """返回单条投苗记录的问题清单（voided 记录也检查，但标记只读）。"""
    issues: list[dict] = []
    locked_note = "（记录已撤销，仅报告不自动修复）" if r.status == "voided" else ""

    # --- 尾数 ---
    qty_ok = False
    qty_int: Optional[int] = None
    if r.quantity is None:
        issues.append({"code": "MISSING_QUANTITY", "detail": "缺少尾数" + locked_note,
                       "auto_repairable": False})
    elif not _is_finite_number(r.quantity):
        issues.append({"code": "NON_FINITE_QUANTITY",
                       "detail": f"尾数非有限数字: {r.quantity!r}{locked_note}",
                       "auto_repairable": False})
    else:
        qf = float(r.quantity)
        if qf <= 0:
            issues.append({"code": "NON_POSITIVE_QUANTITY",
                           "detail": f"尾数必须为正整数: {r.quantity}{locked_note}",
                           "auto_repairable": False})
        elif qf > 100_000_000:
            issues.append({"code": "QUANTITY_OUT_OF_RANGE",
                           "detail": f"尾数超出合理范围: {int(qf)}{locked_note}",
                           "auto_repairable": False})
        elif not qf.is_integer():
            issues.append({"code": "FRACTIONAL_QUANTITY",
                           "detail": f"尾数必须是整数: {r.quantity}{locked_note}",
                           "auto_repairable": False})
        else:
            qty_int = int(qf)
            if not isinstance(r.quantity, int):
                # 可安全规范化（例如老库中存成 1000.0）
                issues.append({"code": "QUANTITY_NOT_INTEGER_TYPE",
                               "detail": f"尾数以非整数类型存储: {r.quantity!r}{locked_note}",
                               "auto_repairable": r.status != "voided"})
            qty_ok = True

    # --- 每尾克重 ---
    wpu_ok = False
    if r.weight_per_unit is None:
        issues.append({"code": "MISSING_WEIGHT_PER_UNIT",
                       "detail": "缺少每尾克重，无法派生/核对总重量" + locked_note,
                       "auto_repairable": False})
    elif not _is_finite_number(r.weight_per_unit):
        issues.append({"code": "NON_FINITE_WEIGHT_PER_UNIT",
                       "detail": f"每尾克重非有限数字: {r.weight_per_unit!r}{locked_note}",
                       "auto_repairable": False})
    else:
        wpu_f = float(r.weight_per_unit)
        if wpu_f <= 0:
            issues.append({"code": "NON_POSITIVE_WEIGHT_PER_UNIT",
                           "detail": f"每尾克重必须为正数: {wpu_f}{locked_note}",
                           "auto_repairable": False})
        elif wpu_f < 0.01 or wpu_f > 5000:
            issues.append({"code": "WEIGHT_PER_UNIT_OUT_OF_RANGE",
                           "detail": f"每尾克重超出合理范围: {wpu_f}{locked_note}",
                           "auto_repairable": False})
        else:
            wpu_ok = True

    # --- 总重量 ---
    if r.total_weight is not None and not _is_finite_number(r.total_weight):
        issues.append({"code": "NON_FINITE_TOTAL_WEIGHT",
                       "detail": f"总重量非有限数字: {r.total_weight!r}{locked_note}",
                       "auto_repairable": qty_ok and wpu_ok and r.status != "voided"})
    elif qty_ok and wpu_ok:
        if not weights_consistent(r.total_weight, qty_int, r.weight_per_unit):
            issues.append({
                "code": "TOTAL_WEIGHT_MISMATCH",
                "detail": (
                    f"总重量 {r.total_weight} 公斤与明细派生值 "
                    f"{derive_total_weight_kg(qty_int, r.weight_per_unit)} 公斤不符"
                    f"{locked_note}"
                ),
                "current": r.total_weight,
                "derived": derive_total_weight_kg(qty_int, r.weight_per_unit),
                "auto_repairable": r.status != "voided",
            })
    elif r.total_weight is None and not (qty_ok and wpu_ok):
        issues.append({"code": "UNVERIFIABLE_TOTAL_WEIGHT",
                       "detail": "总重量缺失且明细不完整，无法派生" + locked_note,
                       "auto_repairable": False})

    # --- 口径元数据回填 ---
    if r.status not in ("active", "voided"):
        issues.append({"code": "INVALID_STATUS",
                       "detail": f"状态异常: {r.status!r}{locked_note}",
                       "auto_repairable": False})
    if not isinstance(r.version, int) or r.version < 1:
        issues.append({"code": "MISSING_VERSION",
                       "detail": "缺少更正版本号" + locked_note,
                       "auto_repairable": r.status != "voided"})
    if not r.metrics_version:
        issues.append({"code": "MISSING_METRICS_VERSION",
                       "detail": "缺少计量口径版本标记" + locked_note,
                       "auto_repairable": r.status != "voided"})

    for item in issues:
        item["record_id"] = r.id
        item["batch_id"] = r.batch_id
        item["status"] = r.status
    return issues


def diagnose(db: Session) -> dict:
    """产出诊断报告；纯只读，不修改数据库。"""
    records = db.query(StockingRecord).order_by(StockingRecord.id).all()
    issues: list[dict] = []
    for r in records:
        issues.extend(_diagnose_record(r))

    # 出塘均重信息性检查：它决定成活率能否按真实口径计算
    harvest_warnings: list[dict] = []
    for s in db.query(HarvestSale).order_by(HarvestSale.id).all():
        if s.weight_per_unit is None:
            harvest_warnings.append({
                "harvest_sale_id": s.id, "batch_id": s.batch_id,
                "code": "MISSING_HARVEST_WEIGHT_PER_UNIT",
                "detail": "出塘记录未填写出塘均重(克/尾)，该批次成活率暂不可计算",
            })
        elif not _is_finite_number(s.weight_per_unit) or float(s.weight_per_unit) <= 0:
            harvest_warnings.append({
                "harvest_sale_id": s.id, "batch_id": s.batch_id,
                "code": "INVALID_HARVEST_WEIGHT_PER_UNIT",
                "detail": f"出塘均重非法: {s.weight_per_unit!r}，请人工更正",
            })
        else:
            try:
                validate_harvest_weight_per_unit(s.weight_per_unit)
            except Exception as exc:  # noqa: BLE001 - 诊断只需收集信息
                harvest_warnings.append({
                    "harvest_sale_id": s.id, "batch_id": s.batch_id,
                    "code": "HARVEST_WEIGHT_PER_UNIT_OUT_OF_RANGE",
                    "detail": str(exc),
                })

    return {
        "metrics_version": METRICS_VERSION,
        "stocking_records_checked": len(records),
        "issue_count": len(issues),
        "issues": issues,
        "harvest_warnings": harvest_warnings,
        "auto_repairable_count": sum(1 for i in issues if i.get("auto_repairable")),
        "manual_review_count": sum(1 for i in issues if not i.get("auto_repairable")),
    }


# ---------------------------------------------------------------------------
# 修复（幂等）
# ---------------------------------------------------------------------------

def repair(db: Session, *, apply: bool = False) -> dict:
    """按诊断结果执行确定性修复。

    :param apply: False 时为 dry-run，只返回计划动作；True 时落库并写
        审计事件。整个过程对每条记录至多产生一次实际变更，因此重复
        执行时第二次报告为零动作。
    """
    records = db.query(StockingRecord).order_by(StockingRecord.id).all()
    actions: list[dict] = []
    unresolved: list[dict] = []

    for r in records:
        issues = _diagnose_record(r)
        if not issues:
            continue
        codes = {i["code"] for i in issues}
        if r.status == "voided":
            unresolved.extend(issues)
            continue

        changes: dict = {}

        # 尾数整数化（仅当确为整数值且范围合法）
        if "QUANTITY_NOT_INTEGER_TYPE" in codes:
            qf = float(r.quantity)
            if qf.is_integer() and 0 < qf <= 100_000_000:
                changes["quantity"] = int(qf)

        # 总重量重新派生（需要有效尾数 + 有效每尾克重）
        need_rederive = bool(codes & {
            "NON_FINITE_TOTAL_WEIGHT", "TOTAL_WEIGHT_MISMATCH",
        })
        target_qty = changes.get("quantity", r.quantity)
        if need_rederive:
            try:
                changes["total_weight"] = derive_total_weight_kg(
                    target_qty, r.weight_per_unit
                )
                # 同时把每尾克重规范到口径精度
                changes["weight_per_unit"] = float(
                    round_half_up(r.weight_per_unit, WEIGHT_PER_UNIT_DECIMALS)
                )
            except Exception:  # noqa: BLE001 - 明细非法时交人工处理
                need_rederive = False

        # 元数据回填
        if "MISSING_VERSION" in codes:
            changes["version"] = 1
        if "MISSING_METRICS_VERSION" in codes:
            changes["metrics_version"] = METRICS_VERSION

        fixed_codes = {i["code"] for i in issues if i.get("auto_repairable")}
        if changes and fixed_codes & ({
            "QUANTITY_NOT_INTEGER_TYPE", "NON_FINITE_TOTAL_WEIGHT",
            "TOTAL_WEIGHT_MISMATCH", "MISSING_VERSION",
            "MISSING_METRICS_VERSION",
        }):
            actions.append({
                "record_id": r.id,
                "batch_id": r.batch_id,
                "issues": sorted(fixed_codes),
                "previous": svc._snapshot(r),
                "changes": changes,
            })
        # 本条记录上无法自动修复的问题
        for i in issues:
            if not i.get("auto_repairable"):
                unresolved.append(i)

    if apply and actions:
        for action in actions:
            record = db.get(StockingRecord, action["record_id"])
            previous = svc._snapshot(record)
            new_version = (record.version if isinstance(record.version, int)
                           and record.version >= 1 else 1)
            # version 仅在实际业务字段更正时递增；纯口径标记回填不改变事实
            business_fields = {"quantity", "weight_per_unit", "total_weight"}
            if business_fields & set(action["changes"].keys()):
                new_version = (record.version or 1) + 1
                action["changes"]["version"] = new_version
            for key, value in action["changes"].items():
                setattr(record, key, value)
            svc.append_event(
                db, record, "repaired", previous, svc._snapshot(record),
                reason=REPAIR_REASON, operator="repair_stocking",
                from_version=previous["version"], to_version=record.version,
            )
        db.commit()

    return {
        "metrics_version": METRICS_VERSION,
        "mode": "apply" if apply else "dry_run",
        "records_checked": len(records),
        "action_count": len(actions),
        "actions": actions,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "note": (
            "已按统一口径派生/回填并写 repaired 审计事件；"
            "未解决项需人工核对，可通过补录或冲销投苗记录处理"
        ),
    }


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys

    from ..database import SessionLocal, engine, Base
    from ..migrations import run_migrations

    parser = argparse.ArgumentParser(description="投苗计量数据诊断与修复")
    parser.add_argument("--apply", action="store_true", help="实际写库修复（默认只诊断）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    db = SessionLocal()
    try:
        if args.apply:
            report = repair(db, apply=True)
        else:
            diag = diagnose(db)
            report = repair(db, apply=False)
            report["diagnostics"] = diag
    finally:
        db.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        mode = "修复" if args.apply else "诊断(dry-run)"
        print(f"[投苗{mode}] 口径版本 {report['metrics_version']}")
        if "diagnostics" in report:
            d = report["diagnostics"]
            print(f"检查记录 {d['stocking_records_checked']} 条，问题 {d['issue_count']} 个；"
                  f"可自动修复 {d['auto_repairable_count']}，需人工 {d['manual_review_count']}")
            for issue in d["issues"]:
                print(f"  - 记录#{issue['record_id']} {issue['code']}: {issue['detail']}")
            for w in d["harvest_warnings"]:
                print(f"  - 出塘#{w['harvest_sale_id']} {w['code']}: {w['detail']}")
        print(f"计划/执行修复动作 {report['action_count']} 个，"
              f"未解决 {report['unresolved_count']} 个")
        for action in report["actions"]:
            print(f"  - 记录#{action['record_id']} {action['issues']} -> {action['changes']}")
        if args.apply and report["action_count"] == 0:
            print("数据已符合统一口径，重复执行无变更。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
