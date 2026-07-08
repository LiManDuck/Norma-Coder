"""
Reminder 系统

向 agent loop 中按事件注入 ``<system-reminder>...</system-reminder>`` 文本，
以便在工具结果或用户输入之后追加上下文提示。
"""

from norma.reminder.reminder import (
    Reminder,
    ReminderEvent,
    ReminderContext,
    ReminderRegistry,
    StaticReminder,
    CallableReminder,
    SYSTEM_REMINDER_OPEN,
    SYSTEM_REMINDER_CLOSE,
    wrap_system_reminder,
)

__all__ = [
    "Reminder",
    "ReminderEvent",
    "ReminderContext",
    "ReminderRegistry",
    "StaticReminder",
    "CallableReminder",
    "SYSTEM_REMINDER_OPEN",
    "SYSTEM_REMINDER_CLOSE",
    "wrap_system_reminder",
]
