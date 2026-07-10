"""
MCP 客户端 - 通过 stdio 与 MCP 服务器通信

实现 JSON-RPC 2.0 协议，支持 initialize / tools/list / tools/call。
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """MCP 服务器配置"""
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    type: str = "stdio"

    def resolve_env_vars(self) -> dict:
        """解析配置中的环境变量引用"""
        resolved = dict(os.environ)
        for key, value in self.env.items():
            resolved[key] = value
        return resolved


class MCPToolInfo(BaseModel):
    """从 MCP 服务器发现的工具信息"""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict, alias="inputSchema")
    annotations: Optional[Dict[str, Any]] = None


class MCPClient:
    """
    MCP 客户端 - 通过 stdio 与 MCP 服务器通信

    使用 JSON-RPC 2.0 协议。
    """

    def __init__(self, server_name: str, config: MCPServerConfig):
        self.server_name = server_name
        self.config = config
        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._connected = False
        self._server_info: Optional[Dict[str, Any]] = None
        self._capabilities: Optional[Dict[str, Any]] = None
        self._instructions: Optional[str] = None
        self._tools: List[MCPToolInfo] = []
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._process is not None and self._process.returncode is None

    @property
    def tools(self) -> List[MCPToolInfo]:
        return self._tools

    @property
    def instructions(self) -> Optional[str]:
        return self._instructions

    async def connect(self) -> None:
        """启动 MCP 服务器进程并完成初始化握手"""
        env = self.config.resolve_env_vars()
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            logger.error(f"MCP server command not found: {self.config.command}")
            raise
        except Exception as e:
            logger.error(f"Failed to start MCP server '{self.server_name}': {e}")
            raise

        # 启动 stdout 读取循环
        self._reader_task = asyncio.create_task(self._read_loop())

        # 发送 initialize 请求
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "norma-coder",
                "version": "0.1.0",
            },
        })

        self._capabilities = result.get("capabilities", {})
        self._server_info = result.get("serverInfo", {})
        self._instructions = result.get("instructions")

        # 发送 initialized 通知
        await self._send_notification("notifications/initialized", {})
        self._connected = True

        logger.info(f"MCP server '{self.server_name}' connected: {self._server_info}")

    async def discover_tools(self) -> List[MCPToolInfo]:
        """发现 MCP 服务器提供的工具"""
        result = await self._send_request("tools/list", {})
        tools_data = result.get("tools", [])
        self._tools = [MCPToolInfo(**t) for t in tools_data]
        logger.info(f"Discovered {len(self._tools)} tools from '{self.server_name}'")
        return self._tools

    async def _rediscover_tools(self) -> None:
        """处理 notifications/tools/list_changed：重新发现工具。

        必须在独立任务中运行（由 _read_loop 经 ``create_task`` 调度），不可在
        _read_loop 内直接 ``await``，否则会与读取循环互相等待而死锁（详见
        _read_loop 内注释）。此处仅捕获并记录异常，避免独立任务里的异常被静默吞掉。
        """
        try:
            await self.discover_tools()
        except Exception as e:
            logger.error(f"Failed to re-discover tools: {e}")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用 MCP 工具"""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        is_error = result.get("isError", False)
        content = result.get("content", [])

        if is_error:
            error_text = ""
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    error_text += item.get("text", "")
            raise RuntimeError(f"MCP tool '{tool_name}' error: {error_text}")

        # 提取文本内容
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))

        return "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)

    async def disconnect(self) -> None:
        """断开与 MCP 服务器的连接"""
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        logger.info(f"MCP server '{self.server_name}' disconnected")

    async def _send_request(self, method: str, params: dict) -> Any:
        """发送 JSON-RPC 请求并等待响应"""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("MCP client not connected")

        self._request_id += 1
        request_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=60.0)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"MCP request timeout: {method}")
        except Exception:
            self._pending_requests.pop(request_id, None)
            raise

    async def _send_notification(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无 id，不期望响应）"""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("MCP client not connected")

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """持续从 MCP 服务器 stdout 读取 JSON-RPC 消息"""
        if self._process is None or self._process.stdout is None:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # EOF：服务器关闭 stdout / 崩溃 / 退出

                try:
                    message = json.loads(line.decode())
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from MCP server: {line!r}")
                    continue

                # 响应消息
                if "id" in message:
                    request_id = message["id"]
                    future = self._pending_requests.pop(request_id, None)
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(
                                RuntimeError(f"MCP error: {error.get('message', 'unknown')}")
                            )
                        else:
                            future.set_result(message.get("result", {}))

                # 通知消息
                elif "method" in message:
                    method = message["method"]
                    if method == "notifications/tools/list_changed":
                        logger.info(f"MCP server '{self.server_name}' tools changed, re-discovering")
                        # 必须用 create_task 异步重发现，绝不能 await：_read_loop 是
                        # 唯一读取 stdout 的协程，若在此 await discover_tools()（其内部
                        # 又 await 工具列表响应），本协程会挂起等响应、而响应只能由本协程
                        # 回到 readline() 才能读出 -> 死锁，直到 _send_request 的 60s
                        # 超时。期间该 client 上所有后续请求（tools/call 等）的响应同样
                        # 无法被读取，整条连接瘫痪 60s。改 create_task 让 read loop 继续
                        # 轮询 stdout，重发现响应由它正常读出并结算 future。
                        asyncio.create_task(self._rediscover_tools())

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"MCP read loop error: {e}")
        finally:
            # 连接断开（EOF/崩溃/取消）：立即失败所有挂起请求，否则调用方需
            # 等满 _send_request 的 60s 超时才感知--服务器崩溃时这是很差的体验。
            self._fail_pending(ConnectionError(
                f"MCP server '{self.server_name}' closed connection"
            ))

    def _fail_pending(self, exc: Exception) -> None:
        """把所有挂起请求的 future 置为异常（连接断开时调用）"""
        if not self._pending_requests:
            return
        pending = list(self._pending_requests.values())
        self._pending_requests.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(exc)
