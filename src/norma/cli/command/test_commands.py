"""内置命令回归测试（headless）。

用真实 NormaCoder + stub LLM + 真实 SessionManager，经 CommandRegistry 逐条
执行 9 个内置命令，确保命令处理器不崩溃且产出输出；并锁定 /compact 的诚实
上报（LLM 可达 -> 成功；不可达 -> 失败，不再误报「压缩完成」）。

运行：``python -m norma.cli.command.test_commands``
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[4]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.agent.norma_coder import NormaCoder  # noqa: E402
from norma.session.session import SessionManager  # noqa: E402
from norma.core.llm_types import LLMRequest, LLMResponse, AssistantMessage  # noqa: E402
from norma.cli.command.registry import CommandRegistry, CommandContext  # noqa: E402
from norma.cli.command.builtin import register_builtin_commands  # noqa: E402


class _FakeLLM:
    """可达 LLM stub：chat() 返回固定摘要，供 /compact 成功路径。"""

    default_stream_mode = False
    max_context_tokens = 1000
    model = "stub-model"

    def estimate_tokens(self, messages) -> int:
        return sum(len(getattr(m, "content", "") or "") for m in messages)

    async def chat(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            response_message=AssistantMessage(content="压缩摘要。", tool_calls=None),
            finish_reason="stop",
        )


class _RaisingLLM:
    """不可达 LLM stub：chat() 抛异常，供 /compact 失败路径。"""

    default_stream_mode = False
    max_context_tokens = 1000
    model = "broken-model"

    def estimate_tokens(self, messages) -> int:
        return sum(len(getattr(m, "content", "") or "") for m in messages)

    async def chat(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError("LLM unreachable")


def _make_agent(tmp_cwd: str, config_home: str, llm) -> NormaCoder:
    os.environ["NORMA_CONFIG_HOME"] = config_home
    sm = SessionManager(cwd=tmp_cwd)
    sm.create(title="cmd-test")
    agent = NormaCoder(
        llm=llm,
        cwd=tmp_cwd,
        name="CmdTestCoder",
        enable_subagent=False,
        enable_skill=False,
        session_manager=sm,
    )
    return agent


def _make_repl(agent: NormaCoder, cwd: str):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    outputs: list = []

    def _print(text: str) -> None:
        outputs.append(text)

    repl = SimpleNamespace(
        agent=agent,
        console=None,
        command_registry=reg,
        cwd=Path(cwd),
        print_output=_print,
        clear_screen=lambda: outputs.append("[CLEARED]"),
    )
    return repl, reg, outputs


async def _run(reg, repl, arg: str):
    ctx = CommandContext(repl=repl, args=arg)
    return await reg.execute(ctx)


async def test_all_commands_execute(tmp_cwd: str, config_home: str) -> None:
    agent = _make_agent(tmp_cwd, config_home, _FakeLLM())
    repl, reg, outputs = _make_repl(agent, tmp_cwd)

    # /exit 返回 False（信号退出），其余返回 None
    for cmd in ["/help", "/status", "/session", "/model", "/resume", "/new", "/clear"]:
        outputs.clear()
        ret = await _run(reg, repl, cmd)
        assert ret is None, f"{cmd} 应返回 None，实际 {ret!r}"
        assert outputs, f"{cmd} 未产出任何输出"

    # /clear 调用 clear_screen
    outputs.clear()
    await _run(reg, repl, "/clear")
    assert any("[CLEARED]" in o for o in outputs), "/clear 未触发清屏"

    # /exit 返回 False
    ret = await _run(reg, repl, "/exit")
    assert ret is False, f"/exit 应返回 False，实际 {ret!r}"

    # 未知命令经 registry.execute 返回 None（由前端 lookup 判定，此处仅验 execute）
    ret = await _run(reg, repl, "/nonexistent_xyz")
    assert ret is None
    print("[PASS] test_all_commands_execute")


async def test_compact_honest_success(tmp_cwd: str, config_home: str) -> None:
    agent = _make_agent(tmp_cwd, config_home, _FakeLLM())
    repl, reg, outputs = _make_repl(agent, tmp_cwd)
    await _run(reg, repl, "/compact")
    joined = "\n".join(outputs)
    assert "压缩完成" in joined, f"可达 LLM 下 /compact 应报告成功: {joined!r}"
    assert "压缩失败" not in joined
    print("[PASS] test_compact_honest_success")


async def test_compact_honest_failure(tmp_cwd: str, config_home2: str) -> None:
    agent = _make_agent(tmp_cwd, config_home2, _RaisingLLM())
    repl, reg, outputs = _make_repl(agent, tmp_cwd)
    await _run(reg, repl, "/compact")
    joined = "\n".join(outputs)
    assert "压缩失败" in joined, f"不可达 LLM 下 /compact 应报告失败: {joined!r}"
    assert "压缩完成" not in joined
    print("[PASS] test_compact_honest_failure")


async def test_new_clears_read_registry(tmp_cwd: str, config_home: str) -> None:
    """/new 应清空「已读文件」注册表，使新对话的「先读后编」门禁重新生效。"""
    agent = _make_agent(tmp_cwd, config_home, _FakeLLM())
    repl, reg, outputs = _make_repl(agent, tmp_cwd)

    # 模拟旧对话读过两个文件
    registry = getattr(agent, "_read_files_registry", None)
    assert registry is not None, "agent 应暴露 _read_files_registry"
    registry.add("/old/conversation/file_a.py")
    registry.add("/old/conversation/file_b.py")
    assert len(registry) == 2

    await _run(reg, repl, "/new")

    assert len(registry) == 0, (
        f"/new 后已读注册表应清空，实际 {len(registry)}: {registry}")
    # 同时确认 memory 被重置（仅剩 system 消息）
    assert len(agent.memory._messages) == 1, "/new 应重置 memory 为单条 system"
    print("[PASS] test_new_clears_read_registry")


async def _amain() -> int:
    # 每个用例独立的临时目录与 config_home，互不污染 ~/.norma
    def _mk():
        tmp = tempfile.mkdtemp()
        cfg = tempfile.mkdtemp()
        return tmp, cfg

    tests = [
        ("all_commands_execute", test_all_commands_execute(*_mk())),
        ("compact_honest_success", test_compact_honest_success(*_mk())),
        ("compact_honest_failure", test_compact_honest_failure(*_mk())),
        ("new_clears_read_registry", test_new_clears_read_registry(*_mk())),
    ]
    failures = 0
    for name, coro in tests:
        try:
            await coro
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failures += 1
            print(f"ERROR {name}: {exc}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_commands_headless() -> None:
    """pytest 入口（若安装 pytest）。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
