"""维度字典交换：分卷包解析、层级校验、版本对账与别名对照。

核心约束（都在这里落地，路由层不判业务）：
- 同一交换包按内容指纹去重：重复上传不新增成员，也不重复登记成员变更。
- 每个成员保留源文件与物理行号，新增代码可逐条追溯。
- 父级未收录或层级成环的边不计入有效层级；成员本身仍保留，但归并时按待映射处理。
- 两版生效期重叠即为版本冲突，并给出同一代码在两版里的上级/名称差异。
- 同一别名指向多个代码、或别名与代码同名，都登记为冲突别名。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime
from typing import Any

from app.exchange_store import SEED_OLD_PACKAGE, exchange_store

FIELD_DEFAULT = "地区"
MAX_DATE = "9999-12-31"
ALIAS_SPLIT = re.compile(r"[,，;；、|]+")
DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")

REQUIRED_HEADERS = ("层级代码", "父级代码", "生效日期", "别名")
OPTIONAL_HEADERS = ("维度字段", "名称", "失效日期", "层级名称")
KNOWN_HEADERS = set(REQUIRED_HEADERS) | set(OPTIONAL_HEADERS)


class ExchangeError(Exception):
    """交换包整包不可接收时抛出，路由层转成 ok=false 的可读说明。"""


def _diag(file: str | None, line: int | None, level: str, code: str, message: str) -> dict[str, Any]:
    return {
        "file": file,
        "line": line,
        "level": level,
        "code": code,
        "message": message,
    }


def _parse_date(value: Any, field: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"{field}「{text}」不是可识别的日期（应为 YYYY-MM-DD）")


def _date_or_max(value: str | None) -> str:
    return value or MAX_DATE


def _ranges_overlap(a_from: str, a_to: str | None, b_from: str, b_to: str | None) -> bool:
    return a_from <= _date_or_max(b_to) and b_from <= _date_or_max(a_to)


def _overlap_bounds(
    a_from: str, a_to: str | None, b_from: str, b_to: str | None
) -> tuple[str, str]:
    start = max(a_from, b_from)
    end = min(_date_or_max(a_to), _date_or_max(b_to))
    return start, end


def _split_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    parts = ALIAS_SPLIT.split(str(value))
    seen: list[str] = []
    for part in parts:
        alias = part.strip()
        if alias and alias not in seen:
            seen.append(alias)
    return seen


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(
    version: str,
    source: str,
    field: str,
    effective_from: str,
    effective_to: str,
    members: list[dict[str, Any]],
) -> str:
    signature = {
        "version": version,
        "source": source,
        "field": field,
        "from": effective_from,
        "to": effective_to,
        "rows": sorted(
            (
                item["code"],
                item["parent_code"],
                item["name"],
                item["effective_from"],
                item["effective_to"],
                tuple(sorted(item["aliases"])),
            )
            for item in members
        ),
    }
    return hashlib.sha256(_canonical_json(signature).encode("utf-8")).hexdigest()


def to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """按给定列输出带 BOM 的 CSV，Excel 直接打开不乱码。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return "﻿" + buffer.getvalue()


