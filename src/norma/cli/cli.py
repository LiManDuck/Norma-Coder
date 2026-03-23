#!/usr/bin/env python3
"""
Norma AI Agent 命令行界面
主入口点，初始化并启动REPL
"""
import argparse
import asyncio
import sys
import json
import os
from urllib.parse import urlparse
from typing import Optional
from pathlib import Path

# Rich库用于美化输出
from rich.console import Console
from rich.table import Table

# Norma核心模块
from norma.agent.chat_agent import ChatAgent
from norma.core.agent_types import (
    BaseAgent
)
from norma.agent.norma_coder import NormaCoder
from norma.core.openai_llm import OpenAILLM
from norma.cli.repl.repl import NormaREPL
import os


class NormaCLI:
    """Norma AI Agent 命令行界面的主控制器"""

    def __init__(self
        
        ):
        self.console = Console()
        self.agent: Optional[BaseAgent] = None
        self.llm: Optional[OpenAILLM] = None
        self.config = self.load_config()
        self._setup_proxy()

        # 加载配置并初始化Agent
        self._init_agent()
    def _setup_proxy(self):
        """根据配置的 base_url 设置代理排除"""
        base_url = self.config.get("base_url", "")
        
        # 解析 URL 获取主机名
        parsed = urlparse(base_url)
        hostname = parsed.hostname  # 自动去除端口和路径
        
        # 构建 NO_PROXY 列表
        no_proxy_list = [
            '127.0.0.1',
            'localhost',
            '*.huawei.com',
            'huawei.com',
        ]
        
        # 如果有主机名，添加到列表
        if hostname:
            no_proxy_list.append(hostname)
            # 如果是域名，也添加通配符版本
            if not hostname.replace('.', '').isdigit():  # 不是纯 IP
                no_proxy_list.append(f'*.{hostname}')
        
        # 设置环境变量
        os.environ['NO_PROXY'] = ','.join(no_proxy_list)
        os.environ['no_proxy'] = os.environ['NO_PROXY']

        
        # 调试输出
        self.console.print(f"[dim]NO_PROXY: {os.environ['NO_PROXY']}[/dim]")
    def load_config(self) -> dict:
        """从配置文件加载设置

        优先级: 命令行参数 > 配置文件 > 默认值
        """
        # 配置文件路径
        config_path = Path.home() / ".norma" / "config.json"

        # 默认配置
        default_config = {
            "model": "glm-4.5-air",
            "api_key": "sk-1234",
            "base_url": "http://api.openai.rnd.huawei.com/v1",
            "stream_mode": False,
        }

        # 如果配置文件存在则加载
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:

                    config_str = f.read().strip()
                    file_config = json.loads(config_str)
                    # 合并配置（文件配置覆盖默认配置）
                    default_config.update(file_config)
            except Exception as e:
                self.console.print(f"[red]读取配置文件失败: {e}[/red]")
        else:
            # 创建配置文件
            try:
                config_path.parent.mkdir(exist_ok=True, parents=True)
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2, ensure_ascii=False)
                self.console.print(f"[green]已创建配置文件: {config_path}[/green]")
            except Exception as e:
                self.console.print(f"[yellow]创建配置文件失败: {e}，使用默认配置[/yellow]")

        return default_config

    def _init_agent(self):
        """初始化Agent和LLM"""
        try:
            # 初始化LLM
            self.llm = OpenAILLM(
                model=self.config["model"],
                api_key=self.config["api_key"],
                base_url=self.config["base_url"],
            )

            self.agent = NormaCoder(
                name="normacoder",
                llm=self.llm,
                cwd =os.getcwd()
            )

            self.console.print("[green]✓ Agent初始化成功[/green]")

        except Exception as e:
            self.console.print(f"[red]Agent初始化失败: {e}[/red]")
            sys.exit(1)

    async def run(self):
        """启动REPL交互循环"""
        # 设置代理地址（如果需要）
    

        # 创建REPL实例并运行
        repl = NormaREPL(
            agent=self.agent,
            cwd=os.getcwd()
        )
        
        await repl.run()
     

def create_parser():
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="norma",
        description="Norma - Norma AI Agent 智能命令行助手"
    )

    # 模型配置
    parser.add_argument("--model", help="指定使用的模型名称")
    parser.add_argument("--api-key", help="指定API密钥")
    parser.add_argument("--base-url", help="指定API基础URL")

    # 运行选项
    parser.add_argument("--query", help="执行单个查询后退出")
    parser.add_argument("--stream", action="store_true", help="启用流式输出")
    parser.add_argument("--show-config", action="store_true", help="显示当前配置并退出")
    parser.add_argument("--version", action="version", version="Norma AI Agent v0.1.0")

    return parser






def main():
    """命令行入口点"""
    parser = argparse.ArgumentParser(
        description='Norma AI Agent - 智能编码助手',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  norma                    # 启动交互式会话
  norma --config custom    # 使用自定义配置
        """
    )
    
    parser.add_argument(
        '--config',
        type=str,
        help='配置文件路径（可选）'
    )
    
    args = parser.parse_args()

    try:
        cli = NormaCLI()
        asyncio.run(cli.run())
    except KeyboardInterrupt:
        print("\n\n👋 程序已退出")
        sys.exit(0)
    except Exception as e:
        console = Console()
        console.print(f"[red]启动失败: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":

    main()
