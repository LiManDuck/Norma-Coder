"""AgentMemory.pull_messages 回归测试（headless）。

锁定压缩关键不变量：当 ToolMessage 数量超过 ``save_toolmessage_num`` 时，最旧的
若干条 ToolMessage 的 ``content`` 被替换为占位符，但 **tool_call_id 链接必须保留**
（经共享的 ``tool_result`` 派生：``ToolMessage.tool_call_id`` -> ``tool_result.tool_call_id``
-> ``request.tool_call_id``）--否则 OpenAI tool 消息缺 tool_call_id 会被 API 拒绝。
且压缩必须**非破坏**（``history_message`` 原文不动，仅返回新列表）。非 ToolMessage
原样保留，消息顺序不变。

``pull_messages`` 被 ``norma_coder`` 在每次 LLM 请求前调用（norma_coder.py:286/453/486），
是 live 代码；此前无直接回归覆盖。

运行：``python -m norma.memory.test_agent_memory``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.llm_types import (  # noqa: E402
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolMessage,
)
from norma.core.tool_types import ToolRequest, ToolRequestResult  # noqa: E402
from norma.memory.agent_memory import AgentMemory  # noqa: E402


def _tool_msg(call_id: str, content: str, is_error: bool = False) -> ToolMessage:
    req = ToolRequest(
        tool_call_id=call_id,
        tool_call_name="Read",
        tool_call_arguments={"file_path": "x.py"},
    )
    result = ToolRequestResult(
        request=req,
        result=content,
        content=content,
        is_error=is_error,
        execution_times=0.0,
    )
    return ToolMessage(tool_result=result, content=content)


async def test_pull_preserves_tool_call_id_under_compression() -> bool:
    """超过阈值时最旧 ToolMessage content 压缩为占位符，tool_call_id 仍保留。"""
    mem = AgentMemory(message_list=None, save_toolmessage_num=3)
    # 5 条 tool 消息 > 阈值 3 -> 压缩最旧 2 条（tc1, tc2），保留最近 3 条
    mem.history_message = [
        SystemMessage(content="sys"),
        UserMessage(content="u1"),
        _tool_msg("tc1", "result1"),
        AssistantMessage(content="a1"),
        _tool_msg("tc2", "result2"),
        _tool_msg("tc3", "result3"),
        UserMessage(content="u2"),
        _tool_msg("tc4", "result4"),
        _tool_msg("tc5", "result5"),
    ]
    pulled = await mem.pull_messages()

    # 顺序与总数保持
    assert len(pulled) == 9, f"消息总数应不变，实际 {len(pulled)}"
    tool_pulled = [m for m in pulled if isinstance(m, ToolMessage)]
    assert len(tool_pulled) == 5, f"tool 消息数应不变，实际 {len(tool_pulled)}"

    # 非工具消息原样保留（顺序不变）
    assert isinstance(pulled[0], SystemMessage) and pulled[0].content == "sys"
    assert isinstance(pulled[1], UserMessage) and pulled[1].content == "u1"
    assert isinstance(pulled[3], AssistantMessage) and pulled[3].content == "a1"
    assert isinstance(pulled[6], UserMessage) and pulled[6].content == "u2"

    # 最旧 2 条 tool（tc1, tc2）content 压缩为占位符，但 tool_call_id 必须保留
    assert "缓存" in pulled[2].content, f"tc1 应压缩为占位符，实际 {pulled[2].content!r}"
    assert pulled[2].tool_call_id == "tc1", (
        f"压缩后 tool_call_id 必须保留（否则 API 拒绝 tool 消息），"
        f"实际 {pulled[2].tool_call_id!r}")
    assert "缓存" in pulled[4].content, f"tc2 应压缩为占位符，实际 {pulled[4].content!r}"
    assert pulled[4].tool_call_id == "tc2", (
        f"压缩后 tool_call_id 必须保留，实际 {pulled[4].tool_call_id!r}")

    # 最近 3 条 tool（tc3, tc4, tc5）保留原文 content + tool_call_id
    assert pulled[5].content == "result3" and pulled[5].tool_call_id == "tc3"
    assert pulled[7].content == "result4" and pulled[7].tool_call_id == "tc4"
    assert pulled[8].content == "result5" and pulled[8].tool_call_id == "tc5"

    # 非破坏：原 history 中 tc1 的 content 仍为原文（压缩仅作用于返回列表）
    assert mem.history_message[2].content == "result1", (
        f"压缩应非破坏，原 history 不应被改写，"
        f"实际 {mem.history_message[2].content!r}")
    return True


async def test_pull_no_compression_when_under_limit() -> bool:
    """ToolMessage 数量 <= 阈值时不压缩，全部原样返回。"""
    mem = AgentMemory(message_list=None, save_toolmessage_num=5)
    mem.history_message = [
        UserMessage(content="u1"),
        _tool_msg("tc1", "result1"),
        _tool_msg("tc2", "result2"),
    ]
    pulled = await mem.pull_messages()
    tool_pulled = [m for m in pulled if isinstance(m, ToolMessage)]
    assert len(tool_pulled) == 2
    # 未压缩：content 为原文，tool_call_id 保留
    assert tool_pulled[0].content == "result1"
    assert tool_pulled[0].tool_call_id == "tc1"
    assert tool_pulled[1].content == "result2"
    assert tool_pulled[1].tool_call_id == "tc2"
    return True


async def _amain() -> int:
    tests = [
        ("pull_preserves_tool_call_id_under_compression",
         test_pull_preserves_tool_call_id_under_compression),
        ("pull_no_compression_when_under_limit",
         test_pull_no_compression_when_under_limit),
    ]
    failures = 0
    for name, fn in tests:
        try:
            ok = await fn()
            assert ok, f"{name} returned False/None"
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_agent_memory_headless() -> None:
    """pytest 入口。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
