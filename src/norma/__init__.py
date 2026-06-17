"""Norma Coder - Python implementation of Claude Code style coding agent.

SDK 用法示例::

    from norma import NormaCoder, OpenAILLM, PermissionConfig, PermissionMode

    llm = OpenAILLM(model="gpt-4o", api_key="sk-...", base_url="...")
    agent = NormaCoder(
        llm=llm,
        cwd=".",
        permission_checker=PermissionChecker(
            PermissionConfig(mode=PermissionMode.AUTO),
        ),
        # 还可注入: hook_manager / reminder_registry / skill_registry /
        # session_manager / subagent_factory / tools=[CustomTool(), ...]
    )
    async for event in agent.run("帮我写一个 hello world"):
        ...
"""

from norma.agent.norma_coder import NormaCoder
from norma.core.openai_llm import OpenAILLM
from norma.core.llm_types import (
    BaseLLM,
    LLMRequest,
    LLMResponse,
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolMessage,
)
from norma.core.tool_types import (
    Tool,
    ToolRequest,
    ToolRequestResult,
)
from norma.core.agent_types import (
    BaseAgent,
    AgentEvent,
    AgentResponse,
)
from norma.permission import (
    PermissionMode,
    PermissionDecision,
    PermissionConfig,
    PermissionChecker,
)
from norma.hook import HookManager, HookConfig, HookEvent
from norma.reminder import ReminderRegistry, Reminder, ReminderEvent, ReminderContext
from norma.skill import SkillRegistry
from norma.mcp import MCPManager
from norma.session import SessionManager, SessionMeta
from norma.messagebus.messagebus import MessageBus, UserInputManager

__all__ = [
    # Agent
    "NormaCoder",
    "BaseAgent",
    "AgentEvent",
    "AgentResponse",
    # LLM
    "OpenAILLM",
    "BaseLLM",
    "LLMRequest",
    "LLMResponse",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    # Tools
    "Tool",
    "ToolRequest",
    "ToolRequestResult",
    # Permission
    "PermissionMode",
    "PermissionDecision",
    "PermissionConfig",
    "PermissionChecker",
    # Hook / reminder / skill / mcp / session
    "HookManager",
    "HookConfig",
    "HookEvent",
    "ReminderRegistry",
    "Reminder",
    "ReminderEvent",
    "ReminderContext",
    "SkillRegistry",
    "MCPManager",
    "SessionManager",
    "SessionMeta",
    # Bus
    "MessageBus",
    "UserInputManager",
]
