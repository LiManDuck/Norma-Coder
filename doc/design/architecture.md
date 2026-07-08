# Norma-Coder 目标架构设计

> 状态：持续更新。后端已大体就绪，本文档聚焦 **前端 TUI 打通** 与 **前后端事件解耦** 这两个核心缺口，
> 并记录与 hermes-agent / claude-code 的差异。TUI 细节待 hermes-agent 研究结论细化。

## 1. 设计目标（来自 `doc/项目重构总体规划.md`）

- 轻量但五脏俱全的 codeagent CLI/TUI：MCP、Skills、各类 Tool 齐全。
- **前后端解耦，基于事件机制**（MessageBus 作为主干）。
- **打通前端实现**（TUI），支持流式输出。
- 后端逻辑对齐 hermes-agent / claude-code。

## 2. 现状盘点

### 2.1 已就绪（保留）
| 子系统 | 位置 | 状态 |
|---|---|---|
| Agent 主循环 | `agent/norma_coder.py` | finish_reason 驱动的 async generator；缺流式 |
| 工具 | `tool/*` | Read/Ls/Glob/Grep/Edit/Write/Bash/Task*/Agent/Skill |
| 工具管理 | `tool/tool_core.py` `NormaArtifact` | 注册/并发执行；含一段死权限代码待清理 |
| MCP | `mcp/{client,tool,manager}.py` | stdio JSON-RPC，可用 |
| Session | `session/session.py` | jsonl 持久化 + /resume + --resume |
| 权限 | `permission/permission.py` | plan/edit/auto + per-tool |
| Hook | `hook/hook.py` | 订阅总线触发 shell 命令 |
| Reminder | `reminder/*` | `<system-reminder>` 注入 |
| Skill | `skill/skill.py` | ~/.norma/skills + cwd/.norma/skills |
| Command | `cli/command/*` | /new /help /exit /clear /model /compact /status /resume /session |
| MessageBus | `messagebus/messagebus.py` | pub/sub + UserInputManager + AgentMessageAdapter；UIRenderer 定义但未接入 |
| LLM | `core/openai_llm.py` | chat() + stream_chat()；stream_chat 实为缓冲后一次性 yield |
| Compaction | `norma_coder._do_compact` | 单层 LLM 摘要 |

### 2.2 缺口（重构重点）
1. **前端 TUI**：当前是 prompt_toolkit 行式 REPL；`textual` 已声明依赖但未使用。
2. **事件解耦未落地**：REPL 直接 `async for event in agent.run()`；MessageBus 不在渲染链路上。
3. **无真正流式**：`stream_chat` 缓冲全部内容后 yield 一次；`LLMResponse.stream_content` 从未填充。
4. **ASK 确认流断裂**：`UI_PROMPT` 已发布但 REPL 从不回 `USER_CONFIRM/USER_REJECT`，ASK 会挂起至超时后拒绝。
5. **Bug**：`openai_llm.py` 用 `Field` 未 import（已修复）。
6. **死代码**：`NormaArtifactContext.check_permission`、`DefaultToolChecker`、`_load_default_tools`、`PermissionResult`。

## 3. 目标架构

```
┌──────────────────────────────────────────────────────────┐
│                     Textual TUI App                       │
│  ┌──────────────────────────┐ ┌────────────────────────┐ │
│  │ ConversationLog (滚动)    │ │ StatusBar (footer)     │ │
│  │  user / assistant 流式    │ │ mode model session tok │ │
│  │  tool 调用/结果 / thinking │ └────────────────────────┘ │
│  └──────────────────────────┘ ┌────────────────────────┐ │
│                               │ InputBox (底部)         │ │
│                               │ /命令 + 文本            │ │
│  + PermissionModal (ASK 覆盖层)└────────────────────────┘ │
└──────────▲───────────────────────────────▲───────────────┘
           │ subscribe 渲染                 │ publish 输入/确认
┌──────────┴───────────────────────────────┴───────────────┐
│                 MessageBus（事件主干）                     │
│        asyncio.Queue pub/sub + MessageType 枚举           │
└──────────▲───────────────────────────────▲───────────────┘
           │ publish agent 事件             │ subscribe 用户输入
┌──────────┴──────────┐          ┌──────────┴──────────────┐
│    AgentRunner      │          │     UserInputManager     │
│ 消费 agent.run() gen │          │ request_confirmation()  │
│ → publish 到总线    │          │ → UI_PROMPT → 等待       │
└──────────▲──────────┘          └─────────────────────────┘
           │ async generator（流式事件）
┌──────────┴──────────────────────────────────────────────┐
│                   NormaCoder.run()                        │
│  while True:                                              │
│    stream_chat() → yield TextDelta/ThinkDelta/...         │
│    if tool_calls: permission → execute → yield 结果       │
│    else: yield AgentResponse(stop)                        │
│  + compaction / reminder / session log                    │
└───────────────────────────────────────────────────────────┘
```

