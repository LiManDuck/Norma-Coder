# 权限 / Hook / Subagent 设计说明

> 起源：开发日志.md @ 2026-06-14
>
> 在保持 Norma-Coder 整体架构（messagebus + tool_manager + agent loop）不变的前提下，
> 增加三个能力：
>
> 1. Subagent (Agent 工具)
> 2. 工具权限系统
> 3. Hook 系统

---

## 1. 总览

```
            ┌──────────────────────┐
 user ────► │  CLI / NormaCLI       │── 加载 config.json
            │   - MessageBus        │
            │   - PermissionChecker │
            │   - HookManager       │
            │   - UserInputManager  │
            └──────────┬────────────┘
                       │
                ┌──────▼──────┐
                │  NormaCoder  │── messagebus 发布 agent/tool 事件
                │              │── 工具调用前调用 PermissionChecker
                │              │── 询问场景调用 UserInputManager
                └──┬────────┬──┘
       tool_calls  │        │ AgentTool（子代理工具）
                   │        ▼
                   │   ┌──────────┐
                   │   │ NormaCoder│ ← 由工厂在主 agent 内 spawn
                   │   └──────────┘
                   ▼
              工具执行
```

---

## 2. 配置文件 (`~/.norma/config.json`)

```json
{
  "model": "glm-4.5-air",
  "api_key": "sk-xxx",
  "base_url": "http://api.openai.rnd.huawei.com/v1",
  "permission": {
    "mode": "edit",
    "tools": {
      "Bash": "ask",
      "Edit": "allow",
      "Agent": "deny"
    }
  },
  "hooks": {
    "session-begin": [
      {"command": "echo session-begin"}
    ],
    "tool-execute-after": [
      {
        "command": "logger 'edit done'",
        "match": {"tool_name": "Edit"}
      }
    ]
  }
}
```

* `permission.mode`：`plan` / `edit` / `auto`
* `permission.tools`：单个工具显式覆盖（值为 `allow` / `ask` / `deny`）
* `hooks.<event>`：可写为对象或对象数组，支持的事件见后文

---

## 3. 权限系统

### 3.1 模式语义

| mode | 默认行为                                                                        |
| ---- | ------------------------------------------------------------------------------- |
| plan | 只读工具放行（Read/LS/Glob/Grep/TodoWrite），其它工具 DENY                     |
| edit | 只读 + 可写工具放行，Bash/Agent 等危险工具 ASK，未知工具 ASK                    |
| auto | 全部 ALLOW                                                                      |

### 3.2 决策流程

```
PermissionChecker.check(req)
   ├─ tools[req.name] 命中？  -> 直接返回该 decision
   ├─ mode == AUTO            -> ALLOW
   ├─ mode == PLAN
   │    └─ name in READ_ONLY  ? ALLOW : DENY
   └─ mode == EDIT
        ├─ name in READ_ONLY  -> ALLOW
        ├─ name in WRITE      -> ALLOW
        ├─ name in DANGEROUS  -> ASK
        └─ 其它               -> ASK
```

### 3.3 ASK 流程（使用 messagebus）

NormaCoder 在收到 `ASK` 时调用 `UserInputManager.request_confirmation`，
向 messagebus 发出 `UI_PROMPT`，等待 `USER_CONFIRM` / `USER_REJECT`。

前端各自处理 `UI_PROMPT`：TUI 弹出 `PermissionModal`（y 允许 / n,esc 拒绝）；
REPL 订阅 `UI_PROMPT` 后用 prompt_toolkit 交互式 y/N 确认（`prompt_confirm`
回调可注入，便于测试）。若调用层未注入 `user_input_manager`，则按拒绝处理（fail-safe）。

### 3.4 拒绝结果

被拒绝的工具会构造 `ToolRequestResult(is_error=True, content={"error":..., "denied":true})`
作为 ToolMessage 反馈给 LLM，从而让模型自行决定下一步。

---

## 4. Hook 系统

### 4.1 事件

