"""
Skill 工具

LLM 通过 ``Skill(name=..., args=...)`` 调用一个已加载的 skill。
内部通过 ``agent_factory`` 创建一个隔离的子 agent 来执行 skill 正文内容，
和 Claude Code 的 SkillTool fork 行为一致。

实现要点
--------
- skill 目录由 ``SkillRegistry.from_default_dirs`` 加载
- 工具描述会列出当前可用 skill，便于 LLM 选择
- 不进行 prompt-based 权限检查（DANGEROUS_TOOLS 已经把 Skill 列入 ASK 默认）
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from norma.core.agent_types import AgentResponse, BaseAgent
from norma.core.tool_types import (
    ParametersSchema,
    Tool,
    ToolRequest,
    ToolRequestError,
    ToolRequestResult,
    ToolSchema,
)
from norma.skill import SkillRegistry

logger = logging.getLogger(__name__)


AgentFactory = Callable[..., BaseAgent]


class SkillTool(Tool):
    """调用一个已加载的 skill"""

    def __init__(
        self,
        registry: SkillRegistry,
        agent_factory: AgentFactory,
    ) -> None:
        self.registry = registry
        self.agent_factory = agent_factory

    @property
    def name(self) -> str:
        return "Skill"

    @property
    def description(self) -> str:
        skills = self.registry.all()
        if not skills:
            return (
                "调用一个 skill 完成具体任务。当前没有可用的 skill；"
                "用户可以在 ~/.norma/skills/ 或 <cwd>/.norma/skills/ 添加 markdown 文件来定义。"
            )
        lines = ["调用一个已注册的 skill。可用 skill 列表："]
        for s in skills:
            desc = s.description or "(no description)"
            alias_note = (
                f" aliases={s.aliases}" if s.aliases else ""
            )
            lines.append(f"- {s.name}: {desc}{alias_note}")
        lines.append(
            "\n参数:\n"
            "- name: skill 名称（必填）\n"
            "- args: 传给 skill 的输入文本（可选，会作为 'Input' 段拼接到 prompt）\n"
            "返回: 子 agent 的最终响应文本。"
        )
        return "\n".join(lines)

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "name": {
                        "type": "string",
                        "description": "skill 名称（必须在已加载的列表中）",
                    },
                    "args": {
                        "type": "string",
                        "description": "传递给 skill 的可选输入文本",
                    },
                },
                required=["name"],
            ),
            strict=False,
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        started = time.time()

        try:
            if isinstance(tool_request.tool_call_arguments, str):
                args = self.parse_string_arguments(tool_request.tool_call_arguments)
            else:
                args = dict(tool_request.tool_call_arguments or {})
        except ToolRequestError as exc:
            return self._error(tool_request, str(exc), started)

        skill_name = (args.get("name") or "").strip()
        if not skill_name:
            return self._error(tool_request, "missing required parameter: name", started)

        skill = self.registry.get(skill_name)
        if skill is None:
            available = ", ".join(self.registry.names())
            return self._error(
                tool_request,
                f"skill '{skill_name}' not found. available: [{available}]",
                started,
            )

        skill_args = args.get("args")
        prompt = skill.render_prompt(args=skill_args if isinstance(skill_args, str) else None)

        try:
            agent = self._make_agent(skill_name, allowed_tools=skill.allowed_tools or None)
            response = await self._consume(agent, prompt)
        except Exception as exc:
            logger.exception(f"skill {skill_name} failed")
            return self._error(tool_request, str(exc), started)

        payload = {
            "skill": skill.name,
            "response": response.response or "",
            "tool_call_nums": response.tool_call_nums,
        }
        return ToolRequestResult(
            request=tool_request,
            result=payload,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=False,
            execution_times=time.time() - started,
        )

    # ------------------------------------------------
    def _make_agent(
        self,
        skill_name: str,
        allowed_tools: Optional[List[str]] = None,
    ) -> BaseAgent:
        """构造子 agent，透传 ``allowed_tools`` 给工厂以收窄工具集（skill 沙箱）。

        自定义工厂可能不接受 ``allowed_tools``/``name`` 关键字，逐级降级：
        (name+allowed_tools) -> (name) -> ()。
        """
        try:
            return self.agent_factory(
                name=f"skill[{skill_name}]", allowed_tools=allowed_tools
            )
        except TypeError:
            pass
        try:
            return self.agent_factory(name=f"skill[{skill_name}]")
        except TypeError:
            return self.agent_factory()

    @staticmethod
    async def _consume(agent: BaseAgent, prompt: str) -> AgentResponse:
        last: Optional[AgentResponse] = None
        async for ev in agent.run(prompt):
            if isinstance(ev, AgentResponse):
                last = ev
                break
        if last is None:
            raise RuntimeError("skill subagent did not yield AgentResponse")
        return last

    @staticmethod
    def _error(req: ToolRequest, msg: str, started: float) -> ToolRequestResult:
        payload = {"error": msg, "tool": req.tool_call_name}
        return ToolRequestResult(
            request=req,
            result=payload,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=True,
            execution_times=time.time() - started,
        )
