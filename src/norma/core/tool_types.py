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
import asyncio
import inspect
import json
import time
import warnings
import typing
import re
from functools import partial
from abc import abstractmethod, ABC


from pydantic import BaseModel, Field, create_model, model_validator
from dataclasses import dataclass
from typing_extensions import Annotated
from openai import AsyncStream
from openai import AsyncOpenAI, AsyncClient
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
import logging


logger = logging.getLogger(__name__)



class ParametersSchema(BaseModel):
    type: str
    properties: Dict[str, Any]
    required: Optional[Sequence[str]] = None
    additionalProperties: Optional[bool] = None


class ToolSchema(BaseModel):
    """工具Schema定义"""
    name: str
    description: Optional[str] = None
    parameters: Optional[ParametersSchema] = None
    strict: bool = True


class ToolRequest(BaseModel):
    tool_call_id: str
    tool_call_name: str
    tool_call_arguments: Dict[str, Any] | str


class ToolRequestError(Exception):
    """工具请求格式错误"""
    def __init__(self,  msg: str , *args, **kwargs) -> None:
        super().__init__(*args)
        self.error_msg = msg
        self.error_name = self.__class__.__name__
        

    def __str__(self) -> str:
        return f"[{self.error_name}]:{self.error_msg}"


class ToolRequestResult(BaseModel):
    """工具执行结果"""
    request: ToolRequest
    result: Any     # 工具的执行结果
    content: str    # 工具返回结果中最终使用的内容, 默认为 result 的string 格式
    is_error: bool = False
    execution_times: float = 0.0

    # 使用 property 提供便捷访问
    @property
    def tool_call_id(self) -> str:
        return self.request.tool_call_id

    @property
    def tool_call_name(self) -> str:
        return self.request.tool_call_name


class Tool:
    """工具基类"""
    
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def schema(self) -> ToolSchema: ...

    @property
    def is_readonly(self) -> bool:
        """是否为只读工具（无副作用）。只读工具可安全并发；写工具需串行。

        默认 False，由具体只读工具覆盖为 True（如 Read/Ls/Glob/Grep）。
        """
        return False

    @abstractmethod
    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行工具调用"""
        pass

    def parse_string_arguments(self, arguments: str) -> Dict[str, Any]:
        """
        从字符串形式的参数解析为 参数名 : 参数值的字典
        尝试从 JSON 格式和 XML 格式中解析
        """
        # 先尝试 JSON 格式
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        
        # 尝试 XML 格式解析
        try:
            # 简单的 XML 参数解析
            # 格式如: <param1>value1</param1><param2>value2</param2>
            pattern = r'<(\w+)>(.*?)</\1>'
            matches = re.findall(pattern, arguments, re.DOTALL)
            if matches:
                result = {}
                for key, value in matches:
                    # 尝试解析值的类型
                    try:
                        # 尝试解析为 JSON 值
                        result[key] = json.loads(value)
                    except:
                        # 保持为字符串
                        result[key] = value.strip()
                if result:
                    return result
        except Exception as e:
            logger.warning(f"XML 格式解析失败: {e}")
        
        # 如果都失败，抛出异常
        raise ToolRequestError(
            f"工具格式错误"
        )

    
class FunctionTool(Tool):
    """基于 Python 函数的工具"""
    
    def __init__(
        self,
        func: Callable[..., Any],
        name: Optional[str] = None,
        description: Optional[str] = None,
        strict: bool = False, 
    ):
        self._func = func
        self._name = name or func.__name__
        self._description = description or inspect.getdoc(func) or ""
        self.strict = strict
        self._args_model = self._build_args_model()
        self._signature = inspect.signature(self._func)
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def description(self) -> str:
        return self._description
    
    def _build_args_model(self) -> type[BaseModel]:
        """根据函数签名构建 Pydantic 参数模型"""
        sig = inspect.signature(self._func)
        type_hints = typing.get_type_hints(self._func)
        
        fields = {}
        for param in sig.parameters.values():
            if param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD
            ):
                raise TypeError(
                    f"Unsupported parameter kind in function '{self.name}': {param.kind}"
                )
            
            param_type = type_hints.get(param.name, Any)
            
            # Use param.name as the description by default for better model understanding
            field_description = param.name

            if param.default is inspect.Parameter.empty:
                fields[param.name] = (param_type, Field(..., description=field_description))
            else:
                fields[param.name] = (param_type, Field(default=param.default, description=field_description))
        
        return create_model(f"{self.name}Args", **fields)
    
    @property
    def schema(self) -> ToolSchema:
        """根据函数自动化构建 tool的schema"""
        params_schema = self._args_model.model_json_schema()
        
        # 清理schema
        params_schema.pop('$defs', None)
        params_schema.pop('$schema', None)
        params_schema.pop('title', None)
        
        params_schema.setdefault('type', 'object')
        params_schema.setdefault('properties', {})
        params_schema.setdefault('additionalProperties', False)
        
        # 提取required字段
        required = params_schema.pop('required', None)
        additional_properties = params_schema.pop('additionalProperties', False)
        
        parameters = ParametersSchema(
            type=params_schema.get('type', 'object'),
            properties=params_schema.get('properties', {}),
            required=required,
            additionalProperties=additional_properties
        )
        
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=parameters,
            strict=self.strict,
        )
    
    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行工具调用 所有工具的调用都是异步执行"""
        logger.info(
            f"执行工具 '{self.name}'，调用 ID: {tool_request.tool_call_id}, "
            f"\n 参数: {tool_request.tool_call_arguments}"
        )
        
        start_time = time.time()
        
        try:
            # 解析参数
            if isinstance(tool_request.tool_call_arguments, str):
                args_dict = self.parse_string_arguments(tool_request.tool_call_arguments)
            else:
                args_dict = tool_request.tool_call_arguments
            
            # 验证参数
            validated_args = self._args_model.model_validate(args_dict)
            
            # 执行函数
            if asyncio.iscoroutinefunction(self._func):
                raw_result = await self._func(**validated_args.model_dump())
            else:
                loop = asyncio.get_running_loop()
                raw_result = await loop.run_in_executor(
                    None,
                    partial(self._func, **validated_args.model_dump())
                )
            
            execution_time = time.time() - start_time
            
            # 构造返回内容
            content = json.dumps(raw_result, ensure_ascii=False) if raw_result is not None else ""
            
            return ToolRequestResult(
                request=tool_request,
                result=raw_result,
                content=content,
                is_error=False,
                execution_times=execution_time
            )
        
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"工具 '{self.name}' 执行失败: {e}", exc_info=True)
            
            error_content = json.dumps({"error": str(e)}, ensure_ascii=False)
            
            return ToolRequestResult(
                request=tool_request,
                result=None,
                content=error_content,
                is_error=True,
                execution_times=execution_time
            )
