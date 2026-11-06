"""AI 答题器单测：地址归一、答案归一、JSON 抽取、分片调用与降级。"""

from __future__ import annotations

import json

import httpx
import pytest

from chaoxing.ai import AiError, AiSolver, _extract_json, endpoint_of, normalize_answer
from chaoxing.api.quiz import Question
from chaoxing.config import AiCfg


def q(qid="7001", qtype="single", options=("甲", "乙", "丙", "丁"), title="题干"):
    return Question(id=qid, qtype=qtype, type_code="0", title=title, options=list(options))


def solver_with(handler, **kw) -> AiSolver:
    cfg = AiCfg(enabled=True, base_url="https://api.test/v1", api_key="sk-test", **kw)
    s = AiSolver(cfg)
    # 替身客户端要带上和正式客户端一样的默认头，否则测不到 Authorization
    s._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
    )
    return s


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# ---------------- 纯函数 ----------------
def test_endpoint_of():
    assert endpoint_of("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert endpoint_of("https://api.openai.com/v1/") == "https://api.openai.com/v1/chat/completions"
    assert endpoint_of("https://x.cn") == "https://x.cn/v1/chat/completions"
    assert endpoint_of("https://x.cn/v1/chat/completions") == "https://x.cn/v1/chat/completions"
    with pytest.raises(AiError):
        endpoint_of("  ")


def test_normalize_single_and_multiple():
    assert normalize_answer("B", q()) == "B"
    assert normalize_answer("答案是 C。", q()) == "C"
    assert normalize_answer("Z", q()) == ""            # 越界字母丢弃
    multi = q(qtype="multiple")
    assert normalize_answer("d,a", multi) == "AD"      # 去重 + 升序
    assert normalize_answer("A、C", multi) == "AC"


def test_normalize_judgement_and_completion():
    j = q(qtype="judgement", options=("正确", "错误"))
    assert normalize_answer("对", j) == "A"
    assert normalize_answer("错误", j) == "B"
    assert normalize_answer("TRUE", j) == "A"
    c = q(qtype="completion", options=())
    assert normalize_answer("“矩阵”", c) == "矩阵"
    assert normalize_answer("a|||b", c) == "a|||b"


def test_extract_json_variants():
    assert _extract_json('{"answers": []}') == {"answers": []}
    assert _extract_json('```json\n{"answers": []}\n```') == {"answers": []}
    assert _extract_json('好的：\n{"a": 1}\n以上') == {"a": 1}
    assert _extract_json('[{"id": "1"}]') == [{"id": "1"}]
    with pytest.raises(AiError):
        _extract_json("完全不是 JSON")


# ---------------- 调用链 ----------------
@pytest.mark.asyncio
async def test_solve_schema_mode():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["mode"] = body["response_format"]["type"]
        seen["auth"] = request.headers.get("authorization")
        payload = json.loads(body["messages"][1]["content"].split("逐题作答：\n", 1)[1])
        answers = [{"id": p["id"], "answer": "C", "confidence": 0.9} for p in payload]
        return reply(json.dumps({"answers": answers}, ensure_ascii=False))

    s = solver_with(handler)
    try:
        got = await s.solve([q()], work="第1章 测验")
    finally:
        await s.close()
    assert seen["mode"] == "json_schema"
    assert seen["auth"] == "Bearer sk-test"
    assert got == {"7001": "C"}


@pytest.mark.asyncio
async def test_solve_falls_back_to_object_mode():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        mode = body["response_format"]["type"]
        calls.append(mode)
        if mode == "json_schema":
            return httpx.Response(400, text="json_schema not supported")
        return reply('{"7001": "A"}')

    s = solver_with(handler)
    try:
        got = await s.solve([q()])
    finally:
        await s.close()
    assert calls == ["json_schema", "json_object"]
    assert got == {"7001": "A"}


@pytest.mark.asyncio
async def test_solve_raises_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    s = solver_with(handler)
    with pytest.raises(AiError) as ei:
        await s.solve([q()])
    await s.close()
    assert "401" in str(ei.value)


@pytest.mark.asyncio
async def test_solve_batches_and_skips_unanswered():
    seen_ids = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"].split("逐题作答：\n", 1)[1])
        assert len(payload) == 1          # batch_size=1 → 每批一题
        seen_ids.append(payload[0]["id"])
        if payload[0]["id"] == "2":
            return reply('{"answers": []}')   # 这批故意不给答案
        item = {"id": payload[0]["id"], "answer": "B", "confidence": 1}
        return reply(json.dumps({"answers": [item]}))

    s = solver_with(handler, batch_size=1, retries=0)
    try:
        got = await s.solve([q("1"), q("2")])
    finally:
        await s.close()
    assert seen_ids[0] == "1"
    assert set(seen_ids[1:]) == {"2"}   # 第 2 批三种 json_mode 都会重试，仍不给答案
    assert got == {"1": "B"}
