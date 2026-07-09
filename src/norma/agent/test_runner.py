"""AgentRunner 单元回归测试。

``AgentRunner`` 是前后端解耦的关键桥接：后台驱动 ``agent.run()`` 生成器，
捕获最终 ``AgentResponse`` 供前端经 done_callback 取用，并保证逃逸异常**不被
静默吞掉**（前端异常可见性契约）。此前仅在 ``test_tui_e2e`` 中被间接覆盖，
本文件直接锁定其契约：

1. 正常驱动：迭代完整事件序列，``wait()`` 返回最终 ``AgentResponse``。
2. ``running`` 属性：未启动 / 运行中 / 完成后 三态正确。
3. ``cancel()``：运行中取消 -> ``wait()`` 抛 ``CancelledError``；未运行时取消为 no-op。
4. 逃逸异常上抛：agent 内部抛出的异常经 ``wait()`` 透传（不被吞），前端据此提示。
5. 重复启动守卫：运行中再次 ``start()`` 抛 ``RuntimeError``。

运行：``python -m norma.agent.test_runner``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator, List, Union

# 以脚本方式运行时确保 src 在 path 上
_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.agent_types import (  # noqa: E402
    AgentEvent,
    AgentInputEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from norma.core.llm_types import UserMessage  # noqa: E402
from norma.agent.runner import AgentRunner  # noqa: E402


class _ScriptedAgent:
    """鸭子类型的最小 agent：按脚本 yield 事件序列，可选在末尾抛异常。"""

    def __init__(
        self,
        events: List[Union[AgentEvent, AgentResponse]],
        raise_exc: Union[BaseException, None] = None,
        yield_delay: float = 0.0,
    ) -> None:
        self._events = list(events)
        self._raise = raise_exc
        self._yield_delay = yield_delay
        self.run_calls: List[str] = []

    async def run(self, query: str) -> AsyncGenerator[Union[AgentEvent, AgentResponse], None]:
        self.run_calls.append(query)
        for item in self._events:
            if self._yield_delay:
                await asyncio.sleep(self._yield_delay)
            yield item
        if self._raise is not None:
            raise self._raise


def _final_response(text: str = "done") -> AgentResponse:
    return AgentResponse(
        agent_name="StubAgent",
        input_message=[UserMessage(content="hi")],
        tools=[],
        prompt_usage=None,
        event_list=[],
        message_list=[],
        response=text,
    )


async def test_drives_and_captures_final() -> None:
    seq = [
        AgentInputEvent(agent_name="StubAgent", task="hi"),
        AgentTextDeltaEvent(agent_name="StubAgent", delta="hel"),
        AgentTextDeltaEvent(agent_name="StubAgent", delta="lo"),
        _final_response("hello"),
    ]
    agent = _ScriptedAgent(seq)
    runner = AgentRunner(agent)
    task = runner.start("hi")
    final = await runner.wait()
    assert isinstance(final, AgentResponse)
    assert final.response == "hello"
    # 生成器被完整驱动（所有事件已迭代）
    assert agent.run_calls == ["hi"]
    await task  # 任务已完成，无异常


async def test_running_property_states() -> None:
    agent = _ScriptedAgent([_final_response()])
    runner = AgentRunner(agent)
    assert runner.running is False  # 未启动
    task = runner.start("hi")
    # 任务可能瞬间完成；running 反映 task 完成态。等待后必为 False。
    await runner.wait()
    assert runner.running is False
    await task


async def test_cancel_propagates_cancelled_error() -> None:
    # 用延迟 yield 确保取消时生成器仍在运行中
    agent = _ScriptedAgent(
        [_final_response()],
        yield_delay=0.2,
    )
    runner = AgentRunner(agent)
    runner.start("hi")
    await asyncio.sleep(0.02)  # 让任务进入运行
    assert runner.running is True
    runner.cancel()
    try:
        await runner.wait()
    except asyncio.CancelledError:
        pass
    else:  # noqa: RET506
        raise AssertionError("cancel 后 wait 应抛 CancelledError")
    assert runner.running is False


async def test_cancel_when_not_running_is_noop() -> None:
    agent = _ScriptedAgent([_final_response()])
    runner = AgentRunner(agent)
    # 未启动时 cancel 不应抛错
    runner.cancel()
    assert runner.running is False


async def test_escaped_exception_propagates() -> None:
    """agent 内部逃逸的异常必须经 wait() 透传，不得被静默吞掉。"""
    agent = _ScriptedAgent([], raise_exc=ValueError("boom-from-agent"))
    runner = AgentRunner(agent)
    runner.start("hi")
    try:
        await runner.wait()
    except ValueError as exc:
        assert "boom-from-agent" in str(exc)
    else:  # noqa: RET506
        raise AssertionError("逃逸异常应经 wait() 上抛，而非被吞掉")
    assert runner.running is False


async def test_double_start_raises() -> None:
    agent = _ScriptedAgent([_final_response()], yield_delay=0.2)
    runner = AgentRunner(agent)
    runner.start("hi")
    await asyncio.sleep(0.02)
    try:
        runner.start("again")
    except RuntimeError:
        pass
    else:  # noqa: RET506
        raise AssertionError("运行中再次 start 应抛 RuntimeError")
    # 清理：取消挂起的任务
    runner.cancel()
    try:
        await runner.wait()
    except asyncio.CancelledError:
        pass


async def main() -> int:
    tests = [
        ("drives_and_captures_final", test_drives_and_captures_final),
        ("running_property_states", test_running_property_states),
        ("cancel_propagates_cancelled_error", test_cancel_propagates_cancelled_error),
        ("cancel_when_not_running_is_noop", test_cancel_when_not_running_is_noop),
        ("escaped_exception_propagates", test_escaped_exception_propagates),
        ("double_start_raises", test_double_start_raises),
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
    print("\nALL AgentRunner tests passed")
    return 0


if __name__ == "__main__":
    # pytest 兼容桩（可选）
    async def _pytest_drives():
        await test_drives_and_captures_final()

    async def _pytest_cancel():
        await test_cancel_propagates_cancelled_error()

    def test_drives_and_captures_final_pytest():
        asyncio.run(_pytest_drives())

    def test_cancel_propagates_cancelled_error_pytest():
        asyncio.run(_pytest_cancel())

    sys.exit(asyncio.run(main()))
