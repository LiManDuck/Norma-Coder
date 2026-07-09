"""NormaApp - 基于 Textual 的 TUI 前端。

架构
----
``NormaApp`` 同时扮演两个角色：

1. **前端**：订阅进程内 :class:`MessageBus`，将 Agent 事件渲染到界面。
2. **REPL 兼容层**：实现 ``print_output`` / ``clear_screen`` 等方法并持有
   ``agent`` / ``cwd`` / ``command_registry`` / ``console``，使得
   :class:`CommandContext` 可以像操作 ``NormaREPL`` 一样操作本应用——
   斜杠命令在 TUI 与旧 REPL 中复用同一套实现。

事件流
------
``NormaCoder.run()`` 既 ``yield`` 事件又经 ``_publish`` 推送到 MessageBus。
本应用通过 ``MessageBus.subscribe_all`` 订阅全部消息，回调中把总线
:class:`Message` 转发为 Textual 自定义消息 ``BusEventMessage`` 投递回 UI 线程，
再在 ``on_bus_event_message`` 中按 ``msg_type`` 渲染。这样 Agent 与前端
完全解耦：Agent 不知道前端是谁，前端也不直接消费生成器。
"""

from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import re
from pathlib import Path
from typing import Optional

from rich.console import RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, RichLog, Static

from norma.agent.runner import AgentRunner
from norma.cli.command import CommandContext, CommandRegistry, register_builtin_commands
from norma.core.agent_types import (
    AgentInputEvent,
    AgentLLMRequestEvent,
    AgentLLMResponseEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentThinkDeltaEvent,
    AgentThinkEvent,
    AgentToolRequestAnswerEvent,
    AgentToolRequestEvent,
)
from norma.messagebus.messagebus import Message, MessageBus, MessageType, UserInputManager
from norma.permission import PermissionMode

logger = logging.getLogger(__name__)


# =====================================================================
# 工具函数
# =====================================================================

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """把 prompt_toolkit 风格的 HTML 片段转成纯文本（命令输出复用）。"""
    return html_module.unescape(_HTML_TAG_RE.sub("", text))


def _truncate(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，共 {len(text)} 字符）"


def _format_args(args) -> str:
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            return args
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)


# =====================================================================
# 权限确认弹窗
# =====================================================================

class PermissionModal(ModalScreen[bool]):
    """工具执行确认弹窗：Agent 请求 ASK 时弹出，返回 True/False。"""

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    PermissionModal > Vertical {
        width: 72;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    PermissionModal #perm-prompt {
        height: auto;
        max-height: 16;
        margin-bottom: 1;
    }
    PermissionModal Horizontal {
        height: 1;
        align-horizontal: center;
    }
    PermissionModal Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("y", "allow", "允许", show=False),
        Binding("n", "deny", "拒绝", show=False),
        Binding("escape", "deny", "拒绝", show=False),
    ]

    def __init__(self, request_id: str, prompt: str) -> None:
        super().__init__()
        self.request_id = request_id
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal

        yield Vertical(
            Static(Text("⚠ 权限确认", style="bold yellow")),
            Static(self.prompt, id="perm-prompt"),
            Horizontal(
                Static(Text("[y] 允许   [n] 拒绝   [esc] 拒绝", style="dim")),
            ),
        )

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


# =====================================================================
# 总线事件 -> Textual 消息
# =====================================================================

class BusEventMessage(TextualMessage):
    """把一条总线 Message 投递到 Textual 消息泵。"""

    def __init__(self, bus_message: Message) -> None:
        self.bus_message = bus_message
        super().__init__()


class TurnFinishedMessage(TextualMessage):
    """一次 Agent 执行结束（正常或异常）。"""

    def __init__(self, ok: bool, error: Optional[str] = None) -> None:
        self.ok = ok
        self.error = error
        super().__init__()


# =====================================================================
# 主应用
# =====================================================================

