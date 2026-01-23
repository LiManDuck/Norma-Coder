"""
TodoWrite Tool - 继承自 Tool 基类的实现

为当前编程会话创建和管理结构化任务列表。帮助跟踪进度、组织复杂任务、向用户展示工作周详性。

何时使用此工具（主动使用场景）：
1. 复杂多步任务 - 当任务需要3个或更多不同的步骤或操作时
2. 非平凡且复杂的任务 - 需要仔细规划或多个操作的任务
3. 用户明确要求任务列表 - 当用户直接要求使用任务列表时
4. 用户提供多个任务 - 当用户提供编号或逗号分隔的任务列表时
5. 收到新指令后 - 立即捕获用户需求作为任务
6. 开始执行任务时 - 在开始工作前标记为in_progress（理想情况同时只有一个in_progress任务）
7. 完成任务后 - 标记为已完成并添加在实施过程中发现的任何新跟进任务

何时不应使用此工具：
1. 只有一个简单、直接的任务
2. 任务琐碎且跟踪没有组织效益
3. 任务可以在少于3个简单步骤内完成
4. 任务纯属对话性或信息性

任务管理规则：
- 任务状态管理：pending（未开始）、in_progress（进行中，限制同时只有1个）、completed（成功完成）
- 实时更新任务状态，完成后立即标记，不要批量完成
- 一次只有1个in_progress任务，完成当前任务后再开始新任务
- 删除不再相关的任务
- 只有在完全完成任务时才标记为completed
- 遇到错误、阻塞或无法完成时保持为in_progress
- 标记为completed的情况：成功完成任务，测试通过，实现完整
- 不要标记为completed的情况：测试失败、实现不完整、遇到未解决的错误、找不到必要文件或依赖项

关键注意事项：
- 任务分解要具体、可操作
- 复杂任务分解为更小、可管理的步骤
- 使用清晰、描述性的任务名称
- 主动进行任务管理展示注意性
- 确保成功完成所有要求
"""

import os
import uuid
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field
import logging
from taiyi.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult,
    ToolRequestError
)

logger = logging.getLogger(__file__)


class TodoItem(BaseModel):
    """单个任务项的数据模型"""
    content: str = Field(..., min_length=1, description="任务内容")
    status: str = Field(..., description="任务状态: pending | in_progress | completed")
    priority: str = Field(..., description="任务优先级: high | medium | low")
    activeForm: str = Field(..., min_length=1, description="进行中时的描述，例如 'Adding unit tests...'")

    def validate_status(self) -> bool:
        """验证状态值"""
        return self.status in ("pending", "in_progress", "completed")
    
    def validate_priority(self) -> bool:
        """验证优先级值"""
        return self.priority in ("high", "medium", "low")


