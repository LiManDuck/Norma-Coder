# Norma-Coder 重构执行计划

> 目标：打通前端 TUI + 前后端事件解耦 + 真正流式，完成一个轻量五脏俱全的 codeagent CLI/TUI。
> 来源：`doc/项目重构总体规划.md` + `doc/design/architecture.md`。
> 持续更新；每完成一阶段 commit/push。

## 阶段总览

- [x] P0 现状盘点与参考研究（claude-code 完成；hermes-agent 进行中）
- [ ] P1 稳定化与清理（修 bug、清死代码）
- [ ] P2 流式基础（stream_chat 增量 + 流式事件类型）
- [ ] P3 事件解耦（AgentRunner 桥接 generator→MessageBus；ASK 确认流闭环）
- [ ] P4 TUI 前端（textual app：滚动日志/状态栏/输入框/确认层/命令）
- [ ] P5 接入与打磨（MCP/skill/command 在 TUI 中可用；流式渲染；模式切换）
- [ ] P6 对齐增强（按需：工具并发分区、parent_uuid 链、分层 compaction、系统提示结构化）

## P0 现状盘点与参考研究
- 通读 src/norma 全量代码，输出架构盘点（见 architecture.md §2）。
- claude-code 研究完成：10 大可移植模式（见 architecture.md §4.1）。
- hermes-agent 研究进行中：聚焦前端 TUI 栈/布局/事件流。

## P1 稳定化与清理
- [x] 修复 `openai_llm.py` 未 import `Field`（providers 配置会崩）。
- [ ] 清理 `tool_core.py` 死代码：`NormaArtifactContext.check_permission`、`DefaultToolChecker` 注释块、`_load_default_tools`、`PermissionResult`/`ExecutionMode`（若未被引用）。
- [ ] 修正 `AgentLLMResponseEvent.resonse` 拼写 → `response`（同步 render.py / console.py）。
- [ ] 冒烟测试：`python -c "import norma"` 通过。

## P2 流式基础
- [ ] 重写 `stream_chat`：增量 yield `LLMResponse`，填充 `stream_content`（delta 文本）/ reasoning delta；末尾 yield 完整 `response_message`+finish_reason。
- [ ] 在 `agent_types.py` 新增 `AgentTextDeltaEvent` / `AgentThinkDeltaEvent`。
- [ ] `NormaCoder.run()` 改用流式：逐 delta yield 事件，结束后 yield 既有 LLMResponse/工具/最终响应事件。
- [ ] `MessageType` 新增 `AGENT_TEXT_DELTA` / `AGENT_THINK_DELTA`；`AgentMessageAdapter` 映射。
- [ ] SDK 路径（`AgentConsole`）兼容新事件。

## P3 事件解耦
- [ ] 新增 `AgentRunner`：`async def run(query)` 消费 `agent.run()` gen，逐事件 publish 到 MessageBus；收集最终 AgentResponse。
- [ ] TUI/REPL 不再直接迭代 gen，改为订阅总线渲染。
- [ ] ASK 确认闭环：TUI 订阅 `UI_PROMPT` → 弹确认层 → publish `USER_CONFIRM/USER_REJECT`，`UserInputManager` future 解锁。
- [ ] 保留 SDK 直消费 gen 的能力（不强制走总线）。

## P4 TUI 前端（textual）
- [ ] `cli/ui/tui/app.py`：`NormaApp(App)` 主应用。
- [ ] 组件：`ConversationLog`(RichLog 滚动)、`StatusBar`(footer)、`InputBox`(底部 Input)、`PermissionModal`(Overlay)。
- [ ] 订阅总线：streaming delta 追加日志；工具调用/结果区块；thinking 折叠。
- [ ] 输入：普通文本 → AgentRunner.run；`/` 命令 → CommandRegistry；Shift+Tab 切换权限模式。
- [ ] `cli.py` 入口：默认启动 TUI（保留 `--repl` 走旧 prompt_toolkit 作为兜底）。

## P5 接入与打磨
- [ ] MCP 工具加载后在 TUI 状态栏可见。
- [ ] Skill 加载提示、/命令在 TUI 可用。
- [ ] 流式渲染：assistant 文本逐字、工具执行实时状态。
- [ ] 中断（Ctrl+C / Esc）取消当前 agent 任务。
- [ ] 端到端冒烟：跑一个读文件+改文件+bash 的真实任务。

## P6 对齐增强（按价值择优）
- [ ] 工具并发分区（is_read_only 并发，写串行）。
- [ ] Session parent_uuid 链 + compact_boundary。
- [ ] 分层 compaction（微压缩清旧 tool_result）。
- [ ] 系统提示结构化（list[str] 块 + env + CLAUDE.md）。
- [ ] MCP 工具名前缀 `mcp__server__tool`。

## 提交节奏
- 每完成一个阶段（或阶段内可独立运行的切片）→ commit + push。
- 开发日志 `doc/开发日志.md` 追加日期与完成项。
