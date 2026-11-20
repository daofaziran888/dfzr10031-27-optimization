"""维度字典交换包与地区订单的内存仓库。

这里的数据是维度管理「字典交换」和数据集市「地区归并」的支撑数据，单独放一份，
不混进通用模块清单：交换包、成员、成员变更、人工追加别名、订单与重算结果。
真实项目里会换成数据库表，当前实现只依赖标准库。
"""
from __future__ import annotations

from typing import Any

# 旧来源的地区交换包：版本 2026.1，生效到 2026-06-30。
# 用 CSV 分卷示例启动，导入后会留下真实的源文件与行号，便于追溯。
SEED_OLD_PACKAGE: dict[str, Any] = {
    "version": "2026.1",
    "source": "旧来源",
    "effective_from": "2026-01-01",
    "effective_to": "2026-06-30",
    "field": "地区",
    "volumes": [
        {
            "file": "region_2026_1.csv",
            "content": (
                "维度字段,层级代码,父级代码,生效日期,别名,名称\n"
                "地区,RG-A,,2026-01-01,甲区,甲\n"
                "地区,R-X,RG-A,2026-01-01,X地区,地区X\n"
                "地区,R-Y,RG-A,2026-01-01,,地区Y\n"
            ),
        }
    ],
}

# 订单示例覆盖：旧版独有区间、两版重叠区间、新版独有区间、缺失地区代码。
# 1001/1002 是需求里「旧来源与新来源各一笔、业务日同在重叠期」的两笔。
SEED_ORDERS: list[dict[str, Any]] = [
    {
        "order_no": "ORD-1001",
        "source": "旧来源",
        "business_date": "2026-05-10",
        "region_code": "R-X",
        "amount": 1200.00,
    },
    {
        "order_no": "ORD-1002",
        "source": "新来源",
        "business_date": "2026-05-20",
        "region_code": "R-X",
        "amount": 860.00,
    },
    {
        "order_no": "ORD-1003",
        "source": "旧来源",
        "business_date": "2026-02-15",
        "region_code": "R-X",
        "amount": 430.00,
    },
    {
        "order_no": "ORD-1004",
        "source": "新来源",
        "business_date": "2026-07-15",
        "region_code": "R-X",
        "amount": 990.00,
    },
    {
        "order_no": "ORD-1005",
        "source": "新来源",
        "business_date": "2026-08-01",
        "region_code": "R-Z",
        "amount": 315.00,
    },
    {
        "order_no": "ORD-1006",
        "source": "旧来源",
        "business_date": "2026-03-01",
        "region_code": "R-Y",
        "amount": 640.00,
    },
]


class ExchangeStore:
    """交换包与订单的内存状态；reset 会恢复示例数据。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self, *, seed_packages: bool = True, seed_orders: bool = True) -> None:
        self.packages: list[dict[str, Any]] = []
        # (package_id, code) -> 成员
        self.members: dict[tuple[str, str], dict[str, Any]] = {}
        self.changes: list[dict[str, Any]] = []
        self.manual_aliases: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = (
            [dict(row) for row in SEED_ORDERS] if seed_orders else []
        )
        self.reconcile_runs: dict[str, dict[str, Any]] = {}
        self.submission_log: list[dict[str, Any]] = []
        self.seed_pending = seed_packages
        self._package_seq = 0
        self._member_seq = 0
        self._change_seq = 0
        self._alias_seq = 0
        self._run_seq = 0
        self._change_keys: set[tuple[str, str]] = set()

    # 下面几个编号由服务层调用，统一放在仓库里保证进程内唯一。
    def next_package_id(self) -> str:
        self._package_seq += 1
        return f"DXP-{self._package_seq:04d}"

    def next_member_id(self) -> int:
        self._member_seq += 1
        return self._member_seq

    def next_change_id(self) -> int:
        self._change_seq += 1
        return self._change_seq

    def next_alias_id(self) -> int:
        self._alias_seq += 1
        return self._alias_seq

    def next_run_id(self) -> str:
        self._run_seq += 1
        return f"REC-{self._run_seq:04d}"


exchange_store = ExchangeStore()
