"""Skill 系统回归测试（headless）。

验证：parse_frontmatter -> Skill.render_prompt -> load_skills_from_dir ->
SkillRegistry.from_dirs（含 alias）-> SkillTool.execute（happy + not-found 路径）。

运行：``python -m norma.skill.test_skill``
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Optional

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.agent_types import AgentResponse  # noqa: E402
from norma.core.tool_types import ToolRequest  # noqa: E402
from norma.skill.skill import (  # noqa: E402
    Skill,
    SkillRegistry,
    load_skills_from_dir,
    parse_frontmatter,
)
from norma.tool.skill_tool.skill_tool import SkillTool  # noqa: E402


class _FakeAgent:
    """最小 subagent mock：run() 立即 yield 一个 AgentResponse"""

    def __init__(self, name: Optional[str] = None):
        self.name = name or "fake"

    async def run(self, prompt: str) -> AsyncGenerator[AgentResponse, None]:
        yield AgentResponse(
            agent_name=self.name,
            input_message=[],
            tools=[],
            prompt_usage=None,
            event_list=[],
            message_list=[],
            response=f"FAKE<{prompt}>",
            tool_call_sequence=None,
            tool_call_nums=0,
        )


def test_frontmatter_and_render() -> None:
    content = (
        "---\n"
        "name: greet\n"
        "description: 问候 skill\n"
        "aliases: [hello, hi]\n"
        "allowed_tools: [Read, Ls]\n"
        "---\n"
        "你是一个问候助手，请热情地打招呼。\n"
    )
    meta, body = parse_frontmatter(content)
    assert meta["name"] == "greet"
    assert meta["description"] == "问候 skill"
    assert meta["aliases"] == ["hello", "hi"]
    assert meta["allowed_tools"] == ["Read", "Ls"]
    assert "问候助手" in body

    skill = Skill(name="greet", description="问候", body=body, aliases=["hello"])
    prompt = skill.render_prompt(args="world")
    assert "# Skill: greet" in prompt
    assert "问候助手" in prompt
    assert "## Input" in prompt
    assert "world" in prompt
    print("[PASS] parse_frontmatter + render_prompt")


def test_load_and_registry(tmpdir: str) -> None:
    d = Path(tmpdir) / "skills"
    d.mkdir()
    (d / "greet.md").write_text(
        "---\nname: greet\ndescription: greet user\naliases: [hi]\n---\nbody-greet\n",
        encoding="utf-8",
    )
    (d / "calc.md").write_text(
        "---\nname: calc\ndescription: calc things\n---\nbody-calc\n",
        encoding="utf-8",
    )
    (d / "notes.txt").write_text("not a skill", encoding="utf-8")  # 非 md，应被忽略

    skills = load_skills_from_dir(d)
    names = sorted(s.name for s in skills)
    assert names == ["calc", "greet"], f"期望 [calc, greet]，实际 {names}"

    reg = SkillRegistry.from_dirs([d])
    assert reg.get("greet") is not None
    assert reg.get("hi") is not None, "alias 'hi' 应能解析到 greet"
    assert reg.get("calc") is not None
    assert reg.get("nope") is None
    assert set(reg.names()) == {"calc", "greet"}, f"names: {reg.names()}"
    print("[PASS] load_skills_from_dir + SkillRegistry.from_dirs (alias)")


async def test_skill_tool_execute(tmpdir: str) -> None:
    d = Path(tmpdir) / "skills2"
    d.mkdir(exist_ok=True)
    (d / "greet.md").write_text(
        "---\nname: greet\ndescription: greet user\n---\n请打招呼。\n",
        encoding="utf-8",
    )
    reg = SkillRegistry.from_dirs([d])

    def factory(name=None):
        return _FakeAgent(name=name)

    tool = SkillTool(registry=reg, agent_factory=factory)
    assert tool.name == "Skill"
    assert "greet" in tool.description, "description 应列出可用 skill"

    # happy path
    req = ToolRequest(
        tool_call_id="s1",
        tool_call_name="Skill",
        tool_call_arguments={"name": "greet", "args": "Alice"},
    )
    res = await tool.execute(req)
    assert res.is_error is False, f"不应报错: {res.content}"
    assert "FAKE<" in res.content, f"应包含子 agent 响应: {res.content!r}"
    # args 与 skill body 都应进入子 agent 的 prompt
    assert "Alice" in res.content, f"args 应进入 prompt: {res.content!r}"
    assert "请打招呼" in res.content, f"skill body 应进入 prompt: {res.content!r}"

    # not-found path
    req2 = ToolRequest(
        tool_call_id="s2",
        tool_call_name="Skill",
        tool_call_arguments={"name": "ghost"},
    )
    res2 = await tool.execute(req2)
    assert res2.is_error is True, "不存在的 skill 应返回 is_error=True"
    assert "not found" in res2.content, f"错误信息应说明未找到: {res2.content!r}"

    # 缺 name 参数
    req3 = ToolRequest(
        tool_call_id="s3",
        tool_call_name="Skill",
        tool_call_arguments={},
    )
    res3 = await tool.execute(req3)
    assert res3.is_error is True
    assert "name" in res3.content
    print("[PASS] SkillTool.execute (happy + not-found + missing-name)")


async def _amain() -> int:
    failures = 0
    # 同步用例
    for fn, args in (
        (test_frontmatter_and_render, ()),
    ):
        try:
            fn(*args)
        except AssertionError as exc:
            print(f"[FAIL] {fn.__name__}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[ERROR] {fn.__name__}: {exc}")
            traceback.print_exc()
            failures += 1
    # 需要 tmpdir 的用例
    with tempfile.TemporaryDirectory() as tmpdir:
        for fn in (test_load_and_registry,):
            try:
                fn(tmpdir)
            except AssertionError as exc:
                print(f"[FAIL] {fn.__name__}: {exc}")
                failures += 1
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[ERROR] {fn.__name__}: {exc}")
                traceback.print_exc()
                failures += 1
        try:
            await test_skill_tool_execute(tmpdir)
        except AssertionError as exc:
            print(f"[FAIL] test_skill_tool_execute: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[ERROR] test_skill_tool_execute: {exc}")
            traceback.print_exc()
            failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL skill tests passed")
    return 0


def test_skill_headless() -> None:
    """pytest 入口"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(asyncio.run(_amain()))
