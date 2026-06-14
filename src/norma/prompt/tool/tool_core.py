"""
工具核心模块 - 简化版
负责工具的注册、删除、执行


实现机制：



NormaArtifact： 工具聚合, 注册, 执行接口
NormaArtifactContext : 工具消息上下文， 恢复的时候 从中恢复 或 保存

ToolPerMission: allow, ask , deny
ToolPerMissionChecker: 基类， 有若干策略，启动时候的可以从中加载若干的检查策略,  同样需要NormaArtifactContext里的内容
"""

import logging
import asyncio
from typing import Dict, List, Optional, Any, Callable,Protocol
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel,Field
from enum import Enum
from abc import ABC
from norma.core.tool_types import (
    Tool,
    FunctionTool,
    ToolRequest,
    ToolRequestResult,
    ToolSchema
)
from norma.prompt.tool.read_tool.read_tool import ReadTool
from norma.prompt.tool.ls_tool.ls_tool import LsTool
from norma.prompt.tool.glob_tool.glob_tool import GlobTool
from norma.prompt.tool.grep_tool.grep_tool import GrepTool
from norma.prompt.tool.edit_tool.edit_tool import EditTool
from norma.prompt.tool.write_tool.write_tool import WriteTool
from norma.prompt.tool.todo_tool.todo_tool import TodoWriteTool
from norma.prompt.tool.bash_tool.bash_tool import BashTool



logger = logging.getLogger(__name__)


class ToolNotFoundError(Exception):
    """工具未找到错误"""
    pass



class ExecutionMode(Enum):
    """执行模式"""
    PLAN = "Plan"          # 规划模式：只读
    AUTO_EDIT = "AutoEdit" # 自动编辑：可读写
    ROOT = "AutoApprove"          # 根模式：完全权限

