"""NormaApp 前端渲染与交互回归测试（headless）。

补充 ``test_tui_e2e.py``：后者只验证 LLM 调用计数与运行状态，本文件聚焦
**前端**本身（用户的首要优先级「打通前端实现」）：

1. 渲染正确性——思考块、多工具调用、工具成功/错误标记都正确落到历史区。
2. 交互——流式中断（Ctrl+C）能干净收尾并恢复输入框。
3. 权限弹窗往返——``UI_PROMPT`` -> 弹窗 -> 按 ``y`` -> ``USER_CONFIRM`` ->
   ``request_confirmation`` 的 future 解析为 ``True``。

渲染测试通过向真实 ``MessageBus`` 发布合成事件、再让 Textual pilot 抽干
消息泵来驱动，完整复现「总线回调 -> post_message -> on_bus_event_message ->
_write_history」链路。唯一记录点是包装 ``_write_history`` 收集每条渲染产物
的纯文本。

运行：``python -m norma.cli.ui.tui.test_tui_render``
"""

from __future__ import annotations

import asyncio
import types
from typing import Optional


# =====================================================================
# 流式 chunk 构造（与真实 OpenAI ChatCompletionChunk 同构）
# =====================================================================

def _chunk(content=None, tc=None, finish=None, reasoning=None, usage=None):
    delta = types.SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=tc
    )
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(usage=usage, choices=[choice])


class _SlowCompletions:
    """永不结束的流式响应：先吐一个增量，随后无限 sleep。

    用于中断测试——agent 会一直停在 ``async for chunk in self.llm(...)``，
    直到被 Ctrl+C 取消。
    """

    async def create(self, **kw):
        async def gen():
            yield _chunk(content="正在", finish=None)
            while True:
                await asyncio.sleep(0.3)
                yield _chunk(content="…", finish=None)

        return gen()


class _SlowClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(completions=_SlowCompletions())


# =====================================================================
# 辅助
# =====================================================================

def _install_recorder(app) -> list[str]:
    """包装 app._write_history，收集每条渲染产物的纯文本。"""
    recorded: list[str] = []
    orig = app._write_history

    def _rec(renderable) -> None:
        # Text 直接取 .plain；Group/Markdown 等经 Console 捕获渲染文本
        plain = getattr(renderable, "plain", None)
        if plain is not None:
            recorded.append(plain)
        else:
            recorded.append(_capture_text(renderable))
        orig(renderable)

    app._write_history = _rec
    return recorded


def _capture_text(renderable) -> str:
    """把任意 rich renderable 渲染成纯文本（用于断言 Group/Markdown 内容）。"""
    from rich.console import Console

    c = Console(record=True, width=100)
    c.print(renderable)
    return c.export_text()


async def _build_app():
    """最小化 app（桩 agent），用于纯渲染/弹窗测试，避免 NormaCLI 重量级装配。"""
    from norma.cli.ui.tui.app import NormaApp
    from norma.messagebus.messagebus import MessageBus, UserInputManager

    bus = MessageBus()
    await bus.start()
    uim = UserInputManager(bus)
    stub_agent = types.SimpleNamespace(
        permission_checker=None,
        llm=types.SimpleNamespace(model="stub"),
        session_manager=None,
    )
    app = NormaApp(
        agent=stub_agent, cwd=".", message_bus=bus, user_input_manager=uim
    )
    return app, bus, uim


async def _drain(pilot, bus) -> None:
    """发布后抽干总线处理器 + Textual 消息泵。"""
    await pilot.pause(delay=0.2)
    await pilot.pause(delay=0.05)


# =====================================================================
# 测试
# =====================================================================

async def test_think_block_render() -> None:
    """流式推理增量 + LLM 响应收尾 -> 历史区出现思考块。"""
    from norma.core.agent_types import (
        AgentThinkDeltaEvent,
        AgentLLMResponseEvent,
        AgentResponse,
    )
    from norma.core.llm_types import LLMResponse, AssistantMessage
    from norma.messagebus.messagebus import Message, MessageType

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_THINK_DELTA,
                payload=AgentThinkDeltaEvent(agent_name="t", delta="先思考一下"),
            ))
            await _drain(pilot, bus)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_LLM_RESPONSE,
                payload=AgentLLMResponseEvent(
                    agent_name="t",
                    response=LLMResponse(
                        response_message=AssistantMessage(content="", tool_calls=None),
                        finish_reason="stop",
                    ),
                ),
            ))
            await _drain(pilot, bus)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_RESPONSE,
                payload=AgentResponse(
                    agent_name="t", input_message=[], tools=None,
                    prompt_usage=None, event_list=[], message_list=[],
                ),
            ))
            await _drain(pilot, bus)

            joined = "\n".join(recorded)
            assert "思考" in joined, f"思考块缺失: {joined!r}"
            assert "先思考一下" in joined, f"推理内容缺失: {joined!r}"
            assert "─" in joined, f"回合分隔符缺失: {joined!r}"
    finally:
        await bus.stop()


