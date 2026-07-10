"""OpenAILLM I/O 回归测试（parse / build / stream 三路径）。

锁定 LLM 层三个此前无回归覆盖的关键路径（真实 LLM 因占位 key 不可达，长期无保护）：

``_parse_response``（非流式响应解析）：
1. ``reasoning_content`` 透传到 ``AssistantMessage.reason_content``
   （此前漏传，导致默认 ``stream_mode=False`` 下思考模型推理被丢弃；
   TUI 非流式分支会读 ``response_message.reason_content``）。
2. 空 ``choices`` 不崩溃（与流式路径一致）。
3. ``tool_calls`` 解析 + ``finish_reason`` 映射。
4. ``usage`` 透传。

``_build_messages``（请求构造，与 parse 反向对称）：
- 四类消息 role 映射 + ToolMessage -> role=tool+tool_call_id
- AssistantMessage.reason_content -> 请求 reasoning_content
- dict 参数序列化为 JSON 字符串 / 空 assistant 不含 content 键

``stream_chat``（流式累积，此前完全由桩替换、未测真实累积）：
- 文本增量逐 chunk yield + 最终累积
- reasoning_content 累积
- tool_calls 跨 chunk 分片拼接
- usage 在无 choices 的尾部 chunk 到达

parse/build 用 ``SimpleNamespace`` 假对象；stream 用 ``_FakeStream`` 假 async 流
替换 ``llm.client.chat.completions.create``。均无需真实网络。
``OpenAILLM.__init__`` 只本地构造 client，离线可用。

运行：``python -m norma.core.test_openai_llm``
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.core.openai_llm import OpenAILLM  # noqa: E402
from norma.core.llm_types import (  # noqa: E402
    LLMRequest,
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolMessage,
)
from norma.core.tool_types import ToolRequest, ToolRequestResult  # noqa: E402


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


# ---------------- _build_messages（请求路径，与 _parse_response 对称）----------------

def test_build_messages_roles_and_tool() -> None:
    """System/User/Assistant/Tool 四类消息 -> 正确 OpenAI role。"""
    llm = _make_llm()
    req = ToolRequest(tool_call_id="tc1", tool_call_name="Read", tool_call_arguments={"file_path": "a.py"})
    result = ToolRequestResult(request=req, result="FILE", content="FILE", is_error=False, execution_times=0.0)
    msgs = [
        SystemMessage(content="你是助手"),
        UserMessage(content="读 a.py"),
        AssistantMessage(content="好的", tool_calls=[req]),
        ToolMessage(tool_result=result, content="FILE"),
    ]
    out = llm._build_messages(LLMRequest(messages=msgs))
    assert out[0] == {"role": "system", "content": "你是助手"}
    assert out[1] == {"role": "user", "content": "读 a.py"}
    assert out[2]["role"] == "assistant"
    assert out[3] == {"role": "tool", "tool_call_id": "tc1", "content": "FILE"}, \
        f"ToolMessage 应映射为 role=tool + tool_call_id，实际: {out[3]}"
    print("[PASS] test_build_messages_roles_and_tool")


def test_build_assistant_reasoning_content_roundtrip() -> None:
    """AssistantMessage.reason_content -> 请求 reasoning_content（与 _parse_response 反向一致）。"""
    llm = _make_llm()
    out = llm._build_messages(LLMRequest(messages=[
        AssistantMessage(content="答案", reason_content="我的推理"),
    ]))
    assert out[0]["reasoning_content"] == "我的推理", \
        f"reason_content 应作为 reasoning_content 发送，实际: {out[0]}"
    print("[PASS] test_build_assistant_reasoning_content_roundtrip")


def test_build_tool_calls_arguments_serialized() -> None:
    """dict 参数应序列化为 JSON 字符串（OpenAI function.arguments 要求 str）。"""
    llm = _make_llm()
    req = ToolRequest(tool_call_id="tc1", tool_call_name="Read", tool_call_arguments={"file_path": "a.py"})
    out = llm._build_messages(LLMRequest(messages=[AssistantMessage(content="", tool_calls=[req])]))
    tc = out[0]["tool_calls"][0]
    assert tc["id"] == "tc1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "Read"
    # dict -> json string
    assert isinstance(tc["function"]["arguments"], str), "arguments 应为 str"
    assert tc["function"]["arguments"] == '{"file_path": "a.py"}', \
        f"dict 应序列化为 JSON 字符串，实际: {tc['function']['arguments']!r}"
    print("[PASS] test_build_tool_calls_arguments_serialized")


def test_build_assistant_no_content_omits_key() -> None:
    """无 content/reason/tool_calls 的 AssistantMessage 不含 content 键（锁定现有行为）。"""
    llm = _make_llm()
    out = llm._build_messages(LLMRequest(messages=[AssistantMessage(content="")]))
    assert out[0] == {"role": "assistant"}, f"空 assistant 应仅含 role，实际: {out[0]}"
    print("[PASS] test_build_assistant_no_content_omits_key")


# ---------------- stream_chat（流式累积，此前完全由桩替换、未测真实累积）----------------

class _FakeStream:
    """假 async 流：逐个 yield 预构造 chunk。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


