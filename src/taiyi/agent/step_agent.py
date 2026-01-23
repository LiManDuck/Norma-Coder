"""
StepAgent 实现
一个基于步骤规划的 单Agent，通过多步骤策略执行复杂任务

"""
from typing import List, Sequence, Optional, AsyncGenerator, Union, Dict, Any
from pydantic import BaseModel, Field
from .core.agent_types import (
    AgentResponse,
    BaseAgent,
    AgentEvent,
    AgentInputEvent,
    AgentThinkEvent,
)
from repo_agent.core.types import (
    LLMMessage,
    Tool,
    SystemMessage,
    UserMessage,
    AssistantMessage
)
from repo_agent.core._base_llm import BaseLLM
from repo_agent.agent import FunctionCallAgent
import logging
import time

logger = logging.getLogger(__name__)


class AgentStep(BaseModel):
    """定义 Agent 执行的单个步骤"""
    step_name: str
    step_prompt: str
    step_tools: List[Tool] | None = None
    step_description: str = Field(description='该步骤主要面向的问题类型和决策场景')
    expect_answer_format: str | BaseModel = ""


class StepAgentConfig(BaseModel):
    """StepAgent 的配置"""
    step_space: Sequence[AgentStep]
    first_step_name: str | None = None  # 如果没有指定，则由 LLM 动态生成
    finish_step_name: str


class AgentStepTask(BaseModel):
    """表示一个待执行的步骤任务"""
    step_object: AgentStep
    step_task: str


class AgentStepTaskAnswer(BaseModel):
    """步骤执行后的答案"""
    step_object: AgentStep
    step_task: str
    step_answer: str
    step_response: AgentResponse


class AgentStepEvent(AgentEvent):
    """步骤事件，记录步骤的执行信息"""
    step_name: str
    step_task: str
    step_answer: Optional[str] = None


