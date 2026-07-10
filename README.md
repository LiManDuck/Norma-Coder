# Norma-Coder

轻量但五脏俱全的 Python codeagent CLI/TUI。参考 [claude-code](https://github.com/anthropic/claude-code) 与 hermes-agent 的架构，单进程内以 **MessageBus 事件机制** 解耦前后端，默认提供 Textual TUI 前端（亦保留 prompt_toolkit REPL 兜底）。

## 特性

- **TUI 前端**（Textual 8.x）：滚动历史、流式输出区、状态栏、输入框、权限确认弹层、Footer；`Ctrl+C` 中断/退出、`Ctrl+L` 清屏、`F2` 切换权限模式。
- **流式**：`stream_chat` 逐增量产出文本/推理 delta，前端实时渲染。
- **事件解耦**：`MessageBus` pub/sub；`AgentRunner` 把 `agent.run()` 生成器桥接到总线，前端订阅渲染；ASK 权限闭环（弹层 -> 确认/拒绝回送）。
- **MCP**：stdio JSON-RPC 2.0 客户端，工具自动注册并加 `mcp__server__tool` 前缀，支持 `readOnlyHint` 注解。
- **Skills**：markdown + frontmatter，`<cwd>/.norma/skills/` 覆盖 `~/.norma/skills/`，经 `Skill` 工具调度子 agent。
- **工具集**：`Read Ls Glob Grep Edit Write bash TaskCreate TaskList TaskGet TaskUpdate Agent`（+ Skill + MCP）；只读工具并发、写工具串行的分区执行。
- **会话**：jsonl 持久化，`/resume` 恢复；compaction 写入 `compact_boundary` 边界，resume 不重放全量历史。
- **上下文压缩**：分层 compaction -- 先微压缩（截断旧 tool_result，无 LLM 调用），仍超阈值再做完整 LLM 摘要。
- **结构化系统提示**：核心指令 md + 环境段 + 项目记忆 `CLAUDE.md`（用户级 `~/.norma/CLAUDE.md` + 项目级，自 cwd 向上遍历祖先目录）。
- **权限**：`plan / edit / auto` 三模式 + per-tool `allow/ask/deny`。
- **Hook**：在 `session-begin/end`、`user-input`、`tool-execute-before/after`、`agent-response` 事件触发外部 shell 命令，注入 `NORMA_HOOK_EVENT`/`TOOL_NAME`/`USER_INPUT` 等环境变量，支持 `match` 过滤。
- **Reminder**：按事件注入 `<system-reminder>`，默认 `TaskNudgeReminder` 提醒使用 Task 工具。

## 安装

```bash
git clone <本项目>
cd Norma-Coder
pip install -r requirements.txt
pip install -e .
```

终端执行 `norma`，显示 TUI 输入框即安装成功。

## 配置

配置文件位于 `~/.norma/config.json`（首次运行自动生成默认配置）：

```jsonc
{
  "model": "glm-4.5-air",
  "api_key": "sk-你的key",
  "base_url": "http://your-llm-endpoint/v1",
  "stream_mode": true,
  "default_provider": "huawei",      // 可选：多 provider 切换
  "providers": {
    "huawei": { "url": "...", "api_key": "...", "models": ["glm-4.5-air"] }
  },
  "permission": { "mode": "auto", "tools": {} },
  "hooks": {
    "tool-execute-after": [{ "command": "echo $TOOL_NAME", "match": { "tool_name": "Edit" } }]
  },
  "mcpServers": {
    "fs": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
  }
}
```

- **Skills**：把 `*.md`（frontmatter + 正文）放进 `~/.norma/skills/` 或 `<cwd>/.norma/skills/`。
- **CLAUDE.md**（项目指令）：放在 `<cwd>/CLAUDE.md`（向上遍历祖先目录都会被收集）或 `~/.norma/CLAUDE.md`（用户全局）。
- **会话文件**：`~/.norma/projects/< sanitized-cwd >/<session_id>.jsonl`。

## 运行

```bash
norma            # 默认 Textual TUI
norma --repl     # 旧版 prompt_toolkit REPL（兜底）
```

**TUI 按键**：`F2` 切换权限模式（plan→edit→auto）、`Ctrl+C` 运行中中断/空闲退出、`Ctrl+L` 清屏。

**斜杠命令**：`/new` `/help` `/exit` `/clear` `/model` `/compact` `/status` `/resume` `/session`。

## Python SDK

```python
import asyncio
from norma import NormaCoder, OpenAILLM, PermissionChecker, PermissionConfig, PermissionMode

llm = OpenAILLM(model="glm-4.5-air", api_key="sk-...", base_url="http://.../v1")
agent = NormaCoder(
    llm=llm,
    cwd=".",
    permission_checker=PermissionChecker(PermissionConfig(mode=PermissionMode.AUTO)),
    # 还可注入: hook_manager / reminder_registry / skill_registry /
    # session_manager / subagent_factory / tools=[CustomTool()]
)

async def main():
    async for event in agent.run("帮我写一个 hello world"):
        # event: AgentEvent 的各子类 / 最终的 AgentResponse
        print(type(event).__name__)

asyncio.run(main())
```

自定义工具：继承 `norma.Tool`，实现 `name` / `description` / `schema` / `execute()`，可选覆写 `is_readonly`，通过 `tools=[...]` 传入或 `tool_manager.register_tool(...)`。

## 测试

回归套件（headless，无需真实 API）。一键运行全部 22 个模块：

```bash
PYTHONPATH=src python -m norma.run_tests    # ✓ 全绿！22/22 模块通过
```

或逐个调试单模块：

```bash
python -m norma.prompt.test_system_prompt      # 系统提示 + CLAUDE.md
python -m norma.agent.test_compact_resume      # compact_boundary + 微压缩
python -m norma.agent.test_runner              # AgentRunner 总线桥接契约
python -m norma.messagebus.test_messagebus     # 总线分发 + 事件映射 + 确认流
python -m norma.core.test_openai_llm           # LLM parse/build/stream
python -m norma.mcp.test_mcp_stdio             # MCP stdio 端到端（mock 服务器）
python -m norma.skill.test_skill               # Skill 系统
python -m norma.hook.test_hook                 # Hook 系统
python -m norma.reminder.test_reminder         # Reminder 系统
python -m norma.tool.agent_tool.test_agent_tool  # 子 agent 调度
python -m norma.cli.ui.tui.test_tui_e2e        # TUI 端到端（mock LLM）
python -m norma.cli.ui.tui.test_tui_render     # TUI 渲染（13 项）
```

真实 LLM 冒烟（在配置了真实 api_key 的环境运行）：

```bash
python -m norma.smoke_real_llm
```

## 架构

- `norma/agent/norma_coder.py`：finish_reason 驱动的主循环、compaction、session 持久化。
- `norma/messagebus/`：事件总线（前后端解耦骨干）+ `UserInputManager`（ASK 确认）。
- `norma/cli/ui/tui/`：Textual TUI；`norma/cli/repl/`：prompt_toolkit 兜底；`norma/cli/command/`：统一斜杠命令。
- `norma/mcp/`、`norma/skill/`、`norma/tool/`、`norma/hook/`、`norma/reminder/`、`norma/permission/`、`norma/session/`、`norma/prompt/`：各特性模块。
- 详见 `doc/design/architecture.md`、`doc/refactor_plan.md`、`doc/开发日志.md`。

## 复现参考

- https://www.anthropic.com/engineering/claude-think-tool
- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/
- https://github.com/Yuyz0112/claude-code-reverse/
- https://minusx.ai/blog/decoding-claude-code
