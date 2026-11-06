"""视频进度上报的 enc 计算（多版本；盐值运行时解析）。

盐值不写死在源码里，由 :mod:`chaoxing.crypto.keys` 运行时解析
（环境变量 CX_ENC_SALT → 本地缓存 → 探测播放器 bundle）。
"""

from __future__ import annotations

import hashlib
from typing import Callable

from .keys import ENC_SALT, get_secret


def current_salt() -> str:
    """当前生效的 enc 盐值。"""
    return get_secret(ENC_SALT)


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def enc_v2(
    clazz_id: str,
    uid: str,
    jobid: str,
    object_id: str,
    playing_time: int,
    duration: int,
    *,
    salt: str | None = None,
) -> str:
    """现行版：[clazzId][uid][jobid][objectId][pt*1000][salt][dur*1000][0_dur]"""
    s = current_salt() if salt is None else salt
    return md5(
        f"[{clazz_id}][{uid}][{jobid}][{object_id}]"
        f"[{int(playing_time) * 1000}][{s}]"
        f"[{int(duration) * 1000}][0_{int(duration)}]"
    )


def enc_v1(
    clazz_id: str,
    uid: str,
    jobid: str,
    object_id: str,
    playing_time: int,
    duration: int,
) -> str:
    """老版：[clazzId][uid][jobid][objectId][pt*1000][0_dur]"""
    return md5(
        f"[{clazz_id}][{uid}][{jobid}][{object_id}]"
        f"[{int(playing_time) * 1000}][0_{int(duration)}]"
    )


VERSIONS: dict[str, Callable[..., str]] = {"v2": enc_v2, "v1": enc_v1}
DEFAULT_VERSION = "v2"


def compute(
    version: str,
    clazz_id: str,
    uid: str,
    jobid: str,
    object_id: str,
    playing_time: int,
    duration: int,
    *,
    salt: str | None = None,
) -> str:
    if (version or DEFAULT_VERSION) == "v1":
        return enc_v1(clazz_id, uid, jobid, object_id, playing_time, duration)
    return enc_v2(clazz_id, uid, jobid, object_id, playing_time, duration, salt=salt)


def candidate_encs(
    clazz_id: str,
    uid: str,
    jobid: str,
    object_id: str,
    playing_time: int,
    duration: int,
    *,
    salt: str | None = None,
) -> dict[str, str]:
    """给探针脚本对拍用：一次算出所有版本（盐值只解析一次）。"""
    s = current_salt() if salt is None else salt
    return {
        "v2": enc_v2(clazz_id, uid, jobid, object_id, playing_time, duration, salt=s),
        "v1": enc_v1(clazz_id, uid, jobid, object_id, playing_time, duration),
    }


def clip_time(duration: int, start: int = 0) -> str:
    """正常播放固定 0_{duration}；拖拽态才是 {start}_{end}。"""
    return f"{start}_{int(duration)}"
