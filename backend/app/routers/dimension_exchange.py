"""维度字典交换接口：导出与导入分卷包、追加别名、输出版本与别名对照。

路由层只负责取参与把业务异常转成可读结果，层级、冲突与幂等判断都在
app/services/dimension_exchange.py 里。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.exchange_store import exchange_store
from app.schemas import ActionResult, AliasPayload, ExchangePackagePayload
from app.services.dimension_exchange import ExchangeError, dimension_exchange_service, to_csv

router = APIRouter(prefix="/api/dimension/exchange", tags=["维度字典交换"])

COMPARISON_COLUMNS = [
    "层级代码", "名称", "版本", "来源", "交换包", "上级代码", "地区分组", "层级路径",
    "别名", "生效起", "生效止", "层级状态", "层级说明", "生效期冲突", "别名冲突", "源文件", "源行号",
]
ALIAS_COLUMNS = ["别名", "冲突类型", "命中代码", "代码名称", "交换包", "源文件", "源行号", "涉及代码数"]
DICTIONARY_COLUMNS = ["维度字段", "层级代码", "父级代码", "生效日期", "失效日期", "别名", "名称"]


def _csv_response(filename: str, rows: list[dict[str, Any]], columns: list[str]) -> Response:
    return Response(
        content=to_csv(rows, columns),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/packages")
def list_packages() -> dict[str, Any]:
    """列出已接收的交换包：成员数、有效层级边与冲突数。"""
    items = dimension_exchange_service.list_packages()
    return {"items": items, "total": len(items)}


@router.post("/packages", response_model=ActionResult)
def import_package(payload: ExchangePackagePayload) -> ActionResult:
    """接收一个含维度字段、层级代码、父级代码、生效日期与别名的分卷包。

    同一内容指纹重复提交时不再新增成员，也不会重复登记成员变更。
    """
    result = dimension_exchange_service.import_package(payload.model_dump())
    entry = {
        "package": result.get("package"),
        "duplicate": result.get("duplicate", False),
        "members_created": result.get("members_created", 0),
        "changes_created": result.get("changes_created", 0),
        "diagnostics": result.get("diagnostics", []),
    }
    return ActionResult(ok=bool(result.get("ok")), message=str(result.get("message")), entry=entry)


@router.get("/packages/{package_id}")
def get_package(package_id: str) -> dict[str, Any]:
    """读取单个交换包的成员明细、成员变更、别名与冲突。"""
    detail = dimension_exchange_service.get_package(package_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"交换包 {package_id} 不存在")
    return detail


@router.get("/packages/{package_id}/export")
def export_package(package_id: str, format: str = Query(default="json", pattern="^(json|csv)$")) -> Any:
    """按交换包导出可重建字典的 CSV（默认 JSON，可直接回传导入）。"""
    try:
        payload = dimension_exchange_service.export_dictionary(package_id)
    except ExchangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if format == "csv":
        rows = payload["volumes"][0]["rows"]
        return _csv_response(f"dimension_{payload['version']}_{package_id}.csv", rows, DICTIONARY_COLUMNS)
    return payload


@router.get("/dictionary/export")
def export_dictionary(format: str = Query(default="json", pattern="^(json|csv)$")) -> Any:
    """导出本地当前字典：可直接作为下一次交换的输入分卷。"""
    try:
        payload = dimension_exchange_service.export_dictionary()
    except ExchangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if format == "csv":
        rows = payload["volumes"][0]["rows"]
        return _csv_response("dimension_dictionary.csv", rows, DICTIONARY_COLUMNS)
    return payload


@router.get("/comparison")
def comparison() -> dict[str, Any]:
    """版本对照：每个代码在各版里的上级、别名、生效期、层级状态与源文件行号。"""
    return dimension_exchange_service.comparison()


@router.get("/comparison.csv")
def comparison_csv() -> Response:
    """下载版本对照 CSV。"""
    rows = dimension_exchange_service.comparison_csv_rows()
    return _csv_response("dimension_comparison.csv", rows, COMPARISON_COLUMNS)


@router.get("/alias-conflicts")
def alias_conflicts() -> dict[str, Any]:
    """冲突别名清单：同一别名指向多个代码，或别名与代码同名。"""
    return dimension_exchange_service.alias_report()


@router.get("/alias-conflicts.csv")
def alias_conflicts_csv() -> Response:
    """下载冲突别名对照 CSV（含源文件与行号）。"""
    rows = dimension_exchange_service.alias_csv_rows()
    return _csv_response("dimension_alias_conflicts.csv", rows, ALIAS_COLUMNS)


@router.post("/packages/{package_id}/aliases", response_model=ActionResult)
def append_alias(package_id: str, payload: AliasPayload) -> ActionResult:
    """为交换包里的某个地区代码按需追加别名。"""
    try:
        result = dimension_exchange_service.append_alias(package_id, payload.code, payload.alias)
    except ExchangeError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=bool(result.get("ok")), message=str(result.get("message")), entry=result.get("entry"))


@router.post("/reset", response_model=ActionResult)
def reset_exchange(seed_packages: bool = Query(default=True)) -> ActionResult:
    """清空交换包与订单演示数据；用于反复验证导入流程。"""
    dimension_exchange_service.reset(seed_packages=seed_packages)
    return ActionResult(
        ok=True,
        message="交换包已重置" + ("（含旧来源示例包）" if seed_packages else ""),
        entry={"packages": len(exchange_store.packages), "orders": len(exchange_store.orders)},
    )
