"""
MCP (Model Context Protocol) 集成

提供 MCP 客户端连接、工具发现和调用能力。
支持 stdio 传输方式。
"""

from norma.mcp.client import MCPClient, MCPServerConfig
from norma.mcp.tool import MCPTool
from norma.mcp.manager import MCPManager

__all__ = [
    "MCPClient",
    "MCPServerConfig",
    "MCPTool",
    "MCPManager",
]
