from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Union,
    cast,
    Protocol,
    runtime_checkable,
    Type,
)
from typing import Union

JSONPrimitive = Union[
    str,
    int,
    float,
    bool,
    None,
]
from abc import abstractmethod
import asyncio
import inspect
import json
import time
import warnings
import typing
import re
from functools import partial
from abc import abstractmethod

from pydantic import BaseModel, Field, create_model, ConfigDict
from dataclasses import dataclass
from typing_extensions import Annotated

from openai import AsyncOpenAI,AsyncClient
from openai._types import NOT_GIVEN
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
    ParsedChatCompletion,
    ParsedChoice,
    ChatCompletionToolMessageParam
)
from openai.types.chat.chat_completion import Choice
from openai.types.shared_params import (
    FunctionDefinition,
    FunctionParameters,
    ResponseFormatJSONObject,
    ResponseFormatText,
    
)

from norma.core.tool_types import (
     Tool,
    ToolSchema,
    ToolRequest,
    ToolRequestResult,
)


import logging
logger = logging.getLogger(__name__)


class SystemMessage(BaseModel):
    """系统消息"""
    role: Literal["system"] = "system"
    content: str
    type: Literal["SystemMessage"] = "SystemMessage"


class UserMessage(BaseModel):
    """用户消息"""
    role: Literal["user"] = "user"
    content: str
    type: Literal["UserMessage"] = "UserMessage"



class AssistantMessage(BaseModel):
    """助手消息"""
    role : str = 'assistant'
    response: Any = None # 模型返回的原生的结果,包含若干字段（可选）
    content: str
    reason_content: str | None = None
    tool_calls : List[ToolRequest] | None = None
    type: Literal["AssistantMessage"] = "AssistantMessage"




class ToolMessage(BaseModel):
    """工具执行结果消息"""
    role: Literal["tool"] = "tool"
    content : str
    tool_result: ToolRequestResult
    type: Literal["ToolResultMessage"] = "ToolResultMessage"

    @property
    def tool_call_id(self):
        return self.tool_result.tool_call_id
    

        



LLMMessage = Annotated[
    Union[SystemMessage, UserMessage, AssistantMessage, ToolMessage],
    Field(discriminator="type")
]







class TopLogprob(BaseModel):
    """Top logprob信息"""
    logprob: float
    bytes: Optional[List[int]] = None


class TokenLogprob(BaseModel):
    token: str
    logprob: float
    top_logprobs: Optional[List[TopLogprob]] = None
    bytes: Optional[List[int]] = None




FinishReasons = Literal["stop", "length", "tool_calls", "content_filter", "unknown"]


class LLMRequest(BaseModel):
    """LLM请求参数"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: Sequence[LLMMessage]
    tools: Sequence[Union[Tool, ToolSchema]] | None = None
    tool_choice: Optional[Union[Literal["auto", "none", "required"], str]] = "auto"
    structured_output: Optional[Type[BaseModel]] = None
    stream_mode: bool = True
    
    
    #通用参数
    temperature: float = 0.1
    top_p: float = 1.0
    max_tokens: Optional[int] = None
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    n: int = 1
    logprobs: bool = False
    top_logprobs: Optional[int] = None


class RequestUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int

class LLMResponse(BaseModel):
    """LLM响应结果

    流式语义：
    - 增量 chunk：``response_message=None``、``finish_reason="unknown"``，
      ``stream_content`` / ``stream_reasoning`` 携带本 chunk 的文本/推理增量。
    - 最终 chunk：``response_message`` 为完整 AssistantMessage，``finish_reason`` 为真实值。
    """
    response_message : AssistantMessage | None
    finish_reason: FinishReasons
    stream_content : str | None  = None
    stream_reasoning : str | None = None  # 推理增量（流式 chunk）

    @property
    def tool_calls(self,) -> List[ToolRequest] | None:
        if self.response_message is not None:
            return  self.response_message.tool_calls
        return None
    @property
    def  content(self) -> str | None:
        if self.response_message is not None:
            return self.response_message.content
        else: 
            return None


   #content: str  
   #tool_calls : Optional[List[ToolRequest]] #  调用工具列表
    reason_content: Optional[str] = None  # 推理过程（如 O1/O3 模型）
    structured_content: Optional[BaseModel] = None  # 结构化输出
    
    # Token 统计
    prompt_tokens: int = 0
    completion_tokens: int = 0
    
    # Logprobs 信息
    logprobs: Optional[List[TokenLogprob]] = None
    





class BaseLLM:



    
    async def __call__(self, llm_request: LLMRequest, **kwargs: Any) -> AsyncGenerator[LLMResponse, None]:
        if llm_request.stream_mode:
            async for chunk in self.stream_chat(llm_request, **kwargs):
                yield chunk
        else:
            response = await self.chat(llm_request, **kwargs)
            yield response  # 只 yield 一次
        

    @abstractmethod
    async def chat(
        self,
        llm_rquest: LLMRequest,
        **kwargs: Any,
    ) -> LLMResponse:
        pass

    @abstractmethod
    async def stream_chat(
        self,
        llm_rquest: LLMRequest,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMResponse, None]:
        pass
