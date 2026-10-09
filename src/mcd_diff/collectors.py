"""采集层。

设计要点：**采集与计算分离**。

- 真实运行时，MCP 工具的调用由 WorkBuddy 智能体编排（见 SKILL.md），
  采集结果落盘为 raw.json；
- 本模块提供读取入口（`JsonFileCollector`）与一个最小的 `McpClient` 协议，
  便于单元测试注入假数据；
- 需要脱离 WorkBuddy 独立采集时，用 `scripts/collect.py` —— 它自带一个
  标准库实现的 Streamable HTTP MCP 客户端，产物同样落盘为 raw.json。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .models import RawData


class McpClient(Protocol):
    """最小 MCP 调用协议（便于替换实现 / 打桩测试）。

    真实实现由 WorkBuddy 的 mcd-mcp 连接器承担；此处仅定义形状。
    """

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:  # pragma: no cover
        ...


#: 本项目允许调用的工具白名单（只读）。
READONLY_TOOLS: tuple[str, ...] = (
    "now-time-info",
    "order-list",
    "query-order",
    "query-meals",
    "query-meal-detail",
    "list-nutrition-foods",
    "query-my-account",
    "query-my-coupons",
    "query-store-coupons",
    "mall-order-list",
    "campaign-calendar",
)

#: 明确禁止调用的交易类工具（安全边界，见 MCP_INTEGRATION.md 第 6 节）。
FORBIDDEN_TOOLS: tuple[str, ...] = (
    "create-order",
    "cancel-order",
    "calculate-price",
    "draw-lottery",
    "party-order-create",
    "mall-create-order",
    "auto-bind-coupons",
)


def assert_readonly(tool_name: str) -> None:
    """在调用前校验工具名，防止误触交易类工具。"""
    if tool_name in FORBIDDEN_TOOLS:
        raise PermissionError(
            f"工具 `{tool_name}` 属于交易类，麦门 Diff 只做本地重算，拒绝调用。"
        )


class JsonFileCollector:
    """从本地 JSON 文件读取采集结果。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def collect(self) -> RawData:
        if not self.path.exists():
            raise FileNotFoundError(
                f"未找到采集数据：{self.path}\n"
                "请先按 SKILL.md 第 1–2 步，用 MCP 工具采集并写入 raw.json。"
            )
        raw: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        return RawData.from_dict(raw)


class MemoryCollector:
    """从内存字典读取（测试用）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def collect(self) -> RawData:
        return RawData.from_dict(self._data)
