"""
Session 系统

管理会话的持久化、加载和列表展示。

设计参考 claude-code 的 ``utils/sessionStorage.ts``：

- 每个会话由一个 ``session_id`` 唯一标识；
- 同一个 ``project`` (基于 ``cwd`` 的清洗后名字) 下的所有会话保存在
  ``<config_home>/projects/<sanitized_cwd>/<session_id>.jsonl``；
- 会话内容以 jsonl 格式追加：每行是一个事件 (user_input / assistant /
  tool_message / meta)；
- ``SessionManager.list_sessions()`` 列出当前项目的所有会话，
  ``SessionManager.load(session_id)`` 加载历史 LLM messages 并恢复内存。

日志格式 (每行 JSON)::

    {"ts": "<iso>", "type": "user", "content": "..."}
    {"ts": "<iso>", "type": "assistant", "content": "...", "tool_calls": [...] }
    {"ts": "<iso>", "type": "tool", "tool_call_id": "...", "tool_name": "...",
     "content": "...", "is_error": false }
    {"ts": "<iso>", "type": "system", "content": "..."}
    {"ts": "<iso>", "type": "meta", "key": "title", "value": "..." }
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ====================== 路径与命名 ======================

DEFAULT_CONFIG_HOME = Path.home() / ".norma"


def get_config_home() -> Path:
    """返回 norma 配置根目录，可由 NORMA_CONFIG_HOME 环境变量覆盖"""
    env = os.environ.get("NORMA_CONFIG_HOME")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG_HOME


def get_projects_dir() -> Path:
    return get_config_home() / "projects"


def sanitize_path(path: str | Path) -> str:
    """将 cwd 转成可作为目录名的字符串

    与 claude-code sanitizePath 等价：移除特殊字符，使用 ``-`` 连接路径段。
    """
    p = str(Path(path).expanduser().resolve())
    # 去掉前导 /（POSIX）或 C:\（Windows）
    p = re.sub(r"^[A-Za-z]:[\\/]", "", p)
    p = p.lstrip(os.sep)
    p = p.replace("\\", "-").replace("/", "-")
    p = re.sub(r"[^A-Za-z0-9._-]", "-", p)
    p = re.sub(r"-+", "-", p)
    return p.strip("-") or "root"


def get_project_dir(cwd: str | Path) -> Path:
    return get_projects_dir() / sanitize_path(cwd)


# ====================== 数据模型 ======================

@dataclass
class SessionMeta:
    """会话元信息（用于 ``/resume`` 列表展示）"""

    session_id: str
    project: str
    cwd: str
    created_at: str
    updated_at: str
    title: str = ""
    message_count: int = 0
    file_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project": self.project,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "message_count": self.message_count,
        }


@dataclass
class SessionRecord:
    """正在使用中的会话记录（持有文件句柄）"""

    session_id: str
    cwd: str
    file_path: Path
    meta: SessionMeta
    _fp: Any = None  # 文件句柄

    def append(self, entry: Dict[str, Any]) -> None:
        if "ts" not in entry:
            entry["ts"] = datetime.now().isoformat()
        line = json.dumps(entry, ensure_ascii=False)
        try:
            if self._fp is None:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                self._fp = open(self.file_path, "a", encoding="utf-8")
            self._fp.write(line + "\n")
            self._fp.flush()
            self.meta.updated_at = entry["ts"]
            if entry.get("type") in ("user", "assistant", "tool"):
                self.meta.message_count += 1
        except Exception as e:
            logger.warning(f"写入 session 文件失败 {self.file_path}: {e}")

    def close(self) -> None:
        if self._fp is not None:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None


# ====================== Manager ======================

class SessionManager:
    """会话管理器

    主要职责：

    - 创建新 session 并写入磁盘；
    - 加载已有 session（仅恢复 LLM messages）；
    - 列出当前项目的所有 session 用于 ``/resume``；
    - 透明地把 user / assistant / tool 消息写入 session 文件。
    """

    def __init__(self, cwd: str | Path):
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.project_dir = get_project_dir(self.cwd)
        self.current: Optional[SessionRecord] = None

    # ---------- 创建 / 加载 ----------

    def create(
        self,
        session_id: Optional[str] = None,
        title: str = "",
    ) -> SessionRecord:
        """创建一个新会话"""
        sid = session_id or self._gen_session_id()
        now = datetime.now().isoformat()
        meta = SessionMeta(
            session_id=sid,
            project=sanitize_path(self.cwd),
            cwd=self.cwd,
            created_at=now,
            updated_at=now,
            title=title,
        )
        path = self.project_dir / f"{sid}.jsonl"
        record = SessionRecord(
            session_id=sid, cwd=self.cwd, file_path=path, meta=meta
        )
        record.append({
            "type": "meta",
            "key": "create",
            "value": {
                "session_id": sid,
                "cwd": self.cwd,
                "created_at": now,
                "title": title,
            },
        })
        meta.file_path = str(path)
        self.current = record
        return record

    def load(self, session_id: str) -> Optional[SessionRecord]:
        """加载已存在的 session（不重新读取消息，只用于追加写）"""
        path = self.project_dir / f"{session_id}.jsonl"
        if not path.exists():
            return None
        meta = self._read_meta(path) or SessionMeta(
            session_id=session_id,
            project=sanitize_path(self.cwd),
            cwd=self.cwd,
            created_at=datetime.fromtimestamp(path.stat().st_ctime).isoformat(),
            updated_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        )
        meta.file_path = str(path)
        record = SessionRecord(
            session_id=session_id, cwd=self.cwd, file_path=path, meta=meta
        )
        record.append({"type": "meta", "key": "resume",
                       "value": {"resumed_at": datetime.now().isoformat()}})
        self.current = record
        return record

    def replay_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """读取 session 文件，返回原始 entries 用于恢复 memory"""
        path = self.project_dir / f"{session_id}.jsonl"
        entries: List[Dict[str, Any]] = []
        if not path.exists():
            return entries
        try:
            with open(path, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"读取 session 失败 {path}: {e}")
        return entries

    # ---------- 列表 ----------

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        if not self.project_dir.exists():
            return []
        metas: List[SessionMeta] = []
        for f in self.project_dir.glob("*.jsonl"):
            meta = self._read_meta(f)
            if meta is not None:
                metas.append(meta)
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas[:limit]

    # ---------- 写入 helper ----------

    def append(self, entry: Dict[str, Any]) -> None:
        if self.current is None:
            return
        self.current.append(entry)

    def close(self) -> None:
        if self.current is not None:
            self.current.append({
                "type": "meta", "key": "close",
                "value": {"closed_at": datetime.now().isoformat()},
            })
            self.current.close()

    # ---------- 内部 ----------

    @staticmethod
    def _gen_session_id() -> str:
        return uuid.uuid4().hex[:12]

    def _read_meta(self, path: Path) -> Optional[SessionMeta]:
        """从 jsonl 文件读取元信息（扫描首行 meta:create 与最后一行）"""
        try:
            session_id = path.stem
            created_at = datetime.fromtimestamp(path.stat().st_ctime).isoformat()
            updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
            title = ""
            cwd = self.cwd
            count = 0
            first_user = ""
            with open(path, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    t = e.get("type")
                    if t == "meta" and e.get("key") == "create":
                        v = e.get("value", {})
                        created_at = v.get("created_at", created_at)
                        title = v.get("title", title)
                        cwd = v.get("cwd", cwd)
                    elif t == "meta" and e.get("key") == "title":
                        title = e.get("value", title)
                    elif t in ("user", "assistant", "tool"):
                        count += 1
                        if t == "user" and not first_user:
                            first_user = (e.get("content") or "")[:60]
            if not title and first_user:
                title = first_user
            return SessionMeta(
                session_id=session_id,
                project=sanitize_path(cwd),
                cwd=cwd,
                created_at=created_at,
                updated_at=updated_at,
                title=title,
                message_count=count,
                file_path=str(path),
            )
        except Exception as e:
            logger.debug(f"_read_meta 失败 {path}: {e}")
            return None
