"""维度字典交换与数据集市地区归并的回归测试。

覆盖需求里的硬约束：重复上传去重、父子依赖、源文件行号追溯、成员变更只登记一次、
两版生效期重叠、同代码上级更正、两种确认口径下的归并/待映射计数与不重复入账。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.exchange_store import exchange_store
from app.main import app

client = TestClient(app)

OLD_CSV = (
    "维度字段,层级代码,父级代码,生效日期,别名,名称\n"
    "地区,RG-A,,2026-01-01,甲区,甲\n"
    "地区,R-X,RG-A,2026-01-01,X地区,地区X\n"
    "地区,R-Y,RG-A,2026-01-01,,地区Y\n"
)
NEW_CSV = (
    "维度字段,层级代码,父级代码,生效日期,别名,名称\n"
    "地区,RG-B,,2026-04-01,乙区,乙\n"
    "地区,R-X,RG-B,2026-04-01,X地区,地区X\n"
    "地区,R-Y,RG-B,2026-04-01,,地区Y\n"
)


@pytest.fixture(autouse=True)
def reset_store():
    client.post("/api/dimension/exchange/reset", params={"seed_packages": False})
    yield
    client.post("/api/dimension/exchange/reset", params={"seed_packages": True})


def import_payload(version: str, source: str, content: str, start: str, end: str) -> dict:
    return {
        "version": version,
        "source": source,
        "field": "地区",
        "effective_from": start,
        "effective_to": end,
        "volumes": [{"file": f"region_{version}.csv", "content": content}],
    }


def import_old() -> dict:
    return client.post(
        "/api/dimension/exchange/packages",
        json=import_payload("2026.1", "旧来源", OLD_CSV, "2026-01-01", "2026-06-30"),
    ).json()["entry"]["package"]


def import_new() -> dict:
    return client.post(
        "/api/dimension/exchange/packages",
        json=import_payload("2026.2", "新来源", NEW_CSV, "2026-04-01", "2026-12-31"),
    ).json()["entry"]["package"]


def test_duplicate_upload_does_not_add_members_or_changes():
    payload = import_payload("2026.1", "旧来源", OLD_CSV, "2026-01-01", "2026-06-30")
    first = client.post("/api/dimension/exchange/packages", json=payload).json()
    second = client.post("/api/dimension/exchange/packages", json=payload).json()
    assert first["ok"] and not first["entry"]["duplicate"]
    assert second["ok"] and second["entry"]["duplicate"]
    assert second["entry"]["members_created"] == 0
    assert second["entry"]["changes_created"] == 0
    detail = client.get(f"/api/dimension/exchange/packages/{first['entry']['package']['id']}").json()
    assert len(detail["members"]) == 3
    assert len(detail["changes"]) == 3
    packages = client.get("/api/dimension/exchange/packages").json()
    assert packages["total"] == 1
    assert packages["items"][0]["duplicate_submissions"] == 1


def test_members_trace_to_source_file_and_line():
    package = import_old()
    detail = client.get(f"/api/dimension/exchange/packages/{package['id']}").json()
    refs = {member["code"]: (member["source_file"], member["source_line"]) for member in detail["members"]}
    assert refs["RG-A"] == ("region_2026.1.csv", 2)
    assert refs["R-X"] == ("region_2026.1.csv", 3)
    assert refs["R-Y"] == ("region_2026.1.csv", 4)


def test_unmet_parent_and_cycle_edges_are_not_valid_hierarchy():
    bad = (
        "维度字段,层级代码,父级代码,生效日期,别名,名称\n"
        "地区,G1,,2026-01-01,,华东\n"
        "地区,A1,G1,2026-01-01,,A1\n"
        "地区,C1,C2,2026-01-01,,环1\n"
        "地区,C2,C1,2026-01-01,,环2\n"
        "地区,CH,GHOST,2026-01-01,,缺父\n"
    )
    result = client.post(
        "/api/dimension/exchange/packages",
        json=import_payload("V-BAD", "批A", bad, "2026-01-01", "2026-12-31"),
    ).json()["entry"]["package"]
    assert result["member_count"] == 5
    assert result["edge_count"] == 4
    assert result["valid_edge_count"] == 1
    assert result["invalid_edge_count"] == 3
    detail = client.get(f"/api/dimension/exchange/packages/{result['id']}").json()
    status = {member["code"]: member["hierarchy_status"] for member in detail["members"]}
    assert status == {"G1": "有效", "A1": "有效", "C1": "无效", "C2": "无效", "CH": "无效"}


def test_duplicate_codes_across_volumes_are_rejected_once():
    payload = {
        "version": "V-DUP",
        "source": "批A",
        "field": "地区",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "volumes": [
            {"file": "a.csv", "content": "层级代码,父级代码,生效日期,别名\nR1,,2026-01-01,\n"},
            {"file": "b.csv", "content": "层级代码,父级代码,生效日期,别名\nR1,,2026-01-01,\n"},
        ],
    }
    entry = client.post("/api/dimension/exchange/packages", json=payload).json()["entry"]
    assert entry["package"]["member_count"] == 1
    assert any(diag["code"] == "重复层级代码" and diag["file"] == "b.csv" for diag in entry["diagnostics"])


def test_overlap_conflict_and_parent_correction():
    import_old()
    import_new()
    comparison = client.get("/api/dimension/exchange/comparison").json()
    assert comparison["totals"]["effective_conflicts"] == 1
    conflict = comparison["conflicts"][0]
    assert (conflict["old_version"], conflict["new_version"]) == ("2026.1", "2026.2")
    assert (conflict["overlap_from"], conflict["overlap_to"]) == ("2026-04-01", "2026-06-30")
    assert {diff["code"] for diff in conflict["diffs"]} >= {"R-X", "R-Y"}
    row = next(row for row in comparison["rows"] if row["code"] == "R-X")
    assert row["parent_changed"] is True
    assert [(v["parent_code"], v["group_name"]) for v in row["versions"]] == [("RG-A", "甲"), ("RG-B", "乙")]


def test_hierarchy_change_registered_only_once_on_resubmission():
    import_old()
    package = import_new()
    detail = client.get(f"/api/dimension/exchange/packages/{package['id']}").json()
    changes = [change for change in detail["changes"] if change["code"] == "R-X"]
    assert len(changes) == 1
    assert changes[0]["change_type"] == "上级更正"
    assert (changes[0]["old_parent_code"], changes[0]["new_parent_code"]) == ("RG-A", "RG-B")
    assert (changes[0]["source_file"], changes[0]["source_line"]) == ("region_2026.2.csv", 3)
    again = client.post(
        "/api/dimension/exchange/packages",
        json=import_payload("2026.2", "新来源", NEW_CSV, "2026-04-01", "2026-12-31"),
    ).json()["entry"]
    assert again["duplicate"] and again["changes_created"] == 0
    detail = client.get(f"/api/dimension/exchange/packages/{package['id']}").json()
    assert len([c for c in detail["changes"] if c["code"] == "R-X"]) == 1


def test_reconcile_by_source_keeps_order_in_its_own_effective_domain():
    import_old()
    import_new()
    run = client.post("/api/data_mart/region/reconcile", json={"policy": "by_source"}).json()["entry"]
    by_order = {item["order_no"]: item for item in run["orders"]}
    assert by_order["ORD-1001"]["group_name"] == "甲"  # 旧来源订单
    assert by_order["ORD-1002"]["group_name"] == "乙"  # 新来源订单
    groups = {group["group_name"]: group["order_count"] for group in run["groups"]}
    assert groups == {"甲": 3, "乙": 2}
    assert run["pending_count"] == 1
    assert run["pending_orders"][0]["order_no"] == "ORD-1005"
    assert run["same_order_counted_once"] is True
    assert run["mapped_count"] + run["pending_count"] == run["order_total"] == 6


def test_reconcile_hold_overlap_defers_ambiguous_orders():
    import_old()
    import_new()
    run = client.post("/api/data_mart/region/reconcile", json={"policy": "hold_overlap"}).json()["entry"]
    by_order = {item["order_no"]: item for item in run["orders"]}
    assert by_order["ORD-1001"]["status"] == "待映射"
    assert by_order["ORD-1002"]["status"] == "待映射"
    assert "重叠期版本无法唯一确认" in by_order["ORD-1001"]["reason"]
    groups = {group["group_name"]: group["order_count"] for group in run["groups"]}
    assert groups == {"甲": 2, "乙": 1}
    assert run["pending_count"] == 3
    assert run["same_order_counted_once"] is True


def test_conflict_report_lists_overlap_alias_and_affected_orders():
    import_old()
    import_new()
    rows = client.get("/api/data_mart/region/conflicts").json()["items"]
    kinds = {row["对照类型"] for row in rows}
    assert {"生效期冲突", "受影响订单"} <= kinds
    affected = {row["受影响订单"] for row in rows if row["对照类型"] == "受影响订单"}
    assert affected == {"ORD-1001", "ORD-1002"}
    assert all(row["重叠起"] == "2026-04-01" and row["重叠止"] == "2026-06-30" for row in rows if row["对照类型"] == "受影响订单")


def test_same_name_and_reverse_edges_export_then_reimport():
    content = (
        "维度字段,层级代码,父级代码,生效日期,别名,名称\n"
        "地区,P1,,2026-01-01,,同名\n"
        "地区,D1,P1,2026-01-01,,同名\n"
        "地区,E1,E2,2026-01-01,,环1\n"
        "地区,E2,E1,2026-01-01,,环2\n"
    )
    payload = import_payload("V-1", "批A", content, "2026-01-01", "2026-12-31")
    package = client.post("/api/dimension/exchange/packages", json=payload).json()["entry"]["package"]

    detail = client.get(f"/api/dimension/exchange/packages/{package['id']}").json()
    assert {m["code"] for m in detail["members"] if m["name_collision"]} == {"P1", "D1"}

    export = client.get(f"/api/dimension/exchange/packages/{package['id']}/export").json()
    assert len(export["volumes"][0]["rows"]) == 4
    again = client.post("/api/dimension/exchange/packages", json=payload).json()
    assert again["entry"]["duplicate"] is True

    reimport = {**export, "version": "V-3", "source": "批A2"}
    entry = client.post("/api/dimension/exchange/packages", json=reimport).json()["entry"]
    assert entry["package"]["member_count"] == 4
    assert entry["package"]["valid_edge_count"] == 1
    assert entry["package"]["invalid_edge_count"] == 2


def test_reconcile_counts_by_two_business_dates():
    content = (
        "维度字段,层级代码,父级代码,生效日期,别名,名称\n"
        "地区,G,,2026-01-01,,组\n"
        "地区,K1,G,2026-01-01,,子1\n"
        "地区,K2,G,2026-01-01,,子2\n"
    )
    package = client.post(
        "/api/dimension/exchange/packages",
        json=import_payload("V-G", "批A", content, "2026-01-01", "2026-12-31"),
    ).json()["entry"]["package"]
    exchange_store.orders[:] = [
        {"order_no": "T-1", "source": "批A", "business_date": "2026-03-01", "region_code": "K1", "amount": 10},
        {"order_no": "T-2", "source": "批A", "business_date": "2026-09-01", "region_code": "K2", "amount": 20},
        {"order_no": "T-3", "source": "批A", "business_date": "2026-09-01", "region_code": "K3", "amount": 30},
    ]
    run = client.post("/api/data_mart/region/reconcile", json={"policy": "hold_overlap"}).json()["entry"]
    assert run["mapped_count"] == 2
    assert run["pending_count"] == 1
    assert run["groups"] == [
        {"group_code": "G", "group_name": "组", "order_count": 2, "amount": 30.0, "orders": ["T-1", "T-2"]}
    ]
    assert client.get(f"/api/dimension/exchange/packages/{package['id']}").status_code == 200


def test_append_alias_is_idempotent_and_reports_conflict():
    package = import_old()
    first = client.post(
        f"/api/dimension/exchange/packages/{package['id']}/aliases",
        json={"code": "R-X", "alias": "甲区"},
    ).json()
    assert first["ok"] and first["entry"]["code"] == "R-X"
    second = client.post(
        f"/api/dimension/exchange/packages/{package['id']}/aliases",
        json={"code": "R-X", "alias": "甲区"},
    ).json()
    assert second["ok"] and "已存在" in second["message"]
    conflict = client.get("/api/dimension/exchange/alias-conflicts").json()
    assert any(item["alias"] == "甲区" and set(item["codes"]) == {"RG-A", "R-X"} for item in conflict["conflicts"])


def test_alias_same_as_code_is_flagged():
    package = import_old()
    client.post(
        f"/api/dimension/exchange/packages/{package['id']}/aliases",
        json={"code": "RG-A", "alias": "R-Y"},
    )
    report = client.get("/api/dimension/exchange/alias-conflicts").json()
    item = next(conflict for conflict in report["conflicts"] if conflict["alias"] == "R-Y")
    assert item["kind"] == "别名与代码同名"
    assert item["codes"] == ["RG-A"]
    assert item["code_collision"] == "R-Y"
