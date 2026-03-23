
from norma.core.llm_types import (
    UserMessage,
    AssistantMessage,
    ToolMessage,
    SystemMessage,
    LLMMessage,
)

from typing import List

from abc import ABC, abstractmethod

from norma.core.tool_types import (
    ParametersSchema,
    Tool,
    ToolRequest,
    ToolRequestResult
)

# 暂时不考虑
class MemoryProcesser(ABC):

    async def on_message_push(self, history_message, add_messages):

        pass


    async def on_message_pull(self, ):


        pass
    
    




class AgentMemory:


    def __init__(self,
        message_list : list[LLMMessage] | None ,
        save_toolmessage_num : int = 50 ,
        ) -> None:
        
        self.history_message = message_list  if message_list is not None else []
        self.save_toolmessage_num = save_toolmessage_num
    


    
        
    async def push_messages(self, add_messages: list[LLMMessage]):
        for message in add_messages:

            self.history_message.append(message)


    async def pull_messages(self):
        """
        处理策略:

        对于toolmessage:
            只保留最近的save_toolmessage_num 条的content
            对于在这之前的toolmessage 去除内容 , 改为占位符 '调用是否成功{}, 当前的tool的执行内容已被临时清除,如果你觉得需要此处内容, 可以尝试重新运行此工具'
        """
        # 统计ToolMessage的数量
        tool_messages = [msg for msg in self.history_message if isinstance(msg, ToolMessage)]

        if len(tool_messages) <= self.save_toolmessage_num:
            # 如果ToolMessage数量不超过限制，直接返回所有消息
            return self.history_message

        # 需要压缩的ToolMessage数量
        num_to_compress = len(tool_messages) - self.save_toolmessage_num
        compressed_count = 0

        # 创建新的消息列表
        result_messages = []

        for msg in self.history_message:
            if isinstance(msg, ToolMessage):
                if compressed_count < num_to_compress:
                    # 压缩这个ToolMessage

            
                    compressed_tool_message = ToolMessage(

                        
                        tool_result= msg.tool_result,
                        content=f'tool执行结果: {not msg.tool_result.is_error}, tool 执行结果被缓存, you can view it to recall this tool '
                    )
                    result_messages.append(compressed_tool_message)
                    compressed_count += 1
                else:
                    # 保留这个ToolMessage
                    result_messages.append(msg)
            else:
                # 非ToolMessage直接保留
                result_messages.append(msg)

        return result_messages




    
    
    
    