async def test_multi_tool_call_render() -> None:
    """一次工具请求含多个 tool_calls -> 每个 都独立渲染一行。"""
    from norma.core.agent_types import AgentToolRequestEvent
    from norma.core.tool_types import ToolRequest
    from norma.messagebus.messagebus import Message, MessageType

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            tcs = [
                ToolRequest(tool_call_id="c1", tool_call_name="Ls",
                            tool_call_arguments={"path": "."}),
                ToolRequest(tool_call_id="c2", tool_call_name="Read",
                            tool_call_arguments={"path": "a.py"}),
            ]
            await bus.publish(Message(
                msg_type=MessageType.AGENT_TOOL_REQUEST,
                payload=AgentToolRequestEvent(
                    agent_name="t", tool_calls=tcs, tool_execution_results=[]
                ),
            ))
            await _drain(pilot, bus)

            tool_lines = [r for r in recorded if "🛠" in r]
            assert len(tool_lines) == 2, f"应渲染 2 行工具调用，实际 {len(tool_lines)}: {recorded!r}"
            joined = "\n".join(recorded)
            assert "Ls" in joined and "Read" in joined
    finally:
        await bus.stop()


async def test_tool_error_and_success_render() -> None:
    """工具结果中 is_error=True 渲染 ✗，is_error=False 渲染 ⚙。"""
    from norma.core.agent_types import AgentToolRequestAnswerEvent
    from norma.core.tool_types import ToolRequest, ToolRequestResult
    from norma.messagebus.messagebus import Message, MessageType

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            err = ToolRequestResult(
                request=ToolRequest(tool_call_id="c1", tool_call_name="Ls",
                                    tool_call_arguments={"path": "."}),
                result=None, content="boom: 不存在", is_error=True,
            )
            ok = ToolRequestResult(
                request=ToolRequest(tool_call_id="c2", tool_call_name="Read",
                                    tool_call_arguments={"path": "a.py"}),
                result={"n": 1}, content="ok", is_error=False,
            )
            await bus.publish(Message(
                msg_type=MessageType.AGENT_TOOL_RESULT,
                payload=AgentToolRequestAnswerEvent(
                    agent_name="t", tool_execution_results=[err, ok]
                ),
            ))
            await _drain(pilot, bus)

            joined = "\n".join(recorded)
            assert "✗" in joined, f"错误标记 ✗ 缺失: {joined!r}"
            assert "⚙" in joined, f"成功标记 ⚙ 缺失: {joined!r}"
            assert "boom: 不存在" in joined, f"错误内容缺失: {joined!r}"
    finally:
        await bus.stop()