### 3.1 关键设计决策
- **保留 `agent.run()` 为 async generator**：SDK 兼容（`async for event in agent.run(q)`）。新增细粒度流式事件类型。
- **AgentRunner**：桥接 generator → MessageBus，使 TUI 完全解耦；SDK 用户仍可直接消费 generator。
- **MessageBus 作为 TUI 主干**：TUI 订阅 agent 事件消息渲染；发布用户输入与确认。
- **真正流式**：`stream_chat` 增量 yield delta；agent 循环 yield `AgentTextDeltaEvent` 等。
- **TUI 选型 textual**（已为依赖；plan 明确要 TUI）。布局细节对齐 hermes-agent（待研究结论）。

### 3.2 新增事件类型（对齐 `cli_design.md` 的 TextChunk/Thinking/ToolUse/Complete 愿景）
- `AgentTextDeltaEvent`：assistant 文本增量
- `AgentThinkDeltaEvent`：推理增量
- （可选）`AgentToolCallDeltaEvent`：工具参数流式
- 复用：`AgentInputEvent` / `AgentLLMRequestEvent` / `AgentLLMResponseEvent` / `AgentToolRequestEvent` / `AgentToolRequestAnswerEvent` / `AgentResponse`
- 对应新增 `MessageType.AGENT_TEXT_DELTA` / `AGENT_THINK_DELTA`

## 4. 与参考实现的差异与对齐

### 4.1 对齐 claude-code（已完成研究）
| 模式 | claude-code | Norma-Coder 现状 → 目标 |
|---|---|---|
| 主循环 | `while True` + 单一 State，按 needs_follow_up 退出 | finish_reason 驱动 → 保留，补流式 delta 事件 |
| 工具 schema | Zod → JSON Schema | Pydantic `model_json_schema()`（已具备） |
| 校验链 | schema→validate_input→check_permissions→call | 已具备 permission；可补 validate_input |
| 工具调度 | is_concurrency_safe 分区（只读并发/写串行） | 全部并发 gather → 可改为分区 |
| tool_result 块 | {tool_use_id, content, is_error} | 已具备 |
| 权限 | modes + allow/deny/ask + bypass-immune | plan/edit/auto + per-tool → 可补规则 glob |
| Hook | 子进程，JSON stdin，exit2=block | 子进程 env 注入 → 可补 JSON stdin/exit2 |
| Skill | name/SKILL.md + inline/fork | 已具备 frontmatter+body + 子agent |
| MCP | mcp__server__tool 命名 | 当前直连 tool.name → 可加前缀 |
| Session | jsonl + parent_uuid 链 + compact_boundary | jsonl 无 parent_uuid → 可补链与边界 |
| Compaction | 微压缩(清旧tool_result)+自动(window-13k)+反应式(413) | 单层 LLM 摘要 → 可分层 |
| 系统提示 | list[str] 块 + 动态段注册 + env + CLAUDE.md | 单字符串 → 可结构化 |
| `<system-reminder>` | 注入 memory/date/skills | reminder 系统已具备 |

### 4.2 对齐 hermes-agent（研究进行中）
- 前端 TUI 栈与布局、事件流形态、流式渲染方式 → 待研究结论后填充本节并细化 §3 的 TUI 部分。

## 5. 非目标（本轮不做）
- 不重写已可用的 MCP/Session/Permission/Hook/Skill/Command 核心，仅做必要接入与清理。
- 不追求 claude-code 全部高级特性（模型回退、缓存编辑、LSP 等），保持“轻量化”。
