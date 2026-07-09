"""
NormaREPL - 交互式命令行界面

更新（2026-06-15）
------------------
- 集成 Command 系统，支持 /new, /help, /exit, /model, /compact, /status 等命令
- 权限确认流程：当 agent 请求确认时，在 REPL 中展示并等待用户输入
- 更好的 UI 渲染
"""

import asyncio
import os
from pathlib import Path
from typing import Optional, Callable, Awaitable

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console

from norma.messagebus.messagebus import MessageType

from norma.core.agent_types import (
    BaseAgent,
    AgentEvent,
    AgentResponse,
    AgentThinkEvent,
    AgentLLMRequestEvent,
    AgentLLMResponseEvent,
    AgentToolRequestEvent,
    AgentToolRequestAnswerEvent,
    AgentInputEvent,
    AgentTextDeltaEvent,
    AgentThinkDeltaEvent,
)
from norma.cli.command import (
    CommandRegistry,
    CommandContext,
    register_builtin_commands,
)
from norma.cli.ui.render import AgentEventRenderer
from norma.permission import PermissionMode


class NormaREPL:
    """交互式 REPL - 类似 Claude Code 风格"""

    def __init__(
        self,
        agent: BaseAgent,
        cwd: str | Path,
        prompt_confirm: Optional[Callable[[str], Awaitable[bool]]] = None,
    ):
        self.agent = agent
        self.agent_render = AgentEventRenderer()
        self.running = True
        self.cwd = Path(cwd)
        self.console = Console()

        # 权限确认回调：默认走 prompt_toolkit 交互；可注入便于测试
        self.prompt_confirm = prompt_confirm or self._default_prompt_confirm

        # 命令系统
        self.command_registry = CommandRegistry()
        register_builtin_commands(self.command_registry)

        # 命令补全
        cmd_names = [f"/{name}" for name in self.command_registry.all_commands()]
        for info in self.command_registry.all_commands().values():
            for alias in info.aliases:
                cmd_names.append(f"/{alias}")
        self.completer = WordCompleter(cmd_names, ignore_case=True)

        # 配置会话
        history_dir = Path.home() / ".norma"
        history_dir.mkdir(exist_ok=True)

        # ---- shift+tab 切换权限模式 ----
        kb = KeyBindings()

        @kb.add("s-tab")
        def _cycle_mode(event):
            new_mode = self._cycle_permission_mode()
            event.app.invalidate()
            print_formatted_text(
                HTML(
                    f"<style fg='ansicyan'>⇄ 执行模式已切换: "
                    f"<b>{new_mode}</b></style>"
                )
            )

        self.key_bindings = kb

        self.session = PromptSession(
            completer=self.completer,
            enable_history_search=True,
            key_bindings=kb,
            bottom_toolbar=self._render_toolbar,
        )

    # ---------- 模式切换 / 工具栏 ----------

    _MODE_CYCLE = [PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.AUTO]

    def _cycle_permission_mode(self) -> str:
        """将权限模式按 plan → edit → auto 循环切换"""
        checker = getattr(self.agent, "permission_checker", None)
        if checker is None or checker.config is None:
            return "n/a"
        current = checker.config.mode
        try:
            idx = self._MODE_CYCLE.index(current)
        except ValueError:
            idx = -1
        new_mode = self._MODE_CYCLE[(idx + 1) % len(self._MODE_CYCLE)]
        checker.config.mode = new_mode
        return new_mode.value

    def _render_toolbar(self):
        checker = getattr(self.agent, "permission_checker", None)
        mode = checker.config.mode.value if checker and checker.config else "n/a"
        model = getattr(self.agent.llm, "model", "?")
        sm = getattr(self.agent, "session_manager", None)
        sid = sm.current.session_id[:8] if sm and sm.current else "-"
        return HTML(
            f" <b fg='ansicyan'>mode</b>={mode}  "
            f"<b fg='ansicyan'>model</b>={model}  "
            f"<b fg='ansicyan'>session</b>={sid}  "
            f"<style fg='#888888'>(Shift+Tab 切换模式)</style>"
        )

    def print_output(self, text: str) -> None:
        """统一文本输出入口：命令处理器通过 ctx.print 调用此方法。

        text 为 prompt_toolkit 的 HTML 片段字符串（与现有命令输出风格一致）。
        """
        try:
            print_formatted_text(HTML(text))
        except Exception:
            # HTML 解析失败时回退到纯文本
            print(text)

    def clear_screen(self) -> None:
        """清屏（REPL 实现：调用系统清屏命令）。"""
        import os
        os.system('clear' if os.name != 'nt' else 'cls')

    def show_banner(self):
        """显示启动横幅"""
        banner = HTML("""
<style fg='ansiblue' bold>Norma Coder CLI</style>

<style fg='ansigreen'>欢迎使用 Norma AI Agent!</style>

<b>可用命令:</b>
  <style fg='ansiyellow'>/help</style>   - 显示帮助信息
  <style fg='ansiyellow'>/new</style>    - 开启新对话
  <style fg='ansiyellow'>/model</style>  - 查看/切换模型
  <style fg='ansiyellow'>/exit</style>   - 退出程序

<style fg='#888888'>提示: 直接输入问题开始对话，使用 Ctrl+D 或 /exit 退出</style>
""")
        print_formatted_text(banner)
        print()

    # ---------- 权限确认（UI_PROMPT 总线订阅）----------

    def _setup_permission_subscription(self) -> None:
        """订阅 UI_PROMPT：agent 请求 ASK 时在 REPL 内交互确认并回送结果。

        REPL 直接消费 ``agent.run()`` 生成器，不经总线拿事件；但权限请求是
        经 ``UserInputManager.request_confirmation`` -> 总线 ``UI_PROMPT`` ->
        await future 的链路。若不订阅，future 会等到超时后默认拒绝（60s 挂起）。
        """
        bus = getattr(self.agent, "message_bus", None)
        uim = getattr(self.agent, "user_input_manager", None)
        if bus is not None and uim is not None:
            bus.subscribe(MessageType.UI_PROMPT, self._on_ui_prompt)

    async def _on_ui_prompt(self, message) -> None:
        payload = getattr(message, "payload", None) or {}
        request_id = payload.get("request_id")
        prompt_text = payload.get("prompt", "")
        uim = getattr(self.agent, "user_input_manager", None)
        if not request_id or uim is None:
            return
        try:
            allowed = await self.prompt_confirm(prompt_text)
        except Exception:  # noqa: BLE001
            allowed = False
        try:
            await uim.respond_confirmation(request_id, allowed)
        except Exception:  # noqa: BLE001
            pass

    async def _default_prompt_confirm(self, prompt_text: str) -> bool:
        """默认权限确认：prompt_toolkit 交互式 y/N。"""
        print_formatted_text(HTML("\n<style fg='ansiyellow' bold>⚠ 权限确认</style>"))
        if prompt_text:
            print_formatted_text(HTML(f"<style fg='ansicyan'>{prompt_text}</style>"))
        answer = await self.session.prompt_async(
            HTML("<b fg='ansiyellow'>允许执行? [y/N] </b>")
        )
        return answer.strip().lower() in ("y", "yes")

    async def run(self):
        """主循环 - 启动REPL"""
        self._setup_permission_subscription()
        self.show_banner()

        while self.running:
            try:
                user_input = await self.session.prompt_async(
                    HTML('<b fg="ansiblue"> ❯ </b> '),
                )
                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.startswith('/'):
                    should_continue = await self.handle_command(user_input)
                    if should_continue is False:
                        break
                else:
                    await self.process_user_input(user_input)

            except KeyboardInterrupt:
                print_formatted_text(
                    HTML("\n<style fg='ansiyellow'>已中断，正在退出...</style>")
                )
                break
            except EOFError:
                print_formatted_text(HTML("\n<style fg='ansigreen'>再见！</style>"))
                break
            except Exception as e:
                print_formatted_text(
                    HTML(f"\n<style fg='ansired'>系统错误: {str(e)}</style>")
                )
                continue

    async def handle_command(self, raw_input: str) -> bool:
        """
        处理斜杠命令

        Returns:
            bool: True 表示继续运行，False 表示退出
        """
        ctx = CommandContext(repl=self, args=raw_input)
        parts = raw_input.split(maxsplit=1)
        cmd_name = parts[0].lstrip("/") if parts else ""

        # 先查表：未知命令直接提示，避免与「命令正常返回 None」混淆
        if not self.command_registry.lookup(cmd_name):
            print_formatted_text(
                HTML(f"<style fg='ansired'>未知命令: /{cmd_name}</style>")
            )
            print_formatted_text(
                HTML("<style fg='#888888'>输入 /help 查看可用命令</style>")
            )
            print()
            return True

        result = await self.command_registry.execute(ctx)

        # /exit 命令返回 False
        if result is False:
            self.running = False
            return False

        print()
        return True

    async def process_user_input(self, user_input: str):
        """处理用户输入并流式显示Agent响应"""
        print()

        try:
            async for event in self.agent.run(user_input):
                if isinstance(event, AgentThinkEvent):
                    continue
                if isinstance(event, AgentLLMRequestEvent):
                    continue
                if isinstance(event, AgentInputEvent):
                    continue
                # 流式增量在旧 REPL 中跳过（最终文本由 AgentLLMResponseEvent 显示）；
                # 真正的逐字流式渲染由 TUI 负责。
                if isinstance(event, (AgentTextDeltaEvent, AgentThinkDeltaEvent)):
                    continue

                formatted_output = self.agent_render.render_event(
                    agent_event=event
                )
                print_formatted_text(formatted_output)

                if isinstance(event, AgentResponse):
                    break

        except KeyboardInterrupt:
            print_formatted_text(
                HTML("\n<style fg='ansiyellow'>操作已取消</style>")
            )
        except Exception as e:
            print_formatted_text(
                HTML(f"\n<style fg='ansired'>错误: {str(e)}</style>")
            )

        print()
