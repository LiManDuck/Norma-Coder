# Norma-Coder 重构执行计划

> 目标：打通前端 TUI + 前后端事件解耦 + 真正流式，完成一个轻量五脏俱全的 codeagent CLI/TUI。
> 来源：`doc/项目重构总体规划.md` + `doc/design/architecture.md`。
> 持续更新；每完成一阶段 commit/push。

## 阶段总览

- [x] P0 现状盘点与参考研究（claude-code 完成；hermes-agent 研究完成：WS 网关+TS TUI 解耦模式）
- [x] P1 稳定化与清理（修关键 bug；部分死代码/拼写延后）
- [x] P2 流式基础（stream_chat 增量 + 流式事件类型）
- [x] P3 事件解耦（AgentRunner 桥接 generator->MessageBus；ASK 确认流闭环）
- [x] P4 TUI 前端（textual app：滚动日志/流式区/状态栏/输入框/确认层/命令）
- [x] P5 接入与打磨（MCP/skill/command 在 TUI 中可用；流式渲染；模式切换；冒烟通过）
- [ ] P6 对齐增强（按需：工具并发分区、parent_uuid 链、分层 compaction、系统提示结构化）

## P0 现状盘点与参考研究
- 通读 src/norma 全量代码，输出架构盘点（见 architecture.md §2）。
- claude-code 研究完成：10 大可移植模式（见 architecture.md §4.1）。
- hermes-agent 研究完成：前端采用「Python WS 网关（tui_gateway）+ TypeScript Ink TUI（ui-tui）」的进程间解耦；Norma 取其「结构化事件发布/订阅」思想，但在单进程内用 MessageBus + Textual 实现，更轻量。

## P1 稳定化与清理
- [x] 修复 `openai_llm.py` 未 import `Field`（providers 配置会崩）。
- [x] 修复 `AssistantMessage.response` 为必填导致**所有** LLM 响应构造崩溃的关键 bug（改为 `= None`）。
- [x] 修复 `system_prompt.py` 引用的 md 文件名错误（`claude_system_prompt.md` -> `claude_code_system_prompt.md`），导致 Agent 构造失败。
- [x] 修复 Windows GBK 控制台无法输出 `✓` 等 Unicode 字符（`main()` 中将 stdout/stderr reconfigure 为 UTF-8）。
- [x] 冒烟测试：`NormaCLI()` 构造通过（agent=normacoder, 12 工具）。
- [x] 清理 `tool_core.py` 死代码：移除 `NormaArtifactContext`/`ExecutionMode`/`PermissionResult`/`ToolExecuteChecker`/`DefaultToolChecker` 注释块/`_load_default_tools`/`_init_from_context`/`_record_call`/`tool_call_history`/`readed_files`，精简 `NormaArtifact`；同步 `tool/__init__.py` 导出。
- [x] 修正 `AgentLLMResponseEvent.resonse` 拼写 -> `response`（agent_types/norma_coder/console/render/tui 5 处同步）。
- [x] 接通 `config.stream_mode` -> `OpenAILLM.default_stream_mode` -> `LLMRequest.stream_mode`（此前配置项与属性均为死代码，agent 恒走流式）。

## P2 流式基础
- [x] 重写 `stream_chat`：增量 yield `LLMResponse`，填充 `stream_content`（delta 文本）/ `stream_reasoning`（推理 delta）；末尾 yield 完整 `response_message`+finish_reason+usage。
- [x] 在 `agent_types.py` 新增 `AgentTextDeltaEvent` / `AgentThinkDeltaEvent`。
- [x] `NormaCoder.run()` 改用流式：逐 delta yield 事件，结束后 yield 既有 LLMResponse/工具/最终响应事件。
- [x] `MessageType` 新增 `AGENT_TEXT_DELTA` / `AGENT_THINK_DELTA`；`AgentMessageAdapter` 映射。
- [x] SDK 路径兼容新事件（`repl.py` 跳过 delta 事件，`render.py` 沿用）。

## P3 事件解耦
- [x] 新增 `AgentRunner`（`src/norma/agent/runner.py`）：后台任务消费 `agent.run()` gen，事件由 `NormaCoder._publish` 推送 MessageBus；捕获最终 AgentResponse；支持 `cancel()`。
- [x] TUI 不直接迭代 gen，改为 `MessageBus.subscribe_all` 订阅渲染。
- [x] ASK 确认闭环：TUI 订阅 `UI_PROMPT` -> `PermissionModal` 弹层 -> `respond_confirmation` publish `USER_CONFIRM/USER_REJECT`，`UserInputManager` future 解锁（已 headless 验证 allow/deny）。
- [x] 保留 SDK 直消费 gen 的能力（`NormaREPL` 与 `async for event in agent.run()` 不变）。

## P4 TUI 前端（textual）
- [x] `cli/ui/tui/app.py`：`NormaApp(App)` 主应用（textual 8.x）。
- [x] 组件：`RichLog`(history 滚动日志) + `Static`(stream 流式区) + `Static`(status 状态栏) + `Input`(底部输入) + `Footer` + `PermissionModal`(ModalScreen)。
- [x] 订阅总线：streaming delta 追加流式区，最终响应/工具调用/结果落盘历史区；thinking 折叠为 dim 块。
- [x] 输入：普通文本 -> `AgentRunner.start`；`/` 命令 -> `CommandRegistry`（统一 `ctx.print` 出口，REPL/TUI 复用）；`F2`/`Shift+Tab` 切换权限模式；`Ctrl+C` 中断/退出；`Ctrl+L` 清屏。
- [x] `cli.py` 入口：默认启动 TUI（`app.run_async()`，与 MessageBus 同事件循环）；保留 `--repl` 走旧 prompt_toolkit 作为兜底。
- [x] 命令输出统一出口：`CommandContext.print` -> `repl.print_output`（REPL: prompt_toolkit HTML；TUI: 剥 HTML 写 RichLog）；`clear_screen` 同理。

## P5 接入与打磨
- [x] MCP 工具加载后注册到 agent（cli.py 既有逻辑），TUI 状态栏可见模型/会话/模式。
- [x] Skill 加载提示（cli.py 既有）；`/` 命令在 TUI 可用（headless 验证 /help /exit）。
- [x] 流式渲染：assistant 文本/推理增量实时追加流式区，收尾落盘（headless 模拟事件验证）。
- [x] 中断：`Ctrl+C` 在运行中调用 `AgentRunner.cancel()`，`TurnFinishedMessage` 收尾。
- [x] 权限确认弹层 allow/deny 闭环（headless 验证）。
- [ ] 端到端真实 LLM 冒烟（需可达 API；本地 plumbing 已用模拟事件走同路径验证）。

## P6 对齐增强（按价值择优）
- [ ] 工具并发分区（is_read_only 并发，写串行）。
- [ ] Session parent_uuid 链 + compact_boundary。
- [ ] 分层 compaction（微压缩清旧 tool_result）。
- [ ] 系统提示结构化（list[str] 块 + env + CLAUDE.md）。
- [ ] MCP 工具名前缀 `mcp__server__tool`。

## 提交节奏
- 每完成一个阶段（或阶段内可独立运行的切片）-> commit + push。
- 开发日志 `doc/开发日志.md` 追加日期与完成项。
