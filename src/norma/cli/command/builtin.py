"""
内置命令

默认命令集：/new, /help, /exit, /clear, /model, /compact, /status
"""

import json
from pathlib import Path
from typing import Optional

from norma.cli.command.registry import CommandRegistry, CommandContext


async def cmd_new(ctx: CommandContext) -> None:
    """开启新对话"""
    from norma.core.llm_types import SystemMessage
    from norma.prompt.system_prompt import SystemPromptService

    system_prompt = SystemPromptService.get_claude_code_system_prompt(
        cwd=str(ctx.repl.cwd)
    )
    ctx.agent.memory._messages = [SystemMessage(content=system_prompt)]

    # 切换到新的 session
    sm = getattr(ctx.agent, "session_manager", None)
    if sm is not None:
        if sm.current:
            sm.close()
        record = sm.create()
        ctx.agent.conversation_id = record.session_id
        ctx.print(
            f"<style fg='ansigreen'>✓ 新对话已开始 "
            f"(session: {record.session_id[:8]}...)</style>"
        )
    else:
        ctx.print("<style fg='ansigreen'>✓ 新对话已开始</style>")


async def cmd_help(ctx: CommandContext) -> None:
    """显示帮助信息"""
    registry = ctx.repl.command_registry
    lines = ["<b>可用命令:</b>", ""]
    for name, info in sorted(registry.all_commands().items()):
        aliases = f" ({', '.join(f'/{a}' for a in info.aliases)})" if info.aliases else ""
        lines.append(
            f"  <style fg='ansiyellow'>/{name}</style>{aliases} - {info.description}"
        )
    if ctx.repl.command_registry.all_commands():
        lines.append("")
    lines.append("<style fg='#888888'>直接输入问题开始对话</style>")
    ctx.print("\n".join(lines))


async def cmd_exit(ctx: CommandContext) -> bool:
    """退出程序"""
    ctx.print("<style fg='ansigreen'>再见！</style>")
    return False


async def cmd_clear(ctx: CommandContext) -> None:
    """清空屏幕"""
    ctx.clear_screen()


async def cmd_model(ctx: CommandContext) -> None:
    """查看或切换模型"""
    llm = ctx.agent.llm

    if ctx.args:
        model_name = ctx.args.strip()
        try:
            if hasattr(llm, 'switch_model'):
                llm.switch_model(model_name)
            else:
                llm.model = model_name
            ctx.print(f"<style fg='ansigreen'>✓ 已切换模型到: {model_name}</style>")
        except ValueError as e:
            ctx.print(f"<style fg='ansired'>切换失败: {e}</style>")
    else:
        # 显示当前模型和所有可用模型
        current_model = getattr(llm, 'model', 'unknown')
        current_provider = getattr(llm, '_default_provider', None)

        lines = [
            f"<b>当前模型:</b> <style fg='ansicyan'>{current_model}</style>",
        ]
        if current_provider:
            lines.append(f"<b>当前 Provider:</b> <style fg='ansicyan'>{current_provider}</style>")

        # 列出所有 Provider 和模型
        providers = getattr(llm, '_providers', {})
        if providers:
            lines.append("")
            lines.append("<b>可用 Provider 和模型:</b>")
            for name, prov in providers.items():
                marker = " *" if name == current_provider else ""
                lines.append(f"  <style fg='ansiyellow'>[{name}]{marker}</style>")
                for m in prov.models:
                    m_marker = " ←" if m == current_model and name == current_provider else ""
                    lines.append(f"    - {m}{m_marker}")
            lines.append("")
            lines.append("<style fg='#888888'>使用 /model provider/model 切换模型</style>")

        ctx.print("\n".join(lines))


async def cmd_compact(ctx: CommandContext) -> None:
    """手动触发上下文压缩"""
    ctx.print("<style fg='ansiyellow'>正在压缩上下文...</style>")
    ok = await ctx.agent._do_compact()
    msg_count = len(ctx.agent.memory._messages)
    if ok:
        ctx.print(f"<style fg='ansigreen'>✓ 压缩完成，当前消息数: {msg_count}</style>")
    else:
        ctx.print(
            f"<style fg='ansired'>✗ 压缩失败（LLM 不可达或出错），"
            f"上下文未变更，当前消息数: {msg_count}</style>"
        )


