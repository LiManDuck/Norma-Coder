"""
CLI Command 系统

提供命令注册机制，支持斜杠命令（如 /new, /help, /model 等）。
每个命令是一个异步函数，接收 REPL 上下文和参数。
"""

from norma.cli.command.registry import (
    CommandRegistry,
    CommandContext,
    command,
)
from norma.cli.command.builtin import register_builtin_commands

__all__ = [
    "CommandRegistry",
    "CommandContext",
    "command",
    "register_builtin_commands",
]
