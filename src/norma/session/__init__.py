"""
Session 系统：会话持久化与历史会话管理。

启动 CLI 或 SDK 时分配一个 session_id；同一会话下所有用户输入与 agent 输出
均以 jsonl 格式写入到 ``~/.norma/projects/<sanitized_cwd>/<session_id>.jsonl``。
"""

from norma.session.session import (
    SessionManager,
    SessionRecord,
    SessionMeta,
    sanitize_path,
    get_config_home,
    get_projects_dir,
    get_project_dir,
)

__all__ = [
    "SessionManager",
    "SessionRecord",
    "SessionMeta",
    "sanitize_path",
    "get_config_home",
    "get_projects_dir",
    "get_project_dir",
]
