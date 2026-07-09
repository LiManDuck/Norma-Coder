"""Hook 系统回归测试（headless）。

验证：
- 显式 dispatch 触发 shell 命令（session-begin）
- 环境变量注入（NORMA_HOOK_EVENT / TOOL_NAME / USER_INPUT）
- match 过滤（tool_name=Edit 命中、Read 不命中）
- MessageBus 订阅：发布 USER_INPUT 消息 -> hook 经总线触发

hook 命令用一个临时 python 辅助脚本把环境变量写入 marker 文件，便于断言。

运行：``python -m norma.hook.test_hook``
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.hook.hook import HookConfig, HookEvent, HookManager  # noqa: E402
from norma.messagebus.messagebus import Message, MessageBus, MessageType  # noqa: E402

# 辅助脚本：把 NORMA_HOOK_EVENT / TOOL_NAME / USER_INPUT 写入 marker 文件
_HELPER = r'''
import os, sys
marker = sys.argv[1]
data = "|".join([
    os.environ.get("NORMA_HOOK_EVENT", ""),
    os.environ.get("TOOL_NAME", ""),
    os.environ.get("USER_INPUT", ""),
])
with open(marker, "w", encoding="utf-8") as f:
    f.write(data)
'''

# exit 2 阻断 helper：向 stderr 写原因后以 exit code 2 退出
_BLOCK_HELPER = r'''
import sys
sys.stderr.write("forbidden: .env protected")
sys.exit(2)
'''

# exit 0 放行 helper
_ALLOW_HELPER = r'''
import sys
sys.exit(0)
'''

# 读取 stdin JSON 并把 tool_name / tool_input.file_path 写入 marker
_STDIN_HELPER = r'''
import sys, json
data = json.load(sys.stdin)
out = {
    "tool_name": data.get("tool_name"),
    "file": (data.get("tool_input") or {}).get("file_path"),
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f)
'''


def _cmd(helper: Path, marker: Path) -> str:
    return f'"{sys.executable}" "{helper}" "{marker}"'


async def test_dispatch_and_match(tmpdir: str) -> None:
    helper = Path(tmpdir) / "hook_writer.py"
    helper.write_text(_HELPER, encoding="utf-8")

    marker_session = Path(tmpdir) / "session.txt"
    marker_tool = Path(tmpdir) / "tool.txt"

    cfg = HookConfig.from_dict({
        "session-begin": [{
            "command": _cmd(helper, marker_session),
            "background": False,
        }],
        "tool-execute-before": [{
            "command": _cmd(helper, marker_tool),
            "background": False,
            "match": {"tool_name": "Edit"},
        }],
    })
    hm = HookManager(config=cfg)

    # 1. session-begin 显式触发
    await hm.dispatch(HookEvent.SESSION_BEGIN)
    assert marker_session.exists(), "session-begin hook 未写 marker"
    content = marker_session.read_text(encoding="utf-8")
    assert content.startswith("session-begin|"), f"EVENT 注入错误: {content!r}"

    # 2. tool-execute-before + match=Edit 命中
    await hm.dispatch(HookEvent.TOOL_EXECUTE_BEFORE, context={"tool_name": "Edit"})
    assert marker_tool.exists(), "Edit 应命中 match"
    tcontent = marker_tool.read_text(encoding="utf-8")
    assert tcontent.split("|")[0] == "tool-execute-before", f"EVENT 错误: {tcontent!r}"
    assert tcontent.split("|")[1] == "Edit", f"TOOL_NAME 注入错误: {tcontent!r}"

    # 3. tool-execute-before + match=Edit 对 Read 不命中
    marker_tool.unlink()
    await hm.dispatch(HookEvent.TOOL_EXECUTE_BEFORE, context={"tool_name": "Read"})
    assert not marker_tool.exists(), "Read 不应命中 match=Edit"
    print("[PASS] dispatch + env injection + match filter")


async def test_bus_subscription(tmpdir: str) -> None:
    helper = Path(tmpdir) / "hook_writer.py"
    helper.write_text(_HELPER, encoding="utf-8")
    marker = Path(tmpdir) / "user_input.txt"

    bus = MessageBus()
    await bus.start()
    try:
        cfg = HookConfig.from_dict({
            "user-input": [{
                "command": _cmd(helper, marker),
                "background": False,
            }],
        })
        hm = HookManager(config=cfg, message_bus=bus)
        hm.attach(bus)

        # 发布 USER_INPUT 消息，hook 经总线订阅触发
        await bus.publish(Message(
            msg_type=MessageType.USER_INPUT,
            payload={"text": "hello-world"},
            conversation_id="conv-1",
        ))
        # 总线异步处理，稍等
        for _ in range(20):
            if marker.exists():
                break
            await asyncio.sleep(0.1)
        assert marker.exists(), "USER_INPUT 经总线未触发 hook"
        content = marker.read_text(encoding="utf-8")
        parts = content.split("|")
        assert parts[0] == "user-input", f"EVENT 错误: {content!r}"
        assert parts[2] == "hello-world", f"USER_INPUT 注入错误: {content!r}"
    finally:
        await bus.stop()
    print("[PASS] MessageBus subscription triggers hook")


async def test_pre_tool_blocking_and_allow(tmpdir: str) -> None:
    block_helper = Path(tmpdir) / "block.py"
    block_helper.write_text(_BLOCK_HELPER, encoding="utf-8")
    allow_helper = Path(tmpdir) / "allow.py"
    allow_helper.write_text(_ALLOW_HELPER, encoding="utf-8")

    # exit 2 -> 阻断，reason = stderr
    cfg_block = HookConfig.from_dict({
        "tool-execute-before": [{
            "command": f'"{sys.executable}" "{block_helper}"',
            "background": False,
        }],
    })
    hm = HookManager(config=cfg_block)
    res = await hm.run_pre_tool_hooks("Edit", {"file_path": "a.env"}, "conv-1")
    assert res.blocked, "exit 2 应阻断"
    assert ".env protected" in res.reason, f"reason 应含 stderr: {res.reason!r}"

    # exit 0 -> 放行
    cfg_allow = HookConfig.from_dict({
        "tool-execute-before": [{
            "command": f'"{sys.executable}" "{allow_helper}"',
            "background": False,
        }],
    })
    hm2 = HookManager(config=cfg_allow)
    res2 = await hm2.run_pre_tool_hooks("Edit", {"file_path": "a.env"}, "conv-1")
    assert not res2.blocked, "exit 0 不应阻断"

    # 无 hook 配置 -> 放行
    hm3 = HookManager(config=HookConfig())
    res3 = await hm3.run_pre_tool_hooks("Edit", {}, "conv-1")
    assert not res3.blocked, "无 hook 不应阻断"
    print("[PASS] pre-tool blocking(exit2)/allow(exit0)/no-hook")


async def test_pre_tool_json_stdin(tmpdir: str) -> None:
    stdin_helper = Path(tmpdir) / "stdin_reader.py"
    stdin_helper.write_text(_STDIN_HELPER, encoding="utf-8")
    marker = Path(tmpdir) / "stdin.json"
    cfg = HookConfig.from_dict({
        "tool-execute-before": [{
            "command": f'"{sys.executable}" "{stdin_helper}" "{marker}"',
            "background": False,
        }],
    })
    hm = HookManager(config=cfg)
    res = await hm.run_pre_tool_hooks(
        "Edit",
        {"file_path": "/tmp/x.env", "old_string": "a", "new_string": "b"},
        "conv-9",
    )
    assert not res.blocked, "exit 0 不应阻断"
    assert marker.exists(), "hook 未写 marker（stdin JSON 未收到?）"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["tool_name"] == "Edit", f"stdin JSON tool_name 错误: {data}"
    assert data["file"] == "/tmp/x.env", f"stdin JSON tool_input.file_path 错误: {data}"
    print("[PASS] pre-tool JSON stdin payload")


async def test_pre_tool_match_filter(tmpdir: str) -> None:
    block_helper = Path(tmpdir) / "block.py"
    block_helper.write_text(_BLOCK_HELPER, encoding="utf-8")
    cfg = HookConfig.from_dict({
        "tool-execute-before": [{
            "command": f'"{sys.executable}" "{block_helper}"',
            "background": False,
            "match": {"tool_name": "Edit"},
        }],
    })
    hm = HookManager(config=cfg)
    # Edit 命中 match -> 阻断
    res_edit = await hm.run_pre_tool_hooks("Edit", {"file_path": "a.env"}, "conv-1")
    assert res_edit.blocked, "Edit 应被 match 命中并阻断"
    # Read 不命中 match -> 放行
    res_read = await hm.run_pre_tool_hooks("Read", {"file_path": "a.env"}, "conv-1")
    assert not res_read.blocked, "Read 不应命中 match=Edit"
    print("[PASS] pre-tool match filter")


async def test_apply_hooks_integration(tmpdir: str) -> None:
    """NormaCoder._apply_hooks 与阻断式 hook 的集成：exit 2 -> denied，stderr 回喂。"""
    from norma.agent.norma_coder import NormaCoder  # noqa: E402
    from norma.session.session import SessionManager  # noqa: E402
    from norma.core.tool_types import ToolRequest  # noqa: E402
    from norma.core.llm_types import LLMRequest, LLMResponse, AssistantMessage  # noqa: E402

    os.environ["NORMA_CONFIG_HOME"] = tmpdir
    block_helper = Path(tmpdir) / "block.py"
    block_helper.write_text(_BLOCK_HELPER, encoding="utf-8")
    cfg = HookConfig.from_dict({
        "tool-execute-before": [{
            "command": f'"{sys.executable}" "{block_helper}"',
            "background": False,
        }],
    })
    hm = HookManager(config=cfg)

    class _LLM:
        default_stream_mode = False
        max_context_tokens = 1000

        async def chat(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                response_message=AssistantMessage(content="ok", tool_calls=None),
                finish_reason="stop",
            )

        def estimate_tokens(self, messages) -> int:
            return 1

    sm = SessionManager(cwd=tmpdir)
    agent = NormaCoder(
        llm=_LLM(),  # type: ignore[arg-type]
        cwd=tmpdir,
        name="HookTest",
        enable_subagent=False,
        enable_skill=False,
        session_manager=sm,
        hook_manager=hm,
    )
    req = ToolRequest(
        tool_call_id="tc1",
        tool_call_name="Edit",
        tool_call_arguments={"file_path": "a.env", "old_string": "a", "new_string": "b"},
    )
    allowed, denied = await agent._apply_hooks([req])
    assert not allowed, "阻断后 allowed 应为空"
    assert "tc1" in denied, "应产生 denied 结果"
    content = denied["tc1"].content
    assert "blocked by hook" in content, f"denied content 应含 blocked by hook: {content!r}"
    assert denied["tc1"].is_error, "阻断结果应为 is_error=True"
    print("[PASS] _apply_hooks integration with NormaCoder")


async def test_e2e_loop_blocks_tool(tmpdir: str) -> None:
    """端到端：LLM 请求 Write .env -> PreToolUse hook exit 2 -> 工具未执行、stderr 回喂 LLM。"""
    from norma.agent.norma_coder import NormaCoder  # noqa: E402
    from norma.session.session import SessionManager  # noqa: E402
    from norma.core.tool_types import ToolRequest  # noqa: E402
    from norma.core.llm_types import (  # noqa: E402
        BaseLLM, LLMRequest, LLMResponse, AssistantMessage,
    )

    os.environ["NORMA_CONFIG_HOME"] = tmpdir
    block_helper = Path(tmpdir) / "block.py"
    block_helper.write_text(_BLOCK_HELPER, encoding="utf-8")
    cfg = HookConfig.from_dict({
        "tool-execute-before": [{
            "command": f'"{sys.executable}" "{block_helper}"',
            "background": False,
        }],
    })
    hm = HookManager(config=cfg)
    target = Path(tmpdir) / "a.env"

    class _LLM(BaseLLM):
        default_stream_mode = False
        max_context_tokens = 100000

        def __init__(self):
            self.calls = 0

        def estimate_tokens(self, messages) -> int:
            return 1

        async def chat(self, request: LLMRequest, **kwargs) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    response_message=AssistantMessage(
                        content="",
                        tool_calls=[ToolRequest(
                            tool_call_id="tc1",
                            tool_call_name="Write",
                            tool_call_arguments={"file_path": str(target), "content": "SECRET=x"},
                        )],
                    ),
                    finish_reason="tool_calls",
                )
            return LLMResponse(
                response_message=AssistantMessage(content="done", tool_calls=None),
                finish_reason="stop",
            )

        async def stream_chat(self, request: LLMRequest, **kwargs):  # type: ignore[override]
            if False:  # pragma: no cover - stream_mode=False 时不会走这里
                yield

    sm = SessionManager(cwd=tmpdir)
    agent = NormaCoder(
        llm=_LLM(),  # type: ignore[arg-type]
        cwd=tmpdir,
        name="E2EHook",
        enable_subagent=False,
        enable_skill=False,
        session_manager=sm,
        hook_manager=hm,
    )

    events = []
    async for ev in agent.run("write SECRET=x to a.env"):
        events.append(ev)

    # 工具被阻断：目标文件不应被创建
    assert not target.exists(), f"Write 应被 hook 阻断，但文件被创建: {target}"
    # stderr 已回喂：事件中存在含 blocked by hook 的工具结果
    found_blocked = False
    for ev in events:
        for r in (getattr(ev, "tool_execution_results", None) or []):
            if "blocked by hook" in (r.content or ""):
                found_blocked = True
                break
        if found_blocked:
            break
    assert found_blocked, "未在事件中找到 blocked by hook 的工具结果（stderr 未回喂）"
    print("[PASS] e2e loop: PreToolUse hook blocks tool, stderr fed back")


async def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for fn in (
            test_dispatch_and_match,
            test_bus_subscription,
            test_pre_tool_blocking_and_allow,
            test_pre_tool_json_stdin,
            test_pre_tool_match_filter,
            test_apply_hooks_integration,
            test_e2e_loop_blocks_tool,
        ):
            try:
                await fn(tmpdir)
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
    print("\nALL hook tests passed")
    return 0


def test_hook_headless() -> None:
    """pytest 入口"""
    with tempfile.TemporaryDirectory() as tmpdir:
        assert asyncio.run(_run_both(tmpdir)) == 0


async def _run_both(tmpdir: str) -> int:
    return await main()


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(asyncio.run(main()))
