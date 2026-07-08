from prompt_toolkit.formatted_text import HTML, merge_formatted_text
import json
import html
from typing import Any
from norma.core.agent_types import (
    AgentEvent,
    AgentLLMResponseEvent,
    AgentResponse,
    AgentToolRequestEvent,
    AgentToolRequestAnswerEvent,
    AgentThinkEvent,
    AgentLLMRequestEvent
)


# todo 使用rich库来丰富前端渲染
class AgentEventRenderer:
    """Agent事件渲染器，将Agent事件转换为可显示的文本格式"""

    def __init__(self):
        #后续添加主题 前端渲染的配置a
        
        pass 

    def render_event(self, agent_event: AgentEvent) -> HTML:
        """
        Renders a specific Agent Event into prompt_toolkit formatted text.

        Args:
            event_type: 事件类型字符串 ("think", "tool_request", "tool_result", etc.)
            content: Agent事件对象

        Returns:
            Any: 渲染后的格式化文本 (FormattedText)，可直接被 prompt_toolkit 打印
        """


        # Body depends on the specific event class
        if isinstance(agent_event, AgentToolRequestEvent):
            body = self._render_tool_request(agent_event)
        elif isinstance(agent_event, AgentToolRequestAnswerEvent):
            body = self._render_tool_result(agent_event)
        elif isinstance(agent_event, AgentLLMResponseEvent):
            body = self._render_llm_response(agent_event)
        elif isinstance(agent_event, AgentResponse):
            body = self._render_final_response(agent_event)
        elif isinstance(agent_event, AgentThinkEvent):
            body = self._render_think_event(agent_event)
        elif isinstance(agent_event, AgentLLMRequestEvent):
            body = self._render_llm_request(agent_event)
        else:
            # Fallback for generic events
            body = self._render_generic_event(agent_event)

        # 使用 merge_formatted_text 将 HTML 对象和换行符组合在一起
        return body



    def _render_tool_request(self, event: AgentToolRequestEvent) -> HTML:
        """Renders the intent to call tools."""
        lines = []
        lines.append(f"<b fg='ansiyellow'>🛠️  Tool Request (Calls: {len(event.tool_calls)})</b>")

        for tool in event.tool_calls:
            # Assuming tool has .name and .arguments attributes
           # tool_name = getattr(tool, 'name', 'Unknown')
            tool_name =tool.tool_call_name
            tool_args = tool.tool_call_arguments

            # Format arguments nicely
            if isinstance(tool_args, str):
                args_display = html.escape(tool_args)
            else:
                args_display = html.escape(json.dumps(tool_args, ensure_ascii=False))

            lines.append(f"   <style fg='ansiyellow'>➜ Calling:</style> <b>{tool_name}</b>")
            lines.append(f"   <style fg='#888888'>Args: {args_display}</style>")

        return HTML("\n".join(lines))

    def _render_tool_result(self, event: AgentToolRequestAnswerEvent) -> HTML:
        """Renders the output/result from tool execution."""
        lines = []
        lines.append("<b fg='ansicyan'>⚙️  Tool Output</b>")

        for result in event.tool_execution_results:
            # Assuming result has content/output
            output_content = getattr(result, 'content', str(result))

            safe_content = html.escape(str(output_content))
            lines.append(f"   <style fg='ansicyan'>➜ Result:</style> {safe_content}")

        return HTML("\n".join(lines))

    def _render_llm_response(self, event: AgentLLMResponseEvent) -> HTML:
        """Renders streaming tokens or intermediate thought processes."""
        text = event.response.content
        return HTML(f"<style fg='ansigreen'>{html.escape(str(text))}</style>")

    def _render_think_event(self, event: AgentThinkEvent) -> HTML:
        """Renders thinking/reasoning content."""
        reason_content = getattr(event, 'reason_content', str(event))
        return HTML(f"<style fg='yellow'>💭 {html.escape(reason_content)}</style>")

    def _render_llm_request(self, event: AgentLLMRequestEvent) -> HTML:
        """Renders LLM request event."""
        request_info = getattr(event, 'request', str(event))
        return HTML(f"<style fg='cyan'>🤖 LLM Request: {html.escape(str(request_info))}</style>")

    def _render_final_response(self, event: AgentResponse) -> HTML:
        """Renders the final conclusion of the agent."""
        # AgentResponse contains response field directly
        #answer = getattr(event, 'response', str(event))
        answer = event.response
        content = (
            f"<b fg='ansimagenta'>✅:</b>\n"
            f"{html.escape(str(answer))}"
        )
        return HTML(content)

    def _render_generic_event(self, event: AgentEvent) -> HTML:
        """Fallback for unknown event types."""
        return HTML(f"<style fg='#888888'>{html.escape(str(event))}</style>")
