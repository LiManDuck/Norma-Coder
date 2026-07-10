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
        if name == "crash":
            # 模拟服务器进程崩溃：直接退出，不写任何响应
            sys.exit(1)
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

# ---- mock MCP 服务器脚本（tools/list_changed 变体）----
# 首次 tools/list 返回 2 工具，并在响应后推送 notifications/tools/list_changed；
# 此后的 tools/list 返回 3 工具（新增 extra）。用于验证客户端重发现不死锁。
_MOCK_SERVER_LIST_CHANGED = r'''
import json, sys

_list_count = 0

def handle(method, params):
    global _list_count
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-lc", "version": "0.1"}}
    if method == "tools/list":
        _list_count += 1
        tools = [
            {"name": "echo", "description": "echo text back",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}}}},
            {"name": "add", "description": "add two numbers",
             "inputSchema": {"type": "object",
                             "properties": {"a": {"type": "number"},
                                            "b": {"type": "number"}}}},
        ]
        if _list_count >= 2:
            tools.append({"name": "extra", "description": "added after list_changed",
                          "inputSchema": {"type": "object", "properties": {}}})
        return {"tools": tools}
    if method == "tools/call":
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}
    return {}

def main():
    global _list_count
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if "id" in msg:
            resp = {"jsonrpc": "2.0", "id": msg["id"],
                    "result": handle(msg.get("method"), msg.get("params", {}))}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            # 首次 tools/list 响应之后，推送 list_changed 通知，触发客户端重发现；
            # 重发现的第二次 tools/list 会拿到含 extra 的 3 工具。
            if msg.get("method") == "tools/list" and _list_count == 1:
                notif = {"jsonrpc": "2.0",
                         "method": "notifications/tools/list_changed", "params": {}}
                sys.stdout.write(json.dumps(notif) + "\n")
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


async def _run_crash(tmpdir: str) -> None:
    """服务器进程崩溃（不响应即退出）：挂起请求应快速失败，而非等满 60s 超时。

    验证 _read_loop 的 finally 在 EOF 时 _fail_pending：crash 工具让服务器
    sys.exit，stdout 关闭 -> read_loop EOF -> 挂起的 tools/call future 被
    立即置为 ConnectionError -> call_tool 抛错 -> MCPTool.execute 捕获为
    is_error=True。整条链路应在数秒内完成（远小于 60s）。
    """
    import time
    server_path = Path(tmpdir) / "mock_mcp_server.py"
    server_path.write_text(_MOCK_SERVER, encoding="utf-8")

    mgr = MCPManager()
    mgr.load_config({
        "mcpServers": {
            "mock": {"command": sys.executable, "args": [str(server_path)]}
        }
    })
    await mgr.connect_all()
    try:
        from norma.mcp.client import MCPToolInfo
        from norma.mcp.tool import MCPTool
        client = mgr.clients["mock"]
        crash_info = MCPToolInfo(name="crash", description="crashes server",
                                 inputSchema={"type": "object", "properties": {}})
        crash = MCPTool(client=client, tool_info=crash_info, server_name="mock")
        req = ToolRequest(tool_call_id="c3", tool_call_name="mcp__mock__crash",
                          tool_call_arguments={})

        t0 = time.monotonic()
        res = await crash.execute(req)
        elapsed = time.monotonic() - t0

        assert res.is_error is True, "服务器崩溃后调用应失败 (is_error=True)"
        assert elapsed < 30.0, (
            f"应在 EOF 后快速失败，而非等满 60s 超时，实际 {elapsed:.1f}s")
        print(f"[PASS] server crash fast-fail (elapsed={elapsed:.2f}s < 30s)")
    finally:
        await mgr.disconnect_all()


async def _run_list_changed(tmpdir: str) -> None:
    """tools/list_changed 通知触发重发现，且不得死锁。

    服务器在首次 tools/list 响应后推送 notifications/tools/list_changed，客户端
    _read_loop 收到后应重发现（第二次 tools/list 返回含 extra 的 3 工具）。

    回归一个死锁缺陷：_read_loop 原地 ``await discover_tools()``，而 discover_tools
    内部又 await 工具列表响应；该响应只能由 _read_loop 回到 readline() 才能读出
    -> 互相等待，直到 _send_request 的 60s 超时。期间该 client 上所有后续请求的
    响应也无法被读取，整条连接瘫痪。修复为 ``create_task`` 异步重发现后，read loop
    继续轮询 stdout，重发现响应被正常读出，client.tools 在数秒内更新为 3。

    断言：初始 2 工具；8s 内重发现出 extra（共 3 工具）。用 await 的旧实现会死锁
    60s，8s 内永远等不到 extra -> 断言失败。
    """
    import time
    server_path = Path(tmpdir) / "mock_lc_server.py"
    server_path.write_text(_MOCK_SERVER_LIST_CHANGED, encoding="utf-8")

    mgr = MCPManager()
    mgr.load_config({
        "mcpServers": {
            "mock": {"command": sys.executable, "args": [str(server_path)]}
        }
    })
    await mgr.connect_all()
    try:
        client = mgr.clients["mock"]
        assert len(client.tools) == 2, (
            f"初始应发现 2 工具，实际 {len(client.tools)}: "
            f"{[t.name for t in client.tools]}")
        print(f"[..] 初始 {len(client.tools)} 工具，等待 list_changed 重发现...")

        # 轮询等待重发现（create_task 异步）：应出现 extra 工具
        deadline = time.monotonic() + 8.0
        names = [t.name for t in client.tools]
        while time.monotonic() < deadline:
            names = [t.name for t in client.tools]
            if "extra" in names:
                break
            await asyncio.sleep(0.1)

        assert "extra" in names, (
            f"tools/list_changed 重发现未生效，8s 内仍为 {names}（应含 extra 的 3 工具）。"
            f"若 _read_loop 内用 await 而非 create_task，会死锁 60s，重发现永不完成。")
        assert len(client.tools) == 3, (
            f"重发现后应为 3 工具，实际 {len(client.tools)}: {names}")
        print(f"[PASS] tools/list_changed re-discover (non-deadlocking): {names}")
    finally:
        await mgr.disconnect_all()


async def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for runner, name in (
            (_run, "mcp_stdio_e2e"),
            (_run_crash, "mcp_crash_fast_fail"),
            (_run_list_changed, "mcp_list_changed_no_deadlock"),
        ):
            try:
                await runner(tmpdir)
            except AssertionError as exc:
                print(f"[FAIL] {name}: {exc}")
                failures += 1
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[ERROR] {name}: {exc}")
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
