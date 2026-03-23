import asyncio
from pathlib import Path
from typing import Optional
from prompt_toolkit import PromptSession
from prompt_toolkit.layout import Layout
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText, HTML
from rich.console import Console
from rich.live import Live
from rich.text import Text

from prompt_toolkit import print_formatted_text

import os

from norma.core.agent_types import BaseAgent, AgentEvent
from norma.core.agent_types import (
    AgentResponse,
    AgentThinkEvent,
    AgentToolRequestEvent,
    AgentLLMRequestEvent,
    AgentLLMResponseEvent,
    AgentToolRequestAnswerEvent,
    AgentResponseEvent,
    AgentInputEvent
)

from ..ui.prompt import (
    get_banner_content,
    get_bottom_toolbar
)

from ..ui.render import AgentEventRenderer
from ..ui.styles import get_style

completer = WordCompleter(['/help', '/exit', '/status'])


class NormaREPL:
    """简洁版 REPL - 类似 Claude Code 风格"""

    def __init__(self, agent: BaseAgent, cwd: str | Path ):
        self.agent = agent
        self.agent_render = AgentEventRenderer()
        self.running = True
        self.cwd = Path(cwd)  
        
        # 创建历史记录目录
        history_dir = Path.home() / ".norma"
        history_dir.mkdir(exist_ok=True)

        # 命令补全
        self.completer = WordCompleter(
            ['/help', '/exit', '/clear', '/status', '/reset'],
            ignore_case=True
        )

        # 配置会话
        self.session = PromptSession(
         #   history=FileHistory(str(history_dir / "history")),
            completer=self.completer,
         #   auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
        )
    
    def show_banner(self):
        """显示启动横幅"""
        banner = HTML("""
<style fg='ansiblue' bold>Taiyi AI Agent CLI v1.0</style>

<style fg='ansigreen'>欢迎使用 Taiyi AI Agent!</style>

<b>可用命令:</b>
  <style fg='ansiyellow'>/exit</style>    - 退出程序
  <style fg='ansiyellow'>/clear</style>   - 清空屏幕

<style fg='#888888'>提示: 直接输入问题开始对话，使用 Ctrl+D 或 /exit 退出</style>
""")
        print_formatted_text(banner)
        print()


   
    
    
    
    async def handle_command(self, command: str) -> bool:
        """
        处理斜杠命令
        
        Returns:
            bool: True表示继续运行，False表示退出
        """
        command = command.strip().lower()
        
        if command == '/exit':
            print_formatted_text(HTML("<style fg='ansigreen'>👋 再见！</style>"))
            return False
            
        #elif command == '/help':
         #   self.show_help()
            
        elif command == '/clear':
            os.system('clear' if os.name != 'nt' else 'cls')
            self.show_banner()
            
        #lif command == '/status':
        #    self.show_status()
            
        #elif command == '/reset':
        #    # 如果你的Agent有重置方法，在这里调用
        #    print_formatted_text(HTML("<style fg='ansigreen'>✓ 对话历史已重置</style>"))
        #    print()
            
        else:
            print_formatted_text(HTML(f"<style fg='ansired'>未知命令: {command}</style>"))
            print_formatted_text(HTML("<style fg='#888888'>输入 /help 查看可用命令</style>"))
            print()
            
        return True

    
    async def run(self):
        """主循环 - 启动REPL"""
        # 显示启动横幅
        print("Taiyi AI Agent CLI v1.0")
        print("欢迎使用 Taiyi AI Agent!")
        print()
        
        while self.running:
            try:
                # 获取用户输入
                user_input = await self.session.prompt_async(
                    HTML('<b fg="ansiblue"> 输入> </b> '),
                    # 可以添加底部工具栏
                    # bottom_toolbar=lambda: HTML('<style fg="#888888">Taiyi Agent | Ctrl+D 退出</style>')
                )
                
                # 去除首尾空白
                user_input = user_input.strip()
                
                # 空输入则跳过
                if not user_input:
                    continue
                
                # 处理命令
                if user_input.startswith('/'):
                    should_continue = await self.handle_command(user_input)
                    if not should_continue:
                        break
                else:
                    # 处理普通对话
                    await self.process_user_input(user_input)
                    
            except KeyboardInterrupt:
            # Ctrl+C 直接退出
                print_formatted_text(HTML("\n<style fg='ansiyellow'>⚠️  已中断，正在退出...</style>"))
                break
                
            except EOFError:
                # Ctrl+D 退出
                print_formatted_text(HTML("\n<style fg='ansigreen'>👋 再见！</style>"))
                break
                
            except Exception as e:
                print_formatted_text(HTML(f"\n<style fg='ansired'>系统错误: {str(e)}</style>"))
                continue
        
    
        
    async def process_user_input(self, user_input: str):
        """
        处理用户输入并流式显示Agent响应
        """
        print()  # 添加空行分隔
        
        try:
            # 调用Agent处理
            async for event in self.agent.run(user_input):
                # 使用你的渲染器格式化事件
                formatted_output = self.agent_render.render_event(
                    agent_event= event
                )
                
                if isinstance(event, AgentThinkEvent):
                    continue
                if isinstance(event,AgentLLMRequestEvent):
                    continue
                if isinstance(event,AgentInputEvent):
                    continue
                if isinstance(event, AgentResponse):
                    break
                # 打印格式化的输出
                print_formatted_text(formatted_output)
                
                # 如果是最终响应，跳出循环
                
                    
        except KeyboardInterrupt:
            print_formatted_text(HTML("\n<style fg='ansiyellow'>⚠️  操作已取消</style>"))
        except Exception as e:
            print_formatted_text(HTML(f"\n<style fg='ansired'>❌ 错误: {str(e)}</style>"))
        
        print()  # 添加空行