class NormaApp(App):
    """Norma Coder 文本用户界面。"""

    CSS = """
    Screen {
        layout: vertical;
    }
    #history {
        height: 1fr;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    #stream {
        height: auto;
        max-height: 40%;
        border: round $accent;
        background: $boost;
        padding: 0 1;
        display: none;
    }
    #status {
        height: 1;
        background: $primary 20%;
        color: $text;
        padding: 0 1;
    }
    #input {
        dock: bottom;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt_or_quit", "中断/退出", show=True),
        Binding("ctrl+l", "clear", "清屏", show=True),
        Binding("f2", "cycle_mode", "切换模式", show=True),
    ]

    _MODE_CYCLE = [PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.AUTO]

    def __init__(
        self,
        agent,
        cwd: str | Path,
        message_bus: MessageBus,
        user_input_manager: UserInputManager,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.cwd = Path(cwd)
        self.message_bus = message_bus
        self.user_input_manager = user_input_manager

        # ---- REPL 兼容属性（CommandContext 通过 ctx.repl.* 访问）----
        from rich.console import Console

        self.console = Console()
        self.command_registry = CommandRegistry()
        register_builtin_commands(self.command_registry)

        # ---- 运行状态 ----
        self.runner: Optional[AgentRunner] = None
        self._stream_text: str = ""
        self._stream_think: str = ""
        self._stream_active: bool = False

    # -----------------------------------------------------------------
    # 布局
    # -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(name="Norma Coder", show_clock=False)
        yield RichLog(id="history", highlight=False, markup=True, auto_scroll=True)
        yield Static("", id="stream", markup=True)
        yield Static("", id="status")
        yield Input(id="input", placeholder="输入问题，或 /help 查看命令")
        yield Footer()

    def on_mount(self) -> None:
        # 订阅总线：所有消息转发为 Textual 消息
        self.message_bus.subscribe_all(self._on_bus_message)
        self._write_banner()
        self._refresh_status()
        # 输入框自动聚焦，启动后即可直接打字
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # REPL 兼容方法
    # -----------------------------------------------------------------

    def print_output(self, text: str) -> None:
        """命令输出入口（与 NormaREPL.print_output 同名同义）。"""
        self._write_history(Text(_strip_html(text)))

    def clear_screen(self) -> None:
        """清屏（与 NormaREPL.clear_screen 同名同义）。"""
        try:
            self.query_one("#history", RichLog).clear()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 渲染辅助
    # -----------------------------------------------------------------

    def _write_history(self, renderable: RenderableType) -> None:
        try:
            log = self.query_one("#history", RichLog)
            log.write(renderable)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"write history error: {exc}")

    def _write_banner(self) -> None:
        self._write_history(
            Text.from_markup(
                "[bold cyan]Norma Coder[/] [dim]TUI[/]\n"
                "[green]欢迎使用 Norma AI Agent！[/] "
                "[dim]直接输入问题开始对话，/help 查看命令。[/]\n"
            )
        )

    def _refresh_status(self) -> None:
        try:
            checker = getattr(self.agent, "permission_checker", None)
            mode = (
                checker.config.mode.value
                if checker and checker.config
                else "n/a"
            )
            model = getattr(self.agent.llm, "model", "?")
            sm = getattr(self.agent, "session_manager", None)
            sid = sm.current.session_id[:8] if sm and sm.current else "-"
            running = "● 运行中" if self._is_running() else "○ 就绪"
            text = (
                f"[b]模式[/b]={mode}  [b]模型[/b]={model}  "
                f"[b]会话[/b]={sid}  {running}  "
                "[dim](F2 切换模式 / Ctrl+C 中断 / Ctrl+L 清屏)[/dim]"
            )
            self.query_one("#status", Static).update(Text.from_markup(text))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"refresh status error: {exc}")

    def _is_running(self) -> bool:
        return self.runner is not None and self.runner.running

    # ---- 流式区 ----

    def _update_stream(self) -> None:
        try:
            widget = self.query_one("#stream", Static)
            parts = []
            if self._stream_think:
                parts.append(Text(self._stream_think, style="dim italic yellow"))
            if self._stream_text:
                parts.append(Text(self._stream_text, style="white"))
            if parts:
                widget.display = True
                widget.update(Text.assemble(*parts) if len(parts) > 1 else parts[0])
            else:
                widget.display = False
                widget.update("")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"update stream error: {exc}")

    def _commit_stream(self) -> None:
        """把当前流式缓冲落盘到历史区，并清空流式区。"""
        if self._stream_think:
            self._write_history(
                Text.assemble(
                    Text("💭 思考\n", style="bold yellow"),
                    Text(self._stream_think, style="dim italic"),
                )
            )
        if self._stream_text:
            self._write_history(
                Text.assemble(
                    Text("🤖 ", style="bold cyan"),
                    Text(self._stream_text),
                )
            )
        self._stream_text = ""
        self._stream_think = ""
        self._stream_active = False
        try:
            widget = self.query_one("#stream", Static)
            widget.display = False
            widget.update("")
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 总线回调（运行在总线处理器任务中，仅转发消息）
    # -----------------------------------------------------------------

    async def _on_bus_message(self, message: Message) -> None:
        # 投递回 UI 线程渲染，避免在总线任务里直接操作控件
        self.post_message(BusEventMessage(message))

    async def on_bus_event_message(self, message: BusEventMessage) -> None:
        bus = message.bus_message
        mtype = bus.msg_type
        payload = bus.payload

        try:
            if mtype == MessageType.AGENT_TEXT_DELTA and isinstance(
                payload, AgentTextDeltaEvent
            ):
                self._stream_active = True
                self._stream_text += payload.delta
                self._update_stream()

            elif mtype == MessageType.AGENT_THINK_DELTA and isinstance(
                payload, AgentThinkDeltaEvent
            ):
                self._stream_active = True
                self._stream_think += payload.delta
                self._update_stream()

            elif mtype == MessageType.AGENT_THINK and isinstance(
                payload, AgentThinkEvent
            ):
                # 非流式思考事件
                self._write_history(
                    Text.assemble(
                        Text("💭 思考\n", style="bold yellow"),
                        Text(payload.reason_content, style="dim italic"),
                    )
                )

            elif mtype == MessageType.AGENT_TOOL_REQUEST and isinstance(
                payload, AgentToolRequestEvent
            ):
                # 工具调用前先把流式文本落盘
                if self._stream_active:
                    self._commit_stream()
                for tc in payload.tool_calls:
                    self._write_history(
                        Text.assemble(
                            Text("🛠  ", style="bold yellow"),
                            Text(tc.tool_call_name, style="bold"),
                            Text(f"({_format_args(tc.tool_call_arguments)})", style="dim"),
                        )
                    )

            elif mtype == MessageType.AGENT_TOOL_RESULT and isinstance(
                payload, AgentToolRequestAnswerEvent
            ):
                for r in payload.tool_execution_results:
                    style = "red" if r.is_error else "cyan"
                    mark = "✗" if r.is_error else "⚙"
                    self._write_history(
                        Text.assemble(
                            Text(f"{mark} {r.tool_call_name}: ", style=f"bold {style}"),
                            Text(_truncate(str(r.content)), style=style),
                        )
                    )

            elif mtype == MessageType.AGENT_LLM_RESPONSE and isinstance(
                payload, AgentLLMResponseEvent
            ):
                # 流式已在 delta 中展示，这里仅收尾；若无 delta 则补打最终内容
                resp = payload.response
                if not self._stream_active:
                    content = getattr(resp.response_message, "content", "") or ""
                    reason = getattr(resp.response_message, "reason_content", None)
                    if reason:
                        self._write_history(
                            Text.assemble(
                                Text("💭 思考\n", style="bold yellow"),
                                Text(reason, style="dim italic"),
                            )
                        )
                    if content:
                        self._write_history(
                            Text.assemble(
                                Text("🤖 ", style="bold cyan"),
                                Text(content),
                            )
                        )
                self._commit_stream()

            elif mtype == MessageType.AGENT_RESPONSE and isinstance(
                payload, AgentResponse
            ):
                if self._stream_active:
                    self._commit_stream()
                # 回合结束分隔
                self._write_history(Text("─" * 60, style="dim"))

            elif mtype == MessageType.UI_PROMPT:
                # 权限确认请求 -> 弹窗
                request_id = payload.get("request_id")
                prompt = payload.get("prompt", "")
                if request_id:
                    self._push_permission_modal(request_id, prompt)

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"render bus event error: {exc}", exc_info=True)

    # -----------------------------------------------------------------
    # 权限确认弹窗
    # -----------------------------------------------------------------

    def _push_permission_modal(self, request_id: str, prompt: str) -> None:
        def _on_result(result: bool | None) -> None:
            allowed = bool(result)
            # 通过 worker 调用 async 的 respond_confirmation
            self._respond_confirmation(request_id, allowed)

        self.push_screen(PermissionModal(request_id, prompt), _on_result)

    @work(exclusive=False)
    async def _respond_confirmation(self, request_id: str, allowed: bool) -> None:
        try:
            await self.user_input_manager.respond_confirmation(request_id, allowed)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"respond_confirmation error: {exc}")

    # -----------------------------------------------------------------
    # 用户输入
    # -----------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._is_running():
            self._write_history(
                Text("⚠ 当前任务运行中，请等待或按 Ctrl+C 中断。", style="yellow")
            )
            event.input.value = ""
            return

        event.input.value = ""

        if text.startswith("/"):
            await self._handle_command(text)
        else:
            self._write_history(
                Text.assemble(Text("❯ ", style="bold green"), Text(text))
            )
            self._start_agent(text)

    async def _handle_command(self, raw: str) -> None:
        parts = raw.split(maxsplit=1)
        cmd_name = parts[0].lstrip("/") if parts else ""
        if not self.command_registry.lookup(cmd_name):
            self._write_history(Text(f"未知命令: /{cmd_name}", style="red"))
            self._write_history(Text("输入 /help 查看可用命令", style="dim"))
            return
        ctx = CommandContext(repl=self, args=raw)
        try:
            result = await self.command_registry.execute(ctx)
        except Exception as exc:  # noqa: BLE001
            self._write_history(Text(f"命令执行出错: {exc}", style="red"))
            return
        if result is False:
            # 命令请求退出
            self.exit()
        self._refresh_status()

    # -----------------------------------------------------------------
    # 驱动 Agent
    # -----------------------------------------------------------------

    def _start_agent(self, query: str) -> None:
        self.runner = AgentRunner(self.agent)
        self._set_input_enabled(False)
        self._refresh_status()
        task = self.runner.start(query)
        task.add_done_callback(self._on_agent_done)

    def _on_agent_done(self, task: asyncio.Task) -> None:
        ok = True
        error: Optional[str] = None
        try:
            task.result()
        except asyncio.CancelledError:
            ok = False
            self.post_message(TurnFinishedMessage(ok=False, error="已中断"))
            return
        except Exception as exc:  # noqa: BLE001
            ok = False
            error = str(exc)
        self.post_message(TurnFinishedMessage(ok=ok, error=error))

    async def on_turn_finished_message(self, message: TurnFinishedMessage) -> None:
        if self._stream_active:
            self._commit_stream()
        if not message.ok:
            self._write_history(
                Text(
                    f"✗ 任务结束（异常）: {message.error or ''}",
                    style="red",
                )
            )
        self._set_input_enabled(True)
        self._refresh_status()
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass

    def _set_input_enabled(self, enabled: bool) -> None:
        try:
            inp = self.query_one("#input", Input)
            inp.disabled = not enabled
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 按键动作
    # -----------------------------------------------------------------

    def action_interrupt_or_quit(self) -> None:
        if self._is_running() and self.runner is not None:
            self.runner.cancel()
            self._write_history(Text("⚠ 正在中断当前任务…", style="yellow"))
        else:
            self.exit()

    def action_clear(self) -> None:
        self.clear_screen()

    def action_cycle_mode(self) -> None:
        checker = getattr(self.agent, "permission_checker", None)
        if checker is None or checker.config is None:
            self._write_history(Text("未启用权限系统", style="yellow"))
            return
        current = checker.config.mode
        try:
            idx = self._MODE_CYCLE.index(current)
        except ValueError:
            idx = -1
        new_mode = self._MODE_CYCLE[(idx + 1) % len(self._MODE_CYCLE)]
        checker.config.mode = new_mode
        self._write_history(
            Text(f"⇄ 执行模式已切换: {new_mode.value}", style="cyan")
        )
        self._refresh_status()