class StepAgent(BaseAgent):
    """
    基于步骤规划的 Agent
    
    执行策略:
    1. 根据整体任务和历史步骤，决策下一个要执行的步骤。
    2. 使用 FunctionCallAgent 执行该步骤。
    3. 记录步骤结果，继续决策直到到达终止步骤。
    """
    
    def __init__(
        self,
        name: str,   
        llm: BaseLLM, 
        step_agent_config: StepAgentConfig,
        tools: List[Tool],

        max_execution_step: int = 30,   # 最大的step次数
        max_tools_calls_in_each_step: int = 10, # 每个step里执行function call的最大次数
        description: str | None = None, 
        system_prompt: Optional[str] = None,
    ):
        super().__init__(name, llm, description, system_prompt)
        
        self.step_agent_config = step_agent_config
        self._all_tools = tools
        
        # 构建步骤名称到步骤对象的映射
        self._step_map: Dict[str, AgentStep] = {
            step.step_name: step for step in step_agent_config.step_space
        }
        
        # 记录历史步骤的结果
        self.step_history: Dict[str, AgentStepTaskAnswer] = {}
        
        # 记录每个步骤的消息历史
        self.step_history_messages: Dict[str, List[LLMMessage]] = {}
        
        # 全局消息列表，用于跟踪整个任务的对话流程
        self.global_messages: List[LLMMessage] = []
        
        # 所有可供选择的步骤名称列表
        self._all_chooseable_step_names = [step.step_name for step in step_agent_config.step_space]

    def _get_tool_list_by_step(self, step: AgentStep) -> List[Tool]:
        """
        获取指定步骤可用的工具列表。
        如果步骤定义了专用工具，则使用步骤工具；否则使用全局工具。
        """
        if step.step_tools is not None:
            return step.step_tools
        return self._all_tools

    def _build_step_system_prompt(self, step: AgentStep) -> str:
        """
        构建单个步骤的系统提示（system prompt）。
        该提示结合了全局系统提示和步骤特定的提示。
        """
        base_prompt = self.system_prompt or "你是一个乐于助人的 AI 助手。"
        
        step_specific_prompt = f"\n\n## 当前步骤: {step.step_name}\n"
        step_specific_prompt += f"{step.step_prompt}\n"
        
        if step.step_description:
            step_specific_prompt += f"\n**步骤描述**: {step.step_description}\n"
        
        if step.expect_answer_format:
            if isinstance(step.expect_answer_format, str):
                step_specific_prompt += f"\n**期望答案格式**: {step.expect_answer_format}\n"
            else:
                # 对于 Pydantic 模型，提示 LLM 遵循 schema
                step_specific_prompt += f"\n**期望答案格式**: 请根据定义的模式（schema）构建你的响应。\n"
        
        return base_prompt + step_specific_prompt

    def _build_step_decision_prompt_messages(self, overall_task: str) -> List[LLMMessage]:
        """
        构建用于步骤决策的 prompt 消息列表。
        其中包含系统提示、当前已完成步骤的历史记录、可用步骤等信息。
        """
        # 构建系统提示
        system_content = self.system_prompt or "你是一个乐于助人的 AI 助手，能够将复杂任务分解为多个步骤。"
        system_content += "\n\n## 你的任务\n"
        system_content += "你需要根据整体任务和已完成步骤的历史记录，来决定下一步要执行的步骤。\n"
        
        # 添加可用步骤信息
        system_content += "\n## 可用步骤\n"
        for step in self.step_agent_config.step_space:
            system_content += f"- **{step.step_name}**: {step.step_description}\n"
        
        # 添加历史步骤信息
        if self.step_history:
            system_content += "\n## 已完成步骤\n"
            for step_name, answer in self.step_history.items():
                system_content += f"- **{step_name}**:\n"
                system_content += f"  - 任务: {answer.step_task}\n"
                # 限制答案长度，避免 prompt 过长
                system_content += f"  - 答案: {answer.step_answer[:200]}...\n"
        
        messages = [
            SystemMessage(content=system_content),
            UserMessage(content=f"整体任务: {overall_task}\n\n请决定下一步要执行的步骤，并为该步骤提供一个具体的任务描述。")
        ]
        
        return messages

    async def _decide_next_step_using_llm(self, overall_task: str) -> AgentStepTask:
        """
        使用 LLM 决策下一个要执行的步骤。
        要求模型以结构化的格式输出决策结果。
        """
        
        # 创建用于下一步决策的结构化输出模型
        class NextStepDecision(BaseModel):
            thinking: str = Field(description="你选择此步骤的思考过程")
            next_step: str = Field(description=f"下一步要执行的步骤名称。必须是以下之一: {', '.join(self._all_chooseable_step_names)}")
            next_step_task: str = Field(description="为所选步骤提供的具体任务描述")
        
        messages = self._build_step_decision_prompt_messages(overall_task)
        
        llm_response = await self.llm.chat(
            messages=messages, 
            structured_output=NextStepDecision
        )
        
        assert isinstance(llm_response.structued_content, NextStepDecision), "LLM 未返回预期的结构化输出"
        
        decision = llm_response.structued_content
        
        # 验证 LLM 返回的步骤名称是否有效
        if decision.next_step not in self._step_map:
            raise ValueError(f"无效的步骤名称: {decision.next_step}。可用步骤: {self._all_chooseable_step_names}")
        
        logger.info(f"LLM决策: 下一步执行 '{decision.next_step}', 原因: {decision.thinking}")
        
        step_object = self._step_map[decision.next_step]
        
        return AgentStepTask(
            step_object=step_object,
            step_task=decision.next_step_task
        )

    async def get_current_step(self, overall_task: str) -> AgentStepTask:
        """
        获取当前应该执行的步骤。
        如果配置了 `first_step_name`，则第一次直接返回该步骤；否则使用 LLM 进行决策。
        """
        # 如果是第一步且配置了起始步骤名称
        if not self.step_history and self.step_agent_config.first_step_name:
            first_step = self._step_map.get(self.step_agent_config.first_step_name)
            if first_step:
                return AgentStepTask(
                    step_object=first_step,
                    step_task=overall_task # 初始任务即为第一步的任务
                )
        
        # 其他情况（非第一步，或未配置起始步骤）均使用 LLM 决策
        return await self._decide_next_step_using_llm(overall_task)

    async def _execute_single_step(self, current_step: AgentStepTask) -> AgentStepTaskAnswer:
        """
        使用 FunctionCallAgent 执行单个步骤。
        
        Args:
            current_step: 当前要执行的步骤任务。
            
        Returns:
            步骤执行后的结果。
        """
        step = current_step.step_object
        
        # 获取该步骤可用的工具
        step_tools = self._get_tool_list_by_step(step)
        
        # 构建该步骤的 system prompt
        step_system_prompt = self._build_step_system_prompt(step)
        
        # 创建一个临时的 FunctionCallAgent 来执行此步骤
        step_agent = FunctionCallAgent(
            name=f"{self.name}_step_{step.step_name}",
            llm=self.llm,
            tools=step_tools,
            system_prompt=step_system_prompt,
            enable_tool_calling=True,
            default_tool_choice="auto",
            max_tool_iterations=5
        )
        
        # 执行步骤任务
        logger.info(f"开始执行步骤: {step.step_name}, 任务: {current_step.step_task}")
        step_response = await step_agent.run(current_step.step_task)
        
        # 保存该步骤内部的消息历史
        self.step_history_messages[step.step_name] = step_response.message_list
        
        # 封装步骤答案
        answer = AgentStepTaskAnswer(
            step_object=step,
            step_task=current_step.step_task,
            step_answer=step_response.response or "",
            step_response=step_response
        )
        
        logger.info(f"步骤 {step.step_name} 执行完成, 答案: {answer.step_answer[:100]}...")
        
        return answer

    async def run(self, task: str) -> AgentResponse:
        """
        运行 Agent 并返回最终的聚合响应。
        """
        final_response = None
        async for message in self.run_stream(task):
            if isinstance(message, AgentResponse):
                final_response = message
        if final_response is None:
            raise RuntimeError("Agent 数据流结束，但未生成最终的 AgentResponse。")
        return final_response

    async def run_stream(self, task: str) -> AsyncGenerator[Union[AgentEvent, AgentResponse], None]:
        """
        以流式方式执行 Agent 的完整流程。
        
        执行流程:
        1. 发出输入事件。
        2. 循环执行：决策下一步 -> 执行步骤 -> 记录结果。
        3. 直到到达终止步骤，发出最终响应并结束。
        """
        start_time = time.time()
        events: List[AgentEvent] = []
        
        # 1. 发出输入事件
        input_event = AgentInputEvent(
            agent_name=self.name,
            task=task,
            duration_seconds=time.time() - start_time
        )
        yield input_event
        events.append(input_event)
        
        # 初始化全局消息列表
        self.global_messages = [
            SystemMessage(content=self.system_prompt or "你是一个乐于助人的 AI 助手。"),
            UserMessage(content=task)
        ]
        
        max_steps = 20  # 设置最大步骤数，防止无限循环
        step_count = 0
        
        while step_count < max_steps:
            step_count += 1
            
            # 2. 决策下一步
            think_start = time.time()
            try:
                current_step_task = await self.get_current_step(task)
            except Exception as e:
                error_msg = f"步骤决策失败: {str(e)}"
                logger.error(error_msg)
                
                think_event = AgentThinkEvent(
                    agent_name=self.name,
                    reason_content=error_msg,
                    duration_seconds=time.time() - think_start
                )
                yield think_event
                events.append(think_event)
                break
            
            step_name = current_step_task.step_object.step_name
            
            think_event = AgentThinkEvent(
                agent_name=self.name,
                reason_content=f"决策执行步骤: {step_name}, 任务: {current_step_task.step_task}",
                duration_seconds=time.time() - think_start
            )
            yield think_event
            events.append(think_event)
            
            # 3. 执行步骤
            step_exec_start = time.time()
            step_answer = await self._execute_single_step(current_step_task)
            step_exec_duration = time.time() - step_exec_start
            
            # 记录步骤结果到历史记录
            self.step_history[step_name] = step_answer
            
            # 发出步骤执行事件
            step_event = AgentStepEvent(
                agent_name=self.name,
                step_name=step_name,
                step_task=current_step_task.step_task,
                step_answer=step_answer.step_answer,
                duration_seconds=step_exec_duration
            )
            yield step_event
            events.append(step_event)
            
            # 将步骤结果添加到全局消息历史中
            self.global_messages.append(
                AssistantMessage(content=f"[步骤: {step_name}]\n{step_answer.step_answer}")
            )
            
            # 4. 检查是否到达终止步骤
            if step_name == self.step_agent_config.finish_step_name:
                logger.info(f"到达终止步骤: {step_name}，任务完成。")
                
                final_response = AgentResponse(
                    agent_name=self.name,
                    response=step_answer.step_answer,
                    event_list=events,
                    message_list=self.global_messages
                )
                yield final_response
                return
        
        # 如果循环结束仍未到达终止步骤（例如达到最大步骤数）
        warning_msg = f"已达到最大步骤数 {max_steps}，强制终止任务。"
        logger.warning(warning_msg)
        
        self.global_messages.append(AssistantMessage(content=warning_msg))
        
        final_response = AgentResponse(
            agent_name=self.name,
            response=warning_msg,
            event_list=events,
            message_list=self.global_messages
        )
        yield final_response
