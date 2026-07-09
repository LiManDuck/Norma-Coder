#!/usr/bin/env python3
"""
Norma AI Agent 命令行界面
主入口点，初始化并启动 REPL

更新（2026-06-15）
------------------
- 加载 MCP 服务器配置并连接
- 支持多 Provider 模型配置
- 集成 command 系统
"""
import argparse
import asyncio
import sys
import json
import os
from urllib.parse import urlparse
from typing import Optional
from pathlib import Path

from rich.console import Console

from norma.core.agent_types import BaseAgent
from norma.agent.norma_coder import NormaCoder
from norma.core.openai_llm import OpenAILLM
from norma.cli.repl.repl import NormaREPL

from norma.messagebus.messagebus import (
    MessageBus,
    UserInputManager,
)
from norma.permission import (
    PermissionChecker,
    PermissionConfig,
)
from norma.hook import (
    HookConfig,
    HookEvent,
    HookManager,
)
from norma.reminder import ReminderRegistry
from norma.reminder.task_reminder import TaskNudgeReminder
from norma.skill import SkillRegistry
from norma.mcp import MCPManager
from norma.session import SessionManager


class NormaCLI:
    """Norma AI Agent 命令行界面的主控制器"""

    def __init__(
        self,
        resume_session: Optional[str] = None,
        model_override: Optional[str] = None,
        config_path: Optional[str] = None,
    ):
        self.console = Console()
        self.agent: Optional[BaseAgent] = None
        self.llm: Optional[OpenAILLM] = None
        self.config = self.load_config(config_path=config_path)
        # --model 覆盖配置中的模型名（优先级高于配置文件）
        if model_override:
            self.config["model"] = model_override
        self.resume_session = resume_session
        self._setup_proxy()

        # ---- messagebus / 权限 / hook ----
        self.message_bus = MessageBus()
        self.user_input_manager = UserInputManager(self.message_bus)
        self.permission_checker = PermissionChecker(
            config=PermissionConfig.from_dict(self.config.get("permission"))
        )
        self.hook_manager = HookManager(
            config=HookConfig.from_dict(self.config.get("hooks"))
        )
        self.hook_manager.attach(self.message_bus)

        # ---- reminder / skill ----
        self.reminder_registry = ReminderRegistry()
        self.reminder_registry.register(TaskNudgeReminder())
        self.skill_registry = SkillRegistry.from_default_dirs(cwd=Path.cwd())
        if self.skill_registry.all():
            self.console.print(
                f"[green]✓ Loaded {len(self.skill_registry.all())} skill(s): "
                f"{', '.join(self.skill_registry.names())}[/green]"
            )

        # ---- MCP ----
        self.mcp_manager = MCPManager()
        self.mcp_manager.load_config(self.config)

        # ---- session ----
        self.session_manager = SessionManager(cwd=os.getcwd())

        self._init_agent()

    # ----------------------- 配置 / 代理 -----------------------

    def _setup_proxy(self):
        base_url = self.config.get("base_url", "")
        parsed = urlparse(base_url)
        hostname = parsed.hostname

        no_proxy_list = [
            '127.0.0.1', 'localhost',
        ]
        if hostname:
            no_proxy_list.append(hostname)
            if not hostname.replace('.', '').isdigit():
                no_proxy_list.append(f'*.{hostname}')

        os.environ['NO_PROXY'] = ','.join(no_proxy_list)
        os.environ['no_proxy'] = os.environ['NO_PROXY']

    def load_config(self, config_path: Optional[str] = None) -> dict:
        from norma.session.session import get_config_home

        default_config = {
            "model": "glm-4.5-air",
            "api_key": "sk-1234",
            "base_url": "http://api.openai.rnd.huawei.com/v1",
            "stream_mode": True,
            "permission": {
                "mode": "auto",
                "tools": {},
            },
            "hooks": {},
            "providers": {},
            "mcpServers": {},
        }

        # --config 显式指定：仅读取，缺失时不自动创建（避免在任意路径落盘）
        if config_path:
            cp = Path(config_path).expanduser()
            if cp.exists():
                try:
                    with open(cp, 'r', encoding='utf-8') as f:
                        file_config = json.loads(f.read().strip())
                    default_config.update(file_config)
                except Exception as e:
                    self.console.print(f"[red]读取配置文件失败: {e}[/red]")
            else:
                self.console.print(
                    f"[yellow]配置文件不存在: {cp}，使用默认配置[/yellow]"
                )
            return default_config

        # 默认：~/.norma/config.json（受 NORMA_CONFIG_HOME 覆盖，与 session 存储一致）
        config_path_obj = get_config_home() / "config.json"
        if config_path_obj.exists():
            try:
                with open(config_path_obj, 'r', encoding='utf-8') as f:
                    file_config = json.loads(f.read().strip())
                default_config.update(file_config)
            except Exception as e:
                self.console.print(f"[red]读取配置文件失败: {e}[/red]")
        else:
            try:
                config_path_obj.parent.mkdir(exist_ok=True, parents=True)
                with open(config_path_obj, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2, ensure_ascii=False)
                self.console.print(f"[green]已创建配置文件: {config_path_obj}[/green]")
            except Exception as e:
                self.console.print(
                    f"[yellow]创建配置文件失败: {e}，使用默认配置[/yellow]"
                )
        return default_config

    # ----------------------- LLM / Provider -----------------------

    def _resolve_llm(self) -> OpenAILLM:
        """根据配置解析 LLM 实例（支持多 Provider）"""
        providers = self.config.get("providers", {})
        default_provider = self.config.get("default_provider")
        model = self.config.get("model", "")
        base_url = self.config.get("base_url", "")
        api_key = self.config.get("api_key", "")

        # 如果有 default_provider 配置且 providers 中存在
        active_url = base_url
        active_key = api_key
        if default_provider and default_provider in providers:
            prov = providers[default_provider]
            active_url = prov.get("url", base_url)
            active_key = prov.get("api_key", api_key)
            if "models" in prov and model not in prov.get("models", []):
                prov_models = prov.get("models", [])
                if prov_models:
                    model = prov_models[0]

        return OpenAILLM(
            model=model,
            api_key=active_key,
            base_url=active_url,
            providers=providers,
            default_provider=default_provider,
            default_stream_mode=self.config.get("stream_mode", True),
        )

    # ----------------------- Agent -----------------------

    def _init_agent(self):
        try:
            self.llm = self._resolve_llm()

            # 创建或恢复 session
            if self.resume_session:
                record = self.session_manager.load(self.resume_session)
                if record is None:
                    self.console.print(
                        f"[yellow]Session {self.resume_session} 不存在，创建新 session[/yellow]"
                    )
                    record = self.session_manager.create()
            else:
                record = self.session_manager.create()

            self.agent = NormaCoder(
                name="normacoder",
                llm=self.llm,
                cwd=os.getcwd(),
                message_bus=self.message_bus,
                permission_checker=self.permission_checker,
                hook_manager=self.hook_manager,
                user_input_manager=self.user_input_manager,
                reminder_registry=self.reminder_registry,
                skill_registry=self.skill_registry,
                session_manager=self.session_manager,
                conversation_id=record.session_id,
            )

            # 如果是 resume 则在 async run() 时恢复消息
            self._pending_restore = self.resume_session if self.resume_session else None

            self.console.print(
                f"[green]✓ Agent初始化成功 "
                f"(permission={self.permission_checker.config.mode.value}, "
                f"session={record.session_id[:8]})[/green]"
            )
        except Exception as e:
            self.console.print(f"[red]Agent初始化失败: {e}[/red]")
            sys.exit(1)

    # ----------------------- 运行 -----------------------

    async def run(self, use_repl: bool = False):
        await self.message_bus.start()
        try:
            # 如有待恢复的 session，先把消息载入 memory
            if getattr(self, "_pending_restore", None):
                try:
                    n = await self.agent.restore_from_session(self._pending_restore)
                    self.console.print(
                        f"[green]✓ 已恢复 session {self._pending_restore[:8]}，"
                        f"加载 {n} 条历史消息[/green]"
                    )
                except Exception as e:
                    self.console.print(f"[yellow]恢复 session 失败: {e}[/yellow]")

            # 连接 MCP 服务器
            if self.mcp_manager.clients:
                self.console.print("[dim]正在连接 MCP 服务器...[/dim]")
                await self.mcp_manager.connect_all()
                mcp_tools = self.mcp_manager.tools
                if mcp_tools:
                    for tool in mcp_tools:
                        self.agent.tool_manager.register_tool(tool)
                    self.console.print(
                        f"[green]✓ 已加载 {len(mcp_tools)} 个 MCP 工具[/green]"
                    )

            await self.hook_manager.dispatch(HookEvent.SESSION_BEGIN)

            if use_repl:
                # 旧版 prompt_toolkit REPL（兜底）
                repl = NormaREPL(agent=self.agent, cwd=os.getcwd())
                await repl.run()
            else:
                # 默认：Textual TUI（前后端经 MessageBus 解耦）
                from norma.cli.ui.tui.app import NormaApp

                app = NormaApp(
                    agent=self.agent,
                    cwd=os.getcwd(),
                    message_bus=self.message_bus,
                    user_input_manager=self.user_input_manager,
                )
                await app.run_async()
        finally:
            try:
                await self.hook_manager.dispatch(HookEvent.SESSION_END)
            except Exception:
                pass
            try:
                self.session_manager.close()
            except Exception:
                pass
            await self.mcp_manager.disconnect_all()
            await self.message_bus.stop()


def main():
    # Windows 默认 GBK 控制台无法输出 ✓ 等 Unicode 字符，强制 UTF-8 输出
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description='Norma AI Agent - 智能编码助手',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  norma                    # 启动交互式会话
  norma --model glm-4      # 指定模型
        """,
    )
    parser.add_argument(
        '--model', type=str, help='指定模型名称'
    )
    parser.add_argument(
        '--config', type=str, help='配置文件路径'
    )
    parser.add_argument(
        '--resume', '-r', type=str, default=None,
        help='恢复指定 session_id 的会话'
    )
    parser.add_argument(
        '--repl', action='store_true', default=False,
        help='使用旧版 prompt_toolkit REPL（默认启动 Textual TUI）'
    )
    args = parser.parse_args()

    try:
        cli = NormaCLI(
            resume_session=args.resume,
            model_override=args.model,
            config_path=args.config,
        )
        asyncio.run(cli.run(use_repl=args.repl))
    except KeyboardInterrupt:
        print("\n\n程序已退出")
        sys.exit(0)
    except Exception as e:
        Console().print(f"[red]启动失败: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