class TodoWriteTool(Tool):
    """TodoWrite工具类 - 继承自Tool基类，用于管理任务列表"""

    def __init__(
        self,
        task_dir: str | Path | None = None,  # 允许写入的地方，如果没有设置，存放在内存中
        max_items: int = 20  # 最大任务数限制
    ) -> None:
        super().__init__()
        self.max_items = max_items
        self.task_dir = Path(task_dir) if task_dir else None
        self.items: list[Dict[str, Any]] = []
        
        # 如果指定了任务目录，尝试加载已有任务
        if self.task_dir:
            self.task_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    @property
    def name(self) -> str:
        return "TodoWrite"

    @property
    def description(self) -> str:
        return """为当前编程会话创建和管理结构化任务列表。帮助跟踪进度、组织复杂任务、向用户展示工作周详性。

何时使用此工具（主动使用场景）：
1. 复杂多步任务 - 当任务需要3个或更多不同的步骤或操作时
2. 非平凡且复杂的任务 - 需要仔细规划或多个操作的任务
3. 用户明确要求任务列表 - 当用户直接要求使用任务列表时
4. 用户提供多个任务 - 当用户提供编号或逗号分隔的任务列表时
5. 收到新指令后 - 立即捕获用户需求作为任务
6. 开始执行任务时 - 在开始工作前标记为in_progress（理想情况同时只有一个in_progress任务）
7. 完成任务后 - 标记为已完成并添加在实施过程中发现的任何新跟进任务

何时不应使用此工具：
1. 只有一个简单、直接的任务
2. 任务琐碎且跟踪没有组织效益
3. 任务可以在少于3个简单步骤内完成
4. 任务纯属对话性或信息性

任务管理规则：
- 任务状态管理：pending（未开始）、in_progress（进行中，限制同时只有1个）、completed（成功完成）
- 实时更新任务状态，完成后立即标记，不要批量完成
- 一次只有1个in_progress任务，完成当前任务后再开始新任务
- 删除不再相关的任务
- 只有在完全完成任务时才标记为completed
- 遇到错误、阻塞或无法完成时保持为in_progress"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "任务内容（必需，最小长度1）"
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "任务状态（必需）"
                                },
                                "priority": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                    "description": "任务优先级（必需）"
                                },
                                "activeForm": {
                                    "type": "string",
                                    "description": "进行中时的描述，例如 'Adding unit tests...'"
                                }
                            },
                            "required": ["content", "status", "priority", "activeForm"]
                        },
                        "description": "更新的任务列表（必需）"
                    }
                },
                required=["todos"]
            ),
            strict=False
        )

    def _validate_todos(self, todos: list[Dict[str, Any]]) -> list[TodoItem]:
        """验证并解析任务列表"""
        validated = []
        in_progress_count = 0

        for i, todo_dict in enumerate(todos):
            try:
                # 使用 Pydantic 模型验证
                item = TodoItem(**todo_dict)
                
                # 额外验证
                if not item.validate_status():
                    raise ToolRequestError(
                        f"任务 {i}: 无效的状态 '{item.status}'，必须是 pending、in_progress 或 completed"
                    )
                
                if not item.validate_priority():
                    raise ToolRequestError(
                        f"任务 {i}: 无效的优先级 '{item.priority}'，必须是 high、medium 或 low"
                    )
                
                if item.status == "in_progress":
                    in_progress_count += 1
                
                validated.append(item)
                
            except Exception as e:
                raise ToolRequestError(f"任务 {i} 验证失败: {str(e)}")

        # 强制约束检查
        if len(validated) > self.max_items:
            raise ToolRequestError(f"任务数量超过最大限制 {self.max_items}")
        
        if in_progress_count > 1:
            raise ToolRequestError("同时只能有1个任务处于 in_progress 状态")

        return validated

    def _render_todos(self) -> str:
        """渲染任务列表为人类可读的文本格式"""
        if not self.items:
            return "暂无任务。"

        lines = []
        
        # 按优先级排序（high > medium > low）
        priority_order = {"high": 0, "medium": 1, "low": 2}
        sorted_items = sorted(
            self.items, 
            key=lambda x: (priority_order.get(x["priority"], 3), x["status"] != "in_progress")
        )
        
        for item in sorted_items:
            priority_symbol = {
                "high": "🔴",
                "medium": "🟡", 
                "low": "🟢"
            }.get(item["priority"], "⚪")
            
            if item["status"] == "completed":
                lines.append(f"[x] {priority_symbol} {item['content']}")
            elif item["status"] == "in_progress":
                lines.append(f"[>] {priority_symbol} {item['content']} <- {item['activeForm']}")
            else:
                lines.append(f"[ ] {priority_symbol} {item['content']}")

        # 统计信息
        completed = sum(1 for t in self.items if t["status"] == "completed")
        in_progress = sum(1 for t in self.items if t["status"] == "in_progress")
        
        lines.append(f"\n📊 进度: {completed}/{len(self.items)} 已完成")
        if in_progress:
            lines.append(f"⚙️  当前进行中: {in_progress} 个任务")

        return "\n".join(lines)

    def _save_to_disk(self) -> None:
        """将任务列表保存到磁盘"""
        if self.task_dir:
            task_file = self.task_dir / "todos.json"
            try:
                task_file.write_text(
                    json.dumps(self.items, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
            except Exception as e:
                logger.warning(f"保存任务列表到磁盘失败: {e}")

    def _load_from_disk(self) -> None:
        """从磁盘加载任务列表"""
        if self.task_dir:
            task_file = self.task_dir / "todos.json"
            if task_file.exists():
                try:
                    self.items = json.loads(task_file.read_text(encoding="utf-8"))
                    logger.info(f"从磁盘加载了 {len(self.items)} 个任务")
                except Exception as e:
                    logger.warning(f"从磁盘加载任务列表失败: {e}")

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行工具调用"""
        logger.info(
            f"执行工具 '{self.name}'，调用 ID: {tool_request.tool_call_id}"
        )
        
        start_time = time.time()
        
        try:
            # 解析参数
            if isinstance(tool_request.tool_call_arguments, str):
                args_dict = self.parse_string_arguments(tool_request.tool_call_arguments)
            else:
                args_dict = tool_request.tool_call_arguments
            
            # 提取 todos 列表
            todos = args_dict.get("todos", [])
            if not isinstance(todos, list):
                raise ToolRequestError("参数 'todos' 必须是一个列表")
            
            # 验证任务列表
            validated_items = self._validate_todos(todos)
            
            # 更新内存中的任务列表
            self.items = [item.model_dump() for item in validated_items]
            
            # 保存到磁盘（如果配置了）
            self._save_to_disk()
            
            # 渲染结果
            rendered = self._render_todos()
            
            execution_time = time.time() - start_time
            
            return ToolRequestResult(
                request=tool_request,
                result=self.items,
                content=rendered,
                is_error=False,
                execution_times=execution_time
            )
        
        except ToolRequestError as e:
            execution_time = time.time() - start_time
            logger.error(f"工具 '{self.name}' 执行失败: {e}")
            
            return ToolRequestResult(
                request=tool_request,
                result=None,
                content=str(e),
                is_error=True,
                execution_times=execution_time
            )
        
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"工具 '{self.name}' 执行出现未预期错误: {e}", exc_info=True)
            
            error_content = json.dumps({"error": str(e)}, ensure_ascii=False)
            
            return ToolRequestResult(
                request=tool_request,
                result=None,
                content=error_content,
                is_error=True,
                execution_times=execution_time
            )
