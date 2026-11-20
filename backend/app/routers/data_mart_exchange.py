"""数据集市地区归并接口：消费维度交换包重算分组、待映射单量与冲突对照。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.exchange_store import exchange_store
from app.schemas import ActionResult, ReconcilePayload
from app.services.dimension_exchange import to_csv
from app.services.region_reconcile import POLICY_LABELS, region_reconcile_service

router = APIRouter(prefix="/api/data_mart/region", tags=["数据集市地区归并"])

ORDER_COLUMNS = [
    "订单号", "来源", "业务日", "地区代码", "金额", "归并结果", "地区分组",
    "分组代码", "命中版本", "命中方式", "待映射原因", "确认口径",
]
CONFLICT_COLUMNS = [
    "对照类型", "地区代码", "名称", "交换包", "版本A", "版本B", "别名", "重叠起",
    "重叠止", "受影响订单", "业务日", "旧归并", "新归并", "源文件", "源行号", "说明",
]


def _csv_response(filename: str, rows: list[dict[str, Any]], columns: list[str]) -> Response:
    return Response(
        content=to_csv(rows, columns),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/policies")
def list_policies() -> dict[str, Any]:
    """可选确认口径：按原来源生效域，或暂缓重叠期归并。"""
    return {
        "default": "hold_overlap",
        "items": [{"key": key, "label": label} for key, label in POLICY_LABELS.items()],
    }


@router.get("/orders")
def list_orders() -> dict[str, Any]:
    """订单原始清单（用于核对业务日与来源）。"""
    items = list(exchange_store.orders)
    return {"items": items, "total": len(items)}


@router.post("/reconcile", response_model=ActionResult)
def reconcile(payload: ReconcilePayload) -> ActionResult:
    """按确认口径重算地区归并与未映射单量；每笔订单只产出一条结果。"""
    try:
        run = region_reconcile_service.reconcile(payload.policy)
    except ValueError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(
        ok=True,
        message=(
            f"按「{run['policy_label']}」重算完成：归并 {run['mapped_count']} 笔、"
            f"待映射 {run['pending_count']} 笔，共 {run['order_total']} 笔，无重复入账"
        ),
        entry=run,
    )


@router.get("/reconcile")
def get_reconcile(policy: str = Query(default="hold_overlap")) -> dict[str, Any]:
    """读取最近一次重算结果；没有就按该口径现算一次。"""
    if policy not in POLICY_LABELS:
        raise HTTPException(status_code=400, detail=f"未确认口径「{policy}」")
    return region_reconcile_service.latest_run(policy)


@router.get("/reconcile.csv")
def reconcile_csv(policy: str = Query(default="hold_overlap")) -> Response:
    """下载逐笔订单归并结果。"""
    if policy not in POLICY_LABELS:
        raise HTTPException(status_code=400, detail=f"未确认口径「{policy}」")
    rows = region_reconcile_service.orders_csv_rows(policy)
    return _csv_response(f"region_reconcile_{policy}.csv", rows, ORDER_COLUMNS)


@router.get("/conflicts")
def list_conflicts() -> dict[str, Any]:
    """冲突别名、生效期冲突与受影响订单的合并对照。"""
    rows = region_reconcile_service.conflict_csv_rows()
    return {"items": rows, "total": len(rows)}


@router.get("/conflicts.csv")
def conflicts_csv() -> Response:
    """下载冲突与受影响订单对照 CSV。"""
    rows = region_reconcile_service.conflict_csv_rows()
    return _csv_response("region_conflicts.csv", rows, CONFLICT_COLUMNS)
