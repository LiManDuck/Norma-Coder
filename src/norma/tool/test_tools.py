"""核心文件工具回归测试（headless，真实文件系统临时目录）。

验证 Read/Write/Edit/Ls/Glob/Grep/Bash/Task 工具的真实行为--这些是
codeagent 的核心能力，此前仅 SkillTool/AgentTool/MCP 有回归覆盖。

运行：``python -m norma.tool.test_tools``
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path


def _req(name: str, args: dict):
    from norma.core.tool_types import ToolRequest

    return ToolRequest(
        tool_call_id="c1", tool_call_name=name, tool_call_arguments=args
    )


async def _run(tool, name: str, args: dict):
    return await tool.execute(_req(name, args))


# =====================================================================
# Write / Read / Edit
# =====================================================================

async def test_write_read_edit() -> bool:
    from norma.tool.write_tool.write_tool import WriteTool
    from norma.tool.read_tool.read_tool import ReadTool
    from norma.tool.edit_tool.edit_tool import EditTool

    # 共享「已读文件」集合：Write/Read 记录、Edit 校验（与 NormaCoder 集成一致）
    registry: set = set()
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.txt")

        # Write -> 落盘内容正确（同时标记为已读）
        w = await _run(WriteTool(read_files_registry=registry), "Write",
                       {"file_path": f, "content": "hello\nworld\n"})
        if w.is_error or Path(f).read_text(encoding="utf-8") != "hello\nworld\n":
            return False

        # Read -> 内容含 hello/world
        r = await _run(ReadTool(read_files_registry=registry), "Read", {"file_path": f})
        if r.is_error or "hello" not in r.content or "world" not in r.content:
            return False

        # Read 缺失文件 -> is_error
        rm = await _run(ReadTool(read_files_registry=registry), "Read",
                        {"file_path": os.path.join(d, "nope.txt")})
        if not rm.is_error:
            return False

        # Edit 唯一匹配 -> 落盘更新（Write 已标记已读，校验通过）
        e = await _run(EditTool(readed_files=registry), "Edit",
                       {"file_path": f, "old_string": "world", "new_string": "WORLD"})
        if e.is_error or Path(f).read_text(encoding="utf-8") != "hello\nWORLD\n":
            return False

        # Edit 非唯一且无 replace_all -> 失败
        Path(f).write_text("x\nx\n", encoding="utf-8")
        e2 = await _run(EditTool(readed_files=registry), "Edit",
                        {"file_path": f, "old_string": "x", "new_string": "y"})
        if not e2.is_error:
            return False

        # Edit 非唯一 + replace_all -> 全部替换
        e3 = await _run(EditTool(readed_files=registry), "Edit",
                        {"file_path": f, "old_string": "x", "new_string": "y", "replace_all": True})
        if e3.is_error or Path(f).read_text(encoding="utf-8") != "y\ny\n":
            return False

    return True


async def test_edit_gate_rejects_unread_file() -> bool:
    """「先读后编」门禁安全契约：未读文件直接 Edit 必须被拒绝。

    此前仅测了正向（Write 标记已读 -> Edit 成功），未覆盖负向安全路径--
    若 is_file_read / registry 接线被破坏，安全门禁失效而现有用例不会报警。
    另测 Write->Edit 跨工具路径一致性（两者须用同一路径规范化，否则 symlinked
    cwd 下会误判）。
    """
    from norma.tool.write_tool.write_tool import WriteTool
    from norma.tool.read_tool.read_tool import ReadTool
    from norma.tool.edit_tool.edit_tool import EditTool

    registry: set = set()
    with tempfile.TemporaryDirectory() as d:
        # 预置一个文件，但不经 Read/Write 触发标记
        f = os.path.join(d, "unread.txt")
        Path(f).write_text("payload\n", encoding="utf-8")

        edit = EditTool(readed_files=registry)
        # registry 为空 -> 必须拒绝
        r = await _run(edit, "Edit",
                       {"file_path": f, "old_string": "payload", "new_string": "P"})
        if not r.is_error:
            return False
        if "read" not in r.content.lower():
            return False

        # Read 之后 -> 放行（Read 用 resolve 标记，Edit 用 resolve 检查，须一致）
        await _run(ReadTool(read_files_registry=registry), "Read", {"file_path": f})
        r2 = await _run(edit, "Edit",
                        {"file_path": f, "old_string": "payload", "new_string": "P"})
        if r2.is_error or Path(f).read_text(encoding="utf-8") != "P\n":
            return False

    return True


# =====================================================================
# Ls / Glob / Grep
# =====================================================================

async def test_ls_glob_grep() -> bool:
    from norma.tool.ls_tool.ls_tool import LsTool
    from norma.tool.glob_tool.glob_tool import GlobTool
    from norma.tool.grep_tool.grep_tool import GrepTool

    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.py").write_text("print('alpha')\n", encoding="utf-8")
        Path(d, "b.txt").write_text("beta BETA\n", encoding="utf-8")
        (Path(d, "sub")).mkdir()

        ls = await _run(LsTool(cwd=d), "Ls", {"path": d})
        if ls.is_error or "a.py" not in ls.content or "b.txt" not in ls.content:
            return False

        g = await _run(GlobTool(cwd=d), "Glob", {"pattern": "*.py"})
        if g.is_error or "a.py" not in g.content or "b.txt" in g.content:
            return False

        gr = await _run(GrepTool(), "Grep", {"pattern": "beta", "path": d})
        if gr.is_error or "b.txt" not in gr.content:
            return False

    return True


# =====================================================================
# Bash
# =====================================================================

async def test_bash() -> bool:
    from norma.tool.bash_tool.bash_tool import BashTool

    with tempfile.TemporaryDirectory() as d:
        b = await _run(BashTool(cwd=d), "Bash", {"command": "echo normarocks", "timeout": 8000})
        if b.is_error or "normarocks" not in b.content:
            return False

        # 空命令 -> is_error
        b2 = await _run(BashTool(cwd=d), "Bash", {"command": ""})
        if not b2.is_error:
            return False

    return True


# =====================================================================
# Task 生命周期
# =====================================================================

async def test_task_lifecycle() -> bool:
    from norma.tool.task_tool.task_tools import (
        TaskCreateTool, TaskListTool, TaskGetTool, TaskUpdateTool,
    )

    lid = "regr-tools"  # 专属 list_id，避免污染默认列表
    create = await _run(TaskCreateTool(), "TaskCreate",
                        {"subject": "T1", "description": "do thing", "list_id": lid})
    if create.is_error:
        return False
    tid = json.loads(create.content)["created"]["id"]

    lst = await _run(TaskListTool(), "TaskList", {"list_id": lid})
    if lst.is_error or "T1" not in lst.content:
        return False

    get = await _run(TaskGetTool(), "TaskGet", {"taskId": str(tid), "list_id": lid})
    if get.is_error or "T1" not in get.content:
        return False

    upd = await _run(TaskUpdateTool(), "TaskUpdate",
                     {"taskId": str(tid), "status": "completed", "list_id": lid})
    if upd.is_error:
        return False

    get2 = await _run(TaskGetTool(), "TaskGet", {"taskId": str(tid), "list_id": lid})
    if get2.is_error or "completed" not in get2.content:
        return False

    return True


# =====================================================================
# 工具执行错误结果（tool_core.execute_tool 异常兜底）
# =====================================================================

async def test_execute_tool_error_content_is_valid_json() -> bool:
    """execute_tool 捕获工具异常时，content 必须是合法 JSON。

    此前用 f-string ``f'{{"error": "{str(e)}"}}'`` 拼接，异常消息含反斜杠
    （Windows 路径 C:\\Users\\...）、双引号或换行时会产出非法 JSON，违反
    content 是 JSON 字符串的隐式契约（MCPTool/task_tools/AgentTool 均用
    json.dumps）。本用例用含这三类字符的异常消息验证修复。
    """
    from norma.core.tool_types import Tool, ToolRequest, ToolRequestResult
    from norma.tool.tool_core import NormaArtifact

    class _ExplodingTool(Tool):
        @property
        def name(self) -> str:
            return "Explode"

        @property
        def description(self) -> str:
            return "raises"

        async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
            # 含反斜杠（Windows 路径）、双引号、换行--f-string 拼接会全部破坏 JSON
            raise RuntimeError('boom at "C:\\Users\\admin" path\nsecond line')

    mgr = NormaArtifact(tools=[_ExplodingTool()])
    res = await mgr.execute_tool(
        ToolRequest(tool_call_id="x1", tool_call_name="Explode",
                    tool_call_arguments={})
    )
    if not res.is_error:
        return False
    # content 必须是可解析的合法 JSON（修复前在此抛 JSONDecodeError）
    try:
        parsed = json.loads(res.content)
    except json.JSONDecodeError:
        return False
    # 错误消息完整往返（反斜杠/引号/换行不被破坏）
    if parsed.get("error") != 'boom at "C:\\Users\\admin" path\nsecond line':
        return False

    # 未知工具分支同样须产出合法 JSON
    res2 = await mgr.execute_tool(
        ToolRequest(tool_call_id="x2", tool_call_name="Nope",
                    tool_call_arguments={})
    )
    if not res2.is_error:
        return False
    try:
        json.loads(res2.content)
    except json.JSONDecodeError:
        return False
    return True


# =====================================================================
# Edit 原子写入
# =====================================================================

async def test_edit_atomic_preserves_original_on_failure() -> bool:
    """Edit 写入须原子：写入失败时原文件不得被截断/丢失。

    此前 EditTool 直接 ``open(file, 'w')`` 写目标文件，写入中途崩溃（进程
    中断/OOM/磁盘满）会留下半截文件，对代码编辑工具而言是严重的数据丢失。
    改为临时文件 + ``os.replace`` 后，失败时原文件完整保留。本用例模拟
    ``os.replace`` 失败，断言原文件内容不变、无残留临时文件、且工具报错。
    """
    from norma.tool.edit_tool.edit_tool import EditTool

    registry: set = set()
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "src.txt")
        original = "line1\nline2\nline3\n"
        Path(f).write_text(original, encoding="utf-8")
        registry.add(str(Path(f).resolve()))

        edit = EditTool(readed_files=registry)
        # 模拟 os.replace 失败（磁盘满/权限等），验证原文件不被破坏
        real_replace = os.replace
        def _boom(*a, **k):  # noqa: ANN001
            raise OSError("simulated replace failure")
        os.replace = _boom
        try:
            r = await _run(edit, "Edit", {
                "file_path": f, "old_string": "line2", "new_string": "LINE2",
            })
        finally:
            os.replace = real_replace

        # 工具须报错
        if not r.is_error:
            return False
        # 原文件必须完整保留（未被截断）
        if Path(f).read_text(encoding="utf-8") != original:
            return False
        # 无残留临时文件
        leftovers = [p for p in os.listdir(d) if p.startswith(".tmp_edit_")]
        if leftovers:
            return False
    return True


# =====================================================================
# 入口
# =====================================================================

async def _amain() -> int:
    tests = [
        ("write_read_edit", test_write_read_edit),
        ("edit_gate_rejects_unread_file", test_edit_gate_rejects_unread_file),
        ("ls_glob_grep", test_ls_glob_grep),
        ("bash", test_bash),
        ("task_lifecycle", test_task_lifecycle),
        ("execute_tool_error_content_is_valid_json",
         test_execute_tool_error_content_is_valid_json),
        ("edit_atomic_preserves_original_on_failure",
         test_edit_atomic_preserves_original_on_failure),
    ]
    failures = 0
    for name, fn in tests:
        try:
            ok = await fn()
            assert ok, f"{name} returned False"
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_tools_headless() -> None:
    """pytest 入口（若安装 pytest）。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
