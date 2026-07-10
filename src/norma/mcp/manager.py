"""
MCP 管理器

管理多个 MCP 服务器连接，统一工具发现和注册。
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from norma.mcp.client import MCPClient, MCPServerConfig
from norma.mcp.tool import MCPTool

logger = logging.getLogger(__name__)


class MCPManager:
    """MCP 管理器 - 管理多个 MCP 服务器连接"""

    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._tools: List[MCPTool] = []

    @property
    def tools(self) -> List[MCPTool]:
        return self._tools

    @property
    def clients(self) -> Dict[str, MCPClient]:
        return self._clients

    def load_config(self, config: dict) -> None:
        """从配置字典加载 MCP 服务器配置

        配置格式:
        {
            "mcpServers": {
                "server-name": {
                    "command": "python",
                    "args": ["-m", "my_mcp_server"],
                    "env": {}
                }
            }
        }
        """
        servers = config.get("mcpServers", {})
        for name, server_config in servers.items():
            if name in self._clients:
                logger.warning(f"MCP server '{name}' already configured, skipping")
                continue

            mcp_config = MCPServerConfig(**server_config)
            client = MCPClient(server_name=name, config=mcp_config)
            self._clients[name] = client
            logger.info(f"Loaded MCP server config: {name}")

    async def connect_all(self) -> None:
        """连接所有已配置的 MCP 服务器"""
        for name, client in self._clients.items():
            try:
                await client.connect()
                tools = await client.discover_tools()
                for tool_info in tools:
                    mcp_tool = MCPTool(
                        client=client,
                        tool_info=tool_info,
                        server_name=name,
                    )
                    self._tools.append(mcp_tool)
                logger.info(
                    f"MCP server '{name}': {len(tools)} tools registered"
                )
            except Exception as e:
                logger.error(f"Failed to connect MCP server '{name}': {e}")
                # connect/discover 抛错时 client 可能已启动子进程与 read loop，
                # 必须断开以免泄漏孤儿进程；disconnect 对未启动的 client 是 no-op。
                try:
                    await client.disconnect()
                except Exception as de:
                    logger.error(f"Error cleaning up MCP server '{name}': {de}")

    async def disconnect_all(self) -> None:
        """断开所有 MCP 服务器连接"""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting MCP server '{name}': {e}")
        self._tools.clear()

    def get_tool_names(self) -> List[str]:
        """获取所有 MCP 工具名称"""
        return [t.name for t in self._tools]
