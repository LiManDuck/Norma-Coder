"""Reminder 系统回归测试（headless）。

验证：
- StaticReminder / CallableReminder 注册与触发
- 事件过滤（USER_INPUT 的 reminder 不在 TOOL_RESULT 触发）
- collect 自动包裹 <system-reminder> 标签；无命中返回 None
- TaskNudgeReminder：超过阈值未用 Task 工具则提醒；使用 Task 工具后重置

运行：``python -m norma.reminder.test_reminder``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.reminder import (  # noqa: E402
    ReminderContext,
    ReminderEvent,
    ReminderRegistry,
    StaticReminder,
)
from norma.reminder.task_reminder import TaskNudgeReminder, TASK_TOOL_NAMES  # noqa: E402


def test_static_and_filtering() -> None:
    reg = ReminderRegistry()
    reg.register(StaticReminder(
        name="user-hint", text="记住用户偏好",
        events=[ReminderEvent.USER_INPUT],
    ))
    reg.register(StaticReminder(
        name="tool-hint", text="检查工具结果",
        events=[ReminderEvent.TOOL_RESULT],
    ))

    # USER_INPUT 事件：只有 user-hint 触发
    text = reg.collect(ReminderContext(event=ReminderEvent.USER_INPUT, user_input="hi"))
    assert text is not None
    assert "<system-reminder>" in text and "</system-reminder>" in text
    assert "记住用户偏好" in text
    assert "检查工具结果" not in text, "TOOL_RESULT 的 reminder 不应在 USER_INPUT 触发"

    # TOOL_RESULT 事件：只有 tool-hint 触发
    text2 = reg.collect(ReminderContext(event=ReminderEvent.TOOL_RESULT, tool_names=["Read"]))
    assert text2 is not None
    assert "检查工具结果" in text2
    assert "记住用户偏好" not in text2

    # 无命中事件
    text3 = reg.collect(ReminderContext(event=ReminderEvent.AGENT_TURN))
    assert text3 is None, "无 reminder 关心 AGENT_TURN，应返回 None"
    print("[PASS] static reminder + event filtering + wrapping")


def test_callable_reminder() -> None:
    reg = ReminderRegistry()
    reg.register_callable(
        name="echo-input",
        events=[ReminderEvent.USER_INPUT],
        func=lambda ctx: f"用户说了: {ctx.user_input}" if ctx.user_input else None,
    )
    text = reg.collect(ReminderContext(event=ReminderEvent.USER_INPUT, user_input="hello"))
    assert text is not None and "用户说了: hello" in text
    # user_input 为空 -> 返回 None -> 不注入
    text2 = reg.collect(ReminderContext(event=ReminderEvent.USER_INPUT, user_input=None))
    assert text2 is None
    print("[PASS] callable reminder")


def test_task_nudge() -> None:
    reg = ReminderRegistry()
    nudge = TaskNudgeReminder(threshold_turns=3, cooldown_turns=3)
    reg.register(nudge)

    # 阈值前不提醒
    for i in range(3):
        text = reg.collect(ReminderContext(event=ReminderEvent.TOOL_RESULT, tool_names=["Read"]))
    # 第 3 次（_turns_since_use 达到 3）应触发提醒
    assert text is not None and "TaskCreate" in text, f"达到阈值应提醒: {text!r}"

    # 使用 Task 工具后重置
    text_after_task = reg.collect(ReminderContext(
        event=ReminderEvent.TOOL_RESULT, tool_names=["TaskUpdate"],
    ))
    assert text_after_task is None, "使用 Task 工具后应重置、不提醒"

    # 再次累计到阈值才提醒
    for i in range(2):
        t = reg.collect(ReminderContext(event=ReminderEvent.TOOL_RESULT, tool_names=["Read"]))
        assert t is None, f"重置后未达阈值不应提醒 (i={i})"
    t = reg.collect(ReminderContext(event=ReminderEvent.TOOL_RESULT, tool_names=["Read"]))
    assert t is not None and "TaskCreate" in t, "再次达阈值应提醒"
    print("[PASS] TaskNudgeReminder threshold + reset")


def main() -> int:
    failures = 0
    for fn in (
        test_static_and_filtering,
        test_callable_reminder,
        test_task_nudge,
    ):
        try:
            fn()
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
    print("\nALL reminder tests passed")
    return 0


def test_reminder_headless() -> None:
    assert main() == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(main())
