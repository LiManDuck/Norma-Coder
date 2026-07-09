"""AgentRunner - 后台驱动 agent.run() 生成器，事件经 MessageBus 发布。

设计目的
--------
将 Agent 与前端解耦：``NormaCoder.run()`` 既 yield 事件又 ``_publish`` 到 MessageBus。
``AgentRunner`` 仅负责"驱动"生成器（迭代它），前端通过订阅 MessageBus 渲染即可，
无需直接消费生成器。SDK 用户仍可直接 ``async for event in agent.run(query)``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from norma.core.agent_types import AgentResponse, BaseAgent

logger = logging.getLogger(__name__)


class AgentRunner:
    """在后台任务中驱动一个 Agent 完成一次 query。"""

    def __init__(self, agent: BaseAgent):
        self.agent = agent
        self._task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, query: str) -> "asyncio.Task[Optional[AgentResponse]]":
        """启动一次 agent 执行（非阻塞）。重复启动会抛错。"""
        if self.running:
            raise RuntimeError("agent already running")
        self._task = asyncio.create_task(self._run(query), name="norma-agent-run")
        return self._task

    async def _run(self, query: str) -> Optional[AgentResponse]:
        final: Optional[AgentResponse] = None
        try:
            async for event in self.agent.run(query):
                # 事件已由 NormaCoder._publish 推送到 MessageBus；
                # 这里只驱动生成器前进，并捕获最终 AgentResponse。
                if isinstance(event, AgentResponse):
                    final = event
        except asyncio.CancelledError:
            logger.info("agent run cancelled")
            raise
        except Exception as exc:
            # 逃逸出 agent 内部 try/except 的意外异常（如 error_response 构造失败）
            # 必须上抛，让前端经 done_callback 显式提示，而非静默吞掉。
            logger.error(f"agent run error: {exc}", exc_info=True)
            raise
        return final

    async def wait(self) -> Optional[AgentResponse]:
        if self._task is None:
            return None
        return await self._task

    def cancel(self) -> None:
        """取消当前 agent 执行。"""
        if self.running:
            self._task.cancel()
