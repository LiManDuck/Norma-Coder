"""权限系统回归测试（headless）。

验证 PermissionChecker.check 的 mode × tool 分类矩阵：
- AUTO  : 全部 ALLOW
- PLAN  : 只读工具 ALLOW，其余（写/危险）一律 DENY
- EDIT  : 只读 + 写工具 ALLOW，危险工具（Bash/Agent/Skill）ASK
- per-tool 显式配置优先于 mode 默认策略

并锁定「工具名大小写必须与 READ_ONLY/WRITE/DANGEROUS_TOOLS 常量一致」--
此前 BashTool.name 为 "bash"（小写）而 DANGEROUS_TOOLS 含 "Bash"（大写），
导致 EDIT 模式下 Bash 仅因「未知工具 -> ASK」兜底才被询问，分类名义错误。

运行：``python -m norma.permission.test_permission``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.permission import (  # noqa: E402
    PermissionChecker,
    PermissionConfig,
    PermissionDecision,
    PermissionMode,
)
from norma.core.tool_types import ToolRequest  # noqa: E402


def _req(name: str) -> ToolRequest:
    return ToolRequest(
        tool_call_id=f"id-{name}",
        tool_call_name=name,
        tool_call_arguments={},
    )


READ_ONLY = ["Read", "Ls", "Glob", "Grep", "TaskList", "TaskGet"]
WRITE = ["Edit", "Write", "TaskCreate", "TaskUpdate"]
DANGEROUS = ["Bash", "Agent", "Skill"]


def test_auto_allows_everything() -> None:
    pc = PermissionChecker(config=PermissionConfig(mode=PermissionMode.AUTO))
    for name in READ_ONLY + WRITE + DANGEROUS + ["SomethingUnknown"]:
        assert pc.check(_req(name)) == PermissionDecision.ALLOW, name


def test_plan_blocks_writes_and_dangerous() -> None:
    pc = PermissionChecker(config=PermissionConfig(mode=PermissionMode.PLAN))
    for name in READ_ONLY:
        assert pc.check(_req(name)) == PermissionDecision.ALLOW, f"PLAN/{name}"
    for name in WRITE + DANGEROUS:
        assert pc.check(_req(name)) == PermissionDecision.DENY, f"PLAN/{name}"
    # 未知工具在 PLAN 模式下也拒绝（只读白名单制）
    assert pc.check(_req("Unknown")) == PermissionDecision.DENY


def test_edit_asks_for_dangerous() -> None:
    pc = PermissionChecker(config=PermissionConfig(mode=PermissionMode.EDIT))
    for name in READ_ONLY + WRITE:
        assert pc.check(_req(name)) == PermissionDecision.ALLOW, f"EDIT/{name}"
    for name in DANGEROUS:
        assert pc.check(_req(name)) == PermissionDecision.ASK, f"EDIT/{name}"
    # 未知工具在 EDIT 模式下询问
    assert pc.check(_req("Unknown")) == PermissionDecision.ASK


def test_explicit_per_tool_overrides_mode() -> None:
    # PLAN 模式下显式放行 Bash
    cfg = PermissionConfig(
        mode=PermissionMode.PLAN,
        tools={"Bash": PermissionDecision.ALLOW},
    )
    pc = PermissionChecker(config=cfg)
    assert pc.check(_req("Bash")) == PermissionDecision.ALLOW
    # 未显式配置的写工具仍按 PLAN 拒绝
    assert pc.check(_req("Edit")) == PermissionDecision.DENY

    # AUTO 模式下显式拒绝 Read
    cfg2 = PermissionConfig(
        mode=PermissionMode.AUTO,
        tools={"Read": PermissionDecision.DENY},
    )
    pc2 = PermissionChecker(config=cfg2)
    assert pc2.check(_req("Read")) == PermissionDecision.DENY
    assert pc2.check(_req("Write")) == PermissionDecision.ALLOW


def test_tool_names_match_classification_constants() -> None:
    """Bash/Agent/Skill 必须落在 DANGEROUS 而非靠兜底；大小写一致。"""
    pc = PermissionChecker(config=PermissionConfig(mode=PermissionMode.EDIT))
    # 关键回归：Bash（大写）应被识别为危险工具 -> ASK，而非「未知 -> ASK」兜底
    assert "Bash" in pc.dangerous_tools
    assert pc.check(_req("Bash")) == PermissionDecision.ASK
    # 小写 "bash" 不应命中（确认是大小写敏感的白名单，防止误以为兜底即正确）
    assert "bash" not in pc.dangerous_tools
    assert "bash" not in pc.write_tools
    assert "bash" not in pc.read_only_tools


def test_tool_annotation_hints_classify_mcp_tools() -> None:
    """工具实例自报的 is_readonly/is_destructive 注解（MCP readOnlyHint 等）
    应被 check 用于分类不在静态集合中的 mcp__ 工具。

    此前 MCPTool.is_readonly/is_destructive 被解析但权限检查器从不查阅--只读
    MCP 工具在 EDIT 模式被「未知 -> ASK」误询问，破坏性 MCP 工具仅靠兜底命中。
    """
    # mcp__ 前缀工具不在任何静态集合
    mcp_name = "mcp__srv__read_file"
    pc_edit = PermissionChecker(config=PermissionConfig(mode=PermissionMode.EDIT))
    pc_plan = PermissionChecker(config=PermissionConfig(mode=PermissionMode.PLAN))

    # 无注解：未知工具 -> EDIT ASK / PLAN DENY（兜底，不变）
    assert pc_edit.check(_req(mcp_name)) == PermissionDecision.ASK
    assert pc_plan.check(_req(mcp_name)) == PermissionDecision.DENY

    # is_readonly=True：视同 Read/Ls -> EDIT ALLOW / PLAN ALLOW（修复核心）
    assert pc_edit.check(_req(mcp_name), is_readonly=True) == PermissionDecision.ALLOW
    assert pc_plan.check(_req(mcp_name), is_readonly=True) == PermissionDecision.ALLOW

    # is_destructive=True：视同 Bash -> EDIT ASK / PLAN DENY（显式分类，非兜底）
    de_name = "mcp__srv__delete_file"
    assert pc_edit.check(_req(de_name), is_destructive=True) == PermissionDecision.ASK
    assert pc_plan.check(_req(de_name), is_destructive=True) == PermissionDecision.DENY

    # 注解不覆盖静态集合：Bash 即便误报 is_readonly 仍按危险工具 ASK
    # （静态分类优先于注解，防恶意/错误注解把危险工具「洗白」为只读）
    assert pc_edit.check(_req("Bash"), is_readonly=True) == PermissionDecision.ASK
    # Read 即便误报 is_destructive 仍按只读工具 ALLOW
    assert pc_edit.check(_req("Read"), is_destructive=True) == PermissionDecision.ALLOW


async def _amain() -> int:
    tests = [
        ("auto_allows_everything", test_auto_allows_everything),
        ("plan_blocks_writes_and_dangerous", test_plan_blocks_writes_and_dangerous),
        ("edit_asks_for_dangerous", test_edit_asks_for_dangerous),
        ("explicit_per_tool_overrides_mode", test_explicit_per_tool_overrides_mode),
        ("tool_names_match_classification_constants",
         test_tool_names_match_classification_constants),
        ("tool_annotation_hints_classify_mcp_tools",
         test_tool_annotation_hints_classify_mcp_tools),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failures += 1
            print(f"ERROR {name}: {exc}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_permission_headless() -> None:
    """pytest 入口（若安装 pytest）。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
