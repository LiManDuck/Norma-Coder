"""
工具核心模块 - 简化版
负责工具的注册、删除、执行

NormaArtifact：工具聚合，提供注册 / 查询 / 并发执行接口。

权限与执行模式已迁移至 ``norma.permission``（PermissionChecker / PermissionMode /
PermissionDecision），本模块不再持有权限相关逻辑。
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from norma.core.tool_types import (
    Tool,
    ToolRequest,
    ToolRequestResult,
    ToolSchema,
)

logger = logging.getLogger(__name__)


class ToolNotFoundError(Exception):
    """工具未找到错误"""
    pass


class NormaArtifact:
    """
    Norma 工具系统
    提供工具的注册、查询和并发执行功能。
    """

    def __init__(
        self,
        tools: Optional[List[Tool]] = None,
        max_concurrent: int = 10,
    ):
        """
        初始化工具系统。

        Args:
            tools: 初始工具列表。
            max_concurrent: 最大并发执行数量。
        """
        self._tools: Dict[str, Tool] = {}
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

        if tools:
            for tool in tools:
                self.register_tool(tool)

    # ==================== 注册 / 查询 ====================

    def register_tool(self, tool: Tool) -> None:
        """注册一个工具。重名时抛出 ValueError。"""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already exists")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    def unregister_tool(self, tool_name: str) -> bool:
        """删除一个工具，返回是否成功。"""
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.info(f"Unregistered tool: {tool_name}")
            return True
        return False

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        """获取工具实例。"""
        return self._tools.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否存在。"""
        return tool_name in self._tools

    def list_tools(self) -> List[str]:
        """列出所有已注册的工具名称。"""
        return list(self._tools.keys())

    def get_tool_schemas(self) -> List[ToolSchema]:
        """获取所有工具的 Schema（用于 LLM API 调用）。"""
        return [tool.schema for tool in self._tools.values()]

    # ==================== 工具执行 ====================

    async def execute_tool(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行单个工具调用。"""
        tool_name = tool_request.tool_call_name
        tool = self._tools.get(tool_name)

        if not tool:
            logger.error(f"Tool '{tool_name}' not found")
            return ToolRequestResult(
                request=tool_request,
                result=None,
                content=f'{{"error": "Tool \'{tool_name}\' not found"}}',
                is_error=True,
                execution_times=0.0,
            )

        try:
            async with self._semaphore:
                logger.info(
                    f"Executing tool '{tool_name}' "
                    f"(call_id: {tool_request.tool_call_id})"
                )
                return await tool.execute(tool_request)
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
            return ToolRequestResult(
                request=tool_request,
                result=None,
                content=f'{{"error": "{str(e)}"}}',
                is_error=True,
                execution_times=0.0,
            )

    async def execute_tools(
        self,
        tool_requests: List[ToolRequest],
    ) -> List[ToolRequestResult]:
        """批量并发执行工具调用。"""
        if not tool_requests:
            return []
        logger.info(f"Executing {len(tool_requests)} tools concurrently")
        tasks = [self.execute_tool(request) for request in tool_requests]
        return await asyncio.gather(*tasks, return_exceptions=False)

    # ==================== 辅助方法 ====================

    def get_status(self) -> Dict[str, Any]:
        """获取系统状态。"""
        return {
            "registered_tools": len(self._tools),
            "tool_names": list(self._tools.keys()),
            "max_concurrent": self.max_concurrent,
        }

    def clear_all(self) -> None:
        """清空所有工具。"""
        self._tools.clear()
        logger.info("Cleared all tools")