def _delta(content=None, reasoning=None, tool_calls=None):
    return SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls)


def _tc_delta(index, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def _chunk(delta=None, finish_reason=None, usage=None, choices=None):
    if choices is not None:
        ch = choices
    else:
        ch = [SimpleNamespace(delta=delta or _delta(), finish_reason=finish_reason)]
    return SimpleNamespace(choices=ch, usage=usage)


def _patch_stream(llm: OpenAILLM, chunks) -> None:
    """把 llm.client 换成返回假流的桩（stream_chat 经 await create(...) 拿流）。"""
    async def _fake_create(**kwargs):
        return _FakeStream(chunks)

    llm.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_fake_create)
        )
    )


async def test_stream_text_accumulation() -> None:
    """文本增量逐 chunk yield，最终 response_message.content 为累积全文。"""
    llm = _make_llm()
    chunks = [
        _chunk(delta=_delta(content="Hello")),
        _chunk(delta=_delta(content=" world")),
        _chunk(delta=_delta(), finish_reason="stop"),
    ]
    _patch_stream(llm, chunks)
    results = [r async for r in llm.stream_chat(LLMRequest(messages=[UserMessage(content="hi")]))]
    deltas, final = results[:-1], results[-1]
    assert [d.stream_content for d in deltas] == ["Hello", " world"], \
        f"增量应逐 chunk yield，实际: {[d.stream_content for d in deltas]}"
    assert final.response_message.content == "Hello world", \
        f"最终 content 应累积，实际: {final.response_message.content!r}"
    assert final.finish_reason == "stop"
    print("[PASS] test_stream_text_accumulation")


async def test_stream_reasoning_accumulation() -> None:
    """reasoning_content 增量累积到最终 response_message.reason_content。"""
    llm = _make_llm()
    chunks = [
        _chunk(delta=_delta(reasoning="思考1")),
        _chunk(delta=_delta(reasoning="思考2", content="答案")),
        _chunk(delta=_delta(), finish_reason="stop"),
    ]
    _patch_stream(llm, chunks)
    results = [r async for r in llm.stream_chat(LLMRequest(messages=[UserMessage(content="hi")]))]
    final = results[-1]
    assert final.response_message.reason_content == "思考1思考2", \
        f"reason_content 应累积，实际: {final.response_message.reason_content!r}"
    assert final.response_message.content == "答案"
    print("[PASS] test_stream_reasoning_accumulation")


async def test_stream_tool_calls_split_across_chunks() -> None:
    """tool_calls 跨 chunk 分片：首片含 id+name，后续片含 arguments 片段，应拼接后解析。"""
    llm = _make_llm()
    chunks = [
        _chunk(delta=_delta(tool_calls=[_tc_delta(0, id="call_1", name="Read", arguments='{"file":"')])),
        _chunk(delta=_delta(tool_calls=[_tc_delta(0, arguments='a.py"}')])),
        _chunk(delta=_delta(), finish_reason="tool_calls"),
    ]
    _patch_stream(llm, chunks)
    results = [r async for r in llm.stream_chat(LLMRequest(messages=[UserMessage(content="read a.py")]))]
    final = results[-1]
    assert final.finish_reason == "tool_calls"
    tcs = final.tool_calls
    assert tcs and len(tcs) == 1
    assert tcs[0].tool_call_id == "call_1"
    assert tcs[0].tool_call_name == "Read"
    assert tcs[0].tool_call_arguments == {"file": "a.py"}, \
        f"分片 arguments 应拼接后解析为 dict，实际: {tcs[0].tool_call_arguments!r}"
    print("[PASS] test_stream_tool_calls_split_across_chunks")


