"""计量口径版本与规则发布。

前端（及任何外部客户端）通过 ``GET /api/metrics/stocking`` 获取与后端
完全一致的单位、精度、舍入、范围与派生公式；前端本地只保留同一版本的
镜像用于即时预览，一切以服务端派生为准。
"""

from fastapi import APIRouter

from ..services.metrics import metrics_descriptor

router = APIRouter(prefix="/api/metrics", tags=["计量口径"])


@router.get("/stocking")
def stocking_metrics():
    return metrics_descriptor()
