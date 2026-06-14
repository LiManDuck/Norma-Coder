"""
Hook 系统

设计目标
--------
允许用户在配置文件 ``hooks`` 字段中声明：在某事件发生时执行外部 shell 命令。

支持的事件 (HookEvent)
----------------------
- session-begin     : REPL 启动时
- session-end       : REPL 退出时
- user-input        : 用户输入事件
- tool-execute-before : 工具调用前
- tool-execute-after  : 工具调用后
- agent-response    : agent 生成最终响应

实现机制
--------
HookManager 订阅消息总线 (MessageBus) 上对应的消息类型。当收到匹配事件时，
从配置中查找匹配的 HookSpec，然后异步调度 shell 命令执行。

配置示例
--------
```json
{
  "hooks": {
    "session-begin": [{"command": "echo session start"}],
    "tool-execute-after": [{"command": "echo $TOOL_NAME", "match": {"tool_name": "Edit"}}]
  }
}
```

被执行的命令会注入若干环境变量，例如 ``TOOL_NAME``、``EVENT``、``CONVERSATION_ID``，
以便用户脚本可识别上下文。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from norma.messagebus.messagebus import (
    Message,
    MessageBus,
    MessageType,
)

logger = logging.getLogger(__name__)


# ====================== 枚举 ======================

class HookEvent(str, Enum):
    """支持的 hook 事件名称（与配置文件中的 key 一一对应）"""

    SESSION_BEGIN = "session-begin"
    SESSION_END = "session-end"
    USER_INPUT = "user-input"
    TOOL_EXECUTE_BEFORE = "tool-execute-before"
    TOOL_EXECUTE_AFTER = "tool-execute-after"
    AGENT_RESPONSE = "agent-response"

    @classmethod
    def from_value(cls, value: Any) -> Optional["HookEvent"]:
        if value is None:
            return None
        try:
            return cls(str(value).lower().replace("_", "-"))
        except ValueError:
            return None


# 事件 -> messagebus 消息类型的映射
EVENT_TO_MESSAGE_TYPE: Dict[HookEvent, MessageType] = {
    HookEvent.USER_INPUT: MessageType.USER_INPUT,
    HookEvent.TOOL_EXECUTE_BEFORE: MessageType.AGENT_TOOL_REQUEST,
    HookEvent.TOOL_EXECUTE_AFTER: MessageType.AGENT_TOOL_RESULT,
    HookEvent.AGENT_RESPONSE: MessageType.AGENT_RESPONSE,
}


# ====================== 配置 ======================

@dataclass
class HookSpec:
    """单条 hook 配置"""

    command: str
    match: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    background: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["HookSpec"]:
        if not isinstance(data, dict):
            return None
        command = data.get("command")
        if not command:
            return None
        return cls(
            command=str(command),
            match=dict(data.get("match") or {}),
            timeout=float(data.get("timeout", 30.0)),
            background=bool(data.get("background", True)),
        )


@dataclass
class HookConfig:
    """所有 hook 的集合"""

    hooks: Dict[HookEvent, List[HookSpec]] = field(default_factory=dict)

    def get(self, event: HookEvent) -> List[HookSpec]:
        return self.hooks.get(event, [])

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "HookConfig":
        if not data:
            return cls()
        result: Dict[HookEvent, List[HookSpec]] = {}
        for raw_event, raw_specs in data.items():
            event = HookEvent.from_value(raw_event)
            if event is None:
                logger.warning(f"unknown hook event '{raw_event}', skipped")
                continue
            specs: List[HookSpec] = []
            if isinstance(raw_specs, dict):
                raw_specs = [raw_specs]
            if not isinstance(raw_specs, list):
                logger.warning(f"hook value of '{raw_event}' must be list/dict, skipped")
                continue
            for item in raw_specs:
                # 允许直接是字符串 -> 视为 command
                if isinstance(item, str):
                    specs.append(HookSpec(command=item))
                else:
                    spec = HookSpec.from_dict(item)
                    if spec is not None:
                        specs.append(spec)
            if specs:
                result[event] = specs
        return cls(hooks=result)


# ====================== 管理器 ======================

class HookManager:
    """
    Hook 管理器：监听 messagebus 事件，触发对应 shell command。
    """

    def __init__(
        self,
        config: Optional[HookConfig] = None,
        message_bus: Optional[MessageBus] = None,
    ):
        self.config = config or HookConfig()
        self.message_bus = message_bus
        self._subscribed = False

    # ---------- 注册 ----------

    def attach(self, message_bus: MessageBus) -> None:
        """订阅 messagebus 上的对应事件"""
        if self._subscribed:
            return
        self.message_bus = message_bus
        for event, msg_type in EVENT_TO_MESSAGE_TYPE.items():
            message_bus.subscribe(msg_type, self._make_handler(event))
        self._subscribed = True

    # ---------- 事件分发 ----------

    def _make_handler(self, event: HookEvent):
        async def _handler(message: Message):
            await self.dispatch(event, message=message)
        return _handler

    async def dispatch(
        self,
        event: HookEvent,
        message: Optional[Message] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """显式触发 hook（session-begin/session-end 由调用方手动触发）"""
        specs = self.config.get(event)
        if not specs:
            return

        env_extra = self._build_env(event, message, context)

        for spec in specs:
            if not self._match(spec, env_extra):
                continue
            await self._run(spec, env_extra)

    # ---------- 匹配 / 环境 ----------

    def _build_env(
        self,
        event: HookEvent,
        message: Optional[Message],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, str]:
        env: Dict[str, str] = {
            "NORMA_HOOK_EVENT": event.value,
            "EVENT": event.value,
        }
        if message is not None:
            if message.conversation_id:
                env["CONVERSATION_ID"] = message.conversation_id
            payload = message.payload
            tool_name = self._extract_tool_name(payload)
            if tool_name:
                env["TOOL_NAME"] = tool_name
            if isinstance(payload, dict) and "text" in payload:
                env["USER_INPUT"] = str(payload.get("text", ""))[:1024]

        if context:
            for k, v in context.items():
                if v is None:
                    continue
                env[str(k).upper()] = str(v)

        return env

    @staticmethod
    def _extract_tool_name(payload: Any) -> Optional[str]:
        if payload is None:
            return None
        # 可能是 AgentToolRequestEvent / AgentToolRequestAnswerEvent
        for attr in ("tool_calls", "tool_execution_results"):
            seq = getattr(payload, attr, None)
            if seq:
                first = seq[0]
                name = getattr(first, "tool_call_name", None)
                if name:
                    return name
                req = getattr(first, "request", None)
                if req is not None:
                    name = getattr(req, "tool_call_name", None)
                    if name:
                        return name
        return None

    @staticmethod
    def _match(spec: HookSpec, env: Dict[str, str]) -> bool:
        """match 字段中的 key/value 必须全部命中环境"""
        if not spec.match:
            return True
        for key, expected in spec.match.items():
            actual = env.get(str(key).upper())
            if actual != str(expected):
                return False
        return True

    # ---------- 执行 ----------

    async def _run(self, spec: HookSpec, env_extra: Dict[str, str]) -> None:
        env = os.environ.copy()
        env.update(env_extra)
        command = spec.command

        async def _exec():
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=spec.timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    logger.warning(f"hook timeout: {shlex.quote(command)}")
                    return
                if proc.returncode != 0:
                    logger.warning(
                        f"hook command failed (rc={proc.returncode}): "
                        f"{shlex.quote(command)} | stderr={stderr[:200]!r}"
                    )
                else:
                    logger.debug(
                        f"hook ok: {shlex.quote(command)} | stdout={stdout[:200]!r}"
                    )
            except Exception as exc:
                logger.warning(f"hook execution error: {exc}")

        if spec.background:
            asyncio.create_task(_exec())
        else:
            await _exec()
