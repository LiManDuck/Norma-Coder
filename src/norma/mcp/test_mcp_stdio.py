"""MCP stdio 端到端回归测试（headless，起一个 mock MCP 服务器子进程）。

验证：MCPManager.load_config -> connect_all -> discover_tools ->
MCPTool 命名前缀 mcp__server__tool -> execute() -> 只读注解 ->
错误（isError）处理 -> 注册进 NormaArtifact 并经 execute_tools 分区执行。

mock 服务器用 stdio 说 JSON-RPC 2.0，提供两个工具：
  - echo(text)            非只读
  - add(a,b)              readOnlyHint=True

运行：``python -m norma.mcp.test_mcp_stdio``
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.tool_types import ToolRequest  # noqa: E402
from norma.tool.tool_core import NormaArtifact  # noqa: E402
from norma.mcp.client import MCPServerConfig  # noqa: E402
from norma.mcp.manager import MCPManager  # noqa: E402

# ---- mock MCP 服务器脚本 ----
_MOCK_SERVER = r'''
import json, sys

def handle(method, params):
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock", "version": "0.1"},
        }
    if method == "tools/list":
        return {"tools": [
            {"name": "echo", "description": "echo text back",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]}},
            {"name": "add", "description": "add two numbers",
             "inputSchema": {"type": "object",
                             "properties": {"a": {"type": "number"},
                                            "b": {"type": "number"}}},
             "annotations": {"readOnlyHint": True}},
        ]}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "echo":
            return {"content": [{"type": "text", "text": "echo: " + str(args.get("text", ""))}],
                    "isError": False}
        if name == "add":
            return {"content": [{"type": "text", "text": "sum: " + str(args.get("a", 0) + args.get("b", 0))}],
                    "isError": False}
        if name == "boom":
            return {"content": [{"type": "text", "text": "intentional failure"}],
                    "isError": True}
        return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
    return {}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if "id" in msg:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg.get("method"), msg.get("params", {}))}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        # 通知（无 id）无需响应

main()
'''


async def _run(tmpdir: str) -> None:
    server_path = Path(tmpdir) / "mock_mcp_server.py"
    server_path.write_text(_MOCK_SERVER, encoding="utf-8")

    mgr = MCPManager()
    mgr.load_config({
        "mcpServers": {
            "mock": {
                "command": sys.executable,
                "args": [str(server_path)],
            }
        }
    })
    assert "mock" in mgr.clients, "load_config 未注册 mock 服务器"

    await mgr.connect_all()
    try:
        tools = mgr.tools
        assert len(tools) == 2, f"期望发现 2 个工具，实际 {len(tools)}: {[t.name for t in tools]}"

        echo = next(t for t in tools if t.name.endswith("__echo"))
        add = next(t for t in tools if t.name.endswith("__add"))
        assert echo.name == "mcp__mock__echo", f"echo 命名前缀错误: {echo.name}"
        assert add.name == "mcp__mock__add", f"add 命名前缀错误: {add.name}"
        assert add.is_readonly is True, "add 应为只读（readOnlyHint）"
        assert echo.is_readonly is False, "echo 应为非只读"
        print("[PASS] discover + naming + annotations")

        # 直接 execute echo
        req = ToolRequest(tool_call_id="c1", tool_call_name=echo.name,
                          tool_call_arguments={"text": "hello"})
        res = await echo.execute(req)
        assert res.is_error is False, f"echo 不应报错: {res.content}"
        assert res.content == "echo: hello", f"echo 返回不符: {res.content!r}"
        print("[PASS] MCPTool.execute echo")

        # 错误工具（isError -> RuntimeError 被 execute 捕获为 is_error=True）
        from norma.mcp.client import MCPClient, MCPToolInfo
        # 复用同一 client 注入一个 boom 工具信息
        boom_info = MCPToolInfo(name="boom", description="always fails",
                                inputSchema={"type": "object", "properties": {}})
        client = mgr.clients["mock"]
        from norma.mcp.tool import MCPTool
        boom = MCPTool(client=client, tool_info=boom_info, server_name="mock")
        req2 = ToolRequest(tool_call_id="c2", tool_call_name="mcp__mock__boom",
                           tool_call_arguments={})
        res2 = await boom.execute(req2)
        assert res2.is_error is True, "boom 应返回 is_error=True"
        print("[PASS] MCPTool.execute error path")

        # 注册进 NormaArtifact，经 execute_tools 分区执行（只读 add 并发）
        artifact = NormaArtifact(tools=[echo, add])
        assert artifact.has_tool("mcp__mock__echo")
        reqs = [
            ToolRequest(tool_call_id="r1", tool_call_name="mcp__mock__add",
                        tool_call_arguments={"a": 2, "b": 3}),
            ToolRequest(tool_call_id="r2", tool_call_name="mcp__mock__add",
                        tool_call_arguments={"a": 10, "b": 20}),
        ]
        results = await artifact.execute_tools(reqs)
        assert len(results) == 2
        # 结果按原序返回
        assert results[0].content == "sum: 5", f"r1 不符: {results[0].content!r}"
        assert results[1].content == "sum: 30", f"r2 不符: {results[1].content!r}"
        assert all(not r.is_error for r in results)
        print("[PASS] NormaArtifact.execute_tools with MCP tools (ordered)")
    finally:
        await mgr.disconnect_all()


async def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            await _run(tmpdir)
        except AssertionError as exc:
            print(f"[FAIL] {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[ERROR] {exc}")
            traceback.print_exc()
            failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL MCP stdio tests passed")
    return 0


def test_mcp_stdio_headless() -> None:
    """pytest 入口"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        assert asyncio.run(_run(tmpdir)) is None or True


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(asyncio.run(main()))
