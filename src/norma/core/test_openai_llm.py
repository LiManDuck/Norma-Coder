"""OpenAILLM 响应解析回归测试。

锁定 ``_parse_response``（非流式）的关键正确性：
1. ``reasoning_content`` 必须透传到 ``AssistantMessage.reason_content``
   （此前漏传，导致默认 ``stream_mode=False`` 下思考模型推理被丢弃；
   TUI 非流式分支会读 ``response_message.reason_content``）。
2. 空 ``choices`` 不崩溃（与流式路径一致）。
3. ``tool_calls`` 解析 + ``finish_reason`` 映射。
4. ``usage`` 透传。

``_parse_response`` 仅访问 completion 的属性，故用 ``SimpleNamespace`` 构造假响应，
无需真实网络。``OpenAILLM.__init__`` 只本地构造 client，离线可用。

运行：``python -m norma.core.test_openai_llm``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.openai_llm import OpenAILLM  # noqa: E402


def _make_llm() -> OpenAILLM:
    # __init__ 仅本地构造 AsyncOpenAI client，不触网
    return OpenAILLM(model="test-model", api_key="sk-test", base_url="http://localhost/v1")


def _msg(content=None, reasoning_content=None, tool_calls=None):
    return SimpleNamespace(
        content=content, reasoning_content=reasoning_content, tool_calls=tool_calls
    )


def _choice(message, finish_reason="stop"):
    return SimpleNamespace(message=message, finish_reason=finish_reason)


def _completion(choices, usage=None):
    return SimpleNamespace(choices=choices, usage=usage)


def test_parse_preserves_reasoning_content() -> None:
    llm = _make_llm()
    comp = _completion([_choice(_msg(content="答案是 42", reasoning_content="我先思考..."))])
    resp = llm._parse_response(comp)
    assert resp.response_message.reason_content == "我先思考...", \
        f"非流式应透传 reasoning_content，实际: {resp.response_message.reason_content!r}"
    assert resp.response_message.content == "答案是 42"
    print("[PASS] test_parse_preserves_reasoning_content")


def test_parse_empty_choices_no_crash() -> None:
    llm = _make_llm()
    comp = _completion(choices=[])  # content_filter 等极端响应
    resp = llm._parse_response(comp)
    assert resp.response_message.content == "", "空 choices 应回退空助手消息"
    assert resp.finish_reason == "unknown", f"空 choices finish_reason 应为 unknown，实际: {resp.finish_reason}"
    assert resp.tool_calls is None
    print("[PASS] test_parse_empty_choices_no_crash")


def test_parse_tool_calls_and_finish_reason() -> None:
    llm = _make_llm()
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="Read", arguments='{"file_path": "a.py"}'),
    )
    comp = _completion([_choice(_msg(content=None, tool_calls=[tc]), finish_reason="tool_calls")])
    resp = llm._parse_response(comp)
    assert resp.finish_reason == "tool_calls"
    tcs = resp.tool_calls
    assert tcs and len(tcs) == 1
    assert tcs[0].tool_call_id == "call_1"
    assert tcs[0].tool_call_name == "Read"
    assert tcs[0].tool_call_arguments == {"file_path": "a.py"}, \
        f"arguments 应解析为 dict，实际: {tcs[0].tool_call_arguments!r}"
    print("[PASS] test_parse_tool_calls_and_finish_reason")


def test_parse_tool_calls_invalid_json_falls_back_to_string() -> None:
    """arguments 非合法 JSON 时回退为原始字符串（不崩）。"""
    llm = _make_llm()
    tc = SimpleNamespace(
        id="call_2",
        function=SimpleNamespace(name="Bash", arguments="not-json{"),
    )
    comp = _completion([_choice(_msg(tool_calls=[tc]), finish_reason="tool_calls")])
    resp = llm._parse_response(comp)
    assert resp.tool_calls[0].tool_call_arguments == "not-json{"
    print("[PASS] test_parse_tool_calls_invalid_json_falls_back_to_string")


def test_parse_usage() -> None:
    llm = _make_llm()
    usage = SimpleNamespace(prompt_tokens=128, completion_tokens=64)
    comp = _completion([_choice(_msg(content="hi"))], usage=usage)
    resp = llm._parse_response(comp)
    assert resp.prompt_tokens == 128
    assert resp.completion_tokens == 64
    print("[PASS] test_parse_usage")


def main() -> int:
    failures = 0
    for runner, name in (
        (test_parse_preserves_reasoning_content, "parse_preserves_reasoning_content"),
        (test_parse_empty_choices_no_crash, "parse_empty_choices_no_crash"),
        (test_parse_tool_calls_and_finish_reason, "parse_tool_calls_and_finish_reason"),
        (test_parse_tool_calls_invalid_json_falls_back_to_string, "parse_tool_calls_invalid_json"),
        (test_parse_usage, "parse_usage"),
    ):
        try:
            runner()
        except AssertionError as exc:
            print(f"[FAIL] {name}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[ERROR] {name}: {exc}")
            traceback.print_exc()
            failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nALL openai_llm parse tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
