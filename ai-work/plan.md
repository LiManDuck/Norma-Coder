# Norma Coder 架构文档

## 项目概述
Norma Coder 是一个用 Python 实现的类 Claude Code 终端编程助手。

## 核心目录结构（2026-06-15 更新）

```
src/norma/
├── agent/           # Agent 实现
│   ├── norma_coder.py    # 主 Agent（finish_reason 驱动循环）
│   ├── functioncall_agent.py
│   └── step_agent.py
├── cli/             # 命令行界面
│   ├── cli.py            # 主入口，加载配置、初始化所有系统
│   ├── repl/repl.py      # 交互式 REPL
│   ├── command/          # 命令系统
│   │   ├── registry.py   # 命令注册表
│   │   └── builtin.py    # 内置命令（/new, /help, /model 等）
│   └── ui/render.py      # UI 渲染
├── core/            # 核心类型
│   ├── llm_types.py      # LLM 消息类型、BaseLLM、LLMRequest/Response
│   ├── tool_types.py     # Tool 基类、ToolRequest/Result、FunctionTool
│   ├── openai_llm.py     # OpenAI 兼容 LLM（支持多 Provider）
│   ├── agent_types.py    # Agent 事件类型、BaseAgent
│   └── memory_types.py   # RepoMemory
├── tool/            # 所有工具实现（从 prompt/tool/ 迁移）
│   ├── tool_core.py      # NormaArtifact 工具管理器
│   ├── read_tool/  ls_tool/  glob_tool/  grep_tool/
│   ├── edit_tool/  write_tool/  bash_tool/
│   ├── agent_tool/  skill_tool/  task_tool/  todo_tool/
│   └── __init__.py       # 统一导出
├── mcp/             # MCP 集成
│   ├── client.py         # MCPClient（stdio, JSON-RPC 2.0）
│   ├── tool.py           # MCPTool 包装器
│   └── manager.py        # MCPManager 管理多服务器
├── messagebus/      # 消息总线
├── permission/      # 权限系统（plan/edit/auto 模式）
├── hook/            # Hook 系统
├── reminder/        # Reminder 系统
├── skill/           # Skill 系统
├── tasks/           # Task 系统
├── session/         # Session 持久化与恢复（2026-06-16 新增）
├── memory/          # Agent Memory（消息压缩）
└── prompt/          # 提示模板
    └── system_prompt.py
```

## 主循环架构

NormaCoder.run() 由 finish_reason 驱动：

1. 用户输入 → UserMessage → reminder 注入
2. LLM 请求 → LLM 响应
3. 判断 finish_reason：
   - `stop` / `content_filter` → 结束，返回 AgentResponse
   - `tool_calls` → 权限检查 → 执行工具 → ToolMessage → reminder 注入 → 继续循环
   - `length` → 尝试 compaction → 继续循环

## Session 系统（2026-06-16）

每次启动 CLI 或通过 SDK 实例化 NormaCoder 时分配一个 ``session_id``；
所有用户输入、assistant 回复、tool 结果都会以 jsonl 追加到：

```
~/.norma/projects/<sanitized_cwd>/<session_id>.jsonl
```

可通过 ``NORMA_CONFIG_HOME`` 环境变量覆盖根目录。

CLI 提供：

- ``norma --resume <session_id>`` 启动时直接恢复某会话
- ``/resume`` 列出当前项目最近 20 个会话；``/resume <序号|id>`` 恢复
- ``/session`` 显示当前 session 信息
- ``/new`` 关闭当前 session 并开启新 session

SDK 用法：

```python
from norma import NormaCoder, OpenAILLM, SessionManager

sm = SessionManager(cwd=".")
record = sm.create(title="my task")  # 或 sm.load("session_id")
agent = NormaCoder(
    llm=OpenAILLM(...),
    cwd=".",
    session_manager=sm,
    conversation_id=record.session_id,
)
```

## 执行模式切换（2026-06-16）

REPL 绑定 ``Shift+Tab`` 在 ``plan → edit → auto`` 三种权限模式之间循环；
底部工具栏实时显示当前 mode / model / session_id。

## Compaction 系统

- 阈值：默认 75% 的 max_context_tokens
- 触发：每轮循环开始时检查 estimated tokens
- 执行：将历史消息发送给 LLM 总结，用摘要替换旧消息
- 手动：`/compact` 命令

## 多 Provider 配置

```json
{
  "model": "glm-4.5-air",
  "providers": {
    "huawei": {
      "url": "http://api.openai.rnd.huawei.com/v1",
      "api_key": "sk-xxx",
      "models": ["glm-4.5-air", "glm-4"]
    },
    "openai": {
      "url": "https://api.openai.com/v1",
      "api_key": "sk-yyy",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "default_provider": "huawei"
}
```

使用 `/model huawei/glm-4` 切换模型。

## MCP 集成

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["-m", "my_mcp_server"],
      "env": {}
    }
  }
}
```

MCP 工具自动注册为 `mcp__<server>__<tool>` 格式。
