# Norma-Coder 代码仓整体 Review（2026-07-09）

> 响应开发日志 2026-06-18 任务#3：review 当前代码仓整体实现，找出所有问题。

## 1. 模块依赖与"各自独立"问题

用户反馈"各个模块完全是相互独立的"——指模块间缺乏统一的事件/依赖主干，存在重复概念与绕过主干的现象。

实际依赖关系（梳理后）：
```
cli.cli  ->  NormaCoder + OpenAILLM + MessageBus + PermissionChecker + HookManager
              + UserInputManager + ReminderRegistry + SkillRegistry + MCPManager + SessionManager
NormaCoder -> tool(NormaArtifact) + memory + prompt + messagebus + permission + hook + reminder + skill + session
NormaCoder.run() 是 async generator，既 yield 事件给消费者，又 _publish 到 MessageBus
NormaREPL -> 直接 async for event in agent.run()（绕过总线！）
MessageBus 上挂着 HookManager 订阅；UIRenderer 已定义但无人实例化
```

**核心问题**：MessageBus 设计为前后端解耦主干，但 REPL 直接消费 generator，总线形同虚设（仅 Hook 用到）。这违背了"前后端解耦=事件机制"的目标。

## 2. 问题清单

### 致命级
- **P0-1 LLM 路径必崩**：`AssistantMessage.response: Any` 无默认值，pydantic v2 视为必填，`_parse_response`/`stream_chat` 构造 AssistantMessage 时未传 response -> 任何真实 LLM 调用都 ValidationError。✅ 已修复（默认 None）。
- **P0-2 providers 崩溃**：`openai_llm.py` 用 `Field` 未 import。✅ 已修复。
- **P0-3 ASK 确认流断裂**：`UserInputManager.request_confirmation` 发 `UI_PROMPT` 等 `USER_CONFIRM/REJECT`，但 REPL 从不回应该消息 -> ASK 权限挂起至 60s 超时后拒绝。待修（TUI 中闭环）。

### 架构级
- **A-1 前端未解耦**：REPL 直接迭代 generator；总线不在渲染链路。-> 重构为 TUI 订阅总线。
- **A-2 无真正流式**：`stream_chat` 缓冲后一次性 yield；`stream_content` 从不填充。✅ 已重构为增量 yield。
- **A-3 无 TUI**：textual 已声明依赖但未使用；当前是 prompt_toolkit 行式 REPL。-> 建 textual TUI。
- **A-4 双重权限系统**：`NormaArtifactContext.check_permission`（死代码，恒 ALLOW）与 `PermissionChecker`（实际使用）并存。-> 清理死代码。

### 代码质量
- **Q-1** `AgentLLMResponseEvent.resonse` 拼写错误（4 处一致，功能不受影响）。
- **Q-2** `tool_core.py` 死代码：`_load_default_tools`、`DefaultToolChecker` 注释块、`ExecutionMode`/`PermissionResult`/`NormaArtifactContext`（仅 __init__ 导出，逻辑未用）。
- **Q-3** `AgentConsole`（util/console.py）引用 `message.request.model`，但 `LLMRequest` 无 model 字段（统计显示用，非崩溃）。
- **Q-4** `config.stream_mode` 字段未接线（LLMRequest 默认 stream_mode=True，循环走流式；config 的 False 被忽略）。
- **Q-5** 两个渲染器并存：`AgentEventRenderer`（prompt_toolkit，REPL 用）与 `AgentConsole`（rich，SDK/示例用），逻辑重复。

## 3. 重构方向（对齐 claude-code 研究结论）

详见 `doc/design/architecture.md` 与 `doc/refactor_plan.md`。要点：
1. MessageBus 作为前后端主干：agent 已 publish；TUI 订阅渲染；ASK 经总线闭环。
2. 真正流式：delta 事件已就绪。
3. textual TUI：滚动日志 + 状态栏 + 输入框 + 确认层 + 命令。
4. 保留 SDK 直消费 generator 的能力。

## 4. 与 claude-code 的差距（可择优对齐）
- 工具并发分区（is_concurrency_safe：只读并发/写串行）——当前全 gather。
- Session parent_uuid 链 + compact_boundary——当前 jsonl 无链。
- 分层 compaction（微压缩清旧 tool_result）——当前单层 LLM 摘要。
- 系统提示结构化（list[str] 块 + env + CLAUDE.md）——当前单字符串。
- MCP 工具名前缀 `mcp__server__tool`——当前直连 tool.name。
