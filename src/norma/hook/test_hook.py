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


async def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for fn in (test_dispatch_and_match, test_bus_subscription):
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
