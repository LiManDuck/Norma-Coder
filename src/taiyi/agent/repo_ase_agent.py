"""
RepoASEAgent 修正版实现 - 基于初始prompt意图

核心工作流程:
1. Think & Plan: 思考当前context是否足够，规划下一步
2. Execution: 执行工具收集信息
3. Update Context: 更新上下文
4. Compress Context: 按需压缩
5. Generate Final Result: 生成最终答案
"""

import json
import logging
import time
import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator, Union, Literal
from pathlib import Path
from pydantic import BaseModel, Field
from datetime import datetime
import sys

from repo_agent.core.agent_types import (
    AgentResponse,
    BaseAgent,
    AgentEvent,
    AgentInputEvent,
    AgentThinkEvent,
    AgentLLMCallEvent,
    AgentToolRequestEvent
)
from repo_agent.core.types import (
    LLMMessage,
    Tool,
    FunctionTool,
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolMessage,
    ToolCall,
    ToolExecutionResult
)
from repo_agent.core._base_llm import BaseLLM
from repo_agent.tool.repo_sandbox import RepoSandbox
from repo_agent.memory.repo_memory import RepoMemory

logger = logging.getLogger(__name__)
DEFAULT_SYSTEM_PROMPT = """你是一名在repo中执行编码、issue解决等问题的代码开发人员。
你需要通过持续探索代码仓库来构建上下文，直到能够完成任务。"""

# System Prompt模板 - 用于动态构建完整的system prompt
RepoASEPROMPT_TEMPLATE = """
{system_prompt}

## 代码仓库信息
{repo_or_reposandbox_memory}

## 可用的额外工具
{ext_tools}


## Context(目前已收集信息 和 已执行过程 )
<task_context>
{agent_context}
</task_context>


## 当前任务
{repo_task}

"""

class ThinkingStepAnswer(BaseModel):
    """思考与判断结果"""
    thinking: str = Field(description="对于当前的最终任务,目前在context已经进展到哪一步,")
    finish_task: bool = Field(
        description="判断当前任务是否已经完成, "
    )
    next_subtask: Optional[str] = Field(
        description="如果当前未能完成,finish_task 为False, 则说明下一步需要做的任务, 期望得到的输出 ",
        default=None
    )


# 各阶段的Assistant Message
DEFAULT_THINK_AND_PLAN_PROMPT = """


基于上文中的目前进展,  即<task_context>中的内容，思考：
- 对于当前任务目前已经进展到哪一步
- 决定下一步要去做什么,
- 如果历史问题仍未解决，则根据当前的执行历史，来更新这个子问题

**判断**:
- 如果当前已经按照用户要求完整执行 , 当前任务已经完成, 则可以结束对话 , 输出最终的结果 = 'finish_task = Ture'
- 尚未完成, 如代码还未review,则 'finish_task = False' , 思考下一步还需要做什么, 在接下来的步骤中, 你将主要执行这个步骤 并汇总该结果到<task_context>
 记录下来, 输出next_subtask

"""

DEFAULT_EXECUTION_PROMPT = """## 

**当前子任务**: {sub_task}


"""

DEFAULT_CONTEXT_UPDATE_PROMPT = """

{sub_task}

你正在为repo任务维护一个context（即上方的"Agent工作区"），需要记录当前轮次执行的操作内容。

**要求**:
- 无论当前轮次是否成功，都要记录
- 记录对当前任务有帮助的repo路径、repo名称、有用的符号和代码
- 对于错误内容进行简单记录，或对没有太多意义和价值的内容置空
- 对于和当前agent任务高度相关的内容，你需要保存所有关键信息


使用相关的工具将对于当前子任务和最终任务中, 探索和思考的内容记录下来, 
- 要求详略得当, 使用
- 符号 代码等说明清晰明确,返回有用的代码仓源码
- 
"""

DEFAULT_CONTEXT_COMPRESS_PROMPT = """
## 压缩当前任务的整个流程历史,精简 但语义内容不受过多影响


**压缩原则**:
- 保留所有关键信息：文件路径、函数名、源代码,重要发现等
- 对于错误内容进行简单记录或置空, 避免重复调用即可
- 对于和当前的Repo内容高度相关内容, 完整保留
- 删除冗余内容、重复信息

**当前上下文**:
{context}

**请输出压缩后的上下文**（直接输出内容）:
"""

