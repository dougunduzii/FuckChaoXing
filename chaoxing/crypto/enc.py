"""视频进度上报的 enc 计算（多版本 + 运行时探测结果）。"""

from __future__ import annotations

import hashlib
from typing import Callable

# 2025 现行版盐值，来自播放页 getEnc 的常量
SALT = "d_yHJ!$pdA~5"


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def enc_v2(
    clazz_id: str, uid: str, jobid: str, object_id: str, playing_time: int, duration: int
) -> str:
    """现行版：[clazzId][uid][jobid][objectId][pt*1000][salt][dur*1000][0_dur]"""
    return md5(
        f"[{clazz_id}][{uid}][{jobid}][{object_id}]"
        f"[{int(playing_time) * 1000}][{SALT}]"
        f"[{int(duration) * 1000}][0_{int(duration)}]"
    )


def enc_v1(
    clazz_id: str, uid: str, jobid: str, object_id: str, playing_time: int, duration: int
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
) -> str:
    fn = VERSIONS.get(version) or VERSIONS[DEFAULT_VERSION]
    return fn(clazz_id, uid, jobid, object_id, playing_time, duration)


def candidate_encs(
    clazz_id: str, uid: str, jobid: str, object_id: str, playing_time: int, duration: int
) -> dict[str, str]:
    """给探针脚本对拍用：一次算出所有版本。"""
    return {
        name: fn(clazz_id, uid, jobid, object_id, playing_time, duration)
        for name, fn in VERSIONS.items()
    }


def clip_time(duration: int, start: int = 0) -> str:
    """正常播放固定 0_{duration}；拖拽态才是 {start}_{end}。"""
    return f"{start}_{int(duration)}"