| 事件名 (config key)    | 触发时机                                  | 对应消息总线类型              |
| ---------------------- | ----------------------------------------- | ----------------------------- |
| `session-begin`        | CLI 启动                                  | （由 cli 直接 dispatch）     |
| `session-end`          | CLI 退出                                  | （由 cli 直接 dispatch）     |
| `user-input`           | `UserInputManager.send_input`             | `USER_INPUT`                  |
| `tool-execute-before`  | NormaCoder 发布工具请求事件               | `AGENT_TOOL_REQUEST`          |
| `tool-execute-after`   | NormaCoder 发布工具结果事件               | `AGENT_TOOL_RESULT`           |
| `agent-response`       | NormaCoder 生成最终响应                   | `AGENT_RESPONSE`              |

### 4.2 配置语法

```json
"hooks": {
  "tool-execute-after": [
    "echo done",                                     // 直接是命令字符串
    {                                                // 完整配置
      "command": "/usr/local/bin/notify",
      "match": {"tool_name": "Edit"},
      "timeout": 10,
      "background": true
    }
  ]
}
```

* `match`: 字典，所有 `key.upper()` 必须在执行环境中精确匹配；命中后才执行
* `background`: true 时通过 `asyncio.create_task` 异步执行；false 时同步等待
* `timeout`: 执行超时秒数，默认 30

### 4.3 注入的环境变量

| 变量              | 含义                                         |
| ----------------- | -------------------------------------------- |
| `NORMA_HOOK_EVENT` / `EVENT` | 事件名（如 `tool-execute-after`） |
| `CONVERSATION_ID` | 会话 id                                      |
| `TOOL_NAME`       | 工具名（仅工具相关事件）                     |
| `USER_INPUT`      | 用户输入（仅 `user-input` 事件，截断 1KB）   |

---

## 5. Subagent / Agent 工具

注册名 `Agent`，参数：

| 参数             | 类型    | 说明                                              |
| ---------------- | ------- | ------------------------------------------------- |
| `prompt`         | string  | 子任务描述（必填；空字符串可用于查询后台进度）    |
| `session_id`     | string  | 复用已有子会话；不传则自动新建                    |
| `run_background` | boolean | true 时立即返回 `task_id`，结果异步写入 session   |
| `description`    | string  | 简短描述，用于日志 / 子 agent 名称                |

### 5.1 主要场景

1. **隔离上下文调研**：主 agent 让子 agent 读源码 / 跑命令再返回摘要，避免主 agent
   被工具结果撑爆 context。
2. **后台并行**：发起后台任务 → 干别的活 → 通过同 session_id 查询。

### 5.2 实现要点

* 工厂注入：`AgentTool(agent_factory=lambda name: NormaCoder(...))`，避免对 NormaCoder
  的硬依赖、防止循环导入；
* `enable_subagent=False`：默认子 agent 不再注册 `Agent` 工具，杜绝无限递归 spawn；
* `_sessions: Dict[str, _SubagentSession]`：以 session_id 为索引，保留 LRU 上限，避免内存泄露；
* 后台模式：`_SubagentSession.background_task` 持有 `asyncio.Task`，结果写到
  `background_result`，再次调用时返回。

---

## 6. NormaCoder 改动摘要

新增构造参数：

```python
NormaCoder(
    ...
    message_bus=MessageBus|None,
    permission_checker=PermissionChecker|None,
    hook_manager=HookManager|None,
    user_input_manager=UserInputManager|None,
    enable_subagent=True,
    subagent_factory=Callable|None,
    conversation_id=str|None,
)
```

主循环逻辑：

1. 在每个 yield 事件之前同步发布到 messagebus（`_publish` / `_publish_agent_response`）。
2. LLM 返回 tool_calls 后调用 `_apply_permission` 拆分为 (allowed, denied)，
   denied 直接产出错误工具结果，不再执行底层工具。
3. allowed 工具仍由 `tool_manager.execute_tools` 并发执行，最后按原顺序合并结果。

---

## 7. 局限与后续 TODO

* `PermissionChecker.check` 当前只看工具名，不看具体参数（如 Bash 的命令内容）。
  如需更细颗粒度，可以扩展为可插拔策略链。
* HookManager 的 `match` 仅支持字符串完全匹配；未来可扩展正则 / glob。
* AgentTool 后台任务没有持久化，进程结束即失效。
