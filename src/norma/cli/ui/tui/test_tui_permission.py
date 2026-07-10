"""TUI 权限确认弹窗端到端回归测试（headless，mock LLM + stub 工具执行）。

补齐前端缺口：``test_tui_e2e`` 用 Ls（AUTO 模式自动放行，永不弹窗），``test_repl_permission``
只覆盖 REPL 路径。本测试驱动**真实 TUI 弹窗往返**：

    输入提交 -> AgentRunner 后台任务 -> LLM 请求 Bash 工具
    -> EDIT 模式判 ASK -> request_confirmation -> 总线 UI_PROMPT
    -> BusEventMessage 跨线程投递 -> on_bus_event_message -> push_screen(PermissionModal)
    -> pilot 检测到弹窗 -> 按 y/n -> action_allow/deny -> dismiss
    -> _respond_confirmation(worker) -> respond_confirmation -> future 解锁
    -> _ask_user 返回 -> 工具放行/拒绝 -> 回合继续 -> 第 2 轮 LLM -> 最终文本 -> 回合结束

唯一 mock：OpenAI HTTP client（``OpenAILLM.client``）与 ``tool_manager.execute_tools``
（避免真实 Bash 子进程在 headless 下的不稳定）。

运行：``python -m norma.cli.ui.tui.test_tui_permission``
"""

from __future__ import annotations

import asyncio
import types
from typing import List


class _BashFakeCompletions:
    """两轮：第 1 轮返回 Bash 工具调用，第 2 轮返回最终文本。"""

    def __init__(self):
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1
        # 非流式（default_stream_mode=False）
        if self.calls == 1:
            msg = types.SimpleNamespace(
                content=None,
                tool_calls=[types.SimpleNamespace(
                    id="c1",
                    function=types.SimpleNamespace(
                        name="Bash",
                        arguments='{"command": "echo hi", "description": "test"}',
                    ),
                )],
            )
            return types.SimpleNamespace(usage=None, choices=[
                types.SimpleNamespace(message=msg, finish_reason="tool_calls")])
        msg = types.SimpleNamespace(content="done", tool_calls=None)
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])


class _FakeClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(completions=_BashFakeCompletions())


