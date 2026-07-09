"""NormaREPL 权限确认回归测试（headless）。

验证 REPL 订阅 ``UI_PROMPT`` 后，agent 的 ``request_confirmation`` 不再
挂起超时--回调经可注入的 ``prompt_confirm`` 拿到用户应答并回送
``respond_confirmation``，future 正常解析为 True/False。

此前 REPL 不订阅 UI_PROMPT，权限请求会等到 60s 超时后默认拒绝。

运行：``python -m norma.cli.repl.test_repl_permission``
"""

from __future__ import annotations

import asyncio
import types


async def _make_repl(allow: bool):
    from norma.cli.repl.repl import NormaREPL
    from norma.messagebus.messagebus import MessageBus, UserInputManager

    bus = MessageBus()
    uim = UserInputManager(bus)
    stub_agent = types.SimpleNamespace(
        message_bus=bus,
        user_input_manager=uim,
        permission_checker=None,
        llm=types.SimpleNamespace(model="stub"),
        session_manager=None,
    )

    seen: list[str] = []

    async def fake_confirm(prompt_text: str) -> bool:
        seen.append(prompt_text)
        return allow

    # 用 __new__ 绕过 PromptSession 构造（Windows 无控制台时 PromptSession 会
    # 急切创建 Win32Output 并抛 NoConsoleScreenBufferError），仅装配权限订阅所需属性
    repl = NormaREPL.__new__(NormaREPL)
    repl.agent = stub_agent
    repl.prompt_confirm = fake_confirm
    repl._setup_permission_subscription()
    return repl, bus, uim, seen


async def test_repl_permission_allow() -> bool:
    repl, bus, uim, seen = await _make_repl(allow=True)
    await bus.start()
    try:
        result = await asyncio.wait_for(
            uim.request_confirmation("允许执行 Edit?", "conv1", timeout=3),
            timeout=5,
        )
        return result is True and seen == ["允许执行 Edit?"]
    finally:
        await bus.stop()


async def test_repl_permission_deny() -> bool:
    repl, bus, uim, seen = await _make_repl(allow=False)
    await bus.start()
    try:
        result = await asyncio.wait_for(
            uim.request_confirmation("允许执行 Bash?", "conv2", timeout=3),
            timeout=5,
        )
        return result is False
    finally:
        await bus.stop()


async def _amain() -> int:
    tests = [
        ("repl_permission_allow", test_repl_permission_allow),
        ("repl_permission_deny", test_repl_permission_deny),
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


def test_repl_permission_headless() -> None:
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
