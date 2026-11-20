"""数据集市地区归并：消费维度交换包，重算分组、待映射单量与冲突对照。

两种确认口径：
- by_source（采用原来源对应的生效域）：按订单来源找该来源、且覆盖业务日的交换包。
- hold_overlap（暂缓重叠期归并）：业务日只被一个版本覆盖才归并；被多个版本覆盖时
  留在待映射，等版本能唯一确认再重算。

无论哪种口径，订单只遍历一次、只产出一条结果，同一笔订单不会被重复入账。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.exchange_store import exchange_store

POLICY_LABELS = {
    "by_source": "按原来源生效域",
    "hold_overlap": "暂缓重叠期归并",
}
DEFAULT_POLICY = "hold_overlap"


def _covers(package: dict[str, Any], business_date: str) -> bool:
    return package["effective_from"] <= business_date <= (package["effective_to"] or "9999-12-31")


def _member_aliases(package_id: str, member: dict[str, Any]) -> list[str]:
    aliases = list(member["aliases"])
    for item in exchange_store.manual_aliases:
        if item["package_id"] == package_id and item["code"] == member["code"] and item["alias"] not in aliases:
            aliases.append(item["alias"])
    return aliases


class RegionReconcileService:
    def packages_for_field(self, field: str = "地区") -> list[dict[str, Any]]:
        return sorted(
            [item for item in exchange_store.packages if item["field"] == field],
            key=lambda item: (item["effective_from"], item["id"]),
        )

    # ------------------------------------------------------------------ 单笔归并
    def _pick_package(
        self, order: dict[str, Any], packages: list[dict[str, Any]], policy: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        business_date = order["business_date"]
        if policy == "by_source":
            candidates = [
                item for item in packages if item["source"] == order["source"] and _covers(item, business_date)
            ]
            if not candidates:
                return None, f"来源「{order['source']}」没有覆盖 {business_date} 的交换包"
            if len(candidates) > 1:
                # 后接收的版本视为更正版本；只在更正来源自身有多版覆盖时采用。
                latest = max(candidates, key=lambda item: (item["effective_from"], item["id"]))
                return latest, None
            return candidates[0], None

        candidates = [item for item in packages if _covers(item, business_date)]
        if not candidates:
            return None, f"没有覆盖 {business_date} 的交换包"
        if len(candidates) > 1:
            versions = "、".join(item["version"] for item in candidates)
            return None, f"重叠期版本无法唯一确认（{versions}）"
        return candidates[0], None

    def _map_member(
        self, package: dict[str, Any], region_code: str
    ) -> tuple[dict[str, Any] | None, str | None, str]:
        members = [
            member for (package_id, _), member in exchange_store.members.items() if package_id == package["id"]
        ]
        exact = next((member for member in members if member["code"] == region_code), None)
        if exact is not None:
            return exact, None, "代码"
        alias_hits = [member for member in members if region_code in _member_aliases(package["id"], member)]
        if len(alias_hits) > 1:
            owners = "、".join(sorted(member["code"] for member in alias_hits))
            return None, f"别名「{region_code}」同时指向 {owners}", "别名"
        if len(alias_hits) == 1:
            return alias_hits[0], None, "别名"
        # 订单里也可能直接写地区名称；同名成员多于一个时同样按歧义处理。
        name_hits = [member for member in members if member["name"] == region_code]
        if len(name_hits) > 1:
            owners = "、".join(sorted(member["code"] for member in name_hits))
            return None, f"地区名称「{region_code}」对应多个代码 {owners}", "名称"
        if len(name_hits) == 1:
            return name_hits[0], None, "名称"
        return None, f"地区代码 {region_code} 未收录", "代码"

    def map_order(
        self, order: dict[str, Any], packages: list[dict[str, Any]], policy: str
    ) -> dict[str, Any]:
        package, package_reason = self._pick_package(order, packages, policy)
        base = {
            "order_no": order["order_no"],
            "source": order["source"],
            "business_date": order["business_date"],
            "region_code": order["region_code"],
            "amount": order["amount"],
        }
        if package is None:
            return {
                **base,
                "status": "待映射",
                "reason": package_reason,
                "package_id": None,
                "version": None,
                "matched_by": None,
                "group_code": None,
                "group_name": None,
                "path_names": [],
            }
        member, member_reason, matched_by = self._map_member(package, order["region_code"])
        if member is None:
            return {
                **base,
                "status": "待映射",
                "reason": member_reason,
                "package_id": package["id"],
                "version": package["version"],
                "matched_by": matched_by,
                "group_code": None,
                "group_name": None,
                "path_names": [],
            }
        if member["hierarchy_status"] != "有效":
            return {
                **base,
                "status": "待映射",
                "reason": f"层级依赖未满足：{member['hierarchy_reason']}",
                "package_id": package["id"],
                "version": package["version"],
                "matched_by": matched_by,
                "group_code": None,
                "group_name": None,
                "path_names": [],
            }
        return {
            **base,
            "status": "已归并",
            "reason": "",
            "package_id": package["id"],
            "version": package["version"],
            "matched_by": matched_by,
            "group_code": member["group_code"],
            "group_name": member["group_name"],
            "path_names": member["path_names"],
        }

    # ------------------------------------------------------------------ 重算
    def reconcile(self, policy: str = DEFAULT_POLICY) -> dict[str, Any]:
        if policy not in POLICY_LABELS:
            raise ValueError(f"未确认口径「{policy}」，可选：{'、'.join(POLICY_LABELS)}")
        packages = self.packages_for_field("地区")
        orders = list(exchange_store.orders)
        details = [self.map_order(order, packages, policy) for order in orders]

        groups: dict[str, dict[str, Any]] = {}
        pending_orders: list[dict[str, Any]] = []
        for item in details:
            if item["status"] == "已归并":
                bucket = groups.setdefault(
                    item["group_code"],
                    {
                        "group_code": item["group_code"],
                        "group_name": item["group_name"],
                        "order_count": 0,
                        "amount": 0.0,
                        "orders": [],
                    },
                )
                bucket["order_count"] += 1
                bucket["amount"] = round(bucket["amount"] + float(item["amount"]), 2)
                bucket["orders"].append(item["order_no"])
            else:
                pending_orders.append(item)

        overlaps = [
            conflict
            for conflict in self._conflicts()
            if conflict["field"] == "地区"
        ]
        run = {
            "run_id": exchange_store.next_run_id(),
            "policy": policy,
            "policy_label": POLICY_LABELS[policy],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "package_ids": [item["id"] for item in packages],
            "order_total": len(details),
            "mapped_count": len(details) - len(pending_orders),
            "pending_count": len(pending_orders),
            "mapped_amount": round(sum(item["amount"] for item in details if item["status"] == "已归并"), 2),
            "pending_amount": round(sum(item["amount"] for item in pending_orders), 2),
            "groups": sorted(groups.values(), key=lambda item: (-item["order_count"], item["group_code"])),
            "pending_orders": pending_orders,
            "orders": details,
            "overlaps": overlaps,
            "same_order_counted_once": len({item["order_no"] for item in details}) == len(details),
        }
        exchange_store.reconcile_runs[run["run_id"]] = run
        return run

    def latest_run(self, policy: str = DEFAULT_POLICY) -> dict[str, Any]:
        for run in reversed(list(exchange_store.reconcile_runs.values())):
            if run["policy"] == policy:
                return run
        return self.reconcile(policy)

    # ------------------------------------------------------------------ 冲突与受影响订单
    def _conflicts(self) -> list[dict[str, Any]]:
        from app.services.dimension_exchange import dimension_exchange_service

        conflicts = dimension_exchange_service.conflicts()
        result: list[dict[str, Any]] = []
        for conflict in conflicts:
            old_package = next(p for p in exchange_store.packages if p["id"] == conflict["old_package_id"])
            new_package = next(p for p in exchange_store.packages if p["id"] == conflict["new_package_id"])
            diff_codes = {diff["code"] for diff in conflict["diffs"]}
            affected = [
                {
                    "order_no": order["order_no"],
                    "source": order["source"],
                    "business_date": order["business_date"],
                    "region_code": order["region_code"],
                    "amount": order["amount"],
                    "in_overlap": conflict["overlap_from"] <= order["business_date"] <= conflict["overlap_to"],
                    "old_group_name": self._group_in(old_package, order["region_code"]),
                    "new_group_name": self._group_in(new_package, order["region_code"]),
                    "touched_by_diff": order["region_code"] in diff_codes,
                }
                for order in exchange_store.orders
                if conflict["overlap_from"] <= order["business_date"] <= conflict["overlap_to"]
                and order["region_code"] in diff_codes
            ]
            result.append(
                {
                    **conflict,
                    "field": old_package["field"],
                    "affected_orders": affected,
                    "affected_count": len(affected),
                }
            )
        return result

    def _group_in(self, package: dict[str, Any], code: str) -> str | None:
        member = exchange_store.members.get((package["id"], code))
        if member is None or member["hierarchy_status"] != "有效":
            return None
        return member["group_name"]

    # ------------------------------------------------------------------ 下载对照
    def orders_csv_rows(self, policy: str = DEFAULT_POLICY) -> list[dict[str, Any]]:
        run = self.latest_run(policy)
        return [
            {
                "订单号": item["order_no"],
                "来源": item["source"],
                "业务日": item["business_date"],
                "地区代码": item["region_code"],
                "金额": item["amount"],
                "归并结果": item["status"],
                "地区分组": item["group_name"] or "",
                "分组代码": item["group_code"] or "",
                "命中版本": item["version"] or "",
                "命中方式": item["matched_by"] or "",
                "待映射原因": item["reason"],
                "确认口径": run["policy_label"],
            }
            for item in run["orders"]
        ]

    def conflict_csv_rows(self) -> list[dict[str, Any]]:
        """把冲突别名、生效期冲突、受影响订单摊平成一张可筛选的对照表。"""
        from app.services.dimension_exchange import dimension_exchange_service

        rows: list[dict[str, Any]] = []
        for conflict in dimension_exchange_service.alias_report()["conflicts"]:
            for owner in conflict["owners"]:
                rows.append(
                    {
                        "对照类型": "冲突别名",
                        "地区代码": owner["code"],
                        "名称": owner["name"],
                        "交换包": owner["package_id"],
                        "版本A": "",
                        "版本B": "",
                        "别名": conflict["alias"],
                        "重叠起": "",
                        "重叠止": "",
                        "受影响订单": "",
                        "业务日": "",
                        "旧归并": "",
                        "新归并": "",
                        "源文件": owner["source_file"],
                        "源行号": owner["source_line"],
                        "说明": conflict["kind"],
                    }
                )
        for conflict in self._conflicts():
            for diff in conflict["diffs"]:
                rows.append(
                    {
                        "对照类型": "生效期冲突",
                        "地区代码": diff["code"],
                        "名称": diff.get("new_name") or diff.get("old_name") or "",
                        "交换包": f"{conflict['old_package_id']} / {conflict['new_package_id']}",
                        "版本A": conflict["old_version"],
                        "版本B": conflict["new_version"],
                        "别名": "",
                        "重叠起": conflict["overlap_from"],
                        "重叠止": conflict["overlap_to"],
                        "受影响订单": "",
                        "业务日": "",
                        "旧归并": diff.get("old_group_name") or "",
                        "新归并": diff.get("new_group_name") or "",
                        "源文件": "",
                        "源行号": "",
                        "说明": diff["kind"],
                    }
                )
            for order in conflict["affected_orders"]:
                rows.append(
                    {
                        "对照类型": "受影响订单",
                        "地区代码": order["region_code"],
                        "名称": "",
                        "交换包": f"{conflict['old_package_id']} / {conflict['new_package_id']}",
                        "版本A": conflict["old_version"],
                        "版本B": conflict["new_version"],
                        "别名": "",
                        "重叠起": conflict["overlap_from"],
                        "重叠止": conflict["overlap_to"],
                        "受影响订单": order["order_no"],
                        "业务日": order["business_date"],
                        "旧归并": order["old_group_name"] or "待映射",
                        "新归并": order["new_group_name"] or "待映射",
                        "源文件": "",
                        "源行号": "",
                        "说明": f"来源 {order['source']}，金额 {order['amount']}",
                    }
                )
        return rows


region_reconcile_service = RegionReconcileService()