class PermissionResult(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # 暂时不考虑 ask的逻辑 需集成系统总线 




#class DefaultToolChecker:
#    """
#    默认工具检查器
#    
#    检查规则：
#    1. Edit 工具调用的文件必须先被 Read 工具读取过
#    """
#    
#    @property
#    def name(self) -> str:
#        return "DefaultToolChecker"
#    
#    def check(
#        self,
#        tool_req: ToolRequest,
#        tool_context: NormaArtifactContext
#    ) -> PermissionResult:
#        """执行默认权限检查"""
#        tool_name = tool_req.tool_call_name
#        params = tool_req.tool_call_arguments
#        
#        # 规则1: Edit 工具必须先读取文件
#        if tool_name in ['edit', 'todo_write']:
#            file_path = params.get('path') or params.get('file_path')
#            
#            if file_path:
#                # 检查文件是否已被读取
#                if not tool_context.is_file_read(file_path):
#                    logger.warning(
#                        f"Edit denied: file '{file_path}' has not been read yet. "
#                        f"Please use 'read' tool first."
#                    )
#                    return PermissionResult.DENY
#        
#        return PermissionResult.ALLOW


class NormaArtifactContext(BaseModel):

    """执行上下文 - 纯数据对象"""
    
    # 基础配置
    cwd: str = Field(default_factory=lambda: str(Path.cwd()))
    extra_dirs: List[str] = Field(default_factory=list)
    mode: ExecutionMode = Field(default=ExecutionMode.AUTO_EDIT)
    
    
    # 工具配置
    allowed_tools: Set[str] = Field(default_factory=set)
    disabled_tools: Set[str] = Field(default_factory=set)
    
    # 目录权限
    allow_edit_dirs: Optional[List[str]] = None
    
    # 文件追踪
    read_files: Set[str] = Field(default_factory=set)
    edited_files: Set[str] = Field(default_factory=set)
    written_files: Set[str] = Field(default_factory=set)
    
            
    def __init__(self, **data):
        super().__init__(**data)
        # 初始化工作目录
        self._working_dirs: Set[Path] = set()
        self._allow_read_dirs: Set[Path] = set()
        self._allow_edit_dirs: Set[Path] = set()
        self._init_directories()
    
    def _init_directories(self):
        """初始化目录结构"""
        # 添加当前工作目录
        self._working_dirs.add(Path(self.cwd).resolve())
        
        # 添加额外目录
        for dir_path in self.extra_dirs:
            self._working_dirs.add(Path(dir_path).resolve())
        
        # 添加用户主目录
        self._working_dirs.add(Path.home())
        
        # 默认可读目录 = 工作目录
        self._allow_read_dirs = self._working_dirs.copy()
        
        # 默认可写目录
        if self.allow_edit_dirs:
            for dir_path in self.allow_edit_dirs:
                self._allow_edit_dirs.add(Path(dir_path).resolve())
        else:
            # 默认只能在 cwd 下写入
            self._allow_edit_dirs.add(Path(self.cwd).resolve())

    async def check_permission(self, tool_req: ToolRequest) -> PermissionResult:
        """
        执行所有权限检查
        
        Returns:
            ALLOW: 所有检查通过
            DENY: 任一检查拒绝
            ASK: 需要用户确认
        """
       # if tool_req.tool_call_name in ['Edit','Write']:
       #     


       # for checker in self._checkers:
       #     result = checker.check(tool_req, self)
       #     if result == PermissionResult.DENY:
       #         logger.warning(
       #             f"Permission denied by {checker.name} for tool '{tool_req.tool_call_name}'"
       #         )
       #         return PermissionResult.DENY
       #     elif result == PermissionResult.ASK:
       #         return PermissionResult.ASK
        
        return PermissionResult.ALLOW

class ToolExecuteChecker(Protocol):
    """工具执行权限检查器协议"""
    
    @property
    def name(self) -> str:
        """检查器名称"""
        ...
    
    def check(
        self,
        tool_req: ToolRequest,
        tool_context: NormaArtifactContext
    ) -> PermissionResult:
        """
        检查工具执行权限
        
        Args:
            tool_req: 工具请求
            tool_context: 执行上下文
            
        Returns:
            权限检查结果
        """
        ...



class NormaArtifact:
    """
    Norma工具系统
    提供工具的注册、删除和执行功能
    """

    def __init__(
        self,
      #  context :  NormaArtifactContext,
        cwd: str| Path | None = None,
        extra_allowd_dirs : List[str] | None = None,
        allow_edit_dirs : List[str] | None = None,
        tools: Optional[List[Tool]] = None,
        max_concurrent: int = 10
        
    ):
        """
        初始化工具系统
        
        Args:
            tools: 初始工具列表
            max_concurrent: 最大并发执行数量
        """
        self.cwd = cwd
        self._tools: Dict[str, Tool] = {}
        self.tool_call_history: List[ToolRequestResult] = []
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.readed_files = [] 

     #   self._load_default_tools()
        # 注册初始工具
        if tools:
            for tool in tools:


                
                self.register_tool(tool)
        

    def _init_from_context(self,):
        pass
    
    def _load_default_tools(self,): 
        


       #ls_tool = LsTool()
       #read_tool = ReadTool()
        default_tools = [
            LsTool(),
            ReadTool(),
            GlobTool(),
            GrepTool(),
            EditTool(),
            WriteTool(),
            TodoWriteTool(),
            BashTool(),
        ]
        for tool in default_tools:
            self.register_tool(tool)
        logger.info(f"Loaded {len(default_tools)} default tools")

    def register_tool(self, tool: Tool) -> None:
        """
        注册一个工具
        
        Args:
            tool: 要注册的工具实例
            
        Raises:
            ValueError: 当工具名称已存在时
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already exists")
        
        self._tools[tool.name] = tool
    


        logger.info(f"Registered tool: {tool.name}")
        

        
    
    def unregister_tool(self, tool_name: str) -> bool:
        """
        删除一个工具
        
        Args:
            tool_name: 工具名称
            
        Returns:
            是否成功删除
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.info(f"Unregistered tool: {tool_name}")
            return True
        return False
    
    def get_tool(self, tool_name: str) -> Optional[Tool]:
        """获取工具实例"""
        return self._tools.get(tool_name)
    
    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否存在"""
        return tool_name in self._tools
    
    def list_tools(self) -> List[str]:
        """列出所有已注册的工具名称"""
        return list(self._tools.keys())
    
    def get_tool_schemas(self) -> List[ToolSchema]:
        """获取所有工具的 Schema（用于 LLM API 调用）"""
        return [tool.schema for tool in self._tools.values()]
    
    # ==================== 工具执行 ====================
    
    async def execute_tool(self, tool_request: ToolRequest) -> ToolRequestResult:
        """
        执行单个工具调用
        
        Args:
            tool_request: 工具请求
            
        Returns:
            工具执行结果
        """
        tool_name = tool_request.tool_call_name
        tool = self._tools.get(tool_name)
        
        if not tool:
            logger.error(f"Tool '{tool_name}' not found")
            result = ToolRequestResult(
                request=tool_request,
                result=None,
                content=f'{{"error": "Tool \'{tool_name}\' not found"}}',
                is_error=True,
                execution_times=0.0
            )
          #  self._record_call(tool_request, result)
            return result
        
        try:
            async with self._semaphore:
                logger.info(f"Executing tool '{tool_name}' (call_id: {tool_request.tool_call_id})")
                result = await tool.execute(tool_request)
              #  self._record_call(tool_request, result)
                return result
                
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
            result = ToolRequestResult(
                request=tool_request,
                result=None,
                content=f'{{"error": "{str(e)}"}}',
                is_error=True,
                execution_times=0.0
            )
            #self._record_call(tool_request, result)
            return result
    
    async def execute_tools(
        self,
        tool_requests: List[ToolRequest]
    ) -> List[ToolRequestResult]:
        """
        批量执行工具调用（并发执行）
        
        Args:
            tool_requests: 工具请求列表
            
        Returns:
            工具执行结果列表
        """
        if not tool_requests:
            return []
        
        logger.info(f"Executing {len(tool_requests)} tools concurrently")
        tasks = [self.execute_tool(request) for request in tool_requests]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return results
    
    # ==================== 历史记录 ====================
    
    def _record_call(
        self,
        #tool_request: ToolRequest,
        result: ToolRequestResult
    ) -> None:
        """记录工具调用到历史列表"""
        
        self.tool_call_history.append(result)
    
    def get_history(self, limit: Optional[int] = None) -> List[ToolRequestResult]:
        """
        获取工具调用历史
        
        Args:
            limit: 可选，限制返回数量（返回最近的N条）
            
        Returns:
            历史记录列表
        """
        if limit:
            return self.tool_call_history[-limit:]
        return self.tool_call_history
    
    def clear_history(self) -> None:
        """清空执行历史"""
        self.tool_call_history.clear()
        logger.info("Cleared tool call history")
    
    # ==================== 辅助方法 ====================
    
    def get_status(self) -> Dict[str, Any]:
        """获取系统状态"""
        return {
            "registered_tools": len(self._tools),
            "tool_names": list(self._tools.keys()),
            "history_count": len(self.tool_call_history),
            "max_concurrent": self.max_concurrent
        }
    
    def clear_all(self) -> None:
        """清空所有工具和历史"""
        self._tools.clear()
        self.tool_call_history.clear()
        logger.info("Cleared all tools and history")