DEFAULT_FINAL_ANSWER_PROMPT = """## 生成最终答案

根据上方"Agent工作区"中构建的上下文，给出完整、准确的答案。
"""


class RepoASEAgentContext(BaseModel):
    """Agent上下文管理器 基于此记录ASEAgent的所有过程"""
    
    static_prompt : str = ""
    context: str = ""  # 核心context，汇总所有轮次的探索结果
    current_round: int = 0
    round_messages: Dict[int, List[LLMMessage]] = Field(default_factory=dict)  # 记录每一轮次的所有的对话历史
    history_messages: List[LLMMessage] = Field(default_factory=list)  # 全局历史消息
    message_visual_windows: int = -1 
    
    def get_system_message_with_context(self) -> SystemMessage:
        
        return SystemMessage(
            content= self.static_prompt + f"/n ## 当前已汇总的信息和执行历史为{self.context}"
        )

    def get_context(self) -> str:
        return self.context
    

    def append_content_context(self, new_content: str) -> str:
        """
        追加内容到context

        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        separator = f"\n\n--- 更新于 {timestamp} ---\n"
        
        if self.context:
            self.context += separator + new_content
        else:
            self.context = new_content
        
        return f"成功追加内容到context，当前context长度: {len(self.context)} 字符"
    
    def update_context(self, new_content: str) -> str:
        """
        使用new_content 来完全替换当前的context
        注意：此tool会重新将当前的上下文内容全部覆盖，确保你新输出的内容记录下所有关键信息
        返回操作结果供LLM知晓
        """
        old_length = len(self.context)
        self.context = new_content
        new_length = len(self.context)
        
        return f"成功更新context，从 {old_length} 字符压缩到 {new_length} 字符"
    
    def add_message(self, message: LLMMessage) -> None:
        """添加消息到历史"""
        self.history_messages.append(message)
        
        # 同时记录到当前轮次
        if self.current_round not in self.round_messages:
            self.round_messages[self.current_round] = []
        self.round_messages[self.current_round].append(message)
    
    def get_context_length(self) -> int:
        """获取context长度"""
        return len(self.context)
    
    def get_history_length(self) -> int:
        """获取历史消息数量"""
        return len(self.history_messages)
    
    def get_context_update_tools(self) -> List[FunctionTool]:
        """提供给agent的用于更新context的tool"""
        return [FunctionTool(func=self.append_content_context)]
    
    def get_context_compress_tool(self) -> List[FunctionTool]:
        """提供给agent的用于压缩context的tool"""
        return [FunctionTool(func=self.update_context)]


class RepoASEAgentResponse(AgentResponse):
    """扩展的Agent响应"""
    round: int = 0
    agent_context_list: List[str] = Field(default_factory=list)


class RepoASEAgent(BaseAgent):
    """
    RepoASE Agent - 基于ASE (Adaptive Search & Exploration) 的代码仓库智能体
    
    根据context，思考当前的context是否有不足内容信息的情况，根据think的内容来进行规划
    """
    
    def __init__(
        self,
        name: str,
        llm: BaseLLM,
        repo_sandbox: RepoSandbox,
        
        # 输出配置
        structured_output: Optional[type[BaseModel]] = None,
        
        # 上下文配置
        context: Optional[RepoASEAgentContext] = None,
        context_compress_max_length: int = 0,  # 0表示不压缩
        history_window_size: int = 0,  # 将最新的k条消息加入到context中
        
        # 工具配置
        ext_tools: Optional[List[Tool]] = None,
        tool_names: Optional[List[str]] = None,
        
        # 提示词配置
        description: Optional[str] = None,
        system_prompt: Optional[str] = None,
        think_and_plan_prompt: Optional[str] = None,
        execution_prompt: Optional[str] = None,
        update_context_prompt: Optional[str] = None,
        compress_context_prompt: Optional[str] = None,
        final_answer_prompt: Optional[str] = None,
        
        # 执行配置
        using_repo_memory: bool = True,
        max_rounds: int = 50,
        max_tool_each_round: int = 30,
        execution_timeout: float = 300.0,
        
        # 日志配置
        logger_path: Optional[str] = None,
    ) -> None:
        super().__init__(
            name=name,
            llm=llm,
            description=description or "代码仓库智能体",
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT
        )
        
        # 核心组件
        self.repo_sandbox = repo_sandbox
        self.structured_output = structured_output
        
        # 上下文
        if context is None:
            self.agent_context = RepoASEAgentContext()
        else:
            self.agent_context = context

        # 对话历史
        self.all_messages: List[LLMMessage] = []
        self.longtrem_messages : List[LLMMessage] = []
        self.current_round_messages :List[LLMMessage] = []
        
        self.context_compress_max_length = context_compress_max_length
        self.history_window_size = history_window_size
        
        # 工具
        self.repo_tools = repo_sandbox.get_sandbox_tools(tool_names)
        self.ext_tools = ext_tools or []
        self.execution_tools = self.repo_tools + self.ext_tools
        
        # 提示词
        self.user_system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.think_and_plan_prompt = think_and_plan_prompt or DEFAULT_THINK_AND_PLAN_PROMPT
        self.execution_prompt = execution_prompt or DEFAULT_EXECUTION_PROMPT
        self.update_context_prompt = update_context_prompt or DEFAULT_CONTEXT_UPDATE_PROMPT
        self.compress_context_prompt = compress_context_prompt or DEFAULT_CONTEXT_COMPRESS_PROMPT
        self.final_answer_prompt = final_answer_prompt or DEFAULT_FINAL_ANSWER_PROMPT
        
        # 执行配置
        self.using_repo_memory = using_repo_memory
        self.max_rounds = max_rounds
        self.max_tool_each_round = max_tool_each_round
        self.execution_timeout = execution_timeout
        
        # 仓库记忆
        self.repo_memories: Dict[str, RepoMemory] = {}
        if using_repo_memory:
            for repo_name in repo_sandbox.repo_names_list:
                memory = RepoMemory.load(repo_name)
                self.repo_memories[repo_name] = memory
        
        # 状态
        self._current_round = 0
        self._context_history: List[str] = []
        self._current_task = ""  # 保存当前任务
        

        self.system_prompt =  self._build_system_prompt()
        
        logger.info(f"RepoASEAgent '{name}' 初始化完成")
        logger.info(f"  - 执行工具: {len(self.execution_tools)} 个")
        logger.info(f"  - 结构化输出: {structured_output.__name__ if structured_output else 'None'}")
        logger.info(f"  - 压缩阈值: {context_compress_max_length} 字符")
        if logger_path:
            logger.info(f"  - 日志路径: {logger_path}")
    
    def _build_system_prompt(self) -> str:
        """
        构建完整的system prompt
        使用RepoASEPROMPT_TEMPLATE，动态填充各部分内容
        """
        # 1. 仓库信息
        repo_info_parts = []
        if self.using_repo_memory and self.repo_memories:
            for repo_name, memory in self.repo_memories.items():
                repo_info_parts.append(f"仓库: {repo_name}")
                # 可以添加更多memory信息
        repo_info = "\n".join(repo_info_parts) if repo_info_parts else "无仓库信息"
        
        # 2. 额外工具说明
        ext_tools_info = ""
        if self.ext_tools:
            tool_names = [tool.name for tool in self.ext_tools]
            ext_tools_info = f"可用的额外工具: {', '.join(tool_names)}"
        else:
            ext_tools_info = "无额外工具"
        
        # 3. 当前任务
        task_info = self._current_task if self._current_task else "（任务将在运行时提供）"
        
        # 4. Agent工作区（context）
        context_info = self.agent_context.context if self.agent_context.context else "（尚未开始探索）"
        
        # 使用模板构建
        full_system_prompt = RepoASEPROMPT_TEMPLATE.format(
            system_prompt=self.user_system_prompt,
            repo_or_reposandbox_memory=repo_info,
            ext_tools=ext_tools_info,
            repo_task=task_info,
            agent_context=context_info
        )
        
        return full_system_prompt

    
    async def _think_and_plan_step(
        self,
        task: str,
        events: List[AgentEvent]
    ) -> AsyncGenerator[Union[AgentEvent, ThinkingStepAnswer], None]:
        """
        思考与规划步骤
        
        判断当前context是否足够回答问题
        如果历史问题仍未解决，根据执行历史更新子问题
        """
        logger.info("=== Step 1: Think & Plan ===")
        start_time = time.time()
        
        # 构建user prompt（不包含context，context在system中）
        prompt = self.think_and_plan_prompt.format(
            current_round=self._current_round,
            max_rounds=self.max_rounds
        )
        
        
      
        
        response = await self.llm.chat(
            messages=messages,
            structured_output=DefaultThinkingAnswer
        )
        
        thinking = response.structued_content
        assert isinstance(thinking, DefaultThinkingAnswer)
        
        duration = time.time() - start_time
        
        # 记录思考结果
        
        if thinking.judgement == '已经思考足够完善':
            reason = f"[Think] ✅ 可以回答: {thinking.thinking}"
        else:
            reason = f"[Think] ➡️ 需要探索: {thinking.next_problem if thinking.next_problem else '(未指定)'}"
        
        think_event = AgentThinkEvent(
            agent_name=self.name,
            reason_content=reason,
            duration_seconds=duration
        )
        yield think_event
        
        yield thinking
    
    async def _execution_step(
        self,
        next_problem: str,
        events: List[AgentEvent]
    ) -> AsyncGenerator[Union[AgentEvent, str], None]:
        """
        执行步骤 
        只使用execution_tools（仓库工具 + 额外工具）
        """
        logger.info("=== Step 2: Execution ===")
        self.agent_logger.log_step_start("Execution")
        start_time = time.time()
        
        prompt = self.execution_prompt.format(next_problem=next_problem)
        
        messages = self._get_messages_for_llm(prompt)
        
        tool_call_count = 0
        execution_results = []
        timeout_reached = False
        iteration = 0

        for iteration in range(self.max_tool_each_round):
            try:
                # 如果模型调用超时，则退出，可能是tool调用过多导致卡死
                response = await asyncio.wait_for(
                    self.llm.chat(
                        messages=messages,
                        tools=self.execution_tools
                    ),
                    timeout=self.execution_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"执行超时 ({self.execution_timeout}秒)")
                self.agent_logger.log_error(f"执行超时 ({self.execution_timeout}秒)")
                timeout_reached = True
                break
            
            llm_event = AgentLLMCallEvent(
                agent_name=self.name,
                duration_seconds=0,
                messages=messages.copy(),
                response=response
            )
            yield llm_event
            
            assistant_msg = AssistantMessage(
                content=response.content if isinstance(response.content, str) else response.content
            )
            messages.append(assistant_msg)
            self.agent_context.add_message(assistant_msg)
            
            # 文本回复说明执行完成
            if isinstance(response.content, str):
                duration = time.time() - start_time
                summary = f"完成执行，共 {tool_call_count} 次工具调用"
                
                # 记录执行摘要
                self.agent_logger.log_execution_summary(summary, duration)
                
                think_event = AgentThinkEvent(
                    agent_name=self.name,
                    reason_content=f"[Execution] {summary}",
                    duration_seconds=duration
                )
                yield think_event
                
                exec_summary = self._generate_execution_summary(
                    execution_results,
                    response.content
                )
                yield exec_summary
                return
            
            # 工具调用
            elif isinstance(response.content, list):
                tool_calls: List[ToolCall] = response.content
                tool_call_count += len(tool_calls)
                
                tool_start = time.time()
                tool_results = []
                for tc in tool_calls:
                    # 记录工具调用
                    self.agent_logger.log_tool_call(tc)
                    
                    result = await self._execute_tool(tc)
                    tool_results.append(result)
                    
                    # 记录工具结果（精简版）
                    self.agent_logger.log_tool_result(result)
                    
                    # 对外输出的event，就不必全部都输出了
                    execution_results.append({
                        "tool": tc.tool_name,
                        "success": not result.is_error,
                        "content": result.content[:100] + "..." if len(result.content) > 100 else result.content
                    })
                
                tool_duration = time.time() - tool_start
                
                tool_event = AgentToolRequestEvent(
                    agent_name=self.name,
                    tool_calls=tool_calls,
                    tool_execution_results=tool_results,
                    duration_seconds=tool_duration
                )
                yield tool_event
                
                # 但是tool message这里必须全部都加入
                tool_msg = ToolMessage(results=tool_results)
                
                # 更新messages
                messages.append(tool_msg)
                self.agent_context.add_message(tool_msg)
                
                continue
            else:
                logger.warning("模型输出异常")
                self.agent_logger.log_error("模型输出异常")
                break
        
        duration = time.time() - start_time
        if timeout_reached: 
            warning = "执行超时" 
        elif iteration == self.max_tool_each_round - 1:
            warning = f"达到最大工具调用次数 ({self.max_tool_each_round})"
        else:
            warning = ""
        
        if warning:
            self.agent_logger.log_execution_summary(warning, duration)
        
        think_event = AgentThinkEvent(
            agent_name=self.name,
            reason_content=f"[Execution] {warning}",
            duration_seconds=duration
        )
        yield think_event
        
        # 当前阶段结束，基于此时的message对问题进行输出回答
        exec_summary = self._generate_execution_summary(execution_results, warning)
        yield exec_summary
    

    
    async def _update_context_step(
        self,
        execution_summary: str,
        events: List[AgentEvent]
    ) -> AsyncGenerator[Union[AgentEvent, bool], None]:
        """
        更新上下文步骤
        
        记录当前轮次执行的操作内容，为全局的最终任务增加信息
        """
        logger.info("=== Step 3: Update Context ===")
        start_time = time.time()
        
        prompt = self.update_context_prompt.format(
            execution_summary=execution_summary
        )
        
        # 获取context更新工具
        context_tools = self.agent_context.get_context_update_tools()
        # 获取当前步骤的prompt 
        
        
        # 当前轮次的current round messages 
        self.current_round_messages.append(AssistantMessage(
            content= 
        ))
        response = await self.llm.chat(
            messages=messages,
            tools=context_tools,
            tool_choice='required'
        )
        
        llm_event = AgentLLMCallEvent(
            agent_name=self.name,
            duration_seconds=0,
            messages=messages.copy(),
            response=response
        )
        yield llm_event
        
        assistant_msg = AssistantMessage(
            content=response.content if isinstance(response.content, str) else response.content
        )
        messages.append(assistant_msg)
        self.agent_context.add_message(assistant_msg)
        
        if isinstance(response.content, list):
            tool_calls: List[ToolCall] = response.content
            
            tool_start = time.time()
            tool_results = []
            for tc in tool_calls:
                # 记录工具调用
                self.agent_logger.log_tool_call(tc)
                
                result = await self._execute_tool(tc)
                tool_results.append(result)
                
                # 记录工具结果
                self.agent_logger.log_tool_result(result)
            
            tool_duration = time.time() - tool_start
            
            tool_event = AgentToolRequestEvent(
                agent_name=self.name,
                tool_calls=tool_calls,
                tool_execution_results=tool_results,
                duration_seconds=tool_duration
            )
            yield tool_event
            
            tool_msg = ToolMessage(results=tool_results)
            messages.append(tool_msg)
            self.agent_context.add_message(tool_msg)
            
            success = any(not r.is_error and r.name == "append_content_context" for r in tool_results)
            
            duration = time.time() - start_time
            status = "✅ 成功" if success else "❌ 失败"
      
            
            think_event = AgentThinkEvent(
                agent_name=self.name,
                reason_content=f"[Update Context] {status}",
                duration_seconds=duration
            )
            yield think_event
            
            yield success
            return
        
        duration = time.time() - start_time
        
        think_event = AgentThinkEvent(
            agent_name=self.name,
            reason_content="[Update Context] ⚠️ 未调用工具",
            duration_seconds=duration
        )
        yield think_event
        yield False
    
    async def _compress_context_step(
        self,
        events: List[AgentEvent]
    ) -> AsyncGenerator[Union[AgentEvent, bool], None]:
        """
        压缩上下文步骤（按需）
        
        对错误内容简单记录或置空，对高度相关内容保存所有关键信息
        """
        if self.context_compress_max_length <= 0:
            yield False
            return
        
        current_length = self.agent_context.get_context_length()
        if current_length <= self.context_compress_max_length:
            yield False
            return
        
        logger.info(f"=== Step 4: Compress Context ===")
        logger.info(f"Context长度 ({current_length}) 超过阈值 ({self.context_compress_max_length})")
        start_time = time.time()
        
        prompt = self.compress_context_prompt.format(
            current_length=current_length,
            max_length=self.context_compress_max_length,
            context=self.agent_context.context
        )
        
        messages = [
            SystemMessage(content="你是一个专业的上下文压缩专家。"),
            UserMessage(content=prompt)
        ]
        
        response = await self.llm.chat(messages=messages)
        
        compressed = response.content if isinstance(response.content, str) else str(response.content)
        
        old_length = current_length
        new_length = len(compressed)
        
        self.agent_context.update_context(compressed)
        self._context_history.append(compressed)
        
        duration = time.time() - start_time
        ratio = (1 - new_length / old_length) * 100 if old_length > 0 else 0
        
        # 记录压缩结果
        
        think_event = AgentThinkEvent(
            agent_name=self.name,
            reason_content=f"[Compress] {old_length} → {new_length} 字符（{ratio:.1f}%）",
            duration_seconds=duration
        )
        yield think_event
        
        yield True
    
    async def _generate_final_result(
        self,
        task: str,
        events: List[AgentEvent]
    ) -> AsyncGenerator[Union[AgentEvent, str], None]:
        """
        生成最终结果
        
        如果有structured_output，使用结构化输出
        否则生成文本答案
        """
        logger.info("=== Generate Final Result ===")
        self.agent_logger.log_step_start("Final Result")
        start_time = time.time()
        
        prompt = self.final_answer_prompt
        
        messages = self._get_messages_for_llm(prompt)
        
        if self.structured_output:
            response = await self.llm.chat(
                messages=messages,
                structured_output=self.structured_output
            )
            
            duration = time.time() - start_time
            
            llm_event = AgentLLMCallEvent(
                agent_name=self.name,
                duration_seconds=duration,
                messages=messages.copy(),
                response=response
            )
            yield llm_event
            
            # 返回结构化内容的JSON字符串
            result = json.dumps(
                response.structued_content.model_dump() if response.structued_content is not None else "",
                ensure_ascii=False,
                indent=2
            )
            
            # 记录最终答案
            self.agent_logger.log_final_answer(result)
            
            think_event = AgentThinkEvent(
                agent_name=self.name,
                reason_content="[Final] ✅ 生成结构化输出",
                duration_seconds=0
            )
            yield think_event
            
            yield result
        
        else:
            response = await self.llm.chat(messages=messages)
            
            duration = time.time() - start_time
            
            llm_event = AgentLLMCallEvent(
                agent_name=self.name,
                duration_seconds=duration,
                messages=messages.copy(),
                response=response
            )
            yield llm_event
            
            final_answer = response.content if isinstance(response.content, str) else str(response.content)
            
            # 记录最终答案
            self.agent_logger.log_final_answer(final_answer)
            
            think_event = AgentThinkEvent(
                agent_name=self.name,
                reason_content="[Final] ✅ 生成文本答案",
                duration_seconds=0
            )
            yield think_event
            
            yield final_answer
    
    async def _execute_tool(self, tool_call: ToolCall) -> ToolExecutionResult:
        """执行工具调用"""
        # 检查所有可用工具
        all_tools = self.execution_tools + self.agent_context.get_context_update_tools() + self.agent_context.get_context_compress_tool()
        
        tool = next((t for t in all_tools if t.name == tool_call.tool_name), None)
        if tool is None:
            return ToolExecutionResult(
                tool_call_id=tool_call.id,
                name=tool_call.tool_name,
                content=f"错误：工具 '{tool_call.tool_name}' 不存在",
                is_error=True
            )
        
        try:
            result = await tool.execute(tool_call)
            return result
        except Exception as e:
            logger.error(f"工具执行错误: {e}", exc_info=True)
            return ToolExecutionResult(
                tool_call_id=tool_call.id,
                name=tool_call.tool_name,
                content=f"工具执行错误: {str(e)}",
                is_error=True
            )
    
    async def run_stream(
        self,
        task: str
    ) -> AsyncGenerator[Union[AgentEvent, RepoASEAgentResponse], None]:
        """
        流式运行Agent
        
        工作流程：
        1. Think & Plan: 判断是否能回答，如果历史问题仍未解决则更新子问题
        2. 如果能 → Generate Final Result
        3. 如果不能 → Execution → Update Context → Compress（可选）→ 回到1
        """
        start_time = time.time()
        events: List[AgentEvent] = []
        
        # 保存当前任务
        self._current_task = task
        
        # 记录任务开始
        
        input_event = AgentInputEvent(
            agent_name=self.name,
            task=task,
            duration_seconds=0
        )
        yield input_event
        events.append(input_event)
        
        self.agent_context.add_message(UserMessage(content=task))
        
        for round_num in range(1, self.max_rounds + 1):
            self._current_round = round_num
            self.agent_context.current_round = round_num
            logger.info(f"\n{'='*60}")
            logger.info(f"Round {round_num}/{self.max_rounds}")
            logger.info(f"{'='*60}")
            
            # 记录轮次开始
            
            try:
                # Step 1: Think & Plan
                thinking = None

                
                async for event in self._think_and_plan_step(task, events):
                    if isinstance(event, ThinkingStepAnswer):
                        thinking = event
                    else:
                        yield event
                        events.append(event)
                
                if thinking is None:
                    raise RuntimeError("Think & Plan 未产生结果")
                
                # 当前任务已经完成
                if thinking.finish_task: 
                    logger.info("✅ 可以回答，生成最终结果")
                    
                    final_answer = None
                    async for event in self._generate_final_result(task, events):
                        if isinstance(event, str):
                            final_answer = event
                        else:
                            yield event
                            events.append(event)
                    
                    if final_answer is None:
                        final_answer = "生成最终答案失败"
                    
                   
                    final_response = RepoASEAgentResponse(
                        agent_name=self.name,
                        response=final_answer,
                        event_list=events,
                        message_list=[],
                        round=round_num,
                        agent_context_list=self._context_history.copy()
                    )
                    yield final_response
                    return
                
                # 如果不能回答，继续探索
                logger.info(f"➡️ 下一步任务: {thinking.next_subtask}")
                
                # Step 2: Execution
                exec_summary = None
                async for event in self._execution_step(thinking.next_subtask or "", events):
                    if isinstance(event, str):
                        exec_summary = event
                    else:
                        yield event
                        events.append(event)
                
                if exec_summary is None:
                    exec_summary = "执行未产生结果"
                
                # Step 3: Update Context
                context_updated = False
                async for event in self._update_context_step(exec_summary, events):
                    if isinstance(event, bool):
                        context_updated = event
                    else:
                        yield event
                        events.append(event)
                
                if not context_updated:
                    logger.warning("⚠️ Context更新失败")
                
                # 记录context历史
                self._context_history.append(self.agent_context.context)
                
                # Step 4: Compress Context（可选）
                async for event in self._compress_context_step(events):
                    if isinstance(event, bool):
                        pass
                    else:
                        yield event
                        events.append(event)
                
                continue
            
            except Exception as e:
                logger.error(f"执行出错: {e}", exc_info=True)
                error_msg = f"执行过程中发生错误: {str(e)}"
                
                # 记录错误
                self.agent_logger.log_error(error_msg)
                self.agent_logger.log_finish(round_num)
                
                final_response = RepoASEAgentResponse(
                    agent_name=self.name,
                    response=error_msg,
                    event_list=events,
                    message_list=[],
                    round=round_num,
                    agent_context_list=self._context_history.copy()
                )
                yield final_response
                return
        
        # 达到最大轮次，强制回答
        warning = f"已达到最大轮次 ({self.max_rounds})，基于当前上下文回答。"
        logger.warning(warning)
        self.agent_logger.log_error(warning)
        
        final_answer = None
        async for event in self._generate_final_result(task, events):
            if isinstance(event, str):
                final_answer = event
            else:
                yield event
                events.append(event)
        
        if final_answer is None:
            final_answer = "无法生成答案"
        
        # 记录执行完成
        self.agent_logger.log_finish(self.max_rounds)
        
        final_response = RepoASEAgentResponse(
            agent_name=self.name,
            response=f"{warning}\n\n{final_answer}",
            event_list=events,
            message_list=[],
            round=self.max_rounds,
            agent_context_list=self._context_history.copy()
        )
        yield final_response
    
    async def run(self, task: str) -> RepoASEAgentResponse:
        """同步运行Agent"""
        final_response = None
        async for message in self.run_stream(task):
            if isinstance(message, RepoASEAgentResponse):
                final_response = message
        
        if final_response is None:
            raise RuntimeError("Agent未产生最终响应")
        
        return final_response
    
    def save_memories(self) -> None:
        """保存所有仓库记忆"""
        if not self.using_repo_memory:
            return
        
        for memory in self.repo_memories.values():
            memory.save_to_disk()
        
        logger.info(f"已保存 {len(self.repo_memories)} 个仓库的记忆")
    
