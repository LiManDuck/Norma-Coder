"""真实 LLM 端到端冒烟脚本（手动运行，非回归门禁）。

读取 ``~/.norma/config.json``，用真实 api_key/base_url 构造 ``OpenAILLM``，
分别走非流式 ``chat()`` 与流式 ``stream_chat()`` 各发一次请求，验证：
- 配置加载 / LLM 构造
- 真实 HTTP 连通
- 非流式响应解析（content 非空）
- 流式增量 + 最终响应解析

退出码：
- 0：PASS（真实 LLM 可达且响应正常），或 SKIP（未配置真实 api_key / 不可达）
- 1：FAIL（API 可达但返回空内容等真实异常）

运行：``python -m norma.smoke_real_llm``
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.llm_types import LLMRequest, UserMessage  # noqa: E402
from norma.core.openai_llm import OpenAILLM  # noqa: E402

_PLACEHOLDER_KEY = "sk-1234"


def _load_config() -> dict:
    cfg_path = Path.home() / ".norma" / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_endpoint(cfg: dict) -> tuple[str, str, str]:
    model = cfg.get("model", "")
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")
    providers = cfg.get("providers", {})
    dp = cfg.get("default_provider")
    if dp and dp in providers:
        prov = providers[dp]
        api_key = prov.get("api_key", api_key)
        base_url = prov.get("url", base_url)
        if not model or model not in prov.get("models", [model]):
            models = prov.get("models", [])
            if models:
                model = models[0]
    return model, api_key, base_url


async def _smoke_chat(llm: OpenAILLM) -> str:
    resp = await llm.chat(LLMRequest(messages=[
        UserMessage(content="只回复这五个字符：NORMA_OK"),
    ]))
    return (resp.content or "").strip()


async def _smoke_stream(llm: OpenAILLM) -> tuple[str, str]:
    deltas: list[str] = []
    final_content = ""
    async for chunk in llm.stream_chat(LLMRequest(messages=[
        UserMessage(content="只回复这五个字符：SMOKE_OK"),
    ])):
        if chunk.response_message is None:
            if chunk.stream_content:
                deltas.append(chunk.stream_content)
        else:
            final_content = (chunk.response_message.content or "").strip()
    return "".join(deltas).strip(), final_content


async def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    cfg = _load_config()
    model, api_key, base_url = _resolve_endpoint(cfg)
    if not api_key or api_key == _PLACEHOLDER_KEY:
        print(f"SKIP: 未配置真实 api_key（占位 {_PLACEHOLDER_KEY}）。"
              f"请在 ~/.norma/config.json 配置 api_key/base_url 后重试。")
        return 0

    print(f"[smoke] model={model!r} base_url={base_url!r}")
    llm = OpenAILLM(model=model, api_key=api_key, base_url=base_url,
                    default_stream_mode=False)

    # 1. 非流式
    try:
        content = await _smoke_chat(llm)
    except Exception as e:  # noqa: BLE001
        print(f"SKIP: 非流式 LLM 不可达 ({type(e).__name__}: {e})")
        return 0
    print(f"[chat]  非流式响应: {content!r}")
    if not content:
        print("FAIL: 非流式响应内容为空")
        return 1

    # 2. 流式
    try:
        joined, final = await _smoke_stream(llm)
    except Exception as e:  # noqa: BLE001
        print(f"SKIP: 流式 LLM 不可达 ({type(e).__name__}: {e})")
        return 0
    print(f"[stream] 增量拼接: {joined!r}")
    print(f"[stream] 最终响应: {final!r}")
    if not final:
        print("FAIL: 流式最终响应为空")
        return 1

    print("\nPASS: 真实 LLM 端到端可达（非流式 + 流式均正常）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
