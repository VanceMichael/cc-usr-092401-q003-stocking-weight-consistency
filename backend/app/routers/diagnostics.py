"""历史投苗计量数据的诊断/修复 HTTP 入口。

* GET  /api/diagnostics/stocking        只读诊断报告（可重复执行）
* POST /api/diagnostics/stocking/repair 修复，默认 dry_run；
  请求体 {"apply": true} 才写库。修复本身幂等，可重复执行。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..scripts import repair_stocking

router = APIRouter(prefix="/api/diagnostics", tags=["历史数据诊断修复"])


class RepairRequest(BaseModel):
    apply: bool = False


@router.get("/stocking")
def stocking_diagnostics(db: Session = Depends(get_db)):
    return repair_stocking.diagnose(db)


@router.post("/stocking/repair")
def stocking_repair(payload: RepairRequest, db: Session = Depends(get_db)):
    return repair_stocking.repair(db, apply=payload.apply)
