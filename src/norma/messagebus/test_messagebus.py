"""MessageBus / AgentMessageAdapter / UserInputManager 单元回归测试。

总线是前后端解耦的主干。此前其行为仅被 ``test_repl_permission`` /
``test_tui_e2e`` 间接覆盖，本文件直接锁定三块契约：

1. **总线分发**：typed ``subscribe`` 只收对应类型；``subscribe_all`` 收全部；
   ``get_history`` 按类型过滤。
2. **AgentMessageAdapter 事件->MessageType 映射**：前端渲染正确性的根契约--
   映射错则 TUI 流式/工具/响应渲染错位。逐一覆盖 8 种已映射事件 + 未映射事件
   回落 SYSTEM_LOG。
3. **UserInputManager 确认流**：``request_confirmation`` 经 future 等待，
   ``respond_confirmation`` 解锁为 True/False；无响应时超时回落 False（不挂死）。

运行：``python -m norma.messagebus.test_messagebus``
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.agent_types import (  # noqa: E402
    AgentInputEvent,
    AgentLLMRequestEvent,
    AgentLLMResponseEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentThinkDeltaEvent,
    AgentThinkEvent,
    AgentToolRequestAnswerEvent,
    AgentToolRequestEvent,
)
from norma.core.llm_types import (  # noqa: E402
    AssistantMessage,
    LLMRequest,
    LLMResponse,
    UserMessage,
)
from norma.core.tool_types import ToolRequest, ToolRequestResult  # noqa: E402
from norma.messagebus.messagebus import (  # noqa: E402
    AgentMessageAdapter,
    Message,
    MessageBus,
    MessageType,
    UserInputManager,
)


# ------------------------------------------------------------------
# 构造辅助
# ------------------------------------------------------------------

def _tool_request(name: str = "Read") -> ToolRequest:
    return ToolRequest(
        tool_call_id=str(uuid.uuid4()),
        tool_call_name=name,
        tool_call_arguments={"path": "x"},
    )


def _tool_result() -> ToolRequestResult:
    return ToolRequestResult(
        request=_tool_request(),
        result="ok",
        content="ok",
        is_error=False,
        execution_times=0.0,
    )


def _llm_response() -> LLMResponse:
    return LLMResponse(
        response_message=AssistantMessage(content="hi", tool_calls=None),
        finish_reason="stop",
    )


def _agent_response() -> AgentResponse:
    return AgentResponse(
        agent_name="Stub",
        input_message=[UserMessage(content="hi")],
        tools=[],
        prompt_usage=None,
        event_list=[],
        message_list=[],
        response="done",
    )


# (event, expected MessageType) —— 覆盖 adapter 的全部 isinstance 分支 + else 回落
_MAPPING_CASES = [
    (AgentThinkEvent(agent_name="S", reason_content="think"), MessageType.AGENT_THINK),
    (AgentTextDeltaEvent(agent_name="S", delta="d"), MessageType.AGENT_TEXT_DELTA),
    (AgentThinkDeltaEvent(agent_name="S", delta="d"), MessageType.AGENT_THINK_DELTA),
    (
        AgentToolRequestEvent(
            agent_name="S", tool_calls=[_tool_request()], tool_execution_results=[]
        ),
        MessageType.AGENT_TOOL_REQUEST,
    ),
    (
        AgentToolRequestAnswerEvent(agent_name="S", tool_execution_results=[_tool_result()]),
        MessageType.AGENT_TOOL_RESULT,
    ),
    (
        AgentLLMRequestEvent(
            agent_name="S", request=LLMRequest(messages=[UserMessage(content="x")])
        ),
        MessageType.AGENT_LLM_REQUEST,
    ),
    (AgentLLMResponseEvent(agent_name="S", response=_llm_response()), MessageType.AGENT_LLM_RESPONSE),
    (_agent_response(), MessageType.AGENT_RESPONSE),
    # 未在 adapter 显式映射的事件 -> SYSTEM_LOG（else 分支）
    (AgentInputEvent(agent_name="S", task="hi"), MessageType.SYSTEM_LOG),
]


# ------------------------------------------------------------------
# 测试
# ------------------------------------------------------------------

async def test_typed_subscribe_dispatches_only_matching() -> None:
    bus = MessageBus()
    got: list[MessageType] = []
    bus.subscribe(MessageType.AGENT_TEXT_DELTA, lambda m: got.append(m.msg_type))
    await bus.start()
    try:
        await bus.publish(Message(msg_type=MessageType.AGENT_TEXT_DELTA, payload={"d": "a"}))
        await bus.publish(Message(msg_type=MessageType.AGENT_THINK, payload={}))
        await asyncio.sleep(0.05)  # 让处理器分发
    finally:
        await bus.stop()
    # 只收到匹配的那一条
    assert got == [MessageType.AGENT_TEXT_DELTA], got


async def test_subscribe_all_receives_every_message() -> None:
    bus = MessageBus()
    all_msgs: list[MessageType] = []
    bus.subscribe_all(lambda m: all_msgs.append(m.msg_type))
    await bus.start()
    try:
        await bus.publish(Message(msg_type=MessageType.USER_INPUT, payload={}))
        await bus.publish(Message(msg_type=MessageType.AGENT_RESPONSE, payload={}))
        await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert all_msgs == [MessageType.USER_INPUT, MessageType.AGENT_RESPONSE], all_msgs


async def test_get_history_filters_by_type() -> None:
    bus = MessageBus()
    await bus.publish(Message(msg_type=MessageType.USER_INPUT, payload={}))
    await bus.publish(Message(msg_type=MessageType.AGENT_RESPONSE, payload={}))
    await bus.publish(Message(msg_type=MessageType.USER_INPUT, payload={}))
    hist_all = bus.get_history()
    hist_user = bus.get_history(MessageType.USER_INPUT)
    assert len(hist_all) == 3, hist_all
    assert len(hist_user) == 2, hist_user
    assert all(m.msg_type == MessageType.USER_INPUT for m in hist_user)


async def test_adapter_event_to_messagetype_mapping() -> None:
    """事件->MessageType 映射是前端渲染正确性的根契约，逐事件锁定。"""
    bus = MessageBus()
    adapter = AgentMessageAdapter(bus)
    seen: list[tuple] = []
    bus.subscribe_all(lambda m: seen.append((m.msg_type, type(m.payload).__name__)))
    await bus.start()
    try:
        for event, _expected in _MAPPING_CASES:
            await adapter.handle_agent_event(event, "conv1")
        await asyncio.sleep(0.08)  # 让处理器分发完全部 9 条
    finally:
        await bus.stop()
    actual_types = [mt for mt, _ in seen]
    expected_types = [exp for _evt, exp in _MAPPING_CASES]
    assert actual_types == expected_types, (
        f"mapping mismatch:\n  actual={actual_types}\n  expected={expected_types}"
    )


async def test_confirmation_allow_unlocks_future() -> None:
    bus = MessageBus()
    uim = UserInputManager(message_bus=bus)
    await bus.start()
    try:
        async def _respond() -> None:
            await asyncio.sleep(0.02)  # 让 request 先建好 future
            # 找到 pending request_id（通过总线历史里的 UI_PROMPT）
            hist = bus.get_history(MessageType.UI_PROMPT)
            assert hist, "UI_PROMPT 未发布"
            rid = hist[-1].payload["request_id"]
            await uim.respond_confirmation(rid, True)

        asyncio.create_task(_respond())
        result = await asyncio.wait_for(
            uim.request_confirmation("允许?", "conv1", timeout=3), timeout=5
        )
        assert result is True, result
    finally:
        await bus.stop()


async def test_confirmation_deny_unlocks_future() -> None:
    bus = MessageBus()
    uim = UserInputManager(message_bus=bus)
    await bus.start()
    try:
        async def _respond() -> None:
            await asyncio.sleep(0.02)
            hist = bus.get_history(MessageType.UI_PROMPT)
            rid = hist[-1].payload["request_id"]
            await uim.respond_confirmation(rid, False)

        asyncio.create_task(_respond())
        result = await asyncio.wait_for(
            uim.request_confirmation("拒绝?", "conv2", timeout=3), timeout=5
        )
        assert result is False, result
    finally:
        await bus.stop()


async def test_confirmation_timeout_returns_false() -> None:
    """无响应时超时回落 False，不挂死（此前 ASK 权限会挂到 60s）。"""
    bus = MessageBus()
    uim = UserInputManager(message_bus=bus)
    await bus.start()
    try:
        result = await asyncio.wait_for(
            uim.request_confirmation("无人应答?", "conv3", timeout=0.3), timeout=3
        )
        assert result is False, result
    finally:
        await bus.stop()


async def main() -> int:
    tests = [
        ("typed_subscribe_dispatches_only_matching", test_typed_subscribe_dispatches_only_matching),
        ("subscribe_all_receives_every_message", test_subscribe_all_receives_every_message),
        ("get_history_filters_by_type", test_get_history_filters_by_type),
        ("adapter_event_to_messagetype_mapping", test_adapter_event_to_messagetype_mapping),
        ("confirmation_allow_unlocks_future", test_confirmation_allow_unlocks_future),
        ("confirmation_deny_unlocks_future", test_confirmation_deny_unlocks_future),
        ("confirmation_timeout_returns_false", test_confirmation_timeout_returns_false),
    ]
    failures = 0
    for name, fn in tests:
        try:
            await fn()
            print(f"[PASS] {name}")
        except AssertionError as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failures += 1
            print(f"[ERROR] {name}: {exc}")
            traceback.print_exc()
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL MessageBus/Adapter/UserInputManager tests passed")
    return 0


if __name__ == "__main__":
    async def _pytest_mapping():
        await test_adapter_event_to_messagetype_mapping()

    async def _pytest_timeout():
        await test_confirmation_timeout_returns_false()

    def test_adapter_event_to_messagetype_mapping_pytest():
        asyncio.run(_pytest_mapping())

    def test_confirmation_timeout_returns_false_pytest():
        asyncio.run(_pytest_timeout())

    sys.exit(asyncio.run(main()))
