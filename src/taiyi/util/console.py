import asyncio
import json
import time
from typing import AsyncGenerator, Optional, List, TypeVar, cast
from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.rule import Rule
from rich.pretty import Pretty
from rich.text import Text
from rich.table import Table

from taiyi.core.agent_types import (
    AgentEvent,
    AgentInputEvent,
    AgentResponse,
    AgentThinkEvent,
    AgentToolRequestEvent,
    AgentLLMRequestEvent,
    AgentLLMResponseEvent,
    AgentToolRequestAnswerEvent,
    BaseAgent,
)
from taiyi.core.tool_types import ToolRequest, ToolRequestResult

T = TypeVar('T', bound=AgentResponse)


async def AgentConsole(
    stream: AsyncGenerator[AgentEvent | AgentResponse, None],
    *,
    output_stats: bool = False,
    show_messages: bool = False,
    compact_mode: bool = False,
) -> AgentResponse:
    """
    Consumes the message stream from agent.run_stream() and renders the events
    to the console with rich formatting.

    Args:
        stream: AsyncGenerator yielding AgentEvent and AgentResponse objects
        output_stats: If True, displays detailed statistics and timing information
        show_messages: If True, displays the LLM messages in detail
        compact_mode: If True, uses a more condensed output format

    Returns:
        The final AgentResponse object
    """
    rich_console = RichConsole()

    async def arich_print(*args, **kwargs) -> None:
        await asyncio.to_thread(rich_console.print, *args, **kwargs)

    start_time = time.time()
    total_tool_calls = 0
    total_llm_calls = 0
    tool_call_chain: List[str] = []

    last_response: Optional[AgentResponse] = None

    async for message in stream:
        if isinstance(message, AgentResponse):
            # Final response received
            duration = time.time() - start_time
            
            # Print completion separator
            await arich_print()
            rule = Rule(
                f"[bold green]{'=' * 20} Agent Complete {'=' * 20}[/bold green]",
                align="center",
                style="bright_green"
            )
            await arich_print(rule)
            await arich_print()
            
            # Display final response in a prominent panel
            response_text = message.response or "[No response generated]"
            response_panel = Panel(
                Text(response_text, style="white"),
                title=f"[bold green]✓ Final Response from {message.agent_name}[/bold green]",
                border_style="bright_green",
                padding=(1, 2),
                expand=False
            )
            await arich_print(response_panel)
            await arich_print()
            
            # Display execution summary in a structured way
            summary_table = Table(
                title="Execution Summary",
                show_header=False,
                border_style="yellow",
                box=None,
                padding=(0, 2)
            )
            summary_table.add_column("Metric", style="cyan bold", no_wrap=True)
            summary_table.add_column("Value", style="white")
            
            summary_table.add_row("Agent Name", message.agent_name)
            summary_table.add_row("Total Events", str(len(message.event_list)))
            summary_table.add_row("LLM Calls", str(total_llm_calls))
            summary_table.add_row("Tool Calls", str(total_tool_calls))
            
            if tool_call_chain:
                tool_chain_str = " → ".join(tool_call_chain)
                summary_table.add_row("Tool Chain", tool_chain_str)
            
            summary_table.add_row("Total Duration", f"{duration:.2f}s")
            summary_table.add_row("Message Count", str(len(message.message_list)))
            
            summary_panel = Panel(
                summary_table,
                title="[bold yellow]📊 Summary[/bold yellow]",
                border_style="yellow",
                expand=False
            )
            await arich_print(summary_panel)
            
            # Display event timeline if stats enabled
            if output_stats and message.event_list:
                await arich_print()
                timeline_table = Table(
                    title="Event Timeline",
                    show_header=True,
                    header_style="bold magenta",
                    border_style="magenta"
                )
                timeline_table.add_column("#", style="dim", width=4)
                timeline_table.add_column("Event Type", style="cyan")
                timeline_table.add_column("Duration", style="yellow", justify="right")
                timeline_table.add_column("Details", style="white")
                
                for idx, event in enumerate(message.event_list, 1):
                    event_type = event.__class__.__name__.replace("Agent", "").replace("Event", "")
                    duration_str = f"{event.duration_seconds:.3f}s"
                    
                    # Generate details based on event type
                    details = ""
                    if isinstance(event, AgentInputEvent):
                        details = f"Task: {event.task[:100]}..."
                    elif isinstance(event, AgentThinkEvent):
                        details = f"{event.reason_content[:]}..."
                  
                    elif isinstance(event, AgentToolRequestEvent):
                        tool_names = [tc.tool_call_name for tc in event.tool_calls]
                        details = f"Tools: {', '.join(tool_names)}"
                    elif isinstance(event, AgentToolRequestAnswerEvent):
                        tool_names = [tr.tool_call_name for tr in event.tool_execution_results]
                        details = f"Tool Answers: {', '.join(tool_names)}"
                    elif isinstance(event, AgentLLMRequestEvent):
                        details = f"LLM Request: {event.request.model}"
                    


                    timeline_table.add_row(
                        str(idx),
                        event_type,
                        duration_str,
                        details
                    )
                
                timeline_panel = Panel(
                    timeline_table,
                    title="[bold magenta]⏱️  Timeline[/bold magenta]",
                    border_style="magenta",
                    expand=False
                )
                await arich_print(timeline_panel)
            
            await arich_print()
            last_response = message

        elif isinstance(message, AgentInputEvent):
            # Display initial task input
            if not compact_mode:
                rule = Rule(
                    f"[bold blue]▶ Agent Started: {message.agent_name}[/bold blue]",
                    align="center",
                    style="blue"
                )
                await arich_print(rule)
            
            task_panel = Panel(
                message.task,
                title="[bold blue]Task Input[/bold blue]",
                border_style="blue",
                expand=False
            )
            await arich_print(task_panel)

        elif isinstance(message, AgentThinkEvent):
            # Display thinking/reasoning
            if not compact_mode:
                think_text = Text()
                think_text.append("💭 ", style="bold magenta")
                think_text.append(message.reason_content, style="dim")
                
                if output_stats:
                    think_text.append(f" ({message.duration_seconds:.3f}s)", style="dim cyan")
                
                await arich_print(think_text)

        elif isinstance(message, AgentLLMResponseEvent):
            # Display LLM call information
            total_llm_calls += 1
            
            if compact_mode:
                llm_text = Text()
                llm_text.append("🤖 LLM Call ", style="bold cyan")
                if output_stats:
                    llm_text.append(f"({message.duration_seconds:.2f}s)", style="dim")
                await arich_print(llm_text)
            else:
                rule = Rule(
                    f"[bold cyan]🤖 LLM Call #{total_llm_calls}[/bold cyan]",
                    align="left",
                    style="cyan"
                )
                await arich_print(rule)
                
                if show_messages and message.resonse.response_message:
                    # Display message history
                    table = Table(
                        title="Messages",
                        show_header=True,
                        header_style="bold cyan",
                        show_lines=True
                    )
                    table.add_column("Role", style="cyan", width=12)
                    table.add_column("Content", style="white")
                    
                    
                    content = message.resonse.response_message.content
                    role = message.resonse.response_message.role
                    table.add_row(role, content)
                    
                    await arich_print(table)
                
                # Display response info
                if output_stats:
                    response_info = (
                        f"[dim]Finish Reason: {message.resonse.response_message} | "
                        f"Duration: {message.duration_seconds:.2f}s[/dim]"
                    )
                    await arich_print(response_info)

        elif isinstance(message, AgentToolRequestEvent):
            # Display tool calls and results
            if not compact_mode:
                rule = Rule(
                    "[bold yellow]🔧 Tool Execution[/bold yellow]",
                    align="left",
                    style="yellow"
                )
                await arich_print(rule)
            
            # Display each tool call
            for tool_call in message.tool_calls:
                total_tool_calls += 1
                tool_call_chain.append(tool_call.tool_call_name)
                
                # Parse arguments for pretty printing
                try:
                    if  isinstance(tool_call.tool_call_arguments,str):
                        args_dict = json.loads(tool_call.tool_call_arguments)
                    else :
                        args_dict = tool_call.tool_call_arguments
                except (json.JSONDecodeError, TypeError, AttributeError):
                    args_dict = {"raw": str(tool_call.tool_call_arguments)}
                
                if compact_mode:
                    tool_text = Text()
                    tool_text.append(f"🔧 {tool_call.tool_call_name}", style="bold yellow")
                    tool_text.append(f"({', '.join(args_dict.keys())})", style="dim")
                    await arich_print(tool_text)
                else:
                    pretty_args = Pretty(args_dict, max_length=10, max_string=100)
                    tool_panel = Panel(
                        pretty_args,
                        title=f"[bold yellow]🔧 Tool: {tool_call.tool_call_name}[/bold yellow]",
                        border_style="yellow",
                        title_align="left",
                        expand=False
                    )
                    await arich_print(tool_panel)
            
            # Display tool results
            for result in message.tool_execution_results:
                if result.is_error:
                    result_panel = Panel(
                        f"[red]{result.content}[/red]",
                        title=f"[bold red]❌ Error: {result.tool_call_name}[/bold red]",
                        border_style="red",
                        expand=False
                    )
                else:
                    content = str(result.content)[:500]
                    if len(str(result.content)) > 500:
                        content += f"\n... ({len(str(result.content)) - 500} more chars)"
                    
                    result_panel = Panel(
                        content,
                        title=f"[bold green]✓ Result: {result.tool_call_name}[/bold green]",
                        border_style="green",
                        expand=False
                    )
                
                if not compact_mode:
                    await arich_print(result_panel)
            
            if output_stats:
                await arich_print(
                    f"[dim]Tool execution time: {message.duration_seconds:.2f}s[/dim]"
                )

        elif isinstance(message, AgentToolRequestAnswerEvent):
            # Display tool answer results
            if not compact_mode:
                rule = Rule(
                    "[bold green]🔧 Tool Answers[/bold green]",
                    align="left",
                    style="green"
                )
                await arich_print(rule)

            # Display each tool answer
            for result in message.tool_execution_results:
                total_tool_calls += 1
                if result.is_error:
                    result_panel = Panel(
                        f"[red]{result.content}[/red]",
                        title=f"[bold red]❌ Error: {result.tool_call_name}[/bold red]",
                        border_style="red",
                        expand=False
                    )
                else:
                    content = str(result.content)[:500]
                    if len(str(result.content)) > 500:
                        content += f"\n... ({len(str(result.content)) - 500} more chars)"

                    result_panel = Panel(
                        content,
                        title=f"[bold green]✓ Answer: {result.tool_call_name}[/bold green]",
                        border_style="green",
                        expand=False
                    )

                if not compact_mode:
                    await arich_print(result_panel)
                elif compact_mode:
                    answer_text = Text()
                    if result.is_error:
                        answer_text.append(f"❌ {result.tool_call_name}", style="bold red")
                    else:
                        answer_text.append(f"✓ {result.tool_call_name}", style="bold green")
                    await arich_print(answer_text)

            if output_stats:
                await arich_print(
                    f"[dim]Tool answer time: {message.duration_seconds:.2f}s[/dim]"
                )

        elif isinstance(message, AgentLLMRequestEvent):
            # Display LLM request information
            total_llm_calls += 1

            if compact_mode:
                llm_text = Text()
                llm_text.append("🤖 LLM Request ", style="bold cyan")
                if output_stats:
                    llm_text.append(f"({message.duration_seconds:.2f}s)", style="dim")
                await arich_print(llm_text)
            else:
                rule = Rule(
                    f"[bold cyan]🤖 LLM Request #{total_llm_calls}[/bold cyan]",
                    align="left",
                    style="cyan"
                )
                await arich_print(rule)

                # Display request info
                if hasattr(message.request, 'model'):
                    model_info = f"[dim]Model: {message.request.model}[/dim]"
                    await arich_print(model_info)

                if show_messages and hasattr(message.request, 'messages'):
                    # Display message history
                    table = Table(
                        title="Request Messages",
                        show_header=True,
                        header_style="bold cyan",
                        show_lines=True
                    )
                    table.add_column("Role", style="cyan", width=12)
                    table.add_column("Content", style="white")

                    for msg in message.request.messages:
                        content = str(msg.content)[:200]
                        if len(str(msg.content)) > 200:
                            content += "..."
                        table.add_row(msg.role, content)

                    await arich_print(table)

                # Display request info
                if output_stats:
                    request_info = (
                        f"[dim]Duration: {message.duration_seconds:.2f}s[/dim]"
                    )
                    await arich_print(request_info)

    if last_response is None:
        raise ValueError("Stream completed without producing an AgentResponse")

    return last_response

