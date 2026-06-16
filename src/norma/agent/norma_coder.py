"""
NormaCoder - 主 Agent

更新（2026-06-15）
------------------
- 以 finish_reason 驱动主循环：stop → 结束，tool_calls → 继续循环
- 支持一次回复同时包含文本内容和 tool_calls
- 工具目录从 prompt/tool/ 迁移至 tool/
"""

from typing import (
     Optional,
     AsyncGenerator,
     List, Union, Callable
)
import json
import uuid
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from norma.core.tool_types import (
    ToolRequest,
    ToolRequestResult,
    Tool,
)
from norma.core.agent_types import (
    BaseAgent,
    AgentEvent,
    AgentInputEvent,
    AgentLLMRequestEvent,
    AgentResponse,
    AgentLLMResponseEvent,
    AgentToolRequestEvent,
    AgentToolRequestAnswerEvent
)
from norma.core.llm_types import (
    BaseLLM,
    LLMMessage,
    UserMessage,
    AssistantMessage,
    ToolMessage,
    SystemMessage,
    LLMRequest,
    LLMResponse
)

from norma.memory.agent_memory import (
    AgentMemory
)
from norma.prompt.system_prompt import SystemPromptService
from norma.tool.tool_core import (
    NormaArtifact
)

from norma.tool import (
    ReadTool,
    LsTool,
    GlobTool,
    GrepTool,
    EditTool,
    WriteTool,
    BashTool,
    AgentTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    SkillTool,
)

from norma.messagebus.messagebus import (
    AgentMessageAdapter,
    Message,
    MessageBus,
    MessageType,
    UserInputManager,
)
from norma.permission import (
    PermissionChecker,
    PermissionDecision,
)
from norma.hook import HookEvent, HookManager
from norma.reminder import (
    ReminderEvent,
    ReminderContext,
    ReminderRegistry,
)
from norma.skill import SkillRegistry


logger = logging.getLogger(__name__)


