"""
OpenAI 兼容 API 的 LLM 实现

支持 OpenAI 格式的 API（包括各类兼容接口），
提供 chat() 和 stream_chat() 方法。
"""

import json
import logging
from typing import Any, AsyncGenerator, Optional, List, Type, Dict
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from openai._types import NOT_GIVEN
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice

from norma.core.llm_types import (
    BaseLLM,
    LLMRequest,
    LLMResponse,
    AssistantMessage,
    FinishReasons,
    RequestUsage,
    ToolRequest,
)
from norma.core.tool_types import Tool, ToolSchema

logger = logging.getLogger(__name__)


class ProviderConfig(BaseModel):
    """Provider 配置"""
    name: str
    url: str = ""
    api_key: str = ""
    models: List[str] = Field(default_factory=list)


class OpenAILLM(BaseLLM):
    """基于 OpenAI 兼容 API 的 LLM 实现，支持多 Provider"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        max_tokens: int = 8192,
        temperature: float = 0.1,
        top_p: float = 1.0,
        default_stream_mode: bool = False,
        max_context_tokens: int = 128000,
        providers: Optional[dict] = None,
        default_provider: Optional[str] = None,
    ):
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.default_stream_mode = default_stream_mode
        self.max_context_tokens = max_context_tokens

        # 多 Provider 支持
        self._providers: Dict[str, ProviderConfig] = {}
        self._default_provider = default_provider
        if providers:
            for name, cfg in providers.items():
                self._providers[name] = ProviderConfig(
                    name=name,
                    url=cfg.get("url", ""),
                    api_key=cfg.get("api_key", ""),
                    models=cfg.get("models", []),
                )

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        logger.info(f"OpenAILLM initialized: model={model}, base_url={base_url}")

    @property
    def available_models(self) -> List[str]:
        """列出所有可用的模型（包括所有 Provider 的模型）"""
        models = [self.model]
        for prov in self._providers.values():
            for m in prov.models:
                full_name = f"{prov.name}/{m}"
                if full_name not in models:
                    models.append(full_name)
        return models

    def switch_model(self, model_name: str) -> None:
        """切换模型（支持 provider/model 格式）"""
        if "/" in model_name:
            provider_name, model_id = model_name.split("/", 1)
            if provider_name in self._providers:
                prov = self._providers[provider_name]
                self.model = model_id
                if prov.url:
                    self._base_url = prov.url
                if prov.api_key:
                    self._api_key = prov.api_key
                # 重建 client
                self.client = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                )
                logger.info(f"Switched to provider '{provider_name}', model '{model_id}'")
            else:
                raise ValueError(f"Provider '{provider_name}' not found")
        else:
            self.model = model_name
            logger.info(f"Switched to model '{model_name}'")

    def switch_provider(self, provider_name: str) -> None:
        """切换到指定 Provider（使用其第一个模型）"""
        if provider_name not in self._providers:
            raise ValueError(f"Provider '{provider_name}' not found")
        prov = self._providers[provider_name]
        if prov.models:
            self.model = prov.models[0]
        if prov.url:
            self._base_url = prov.url
        if prov.api_key:
            self._api_key = prov.api_key
        self.client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )
        self._default_provider = provider_name
        logger.info(f"Switched to provider '{provider_name}', model '{self.model}'")

    def _build_messages(self, llm_request: LLMRequest) -> list[dict]:
        """将内部消息格式转换为 OpenAI API 格式"""
        openai_messages = []
        for msg in llm_request.messages:
            if msg.type == "SystemMessage":
                openai_messages.append({
                    "role": "system",
                    "content": msg.content,
                })
            elif msg.type == "UserMessage":
                openai_messages.append({
                    "role": "user",
                    "content": msg.content,
                })
            elif msg.type == "AssistantMessage":
                assistant_msg: dict = {"role": "assistant"}
                if msg.content:
                    assistant_msg["content"] = msg.content
                if msg.reason_content:
                    assistant_msg["reasoning_content"] = msg.reason_content
                if msg.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_call_name,
                                "arguments": (
                                    tc.tool_call_arguments
                                    if isinstance(tc.tool_call_arguments, str)
                                    else json.dumps(tc.tool_call_arguments, ensure_ascii=False)
                                ),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                openai_messages.append(assistant_msg)
            elif msg.type == "ToolResultMessage":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
        return openai_messages

    def _build_tools(self, llm_request: LLMRequest) -> list[dict] | object:
        """将工具 Schema 转换为 OpenAI API 的 tools 格式"""
        if not llm_request.tools:
            return NOT_GIVEN

        tools = []
        for tool in llm_request.tools:
            if isinstance(tool, Tool):
                schema = tool.schema
            elif isinstance(tool, ToolSchema):
                schema = tool
            else:
                continue

            tool_def = {
                "type": "function",
                "function": {
                    "name": schema.name,
                    "description": schema.description or "",
                    "parameters": (
                        schema.parameters.model_dump(exclude_none=True)
                        if schema.parameters
                        else {"type": "object", "properties": {}}
                    ),
                },
            }
            if schema.strict:
                tool_def["function"]["strict"] = True
                params = tool_def["function"]["parameters"]
                params.setdefault("additionalProperties", False)
            tools.append(tool_def)

        return tools if tools else NOT_GIVEN

    def _parse_response(self, completion: ChatCompletion) -> LLMResponse:
        """解析 OpenAI ChatCompletion 响应为内部格式"""
        # 防御空 choices（如 content_filter 等极端响应），与流式路径一致
        if not completion.choices:
            logger.warning("LLM response has no choices")
            return LLMResponse(
                response_message=AssistantMessage(content="", tool_calls=None),
                finish_reason="unknown",
            )

        choice: Choice = completion.choices[0]
        message = choice.message

        # 解析 tool_calls
        tool_calls = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = tc.function.arguments
                try:
                    args_dict = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args_dict = args
                tool_calls.append(ToolRequest(
                    tool_call_id=tc.id,
                    tool_call_name=tc.function.name,
                    tool_call_arguments=args_dict,
                ))

        # 解析 finish_reason
        finish_reason_map = {
            "stop": "stop",
            "length": "length",
            "tool_calls": "tool_calls",
            "content_filter": "content_filter",
        }
        finish_reason = finish_reason_map.get(
            choice.finish_reason, "unknown"
        )

        # 解析推理内容（兼容 reasoning_content 字段，与流式路径一致；
        # 此前非流式路径漏传 reason_content，导致默认 stream_mode=False 下
        # 思考模型的推理被静默丢弃——TUI 非流式分支会读 response_message.reason_content）
        reason_content = getattr(message, "reasoning_content", None)

        # 构建 AssistantMessage
        assistant_message = AssistantMessage(
            content=message.content or "",
            reason_content=reason_content,
            tool_calls=tool_calls,
        )

        # 构建 usage
        prompt_tokens = 0
        completion_tokens = 0
        if completion.usage:
            prompt_tokens = completion.usage.prompt_tokens or 0
            completion_tokens = completion.usage.completion_tokens or 0

        return LLMResponse(
            response_message=assistant_message,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def chat(self, llm_request: LLMRequest, **kwargs: Any) -> LLMResponse:
        """非流式调用"""
        openai_messages = self._build_messages(llm_request)
        tools = self._build_tools(llm_request)

        request_params: dict = {
            "model": kwargs.pop("model", self.model),
            "messages": openai_messages,
            "temperature": kwargs.pop("temperature", llm_request.temperature),
            "top_p": kwargs.pop("top_p", llm_request.top_p),
            "max_tokens": kwargs.pop("max_tokens", llm_request.max_tokens or self.max_tokens),
        }

        if tools is not NOT_GIVEN:
            request_params["tools"] = tools
            request_params["tool_choice"] = llm_request.tool_choice

        if llm_request.structured_output:
            request_params["response_format"] = {"type": "json_object"}

        request_params.update(kwargs)

        logger.debug(f"Sending chat request: model={request_params.get('model')}, "
                      f"messages={len(openai_messages)}, tools={len(tools) if isinstance(tools, list) else 0}")

        completion: ChatCompletion = await self.client.chat.completions.create(
            **request_params
        )

        response = self._parse_response(completion)
        logger.debug(f"Chat response: finish_reason={response.finish_reason}, "
                      f"has_tool_calls={response.tool_calls is not None}, "
                      f"content_len={len(response.content or '')}")
        return response

    async def stream_chat(
        self, llm_request: LLMRequest, **kwargs: Any
    ) -> AsyncGenerator[LLMResponse, None]:
        """流式调用

        逐 chunk yield 增量 ``LLMResponse``（``stream_content`` / ``stream_reasoning``
        携带增量，``response_message=None``），最后 yield 一个完整的 ``LLMResponse``
        （含 ``response_message``、``finish_reason``、usage）。
        """
        openai_messages = self._build_messages(llm_request)
        tools = self._build_tools(llm_request)

        request_params: dict = {
            "model": kwargs.pop("model", self.model),
            "messages": openai_messages,
            "temperature": kwargs.pop("temperature", llm_request.temperature),
            "top_p": kwargs.pop("top_p", llm_request.top_p),
            "max_tokens": kwargs.pop("max_tokens", llm_request.max_tokens or self.max_tokens),
            "stream": True,
        }

        if tools is not NOT_GIVEN:
            request_params["tools"] = tools
            request_params["tool_choice"] = llm_request.tool_choice

        if llm_request.structured_output:
            request_params["response_format"] = {"type": "json_object"}

        request_params.update(kwargs)

        # 流式累积状态
        content_buffer = ""
        reasoning_buffer = ""
        tool_calls_map: dict[int, dict] = {}  # index -> {id, name, arguments}
        finish_reason = "unknown"
        prompt_tokens = 0
        completion_tokens = 0

        finish_reason_map = {
            "stop": "stop",
            "length": "length",
            "tool_calls": "tool_calls",
            "content_filter": "content_filter",
        }

        stream = await self.client.chat.completions.create(**request_params)

        async for chunk in stream:
            # usage 可能在无 choices 的末尾 chunk 中到达
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tokens = usage.prompt_tokens or 0
                completion_tokens = usage.completion_tokens or 0

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # 文本增量
            if delta.content:
                content_buffer += delta.content
                yield LLMResponse(
                    response_message=None,
                    finish_reason="unknown",
                    stream_content=delta.content,
                )

            # 推理增量（兼容 reasoning_content 字段）
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_buffer += reasoning
                yield LLMResponse(
                    response_message=None,
                    finish_reason="unknown",
                    stream_reasoning=reasoning,
                )

            # 累积 tool_calls（不对外 yield 增量，最终一次性给出）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_map[idx]["arguments"] += tc_delta.function.arguments

            # 处理 finish_reason
            if choice.finish_reason:
                finish_reason = finish_reason_map.get(
                    choice.finish_reason, "unknown"
                )

        # 构建最终 tool_calls
        final_tool_calls = None
        if tool_calls_map:
            final_tool_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc_data = tool_calls_map[idx]
                args = tc_data["arguments"]
                try:
                    args_dict = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args_dict = args
                final_tool_calls.append(ToolRequest(
                    tool_call_id=tc_data["id"],
                    tool_call_name=tc_data["name"],
                    tool_call_arguments=args_dict,
                ))

        assistant_message = AssistantMessage(
            content=content_buffer,
            reason_content=reasoning_buffer if reasoning_buffer else None,
            tool_calls=final_tool_calls,
        )

        yield LLMResponse(
            response_message=assistant_message,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def estimate_tokens(self, messages: list) -> int:
        """简单估算 token 数量（基于字符数）"""
        total_chars = 0
        for msg in messages:
            if hasattr(msg, "content") and msg.content:
                total_chars += len(msg.content)
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    total_chars += len(json.dumps(tc.tool_call_arguments, ensure_ascii=False))
        # 粗略估计：1 token ≈ 1.5 中文字符 ≈ 4 英文字符
        return int(total_chars / 2.5)