async def test_stream_usage_in_trailing_chunk() -> None:
    """usage 可能在无 choices 的末尾 chunk 到达（stream_options include_usage）。"""
    llm = _make_llm()
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=25)
    chunks = [
        _chunk(delta=_delta(content="hi")),
        _chunk(delta=_delta(), finish_reason="stop"),
        _chunk(choices=[], usage=usage),
    ]
    _patch_stream(llm, chunks)
    results = [r async for r in llm.stream_chat(LLMRequest(messages=[UserMessage(content="hi")]))]
    final = results[-1]
    assert final.prompt_tokens == 50, f"usage 应从尾部 chunk 捕获，实际: {final.prompt_tokens}"
    assert final.completion_tokens == 25
    print("[PASS] test_stream_usage_in_trailing_chunk")


def test_switch_model_provider_updates_default_provider() -> None:
    """switch_model('provider/model') 应同步 _default_provider。

    此前 switch_model 更新 model/base_url/api_key/client 但漏更 _default_provider，
    导致 /model 显示仍把旧 provider 标为当前（*），且无法用 ← 标记刚切换的模型
    （cmd_model 的显示按 _default_provider 匹配）。与 switch_provider 行为对齐。
    """
    llm = OpenAILLM(
        model="m-a",
        api_key="sk-a",
        base_url="http://host-a/v1",
        providers={
            "provA": {"url": "http://host-a/v1", "api_key": "sk-a", "models": ["m-a"]},
            "provB": {"url": "http://host-b/v1", "api_key": "sk-b", "models": ["m-b1", "m-b2"]},
        },
        default_provider="provA",
    )
    assert llm._default_provider == "provA"

    llm.switch_model("provB/m-b2")

    assert llm.model == "m-b2", f"model 应切换到 m-b2，实际 {llm.model!r}"
    assert llm._default_provider == "provB", (
        f"_default_provider 应同步到 provB，实际 {llm._default_provider!r}")
    assert llm._base_url == "http://host-b/v1", f"base_url 应为 provB 的，实际 {llm._base_url!r}"
    assert llm._api_key == "sk-b", f"api_key 应为 provB 的，实际 {llm._api_key!r}"

    # 纯模型名（无 provider）不应改动 _default_provider
    llm.switch_model("m-b1")
    assert llm.model == "m-b1"
    assert llm._default_provider == "provB", "纯模型切换不应改动 _default_provider"

    # 未知 provider 应抛 ValueError
    try:
        llm.switch_model("provX/m")
    except ValueError:
        pass
    else:
        raise AssertionError("未知 provider 应抛 ValueError")
    print("[PASS] test_switch_model_provider_updates_default_provider")


def main() -> int:
    failures = 0
    for runner, name in (
        (test_parse_preserves_reasoning_content, "parse_preserves_reasoning_content"),
        (test_parse_empty_choices_no_crash, "parse_empty_choices_no_crash"),
        (test_parse_tool_calls_and_finish_reason, "parse_tool_calls_and_finish_reason"),
        (test_parse_tool_calls_invalid_json_falls_back_to_string, "parse_tool_calls_invalid_json"),
        (test_parse_usage, "parse_usage"),
        (test_build_messages_roles_and_tool, "build_messages_roles_and_tool"),
        (test_build_assistant_reasoning_content_roundtrip, "build_assistant_reasoning_content"),
        (test_build_tool_calls_arguments_serialized, "build_tool_calls_arguments_serialized"),
        (test_build_assistant_no_content_omits_key, "build_assistant_no_content_omits_key"),
        (test_stream_text_accumulation, "stream_text_accumulation"),
        (test_stream_reasoning_accumulation, "stream_reasoning_accumulation"),
        (test_stream_tool_calls_split_across_chunks, "stream_tool_calls_split"),
        (test_stream_usage_in_trailing_chunk, "stream_usage_in_trailing_chunk"),
        (test_switch_model_provider_updates_default_provider, "switch_model_provider_updates_default_provider"),
    ):
        try:
            if inspect.iscoroutinefunction(runner):
                asyncio.run(runner())
            else:
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
    print("\nALL openai_llm tests passed (parse + build + stream)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
