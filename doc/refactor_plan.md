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
- [~] P6 对齐增强（按需：~~工具并发分区~~、~~compact_boundary~~、~~系统提示结构化~~、~~分层 compaction~~ 已完成；parent_uuid 链可选未做）

> **当前状态（功能完整）**：P0–P5 全部完成，P6 高价值项（并发分区 / compact_boundary / 系统提示结构化 / 分层 compaction）均已完成并有 headless 回归测试。MCP/Skill/TUI/compact 四大特性经端到端回归验证。P7 进一步硬化前端、移除死代码、修复 REPL 权限挂起与 3 个核心工具致命缺陷（BashTool Windows 崩溃 + 120s 超时、EditTool 先读后编门禁失效）、BashTool 名称大小写对齐 DANGEROUS_TOOLS、`/compact` 误报成功，并实现 PreToolUse 阻断式 hook（exit 2 + JSON stdin + stderr 回喂），以及**前端异常可见性**（`AgentResponse.error` 字段 + TUI/REPL 红字 ✗ 提示 + AgentRunner 不再静默吞掉逃逸异常），并**修复 CLI 参数失效**（`--model`/`--config` 此前被解析但未传入 NormaCLI，且默认配置路径不随 `NORMA_CONFIG_HOME` 走），**TUI 回复 Markdown 渲染**（落盘历史区渲染代码块/加粗/列表，此前裸 ```` ``` ````），**TUI 多行粘贴保留**（textual `Input._on_paste` 此前 `splitlines()[0]` 截断，代码块粘贴只剩首行），**修复非流式 `_parse_response` 丢弃思考模型 reasoning_content**（默认 `stream_mode=False` 下推理被静默丢弃），**TUI 中断与异常分流提示**（Ctrl+C 主动中断显示中性「⚠ 已中断」，不再误示为红字「✗ 任务结束（异常）」），**补齐 `python -m norma` 顶层入口**（新增 `src/norma/__main__.py` 委托 `cli.main`，此前 `python -m norma` 报 "cannot be directly executed"；argparse `prog="norma"` 使两种入口 usage 行均显示 `norma`），**修复「先读后编/先读后写」门禁路径规范化不一致**（`WriteTool` 用 `os.path.abspath` 标记、`ReadTool`/`EditTool` 用 `Path.resolve` 检查，符号链接 cwd 下会误判未读而拒绝编辑刚写入的文件；统一为 `resolve`），**补齐 TUI 权限弹窗往返回归**（`test_tui_e2e` 用 Ls 在 AUTO 模式自动放行永不弹窗、`test_repl_permission` 仅覆盖 REPL 路径；新增 `test_tui_permission` 驱动真实 TUI 弹窗往返：EDIT 模式 Bash→ASK→总线 UI_PROMPT→BusEventMessage 跨线程投递→`push_screen(PermissionModal)`→pilot 按 y/n→`_respond_confirmation` worker→future 解锁→工具放行/拒绝→回合继续，allow 执行工具 / deny 不执行，均 2 轮 LLM 回合结束）。**补齐 skill `allowed_tools` 沙箱**（frontmatter 的 `allowed_tools: [Read, Grep]` 此前被解析存储但从未用于收窄子 agent 工具集--子 agent 仍是带 Bash/Write 的完整 NormaCoder，安全相关的不一致；`NormaCoder` 新增 `tool_whitelist` 构造参数按名收窄默认工具、`_default_subagent_factory` 透传、`SkillTool._make_agent` 把 `skill.allowed_tools` 透传给工厂并逐级降级兼容自定义工厂），**补齐 session restore 的 tool_calls/reason_content/tool 消息往返回归**（`test_restore_trims_at_boundary` 此前用 `tool_calls: None`，未覆盖 tool_calls 重建/reason_content 透传/tool 消息重建/tool_call_id 链接一致性/is_error 保留；新增 `restore_tool_calls_roundtrip` + `restore_tool_error_flag`，`test_compact_resume` 3->5 项），**修复 MCP 服务器崩溃时挂起请求等满 60s 超时**（`_read_loop` 在 EOF 时未 resolve `_pending_requests` 的 future，调用方需等满 `_send_request` 60s 超时才感知连接断开；新增 `finally` + `_fail_pending` 在连接断开时立即把挂起 future 置为 ConnectionError，使在途请求快速失败；`test_mcp_stdio` 新增 `_run_crash` 验证 elapsed<30s），**修复 `/model provider/model` 切换后 `_default_provider` 不同步**（`switch_model` 更新了 model/base_url/api_key/client 但漏更 `_default_provider`，导致 `/model` 显示仍把旧 provider 标为当前且无法标记刚切换的模型；与 `switch_provider` 对齐补 `self._default_provider = provider_name`，`test_openai_llm` 新增 `switch_model_provider_updates_default_provider`），**修复 `/new` 不清空「已读文件」注册表**（`/new` 重置 memory+session 但未清 `read_files_registry`，旧对话读过、新对话未读的文件仍被「先读后编」门禁放行编辑；`NormaCoder` 暴露 `_read_files_registry`、`cmd_new` 清空，`test_commands` 新增 `new_clears_read_registry`），**修复 AgentTool 后台运行中前台调用的并发竞态**（`_run_foreground` 未检查同 session 是否有后台任务在跑，与后台 `_job` 对同一子 agent 并发 `_consume_agent` -> 两个 `agent.run()` 交错篡改 memory/事件流；`_run_background` 有守卫而 `_run_foreground` 没有；`_run_foreground` 开头加守卫，background_task 未 done 时返回 `status=running` 引导用「空 prompt+session_id」查询，被拦截不再调 `_consume_agent`；`test_agent_tool` 新增 `test_foreground_during_background`，用 `_SlowAgent`（gate 挂起）确认后台真 running、前台返回 running、`slow.calls` 不增长证明无并发、放行后轮询到 done，4->5 项），**修复 TUI 权限弹窗上按 Ctrl+C 中断后残留孤儿弹窗**（`action_interrupt_or_quit` 取消 runner 后，`request_confirmation` 的 future 在 finally 被清理、`TurnFinishedMessage` 重新启用输入框，但 `push_screen(PermissionModal)` 从未被 dismiss，遮蔽输入框；用户看到权限提示后按 Ctrl+C 是常见操作；`on_turn_finished_message` 开头新增 `_dismiss_open_permission_modal()`，screen 仍是 PermissionModal 时 `dismiss(None)`，正常流程 no-op、`respond_confirmation` 对已 pop 的 request_id no-op 无副作用；`test_tui_permission` 新增 `test_modal_interrupt_dismisses`，2->3 项，已验证无修复时抛 AssertionError）。**修复 execute_tool 异常兜底产出非法 JSON 的 content**（`tool_core.execute_tool` 工具未找到 / 异常兜底两处用 f-string `f'{{"error": "{str(e)}"}}'` 拼 content，异常消息含反斜杠（Windows 路径 `C:\Users\...`）、双引号、换行时产出非法 JSON，违反「content 始终是合法 JSON」契约（MCPTool/task_tools/AgentTool 均用 json.dumps，唯独此兜底路径用 f-string）；改用 `json.dumps`，`test_tools` 新增 `execute_tool_error_content_is_valid_json`，用含三类破坏字符的异常消息验证 content 可 json.loads 且错误消息完整往返，5->6 项，已验证无修复时返回 False），**接通 MCP 工具注解到权限检查器**（`MCPTool.is_readonly`/`is_destructive` 从 annotations 解析但 `PermissionChecker.check` 只按名查静态集合、从不查阅--mcp__ 工具在 EDIT 全走「未知->ASK」兜底，只读 MCP 工具每次被询问；`check` 增 `is_readonly`/`is_destructive` 形参，**静态分类优先于自报注解**防洗白，`_apply_permission` 经 `tool_manager.get_tool`+`getattr` 透传注解；`test_permission` 新增 `tool_annotation_hints_classify_mcp_tools` 含安全契约锁定，5->6；`test_compact_resume` 新增 `test_apply_permission_uses_readonly_hint` 验证接线，5->6）。仅剩 parent_uuid 链（低价值可选）与真实 LLM 端到端冒烟（需可达 API，本环境不可达）。回归套件 18/18：`test_system_prompt` / `test_compact_resume` / `test_mcp_stdio` / `test_skill` / `test_tui_e2e` / `test_tui_render`（13 项） / `test_tui_permission`（TUI 权限弹窗 allow/deny 往返 2 项） / `test_repl_permission` / `test_hook` / `test_reminder` / `test_agent_tool` / `test_tools` / `test_permission` / `test_commands` / `test_cli`（6 项，含 `python -m norma` 入口回归） / `test_openai_llm` / `test_runner`（AgentRunner 总线桥接契约 6 项） / `test_messagebus`（总线分发 + AgentMessageAdapter 事件->MessageType 映射 + UserInputManager 确认流 7 项），均 headless 可独立运行，并可通过 `python -m norma.run_tests` 一键发现与运行。`AgentRunner` 已纳入 SDK 导出（`from norma import AgentRunner`）。死代码 `norma/util/console.py` / `norma/core/memory_types.py` / `messagebus.UIRenderer`（被 TUI 总线订阅 + `AgentEventRenderer` 取代，零实例化、`render_confirmation_prompt`/`render_event` 依赖已失效）已清理（可从 git 历史恢复）。
> **已知遗留**：3 个旧 repo-ASE agent 模块（`functioncall_agent`/`repo_ase_agent`/`step_agent.py`）已在 P7 移除（可从 git 历史恢复）。`norma/util/console.py`（415 行 `AgentConsole` 异步渲染器，被 `norma.cli.ui.render.AgentEventRenderer` 取代，零导入、未导出、未文档化）与 `norma/core/memory_types.py`（334 行旧 `RepoMemory`/`RepoMemoryManager` 设计，被活跃的 `norma.memory.agent_memory.AgentMemory` 取代，零导入、未导出、未文档化）已移除（可从 git 历史恢复）。`TodoWriteTool` 为导出但未注册的死工具（Task 系统为活跃实现），保留为 SDK 可选工具未清理。无其他存活遗留。

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
- [x] 专项回归测试覆盖（headless、可独立运行，13/13 全绿）：TUI e2e（`test_tui_e2e.py`，mock OpenAI client，流式+非流式两轮）、TUI 前端渲染与交互（`test_tui_render.py`，思考块/多工具调用/工具成功错误标记/流式中断/命令路径/F2 模式/权限弹窗往返）、REPL 权限确认（`test_repl_permission.py`，UI_PROMPT 订阅 + 可注入 prompt_confirm，allow/deny）、MCP stdio（`test_mcp_stdio.py`，mock MCP 服务器子进程）、Skill（`test_skill.py`，mock 子 agent）、compact/resume（`test_compact_resume.py`，边界+微压缩）、系统提示（`test_system_prompt.py`，CLAUDE.md 注入与排序）、Reminder（`test_reminder.py`）、Hook（`test_hook.py`，环境变量/match/总线订阅）、AgentTool（`test_agent_tool.py`，前台/后台/session 复用）、核心工具（`test_tools.py`，write/read/edit 落盘 + ls/glob/grep + bash + task 生命周期，验证共享 registry 先读后编门禁）、权限分类（`test_permission.py`，AUTO/PLAN/EDIT 矩阵 + per-tool 覆盖 + 工具名大小写命中常量）、内置命令（`test_commands.py`，9 命令不崩溃 + /compact 诚实上报成功/失败）。
- [x] 端到端真实 LLM 冒烟脚本 `python -m norma.smoke_real_llm`：读取 `~/.norma/config.json`，用真实 api_key/base_url 走非流式 `chat()` + 流式 `stream_chat()` 各一次，验证连通与解析；未配置真实 key 或不可达时 SKIP（不阻塞），可由用户在自有环境一键验证。

## P6 对齐增强（按价值择优）
- [x] 工具并发分区（只读并发，写串行）：`Tool.is_readonly` 元数据 + `NormaArtifact.execute_tools` 分区，结果按原序返回；Read/Ls/Glob/Grep/TaskGet/TaskList 标记只读。
- [x] MCP 工具名前缀 `mcp__server__tool`（`MCPTool` 已实现，含 `is_readonly`/`is_destructive`）。
- [x] compact_boundary：`_do_compact` 写入 `compact_boundary` 边界条目到 session jsonl；`restore_from_session` 遇到边界时丢弃边界前重放、仅保留 system+摘要+边界后轮次（回归测试 `test_compact_resume.py` 通过）。
- [x] 分层 compaction（微压缩清旧 tool_result）：新增 `_micro_compact`（无 LLM 调用），截断较早的 tool_result 内容、保留最近 N 条原文，不删消息、保留 tool_call_id 链接；`_should_compact` 触发与 `finish_reason=length` 路径均改为「先微压缩，仍超阈值再完整摘要」。回归测试 `test_compact_resume.py::test_micro_compact` 通过。
- [x] 系统提示结构化：`SystemPromptService` 拼装「核心指令 md + 环境段（cwd/平台）+ 项目记忆 CLAUDE.md」；CLAUDE.md 收集用户级 `~/.norma/CLAUDE.md` + 项目级（自 cwd 向上遍历祖先至根），项目级排在用户级之后（更优先），超长截断。回归测试 `test_system_prompt.py` 通过。

## P7 清理与前端硬化
- [x] 移除 3 个遗留死 agent 模块（`functioncall_agent.py`/`repo_ase_agent.py`/`step_agent.py`）：闭环死集群，引用已删除模块且含语法错误，无存活代码引用（可从 git 历史恢复）；`walk_packages` 导入失败 3 -> 0。
- [x] 前端硬化回归 `test_tui_render.py`（7 项）：补 `test_tui_e2e.py` 未覆盖的前端渲染正确性与交互--思考块、多工具调用、工具成功(⚙)/错误(✗)标记、流式中断(Ctrl+C)、命令路径(/help 渲染 + 未知命令提示 + /clear 不误报)、F2 权限模式循环、权限弹窗往返(UI_PROMPT->弹窗->y->True)。向真实 MessageBus 发布合成事件 + Textual pilot 抽干消息泵，复现总线->post_message->渲染全链路。
- [x] 修复 REPL 权限确认挂起：REPL 订阅 `UI_PROMPT`，回调经可注入 `prompt_confirm`（默认 prompt_toolkit y/N）回送 `respond_confirmation`，不再 60s 超时默认拒绝。回归 `test_repl_permission.py`（allow/deny）。
- [x] 修复 3 个导致核心工具完全不可用的致命缺陷：① BashTool 在 Windows 上 `os.setsid` AttributeError 崩溃 -> 改用 `_BASH_PREEXEC_FN`（POSIX setsid / Windows None）+ `shutil.which('bash')` 跨平台解析；② BashTool 每条命令必 120s 超时 -> 退出码标记 `echo {m}$__EXIT_CODE{m}` 被 bash 贪婪解析为未设置变量，改为 `echo "{m}$?{m}"`；③ EditTool「先读后编」门禁恒失败 -> `norma_coder` 注入共享 `read_files_registry` 传给 Read/Edit/Write，ReadTool 成功后记录、WriteTool 写入后标记。附带修复 EditTool 解析失败 `is_error=False` 笔误。回归 `test_tools.py`（4 项）。
- [x] 修复 BashTool 名称大小写：`name` 由 `"bash"` 改为 `"Bash"`，对齐 `DANGEROUS_TOOLS` 与全工具大小写约定，使 EDIT 模式危险工具分类真正命中（此前仅靠「未知工具 -> ASK」兜底）。新增 `test_permission.py`（5 项）锁定 mode×tool 分类矩阵。
- [x] 修复 `/compact` 误报成功：`_do_compact` 改返回 `bool`（True 已压缩 / False 失败 memory 不变），`cmd_compact` 依据返回值如实报告；主循环两处死代码 `if compact_event: yield compact_event`（恒 None 且无事件类型）改为副作用驱动。新增 `test_commands.py`（3 项）覆盖 9 个内置命令不崩溃 + /compact 诚实上报。
- [ ] Session parent_uuid 链（分支/fork，可选，价值较低）。
- [x] JSON stdin + exit2 阻断式 Hook：`HookManager.run_pre_tool_hooks` 实现 PreToolUse 阻断语义--hook 经 stdin 收到 JSON 上下文，exit 2 阻断工具并把 stderr 回喂 LLM，`_apply_hooks` 串联在 `_apply_permission` 之后。与静态权限分工（静态模式门禁 vs 动态可脚本化门禁）。回归 `test_hook.py` 7 项（含端到端主循环：LLM 请求 Write .env -> hook 阻断 -> 文件未创建 -> stderr 回喂）。
- [x] 前端异常可见性（用户首要优先级「打通前端」的关键缺口）：此前 LLM 不可达/解析失败等异常经 `NormaCoder.run` 兜底为 `AgentResponse(response="发生了错误: ...")`，但 TUI 的 `AGENT_RESPONSE` 处理只画分隔符、不渲染 `response`，导致**最常见的真实失败对用户完全静默**（回合凭空结束、无任何提示）。修复：① `AgentResponse` 新增 `error: Optional[str]` 字段，`NormaCoder` 异常分支置 `error=str(e)`；② TUI `AGENT_RESPONSE` 处理在 `payload.error` 非空时以红字 ✗ 显式提示（且在流式部分输出后仍提示，不丢弃）；③ `render.py` REPL 路径错误用 ✗ 红字替代误导性的 ✅；④ `AgentRunner._run` 不再 `except: return None` 静默吞掉逃逸异常，改为上抛，经 `_on_agent_done` -> `TurnFinishedMessage(ok=False)` 写出错误。回归 `test_tui_render.py` 新增 3 项（异常渲染 / 流式后异常 / 逃逸异常上浮），并经真实 `NormaCLI` + 抛错 LLM 桩验证 `AgentResponse.error` 经总线送达。
- [x] 修复 CLI 参数失效：`main()` 用 argparse 解析了 `--model`（且写在帮助示例 `norma --model glm-4` 里）与 `--config`，却**只把 `args.resume` 传进 `NormaCLI`**，二者被静默丢弃，用户 `norma --model X` / `norma --config f.json` 完全不生效。修复：`NormaCLI.__init__` 增 `model_override`/`config_path` 形参，`main()` 透传；`load_config(config_path=...)` 支持 `--config` 显式文件（缺失则回退默认、不在任意路径落盘），默认路径改用 `session.get_config_home()`（随 `NORMA_CONFIG_HOME` 走，与 session 存储一致、测试可隔离），`model_override` 覆盖 `config["model"]` 且优先级高于配置文件。新增 `test_cli.py`（5 项）锁定 model_override 覆盖 / config_path 加载 / 缺失不崩 / NORMA_CONFIG_HOME 隔离 / override 优先于文件。
- [x] TUI 回复 Markdown 渲染（代码代理可读性关键项）：此前助手回复以纯 `Text` 落盘历史区，含代码块的回复显示为裸 ```` ``` ```` 文本。改为落盘时经 `rich.markdown.Markdown` 渲染（代码块语法高亮 / 加粗 / 列表 / 表格）；流式增量阶段仍以纯文本追加流式区（增量 Markdown 重排会抖动），`_commit_stream` 与非流式 `AGENT_LLM_RESPONSE` 路径统一用 `_render_assistant`（`Group(🤖 前缀, Markdown(text))`）。错误路径仍用红字 `Text`（不渲染 Markdown）。回归 `test_tui_render.py` 新增 `assistant_markdown_render`（共 11 项），并改 `_install_recorder` 经 `Console.export_text` 捕获 Group/Markdown 渲染文本。
- [x] TUI 多行粘贴保留（代码代理核心 UX 缺口）：textual `Input._on_paste` 执行 `event.text.splitlines()[0]`，多行粘贴（代码块/报错栈）**只剩首行**送达 agent。经实测 `Input.value` 本身可持有换行、Enter 提交时整段发出，仅 paste 处理截断。新增 `_MultiLineInput(Input)` 重写 `_on_paste` 插入完整文本（不截断）；widget 类型不变（仍 `Input` 子类），故 `query_one("#input", Input)` / `.value` / `press("enter")` 全部兼容，零布局/测试改动。提交后历史区回显完整多行文本。回归 `test_tui_render.py` 新增 `multiline_paste_preserved`（共 12 项）。注：手动键入换行仍受终端 shift+enter 兼容性限制（本修复聚焦粘贴这一主路径，手动多行可用 Read 工具引用文件替代）。
- [x] EditTool 原子写入（数据完整性）：`_edit_file` 直写 `open(file,'w')` 截断目标后逐步写入，写入中途崩溃/中断（Ctrl+C/OOM/磁盘满）留半截文件、无回滚。改用 `WriteTool._atomic_write` 同款模式：同目录 `tempfile.mkstemp` + `os.fdopen`/flush/`os.fsync` 落盘 + `os.replace` 原子替换 + 失败清理临时文件。回归 `test_tools.py` 新增 `edit_atomic_preserves_original_on_failure`（6->7 项，模拟 `os.replace` 失败断言原文件完整 + 无残留临时文件，revert-verify 确认用例有齿）。
- [x] WriteTool 覆盖写入原子性（Windows 实测缺陷）：`_atomic_write` 收尾 `shutil.move` 在 Windows 覆盖已存在文件时回退 `copy2`+`unlink`（非原子，拷贝中途崩溃截断目标），覆盖正是编辑主路径。改 `os.replace`（Windows/POSIX 均原子替换）。回归 `test_tools.py` 新增 `write_overwrite_atomic_preserves_original_on_failure`（7->8 项，revert-verify 有齿）。
- [x] GrepTool 单文件 count/content + Windows 盘符解析（实测三缺陷）：单文件缺 `-H` 致 count 丢匹配、content 行号误当文件名；`_parse_content_output` 从左 `split(':')` 把盘符 `C:` 当分隔符致 content 模式 Windows 下全废。修：始终 `-H` + 非贪婪正则 `^(.*?):(\d+):(.*)$`（盘符冒号后跟 `\` 非数字，首组停在真 `:行号:`）+ 无 `-n` 用 `rsplit`。回归 `test_tools.py` 新增 `grep_single_file_and_content_modes`（8->9 项，revert-verify 有齿）。
- [x] BashTool Windows 编码 + 失败命令丢输出（实测两缺陷）：`Popen(text=True)` 默认 locale 编码（中文 Windows cp936），发非 ASCII 命令 `gbk codec can't encode` 抛错、UTF-8 输出误解码。改 `encoding=utf-8,errors=replace`。又错误路径 content 仅含 error+session_id 丢弃 output/stderr/exit_code，agent 无法据失败输出诊断。错误路径改为与成功路径结构对齐。回归 `test_tools.py` 新增 `bash_nonascii_and_error_output`（9->10 项，revert-verify 有齿）。





## 提交节奏
- 每完成一个阶段（或阶段内可独立运行的切片）-> commit + push。
- 开发日志 `doc/开发日志.md` 追加日期与完成项。
