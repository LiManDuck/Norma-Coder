"""
Task tools 状态相关的 reminder：

- ``TaskNudgeReminder``：当 LLM 在多轮内都没有使用 Task 工具，但当前
  又确实在做复杂工作时（多轮对话），提醒它考虑使用 TaskCreate / TaskUpdate
  来跟踪进度。

实现思路与 Claude Code 的 ``task_reminder`` 类似：
- 每次工具结果回调时统计：本轮是否有任务工具被调用？若有则重置计数；否则 +1。
- 计数超过阈值且没有当前的 in_progress 任务时，向上下文注入提醒。
"""

from __future__ import annotations

from typing import List, Optional

from norma.reminder import Reminder, ReminderContext, ReminderEvent

TASK_TOOL_NAMES = {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet"}

# 与 Claude Code 的 TODO_REMINDER_CONFIG 对齐
TURNS_SINCE_USE_THRESHOLD = 10
TURNS_BETWEEN_REMINDERS = 10


class TaskNudgeReminder(Reminder):
    """提醒 LLM 在合适的时候使用 Task 工具"""

    name = "task-nudge"
    events = [ReminderEvent.TOOL_RESULT, ReminderEvent.USER_INPUT]

    def __init__(
        self,
        threshold_turns: int = TURNS_SINCE_USE_THRESHOLD,
        cooldown_turns: int = TURNS_BETWEEN_REMINDERS,
    ) -> None:
        self.threshold_turns = threshold_turns
        self.cooldown_turns = cooldown_turns
        self._turns_since_use = 0
        self._turns_since_reminder = 0
        self._reminded_once = False

    def reset(self) -> None:
        self._turns_since_use = 0
        self._turns_since_reminder = 0
        self._reminded_once = False

    def get_text(self, ctx: ReminderContext) -> Optional[str]:
        if ctx.event == ReminderEvent.TOOL_RESULT:
            tools_used: List[str] = ctx.tool_names or []
            if any(t in TASK_TOOL_NAMES for t in tools_used):
                # 重置
                self._turns_since_use = 0
                self._turns_since_reminder = self.cooldown_turns  # 立即可再提醒
                return None
            self._turns_since_use += 1
            self._turns_since_reminder += 1
        elif ctx.event == ReminderEvent.USER_INPUT:
            # 用户重新发问也算一轮
            self._turns_since_use += 1
            self._turns_since_reminder += 1

        if self._turns_since_use < self.threshold_turns:
            return None
        if self._reminded_once and self._turns_since_reminder < self.cooldown_turns:
            return None

        self._turns_since_reminder = 0
        self._reminded_once = True
        return (
            "Task 工具最近一段时间没有被使用。如果你正在进行需要跟踪进度的多步任务，"
            "请考虑使用 TaskCreate 添加新任务，并通过 TaskUpdate 把状态更新为 "
            "in_progress / completed；如果列表已经过时也可以及时清理。\n"
            "仅在与当前工作相关时使用——不要向用户提及本提醒。"
        )
