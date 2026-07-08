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
    UserMessage,
)
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


async def main() -> int:
    failures = 0
    for runner, name in (
        (test_compact_writes_boundary, "compact_writes_boundary"),
        (test_restore_trims_at_boundary, "restore_trims_at_boundary"),
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

    def test_compact_writes_boundary_pytest():
        asyncio.run(_pytest_compact())

    def test_restore_trims_at_boundary_pytest():
        asyncio.run(_pytest_restore())

    raise SystemExit(asyncio.run(main()))
