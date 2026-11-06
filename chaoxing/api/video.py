"""视频 / 音频任务点：播放信息查询 + 进度上报。"""

from __future__ import annotations

import time
from typing import Any

from ..crypto import enc as enc_mod
from ..session import CxSession

STATUS_URL = "https://mooc1.chaoxing.com/ananas/status/{object_id}"
LOG_URL = "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/{cpi}/{dtoken}"

VIDEO_REFERER = "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842"
AUDIO_REFERER = "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842"


def referer(dtype: str) -> str:
    return AUDIO_REFERER if dtype.lower() == "audio" else VIDEO_REFERER


async def fetch_status(sess: CxSession, object_id: str, fid: str = "") -> dict[str, Any] | None:
    url = STATUS_URL.format(object_id=object_id)
    data = await sess.get_json(url, params={"k": fid or "0", "flag": "normal"}, headers={"Referer": VIDEO_REFERER})
    if isinstance(data, dict) and data.get("status") == "success":
        return data
    return None


def build_params(
    *,
    clazz_id: str,
    course_id: str,
    jobid: str,
    object_id: str,
    other_info: str,
    userid: str,
    playing_time: int,
    duration: int,
    dtype: str,
    version: str,
    att: dict[str, Any] | None = None,
    rt: str | None = None,
) -> dict[str, Any]:
    pt = max(0, min(int(playing_time), int(duration)))
    params: dict[str, Any] = {
        "clazzId": clazz_id,
        "courseId": course_id,
        "jobid": jobid,
        "objectId": object_id,
        "otherInfo": other_info,
        "userid": userid,
        "playingTime": pt,
        "duration": int(duration),
        "clipTime": enc_mod.clip_time(duration),
        "isdrag": 3,
        "view": "pc",
        "dtype": dtype,
        "enc": enc_mod.compute(version, clazz_id, userid, jobid, object_id, pt, duration),
        "_t": str(int(time.time() * 1000)),
    }
    if rt:
        params["rt"] = rt
    for key in ("attDuration", "attDurationEnc", "videoFaceCaptureEnc"):
        if att and att.get(key):
            params[key] = att[key]
    return params


async def report(
    sess: CxSession,
    *,
    cpi: str,
    dtoken: str,
    params: dict[str, Any],
    dtype: str = "Video",
    lane: str | None = None,
    audit_task: int | None = None,
) -> tuple[bool, int, dict[str, Any] | None]:
    """返回 (isPassed, http_status, body)。"""
    url = LOG_URL.format(cpi=cpi, dtoken=dtoken)
    resp = await sess.get(url, params=params, headers={"Referer": referer(dtype)}, lane=lane, audit_task=audit_task)
    try:
        body = resp.json()
    except Exception:
        body = None
    passed = bool(isinstance(body, dict) and body.get("isPassed"))
    return passed, resp.status_code, body

async def fetch_status_raw(sess: CxSession, object_id: str, fid: str) -> dict[str, Any] | None:
    """原样返回 status 端点的 JSON，不做 status==success 过滤，用于判断 objectId 到底是什么。"""
    if not object_id:
        return None
    resp = await sess.get(
        STATUS_URL.format(object_id=object_id),
        params={"k": fid, "flag": "normal"},
        headers={"Referer": VIDEO_REFERER},
    )
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None
