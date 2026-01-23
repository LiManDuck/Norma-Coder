# CLI 助手架构详解


# cli端显示

```


# 布局

┌─────────────────────────────────────────────┐
│ [历史滚动区]                                 │
│ > 帮我分析一下市场趋势                        │
│ ● 接收任务                                   │
│   ⎿ 分析市场趋势                             │
│ ● 思考中...                                  │
│ ● 调用工具 (search)                          │
│   ⎿ search: Done · 1.2s                     │
│ ─────────────────────────────────           │
│ 根据最新数据...                              │
│ ─────────────────────────────────           │
│                                             │
│ > 继续深入分析                               │
│ ● 接收任务                                   │
│   ⎿ 继续深入分析                             │
│                                             │
├─────────────────────────────────────────────┤
│ [Live状态区 - 实时更新]                       │
│ ⚡ 执行中：正在调用工具 search (步骤 2/5)      │
├─────────────────────────────────────────────┤
│ [输入框 - 固定底部]                           │
│ > _                                         │
└─────────────────────────────────────────────┘

设计 ： Rich Layout + Prompt Toolkit


```

技术要点：

Rich.Layout: 分区布局管理
Rich.Live: 只更新Live区，不影响历史
Prompt Toolkit: 独立在底部渲染输入框
Console.print(): 追加历史记录






## 数据流详解

### 1. 用户输入流

```
用户 → REPL.start() → display.prompt_user() → 获取输入字符串
                                                      ↓
                                        判断: 命令?（/开头视为命令） or 文本?
                                                      ↓
                                        ┌──────────────┴─────────────┐
                                        ↓                            ↓
                                   处理命令                      调用agent 
                                 _handle_command()          await agent.run(query )
```

### 2. 模型响应流

```
agent.run(messages)
            ↓

生成 Event 流: Event1, Event2, Event3...
            ↓
REPL 接收事件流
            ↓
逐个传给 Display.handle(event)
            ↓
Display 根据 event.type 调用对应方法
  ├─ TextChunkEvent → on_text_chunk() → 打印文本
  ├─ ThinkingEvent  → on_thinking()   → 显示思考
  ├─ ToolUseEvent   → on_tool_use()   → 显示工具调用
  └─ CompleteEvent  → on_complete()   → 换行，添加到历史
```

### 3. 事件处理流

```
ModelInterface.send_message()
    ↓
for event in stream:
    ↓
    yield TextChunkEvent(text)
    ↓
REPL._process_assistant_response()
    ↓
    display.handle(event)  # 统一入口
        ↓
        event.type == TEXT_CHUNK?
            → display.on_text_chunk(event)
                → console.print(text, end="")
        
        event.type == THINKING?
            → display.on_thinking(event)
                → console.print("💭 思考中...")
        
        event.type == COMPLETE?
            → display.on_complete(event)
                → conversation.add_assistant_message()
```
