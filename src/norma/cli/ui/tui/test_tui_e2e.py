"""NormaApp 端到端回归测试（headless，mock OpenAI HTTP 客户端）。

验证真实链路：输入提交 -> AgentRunner 驱动 agent.run() -> stream_chat/chat
-> finish_reason 分支 -> 工具执行 -> MessageBus 发布 -> TUI 渲染 -> 回合结束。
唯一 mock 的是 OpenAI HTTP client（``OpenAILLM.client``）。

运行：``python -m norma.cli.ui.tui.test_tui_e2e``
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Optional


def _chunk(content=None, tc=None, finish=None, usage=None):
    delta = types.SimpleNamespace(
        content=content, reasoning_content=None, tool_calls=tc
    )
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(usage=usage, choices=[choice])


def _tc_delta(idx, cid=None, name=None, args=None):
    return types.SimpleNamespace(
        index=idx, id=cid, function=types.SimpleNamespace(name=name, arguments=args)
    )


class _FakeCompletions:
    """两轮：第一轮返回 Ls 工具调用，第二轮返回最终文本。"""

    def __init__(self):
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1
        stream = kw.get("stream", False)
        ls_args = json.dumps({"path": "."})
        if stream:
            async def gen():
                if self.calls == 1:
                    yield _chunk(tc=[_tc_delta(0, "c1", "Ls", ls_args)], finish="tool_calls")
                else:
                    yield _chunk(content="已列出目录", finish="stop",
                                 usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))
            return gen()
        # 非流式
        if self.calls == 1:
            msg = types.SimpleNamespace(
                content=None,
                tool_calls=[types.SimpleNamespace(
                    id="c1", function=types.SimpleNamespace(name="Ls", arguments=ls_args)
                )],
            )
            return types.SimpleNamespace(usage=None, choices=[
                types.SimpleNamespace(message=msg, finish_reason="tool_calls")])
        msg = types.SimpleNamespace(content="已列出目录", tool_calls=None)
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])


class _FakeClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions())


async def _run_once(stream_mode: bool) -> bool:
    from norma.cli.cli import NormaCLI
    from norma.cli.ui.tui.app import NormaApp

    cli = NormaCLI()
    cli.llm.default_stream_mode = stream_mode
    cli.llm.client = _FakeClient()
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
            app.query_one("#input").value = "列出当前目录"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(delay=0.15)
                if not app._is_running():
                    break
            calls = cli.llm.client.chat.completions.calls
            ok = calls >= 2 and not app._is_running()
            print(f"[stream={stream_mode}] llm_calls={calls} "
                  f"running={app._is_running()} -> {'OK' if ok else 'CHECK'}")
            return ok
    finally:
        await cli.message_bus.stop()


async def _amain() -> int:
    r1 = await _run_once(True)
    r2 = await _run_once(False)
    print(f"REAL E2E (mocked LLM): streaming={r1} non-streaming={r2}")
    return 0 if (r1 and r2) else 1


def test_tui_e2e_mocked_llm() -> None:
    """pytest 入口（若安装 pytest）。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    import sys
    # 与 cli.main() 一致：Windows GBK 控制台无法编码 ✓ 等字符，需切到 UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
