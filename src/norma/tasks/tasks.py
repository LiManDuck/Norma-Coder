"""
Task 存储与模型

Claude Code 风格的任务管理：每个任务有 id / subject / description /
activeForm / status / owner / blocks / blockedBy / metadata。

存储策略
--------
- 每个 conversation 一个 json 文件： ``<dir>/<conversation_id>.json``
- 默认路径： ``~/.norma/tasks/``
- 进程内单例 ``TaskStore`` 提供读写接口；并发安全通过 asyncio.Lock 保证
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class Task:
    id: str
    subject: str
    description: str
    activeForm: Optional[str] = None
    owner: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    blocks: List[str] = field(default_factory=list)
    blockedBy: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, TaskStatus) else self.status
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        status = data.get("status", TaskStatus.PENDING)
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except ValueError:
                status = TaskStatus.PENDING
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            subject=str(data.get("subject", "")),
            description=str(data.get("description", "")),
            activeForm=data.get("activeForm"),
            owner=data.get("owner"),
            status=status,
            blocks=list(data.get("blocks") or []),
            blockedBy=list(data.get("blockedBy") or []),
            metadata=dict(data.get("metadata") or {}),
        )


class TaskStore:
    """基于文件的简单任务存储"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path.home() / ".norma" / "tasks"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._next_id = 1

    # ---------- 文件路径 ----------
    def _path(self, list_id: str) -> Path:
        safe = "".join(c for c in list_id if c.isalnum() or c in ("-", "_"))
        return self.base_dir / f"{safe or 'default'}.json"

    # ---------- IO ----------
    def _load(self, list_id: str) -> List[Task]:
        p = self._path(list_id)
        if not p.exists():
            return []
        try:
            with p.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        return [Task.from_dict(item) for item in raw or []]

    def _save(self, list_id: str, tasks: List[Task]) -> None:
        p = self._path(list_id)
        with p.open("w", encoding="utf-8") as f:
            json.dump([t.to_dict() for t in tasks], f, ensure_ascii=False, indent=2)

    # ---------- API ----------
    async def list(self, list_id: str) -> List[Task]:
        async with self._lock:
            return self._load(list_id)

    async def get(self, list_id: str, task_id: str) -> Optional[Task]:
        async with self._lock:
            for t in self._load(list_id):
                if t.id == task_id:
                    return t
            return None

    async def create(
        self,
        list_id: str,
        subject: str,
        description: str,
        activeForm: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        async with self._lock:
            tasks = self._load(list_id)
            new_id = self._allocate_id(tasks)
            task = Task(
                id=new_id,
                subject=subject,
                description=description,
                activeForm=activeForm,
                metadata=dict(metadata or {}),
            )
            tasks.append(task)
            self._save(list_id, tasks)
            return task

    async def update(
        self,
        list_id: str,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        activeForm: Optional[str] = None,
        owner: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        addBlocks: Optional[List[str]] = None,
        addBlockedBy: Optional[List[str]] = None,
    ) -> Optional[Task]:
        async with self._lock:
            tasks = self._load(list_id)
            target: Optional[Task] = None
            for t in tasks:
                if t.id == task_id:
                    target = t
                    break
            if target is None:
                return None

            if status is not None:
                if status == "deleted":  # support sentinel
                    tasks.remove(target)
                    self._save(list_id, tasks)
                    return target
                target.status = status if isinstance(status, TaskStatus) else TaskStatus(status)
            if subject is not None:
                target.subject = subject
            if description is not None:
                target.description = description
            if activeForm is not None:
                target.activeForm = activeForm
            if owner is not None:
                target.owner = owner
            if metadata is not None:
                # merge; None means delete
                for k, v in metadata.items():
                    if v is None:
                        target.metadata.pop(k, None)
                    else:
                        target.metadata[k] = v
            if addBlocks:
                for b in addBlocks:
                    if b not in target.blocks:
                        target.blocks.append(b)
                    # mirror on the other side
                    other = next((x for x in tasks if x.id == b), None)
                    if other and target.id not in other.blockedBy:
                        other.blockedBy.append(target.id)
            if addBlockedBy:
                for b in addBlockedBy:
                    if b not in target.blockedBy:
                        target.blockedBy.append(b)
                    other = next((x for x in tasks if x.id == b), None)
                    if other and target.id not in other.blocks:
                        other.blocks.append(target.id)

            self._save(list_id, tasks)
            return target

    async def delete(self, list_id: str, task_id: str) -> bool:
        async with self._lock:
            tasks = self._load(list_id)
            new_tasks = [t for t in tasks if t.id != task_id]
            if len(new_tasks) == len(tasks):
                return False
            # cleanup back-references
            for t in new_tasks:
                t.blocks = [x for x in t.blocks if x != task_id]
                t.blockedBy = [x for x in t.blockedBy if x != task_id]
            self._save(list_id, new_tasks)
            return True

    # ---------- 辅助 ----------
    def _allocate_id(self, tasks: List[Task]) -> str:
        used = {t.id for t in tasks}
        n = max((int(t.id) for t in tasks if t.id.isdigit()), default=0) + 1
        while str(n) in used:
            n += 1
        return str(n)


_default_store: Optional[TaskStore] = None


def get_default_store() -> TaskStore:
    """获取进程内默认 TaskStore（懒加载）"""
    global _default_store
    if _default_store is None:
        _default_store = TaskStore()
    return _default_store
