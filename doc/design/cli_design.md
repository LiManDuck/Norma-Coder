# CLI / TUI 前端设计

> 状态：已实现。本文档描述**实际落地**的前端架构（Textual TUI 默认 + prompt_toolkit REPL 兜底），
> 与 `architecture.md` §3 的高层架构互补，聚焦前端层本身。
> 历史早期愿景（Rich.Layout + Prompt Toolkit 单前端）已被取代。

## 1. 两个前端，同一后端

`cli.py:NormaCLI.run(use_repl)` 按开关选择前端，二者共享同一 `NormaCoder` agent +
`MessageBus` + `UserInputManager` + `CommandRegistry`：

| 前端 | 启动 | 实现 | 渲染栈 | 定位 |
|---|---|---|---|---|
| **TUI**（默认） | `norma` | `cli/ui/tui/app.py:NormaApp` | textual 8.x + rich | 主前端，流式 / Markdown / 弹窗 |
| **REPL**（兜底） | `norma --repl` | `cli/repl/repl.py:NormaREPL` | prompt_toolkit + rich.Console | 极简兜底，无 textual 依赖时可用 |

二者都把自身伪装成「REPL 兼容」对象（`print_output` / `clear_screen` 同名同义），
使 `CommandContext` 通过 `ctx.repl.*` 调用的命令处理器在前端间零改动复用。

## 2. TUI 结构（Textual）

`NormaApp.compose()` 自上而下：

```
┌─ Header(name="Norma Coder") ─────────────────────────┐
│ RichLog(id="history")   ← 落盘历史（user / assistant / tool / 错误 / 回合分隔）│
│ Static(id="stream")     ← 流式增量区（思考 + 文本，实时刷新，落盘后隐藏）     │
│ Static(id="status")     ← 状态栏：模式/模型/会话/运行态/快捷键提示             │
│ _MultiLineInput(id="input") ← 输入框（/命令 + 文本，多行粘贴保留）            │
└─ Footer() ───────────────────────────────────────────┘
        + PermissionModal（ASK 覆盖层，push_screen 弹出）
```

- `RichLog`（markup=True, auto_scroll=True）：追加式落盘，`_write_history(renderable)` 统一入口。
- `Static#stream`：流式阶段 `display=True` 实时刷新；`_commit_stream()` 落盘后 `display=False`。
- `_MultiLineInput(Input)`：textual `Input._on_paste` 默认 `splitlines()[0]` 会截断多行粘贴，
  子类重写 `_on_paste` 插入完整 `event.text`；widget 类型仍为 `Input`，`query_one("#input", Input)` 兼容。

## 3. 消息路由：总线 → UI 线程 → 渲染

TUI 不在总线处理器任务里直接操作控件（会跨线程踩 textual），而是两跳转发：

```
MessageBus（asyncio.Queue pub/sub）
   │ subscribe_all(NormaApp._on_bus_message)
   ↓
_on_bus_message(message)            # 总线任务线程
   │ self.post_message(BusEventMessage(message))   # 跨线程投递回 UI 线程
   ↓
on_bus_event_message(BusEventMessage)  # textual UI 线程，安全操作控件
   │ 按 bus.msg_type 分发渲染
```

`BusEventMessage` 是 textual `Message` 子类，仅携带原总线消息，保证渲染发生在 UI 线程。

## 4. 事件 → 渲染映射

`on_bus_event_message` 按 `MessageType` 分支（`app.py:417`）：

| MessageType | payload 类型 | 渲染动作 |
|---|---|---|
| `AGENT_TEXT_DELTA` | `AgentTextDeltaEvent` | 累积 `_stream_text`，`_update_stream()` 刷新流式区 |
| `AGENT_THINK_DELTA` | `AgentThinkDeltaEvent` | 累积 `_stream_think`（dim italic yellow），刷新流式区 |
| `AGENT_THINK` | `AgentThinkEvent` | 非流式思考，直接落盘 `💭 思考` 块 |
| `AGENT_TOOL_REQUEST` | `AgentToolRequestEvent` | 先 `_commit_stream()` 落盘已吐文本；逐工具落盘 `🛠 name(args)` |
| `AGENT_TOOL_RESULT` | `AgentToolRequestAnswerEvent` | 逐结果落盘 `⚙ name: content`（错误 `✗` 红字） |
| `AGENT_LLM_RESPONSE` | `AgentLLMResponseEvent` | 收尾；若全程无 delta（非流式），补打 `_render_assistant(content)`；`_commit_stream()` |
| `AGENT_RESPONSE` | `AgentResponse` | 落盘残留流式；`payload.error` 非空则红字 `✗ 任务异常`；画回合分隔 `─×60` |
| `UI_PROMPT` | dict | `push_screen(PermissionModal)` 弹权限确认层 |

## 5. 流式提交流（streaming commit）

流式增量只更新 `#stream` 区（不落盘、不抖动）；在以下任一边界把缓冲落盘到 `#history` 并清空流式区：

1. `AGENT_TOOL_REQUEST`（工具调用前先把已吐文本固化）
2. `AGENT_LLM_RESPONSE`（本轮 LLM 结束）
3. `AGENT_RESPONSE`（回合结束 / 异常收尾）

