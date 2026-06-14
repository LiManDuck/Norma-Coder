#!/usr/bin/env python3
"""
Norma AI Agent 命令行界面
主入口点，初始化并启动 REPL

更新（2026-06-14）
------------------
- 加载 ``permission`` / ``hooks`` 配置
- 启动 messagebus 并把 PermissionChecker / HookManager / UserInputManager
  注入到 NormaCoder 中
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


class NormaCLI:
    """Norma AI Agent 命令行界面的主控制器"""

    def __init__(self):
        self.console = Console()
        self.agent: Optional[BaseAgent] = None
        self.llm: Optional[OpenAILLM] = None
        self.config = self.load_config()
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

        self._init_agent()

    # ----------------------- 配置 / 代理 -----------------------

    def _setup_proxy(self):
        base_url = self.config.get("base_url", "")
        parsed = urlparse(base_url)
        hostname = parsed.hostname

        no_proxy_list = [
            '127.0.0.1', 'localhost',
            '*.huawei.com', 'huawei.com',
        ]
        if hostname:
            no_proxy_list.append(hostname)
            if not hostname.replace('.', '').isdigit():
                no_proxy_list.append(f'*.{hostname}')

        os.environ['NO_PROXY'] = ','.join(no_proxy_list)
        os.environ['no_proxy'] = os.environ['NO_PROXY']
        self.console.print(f"[dim]NO_PROXY: {os.environ['NO_PROXY']}[/dim]")

    def load_config(self) -> dict:
        config_path = Path.home() / ".norma" / "config.json"
        default_config = {
            "model": "glm-4.5-air",
            "api_key": "sk-1234",
            "base_url": "http://api.openai.rnd.huawei.com/v1",
            "stream_mode": False,
            "permission": {
                "mode": "auto",
                "tools": {},
            },
            "hooks": {},
        }
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    file_config = json.loads(f.read().strip())
                default_config.update(file_config)
            except Exception as e:
                self.console.print(f"[red]读取配置文件失败: {e}[/red]")
        else:
            try:
                config_path.parent.mkdir(exist_ok=True, parents=True)
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2, ensure_ascii=False)
                self.console.print(f"[green]已创建配置文件: {config_path}[/green]")
            except Exception as e:
                self.console.print(
                    f"[yellow]创建配置文件失败: {e}，使用默认配置[/yellow]"
                )
        return default_config

    # ----------------------- Agent -----------------------

    def _init_agent(self):
        try:
            self.llm = OpenAILLM(
                model=self.config["model"],
                api_key=self.config["api_key"],
                base_url=self.config["base_url"],
            )
            self.agent = NormaCoder(
                name="normacoder",
                llm=self.llm,
                cwd=os.getcwd(),
                message_bus=self.message_bus,
                permission_checker=self.permission_checker,
                hook_manager=self.hook_manager,
                user_input_manager=self.user_input_manager,
            )
            self.console.print(
                f"[green]✓ Agent初始化成功 "
                f"(permission={self.permission_checker.config.mode.value})[/green]"
            )
        except Exception as e:
            self.console.print(f"[red]Agent初始化失败: {e}[/red]")
            sys.exit(1)

    # ----------------------- 运行 -----------------------

    async def run(self):
        await self.message_bus.start()
        try:
            await self.hook_manager.dispatch(HookEvent.SESSION_BEGIN)
            repl = NormaREPL(agent=self.agent, cwd=os.getcwd())
            await repl.run()
        finally:
            try:
                await self.hook_manager.dispatch(HookEvent.SESSION_END)
            except Exception:
                pass
            await self.message_bus.stop()


def create_parser():
    parser = argparse.ArgumentParser(
        prog="norma",
        description="Norma - Norma AI Agent 智能命令行助手",
    )
    parser.add_argument("--model", help="指定使用的模型名称")
    parser.add_argument("--api-key", help="指定API密钥")
    parser.add_argument("--base-url", help="指定API基础URL")
    parser.add_argument("--query", help="执行单个查询后退出")
    parser.add_argument("--stream", action="store_true", help="启用流式输出")
    parser.add_argument(
        "--show-config", action="store_true", help="显示当前配置并退出"
    )
    parser.add_argument(
        "--version", action="version", version="Norma AI Agent v0.1.0"
    )
    return parser


def main():
    parser = argparse.ArgumentParser(
        description='Norma AI Agent - 智能编码助手',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  norma                    # 启动交互式会话
  norma --config custom    # 使用自定义配置
        """,
    )
    parser.add_argument(
        '--config', type=str, help='配置文件路径（可选）'
    )
    parser.parse_args()

    try:
        cli = NormaCLI()
        asyncio.run(cli.run())
    except KeyboardInterrupt:
        print("\n\n👋 程序已退出")
        sys.exit(0)
    except Exception as e:
        Console().print(f"[red]启动失败: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
