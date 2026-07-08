"""系统提示构建。

结构化拼装：
1. 核心指令（``claude_code_system_prompt.md`` 静态文件）
2. 当前任务环境（cwd / 平台）
3. 项目记忆 CLAUDE.md（用户级 ``~/.norma/CLAUDE.md`` + 项目级，自 cwd 向上遍历祖先目录）

CLAUDE.md 是 codeagent 的核心能力：项目特定的指令与约定通过它注入系统提示，
优先级 项目级 > 用户级（更近的目录覆盖更远的）。
"""

import os
import sys
from pathlib import Path


class SystemPromptService:
    """系统提示服务"""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------
    # 公共入口
    # ------------------------------------------------
    @staticmethod
    def get_claude_code_system_prompt(cwd: str | None = None) -> str:
        parts: list[str] = []

        # 1. 核心指令
        base_file = Path(os.path.abspath(__file__)).parent / "claude_code_system_prompt.md"
        parts.append(base_file.read_text(encoding="utf-8"))

        # 2. 当前任务环境
        parts.append(SystemPromptService._env_section(cwd))

        # 3. 项目记忆 CLAUDE.md（用户级 + 项目级）
        claude_md = SystemPromptService._collect_claude_md(cwd)
        if claude_md:
            parts.append(claude_md)

        return "\n".join(parts)

    # ------------------------------------------------
    # 环境段
    # ------------------------------------------------
    @staticmethod
    def _env_section(cwd: str | None) -> str:
        lines = ["\n# 当前任务环境"]
        if cwd is not None:
            lines.append(f"你当前处在 {cwd} 目录下，可以访问该目录下的文件。")
        lines.append(f"运行平台: {sys.platform}（{'Windows' if os.name == 'nt' else 'POSIX'}）。")
        return "\n".join(lines)

    # ------------------------------------------------
    # CLAUDE.md 收集
    # ------------------------------------------------
    _MAX_CLAUDE_MD_BYTES = 32 * 1024  # 单文件截断，避免巨文件撑爆上下文

    @staticmethod
    def _collect_claude_md(cwd: str | None) -> str:
        """收集用户级 + 项目级 CLAUDE.md，返回格式化段落（无则返回空串）。

        项目级：自 cwd 向上遍历祖先目录至根，收集所有 CLAUDE.md；
                外层在前、内层（更具体）在后，以便后者覆盖前者语义。
        用户级：``~/.norma/CLAUDE.md``，优先级最低（最前）。
        """
        collected: list[tuple[str, Path]] = []

        # 用户级
        user_md = Path.home() / ".norma" / "CLAUDE.md"
        if user_md.is_file():
            collected.append(("用户级 (~/.norma/CLAUDE.md)", user_md))

        # 项目级（自 cwd 向上至根）
        if cwd:
            chain: list[Path] = []
            cur = Path(cwd).resolve()
            while True:
                chain.append(cur)
                if cur.parent == cur:  # 到达文件系统根
                    break
                cur = cur.parent
            # 外层在前
            for d in reversed(chain):
                f = d / "CLAUDE.md"
                if f.is_file():
                    collected.append((f"项目级 ({f.parent}/CLAUDE.md)", f))

        if not collected:
            return ""

        parts = [
            "\n# 项目记忆 (CLAUDE.md)",
            "以下为按需加载的 CLAUDE.md 指令，优先级：项目级 > 用户级（更近的目录更优先）。",
        ]
        limit = SystemPromptService._MAX_CLAUDE_MD_BYTES
        for label, f in collected:
            try:
                txt = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if len(txt.encode("utf-8")) > limit:
                txt = txt[: limit // 2] + "\n...(已截断，原文过长)"
            parts.append(f"\n## {label}\n{txt}")
        return "\n".join(parts)


if __name__ == "__main__":
    print(SystemPromptService.get_claude_code_system_prompt(cwd=os.getcwd()))