async def test_interrupt_mid_stream() -> None:
    """流式输出过程中 Ctrl+C -> agent 任务取消、输入框恢复可用。"""
    from norma.cli.cli import NormaCLI
    from norma.cli.ui.tui.app import NormaApp

    cli = NormaCLI()
    cli.llm.default_stream_mode = True
    cli.llm.client = _SlowClient()
    await cli.message_bus.start()
    try:
        app = NormaApp(
            agent=cli.agent, cwd=".", message_bus=cli.message_bus,
            user_input_manager=cli.user_input_manager,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#input").value = "慢慢回答"
            await pilot.press("enter")

            # 等待 agent 进入流式（running 变 True）
            running_seen = False
            for _ in range(30):
                await pilot.pause(delay=0.1)
                if app._is_running():
                    running_seen = True
                    break
            assert running_seen, "agent 未进入运行态，无法测试中断"

            # 中断
            await pilot.press("ctrl+c")
            for _ in range(40):
                await pilot.pause(delay=0.1)
                if not app._is_running():
                    break

            assert not app._is_running(), "中断后 agent 仍在运行"
            inp = app.query_one("#input")
            assert not inp.disabled, "中断后输入框未恢复"
    finally:
        await cli.message_bus.stop()


async def test_command_paths() -> None:
    """/help 渲染帮助、/foobar 给出未知命令提示、/clear 不误报未知命令。"""
    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            # /help -> 帮助文本，且不被误报为「未知命令」
            app.query_one("#input").value = "/help"
            await pilot.press("enter")
            await _drain(pilot, bus)
            joined = "\n".join(recorded)
            assert "可用命令" in joined, f"帮助文本缺失: {joined!r}"
            assert "未知命令" not in joined, "/help 被误报为未知命令"

            # /foobar -> 未知命令提示
            recorded.clear()
            app.query_one("#input").value = "/foobar"
            await pilot.press("enter")
            await _drain(pilot, bus)
            joined = "\n".join(recorded)
            assert "未知命令" in joined and "foobar" in joined, f"未知命令提示缺失: {joined!r}"

            # /clear -> 清屏，不误报未知命令（cmd_clear 仅调用 clear_screen，不打印）
            recorded.clear()
            app.query_one("#input").value = "/clear"
            await pilot.press("enter")
            await _drain(pilot, bus)
            joined = "\n".join(recorded)
            assert "未知命令" not in joined, "/clear 被误报为未知命令"
    finally:
        await bus.stop()


async def test_f2_cycles_permission_mode() -> None:
    """F2 在 plan -> edit -> auto 间循环切换权限模式并回到起点。"""
    from norma.cli.cli import NormaCLI
    from norma.cli.ui.tui.app import NormaApp
    from norma.permission import PermissionMode

    cli = NormaCLI()
    await cli.message_bus.start()
    try:
        app = NormaApp(
            agent=cli.agent, cwd=".", message_bus=cli.message_bus,
            user_input_manager=cli.user_input_manager,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            checker = cli.agent.permission_checker
            assert checker is not None and checker.config is not None
            start = checker.config.mode
            assert start in (PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.AUTO)

            modes = []
            for _ in range(3):
                await pilot.press("f2")
                await pilot.pause(delay=0.05)
                modes.append(checker.config.mode)

            assert len(set(modes)) == 3, f"三次按键应遍历三种模式，实际 {modes}"
            assert modes[-1] == start, f"第三次应回到起点 {start}，实际 {modes[-1]}"
    finally:
        await cli.message_bus.stop()


async def test_permission_modal_roundtrip() -> None:
    """UI_PROMPT -> 权限弹窗 -> 按 y -> request_confirmation 返回 True。"""
    from norma.messagebus.messagebus import Message, MessageType  # noqa: F401
    from norma.cli.ui.tui.app import PermissionModal

    app, bus, uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()

            # 后台发起确认请求 -> 总线发布 UI_PROMPT -> 弹窗
            fut = asyncio.ensure_future(
                uim.request_confirmation("允许执行 X?", "conv1", timeout=5)
            )

            modal_up = False
            for _ in range(30):
                await pilot.pause(delay=0.1)
                if isinstance(app.screen, PermissionModal):
                    modal_up = True
                    break
            assert modal_up, f"权限弹窗未弹出，当前 screen={app.screen!r}"

            await pilot.press("y")
            result = await asyncio.wait_for(fut, timeout=3)
            assert result is True, f"确认应返回 True，实际 {result!r}"
    finally:
        await bus.stop()


# =====================================================================
# 入口
# =====================================================================

async def test_multiline_paste_preserved() -> None:
    """多行粘贴：完整文本保留在 value（父类 Input._on_paste 仅取首行）。

    回归点：粘贴含换行的代码块/报错栈时，``_MultiLineInput`` 保留全部行，
    Enter 提交时整段送达 agent，而非只剩首行。
    """
    from textual import events

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input")
            inp.post_message(events.Paste(text="line1\nline2\nline3"))
            await pilot.pause(delay=0.1)
            assert "\n" in inp.value, f"多行粘贴被截断为首行: {inp.value!r}"
            assert "line2" in inp.value and "line3" in inp.value, (
                f"后续行丢失: {inp.value!r}"
            )
    finally:
        await bus.stop()


async def test_assistant_markdown_render() -> None:
    """助手回复落盘历史区时渲染为 Markdown：代码块内容仍可达。

    回归点：流式增量以纯文本入流式区，``_commit_stream`` 落盘时统一渲染为
    ``Markdown``（代码块语法高亮 / 加粗 / 列表），含代码的回复可读而非裸 ```` ``` ````。
    """
    from norma.core.agent_types import AgentTextDeltaEvent, AgentLLMResponseEvent
    from norma.core.llm_types import LLMResponse, AssistantMessage
    from norma.messagebus.messagebus import Message, MessageType

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            code = "```python\nprint('hi')\n```"
            await bus.publish(Message(
                msg_type=MessageType.AGENT_TEXT_DELTA,
                payload=AgentTextDeltaEvent(agent_name="t", delta=code),
            ))
            await _drain(pilot, bus)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_LLM_RESPONSE,
                payload=AgentLLMResponseEvent(
                    agent_name="t",
                    response=LLMResponse(
                        response_message=AssistantMessage(content="", tool_calls=None),
                        finish_reason="stop",
                    ),
                ),
            ))
            await _drain(pilot, bus)

            joined = "\n".join(recorded)
            assert "print('hi')" in joined, f"代码块文本落盘后丢失: {joined!r}"
    finally:
        await bus.stop()


