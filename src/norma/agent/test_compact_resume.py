"""resume-after-compaction 正确性回归测试。

验证两件事：
1. ``_do_compact`` 执行后会把 ``compact_boundary`` 边界写入 session jsonl，
   且内存被替换为 [system, 摘要]。
2. ``restore_from_session`` 遇到 ``compact_boundary`` 时，丢弃边界前的全部重放，
   仅保留 system + 摘要 + 边界后的后续轮次（而不是重放全量历史）。

运行：``python -m norma.agent.test_compact_resume``
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# 确保以脚本方式运行时 src 在 path 上
_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.llm_types import (  # noqa: E402
    AssistantMessage,
    LLMRequest,
    LLMResponse,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from norma.core.tool_types import ToolRequest, ToolRequestResult  # noqa: E402
from norma.agent.norma_coder import NormaCoder  # noqa: E402
from norma.session.session import SessionManager, sanitize_path  # noqa: E402


class _FakeLLM:
    """最小 LLM mock：仅实现 _do_compact 用到的 chat / max_context_tokens / estimate_tokens"""

    default_stream_mode = False
    max_context_tokens = 1000

    def estimate_tokens(self, messages) -> int:
        return sum(len(getattr(m, "content", "") or "") for m in messages)

    async def chat(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            response_message=AssistantMessage(content="这是压缩摘要。", tool_calls=None),
            finish_reason="stop",
        )


def _make_agent(tmp_cwd: str, sm: SessionManager) -> NormaCoder:
    return NormaCoder(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        cwd=tmp_cwd,
        name="TestCoder",
        enable_subagent=False,
        enable_skill=False,
        session_manager=sm,
    )


async def test_compact_writes_boundary(tmp_cwd: str, config_home: str) -> None:
    os.environ["NORMA_CONFIG_HOME"] = config_home
    sm = SessionManager(cwd=tmp_cwd)
    rec = sm.create(title="compact-test")
    sid = rec.session_id

    agent = _make_agent(tmp_cwd, sm)
    # 灌入若干历史消息（system 已在构造时加入）
    await agent.memory.push_messages([
        UserMessage(content="用户问题A"),
        AssistantMessage(content="助手回答B", tool_calls=None),
    ])

    await agent._do_compact()

    # 1) 内存被替换为 [system, 摘要]
    msgs = agent.memory._messages
    assert len(msgs) == 2, f"期望压缩后 2 条消息，实际 {len(msgs)}"
    assert isinstance(msgs[0], SystemMessage), "第一条应为 SystemMessage"
    assert isinstance(msgs[1], UserMessage), "第二条应为 UserMessage(摘要)"
    assert "这是压缩摘要。" in msgs[1].content, "摘要内容应进入 UserMessage"

    # 2) session jsonl 中存在 compact_boundary 条目，且 content 与内存摘要一致
    path = Path(config_home) / "projects" / sanitize_path(tmp_cwd) / f"{sid}.jsonl"
    assert path.exists(), f"session 文件不存在: {path}"
    found_boundary = False
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("type") == "compact_boundary":
                found_boundary = True
                assert e.get("content") == msgs[1].content, "边界 content 应与内存摘要一致"
    assert found_boundary, "session 中未找到 compact_boundary 条目"
    print("[PASS] test_compact_writes_boundary")


async def test_restore_trims_at_boundary(tmp_cwd: str, config_home: str) -> None:
    os.environ["NORMA_CONFIG_HOME"] = config_home
    sm = SessionManager(cwd=tmp_cwd)
    rec = sm.create(title="restore-test")
    sid = rec.session_id

    summary_content = (
        "<compact-boundary>\n以下是之前对话的摘要：\n历史摘要S\n</compact-boundary>"
    )
    # 手工构造一个含压缩边界的 session：
    #   user A -> assistant B -> [compact_boundary(S)] -> user C -> assistant D
    sm.append({"type": "user", "content": "用户问题A"})
    sm.append({"type": "assistant", "content": "助手回答B", "tool_calls": None})
    sm.append({"type": "compact_boundary", "content": summary_content})
    sm.append({"type": "user", "content": "压缩后用户C"})
    sm.append({"type": "assistant", "content": "压缩后助手D", "tool_calls": None})

    agent = _make_agent(tmp_cwd, sm)
    restored = await agent.restore_from_session(sid)

    msgs = agent.memory._messages
    # 期望：system + 摘要 + 压缩后 user C + 压缩后 assistant D  = 4 条
    # 边界前的 user A / assistant B 必须被丢弃
    assert len(msgs) == 4, f"期望恢复 4 条消息，实际 {len(msgs)}: {[m.content[:20] for m in msgs]}"
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], UserMessage) and msgs[1].content == summary_content, \
        f"第二条应为摘要，实际: {msgs[1].content!r}"
    assert isinstance(msgs[2], UserMessage) and msgs[2].content == "压缩后用户C"
    assert isinstance(msgs[3], AssistantMessage) and msgs[3].content == "压缩后助手D"
    # restored 计数：边界重置为 1，再加压缩后的 2 条 = 3
    assert restored == 3, f"期望 restored=3，实际 {restored}"
    print("[PASS] test_restore_trims_at_boundary")


async def test_micro_compact(tmp_cwd: str, config_home: str) -> None:
    """微压缩：截断较早的 tool_result，保留近期 N 条，不删消息、保留 tool_call_id"""
    os.environ["NORMA_CONFIG_HOME"] = config_home
    sm = SessionManager(cwd=tmp_cwd)
    sm.create(title="micro-test")
    agent = _make_agent(tmp_cwd, sm)
    agent._tool_retain = 6  # 保留最近 6 条 tool_result

    # 构造 8 个 (assistant+tool) 轮次，tool_result 内容很长（500 字符）
    long_content = "X" * 500
    for i in range(8):
        req = ToolRequest(
            tool_call_id=f"tc_{i}",
            tool_call_name="Read",
            tool_call_arguments={"path": f"f{i}"},
        )
        await agent.memory.push_messages([AssistantMessage(
            content=f"turn {i}", tool_calls=[req],
        )])
        await agent.memory.push_messages([ToolMessage(
            tool_result=ToolRequestResult(
                request=req, result=long_content, content=long_content,
                is_error=False, execution_times=0.0,
            ),
            content=long_content,
        )])

    tool_before = [m for m in agent.memory._messages if isinstance(m, ToolMessage)]
    assert len(tool_before) == 8

    changed = await agent._micro_compact()
    assert changed is True, "应有微压缩改动（8>6）"

    tool_after = [m for m in agent.memory._messages if isinstance(m, ToolMessage)]
    assert len(tool_after) == 8, "微压缩不应删除消息"
    # 最近 6 条保持原文
    for m in tool_after[-6:]:
        assert m.content == long_content, "最近 6 条 tool_result 应保持原文"
    # 最早 2 条被截断
    for m in tool_after[:2]:
        assert len(m.content) < 500, "较早的 tool_result 应被截断"
        assert m.content.startswith("X" * 300), "应保留前 300 字符"
        assert "已微压缩" in m.content, "应含截断占位标记"
        # tool_call_id 链接保持
        assert m.tool_call_id in {f"tc_{i}" for i in range(2)}

    # 再调一次（已无超过 retain 的可压缩新增）应返回 False
    changed2 = await agent._micro_compact()
    # 最早 2 条已短于阈值，不会被再次截断 -> False
    assert changed2 is False, "已无可压缩项时应返回 False"
    print("[PASS] test_micro_compact")


async def main() -> int:
    failures = 0
    for runner, name in (
        (test_compact_writes_boundary, "compact_writes_boundary"),
        (test_restore_trims_at_boundary, "restore_trims_at_boundary"),
        (test_micro_compact, "micro_compact"),
    ):
        with tempfile.TemporaryDirectory() as tmp_cwd, \
                tempfile.TemporaryDirectory() as config_home:
            try:
                await runner(tmp_cwd, config_home)
            except AssertionError as exc:
                print(f"[FAIL] {name}: {exc}")
                failures += 1
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[ERROR] {name}: {exc}")
                traceback.print_exc()
                failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL compact/resume tests passed")
    return 0


if __name__ == "__main__":
    # pytest 入口（可选）
    async def _pytest_compact():
        await test_compact_writes_boundary(tempfile.mkdtemp(), tempfile.mkdtemp())

    async def _pytest_restore():
        await test_restore_trims_at_boundary(tempfile.mkdtemp(), tempfile.mkdtemp())

    async def _pytest_micro():
        await test_micro_compact(tempfile.mkdtemp(), tempfile.mkdtemp())

    def test_compact_writes_boundary_pytest():
        asyncio.run(_pytest_compact())

    def test_restore_trims_at_boundary_pytest():
        asyncio.run(_pytest_restore())

    def test_micro_compact_pytest():
        asyncio.run(_pytest_micro())

    raise SystemExit(asyncio.run(main()))