class NormaCoder(BaseAgent):
    """
    NormaCoder - 基于LLM的代码助手Agent

    主循环由 finish_reason 驱动:
    - stop / content_filter → 结束循环，返回最终响应
    - tool_calls → 执行工具调用后继续循环
    - length → 上下文超限，触发 compaction 或停止
    """

    @property
    def name(self):
        return self._name

    def __init__(self,
        llm: BaseLLM,
        cwd: str | Path,
        allowd_dirs: list[str | Path] = [],
        name: str = 'NormaCoder',
        tools: Optional[List[Tool]] = None,
        delta_instructions: Optional[str] = None,
        memory_tool_message_limit: int = 10,
        max_runturns: int = 100,
        # 系统总线 / 权限 / hook
        message_bus: Optional[MessageBus] = None,
        permission_checker: Optional[PermissionChecker] = None,
        hook_manager: Optional[HookManager] = None,
        user_input_manager: Optional[UserInputManager] = None,
        enable_subagent: bool = True,
        subagent_factory: Optional[Callable[..., BaseAgent]] = None,
        conversation_id: Optional[str] = None,
        reminder_registry: Optional[ReminderRegistry] = None,
        skill_registry: Optional[SkillRegistry] = None,
        enable_skill: bool = True,
        # compaction
        compact_threshold: float = 0.75,
    ):
        self._name = name
        self.llm = llm
        self.cwd = cwd
        self.max_runturns = max_runturns
        self.compact_threshold = compact_threshold

        # ---- 系统总线 / 权限 / hook ----
        self.message_bus = message_bus
        self.permission_checker = permission_checker
        self.hook_manager = hook_manager
        self.user_input_manager = user_input_manager
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self._adapter = (
            AgentMessageAdapter(message_bus) if message_bus is not None else None
        )
        # ---- reminder ----
        self.reminder_registry = reminder_registry or ReminderRegistry()
        # ---- skill ----
        self.skill_registry = skill_registry or SkillRegistry()

        # ---- 工具 ----
        default_tools: List[Tool] = [
            ReadTool(),
            LsTool(cwd=cwd),
            GlobTool(cwd=cwd),
            GrepTool(),
            EditTool(),
            WriteTool(),
            TaskCreateTool(),
            TaskListTool(),
            TaskGetTool(),
            TaskUpdateTool(),
            BashTool(cwd=cwd),
        ]

        if enable_subagent:
            factory = subagent_factory or self._default_subagent_factory
            default_tools.append(AgentTool(agent_factory=factory))

        if enable_skill and self.skill_registry.all():
            skill_factory = subagent_factory or self._default_subagent_factory
            default_tools.append(
                SkillTool(
                    registry=self.skill_registry,
                    agent_factory=skill_factory,
                )
            )

        all_tools = default_tools + (tools or [])
        self.tool_manager = NormaArtifact(tools=all_tools)

        # ---- 系统提示 + memory ----
        self.system_prompt = SystemPromptService.get_claude_code_system_prompt(
            cwd=str(cwd)
        )
        messages = [SystemMessage(content=self.system_prompt)]
        self.memory = AgentMemory(
            message_list=messages,
            save_toolmessage_num=memory_tool_message_limit,
        )

    # =====================================================
    # 子 agent 工厂
    # =====================================================
    def _default_subagent_factory(self, name: Optional[str] = None) -> "NormaCoder":
        """默认的子 agent 工厂：复用 LLM 与 cwd，禁用嵌套 subagent，避免无限递归"""
        return NormaCoder(
            llm=self.llm,
            cwd=self.cwd,
            name=name or "NormaSubAgent",
            max_runturns=self.max_runturns,
            message_bus=self.message_bus,
            permission_checker=self.permission_checker,
            hook_manager=None,
            user_input_manager=self.user_input_manager,
            enable_subagent=False,
            conversation_id=self.conversation_id,
            reminder_registry=self.reminder_registry,
            skill_registry=self.skill_registry,
            enable_skill=False,
        )

    # =====================================================
    # 主循环 - 由 finish_reason 驱动
    # =====================================================
    async def run(self, query: str) -> AsyncGenerator[Union[AgentEvent, AgentResponse], None]:
        start_time = datetime.now()
        events: List[AgentEvent] = []

        try:
            input_event = AgentInputEvent(
                agent_name=self.name,
                task=query,
                create_time=start_time.isoformat(),
            )
            events.append(input_event)
            await self._publish(input_event)
            yield input_event

            await self.memory.push_messages([UserMessage(content=query)])

            # ---- reminder: user-input 之后 ----
            user_reminder = self.reminder_registry.collect(
                ReminderContext(
                    event=ReminderEvent.USER_INPUT,
                    conversation_id=self.conversation_id,
                    user_input=query,
                )
            )
            if user_reminder:
                await self.memory.push_messages([UserMessage(content=user_reminder)])

            for _turn in range(self.max_runturns):
                # ---- 检查是否需要 compaction ----
                if await self._should_compact():
                    compact_event = await self._do_compact()
                    if compact_event:
                        yield compact_event

                history_messages = await self.memory.pull_messages()

                llm_request = LLMRequest(
                    messages=history_messages,
                    tools=self.tool_manager.get_tool_schemas(),
                )

                llm_request_event = AgentLLMRequestEvent(
                    agent_name=self.name,
                    request=llm_request,
                    create_time=datetime.now().isoformat(),
                )
                events.append(llm_request_event)
                await self._publish(llm_request_event)
                yield llm_request_event

                llm_response: LLMResponse = await self.llm.chat(llm_request)

                llm_response_event = AgentLLMResponseEvent(
                    agent_name=self.name,
                    resonse=llm_response,
                    create_time=datetime.now().isoformat(),
                )
                events.append(llm_response_event)
                await self._publish(llm_response_event)
                yield llm_response_event

                # 将 assistant 消息（可能同时包含文本和 tool_calls）推入记忆
                await self.memory.push_messages([llm_response.response_message])

                # ---- 以 finish_reason 决定是否继续 ----
                finish_reason = llm_response.finish_reason

                if finish_reason == "stop" or finish_reason == "content_filter":
                    # 模型结束回复 → 返回最终响应
                    response = self._build_final_response(query, events, llm_response)
                    await self._publish_agent_response(response)
                    yield response
                    break

                elif finish_reason == "tool_calls" and llm_response.tool_calls:
                    # 有工具调用 → 执行工具，继续循环
                    tool_requests = [
                        ToolRequest(
                            tool_call_id=tc.tool_call_id or str(uuid.uuid4()),
                            tool_call_name=tc.tool_call_name,
                            tool_call_arguments=tc.tool_call_arguments,
                        )
                        for tc in llm_response.tool_calls
                    ]

                    # ---- 权限检查 ----
                    allowed_requests, denied_results = await self._apply_permission(
                        tool_requests
                    )

                    tool_request_event = AgentToolRequestEvent(
                        agent_name=self.name,
                        tool_calls=tool_requests,
                        tool_execution_results=[],
                    )
                    events.append(tool_request_event)
                    await self._publish(tool_request_event)
                    yield tool_request_event

                    # 执行允许的工具
                    executed_results: List[ToolRequestResult] = []
                    if allowed_requests:
                        executed_results = await self.tool_manager.execute_tools(
                            allowed_requests
                        )

                    tool_results = self._merge_results(
                        tool_requests, executed_results, denied_results
                    )
                    tool_request_event.tool_execution_results = tool_results

                    tool_answer_event = AgentToolRequestAnswerEvent(
                        agent_name=self.name,
                        tool_execution_results=tool_results,
                    )
                    events.append(tool_answer_event)
                    await self._publish(tool_answer_event)
                    yield tool_answer_event

                    tool_messages = [
                        ToolMessage(tool_result=r, content=r.content)
                        for r in tool_results
                    ]
                    await self.memory.push_messages(tool_messages)

                    # ---- reminder: tool-result 之后 ----
                    tool_reminder = self.reminder_registry.collect(
                        ReminderContext(
                            event=ReminderEvent.TOOL_RESULT,
                            conversation_id=self.conversation_id,
                            turn_index=_turn + 1,
                            tool_names=[r.tool_call_name for r in tool_results],
                        )
                    )
                    if tool_reminder:
                        await self.memory.push_messages(
                            [UserMessage(content=tool_reminder)]
                        )
                    # 继续下一轮循环

                elif finish_reason == "length":
                    # 上下文超限 → 尝试 compaction
                    logger.warning("finish_reason=length, attempting compaction")
                    compact_event = await self._do_compact()
                    if compact_event:
                        yield compact_event
                    # compaction 后继续循环

                else:
                    # 其他未知情况 → 结束
                    logger.warning(f"Unknown finish_reason: {finish_reason}, stopping")
                    response = self._build_final_response(query, events, llm_response)
                    await self._publish_agent_response(response)
                    yield response
                    break

            else:
                logger.warning(
                    f"达到最大循环次数 {self.max_runturns}，强制返回"
                )
                final_messages = await self.memory.pull_messages()
                last_message = final_messages[-1] if final_messages else None
                if isinstance(last_message, AssistantMessage):
                    final_text = last_message.content or "已达到最大执行轮次"
                else:
                    final_text = "已达到最大执行轮次，但未获得最终回答"

                response = AgentResponse(
                    agent_name=self.name,
                    input_message=[UserMessage(content=query)],
                    tools=list(self.tool_manager._tools.values()),
                    prompt_usage=None,
                    event_list=events,
                    message_list=final_messages,
                    response=final_text,
                    tool_call_sequence=None,
                    tool_call_nums=sum(
                        1 for msg in final_messages
                        if isinstance(msg, ToolMessage)
                    ),
                )
                await self._publish_agent_response(response)
                yield response

        except Exception as e:
            logger.error(f"Error in NormaCoder.run: {e}", exc_info=True)
            error_response = AgentResponse(
                agent_name=self.name,
                input_message=[UserMessage(content=query)],
                tools=list(self.tool_manager._tools.values())
                    if hasattr(self, 'tool_manager') else [],
                prompt_usage=None,
                event_list=events,
                message_list=await self.memory.pull_messages()
                    if hasattr(self, 'memory') else [],
                response=f"发生了错误: {str(e)}",
                tool_call_sequence=None,
                tool_call_nums=0,
            )
            await self._publish_agent_response(error_response)
            yield error_response

    # =====================================================
    # Compaction（上下文压缩）
    # =====================================================
    async def _should_compact(self) -> bool:
        """判断是否需要压缩上下文"""
        if not hasattr(self.llm, 'max_context_tokens'):
            return False
        max_tokens = self.llm.max_context_tokens  # type: ignore
        if max_tokens <= 0:
            return False
        messages = self.memory._messages
        estimated = 0
        if hasattr(self.llm, 'estimate_tokens'):
            estimated = self.llm.estimate_tokens(messages)  # type: ignore
        else:
            # 粗略估计
            total_chars = sum(len(m.content) for m in messages if hasattr(m, 'content') and m.content)
            estimated = int(total_chars / 2.5)
        return estimated > max_tokens * self.compact_threshold

    async def _do_compact(self):
        """执行上下文压缩：让模型总结历史消息，保留关键信息"""
        logger.info("Starting context compaction")
        try:
            messages = self.memory._messages
            # 构建摘要请求
            summary_prompt = (
                "请总结以下对话历史，保留关键信息：用户请求、已完成的操作、"
                "重要发现、当前进度、待办事项。不要丢失任何关键上下文。"
                "\n\n只输出总结文本，不要调用任何工具。"
            )

            # 取系统提示 + 摘要提示 + 历史消息
            system_msg = messages[0] if messages and isinstance(messages[0], SystemMessage) else None
            compact_messages = []
            if system_msg:
                compact_messages.append(system_msg)
            compact_messages.append(UserMessage(content=summary_prompt))

            # 将历史消息压缩为一个字符串
            history_text_parts = []
            for msg in messages[1:]:
                if isinstance(msg, SystemMessage):
                    continue
                if isinstance(msg, UserMessage):
                    history_text_parts.append(f"[User]: {msg.content}")
                elif isinstance(msg, AssistantMessage):
                    if msg.content:
                        history_text_parts.append(f"[Assistant]: {msg.content}")
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            history_text_parts.append(f"[Tool Call: {tc.tool_call_name}]: {json.dumps(tc.tool_call_arguments, ensure_ascii=False)}")
                elif isinstance(msg, ToolMessage):
                    history_text_parts.append(f"[Tool Result: {msg.tool_result.tool_call_name}]: {msg.content[:500]}")

            history_text = "\n".join(history_text_parts)
            compact_messages.append(UserMessage(content=history_text[-8000:]))

            llm_request = LLMRequest(messages=compact_messages)
            llm_response = await self.llm.chat(llm_request)
            summary = llm_response.content or "对话历史已压缩"

            # 重建消息：系统提示 + 压缩标记 + 摘要
            new_messages = [system_msg] if system_msg else []
            new_messages.append(UserMessage(content=f"<compact-boundary>\n以下是之前对话的摘要：\n{summary}\n</compact-boundary>"))

            self.memory._messages = new_messages
            logger.info(f"Compaction complete: {len(messages)} → {len(new_messages)} messages")

        except Exception as e:
            logger.error(f"Compaction failed: {e}", exc_info=True)
        return None

    # =====================================================
    # 辅助方法
    # =====================================================
    def _build_final_response(
        self, query: str, events: List[AgentEvent], llm_response: LLMResponse
    ) -> AgentResponse:
        """构建最终 AgentResponse"""
        final_messages = self.memory._messages
        return AgentResponse(
            agent_name=self.name,
            input_message=[UserMessage(content=query)],
            tools=list(self.tool_manager._tools.values()),
            prompt_usage=None,
            event_list=events,
            message_list=final_messages,
            response=llm_response.response_message.content or "",
            tool_call_sequence=None,
            tool_call_nums=sum(
                1 for msg in final_messages
                if isinstance(msg, ToolMessage)
            ),
        )

    async def _apply_permission(
        self, tool_requests: List[ToolRequest]
    ) -> tuple[List[ToolRequest], dict[str, ToolRequestResult]]:
        """运行权限检查，返回 (允许执行的 requests, 拒绝/失败的 result 映射)"""
        if self.permission_checker is None:
            return list(tool_requests), {}

        allowed: List[ToolRequest] = []
        denied: dict[str, ToolRequestResult] = {}

        for req in tool_requests:
            decision = self.permission_checker.check(req)

            if decision == PermissionDecision.ALLOW:
                allowed.append(req)
            elif decision == PermissionDecision.DENY:
                denied[req.tool_call_id] = self._make_denied_result(
                    req, "permission denied by current mode/config"
                )
            else:  # ASK
                ok = await self._ask_user(req)
                if ok:
                    allowed.append(req)
                else:
                    denied[req.tool_call_id] = self._make_denied_result(
                        req, "user rejected the tool execution"
                    )

        return allowed, denied

    async def _ask_user(self, req: ToolRequest) -> bool:
        """通过消息总线让用户确认，没有总线时默认拒绝"""
        if self.user_input_manager is None:
            logger.warning(
                f"tool '{req.tool_call_name}' requires confirmation but no "
                f"user_input_manager configured -> deny"
            )
            return False
        prompt = (
            f"工具 [{req.tool_call_name}] 需要确认才能执行。\n"
            f"参数: {req.tool_call_arguments}\n"
            f"是否允许?"
        )
        try:
            return await self.user_input_manager.request_confirmation(
                prompt=prompt,
                conversation_id=self.conversation_id,
            )
        except Exception as exc:
            logger.warning(f"user confirmation error: {exc}")
            return False

    @staticmethod
    def _make_denied_result(req: ToolRequest, reason: str) -> ToolRequestResult:
        payload = {"error": reason, "denied": True, "tool": req.tool_call_name}
        return ToolRequestResult(
            request=req,
            result=payload,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=True,
            execution_times=0.0,
        )

    @staticmethod
    def _merge_results(
        tool_requests: List[ToolRequest],
        executed: List[ToolRequestResult],
        denied: dict[str, ToolRequestResult],
    ) -> List[ToolRequestResult]:
        executed_by_id = {r.tool_call_id: r for r in executed}
        merged: List[ToolRequestResult] = []
        for req in tool_requests:
            if req.tool_call_id in denied:
                merged.append(denied[req.tool_call_id])
            elif req.tool_call_id in executed_by_id:
                merged.append(executed_by_id[req.tool_call_id])
            else:
                merged.append(
                    NormaCoder._make_denied_result(req, "no execution result")
                )
        return merged

    async def _publish(self, event: AgentEvent) -> None:
        if self._adapter is not None:
            try:
                await self._adapter.handle_agent_event(event, self.conversation_id)
            except Exception as exc:
                logger.warning(f"messagebus publish error: {exc}")

    async def _publish_agent_response(self, response: AgentResponse) -> None:
        if self.message_bus is None:
            return
        try:
            await self.message_bus.publish(Message(
                msg_type=MessageType.AGENT_RESPONSE,
                payload=response,
                conversation_id=self.conversation_id,
            ))
        except Exception as exc:
            logger.warning(f"messagebus publish error: {exc}")
