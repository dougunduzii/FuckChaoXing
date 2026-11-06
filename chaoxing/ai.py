"""AI 答题器：用 OpenAI 兼容的 /v1/chat/completions 接口把题目做出来。

设计要点：
    * 走标准 OpenAI 协议（base_url + api_key 自填），不依赖 openai SDK；
    * 要求模型按 **JSON Schema** 返回，schema 在下面 _SCHEMA 里，strict=true；
    * 服务端不支持 json_schema 时自动降级 json_object，再不行就裸文本解析；
    * 返回值一律经过 _normalize 归一到超星认的格式（单选一个字母、多选升序字母串、
      判断 A/B、填空/简答短文本），避免模型自由发挥把答案写坏。

用法：
    from chaoxing.ai import AiSolver
    solver = AiSolver(settings.ai)
    got = await solver.solve(questions, work="第1章 测验")
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterable

import httpx
from loguru import logger

from .api.quiz import Question
from .config import AiCfg

_SCHEMA: dict[str, Any] = {
    "name": "quiz_answers",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "answer": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["id", "answer", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["answers"],
        "additionalProperties": False,
    },
}

_SYSTEM = """你是一个做题机器人，正在完成高校在线课程的章节测验。规则：
1. 选择题：单选只返回一个选项字母（如 A）；多选返回全部正确字母、按升序连写（如 ACD）；
2. 判断题：正确返回 A，错误返回 B；
3. 填空题：返回要填的文本，多个空用 ||| 分隔；
4. 简答题：返回不超过 80 字的中文答案；
5. 只根据题目与选项本身推理，不要输出解释、不要输出题号；
6. 必须严格按给定 JSON Schema 返回，answers 里每题一项，id 用题目给的 id 原样回填。"""

_TRUE_TOKENS = {"A", "T", "TRUE", "对", "正确", "√", "是", "Y", "YES", "1"}
_FALSE_TOKENS = {"B", "F", "FALSE", "错", "错误", "×", "X", "否", "N", "NO", "0"}


class AiError(RuntimeError):
    """AI 接口调用失败（网络 / 鉴权 / 返回不可解析）。"""


def endpoint_of(base_url: str) -> str:
    """把用户填的 base_url 归一化成 chat/completions 的完整地址。"""
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise AiError("ai.base_url 为空")
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return b + "/chat/completions"
    return b + "/v1/chat/completions"


def _option_letters(n: int) -> list[str]:
    return [chr(65 + i) for i in range(max(0, min(n, 26)))]


def normalize_answer(raw: str, q: Question) -> str:
    """把模型返回的自由文本压成超星能认的答案格式。"""
    text = (raw or "").strip()
    if not text:
        return ""
    letters = _option_letters(len(q.options))

    if q.qtype == "judgement":
        token = text.upper().strip(" 。.、,，:：")
        if token in _TRUE_TOKENS or any(t in token for t in ("正确", "对", "TRUE")):
            return "A"
        if token in _FALSE_TOKENS or any(t in token for t in ("错误", "错", "FALSE")):
            return "B"
        # 模型可能直接给了选项字母
        picked = re.findall(r"[A-B]", token)
        return picked[0] if picked else "A"

    if q.qtype in ("single", "multiple"):
        found = re.findall(r"[A-Z]", text.upper())
        picked = [c for c in found if c in letters]
        if not picked:
            return ""
        uniq = sorted(set(picked))
        if q.qtype == "single":
            return uniq[0]
        return "".join(uniq)

    # 填空 / 简答：保留原文，去掉包裹引号
    cleaned = text.strip().strip('"').strip("“”").strip()
    return cleaned[:200]


def _extract_json(content: str) -> Any:
    """从模型输出里抠出 JSON：兼容 ```json 围栏、前后废话、裸数组。"""
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    m = re.search(r"\[.*\]", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise AiError(f"模型返回不是 JSON：{content[:200]}")


def _pairs_to_map(data: Any) -> dict[str, str]:
    """把各种形状的返回值统一成 {题 id: 答案原文}。"""
    out: dict[str, str] = {}
    if isinstance(data, dict):
        if isinstance(data.get("answers"), list):
            for item in data["answers"]:
                if isinstance(item, dict) and item.get("id") is not None:
                    out[str(item["id"])] = str(item.get("answer", ""))
            return out
        for k, v in data.items():
            if isinstance(v, dict):
                v = v.get("answer", "")
            out[str(k)] = str(v)
        return out
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id") is not None:
                out[str(item["id"])] = str(item.get("answer", ""))
    return out


class AiSolver:
    """一个 work 一次请求，按 batch_size 分片，串行发送（避免自己再造一次风控）。"""

    def __init__(self, cfg: AiCfg) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=15.0),
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AiSolver":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ---------------- 对外 ----------------
    async def solve(self, questions: Iterable[Question], *, work: str = "") -> dict[str, str]:
        qs = [q for q in questions if q.options or q.qtype in ("completion", "shortanswer", "judgement")]
        if not qs:
            return {}
        result: dict[str, str] = {}
        size = max(1, int(self.cfg.batch_size))
        batches = [qs[i : i + size] for i in range(0, len(qs), size)]
        for idx, batch in enumerate(batches, 1):
            try:
                raw = await self._ask(batch, work=work, part=f"{idx}/{len(batches)}")
            except AiError as exc:
                # 鉴权/额度问题直接抛给上层；单批失败只丢这一批，别把整份测验拖死
                if "401" in str(exc) or "403" in str(exc):
                    raise
                logger.warning(f"[AI] 第 {idx}/{len(batches)} 批失败，跳过：{str(exc)[:160]}")
                continue
            for q in batch:
                ans = normalize_answer(raw.get(q.id, ""), q)
                if ans:
                    result[q.id] = ans
        logger.info(f"[AI] {work or '测验'} 共 {len(qs)} 题，返回 {len(result)} 个答案")
        return result

    # ---------------- 内部 ----------------
    async def _ask(self, batch: list[Question], *, work: str, part: str) -> dict[str, str]:
        payload_questions = []
        for q in batch:
            payload_questions.append(
                {
                    "id": q.id,
                    "type": q.qtype,
                    "stem": q.title,
                    "options": [
                        {"letter": _option_letters(len(q.options))[i] if i < 26 else "?", "text": opt}
                        for i, opt in enumerate(q.options)
                    ],
                }
            )
        user = (
            f"章节测验：{work or '未命名'}\n"
            f"本批共 {len(batch)} 题（第 {part} 批），逐题作答：\n"
            + json.dumps(payload_questions, ensure_ascii=False, indent=2)
        )
        modes = ["schema", "object", "text"]
        start = modes.index(self.cfg.json_mode) if self.cfg.json_mode in modes else 0
        last_err: Exception | None = None
        for mode in modes[start:]:
            try:
                content = await self._post(user, mode)
                data = _extract_json(content)
                got = _pairs_to_map(data)
                if got:
                    return got
                last_err = AiError("模型返回了空 answers")
            except AiError as exc:
                last_err = exc
                msg = str(exc)
                logger.debug(f"[AI] json_mode={mode} 失败：{msg[:160]}")
                if "401" in msg or "403" in msg:
                    raise  # 鉴权错了，换模式也没用
                continue
        raise last_err or AiError("AI 调用失败")

    async def _post(self, user: str, mode: str) -> str:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        if mode == "schema":
            body["response_format"] = {"type": "json_schema", "json_schema": _SCHEMA}
        elif mode == "object":
            body["response_format"] = {"type": "json_object"}
        elif mode == "text":
            body["messages"][0]["content"] += "\n只输出 JSON，不要任何其它文字。"

        url = endpoint_of(self.cfg.base_url)
        last: Exception | None = None
        for attempt in range(max(1, self.cfg.retries + 1)):
            try:
                resp = await self._client.post(url, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = AiError(f"网络错误：{exc}")
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = AiError(f"HTTP {resp.status_code}：{resp.text[:200]}")
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise AiError(f"HTTP {resp.status_code}：{resp.text[:300]}")
            try:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                raise AiError(f"响应结构异常：{resp.text[:200]} ({exc})") from exc
        raise last or AiError("AI 请求失败")
