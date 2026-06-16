"""
Task 工具实现：TaskCreate / TaskList / TaskGet / TaskUpdate

参考 Claude Code 中 TaskCreateTool / TaskListTool / TaskGetTool /
TaskUpdateTool 的语义：通过工具来增删改查任务列表，便于 LLM 自主跟踪
多步任务的状态。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from norma.core.tool_types import (
    ParametersSchema,
    Tool,
    ToolRequest,
    ToolRequestError,
    ToolRequestResult,
    ToolSchema,
)
from norma.tasks import (
    Task,
    TaskStatus,
    TaskStore,
    get_default_store,
)

logger = logging.getLogger(__name__)


DEFAULT_LIST_ID = "default"


def _list_id_from_args(args: Dict[str, Any]) -> str:
    return str(args.get("list_id") or DEFAULT_LIST_ID)


def _task_to_summary(t: Task) -> Dict[str, Any]:
    return {
        "id": t.id,
        "subject": t.subject,
        "status": t.status.value if isinstance(t.status, TaskStatus) else t.status,
        "owner": t.owner or "",
        "blockedBy": list(t.blockedBy),
    }


def _ok(req: ToolRequest, payload: Any, started: float) -> ToolRequestResult:
    return ToolRequestResult(
        request=req,
        result=payload,
        content=json.dumps(payload, ensure_ascii=False, default=str),
        is_error=False,
        execution_times=time.time() - started,
    )


def _err(req: ToolRequest, msg: str, started: float) -> ToolRequestResult:
    payload = {"error": msg, "tool": req.tool_call_name}
    return ToolRequestResult(
        request=req,
        result=payload,
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        execution_times=time.time() - started,
    )


def _parse_args(req: ToolRequest, base_tool: Tool) -> Dict[str, Any]:
    if isinstance(req.tool_call_arguments, str):
        return base_tool.parse_string_arguments(req.tool_call_arguments)
    return dict(req.tool_call_arguments or {})


# ---------- TaskCreate ----------
class TaskCreateTool(Tool):
    """创建一个新任务"""

    def __init__(self, store: Optional[TaskStore] = None) -> None:
        self.store = store or get_default_store()

    @property
    def name(self) -> str:
        return "TaskCreate"

    @property
    def description(self) -> str:
        return (
            "为当前编程会话创建一个结构化任务，便于跟踪复杂多步工作。\n\n"
            "适合使用的场景：\n"
            "1. 用户给出三步以上、需要规划的任务\n"
            "2. 非平凡的实现工作，需要拆分步骤\n"
            "3. 收到新指令后立即记录\n"
            "4. 想要展示进度或对工作做组织\n\n"
            "字段：\n"
            "- subject: 任务标题（祈使句，例如 'Run tests'）\n"
            "- description: 详细描述\n"
            "- activeForm: 进行中的描述（例如 'Running tests'，可选）\n"
            "- metadata: 任意附加 KV（可选）\n"
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "subject": {"type": "string", "description": "任务标题"},
                    "description": {"type": "string", "description": "任务详细描述"},
                    "activeForm": {
                        "type": "string",
                        "description": "进行中描述，例如 'Running tests'",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "附加元数据键值对",
                        "additionalProperties": True,
                    },
                    "list_id": {
                        "type": "string",
                        "description": "可选：任务列表 id（多会话隔离）",
                    },
                },
                required=["subject", "description"],
            ),
            strict=False,
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        started = time.time()
        try:
            args = _parse_args(tool_request, self)
            subject = args.get("subject")
            description = args.get("description")
            if not subject or not description:
                return _err(tool_request, "subject and description are required", started)
            task = await self.store.create(
                list_id=_list_id_from_args(args),
                subject=str(subject),
                description=str(description),
                activeForm=args.get("activeForm"),
                metadata=args.get("metadata") or {},
            )
            return _ok(
                tool_request,
                {"created": task.to_dict(), "message": f"Task #{task.id} created"},
                started,
            )
        except Exception as exc:
            logger.exception("TaskCreate failed")
            return _err(tool_request, str(exc), started)


# ---------- TaskList ----------
class TaskListTool(Tool):
    """列出所有任务（摘要）"""

    def __init__(self, store: Optional[TaskStore] = None) -> None:
        self.store = store or get_default_store()

    @property
    def name(self) -> str:
        return "TaskList"

    @property
    def description(self) -> str:
        return (
            "列出当前会话的所有任务摘要，包括 id / subject / status / owner / blockedBy。\n"
            "用于查找下一个可执行的任务（status=pending 且 blockedBy 为空），或检查整体进度。"
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "list_id": {
                        "type": "string",
                        "description": "可选：任务列表 id",
                    },
                },
                required=[],
            ),
            strict=False,
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        started = time.time()
        try:
            args = _parse_args(tool_request, self)
            tasks = await self.store.list(_list_id_from_args(args))
            payload = {"tasks": [_task_to_summary(t) for t in tasks], "count": len(tasks)}
            return _ok(tool_request, payload, started)
        except Exception as exc:
            logger.exception("TaskList failed")
            return _err(tool_request, str(exc), started)


# ---------- TaskGet ----------
class TaskGetTool(Tool):
    """获取单个任务的全部字段"""

    def __init__(self, store: Optional[TaskStore] = None) -> None:
        self.store = store or get_default_store()

    @property
    def name(self) -> str:
        return "TaskGet"

    @property
    def description(self) -> str:
        return (
            "根据 id 获取任务的完整信息：subject / description / status / "
            "activeForm / owner / blocks / blockedBy / metadata。"
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "taskId": {"type": "string", "description": "任务 id"},
                    "list_id": {
                        "type": "string",
                        "description": "可选：任务列表 id",
                    },
                },
                required=["taskId"],
            ),
            strict=False,
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        started = time.time()
        try:
            args = _parse_args(tool_request, self)
            task_id = args.get("taskId")
            if not task_id:
                return _err(tool_request, "taskId is required", started)
            task = await self.store.get(_list_id_from_args(args), str(task_id))
            if task is None:
                return _err(tool_request, f"task {task_id} not found", started)
            return _ok(tool_request, task.to_dict(), started)
        except Exception as exc:
            logger.exception("TaskGet failed")
            return _err(tool_request, str(exc), started)


# ---------- TaskUpdate ----------
class TaskUpdateTool(Tool):
    """更新任务的状态/字段，或建立 blocks/blockedBy 依赖"""

    def __init__(self, store: Optional[TaskStore] = None) -> None:
        self.store = store or get_default_store()

    @property
    def name(self) -> str:
        return "TaskUpdate"

    @property
    def description(self) -> str:
        return (
            "更新已有任务。常见用途：\n"
            "- 开始工作时：status=in_progress\n"
            "- 完成工作时：status=completed\n"
            "- 删除任务：status=deleted\n"
            "- 修改字段：subject / description / activeForm / owner / metadata\n"
            "- 依赖：addBlocks / addBlockedBy 列表，互为镜像\n"
            "状态流：pending -> in_progress -> completed。\n"
            "永远只在工作真正完成后才标记 completed。"
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "taskId": {"type": "string", "description": "任务 id"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "deleted"],
                    },
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "activeForm": {"type": "string"},
                    "owner": {"type": "string"},
                    "metadata": {
                        "type": "object",
                        "description": "merge 进 metadata；值为 null 时删除该键",
                        "additionalProperties": True,
                    },
                    "addBlocks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标记当前任务阻塞的其它任务 id",
                    },
                    "addBlockedBy": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标记阻塞当前任务的其它任务 id",
                    },
                    "list_id": {"type": "string"},
                },
                required=["taskId"],
            ),
            strict=False,
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        started = time.time()
        try:
            args = _parse_args(tool_request, self)
            task_id = args.get("taskId")
            if not task_id:
                return _err(tool_request, "taskId is required", started)
            list_id = _list_id_from_args(args)

            status_arg = args.get("status")
            if status_arg == "deleted":
                ok = await self.store.delete(list_id, str(task_id))
                if not ok:
                    return _err(tool_request, f"task {task_id} not found", started)
                return _ok(
                    tool_request,
                    {"deleted": True, "id": str(task_id)},
                    started,
                )

            status: Optional[TaskStatus] = None
            if status_arg is not None:
                try:
                    status = TaskStatus(status_arg)
                except ValueError:
                    return _err(tool_request, f"invalid status '{status_arg}'", started)

            task = await self.store.update(
                list_id=list_id,
                task_id=str(task_id),
                status=status,
                subject=args.get("subject"),
                description=args.get("description"),
                activeForm=args.get("activeForm"),
                owner=args.get("owner"),
                metadata=args.get("metadata"),
                addBlocks=args.get("addBlocks"),
                addBlockedBy=args.get("addBlockedBy"),
            )
            if task is None:
                return _err(tool_request, f"task {task_id} not found", started)
            return _ok(tool_request, {"updated": task.to_dict()}, started)
        except Exception as exc:
            logger.exception("TaskUpdate failed")
            return _err(tool_request, str(exc), started)
