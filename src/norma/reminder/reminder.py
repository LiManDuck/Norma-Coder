"""
Reminder 系统核心实现

设计要点
--------
- ``Reminder``: 抽象基类，子类实现 ``get_text(ctx)`` 决定是否在某事件触发时注入文本。
- ``ReminderRegistry``: 注册/触发 reminders。可在 agent loop 内于工具结果之后或
  用户输入之后调用，将所有 reminder 输出拼接为单个字符串（已包裹
  ``<system-reminder>`` 标签）。
- 所有文本默认采用 ``<system-reminder>...</system-reminder>`` 包裹，
  与 Claude Code 行为保持一致。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SYSTEM_REMINDER_OPEN = "<system-reminder>"
SYSTEM_REMINDER_CLOSE = "</system-reminder>"


def wrap_system_reminder(text: str) -> str:
    """把单段文本包裹成 <system-reminder>...</system-reminder>"""
    text = text.strip("\n")
    return f"{SYSTEM_REMINDER_OPEN}\n{text}\n{SYSTEM_REMINDER_CLOSE}"


class ReminderEvent(str, Enum):
    """触发 reminder 的事件名"""
    USER_INPUT = "user-input"               # 用户刚刚输入
    TOOL_RESULT = "tool-result"             # 工具刚刚执行完
    AGENT_TURN = "agent-turn"               # 每轮 LLM 调用前
    SESSION_BEGIN = "session-begin"


@dataclass
class ReminderContext:
    """Reminder 触发时传入的上下文"""
    event: ReminderEvent
    conversation_id: Optional[str] = None
    turn_index: int = 0                     # 当前轮次（1-based 推荐）
    user_input: Optional[str] = None        # event==USER_INPUT 时
    tool_names: List[str] = field(default_factory=list)  # event==TOOL_RESULT 时
    extra: Dict[str, Any] = field(default_factory=dict)


class Reminder(ABC):
    """单个 reminder 的基类"""

    name: str = "Reminder"

    # 关心的事件，命中其中之一才调用 get_text
    events: List[ReminderEvent] = []

    @abstractmethod
    def get_text(self, ctx: ReminderContext) -> Optional[str]:
        """
        返回需要注入的提示文本（不含 <system-reminder> 标签）。
        返回 None / 空串 表示当前不注入。
        """
        ...

    def reset(self) -> None:
        """会话重置时调用（可选）"""
        return None


class StaticReminder(Reminder):
    """文本固定的 reminder，便于快速注入静态提示"""

    def __init__(
        self,
        name: str,
        text: str,
        events: Optional[List[ReminderEvent]] = None,
    ) -> None:
        self.name = name
        self._text = text
        self.events = events or [ReminderEvent.SESSION_BEGIN]

    def get_text(self, ctx: ReminderContext) -> Optional[str]:
        return self._text


class CallableReminder(Reminder):
    """从可调用对象生成文本的 reminder"""

    def __init__(
        self,
        name: str,
        events: List[ReminderEvent],
        func: Callable[[ReminderContext], Optional[str]],
    ) -> None:
        self.name = name
        self.events = list(events)
        self._func = func

    def get_text(self, ctx: ReminderContext) -> Optional[str]:
        try:
            return self._func(ctx)
        except Exception as exc:
            logger.warning(f"reminder '{self.name}' get_text error: {exc}")
            return None


class ReminderRegistry:
    """
    Reminder 注册表

    在 agent loop 中：
        text = registry.collect(ReminderContext(...))
        if text: append_to_context(text)

    ``collect`` 会自动用 ``<system-reminder>`` 标签包裹返回内容；
    如果没有 reminder 命中，返回 ``None``。
    """

    def __init__(self) -> None:
        self._reminders: Dict[str, Reminder] = {}

    # --------- 注册 / 注销 ---------
    def register(self, reminder: Reminder) -> None:
        if reminder.name in self._reminders:
            logger.warning(f"reminder '{reminder.name}' already registered, overwriting")
        self._reminders[reminder.name] = reminder

    def register_static(
        self,
        name: str,
        text: str,
        events: Optional[List[ReminderEvent]] = None,
    ) -> None:
        self.register(StaticReminder(name, text, events))

    def register_callable(
        self,
        name: str,
        events: List[ReminderEvent],
        func: Callable[[ReminderContext], Optional[str]],
    ) -> None:
        self.register(CallableReminder(name, events, func))

    def unregister(self, name: str) -> bool:
        return self._reminders.pop(name, None) is not None

    def has(self, name: str) -> bool:
        return name in self._reminders

    def reset_all(self) -> None:
        for r in self._reminders.values():
            try:
                r.reset()
            except Exception as exc:
                logger.warning(f"reminder '{r.name}' reset error: {exc}")

    # --------- 触发 ---------
    def collect(self, ctx: ReminderContext) -> Optional[str]:
        """
        遍历所有 reminder，返回拼接后的 system-reminder 块；无则返回 None。
        每条 reminder 单独包裹一个 <system-reminder>，便于阅读 / 调试。
        """
        chunks: List[str] = []
        for reminder in self._reminders.values():
            if reminder.events and ctx.event not in reminder.events:
                continue
            try:
                text = reminder.get_text(ctx)
            except Exception as exc:
                logger.warning(f"reminder '{reminder.name}' raised: {exc}")
                continue
            if not text:
                continue
            chunks.append(wrap_system_reminder(text))
        if not chunks:
            return None
        return "\n".join(chunks)
