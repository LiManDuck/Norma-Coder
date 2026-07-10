"""
Agent (subagent) 工具

提供给 LLM 的能力：在主 agent 上下文中再次调度一个子 agent 完成独立子任务。

参数
----
- prompt: str          子任务描述（必填）
- session_id: str|None 复用已有子会话；None 时自动创建新会话
- run_background: bool 默认 false；true 时立即返回 task id，结果异步落到内部表
- description: str|None 简短描述（用于日志/UI）

返回
----
非后台模式：返回子 agent 最终响应文本与 session_id；
后台模式 ：返回 ``{status: "running", task_id, session_id}``，调用者可后续通过
            ``session_id`` 重新调用该工具（prompt 留空）来查询进度。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from norma.core.agent_types import (
    AgentEvent,
    AgentResponse,
    BaseAgent,
)
from norma.core.tool_types import (
    ParametersSchema,
    Tool,
    ToolRequest,
    ToolRequestError,
    ToolRequestResult,
    ToolSchema,
)

logger = logging.getLogger(__name__)


# --- Subagent 工厂 -----------------------------------------------------------

#: ``AgentFactory`` 接受 ``(name, system_prompt_extra)`` 返回 ``BaseAgent``。
#: 由 cli 在创建工具时用 lambda 注入，避免循环依赖。
AgentFactory = Callable[..., BaseAgent]


@dataclass
class _SubagentSession:
    session_id: str
    agent: BaseAgent
    last_response: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    background_task: Optional[asyncio.Task] = None
    background_status: str = "idle"   # idle / running / done / error
    background_result: Optional[Dict[str, Any]] = None


class AgentTool(Tool):
    """
    Agent (subagent) 工具
    """

    @property
    def name(self) -> str:
        return "Agent"

    @property
    def description(self) -> str:
        return (
            "调度一个子 Agent 来完成独立子任务，可用于并行/隔离上下文的研究、调研、长任务。\n"
            "参数:\n"
            "- prompt: 子任务描述（必填）\n"
            "- session_id: 可选，传入则复用同名子会话，便于多轮对话；不传则自动创建新会话\n"
            "- run_background: 可选，true 时立即返回 task_id，结果异步生成；默认 false\n"
            "- description: 可选，简短描述用于日志显示\n\n"
            "返回:\n"
            "- 非后台模式: 子 agent 最终响应文本 + session_id\n"
            "- 后台模式: {status: running, task_id, session_id}，可重复调用并以同一 session_id 查询进度"
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "prompt": {
                        "type": "string",
                        "description": "子任务描述。后台查询时可留空字符串",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "可选，复用已有子会话",
                    },
                    "run_background": {
                        "type": "boolean",
                        "description": "可选，true 时异步执行并立即返回",
                    },
                    "description": {
                        "type": "string",
                        "description": "可选，简短描述",
                    },
                },
                required=["prompt"],
                additionalProperties=False,
            ),
            strict=False,
        )

    def __init__(
        self,
        agent_factory: AgentFactory,
        max_sessions: int = 32,
    ):
        """
        Args:
            agent_factory: 用于创建 BaseAgent 实例的工厂；签名建议 ``(name=...) -> BaseAgent``
            max_sessions:  保留的最大会话数（超出按 LRU 清理）
        """
        self.agent_factory = agent_factory
        self.max_sessions = max_sessions
        self._sessions: Dict[str, _SubagentSession] = {}
        self._lock = asyncio.Lock()

    # -------------------------- 公共 API --------------------------

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        start = time.time()

        # 解析参数
        try:
            if isinstance(tool_request.tool_call_arguments, str):
                args = self.parse_string_arguments(tool_request.tool_call_arguments)
            else:
                args = dict(tool_request.tool_call_arguments)
        except ToolRequestError as e:
            return self._error_result(tool_request, str(e), start)

        prompt = (args.get("prompt") or "").strip()
        session_id = args.get("session_id") or None
        run_background = bool(args.get("run_background", False))
        description = args.get("description")

        # 1) 仅查询某 session 的后台进度
        if not prompt and session_id:
            return self._build_status_result(tool_request, session_id, start)

        if not prompt:
            return self._error_result(
                tool_request, "missing required parameter: prompt", start
            )

        # 2) 准备 session
        async with self._lock:
            session = self._get_or_create_session(session_id, description)

        # 3) 执行
        if run_background:
            return await self._run_background(tool_request, session, prompt, start)
        return await self._run_foreground(tool_request, session, prompt, start)

    # -------------------------- 内部方法 --------------------------

    def _get_or_create_session(
        self, session_id: Optional[str], description: Optional[str]
    ) -> _SubagentSession:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]

        new_id = session_id or f"agent-{uuid.uuid4().hex[:8]}"
        agent_name = description or f"sub-agent[{new_id}]"
        try:
            agent = self.agent_factory(name=agent_name)
        except TypeError:
            # 工厂可能不支持 name 参数
            agent = self.agent_factory()

        session = _SubagentSession(session_id=new_id, agent=agent)
        self._sessions[new_id] = session
        self._gc_sessions()
        return session

    def _gc_sessions(self) -> None:
        if len(self._sessions) <= self.max_sessions:
            return
        # 删除最早的 idle/done 会话
        for sid in list(self._sessions.keys()):
            if len(self._sessions) <= self.max_sessions:
                break
            sess = self._sessions[sid]
            if sess.background_status in ("idle", "done", "error"):
                self._sessions.pop(sid, None)

    async def _consume_agent(
        self, agent: BaseAgent, prompt: str
    ) -> AgentResponse:
        last: Optional[AgentResponse] = None
        # BaseAgent.run 是异步生成器
        async for event in agent.run(prompt):
            if isinstance(event, AgentResponse):
                last = event
                break
        if last is None:
            raise RuntimeError("subagent did not yield AgentResponse")
        return last

    async def _run_foreground(
        self,
        tool_request: ToolRequest,
        session: _SubagentSession,
        prompt: str,
        start: float,
    ) -> ToolRequestResult:
        # 防止与同 session 的后台任务并发跑同一子 agent：两个 agent.run() 会交错
        # 篡改子 agent 的 memory / 事件流。后台任务仍运行时拒绝前台执行，引导
        # 调用方用「空 prompt + session_id」查询进度（与 _run_background 的守卫一致）。
        if (
            session.background_task is not None
            and not session.background_task.done()
        ):
            payload = {
                "status": "running",
                "session_id": session.session_id,
                "message": (
                    "a background task is still running on this session; "
                    "query it with empty prompt + session_id instead of "
                    "starting a foreground run"
                ),
            }
            return ToolRequestResult(
                request=tool_request,
                result=payload,
                content=json.dumps(payload, ensure_ascii=False),
                is_error=False,
                execution_times=time.time() - start,
            )
        try:
            response = await self._consume_agent(session.agent, prompt)
            session.last_response = response.response or ""
            session.history.append({"prompt": prompt, "response": session.last_response})
            session.background_status = "done"
            payload = {
                "status": "done",
                "session_id": session.session_id,
                "response": session.last_response,
                "tool_call_nums": response.tool_call_nums,
            }
            return ToolRequestResult(
                request=tool_request,
                result=payload,
                content=json.dumps(payload, ensure_ascii=False),
                is_error=False,
                execution_times=time.time() - start,
            )
        except Exception as exc:
            logger.error(f"sub agent error: {exc}", exc_info=True)
            session.background_status = "error"
            return self._error_result(tool_request, str(exc), start)

    async def _run_background(
        self,
        tool_request: ToolRequest,
        session: _SubagentSession,
        prompt: str,
        start: float,
    ) -> ToolRequestResult:
        if (
            session.background_task is not None
            and not session.background_task.done()
        ):
            payload = {
                "status": "running",
                "task_id": session.session_id,
                "session_id": session.session_id,
                "message": "previous background task still running, please wait",
            }
            return ToolRequestResult(
                request=tool_request,
                result=payload,
                content=json.dumps(payload, ensure_ascii=False),
                is_error=False,
                execution_times=time.time() - start,
            )

        async def _job():
            try:
                response = await self._consume_agent(session.agent, prompt)
                session.last_response = response.response or ""
                session.history.append(
                    {"prompt": prompt, "response": session.last_response}
                )
                session.background_status = "done"
                session.background_result = {
                    "status": "done",
                    "response": session.last_response,
                }
            except Exception as exc:
                logger.error(f"background sub agent error: {exc}", exc_info=True)
                session.background_status = "error"
                session.background_result = {"status": "error", "error": str(exc)}

        session.background_status = "running"
        session.background_result = None
        session.background_task = asyncio.create_task(_job())

        payload = {
            "status": "running",
            "task_id": session.session_id,
            "session_id": session.session_id,
            "message": "subagent dispatched in background; query later with same session_id",
        }
        return ToolRequestResult(
            request=tool_request,
            result=payload,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=False,
            execution_times=time.time() - start,
        )

    def _build_status_result(
        self,
        tool_request: ToolRequest,
        session_id: str,
        start: float,
    ) -> ToolRequestResult:
        session = self._sessions.get(session_id)
        if session is None:
            return self._error_result(
                tool_request, f"unknown session_id: {session_id}", start
            )
        payload: Dict[str, Any] = {
            "status": session.background_status,
            "session_id": session_id,
        }
        if session.background_result is not None:
            payload.update(session.background_result)
        elif session.last_response is not None:
            payload["response"] = session.last_response
        return ToolRequestResult(
            request=tool_request,
            result=payload,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=False,
            execution_times=time.time() - start,
        )

    @staticmethod
    def _error_result(
        tool_request: ToolRequest, msg: str, start: float
    ) -> ToolRequestResult:
        payload = {"error": msg}
        return ToolRequestResult(
            request=tool_request,
            result=payload,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=True,
            execution_times=time.time() - start,
        )
