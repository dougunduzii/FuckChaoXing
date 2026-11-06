"""任务执行器基类。"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from typing import Any, ClassVar

from ..config import Settings
from ..session import CxSession
from ..store.repo import Repo


@dataclass(slots=True)
class TaskResult:
    ok: bool
    detail: str = ""
    retryable: bool = True
    skipped: bool = False

    @classmethod
    def done(cls, detail: str = "") -> "TaskResult":
        return cls(True, detail, False, False)

    @classmethod
    def retry(cls, detail: str = "") -> "TaskResult":
        return cls(False, detail, True, False)

    @classmethod
    def fatal(cls, detail: str = "") -> "TaskResult":
        return cls(False, detail, False, False)

    @classmethod
    def skip(cls, detail: str = "") -> "TaskResult":
        return cls(True, detail, False, True)


class BaseTask:
    type: ClassVar[str] = ""

    def __init__(
        self,
        sess: CxSession,
        repo: Repo,
        settings: Settings,
        *,
        dry_run: bool = False,
        lane_index: int = 0,
        lane_total: int = 1,
        speed: float | None = None,
    ) -> None:
        self.sess = sess
        self.repo = repo
        self.settings = settings
        self.dry_run = dry_run
        # 并发通道信息：lane_index/lane_total 用来把各路的上报时刻错开
        self.lane_index = lane_index
        self.lane_total = max(1, lane_total)
        # 本次运行的倍速覆盖（None 表示用 config.toml 的 [video] speed）
        self.speed = speed
        self.lane_key = ""

    def stagger(self, interval: float) -> float:
        """本通道应等待的错峰秒数：N 路并发时把心跳均匀铺在一个 interval 内。"""
        if self.lane_total <= 1:
            return 0.0
        # 用 job 的稳定散列做偏移，避免每轮都从头排，也不会所有路都从 0 开始
        seed = (zlib.crc32(self.lane_key.encode("utf-8")) % 1000) / 1000
        base = (self.lane_index % self.lane_total) / self.lane_total
        return (base + 0.5 * seed / self.lane_total) * interval

    async def run(self, task: dict[str, Any]) -> TaskResult:  # pragma: no cover
        raise NotImplementedError

    # ---------------- 辅助 ----------------
    @staticmethod
    def att(task: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(task.get("att_json") or "{}")
        except json.JSONDecodeError:
            return {}

    @property
    def uid(self) -> str:
        return getattr(self.sess, "uid", "") or ""

    def _title(self, task: dict[str, Any]) -> str:
        return f"{task.get('course_title', '')} / {task.get('chapter_title', '')} / {task.get('title', '')}"