async def _drive(allow: bool) -> dict:
    """驱动一次完整弹窗往返，返回断言用的事实字典。"""
    from norma.cli.cli import NormaCLI
    from norma.cli.ui.tui.app import NormaApp, PermissionModal
    from norma.core.tool_types import ToolRequest, ToolRequestResult
    from norma.permission import PermissionMode

    cli = NormaCLI()
    # EDIT 模式：Bash -> ASK -> 弹窗
    cli.permission_checker.config.mode = PermissionMode.EDIT
    cli.llm.default_stream_mode = False
    cli.llm.client = _FakeClient()

    # stub 工具执行：避免真实 Bash 子进程；记录是否被调用以区分 allow/deny
    execute_calls: List[int] = []

    async def _stub_execute(requests):
        execute_calls.append(len(requests))
        return [
            ToolRequestResult(request=r, result="ok",
                              content="stubbed bash output", is_error=False)
            for r in requests
        ]

    cli.agent.tool_manager.execute_tools = _stub_execute  # type: ignore[assignment]

    await cli.message_bus.start()
    modal_prompt: List[str] = []
    try:
        app = NormaApp(
            agent=cli.agent,
            cwd=".",
            message_bus=cli.message_bus,
            user_input_manager=cli.user_input_manager,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#input").value = "run echo hi"
            await pilot.press("enter")

            # 轮询等待弹窗出现（agent 后台任务请求确认 -> 总线 -> UI 线程 push_screen）
            appeared = False
            for _ in range(80):
                await pilot.pause(delay=0.1)
                if isinstance(app.screen, PermissionModal):
                    appeared = True
                    modal_prompt.append(app.screen.prompt)
                    break
            assert appeared, "权限弹窗从未出现（ASK 链路未打通）"

            # 应答弹窗
            await pilot.press("y" if allow else "n")

            # 轮询等待回合结束
            for _ in range(80):
                await pilot.pause(delay=0.15)
                if not app._is_running():
                    break

        return {
            "llm_calls": cli.llm.client.chat.completions.calls,
            "running": app._is_running(),
            "execute_calls": len(execute_calls),
            "modal_prompt": modal_prompt[0] if modal_prompt else "",
        }
    finally:
        await cli.message_bus.stop()


async def test_modal_allow() -> bool:
    facts = await _drive(allow=True)
    assert facts["llm_calls"] >= 2, f"allow: 应至少 2 轮 LLM, got {facts['llm_calls']}"
    assert not facts["running"], "allow: 回合应已结束"
    assert facts["execute_calls"] == 1, (
        f"allow: 工具应被执行 1 次, got {facts['execute_calls']}")
    assert "Bash" in facts["modal_prompt"], (
        f"allow: 弹窗 prompt 应含工具名 Bash, got {facts['modal_prompt']!r}")
    print(f"[PASS] modal allow: llm={facts['llm_calls']} exec={facts['execute_calls']}")
    return True


async def test_modal_deny() -> bool:
    facts = await _drive(allow=False)
    assert facts["llm_calls"] >= 2, f"deny: 应至少 2 轮 LLM, got {facts['llm_calls']}"
    assert not facts["running"], "deny: 回合应已结束"
    assert facts["execute_calls"] == 0, (
        f"deny: 工具不应执行, got {facts['execute_calls']}")
    assert "Bash" in facts["modal_prompt"], (
        f"deny: 弹窗 prompt 应含工具名 Bash, got {facts['modal_prompt']!r}")
    print(f"[PASS] modal deny: llm={facts['llm_calls']} exec={facts['execute_calls']}")
    return True


async def _drive_interrupt() -> dict:
    """驱动到弹窗出现后按 Ctrl+C 中断（而非应答），返回弹窗是否被收起等事实。"""
    from norma.cli.cli import NormaCLI
    from norma.cli.ui.tui.app import NormaApp, PermissionModal
    from norma.core.tool_types import ToolRequest, ToolRequestResult
    from norma.permission import PermissionMode

    cli = NormaCLI()
    cli.permission_checker.config.mode = PermissionMode.EDIT
    cli.llm.default_stream_mode = False
    cli.llm.client = _FakeClient()

    # 中断发生在工具执行前（弹窗阶段），stub 仅作兜底
    async def _stub_execute(requests):
        return [
            ToolRequestResult(request=r, result="ok", content="stub", is_error=False)
            for r in requests
        ]

    cli.agent.tool_manager.execute_tools = _stub_execute  # type: ignore[assignment]

    await cli.message_bus.start()
    try:
        app = NormaApp(
            agent=cli.agent,
            cwd=".",
            message_bus=cli.message_bus,
            user_input_manager=cli.user_input_manager,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#input").value = "run echo hi"
            await pilot.press("enter")

            # 等待弹窗出现（agent 阻塞在 request_confirmation 的 future 上）
            appeared = False
            for _ in range(80):
                await pilot.pause(delay=0.1)
                if isinstance(app.screen, PermissionModal):
                    appeared = True
                    break
            assert appeared, "权限弹窗从未出现（无法进入中断场景）"

            # 用户在弹窗上按 Ctrl+C 中断（而非应答）
            app.action_interrupt_or_quit()

            # 轮询等待回合结束 + 弹窗被收起
            dismissed = False
            for _ in range(80):
                await pilot.pause(delay=0.1)
                if not app._is_running() and not isinstance(
                    app.screen, PermissionModal
                ):
                    dismissed = True
                    break

            return {
                "running": app._is_running(),
                "dismissed": dismissed,
                "screen_is_modal": isinstance(app.screen, PermissionModal),
            }
    finally:
        await cli.message_bus.stop()


async def test_modal_interrupt_dismisses() -> bool:
    facts = await _drive_interrupt()
    assert not facts["running"], "中断后回合应已结束"
    assert facts["dismissed"], (
        "中断后权限弹窗应被自动收起（不应残留孤儿弹窗遮蔽输入框）")
    assert not facts["screen_is_modal"], "当前屏幕不应仍是 PermissionModal"
    print("[PASS] modal interrupt dismisses orphan modal")
    return True


async def _amain() -> int:
    tests = [
        ("modal_allow", test_modal_allow),
        ("modal_deny", test_modal_deny),
        ("modal_interrupt_dismisses", test_modal_interrupt_dismisses),
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


def test_tui_permission_headless() -> None:
    """pytest 入口（若安装 pytest）。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
