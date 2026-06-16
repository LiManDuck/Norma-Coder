"""
命令注册表

实现命令的注册、查找、执行机制。
支持命令别名和参数自动解析。
"""

import logging
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from norma.cli.repl.repl import NormaREPL

logger = logging.getLogger(__name__)


class CommandContext:
    """命令执行上下文"""

    def __init__(self, repl: "NormaREPL", args: str = ""):
        self.repl = repl
        self.args = args.strip()

    @property
    def agent(self):
        return self.repl.agent

    @property
    def console(self):
        return self.repl.console


class CommandInfo:
    """命令元信息"""

    def __init__(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        aliases: Optional[List[str]] = None,
        usage: str = "",
    ):
        self.name = name
        self.handler = handler
        self.description = description
        self.aliases = aliases or []
        self.usage = usage


class CommandRegistry:
    """命令注册表"""

    def __init__(self):
        self._commands: Dict[str, CommandInfo] = {}
        self._aliases: Dict[str, str] = {}  # alias -> command name

    def register(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        aliases: Optional[List[str]] = None,
        usage: str = "",
    ) -> None:
        """注册一个命令"""
        info = CommandInfo(
            name=name,
            handler=handler,
            description=description,
            aliases=aliases or [],
            usage=usage,
        )
        self._commands[name] = info
        for alias in (aliases or []):
            self._aliases[alias] = name
        logger.debug(f"Registered command: /{name}")

    def lookup(self, name: str) -> Optional[CommandInfo]:
        """查找命令（支持别名）"""
        if name in self._commands:
            return self._commands[name]
        if name in self._aliases:
            return self._commands.get(self._aliases[name])
        return None

    def all_commands(self) -> Dict[str, CommandInfo]:
        """返回所有已注册的命令"""
        return dict(self._commands)

    async def execute(self, ctx: CommandContext) -> Any:
        """执行命令"""
        raw = ctx.args
        parts = raw.split(maxsplit=1)
        cmd_name = parts[0].lstrip("/")
        cmd_args = parts[1] if len(parts) > 1 else ""

        info = self.lookup(cmd_name)
        if info is None:
            return None

        new_ctx = CommandContext(repl=ctx.repl, args=cmd_args)
        return await info.handler(new_ctx)


def command(
    name: str,
    description: str = "",
    aliases: Optional[List[str]] = None,
    usage: str = "",
):
    """命令注册装饰器"""
    def decorator(func: Callable):
        func._command_info = {
            "name": name,
            "description": description,
            "aliases": aliases or [],
            "usage": usage,
        }
        return func
    return decorator