async def test_error_response_rendered() -> None:
    """异常收尾（如 LLM 不可达）：无任何前置输出时，错误文本必须显式渲染。

    回归点：``AgentResponse.error`` 非空时，TUI 不再静默只画分隔符，
    而是把错误以 ✗ 红字呈现给用户。
    """
    from norma.core.agent_types import AgentResponse
    from norma.messagebus.messagebus import Message, MessageType

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_RESPONSE,
                payload=AgentResponse(
                    agent_name="t", input_message=[], tools=None,
                    prompt_usage=None, event_list=[], message_list=[],
                    response="发生了错误: connection refused",
                    error="connection refused",
                ),
            ))
            await _drain(pilot, bus)

            joined = "\n".join(recorded)
            assert "✗" in joined, f"错误标记 ✗ 缺失: {joined!r}"
            assert "connection refused" in joined, f"错误文本被静默吞掉: {joined!r}"
            assert "任务异常" in joined, f"错误标题缺失: {joined!r}"
    finally:
        await bus.stop()


async def test_error_shown_after_partial_stream() -> None:
    """流式已吐部分文本后异常：部分文本与错误提示都应可见。

    回归点：``error`` 在已有流式输出时仍要显示（不能因「本回合已展示过文本」
    而丢弃错误）。
    """
    from norma.core.agent_types import AgentTextDeltaEvent, AgentResponse
    from norma.messagebus.messagebus import Message, MessageType

    app, bus, _uim = await _build_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_TEXT_DELTA,
                payload=AgentTextDeltaEvent(agent_name="t", delta="部分回答"),
            ))
            await _drain(pilot, bus)

            await bus.publish(Message(
                msg_type=MessageType.AGENT_RESPONSE,
                payload=AgentResponse(
                    agent_name="t", input_message=[], tools=None,
                    prompt_usage=None, event_list=[], message_list=[],
                    response="发生了错误: downstream timeout",
                    error="downstream timeout",
                ),
            ))
            await _drain(pilot, bus)

            joined = "\n".join(recorded)
            assert "部分回答" in joined, f"流式部分文本丢失: {joined!r}"
            assert "✗" in joined and "downstream timeout" in joined, (
                f"流式后异常未显式提示: {joined!r}"
            )
    finally:
        await bus.stop()


async def test_unexpected_error_surfaced() -> None:
    """agent.run() 抛出逃逸异常 -> TUI 经 done_callback 显式提示，不静默结束。

    回归点：``AgentRunner`` 不再吞掉逃逸异常返回 None，而是上抛，使
    ``_on_agent_done`` 走 ``TurnFinishedMessage(ok=False)`` 路径写出错误。
    """
    from norma.cli.ui.tui.app import NormaApp
    from norma.messagebus.messagebus import MessageBus, UserInputManager

    bus = MessageBus()
    await bus.start()
    uim = UserInputManager(bus)

    class _RaisingAgent:
        permission_checker = None
        llm = types.SimpleNamespace(model="stub")
        session_manager = None

        async def run(self, query):  # noqa: ANN001 - 异步生成器桩
            raise RuntimeError("内部意外错误: bug")
            yield  # noqa: unreachable - 标记为 async generator

    app = NormaApp(
        agent=_RaisingAgent(), cwd=".", message_bus=bus, user_input_manager=uim
    )
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            recorded = _install_recorder(app)

            app.query_one("#input").value = "go"
            await pilot.press("enter")

            for _ in range(40):
                await pilot.pause(delay=0.1)
                if not app._is_running():
                    break

            assert not app._is_running(), "逃逸异常后回合未结束"
            joined = "\n".join(recorded)
            assert ("异常" in joined) or ("意外错误" in joined), (
                f"逃逸异常被静默吞掉: {joined!r}"
            )
    finally:
        await bus.stop()


async def _amain() -> int:
    tests = [
        ("think_block_render", test_think_block_render),
        ("multi_tool_call_render", test_multi_tool_call_render),
        ("tool_error_and_success_render", test_tool_error_and_success_render),
        ("interrupt_mid_stream", test_interrupt_mid_stream),
        ("command_paths", test_command_paths),
        ("f2_cycles_permission_mode", test_f2_cycles_permission_mode),
        ("permission_modal_roundtrip", test_permission_modal_roundtrip),
        ("multiline_paste_preserved", test_multiline_paste_preserved),
        ("assistant_markdown_render", test_assistant_markdown_render),
        ("error_response_rendered", test_error_response_rendered),
        ("error_shown_after_partial_stream", test_error_shown_after_partial_stream),
        ("unexpected_error_surfaced", test_unexpected_error_surfaced),
    ]
    failures = 0
    for name, fn in tests:
        try:
            await fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_tui_render_headless() -> None:
    """pytest 入口（若安装 pytest）。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    import sys
    # 与 cli.main() 一致：Windows GBK 控制台无法编码 ✓/🛠/✗ 等字符
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
