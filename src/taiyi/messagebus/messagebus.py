"""
Taiyi 消息总线架构
支持双向交互、异步处理、多订阅者模式
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from enum import Enum
from datetime import datetime
import uuid


# ==================== 消息类型定义 ====================

class MessageType(Enum):
    """消息类型枚举"""
    # 用户相关
    USER_INPUT = "user_input"              # 用户输入
    USER_INTERRUPT = "user_interrupt"      # 用户中断
    USER_CONFIRM = "user_confirm"          # 用户确认
    USER_REJECT = "user_reject"            # 用户拒绝
    
    # Agent相关
    AGENT_THINK = "agent_think"            # Agent思考
    AGENT_TOOL_REQUEST = "agent_tool_req"  # Agent工具请求
    AGENT_TOOL_RESULT = "agent_tool_res"   # 工具执行结果
    AGENT_LLM_REQUEST = "agent_llm_req"    # LLM请求
    AGENT_LLM_RESPONSE = "agent_llm_resp"  # LLM响应
    AGENT_RESPONSE = "agent_response"      # Agent最终响应
    AGENT_ERROR = "agent_error"            # Agent错误
    
    # 系统相关
    SYSTEM_STATUS = "system_status"        # 系统状态
    SYSTEM_LOG = "system_log"              # 系统日志
    
    # UI相关
    UI_RENDER = "ui_render"                # UI渲染请求
    UI_CLEAR = "ui_clear"                  # 清空屏幕
    UI_PROMPT = "ui_prompt"                # 需要用户确认


@dataclass
class Message:
    """消息基类"""
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: MessageType = MessageType.SYSTEM_LOG
    timestamp: datetime = field(default_factory=datetime.now)
    payload: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # 用于追踪消息链
    parent_id: Optional[str] = None
    conversation_id: Optional[str] = None


# ==================== 消息总线 ====================

class MessageBus:
    """
    异步消息总线
    支持发布-订阅模式、消息过滤、优先级处理
    """
    
    def __init__(self, max_queue_size: int = 1000):
        # 消息队列
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        
        # 订阅者映射: {MessageType: [callback_functions]}
        self._subscribers: Dict[MessageType, List[Callable]] = {}
        
        # 全局订阅者（接收所有消息）
        self._global_subscribers: List[Callable] = []
        
        # 消息历史（用于调试）
        self._message_history: List[Message] = []
        self._max_history = 100
        
        # 运行状态
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None
        
    def subscribe(self, msg_type: MessageType, callback: Callable):
        """订阅特定类型的消息"""
        if msg_type not in self._subscribers:
            self._subscribers[msg_type] = []
        self._subscribers[msg_type].append(callback)
        
    def subscribe_all(self, callback: Callable):
        """订阅所有消息"""
        self._global_subscribers.append(callback)
        
    def unsubscribe(self, msg_type: MessageType, callback: Callable):
        """取消订阅"""
        if msg_type in self._subscribers:
            self._subscribers[msg_type].remove(callback)
            
    async def publish(self, message: Message):
        """发布消息到总线"""
        await self._queue.put(message)
        
        # 保存历史
        self._message_history.append(message)
        if len(self._message_history) > self._max_history:
            self._message_history.pop(0)
    
    async def _process_messages(self):
        """消息处理循环"""
        while self._running:
            try:
                # 获取消息（超时避免阻塞）
                message = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=0.1
                )
                
                # 分发给全局订阅者
                for callback in self._global_subscribers:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(message)
                        else:
                            callback(message)
                    except Exception as e:
                        print(f"Global subscriber error: {e}")
                
                # 分发给特定订阅者
                if message.msg_type in self._subscribers:
                    for callback in self._subscribers[message.msg_type]:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(message)
                            else:
                                callback(message)
                        except Exception as e:
                            print(f"Subscriber error for {message.msg_type}: {e}")
                            
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Message processing error: {e}")
    
    async def start(self):
        """启动消息总线"""
        if not self._running:
            self._running = True
            self._processor_task = asyncio.create_task(self._process_messages())
            
    async def stop(self):
        """停止消息总线"""
        self._running = False
        if self._processor_task:
            await self._processor_task
            
    def get_history(self, msg_type: Optional[MessageType] = None) -> List[Message]:
        """获取消息历史"""
        if msg_type:
            return [m for m in self._message_history if m.msg_type == msg_type]
        return self._message_history.copy()


# ==================== 用户输入管理器 ====================

class UserInputManager:
    """
    用户输入管理器
    处理用户输入并发布到消息总线
    支持等待用户确认
    """
    
    def __init__(self, message_bus: MessageBus):
        self.message_bus = message_bus
        self._confirmation_futures: Dict[str, asyncio.Future] = {}
        
        # 订阅用户确认/拒绝消息
        self.message_bus.subscribe(MessageType.USER_CONFIRM, self._handle_confirmation)
        self.message_bus.subscribe(MessageType.USER_REJECT, self._handle_rejection)
        
    async def send_input(self, text: str, conversation_id: str):
        """发送用户输入"""
        message = Message(
            msg_type=MessageType.USER_INPUT,
            payload={"text": text},
            conversation_id=conversation_id
        )
        await self.message_bus.publish(message)
        
    async def send_interrupt(self, conversation_id: str):
        """发送中断信号"""
        message = Message(
            msg_type=MessageType.USER_INTERRUPT,
            conversation_id=conversation_id
        )
        await self.message_bus.publish(message)
        
    async def request_confirmation(
        self, 
        prompt: str, 
        conversation_id: str,
        timeout: float = 60.0
    ) -> bool:
        """
        请求用户确认
        返回 True(确认) 或 False(拒绝)
        """
        request_id = str(uuid.uuid4())
        
        # 创建Future等待响应
        future = asyncio.Future()
        self._confirmation_futures[request_id] = future
        
        # 发送确认请求
        message = Message(
            msg_type=MessageType.UI_PROMPT,
            payload={
                "request_id": request_id,
                "prompt": prompt
            },
            conversation_id=conversation_id
        )
        await self.message_bus.publish(message)
        
        try:
            # 等待用户响应
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            # 超时默认拒绝
            return False
        finally:
            # 清理
            self._confirmation_futures.pop(request_id, None)
    
    async def _handle_confirmation(self, message: Message):
        """处理用户确认"""
        request_id = message.payload.get("request_id")
        if request_id in self._confirmation_futures:
            self._confirmation_futures[request_id].set_result(True)
            
    async def _handle_rejection(self, message: Message):
        """处理用户拒绝"""
        request_id = message.payload.get("request_id")
        if request_id in self._confirmation_futures:
            self._confirmation_futures[request_id].set_result(False)


# ==================== Agent适配器 ====================

class AgentMessageAdapter:
    """
    Agent消息适配器
    将Agent事件转换为消息总线消息
    """
    
    def __init__(self, message_bus: MessageBus):
        self.message_bus = message_bus
        
    async def handle_agent_event(self, event: Any, conversation_id: str):
        """将Agent事件转换为消息"""
        from taiyi.core.agent_types import (
            AgentThinkEvent,
            AgentToolRequestEvent,
            AgentToolRequestAnswerEvent,
            AgentLLMRequestEvent,
            AgentLLMResponseEvent,
            AgentResponse
        )
        
        # 根据事件类型映射消息类型
        if isinstance(event, AgentThinkEvent):
            msg_type = MessageType.AGENT_THINK
        elif isinstance(event, AgentToolRequestEvent):
            msg_type = MessageType.AGENT_TOOL_REQUEST
        elif isinstance(event, AgentToolRequestAnswerEvent):
            msg_type = MessageType.AGENT_TOOL_RESULT
        elif isinstance(event, AgentLLMRequestEvent):
            msg_type = MessageType.AGENT_LLM_REQUEST
        elif isinstance(event, AgentLLMResponseEvent):
            msg_type = MessageType.AGENT_LLM_RESPONSE
        elif isinstance(event, AgentResponse):
            msg_type = MessageType.AGENT_RESPONSE
        else:
            msg_type = MessageType.SYSTEM_LOG
            
        message = Message(
            msg_type=msg_type,
            payload=event,
            conversation_id=conversation_id
        )
        
        await self.message_bus.publish(message)


# ==================== UI渲染器 ====================

class UIRenderer:
    """
    UI渲染器
    订阅Agent消息并渲染到界面
    """
    
    def __init__(self, message_bus: MessageBus, agent_renderer):
        self.message_bus = message_bus
        self.agent_renderer = agent_renderer
        
        # 订阅需要渲染的消息类型
        render_types = [
            MessageType.AGENT_THINK,
            MessageType.AGENT_TOOL_REQUEST,
            MessageType.AGENT_TOOL_RESULT,
            MessageType.AGENT_LLM_RESPONSE,
            MessageType.AGENT_RESPONSE,
            MessageType.UI_PROMPT,
        ]
        
        for msg_type in render_types:
            self.message_bus.subscribe(msg_type, self.render_message)
    
    async def render_message(self, message: Message):
        """渲染单个消息"""
        from prompt_toolkit import print_formatted_text
        
        if message.msg_type == MessageType.UI_PROMPT:
            # 特殊处理确认提示
            prompt = message.payload.get("prompt", "")
            formatted = self.agent_renderer.render_confirmation_prompt(prompt)
            print_formatted_text(formatted)
        else:
            # 使用现有的渲染器
            event = message.payload
            formatted = self.agent_renderer.render_event(
                event_type=message.msg_type.value,
                content=event
            )
            print_formatted_text(formatted)


# ==================== 使用示例 ====================

async def example_usage():
    """示例：如何使用消息总线架构"""
    
    # 1. 创建消息总线
    bus = MessageBus()
    await bus.start()
    
    # 2. 创建组件
    user_input_mgr = UserInputManager(bus)
    agent_adapter = AgentMessageAdapter(bus)
    
    # 3. 订阅消息（日志示例）
    async def log_all_messages(message: Message):
        print(f"[LOG] {message.msg_type.value}: {message.msg_id}")
    
    bus.subscribe_all(log_all_messages)
    
    # 4. 发送用户输入
    conversation_id = str(uuid.uuid4())
    await user_input_mgr.send_input("帮我创建一个Python文件", conversation_id)
    
    # 5. Agent处理（模拟）
    # 假设Agent需要确认
    confirmed = await user_input_mgr.request_confirmation(
        "是否允许创建 example.py?",
        conversation_id
    )
    
    if confirmed:
        print("用户已确认")
    else:
        print("用户已拒绝")
    
    # 6. 停止总线
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(example_usage())
