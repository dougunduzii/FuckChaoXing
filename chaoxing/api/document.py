"""文档 / 阅读 / 空页任务点：都是单次打卡。

注意：超星对 document / read 这两类**不会**在 cards 页返回可靠的 isPassed，
唯一判据是打卡接口的回执（`{"msg":"考核点已经完成","status":true}`）。
所以这里把回执解析出来交给上层，别拿 HTTP 200 当成功。
"""

from __future__ import annotations

import json
import re
import time

from ..session import CxSession
from ..utils import text_of

DOC_URL = "https://mooc1.chaoxing.com/ananas/job/document"
READ_URL = "https://mooc1.chaoxing.com/ananas/job/readv2"

_NODE_RE = re.compile(r"nodeId_(.*?)-")

# 回执里出现这些字样 = 考核点已经算完成
_OK_WORDS = ("已经完成", "已完成", "考核点已完成")


def knowledge_from_other_info(other_info: str, fallback: str = "") -> str:
    m = _NODE_RE.search(other_info or "")
    return m.group(1) if m else fallback


def parse_receipt(status: int, body: str) -> tuple[bool, str]:
    """解析打卡回执 → (是否算完成, 给日志看的一句话)。"""
    body = (body or "").strip()
    msg = ""
    ok = status == 200
    try:
        data = json.loads(body) if body.startswith("{") else {}
    except json.JSONDecodeError:
        data = {}
    if data:
        msg = str(data.get("msg") or data.get("message") or "")
        flag = data.get("status")
        if flag in (True, "true", 1, "1"):
            ok = True
        elif flag in (False, "false", 0, "0"):
            ok = any(w in msg for w in _OK_WORDS)
    if not msg:
        msg = body[:80]
    if any(w in msg for w in _OK_WORDS):
        ok = True
    return ok, msg


async def report_document(
    sess: CxSession,
    *,
    jobid: str,
    knowledge_id: str,
    course_id: str,
    clazz_id: str,
    jtoken: str,
) -> tuple[bool, str]:
    params = {
        "jobid": jobid,
        "knowledgeid": knowledge_id or "",
        "courseid": course_id,
        "clazzid": clazz_id,
        "jtoken": jtoken,
        "_dc": str(int(time.time() * 1000)),
    }
    resp = await sess.get(
        DOC_URL,
        params=params,
        headers={"Referer": "https://mooc1.chaoxing.com/ananas/modules/pdf/index.html"},
    )
    return parse_receipt(resp.status_code, text_of(resp))


async def report_read(
    sess: CxSession,
    *,
    jobid: str,
    knowledge_id: str,
    course_id: str,
    clazz_id: str,
    jtoken: str,
) -> tuple[bool, str]:
    params = {
        "jobid": jobid,
        "knowledgeid": knowledge_id,
        "jtoken": jtoken,
        "courseid": course_id,
        "clazzid": clazz_id,
    }
    resp = await sess.get(READ_URL, params=params, headers={"Referer": "https://mooc1.chaoxing.com/"})
    return parse_receipt(resp.status_code, text_of(resp))