`_commit_stream()`：思考缓冲 → `💭 思考` 块；文本缓冲 → `_render_assistant(text)`（Markdown）；
随后清空 `_stream_text/_stream_think`、隐藏 `#stream`。

## 6. Markdown 渲染与错误可见性

- **Markdown**：落盘历史区的助手回复经 `_render_assistant(text) = Group(Text("🤖 "), Markdown(text))`
  渲染（代码块高亮 / 加粗 / 列表）。流式增量阶段保持纯文本（增量 Markdown 重排会抖动），提交时统一渲染。
- **错误可见性**：`AgentResponse.error` 非空（LLM 不可达 / bad key / 解析失败 / 逃逸异常）时，
  TUI 以 `✗ 任务异常: <error>` 红字显式提示，即便流式已吐部分文本也不丢弃。此为「打通前端」关键缺口修复。

## 7. 权限确认弹窗（ASK 闭环）

```
agent 请求确认 → UserInputManager.request_confirmation()
   → MessageBus 发布 UI_PROMPT（含 request_id + prompt）
   → TUI on_bus_event_message 收到 UI_PROMPT
   → push_screen(PermissionModal(request_id, prompt))
   → 用户 y/N/ESC → _on_result → _respond_confirmation(request_id, allowed)
   → UserInputManager.respond_confirmation() → 发布 USER_CONFIRM/USER_REJECT → 解锁 future
```

`PermissionModal` 是 textual `ModalScreen` 子类；`respond_confirmation` 经 textual `@work` 包装以在 UI 线程安全调用 async 方法。

## 8. 状态栏与快捷键

`_refresh_status()` 渲染 `#status`：`模式=plan/edit/auto  模型=glm-...  会话=xxxxxxxx  ●运行中/○就绪  (F2 切换 / Ctrl+C 中断 / Ctrl+L 清屏)`。

- **F2**：循环切换权限模式 plan → edit → auto（`PermissionChecker.config.mode`）。
- **Ctrl+C**：中断当前 `AgentRunner`（cancel agent.run()），恢复输入框。
- **Ctrl+L**：清屏（清空 `#history`）。
- 运行态：`AgentRunner.running` 标志，运行中输入框禁用以防并发提交。

## 9. 命令系统（前端共享）

`CommandRegistry` + `register_builtin_commands` 注册 9 个内置命令：
`/new /help /exit /clear /model /compact /status /resume /session`。

`CommandContext(repl, args)` 把当前前端对象作为 `repl` 传入，命令处理器经 `ctx.repl.print_output()` /
`ctx.repl.clear_screen()` / `ctx.agent.*` 操作，使 TUI 与 REPL 复用同一套命令实现。

- `/model`：`llm.switch_model(name)` 切换模型。
- `/compact`：`agent._do_compact()`，按返回 bool 如实报告成功 / 失败（LLM 不可达时不误报）。
- `/resume`：按 session_id 或序号列出 / 恢复历史会话。
- 未知命令：`registry.lookup()` 先查表，未命中才提示，避免与「已知命令返回 None」混淆。

## 10. REPL 兜底（prompt_toolkit）

`NormaREPL` 用 `PromptSession`（`WordCompleter` 补全 `/` 命令、`enable_history_search`、
`bottom_toolbar` 显示模式/模型/会话、`shift+tab` 循环权限模式）。

- 直接 `async for event in agent.run(query)` 消费生成器（不经总线拿 agent 事件），
  经 `AgentEventRenderer.render_event()` 转 `prompt_toolkit.HTML` 后 `print_formatted_text`。
- **权限订阅**：REPL 虽不订阅 agent 事件，但必须订阅 `UI_PROMPT`--否则 ASK 请求的 future
  会等 60s 超时后默认拒绝。`_setup_permission_subscription` 订阅 `UI_PROMPT`，
  回调经可注入的 `prompt_confirm`（默认 prompt_toolkit y/N 交互）拿到应答后回送 `respond_confirmation`。
- 流式增量（`AgentTextDeltaEvent`/`AgentThinkDeltaEvent`）在 REPL 中跳过，最终文本由
  `AgentLLMResponseEvent` 一次性显示（真正的逐字流式由 TUI 负责）。
- 回复以 `html.escape` 纯文本渲染（非 Markdown）--兜底路径，保持轻量；主前端 TUI 已具备 Markdown。

## 11. 关键设计决策

- **TUI 选 textual**：plan 明确要 TUI；textual 提供控件 / 布局 / 消息循环 / pilot 测试，rich 仅做渲染原语。
- **总线作为前后端唯一耦合点**：agent 只管 `publish` 事件，前端只管 `subscribe` 渲染；前端可替换（TUI/REPL/未来 Web）而不动后端。
- **`post_message` 跨线程桥**：总线回调在 asyncio 任务里，textual 控件必须在 UI 线程操作；`BusEventMessage` 桥接二者，避免直接跨线程踩控件。
- **流式纯文本 + 落盘 Markdown**：流式阶段避免 Markdown 重排抖动，提交时一次性渲染，兼顾实时性与可读性。
- **REPL 保留为兜底**：无 textual / 极简环境可用；与 TUI 共享 agent / 总线 / 命令，零重复逻辑。
