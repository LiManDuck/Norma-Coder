"""
Skill 系统

每个 skill 文件采用 markdown + 简单 YAML frontmatter 风格：

    ---
    name: my-skill
    description: 一段告诉 LLM 什么时候使用本 skill 的描述
    aliases: [foo, bar]
    allowed_tools: [Read, Grep]   # 可选
    ---

    # 任务正文
    在这里写下完成 skill 时要遵循的步骤、注意事项等内容。

执行流程：当 LLM 调用 ``Skill(name=...)``，框架会从已加载的 ``SkillRegistry``
中找到对应的 skill，把整段正文（含上下文 args）作为 prompt 发送给一个
子 agent，借此完成模块化的复杂操作。

加载路径默认：
- ``~/.norma/skills/``       （用户级）
- ``<cwd>/.norma/skills/``   （项目级，覆盖同名)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


# ---------- frontmatter 解析 ----------

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def parse_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """
    解析 markdown 顶部 ``--- ... ---`` 风格 frontmatter。

    为避免引入 PyYAML 依赖，这里实现一个非常受限的子集：
    - ``key: value``
    - ``key: [a, b, c]``  （数组，元素去掉引号）
    - 未知字段原样保留为字符串

    返回 (meta, body)
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    meta_text = match.group("meta")
    body = match.group("body")

    meta: Dict[str, Any] = {}
    for raw_line in meta_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        meta[key] = _parse_scalar(value)
    return meta, body


def _parse_scalar(value: str) -> Any:
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(x.strip()) for x in inner.split(",") if x.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return _strip_quotes(value)


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {"'", '"'}:
        return s[1:-1]
    return s


# ---------- 数据模型 ----------

@dataclass
class Skill:
    name: str
    description: str
    body: str
    source_path: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def render_prompt(self, args: Optional[str] = None) -> str:
        """生成给子 agent 的 prompt。"""
        parts = [f"# Skill: {self.name}", ""]
        if self.description:
            parts.append(self.description)
            parts.append("")
        parts.append(self.body.strip())
        if args:
            parts.append("")
            parts.append("## Input")
            parts.append(args)
        return "\n".join(parts).strip()


# ---------- 加载 ----------

def _load_skill_file(path: Path) -> Optional[Skill]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"failed to read skill file {path}: {exc}")
        return None

    meta, body = parse_frontmatter(content)
    name = str(meta.get("name") or path.stem).strip()
    if not name:
        logger.warning(f"skill {path} missing name, skipped")
        return None
    description = str(meta.get("description") or "").strip()

    aliases = meta.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    allowed_tools = meta.get("allowed_tools") or meta.get("allowedTools") or []
    if isinstance(allowed_tools, str):
        allowed_tools = [allowed_tools]

    extra_meta = {
        k: v for k, v in meta.items()
        if k not in {"name", "description", "aliases", "allowed_tools", "allowedTools"}
    }

    return Skill(
        name=name,
        description=description,
        body=body,
        source_path=str(path),
        aliases=list(aliases),
        allowed_tools=list(allowed_tools),
        metadata=extra_meta,
    )


def load_skills_from_dir(directory: Path) -> List[Skill]:
    """从单个目录加载 *.md 文件作为 skills"""
    if not directory.exists() or not directory.is_dir():
        return []
    skills: List[Skill] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix.lower() in {".md", ".markdown"}:
            skill = _load_skill_file(entry)
            if skill is not None:
                skills.append(skill)
    return skills


# ---------- registry ----------

class SkillRegistry:
    """
    Skill 注册表

    支持从多个目录加载 skill 文件。后加载的同名 skill 会覆盖先加载的，
    适合“项目目录覆盖用户目录”这种使用方式。
    """

    def __init__(self, skills: Optional[Iterable[Skill]] = None) -> None:
        self._skills: Dict[str, Skill] = {}
        if skills:
            for s in skills:
                self.register(s)

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        for alias in skill.aliases:
            if alias and alias not in self._skills:
                self._skills[alias] = skill

    def get(self, name: str) -> Optional[Skill]:
        if not name:
            return None
        return self._skills.get(name)

    def all(self) -> List[Skill]:
        seen: set[str] = set()
        result: List[Skill] = []
        for s in self._skills.values():
            if s.name in seen:
                continue
            seen.add(s.name)
            result.append(s)
        return result

    def names(self) -> List[str]:
        return sorted({s.name for s in self._skills.values()})

    @classmethod
    def from_dirs(cls, dirs: Iterable[Path]) -> "SkillRegistry":
        registry = cls()
        for d in dirs:
            for s in load_skills_from_dir(Path(d)):
                registry.register(s)
        return registry

    @classmethod
    def from_default_dirs(
        cls,
        cwd: Optional[Path] = None,
        extra_dirs: Optional[Iterable[Path]] = None,
    ) -> "SkillRegistry":
        """加载 ~/.norma/skills、<cwd>/.norma/skills 以及调用方追加的目录"""
        dirs: List[Path] = []
        home = Path.home() / ".norma" / "skills"
        dirs.append(home)
        if cwd is not None:
            dirs.append(Path(cwd) / ".norma" / "skills")
        if extra_dirs:
            for d in extra_dirs:
                dirs.append(Path(d))
        return cls.from_dirs(dirs)
