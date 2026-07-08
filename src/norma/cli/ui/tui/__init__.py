"""Norma Coder Textual TUI 前端。

通过订阅进程内 MessageBus 渲染 Agent 事件，实现前后端解耦。
入口应用 :class:`norma.cli.ui.tui.app.NormaApp`。
"""

from norma.cli.ui.tui.app import NormaApp

__all__ = ["NormaApp"]