async def cmd_status(ctx: CommandContext) -> None:
    """显示当前状态"""
    agent = ctx.agent
    tool_status = agent.tool_manager.get_status()
    msg_count = len(agent.memory._messages)
    model_name = getattr(agent.llm, 'model', 'unknown')

    lines = [
        "<b>Norma Coder 状态</b>",
        f"  模型: <style fg='ansicyan'>{model_name}</style>",
        f"  消息数: {msg_count}",
        f"  已注册工具: {tool_status.get('registered_tools', 0)}",
        f"  会话ID: {agent.conversation_id[:8]}...",
        f"  权限模式: {agent.permission_checker.config.mode.value if agent.permission_checker else 'N/A'}",
    ]
    sm = getattr(agent, "session_manager", None)
    if sm and sm.current:
        lines.append(
            f"  Session 文件: <style fg='#888888'>{sm.current.file_path}</style>"
        )
    ctx.print("\n".join(lines))


async def cmd_resume(ctx: CommandContext) -> None:
    """列出本地历史会话或恢复指定会话

    用法:
      /resume          - 列出当前项目的最近会话
      /resume <id>     - 恢复指定 session_id 的会话
      /resume <序号>    - 恢复列表中第 N 个会话（1-based）
    """
    agent = ctx.agent
    sm = getattr(agent, "session_manager", None)
    if sm is None:
        ctx.print("<style fg='ansired'>当前未启用 Session 系统</style>")
        return

    metas = sm.list_sessions(limit=20)

    if not ctx.args:
        if not metas:
            ctx.print("<style fg='#888888'>暂无历史会话</style>")
            return
        lines = ["<b>历史会话 (按更新时间倒序):</b>", ""]
        for i, m in enumerate(metas, 1):
            current_marker = ""
            if sm.current and m.session_id == sm.current.session_id:
                current_marker = " <style fg='ansigreen'>← 当前</style>"
            title = m.title or "(无标题)"
            lines.append(
                f"  <style fg='ansiyellow'>{i:>2}.</style> "
                f"<style fg='ansicyan'>{m.session_id}</style>  "
                f"消息={m.message_count}  "
                f"<style fg='#888888'>{m.updated_at[:19]}</style>  "
                f"{title}{current_marker}"
            )
        lines.append("")
        lines.append(
            "<style fg='#888888'>使用 /resume &lt;序号&gt; 或 "
            "/resume &lt;session_id&gt; 恢复</style>"
        )
        ctx.print("\n".join(lines))
        return

    target = ctx.args.strip()
    chosen: Optional[str] = None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(metas):
            chosen = metas[idx].session_id
    if chosen is None:
        for m in metas:
            if m.session_id.startswith(target):
                chosen = m.session_id
                break
    if chosen is None:
        ctx.print(f"<style fg='ansired'>未找到匹配的会话: {target}</style>")
        return

    # 关闭旧会话，打开目标会话
    if sm.current:
        sm.close()
    record = sm.load(chosen)
    if record is None:
        ctx.print(f"<style fg='ansired'>会话文件不存在: {chosen}</style>")
        return
    agent.conversation_id = chosen
    restored = await agent.restore_from_session(chosen)
    ctx.print(
        f"<style fg='ansigreen'>✓ 已恢复会话 {chosen[:8]}...，"
        f"加载 {restored} 条历史消息</style>"
    )


async def cmd_session(ctx: CommandContext) -> None:
    """显示当前 session 信息"""
    sm = getattr(ctx.agent, "session_manager", None)
    if sm is None or sm.current is None:
        ctx.print("<style fg='#888888'>当前未启用或未创建 session</style>")
        return
    cur = sm.current
    lines = [
        "<b>Session 信息</b>",
        f"  ID: <style fg='ansicyan'>{cur.session_id}</style>",
        f"  CWD: {cur.cwd}",
        f"  文件: <style fg='#888888'>{cur.file_path}</style>",
        f"  创建时间: {cur.meta.created_at}",
        f"  更新时间: {cur.meta.updated_at}",
        f"  消息数: {cur.meta.message_count}",
    ]
    ctx.print("\n".join(lines))


def register_builtin_commands(registry: CommandRegistry) -> None:
    """注册所有内置命令"""
    registry.register("new", cmd_new, description="开启新对话", aliases=[])
    registry.register("help", cmd_help, description="显示帮助信息", aliases=["h", "?"])
    registry.register("exit", cmd_exit, description="退出程序", aliases=["quit", "q"])
    registry.register("clear", cmd_clear, description="清空屏幕", aliases=["cls"])
    registry.register("model", cmd_model, description="查看或切换模型", aliases=[])
    registry.register("compact", cmd_compact, description="手动压缩上下文", aliases=[])
    registry.register("status", cmd_status, description="显示当前状态", aliases=[])
    registry.register("resume", cmd_resume, description="列出/恢复历史会话", aliases=[])
    registry.register("session", cmd_session, description="显示当前 session 信息", aliases=[])
