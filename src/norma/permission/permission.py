"""
工具权限系统

设计目标
--------
提供一种轻量、可扩展的工具权限策略，使得：

1. 用户可以通过配置文件中的 ``permission`` 字段切换全局策略 (plan / edit / auto)；
2. 同时允许针对单个工具进行精细化的权限设置 (allow / ask / deny)；
3. 权限询问 (ASK) 通过 messagebus 与用户交互，由 ``UserInputManager`` 完成。

权限决策流程
------------
1. 命中 ``per-tool`` 显式配置 -> 直接返回；
2. 否则按 ``mode`` 进入默认策略：
   - PLAN  : 只允许只读工具，其它一律 DENY；
   - EDIT  : 只读 + 可写工具放行，Bash/Agent 这类危险工具走 ASK；
   - AUTO  : 全部 ALLOW。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set

from norma.core.tool_types import ToolRequest

logger = logging.getLogger(__name__)


# ====================== 默认工具分类 ======================

READ_ONLY_TOOLS: Set[str] = {
    "Read",
    "LS",
    "Ls",
    "Glob",
    "Grep",
    "TodoWrite",   # TodoWrite 只是写入内部 todo 列表，不影响磁盘
    "TaskList",
    "TaskGet",
}

WRITE_TOOLS: Set[str] = {
    "Edit",
    "Write",
    "TaskCreate",
    "TaskUpdate",
}

DANGEROUS_TOOLS: Set[str] = {
    "Bash",
    "Agent",
    "Skill",
}


# ====================== 枚举 ======================

class PermissionMode(str, Enum):
    """全局权限模式"""

    PLAN = "plan"
    EDIT = "edit"
    AUTO = "auto"

    @classmethod
    def from_value(cls, value: Any, default: "PermissionMode") -> "PermissionMode":
        if value is None:
            return default
        if isinstance(value, PermissionMode):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            logger.warning(f"unknown permission mode '{value}', fallback to {default.value}")
            return default


class PermissionDecision(str, Enum):
    """工具权限决策结果"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

    @classmethod
    def from_value(cls, value: Any) -> Optional["PermissionDecision"]:
        if value is None:
            return None
        if isinstance(value, PermissionDecision):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            logger.warning(f"unknown permission rule value '{value}'")
            return None


# ====================== 配置 ======================

@dataclass
class PermissionRule:
    """单个工具权限"""

    tool_name: str
    decision: PermissionDecision


@dataclass
class PermissionConfig:
    """权限配置 - 由 cli 从 ~/.norma/config.json 加载"""

    mode: PermissionMode = PermissionMode.AUTO
    tools: Dict[str, PermissionDecision] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "PermissionConfig":
        if not data:
            return cls()

        mode = PermissionMode.from_value(data.get("mode"), PermissionMode.AUTO)

        raw_tools = data.get("tools") or {}
        tools: Dict[str, PermissionDecision] = {}
        for name, value in raw_tools.items():
            decision = PermissionDecision.from_value(value)
            if decision is not None:
                tools[name] = decision

        return cls(mode=mode, tools=tools)


# ====================== 权限检查器 ======================

class PermissionChecker:
    """
    工具权限检查器

    使用方式::

        checker = PermissionChecker(config)
        decision = checker.check(tool_request)
        if decision == PermissionDecision.ASK:
            ok = await user_input_mgr.request_confirmation(...)
    """

    def __init__(
        self,
        config: Optional[PermissionConfig] = None,
        read_only_tools: Optional[Set[str]] = None,
        write_tools: Optional[Set[str]] = None,
        dangerous_tools: Optional[Set[str]] = None,
    ):
        self.config = config or PermissionConfig()
        self.read_only_tools = set(read_only_tools or READ_ONLY_TOOLS)
        self.write_tools = set(write_tools or WRITE_TOOLS)
        self.dangerous_tools = set(dangerous_tools or DANGEROUS_TOOLS)

    # ---------- 公开 API ----------

    def check(
        self,
        tool_request: ToolRequest,
        is_readonly: bool = False,
        is_destructive: bool = False,
    ) -> PermissionDecision:
        """对单个工具请求做权限决策。

        ``is_readonly`` / ``is_destructive`` 来自工具实例自报的注解（如 MCP
        工具的 ``readOnlyHint`` / ``destructiveHint``）--内置工具按名落入静态
        分类集合，MCP 工具名带 ``mcp__`` 前缀不在任何静态集合中，需靠注解才能
        正确分类（否则只读 MCP 工具在 EDIT 模式会被「未知 -> ASK」误询问）。
        """
        tool_name = tool_request.tool_call_name

        # 1) per-tool 显式配置优先
        if tool_name in self.config.tools:
            decision = self.config.tools[tool_name]
            logger.debug(f"permission: explicit config {tool_name} -> {decision.value}")
            return decision

        # 2) 走 mode 默认策略
        mode = self.config.mode

        if mode == PermissionMode.AUTO:
            return PermissionDecision.ALLOW

        if mode == PermissionMode.PLAN:
            # 静态分类优先于自报注解：危险工具即便误报 is_readonly 仍 DENY，
            # 防止错误/恶意注解把危险工具「洗白」为只读而绕过 PLAN 门禁。
            if tool_name in self.dangerous_tools:
                return PermissionDecision.DENY
            if tool_name in self.read_only_tools or is_readonly:
                return PermissionDecision.ALLOW
            return PermissionDecision.DENY

        # mode == EDIT
        if tool_name in self.read_only_tools or tool_name in self.write_tools:
            return PermissionDecision.ALLOW
        if tool_name in self.dangerous_tools:
            return PermissionDecision.ASK
        # 静态集合未覆盖（如 mcp__ 工具）：信任自报注解
        if is_readonly:
            return PermissionDecision.ALLOW
        if is_destructive:
            return PermissionDecision.ASK
        # 未知工具 -> 询问
        return PermissionDecision.ASK

    # ---------- 调试用 ----------

    def describe(self) -> Dict[str, Any]:
        return {
            "mode": self.config.mode.value,
            "tools": {k: v.value for k, v in self.config.tools.items()},
            "read_only_tools": sorted(self.read_only_tools),
            "write_tools": sorted(self.write_tools),
            "dangerous_tools": sorted(self.dangerous_tools),
        }
