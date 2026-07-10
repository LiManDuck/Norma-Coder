from typing import Any, AsyncGenerator, Mapping, Sequence, List, Optional,Union
from dataclasses import dataclass

from pydantic import BaseModel, Field
from abc import ABC, abstractmethod
from datetime import datetime
from norma.core.llm_types import (
    LLMMessage, AssistantMessage , LLMResponse, ToolMessage,BaseLLM,
    LLMRequest
)


from norma.core.tool_types import (
    ToolRequest, ToolRequestResult,
    Tool
)

class AgentEvent(BaseModel):
    agent_name: str
    create_time: str = Field(default_factory=lambda: datetime.now().isoformat())
    duration_seconds: float = 0.0

class AgentToolRequestEvent(AgentEvent):
    tool_calls: List[ToolRequest]
    tool_execution_results: List[ToolRequestResult]

class AgentToolRequestAnswerEvent(AgentEvent):
    
    tool_execution_results: List[ToolRequestResult]



class AgentResponseEvent(AgentEvent):

    response_message : AssistantMessage

    @property
    def response_content(self):
        return self.response_message.content





class AgentThinkEvent(AgentEvent):
    reason_content: str


class AgentTextDeltaEvent(AgentEvent):
    """流式文本增量：assistant 正在输出的文本片段"""
    delta: str


class AgentThinkDeltaEvent(AgentEvent):
    """流式推理增量：模型思考过程的增量片段"""
    delta: str

class AgentLLMRequestEvent(AgentEvent):
    request: LLMRequest


class AgentLLMResponseEvent(AgentEvent):
    response: LLMResponse


class AgentInputEvent(AgentEvent):
    task: str




@dataclass
class AgentResponse:

    agent_name: str
    input_message : Sequence[LLMMessage]
    tools: List[Tool] | None
    prompt_usage: None 

    event_list: Sequence[AgentEvent]
    message_list: List[LLMMessage]
    response: str | None = None
    # 非空表示本次执行以异常收尾；前端据此显式提示错误（区别于正常回复）。
    error: Optional[str] = None

    tool_call_sequence: Sequence[ToolRequest] | None = None
    tool_call_nums  : int | None = None



    def __post_init__(self) -> None:
        # stdlib dataclass 的 post-init 钩子（构造后实际执行）。
        # 此前用 pydantic ``@model_validator(mode='after')``，但 AgentResponse
        # 是 stdlib ``@dataclass`` 而非 pydantic dataclass，validator 永不被
        # 调用 -> response/tool_call_nums 的自动填充实为死代码（构造时传 None
        # 就保持 None，前端可能拿到空回复）。改用 ``__post_init__`` 使自动填充
        # 真正生效：response 为空则取末条 AssistantMessage.content；tool_call_nums
        # 为空则按 ToolMessage 计数。已显式赋值的字段（if None 守卫）不受影响。
        if self.response is None and self.message_list:
            last_message = self.message_list[-1]
            if isinstance(last_message, AssistantMessage) and isinstance(last_message.content, str):
                self.response = last_message.content
            else:
                self.response = ""

        if self.tool_call_nums is None:
            self.tool_call_nums = len([msg for msg in self.message_list if isinstance(msg, ToolMessage)])

class BaseAgentConfig(BaseModel):

    agent_name: str
    agent_description: str
    agent_task : str | None
    

class AgentTypes(BaseModel):
    pass

class SubAgentTypes():
    pass




class BaseAgentContext:


    def __init__(self) -> None:
        pass



class BaseAgent(ABC):
    """Agent基类"""

    def __init__(
        self,
        name: str,
        llm: BaseLLM ,

        description : str | None  = None,
        system_prompt: Optional[str] = None,
    ):
        self.name = name
        self.llm = llm
        self.descrition = description
        self.system_prompt = system_prompt
        self._history: list[LLMMessage] = []

    @abstractmethod
    async def run(self, task: str) ->  AsyncGenerator[Union[AgentEvent, AgentResponse], None]:
        """运行Agent"""
        pass

