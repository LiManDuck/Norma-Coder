"""Session 持久化回归测试（headless，隔离 NORMA_CONFIG_HOME 到临时目录）。

锁定 SessionManager 的关键不变量：

1. **往返一致**：``create -> append(user/assistant/tool) -> close -> replay_messages``
   读回的 entries 类型/内容/顺序保真，且 append 自动补 ``ts`` 时间戳；
2. **损坏行容错**：jsonl 中混入未闭合的 JSON 行时，``replay_messages`` 跳过该行、
   不崩，有效 entries 数量不变--这是 session 抗崩溃的设计核心（追加写 + 容错读，
   写入中途断电/Ctrl+C 留半截行不破坏读取）；
3. **list_sessions** 能列出会话并统计 ``message_count``（仅计 user/assistant/tool）；
4. 不存在的 session：``load`` 返回 None、``replay_messages`` 返回空列表。

此前 session.py 无直接回归覆盖（仅经 test_compact_resume 间接走 restore_from_session）。

运行：``python -m norma.session.test_session``
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.session.session import SessionManager  # noqa: E402


def test_session_roundtrip_and_torn_line_tolerance() -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["NORMA_CONFIG_HOME"] = tmpdir
        try:
            mgr = SessionManager(cwd=tmpdir)
            record = mgr.create(session_id="test1234", title="mytitle")
            assert record.session_id == "test1234"
            record.append({"type": "user", "content": "hello"})
            record.append({"type": "assistant", "content": "hi",
                           "tool_calls": [{"id": "tc1", "name": "Read"}]})
            record.append({"type": "tool", "tool_call_id": "tc1",
                           "tool_name": "Read", "content": "FILE", "is_error": False})
            record.close()

            # 往返：replay_messages 读回全部 entries
            entries = mgr.replay_messages("test1234")
            types = [e.get("type") for e in entries]
            assert "user" in types and "assistant" in types and "tool" in types, (
                f"应读回 user/assistant/tool，实际 types={types}")
            user_e = next(e for e in entries if e.get("type") == "user")
            assert user_e["content"] == "hello", f"user content 不符: {user_e}"
            tool_e = next(e for e in entries if e.get("type") == "tool")
            assert tool_e["content"] == "FILE" and tool_e["tool_call_id"] == "tc1", (
                f"tool entry 不符: {tool_e}")
            assert "ts" in user_e, "append 应自动补 ts 时间戳"

            # 容错：追加一行损坏 JSON + 其后一行有效 JSON。replay_messages 应跳过
            # 损坏行、**继续**读出其后的有效行--仅靠外层 try/except 会在损坏行处中断
            # 并丢失其后内容，内层逐行容错（except: continue）才是抗中断关键。
            fpath = record.file_path
            with open(fpath, "a", encoding="utf-8") as f:
                f.write('{"broken json 不闭合\n')
                f.write('{"type": "user", "content": "after_torn"}\n')
            entries2 = mgr.replay_messages("test1234")
            # 损坏行被跳过，且其后的有效行仍被读出 -> 比原 entries 多 1
            assert len(entries2) == len(entries) + 1, (
                f"损坏行应被跳过、其后的有效行仍应读出，"
                f"预期 {len(entries) + 1}，实际 {len(entries2)}")
            assert any(e.get("content") == "after_torn" for e in entries2), (
                "损坏行之后的有效 user 行应被读出")
            assert all(e.get("content") != "broken json 不闭合" for e in entries2), (
                "损坏行不应进入结果")

            # list_sessions 能列出并统计 message_count（user+assistant+tool+after_torn = 4）
            sessions = mgr.list_sessions()
            assert any(s.session_id == "test1234" for s in sessions), (
                f"list_sessions 应列出 test1234，"
                f"实际 {[s.session_id for s in sessions]}")
            s = next(s for s in sessions if s.session_id == "test1234")
            assert s.message_count == 4, (
                f"message_count 应为 4（user+assistant+tool+after_torn），"
                f"实际 {s.message_count}")
            return True
        finally:
            os.environ.pop("NORMA_CONFIG_HOME", None)


def test_load_nonexistent_returns_none() -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["NORMA_CONFIG_HOME"] = tmpdir
        try:
            mgr = SessionManager(cwd=tmpdir)
            assert mgr.load("no_such_session") is None, "不存在的 session 应返回 None"
            assert mgr.replay_messages("no_such_session") == [], (
                "不存在的 session replay 应返回空列表")
            return True
        finally:
            os.environ.pop("NORMA_CONFIG_HOME", None)


def _amain() -> int:
    tests = [
        ("session_roundtrip_and_torn_line_tolerance",
         test_session_roundtrip_and_torn_line_tolerance),
        ("load_nonexistent_returns_none", test_load_nonexistent_returns_none),
    ]
    failures = 0
    for name, fn in tests:
        try:
            ok = fn()
            assert ok, f"{name} returned False/None"
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_session_headless() -> None:
    """pytest 入口。"""
    assert _amain() == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(_amain())
