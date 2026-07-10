"""AgentResponse 自动填充契约回归测试（headless）。

锁定 AgentResponse 的 post-init 自动填充不变量：

1. **response 自动填充**：``response=None`` 且 ``message_list`` 末条为
   AssistantMessage 时，``response`` 取其 ``content``；此前用 pydantic
   ``@model_validator`` 但 AgentResponse 是 stdlib ``@dataclass``，validator
   永不被调用 -> 自动填充是死代码、response 保持 None -> 前端可能拿到空回复。
   改用 stdlib ``__post_init__`` 后自动填充真正生效；
2. **显式 response 不被覆盖**：已赋值的 ``response`` 不被 post-init 改写；
3. **空 message_list 保持 None**：``message_list=[]`` 且 ``response=None``
   时保持 None（前端走分隔符路径，如 test_tui_render 的思考块用例）；
4. **tool_call_nums 自动计数**：``tool_call_nums=None`` 时按 ToolMessage 计数。

运行：``python -m norma.core.test_agent_types``
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.agent_types import AgentResponse  # noqa: E402
from norma.core.llm_types import AssistantMessage, UserMessage, ToolMessage  # noqa: E402
from norma.core.tool_types import (  # noqa: E402
    ToolRequest,
    ToolRequestResult,
)


def _tool_msg(call_id: str) -> ToolMessage:
    req = ToolRequest(
        tool_call_id=call_id, tool_call_name="Read",
        tool_call_arguments={"file_path": "x.py"})
    res = ToolRequestResult(request=req, result="ok", content="ok")
    return ToolMessage(tool_result=res, content="ok")


def test_response_autofill_from_last_assistant_message() -> bool:
    ar = AgentResponse(
        agent_name="t",
        input_message=[UserMessage(content="q")],
        tools=None,
        prompt_usage=None,
        event_list=[],
        message_list=[AssistantMessage(content="hello", tool_calls=None)],
        response=None,
        tool_call_sequence=None,
        tool_call_nums=None,
    )
    assert ar.response == "hello", (
        f"response=None + 末条 AssistantMessage 应自动填充为 'hello'，"
        f"实际 {ar.response!r}（post-init 未生效则保持 None -> 前端空回复）")
    return True


def test_explicit_response_preserved() -> bool:
    ar = AgentResponse(
        agent_name="t",
        input_message=[],
        tools=None,
        prompt_usage=None,
        event_list=[],
        message_list=[AssistantMessage(content="ignored", tool_calls=None)],
        response="explicit",
        tool_call_sequence=None,
        tool_call_nums=None,
    )
    assert ar.response == "explicit", (
        f"显式 response 不应被 post-init 覆盖，实际 {ar.response!r}")
    return True


def test_empty_message_list_keeps_response_none() -> bool:
    # 前端分隔符路径依赖此：message_list 空 + response=None -> 保持 None
    ar = AgentResponse(
        agent_name="t",
        input_message=[],
        tools=None,
        prompt_usage=None,
        event_list=[],
        message_list=[],
        response=None,
        tool_call_sequence=None,
        tool_call_nums=None,
    )
    assert ar.response is None, (
        f"空 message_list + response=None 应保持 None，实际 {ar.response!r}")
    return True


def test_tool_call_nums_autofill() -> bool:
    ar = AgentResponse(
        agent_name="t",
        input_message=[UserMessage(content="q")],
        tools=None,
        prompt_usage=None,
        event_list=[],
        message_list=[
            AssistantMessage(content="calling", tool_calls=None),
            _tool_msg("tc1"),
            _tool_msg("tc2"),
            AssistantMessage(content="done", tool_calls=None),
        ],
        response=None,
        tool_call_sequence=None,
        tool_call_nums=None,
    )
    assert ar.tool_call_nums == 2, (
        f"tool_call_nums=None 应按 ToolMessage 计数得 2，实际 {ar.tool_call_nums}")
    # response 也应从末条 AssistantMessage 自动填充
    assert ar.response == "done", (
        f"response 应取末条 AssistantMessage='done'，实际 {ar.response!r}")
    return True


def _amain() -> int:
    tests = [
        ("response_autofill_from_last_assistant_message",
         test_response_autofill_from_last_assistant_message),
        ("explicit_response_preserved", test_explicit_response_preserved),
        ("empty_message_list_keeps_response_none",
         test_empty_message_list_keeps_response_none),
        ("tool_call_nums_autofill", test_tool_call_nums_autofill),
    ]
    failures = 0
    for name, fn in tests:
        try:
            ok = fn()
            assert ok, f"{name} returned False/None"
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_agent_types_headless() -> None:
    """pytest 入口。"""
    assert _amain() == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(_amain())
