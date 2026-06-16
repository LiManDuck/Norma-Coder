"""
内置命令

默认命令集：/new, /help, /exit, /clear, /model, /compact, /status
"""

import json
from pathlib import Path
from typing import Optional

from prompt_toolkit.formatted_text import HTML
from prompt_toolkit import print_formatted_text

from norma.cli.command.registry import CommandRegistry, CommandContext


async def cmd_new(ctx: CommandContext) -> None:
    """开启新对话"""
    from norma.core.llm_types import SystemMessage
    from norma.prompt.system_prompt import SystemPromptService

    system_prompt = SystemPromptService.get_claude_code_system_prompt(
        cwd=str(ctx.repl.cwd)
    )
    ctx.agent.memory._messages = [SystemMessage(content=system_prompt)]
    print_formatted_text(HTML("<style fg='ansigreen'>✓ 新对话已开始</style>"))


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
    print_formatted_text(HTML("\n".join(lines)))


async def cmd_exit(ctx: CommandContext) -> bool:
    """退出程序"""
    print_formatted_text(HTML("<style fg='ansigreen'>再见！</style>"))
    return False


async def cmd_clear(ctx: CommandContext) -> None:
    """清空屏幕"""
    import os
    os.system('clear' if os.name != 'nt' else 'cls')


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
            print_formatted_text(
                HTML(f"<style fg='ansigreen'>✓ 已切换模型到: {model_name}</style>")
            )
        except ValueError as e:
            print_formatted_text(
                HTML(f"<style fg='ansired'>切换失败: {e}</style>")
            )
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

        print_formatted_text(HTML("\n".join(lines)))


async def cmd_compact(ctx: CommandContext) -> None:
    """手动触发上下文压缩"""
    print_formatted_text(HTML("<style fg='ansiyellow'>正在压缩上下文...</style>"))
    await ctx.agent._do_compact()
    msg_count = len(ctx.agent.memory._messages)
    print_formatted_text(
        HTML(f"<style fg='ansigreen'>✓ 压缩完成，当前消息数: {msg_count}</style>")
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
    print_formatted_text(HTML("\n".join(lines)))


def register_builtin_commands(registry: CommandRegistry) -> None:
    """注册所有内置命令"""
    registry.register("new", cmd_new, description="开启新对话", aliases=[])
    registry.register("help", cmd_help, description="显示帮助信息", aliases=["h", "?"])
    registry.register("exit", cmd_exit, description="退出程序", aliases=["quit", "q"])
    registry.register("clear", cmd_clear, description="清空屏幕", aliases=["cls"])
    registry.register("model", cmd_model, description="查看或切换模型", aliases=[])
    registry.register("compact", cmd_compact, description="手动压缩上下文", aliases=[])
    registry.register("status", cmd_status, description="显示当前状态", aliases=[])
