"""系统提示结构化回归测试（headless）：CLAUDE.md 注入 + 环境段 + 截断。

验证 SystemPromptService.get_claude_code_system_prompt：
- 始终包含核心指令 + 环境段（cwd / 平台）
- 项目级 CLAUDE.md（cwd 及祖先目录）被注入，标注「项目级」
- 用户级 ~/.norma/CLAUDE.md 被注入，标注「用户级」
- 项目级排在用户级之后（更优先）
- 无 CLAUDE.md 时不输出「项目记忆」段
- 超长文件被截断

运行：``python -m norma.prompt.test_system_prompt``
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.prompt.system_prompt import SystemPromptService  # noqa: E402


def test_env_section_always_present() -> None:
    with tempfile.TemporaryDirectory() as d:
        prompt = SystemPromptService.get_claude_code_system_prompt(cwd=d)
        assert "# 当前任务环境" in prompt, "应包含环境段"
        assert d in prompt, "cwd 应出现在环境段"
        assert "运行平台" in prompt, "应包含平台信息"
        assert "项目记忆" not in prompt, "无 CLAUDE.md 时不应有项目记忆段"
    print("[PASS] env section present, no claude.md section when absent")


def test_project_claude_md_injected() -> None:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "CLAUDE.md").write_text("项目专属指令：使用 pytest。", encoding="utf-8")
        prompt = SystemPromptService.get_claude_code_system_prompt(cwd=d)
        assert "# 项目记忆 (CLAUDE.md)" in prompt
        assert "项目级" in prompt
        assert "使用 pytest" in prompt, "项目 CLAUDE.md 内容应被注入"
    print("[PASS] project CLAUDE.md injected")


def test_parent_dir_claude_md_walked() -> None:
    with tempfile.TemporaryDirectory() as root:
        parent = Path(root) / "repo"
        child = parent / "pkg"
        child.mkdir(parents=True)
        (parent / "CLAUDE.md").write_text("仓库级指令：monorepo 约定。", encoding="utf-8")
        (child / "CLAUDE.md").write_text("包级指令：仅本包。", encoding="utf-8")
        prompt = SystemPromptService.get_claude_code_system_prompt(cwd=str(child))
        assert "monorepo 约定" in prompt, "祖先目录 CLAUDE.md 应被收集"
        assert "仅本包" in prompt, "当前目录 CLAUDE.md 应被收集"
        # 内层（更具体）应排在外层之后
        assert prompt.index("仅本包") > prompt.index("monorepo 约定"), \
            "更近的目录 CLAUDE.md 应排在更远的之后（更优先）"
    print("[PASS] parent-dir CLAUDE.md walked + ordered")


def test_user_level_claude_md() -> None:
    # 临时覆盖 USERPROFILE/HOME 指向一个临时家目录
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
        (Path(home) / ".norma").mkdir()
        (Path(home) / ".norma" / "CLAUDE.md").write_text("用户全局指令：用中文回复。", encoding="utf-8")
        saved = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
        os.environ["USERPROFILE"] = home
        os.environ["HOME"] = home
        try:
            prompt = SystemPromptService.get_claude_code_system_prompt(cwd=d)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        assert "用户级" in prompt
        assert "用中文回复" in prompt, "用户级 CLAUDE.md 应被注入"
        assert "项目记忆" in prompt
    print("[PASS] user-level CLAUDE.md injected")


def test_priority_project_after_user() -> None:
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
        (Path(home) / ".norma").mkdir()
        (Path(home) / ".norma" / "CLAUDE.md").write_text("USER_MARKER", encoding="utf-8")
        (Path(d) / "CLAUDE.md").write_text("PROJECT_MARKER", encoding="utf-8")
        saved = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
        os.environ["USERPROFILE"] = home
        os.environ["HOME"] = home
        try:
            prompt = SystemPromptService.get_claude_code_system_prompt(cwd=d)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        assert prompt.index("PROJECT_MARKER") > prompt.index("USER_MARKER"), \
            "项目级应排在用户级之后（更优先）"
    print("[PASS] project-level ordered after user-level")


def test_truncation() -> None:
    with tempfile.TemporaryDirectory() as d:
        huge = "X" * (SystemPromptService._MAX_CLAUDE_MD_BYTES + 5000)
        (Path(d) / "CLAUDE.md").write_text(huge, encoding="utf-8")
        prompt = SystemPromptService.get_claude_code_system_prompt(cwd=d)
        assert "已截断" in prompt, "超长 CLAUDE.md 应被截断并标注"
    print("[PASS] oversized CLAUDE.md truncated")


def main() -> int:
    failures = 0
    for fn in (
        test_env_section_always_present,
        test_project_claude_md_injected,
        test_parent_dir_claude_md_walked,
        test_user_level_claude_md,
        test_priority_project_after_user,
        test_truncation,
    ):
        try:
            fn()
        except AssertionError as exc:
            print(f"[FAIL] {fn.__name__}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[ERROR] {fn.__name__}: {exc}")
            traceback.print_exc()
            failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL system-prompt tests passed")
    return 0


def test_system_prompt_headless() -> None:
    """pytest 入口"""
    assert main() == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(main())