class DimensionExchangeService:
    def __init__(self) -> None:
        self.ensure_seeded()

    # ------------------------------------------------------------------ 生命周期
    def ensure_seeded(self) -> None:
        if exchange_store.packages or not exchange_store.seed_pending:
            return
        self.import_package(dict(SEED_OLD_PACKAGE), seeded=True)

    def reset(self, *, seed_packages: bool = False, seed_orders: bool = True) -> None:
        exchange_store.reset(seed_packages=seed_packages, seed_orders=seed_orders)
        self.ensure_seeded()

    # ------------------------------------------------------------------ 导入
    def import_package(self, payload: dict[str, Any], *, seeded: bool = False) -> dict[str, Any]:
        diagnostics: list[dict[str, Any]] = []
        version = str(payload.get("version") or "").strip()
        source = str(payload.get("source") or "").strip()
        field = str(payload.get("field") or FIELD_DEFAULT).strip() or FIELD_DEFAULT
        if not version:
            diagnostics.append(_diag(None, None, "error", "缺少版本", "交换包必须给出维度字典版本"))
        if not source:
            diagnostics.append(_diag(None, None, "error", "缺少来源", "交换包必须注明数据来源"))
        try:
            effective_from = _parse_date(payload.get("effective_from"), "生效期起")
            effective_to = _parse_date(payload.get("effective_to"), "生效期止")
        except ValueError as exc:
            diagnostics.append(_diag(None, None, "error", "生效期无效", str(exc)))
            effective_from, effective_to = None, None
        if effective_from and effective_to and effective_from > effective_to:
            diagnostics.append(_diag(None, None, "error", "生效期倒置", "生效期起不能晚于生效期止"))
        # 未给生效期止视为长期有效，成员仍按行内失效日期收窄。
        if effective_from and not effective_to:
            effective_to = MAX_DATE

        volumes = payload.get("volumes")
        if not isinstance(volumes, list) or not volumes:
            diagnostics.append(_diag(None, None, "error", "缺少分卷", "至少需要一个分卷文件（content 或 rows）"))

        if diagnostics:
            return {"ok": False, "duplicate": False, "message": "交换包未通过校验，未写入任何成员", "diagnostics": diagnostics}

        assert effective_from and effective_to
        members: list[dict[str, Any]] = []
        volume_meta: list[dict[str, Any]] = []
        seen_codes: dict[str, tuple[str, int]] = {}
        for index, volume in enumerate(volumes):
            if not isinstance(volume, dict):
                diagnostics.append(_diag(None, None, "error", "分卷格式错误", f"第 {index + 1} 个分卷不是对象"))
                continue
            file_name = str(volume.get("file") or f"volume-{index + 1}.csv").strip()
            read = self._read_volume(
                volume,
                file_name,
                field=field,
                effective_from=effective_from,
                effective_to=effective_to,
                seen_codes=seen_codes,
                diagnostics=diagnostics,
            )
            volume_meta.append({"file": file_name, "rows": len(read)})
            members.extend(read)

        if not members:
            diagnostics.append(_diag(None, None, "error", "没有可用成员", "所有分卷都没有解析出可接收的层级代码"))
            return {"ok": False, "duplicate": False, "message": "交换包没有可用成员，未写入", "diagnostics": diagnostics}

        fingerprint = _fingerprint(version, source, field, effective_from, effective_to, members)
        for existing in exchange_store.packages:
            if existing["fingerprint"] == fingerprint:
                existing["duplicate_submissions"] = int(existing.get("duplicate_submissions", 0)) + 1
                exchange_store.submission_log.append(
                    {
                        "package_id": existing["id"],
                        "version": version,
                        "source": source,
                        "result": "重复提交，未新增成员",
                        "at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                return {
                    "ok": True,
                    "duplicate": True,
                    "message": f"同一交换包已登记为 {existing['id']}，本次未新增成员，也未重复登记成员变更",
                    "package": self._summary(existing),
                    "members_created": 0,
                    "changes_created": 0,
                    "diagnostics": diagnostics,
                }

        package = {
            "id": exchange_store.next_package_id(),
            "version": version,
            "source": source,
            "field": field,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "submitted_at": datetime.now().isoformat(timespec="seconds"),
            "fingerprint": fingerprint,
            "seeded": seeded,
            "status": "已接收",
            "duplicate_submissions": 0,
            "volumes": volume_meta,
        }
        # 成员与包绑定后统一做层级解析，保证同一分卷包内父子关系自洽。
        for item in members:
            member = {
                "id": exchange_store.next_member_id(),
                "package_id": package["id"],
                "code": item["code"],
                "name": item["name"],
                "parent_code": item["parent_code"],
                "aliases": item["aliases"],
                "effective_from": item["effective_from"],
                "effective_to": item["effective_to"],
                "source_file": item["source_file"],
                "source_line": item["source_line"],
                "hierarchy_status": "有效",
                "hierarchy_reason": "",
                "depth": 0,
                "path_codes": [item["code"]],
                "path_names": [item["name"]],
                "group_code": item["code"],
                "group_name": item["name"],
                "name_collision": False,
                "alias_conflicts": [],
            }
            exchange_store.members[(package["id"], member["code"])] = member

        package_members = self._package_members(package["id"])
        self._resolve_hierarchy(package_members)
        self._flag_same_names(package_members)

        package["member_count"] = len(package_members)
        package["edge_count"] = sum(1 for m in package_members if m["parent_code"])
        package["valid_edge_count"] = sum(
            1 for m in package_members if m["parent_code"] and m["hierarchy_status"] == "有效"
        )
        package["invalid_edge_count"] = package["edge_count"] - package["valid_edge_count"]
        package["conflict_count"] = 0
        exchange_store.packages.append(package)

        changes = self._register_changes(package)
        conflicts = self._refresh_conflicts()
        self._sync_conflict_counts(conflicts)
        exchange_store.submission_log.append(
            {
                "package_id": package["id"],
                "version": version,
                "source": source,
                "result": "接收成功",
                "at": package["submitted_at"],
            }
        )

        return {
            "ok": True,
            "duplicate": False,
            "message": (
                f"交换包 {package['id']} 已接收：{package['member_count']} 个成员，"
                f"有效层级边 {package['valid_edge_count']}/{package['edge_count']}，"
                f"成员变更 {len(changes)} 条"
            ),
            "package": self._summary(package),
            "members_created": package["member_count"],
            "changes_created": len(changes),
            "changes": changes,
            "diagnostics": diagnostics,
        }

    # ------------------------------------------------------------------ 分卷解析
    def _read_volume(
        self,
        volume: dict[str, Any],
        file_name: str,
        *,
        field: str,
        effective_from: str,
        effective_to: str,
        seen_codes: dict[str, tuple[str, int]],
        diagnostics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw_rows: list[tuple[int, dict[str, Any]]] = []
        if volume.get("rows") is not None:
            rows = volume.get("rows")
            if not isinstance(rows, list):
                diagnostics.append(_diag(file_name, None, "error", "分卷格式错误", "rows 必须是对象数组"))
                return []
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    raw_rows.append((index + 1, {str(k).strip(): v for k, v in row.items()}))
                else:
                    diagnostics.append(_diag(file_name, index + 1, "error", "行格式错误", "每一行必须是键值对象"))
        else:
            content = volume.get("content")
            if not isinstance(content, str) or not content.strip():
                diagnostics.append(_diag(file_name, None, "error", "分卷为空", "分卷 content 不能为空"))
                return []
            raw_rows = self._read_csv(content, file_name, diagnostics)
            if raw_rows is None:
                return []

        members: list[dict[str, Any]] = []
        for line, row in raw_rows:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            member = self._build_member(
                row,
                file_name=file_name,
                line=line,
                field=field,
                effective_from=effective_from,
                effective_to=effective_to,
                seen_codes=seen_codes,
                diagnostics=diagnostics,
            )
            if member is not None:
                members.append(member)
        return members

    def _read_csv(
        self,
        content: str,
        file_name: str,
        diagnostics: list[dict[str, Any]],
    ) -> list[tuple[int, dict[str, Any]]] | None:
        text = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        first_line = text.split("\n", 1)[0]
        delimiter = "\t" if "\t" in first_line and "," not in first_line else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            diagnostics.append(_diag(file_name, None, "error", "分卷为空", "CSV 没有表头"))
            return None
        header = [str(item).strip().lstrip("﻿") for item in header]
        missing = [name for name in REQUIRED_HEADERS if name not in header]
        if missing:
            diagnostics.append(
                _diag(file_name, 1, "error", "缺少表头", f"分卷缺少列：{'、'.join(missing)}；至少需要 {'、'.join(REQUIRED_HEADERS)}")
            )
            return None
        unknown = [name for name in header if name and name not in KNOWN_HEADERS]
        if unknown:
            diagnostics.append(
                _diag(file_name, 1, "info", "忽略未知列", f"未识别的列按扩展字段忽略：{'、'.join(unknown)}")
            )
        rows: list[tuple[int, dict[str, Any]]] = []
        for values in reader:
            line = reader.line_num
            mapped: dict[str, Any] = {}
            for position, name in enumerate(header):
                if name:
                    mapped[name] = values[position] if position < len(values) else ""
            rows.append((line, mapped))
        return rows

    def _build_member(
        self,
        row: dict[str, Any],
        *,
        file_name: str,
        line: int,
        field: str,
        effective_from: str,
        effective_to: str,
        seen_codes: dict[str, tuple[str, int]],
        diagnostics: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        row_field = str(row.get("维度字段") or "").strip()
        if row_field and row_field != field:
            diagnostics.append(
                _diag(file_name, line, "error", "维度字段不一致", f"该行维度字段为「{row_field}」，交换包为「{field}」")
            )
            return None
        code = str(row.get("层级代码") or "").strip()
        if not code:
            diagnostics.append(_diag(file_name, line, "error", "缺少层级代码", "层级代码为空，无法登记成员"))
            return None
        if code in seen_codes:
            origin_file, origin_line = seen_codes[code]
            diagnostics.append(
                _diag(file_name, line, "error", "重复层级代码", f"代码 {code} 已在 {origin_file} 第 {origin_line} 行出现，本行不重复登记")
            )
            return None
        parent_code = str(row.get("父级代码") or "").strip()
        if parent_code == code:
            diagnostics.append(_diag(file_name, line, "error", "自引用父级", f"代码 {code} 的父级不能指向自己"))
            return None
        try:
            row_from = _parse_date(row.get("生效日期"), "生效日期")
            row_to = _parse_date(row.get("失效日期"), "失效日期") if "失效日期" in row else None
        except ValueError as exc:
            diagnostics.append(_diag(file_name, line, "error", "日期无效", str(exc)))
            return None
        if not row_from:
            diagnostics.append(_diag(file_name, line, "error", "缺少生效日期", "每一行都必须给出生效日期"))
            return None
        if row_to and row_from > row_to:
            diagnostics.append(_diag(file_name, line, "error", "日期倒置", f"{code} 的生效日期晚于失效日期"))
            return None
        member_from = max(row_from, effective_from)
        member_to = min(row_to or effective_to, effective_to)
        if row_from < effective_from or (row_to and row_to > effective_to):
            diagnostics.append(
                _diag(
                    file_name,
                    line,
                    "warn",
                    "日期收窄",
                    f"{code} 的生效期 {row_from}~{row_to or '长期'} 超出包范围，已按 {member_from}~{member_to} 收窄",
                )
            )
        if member_from > member_to:
            diagnostics.append(
                _diag(file_name, line, "error", "日期落在包外", f"{code} 的生效期与交换包范围没有交集，本行不登记")
            )
            return None
        seen_codes[code] = (file_name, line)
        name = str(row.get("名称") or "").strip() or code
        return {
            "code": code,
            "name": name,
            "parent_code": parent_code,
            "aliases": _split_aliases(row.get("别名")),
            "effective_from": member_from,
            "effective_to": member_to,
            "source_file": file_name,
            "source_line": line,
        }

    # ------------------------------------------------------------------ 层级
    def _package_members(self, package_id: str) -> list[dict[str, Any]]:
        return [m for (pid, _), m in exchange_store.members.items() if pid == package_id]

    def _resolve_hierarchy(self, members: list[dict[str, Any]]) -> None:
        by_code = {member["code"]: member for member in members}
        for member in members:
            chain: list[dict[str, Any]] = [member]
            seen = {member["code"]: member}
            cursor = member
            cycle: list[str] | None = None
            missing: str | None = None
            while cursor["parent_code"]:
                parent_code = cursor["parent_code"]
                parent = by_code.get(parent_code)
                if parent is None:
                    missing = parent_code
                    break
                if parent_code in seen:
                    cycle_start = next(
                        (index for index, item in enumerate(chain) if item["code"] == parent_code),
                        0,
                    )
                    cycle = [item["code"] for item in chain[cycle_start:]]
                    break
                seen[parent_code] = parent
                chain.append(parent)
                cursor = parent

            if missing is not None:
                member["hierarchy_status"] = "无效"
                member["hierarchy_reason"] = f"父级 {missing} 未收录"
                continue
            if cycle is not None:
                member["hierarchy_status"] = "无效"
                if member["code"] in cycle:
                    member["hierarchy_reason"] = f"层级成环：{' → '.join(cycle + [cycle[0]])}"
                else:
                    member["hierarchy_reason"] = f"上级层级成环：{' → '.join(cycle + [cycle[0]])}"
                continue

            root = chain[-1]
            member["depth"] = len(chain) - 1
            member["path_codes"] = [item["code"] for item in reversed(chain)]
            member["path_names"] = [item["name"] for item in reversed(chain)]
            member["group_code"] = root["code"]
            member["group_name"] = root["name"]

    def _flag_same_names(self, members: list[dict[str, Any]]) -> None:
        by_name: dict[str, list[dict[str, Any]]] = {}
        for member in members:
            by_name.setdefault(member["name"], []).append(member)
        for name, group in by_name.items():
            if len(group) < 2:
                continue
            codes = [item["code"] for item in group]
            for member in group:
                member["name_collision"] = True
                member["name_collision_codes"] = codes

    # ------------------------------------------------------------------ 成员变更
    def _register_changes(self, package: dict[str, Any]) -> list[dict[str, Any]]:
        base = self._base_package(package)
        new_members = {m["code"]: m for m in self._package_members(package["id"])}
        old_members = (
            {m["code"]: m for m in self._package_members(base["id"])} if base is not None else {}
        )
        registered: list[dict[str, Any]] = []
        for code, member in new_members.items():
            old = old_members.get(code)
            changed_fields: list[str] = []
            if old is None:
                change_type = "新增代码"
            else:
                if old["name"] != member["name"]:
                    changed_fields.append("名称")
                if old["parent_code"] != member["parent_code"]:
                    changed_fields.append("父级代码")
                if sorted(old["aliases"]) != sorted(member["aliases"]):
                    changed_fields.append("别名")
                if old["effective_from"] != member["effective_from"] or old["effective_to"] != member["effective_to"]:
                    changed_fields.append("生效期")
                change_type = "上级更正" if "父级代码" in changed_fields else ("更正" if changed_fields else None)
            if change_type is None:
                continue
            key = (package["id"], code)
            if key in exchange_store._change_keys:  # 同一包同一代码只登记一次
                continue
            exchange_store._change_keys.add(key)
            change = {
                "id": exchange_store.next_change_id(),
                "package_id": package["id"],
                "version": package["version"],
                "code": code,
                "change_type": change_type,
                "changed_fields": changed_fields,
                "old_parent_code": old["parent_code"] if old else None,
                "new_parent_code": member["parent_code"],
                "old_aliases": list(old["aliases"]) if old else [],
                "new_aliases": list(member["aliases"]),
                "source_file": member["source_file"],
                "source_line": member["source_line"],
                "base_package_id": base["id"] if base else None,
            }
            exchange_store.changes.append(change)
            registered.append(change)
        if base is not None:
            for code, old in old_members.items():
                if code in new_members:
                    continue
                key = (package["id"], code)
                if key in exchange_store._change_keys:
                    continue
                exchange_store._change_keys.add(key)
                change = {
                    "id": exchange_store.next_change_id(),
                    "package_id": package["id"],
                    "version": package["version"],
                    "code": code,
                    "change_type": "移除代码",
                    "changed_fields": ["成员"],
                    "old_parent_code": old["parent_code"],
                    "new_parent_code": None,
                    "old_aliases": list(old["aliases"]),
                    "new_aliases": [],
                    "source_file": old["source_file"],
                    "source_line": old["source_line"],
                    "base_package_id": base["id"],
                }
                exchange_store.changes.append(change)
                registered.append(change)
        return registered

    def _base_package(self, package: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in exchange_store.packages
            if item["field"] == package["field"] and item["id"] != package["id"]
        ]
        earlier = [item for item in candidates if item["effective_from"] < package["effective_from"]]
        pool = earlier or candidates
        if not pool:
            return None
        return max(pool, key=lambda item: (item["effective_from"], item["id"]))

    # ------------------------------------------------------------------ 冲突
    def _refresh_conflicts(self) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        packages = exchange_store.packages
        for index, old in enumerate(packages):
            for new in packages[index + 1:]:
                if old["field"] != new["field"]:
                    continue
                if not _ranges_overlap(old["effective_from"], old["effective_to"], new["effective_from"], new["effective_to"]):
                    continue
                start, end = _overlap_bounds(old["effective_from"], old["effective_to"], new["effective_from"], new["effective_to"])
                old_members = {m["code"]: m for m in self._package_members(old["id"])}
                new_members = {m["code"]: m for m in self._package_members(new["id"])}
                diffs: list[dict[str, Any]] = []
                for code in sorted(set(old_members) | set(new_members)):
                    left = old_members.get(code)
                    right = new_members.get(code)
                    if left is None or right is None:
                        diffs.append(
                            {
                                "code": code,
                                "old_parent_code": left["parent_code"] if left else None,
                                "new_parent_code": right["parent_code"] if right else None,
                                "old_group_name": left["group_name"] if left else None,
                                "new_group_name": right["group_name"] if right else None,
                                "kind": "仅单版收录",
                            }
                        )
                        continue
                    if left["parent_code"] != right["parent_code"] or left["name"] != right["name"]:
                        diffs.append(
                            {
                                "code": code,
                                "old_parent_code": left["parent_code"],
                                "new_parent_code": right["parent_code"],
                                "old_group_name": left["group_name"],
                                "new_group_name": right["group_name"],
                                "old_name": left["name"],
                                "new_name": right["name"],
                                "kind": "归并口径不一致",
                            }
                        )
                conflicts.append(
                    {
                        "old_package_id": old["id"],
                        "new_package_id": new["id"],
                        "old_version": old["version"],
                        "new_version": new["version"],
                        "overlap_from": start,
                        "overlap_to": end,
                        "diffs": diffs,
                    }
                )
        return conflicts

    def conflicts(self) -> list[dict[str, Any]]:
        conflicts = self._refresh_conflicts()
        self._sync_conflict_counts(conflicts)
        return conflicts

    def _sync_conflict_counts(self, conflicts: list[dict[str, Any]] | None = None) -> None:
        """包上的冲突数跟随当前版本关系刷新，避免新增版本后旧包计数滞后。"""
        conflicts = conflicts if conflicts is not None else self._refresh_conflicts()
        for package in exchange_store.packages:
            package["conflict_count"] = sum(
                1
                for conflict in conflicts
                if package["id"] in (conflict["old_package_id"], conflict["new_package_id"])
            )

    # ------------------------------------------------------------------ 查询
    def list_packages(self) -> list[dict[str, Any]]:
        return [self._summary(p) for p in exchange_store.packages]

    def get_package(self, package_id: str) -> dict[str, Any] | None:
        package = next((item for item in exchange_store.packages if item["id"] == package_id), None)
        if package is None:
            return None
        detail = self._summary(package)
        detail["members"] = [
            self._member_view(member) for member in self._package_members(package_id)
        ]
        detail["changes"] = [
            dict(change) for change in exchange_store.changes if change["package_id"] == package_id
        ]
        detail["aliases"] = [
            alias for alias in exchange_store.manual_aliases if alias["package_id"] == package_id
        ]
        detail["volume_details"] = list(package["volumes"])
        detail["conflicts"] = [
            conflict
            for conflict in self.conflicts()
            if package_id in (conflict["old_package_id"], conflict["new_package_id"])
        ]
        return detail

    def _member_view(self, member: dict[str, Any]) -> dict[str, Any]:
        return {
            **member,
            "aliases": list(member["aliases"]) + self._manual_aliases(member["package_id"], member["code"]),
        }

    def _summary(self, package: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": package["id"],
            "version": package["version"],
            "source": package["source"],
            "field": package["field"],
            "effective_from": package["effective_from"],
            "effective_to": package["effective_to"],
            "submitted_at": package["submitted_at"],
            "status": package["status"],
            "member_count": package.get("member_count", 0),
            "edge_count": package.get("edge_count", 0),
            "valid_edge_count": package.get("valid_edge_count", 0),
            "invalid_edge_count": package.get("invalid_edge_count", 0),
            "conflict_count": package.get("conflict_count", 0),
            "duplicate_submissions": package.get("duplicate_submissions", 0),
            "volumes": list(package.get("volumes", [])),
            "seeded": package.get("seeded", False),
        }

    # ------------------------------------------------------------------ 手工别名
    def _manual_aliases(self, package_id: str, code: str) -> list[str]:
        aliases: list[str] = []
        for item in exchange_store.manual_aliases:
            if item["package_id"] == package_id and item["code"] == code and item["alias"] not in aliases:
                aliases.append(item["alias"])
        return aliases

    def append_alias(self, package_id: str, code: str, alias: str) -> dict[str, Any]:
        package = next((item for item in exchange_store.packages if item["id"] == package_id), None)
        if package is None:
            raise ExchangeError(f"交换包 {package_id} 不存在")
        member = exchange_store.members.get((package_id, code))
        if member is None:
            raise ExchangeError(f"交换包 {package_id} 里没有代码 {code}")
        alias = str(alias or "").strip()
        if not alias:
            raise ExchangeError("别名不能为空")
        if alias in member["aliases"] or alias in self._manual_aliases(package_id, code):
            return {"ok": True, "applied": False, "message": f"别名「{alias}」已存在，未重复追加", "alias": alias, "code": code}
        entry = {
            "id": exchange_store.next_alias_id(),
            "package_id": package_id,
            "code": code,
            "alias": alias,
            "source": "人工追加",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        exchange_store.manual_aliases.append(entry)
        return {"ok": True, "applied": True, "message": f"已为 {code} 追加别名「{alias}」", "entry": entry}

    def alias_report(self) -> dict[str, Any]:
        owners: dict[str, list[dict[str, Any]]] = {}
        total = 0
        for (package_id, code), member in exchange_store.members.items():
            aliases = list(member["aliases"]) + self._manual_aliases(package_id, code)
            for alias in aliases:
                total += 1
                owners.setdefault(alias, [])
                if not any(item["package_id"] == package_id and item["code"] == code for item in owners[alias]):
                    owners[alias].append(
                        {
                            "package_id": package_id,
                            "code": code,
                            "name": member["name"],
                            "source_file": member["source_file"],
                            "source_line": member["source_line"],
                        }
                    )
        code_owner = {code: member for (_, code), member in exchange_store.members.items()}
        conflicts: list[dict[str, Any]] = []
        for alias, owners_list in sorted(owners.items()):
            distinct_codes = sorted({item["code"] for item in owners_list})
            alias_with_code = alias in code_owner
            if len(distinct_codes) > 1 or alias_with_code:
                conflicts.append(
                    {
                        "alias": alias,
                        "codes": distinct_codes,
                        "kind": (
                            "别名与代码同名"
                            if alias_with_code
                            else "同一别名指向多个代码"
                        ),
                        "code_collision": alias if alias_with_code else None,
                        "owners": sorted(owners_list, key=lambda item: (item["package_id"], item["code"])),
                        "code_names": sorted({code_owner[code]["name"] for code in distinct_codes if code in code_owner}),
                    }
                )
        return {
            "total_alias_bindings": total,
            "distinct_aliases": len(owners),
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
        }

    # ------------------------------------------------------------------ 对照输出
    def comparison(self) -> dict[str, Any]:
        packages = sorted(exchange_store.packages, key=lambda item: (item["effective_from"], item["id"]))
        conflicts = self.conflicts()
        alias_report = self.alias_report()
        alias_conflict_codes = {
            (owner["package_id"], owner["code"])
            for conflict in alias_report["conflicts"]
            for owner in conflict["owners"]
        }
        codes: list[str] = []
        for package in packages:
            for member in sorted(self._package_members(package["id"]), key=lambda item: item["code"]):
                if member["code"] not in codes:
                    codes.append(member["code"])
        rows: list[dict[str, Any]] = []
        for code in codes:
            versions: list[dict[str, Any]] = []
            source_files: list[str] = []
            for package in packages:
                member = exchange_store.members.get((package["id"], code))
                if member is None:
                    continue
                conflict_hit = any(
                    code in {diff["code"] for diff in conflict["diffs"]}
                    and package["id"] in (conflict["old_package_id"], conflict["new_package_id"])
                    for conflict in conflicts
                )
                aliases = list(member["aliases"]) + self._manual_aliases(package["id"], code)
                versions.append(
                    {
                        "package_id": package["id"],
                        "version": package["version"],
                        "source": package["source"],
                        "name": member["name"],
                        "parent_code": member["parent_code"],
                        "aliases": aliases,
                        "effective_from": member["effective_from"],
                        "effective_to": member["effective_to"],
                        "hierarchy_status": member["hierarchy_status"],
                        "hierarchy_reason": member["hierarchy_reason"],
                        "group_code": member["group_code"],
                        "group_name": member["group_name"],
                        "path_names": member["path_names"],
                        "source_file": member["source_file"],
                        "source_line": member["source_line"],
                        "effective_conflict": conflict_hit,
                        "alias_conflict": (package["id"], code) in alias_conflict_codes,
                    }
                )
                source_files.append(f"{member['source_file']}:{member['source_line']}")
            rows.append(
                {
                    "code": code,
                    "name": versions[0]["name"] if versions else "",
                    "versions": versions,
                    "version_count": len(versions),
                    "parent_changed": len({item["parent_code"] for item in versions}) > 1,
                    "name_changed": len({item["name"] for item in versions}) > 1,
                    "alias_conflict": any(item["alias_conflict"] for item in versions),
                    "effective_conflict": any(item["effective_conflict"] for item in versions),
                    "source_refs": source_files,
                }
            )
        return {
            "packages": [self._summary(package) for package in packages],
            "conflicts": conflicts,
            "alias_report": alias_report,
            "rows": rows,
            "totals": {
                "packages": len(packages),
                "codes": len(rows),
                "codes_with_parent_change": sum(1 for row in rows if row["parent_changed"]),
                "codes_with_alias_conflict": sum(1 for row in rows if row["alias_conflict"]),
                "effective_conflicts": len(conflicts),
            },
        }

    def comparison_csv_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.comparison()["rows"]:
            if not row["versions"]:
                continue
            for version in row["versions"]:
                rows.append(
                    {
                        "层级代码": row["code"],
                        "名称": version["name"],
                        "版本": version["version"],
                        "来源": version["source"],
                        "交换包": version["package_id"],
                        "上级代码": version["parent_code"],
                        "地区分组": version["group_name"],
                        "层级路径": "/".join(version["path_names"]),
                        "别名": "、".join(version["aliases"]),
                        "生效起": version["effective_from"],
                        "生效止": version["effective_to"],
                        "层级状态": version["hierarchy_status"],
                        "层级说明": version["hierarchy_reason"],
                        "生效期冲突": "是" if version["effective_conflict"] else "否",
                        "别名冲突": "是" if version["alias_conflict"] else "否",
                        "源文件": version["source_file"],
                        "源行号": version["source_line"],
                    }
                )
        return rows

    def alias_csv_rows(self) -> list[dict[str, Any]]:
        report = self.alias_report()
        rows: list[dict[str, Any]] = []
        for conflict in report["conflicts"]:
            for owner in conflict["owners"]:
                rows.append(
                    {
                        "别名": conflict["alias"],
                        "冲突类型": conflict["kind"],
                        "命中代码": owner["code"],
                        "代码名称": owner["name"],
                        "交换包": owner["package_id"],
                        "源文件": owner["source_file"],
                        "源行号": owner["source_line"],
                        "涉及代码数": len(conflict["codes"]),
                    }
                )
        return rows

    # ------------------------------------------------------------------ 字典导出
    def export_dictionary(self, package_id: str | None = None) -> dict[str, Any]:
        if package_id:
            package = next((item for item in exchange_store.packages if item["id"] == package_id), None)
            if package is None:
                raise ExchangeError(f"交换包 {package_id} 不存在")
            members = self._package_members(package_id)
            version = package["version"]
            source = package["source"]
            field = package["field"]
            effective_from = package["effective_from"]
            effective_to = package["effective_to"]
        else:
            if not exchange_store.packages:
                raise ExchangeError("当前没有可导出的维度字典")
            members = self._current_members()
            field = exchange_store.packages[0]["field"]
            version = "本地字典导出"
            source = "本地字典"
            effective_from = min(member["effective_from"] for member in members)
            effective_to = max(_date_or_max(member["effective_to"]) for member in members)
            effective_to = None if effective_to == MAX_DATE else effective_to
        rows = []
        for member in sorted(members, key=lambda item: (item["depth"], item["code"])):
            row_effective_to = member["effective_to"]
            # 成员失效日期与包范围一致时不必重复写出，导出的分卷可原样再导入。
            if effective_to and row_effective_to == effective_to:
                row_effective_to = ""
            rows.append(
                {
                    "维度字段": field,
                    "层级代码": member["code"],
                    "父级代码": member["parent_code"],
                    "生效日期": member["effective_from"],
                    "失效日期": row_effective_to,
                    "别名": "、".join(
                        list(member["aliases"]) + self._manual_aliases(member["package_id"], member["code"])
                    ),
                    "名称": member["name"],
                }
            )
        return {
            "version": version,
            "source": source,
            "field": field,
            "effective_from": effective_from,
            "effective_to": effective_to or MAX_DATE,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "volumes": [{"file": f"dictionary_{version}.csv", "rows": rows}],
        }

    def _current_members(self) -> list[dict[str, Any]]:
        """按生效期取每个代码的最新版本，作为本地字典的当前视图。"""
        latest: dict[str, dict[str, Any]] = {}
        for package in sorted(exchange_store.packages, key=lambda item: (item["effective_from"], item["id"])):
            for member in self._package_members(package["id"]):
                latest[member["code"]] = member
        return list(latest.values())


dimension_exchange_service = DimensionExchangeService()
