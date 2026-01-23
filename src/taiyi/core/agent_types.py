from typing import Any, AsyncGenerator, Mapping, Sequence, List, Optional,Union
from dataclasses import dataclass

from pydantic import BaseModel, SerializeAsAny, model_validator,Field
from abc import ABC, abstractmethod
from datetime import datetime
from taiyi.core.llm_types import (
    LLMMessage, AssistantMessage , LLMResponse, ToolMessage,BaseLLM,
    LLMRequest
)


from taiyi.core.tool_types import (
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

class AgentLLMRequestEvent(AgentEvent):
    request: LLMRequest


class AgentLLMResponseEvent(AgentEvent):
    resonse: LLMResponse


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
   
    tool_call_sequence: Sequence[ToolRequest] | None = None 
    tool_call_nums  : int | None = None



    @model_validator(mode='after')
    def check_response(self) -> "AgentResponse":
        if self.response is None and self.message_list:
            last_message = self.message_list[-1]
            if isinstance(last_message, AssistantMessage) and isinstance(last_message.content, str):
                self.response = last_message.content
            else:
                self.response = ""

        if self.tool_call_nums is None:
            self.tool_call_nums = len([msg for msg in self.message_list if isinstance(msg, ToolMessage) ])

        return self

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

