"""AgentTool（子 agent 调度）回归测试（headless）。

验证：
- 前台模式：execute(prompt) 返回 status=done + response + session_id
- 缺 prompt -> is_error
- 后台模式：run_background=true 立即返回 running；同 session_id 空 prompt 查询 -> done + response
- session 复用：同 session_id 复用既有 session

运行：``python -m norma.tool.agent_tool.test_agent_tool``
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncGenerator, Optional

_SRC = Path(__file__).resolve().parents[4]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.agent_types import AgentResponse  # noqa: E402
from norma.core.tool_types import ToolRequest  # noqa: E402
from norma.tool.agent_tool.agent_tool import AgentTool  # noqa: E402


class _FakeAgent:
    """最小子 agent mock：run() 产出一条 AgentResponse，内容含 prompt 标记"""

    def __init__(self, name: Optional[str] = None):
        self.name = name or "fake-sub"
        self.calls = 0

    async def run(self, prompt: str) -> AsyncGenerator[AgentResponse, None]:
        self.calls += 1
        yield AgentResponse(
            agent_name=self.name,
            input_message=[],
            tools=[],
            prompt_usage=None,
            event_list=[],
            message_list=[],
            response=f"SUB<{prompt}>",
            tool_call_sequence=None,
            tool_call_nums=0,
        )


def _factory(name=None):
    return _FakeAgent(name=name)


async def test_foreground() -> None:
    tool = AgentTool(agent_factory=_factory)
    req = ToolRequest(
        tool_call_id="a1", tool_call_name="Agent",
        tool_call_arguments={"prompt": "调研 X"},
    )
    res = await tool.execute(req)
    assert res.is_error is False, f"不应报错: {res.content}"
    payload = json.loads(res.content)
    assert payload["status"] == "done"
    assert "SUB<调研 X>" in payload["response"], f"应含子 agent 响应: {payload}"
    assert "session_id" in payload
    print("[PASS] foreground execute")


async def test_missing_prompt() -> None:
    tool = AgentTool(agent_factory=_factory)
    req = ToolRequest(
        tool_call_id="a2", tool_call_name="Agent",
        tool_call_arguments={},
    )
    res = await tool.execute(req)
    assert res.is_error is True
    assert "prompt" in res.content
    print("[PASS] missing prompt -> error")


async def test_background_and_query() -> None:
    tool = AgentTool(agent_factory=_factory)
    # 启动后台任务
    req = ToolRequest(
        tool_call_id="a3", tool_call_name="Agent",
        tool_call_arguments={"prompt": "长任务", "run_background": True},
    )
    res = await tool.execute(req)
    payload = json.loads(res.content)
    assert payload["status"] == "running", f"后台应立即返回 running: {payload}"
    sid = payload["session_id"]

    # 轮询查询（空 prompt + 同 session_id）
    done = None
    for _ in range(30):
        qreq = ToolRequest(
            tool_call_id="a4", tool_call_name="Agent",
            tool_call_arguments={"prompt": "", "session_id": sid},
        )
        qres = await tool.execute(qreq)
        qp = json.loads(qres.content)
        if qp.get("status") == "done":
            done = qp
            break
        await asyncio.sleep(0.1)
    assert done is not None, "后台任务应在轮询内完成"
    assert "SUB<长任务>" in done.get("response", ""), f"查询应返回响应: {done}"
    print("[PASS] background + query")


async def test_session_reuse() -> None:
    tool = AgentTool(agent_factory=_factory)
    sid = "fixed-session-1"
    req1 = ToolRequest(
        tool_call_id="a5", tool_call_name="Agent",
        tool_call_arguments={"prompt": "第一问", "session_id": sid},
    )
    await tool.execute(req1)
    sess1 = tool._sessions[sid]
    req2 = ToolRequest(
        tool_call_id="a6", tool_call_name="Agent",
        tool_call_arguments={"prompt": "第二问", "session_id": sid},
    )
    await tool.execute(req2)
    sess2 = tool._sessions[sid]
    assert sess1 is sess2, "同 session_id 应复用同一 session 对象"
    assert len(sess2.history) == 2, f"应累计 2 轮历史，实际 {len(sess2.history)}"
    print("[PASS] session reuse")


async def main() -> int:
    failures = 0
    for fn in (
        test_foreground,
        test_missing_prompt,
        test_background_and_query,
        test_session_reuse,
    ):
        try:
            await fn()
        except AssertionError as exc:
            print(f"[FAIL] {fn.__name__}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[ERROR] {fn.__name__}: {exc}")
            traceback.print_exc()
            failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL agent-tool tests passed")
    return 0


def test_agent_tool_headless() -> None:
    assert asyncio.run(main()) == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(asyncio.run(main()))
