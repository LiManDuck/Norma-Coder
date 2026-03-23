from typing import (
     Optional,
     AsyncGenerator,
     List,Union
)
import json
import uuid
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from norma.core.tool_types import (
    ToolRequest,
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
    TodoWriteTool,
    BashTool
)


logger = logging.getLogger(__name__)


class NormaCoder(BaseAgent):
    """
    NormaCoder - 基于LLM的代码助手Agent

    集成了工具管理和内存系统，能够:
    - 执行各种文件操作工具
    - 管理对话记忆和上下文
    - 流式输出响应和事件
    """


    @property
    def name(self):
        return 'norma_coder'
    def __init__(self,
       
        llm: BaseLLM,
        cwd: str| Path  ,
        allowd_dirs: list[str| Path] = [],
        name: str = 'NormaCoder',
        tools: Optional[List[Tool]] = None,
        delta_instructions: Optional[str] = None,
        memory_tool_message_limit: int = 10,
        max_runturns : int = 100,
    ):
        """
        初始化TaiyiCoder

        Args:
            name: Agent名称
            llm: LLM实例
            tools: 工具列表
            memory_tool_message_limit: 内存中保存的ToolMessage数量限制
        """
        self.llm = llm
        self.max_runturns = max_runturns 
        # 初始化工具管理系统
        default_tools = [
            ReadTool(),
            LsTool( cwd = cwd),
            GlobTool(cwd = cwd),
            GrepTool(),
            EditTool(),
            WriteTool(),
            TodoWriteTool(),
            BashTool(cwd = cwd)
        ]

        all_tools = default_tools + (tools or [])
    
        self.tool_manager = NormaArtifact(tools=all_tools)

        # 导入消息记忆
       


        # 设置默认系统提示词

        self.system_prompt = SystemPromptService.get_claude_code_system_prompt(cwd= str(cwd) )



        messages = [SystemMessage(content=self.system_prompt)] 

        self.memory = AgentMemory(
            message_list=messages,
            save_toolmessage_num=memory_tool_message_limit
        )





    async def run(self, query: str) -> AsyncGenerator[Union[AgentEvent, AgentResponse], None]:
        """
        流式运行Agent - 修复版
        
        主要修复：
        1. 工具调用后继续循环，而不是注释掉
        2. 添加 AssistantMessage 到 memory
        3. 正确的循环退出条件
        4. 完善错误处理
        """
        start_time = datetime.now()
        events = []
        
        try:
            # ========================================
            # 1. 创建输入事件
            # ========================================
            input_event = AgentInputEvent(
                agent_name=self.name,
                task=query,
                create_time=start_time.isoformat()
            )
            events.append(input_event)
            yield input_event
            
            # ========================================
            # 2. 添加用户消息到 memory
            # ========================================
            await self.memory.push_messages([UserMessage(content=query)])
            
            # ========================================
            # 3. 主循环 - Agent-Tool 交互
            # ========================================
            for turn in range(self.max_runturns):
                
                # 3.1 获取消息历史
                history_messages = await self.memory.pull_messages()
                
                # 3.2 创建 LLM 请求
                llm_request = LLMRequest(
                    messages=history_messages,
                    tools=self.tool_manager.get_tool_schemas()
                )
                
                # 3.3 发送请求事件
                llm_request_event = AgentLLMRequestEvent(
                    agent_name=self.name,
                    request=llm_request,
                    create_time=datetime.now().isoformat()
                )
                events.append(llm_request_event)
                yield llm_request_event
                
                # 3.4 调用 LLM
                llm_response: LLMResponse = await self.llm.chat(llm_request)
                
                # 3.5 发送响应事件
                llm_response_event = AgentLLMResponseEvent(
                    agent_name=self.name,
                    resonse=llm_response,
                    create_time=datetime.now().isoformat()
                )
                events.append(llm_response_event)
                yield llm_response_event
                
                # 3.6 将 LLM 的响应消息保存到 memory
                # 🔥 关键修复：必须保存 AssistantMessage！
                await self.memory.push_messages([llm_response.response_message])
                
                # ========================================
                # 4. 判断是否需要工具调用
                # ========================================
                if llm_response.tool_calls:
                    # ----------------------------------------
                    # 4.1 有工具调用 - 执行工具
                    # ----------------------------------------
                    
                    # 构建工具请求
                    tool_requests = [
                        ToolRequest(
                            tool_call_id=tool_call.tool_call_id or str(uuid.uuid4()),
                            tool_call_name=tool_call.tool_call_name,
                            tool_call_arguments=tool_call.tool_call_arguments
                        )
                        for tool_call in llm_response.tool_calls
                    ]
                    
                    # 发送工具请求事件
                    tool_request_event = AgentToolRequestEvent(
                        agent_name=self.name,
                        tool_calls=tool_requests,
                        tool_execution_results=[]
                    )
                    events.append(tool_request_event)
                    yield tool_request_event
                    
                    # 执行工具
                    tool_results = await self.tool_manager.execute_tools(tool_requests)
                    
                    # 更新事件
                    tool_request_event.tool_execution_results = tool_results
                    
                    # 发送工具结果事件
                    tool_answer_event = AgentToolRequestAnswerEvent(
                        agent_name=self.name,
                        tool_execution_results=tool_results
                    )
                    events.append(tool_answer_event)
                    yield tool_answer_event
                    
                    # 将工具结果保存到 memory
                    tool_messages = [
                        ToolMessage(
                            tool_result=result,
                            content=result.content
                        )
                        for result in tool_results
                    ]
                    await self.memory.push_messages(tool_messages)
                    
                    # 🔥 关键：继续循环，让 LLM 基于工具结果生成最终回答
                    # 不要在这里 break，让循环继续
                    
                else:
                    # ----------------------------------------
                    # 4.2 没有工具调用 - 生成最终响应
                    # ----------------------------------------
                    
                    # 获取最终消息列表
                    final_messages = await self.memory.pull_messages()
                    
                    # 构建最终响应
                    response = AgentResponse(
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
                        )
                    )
                    yield response
                    
                    # 🔥 关键：找到最终答案，退出循环
                    break
            
            else:
                # ========================================
                # 5. 循环次数耗尽 - 强制返回
                # ========================================
                logger.warning(f"达到最大循环次数 {self.max_runturns}，强制返回")
                
                final_messages = await self.memory.pull_messages()
                last_message = final_messages[-1] if final_messages else None
                
                # 尝试从最后一条消息提取内容
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
                    )
                )
                yield response
        
        except Exception as e:
            # ========================================
            # 6. 错误处理
            # ========================================
            logger.error(f"Error in NormaCoder.run: {e}", exc_info=True)
            
            error_response = AgentResponse(
                agent_name=self.name,
                input_message=[UserMessage(content=query)],
                tools=list(self.tool_manager._tools.values()) if hasattr(self, 'tool_manager') else [],
                prompt_usage=None,
                event_list=events,
                message_list=await self.memory.pull_messages() if hasattr(self, 'memory') else [],
                response=f"发生了错误: {str(e)}",
                tool_call_sequence=None,
                tool_call_nums=0
            )
            yield error_response
