"""
MCP 工具包装器

将 MCP 服务器提供的工具包装为 Norma 内部的 Tool 接口，
使其可以像内置工具一样被 agent 调用。
"""

import json
import time
import logging
from typing import Any, Dict, Optional

from norma.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult,
)
from norma.mcp.client import MCPClient, MCPToolInfo

logger = logging.getLogger(__name__)


class MCPTool(Tool):
    """MCP 工具包装器 - 将远程 MCP 工具映射为本地 Tool 接口"""

    def __init__(
        self,
        client: MCPClient,
        tool_info: MCPToolInfo,
        server_name: str,
    ):
        self._client = client
        self._tool_info = tool_info
        self._server_name = server_name
        self._prefixed_name = f"mcp__{server_name}__{tool_info.name}"

    @property
    def name(self) -> str:
        return self._prefixed_name

    @property
    def description(self) -> str:
        return self._tool_info.description or f"MCP tool from {self._server_name}"

    @property
    def schema(self) -> ToolSchema:
        input_schema = self._tool_info.input_schema
        params = ParametersSchema(
            type=input_schema.get("type", "object"),
            properties=input_schema.get("properties", {}),
            required=input_schema.get("required"),
            additionalProperties=input_schema.get("additionalProperties"),
        )
        return ToolSchema(
            name=self._prefixed_name,
            description=self.description,
            parameters=params,
            strict=False,
        )

    @property
    def is_readonly(self) -> bool:
        """根据 MCP annotations 判断是否只读"""
        if self._tool_info.annotations:
            return self._tool_info.annotations.get("readOnlyHint", False)
        return False

    @property
    def is_destructive(self) -> bool:
        """根据 MCP annotations 判断是否破坏性"""
        if self._tool_info.annotations:
            return self._tool_info.annotations.get("destructiveHint", False)
        return False

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行 MCP 工具调用"""
        start_time = time.time()

        try:
            # 解析参数
            if isinstance(tool_request.tool_call_arguments, str):
                args = json.loads(tool_request.tool_call_arguments)
            else:
                args = tool_request.tool_call_arguments

            # 调用 MCP 服务器
            result = await self._client.call_tool(
                tool_name=self._tool_info.name,
                arguments=args,
            )

            execution_time = time.time() - start_time
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

            return ToolRequestResult(
                request=tool_request,
                result=result,
                content=content,
                is_error=False,
                execution_times=execution_time,
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"MCP tool '{self._prefixed_name}' execution failed: {e}")
            error_content = json.dumps({"error": str(e)}, ensure_ascii=False)

            return ToolRequestResult(
                request=tool_request,
                result=None,
                content=error_content,
                is_error=True,
                execution_times=execution_time,
            )
