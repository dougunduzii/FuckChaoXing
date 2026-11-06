"""面板任务中枢：单任务执行 + 日志环形缓冲 + SSE 广播。

设计约束（直接决定面板能不能用）：
- 同一时刻只允许一个任务在跑。超星的风控看的是并发任务点，Web 端点两次扫描
  等于把风险放大一倍，所以在 Hub 层就用 409 挡掉，而不是靠前端按钮置灰。
- 日志走 loguru 的 sink，与 CLI 同一条管线；面板只是多一个消费者，不是另造一套。
- 每条日志带自增 seq，前端断线重连后带 ``after`` 续传，不会既重复又丢行。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

JOB_HISTORY = 30


@dataclass(slots=True)
class Job:
    """一次面板任务。字段与前端契约一一对应，改名要同步 app.js。"""

    id: int
    kind: str  # scan / run / reset / enc / login / browser-login
    label: str
    status: str = "queued"  # queued / running / done / failed / cancelled
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    progress: dict[str, Any] = field(default_factory=lambda: {"done": 0, "total": 0, "message": ""})
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": dict(self.progress),
            "params": dict(self.params),
            "result": self.result,
            "error": self.error,
        }


class Hub:
    """任务与日志的唯一持有者；App 层只读它、不缓存自己的副本。"""

    def __init__(self, *, log_limit: int = 800) -> None:
        self.log_limit = log_limit
        self.logs: deque[dict[str, Any]] = deque(maxlen=log_limit)
        self.job: Job | None = None
        self.history: deque[Job] = deque(maxlen=JOB_HISTORY)
        self._seq = 0
        self._job_seq = 0
        self._task: asyncio.Task[Any] | None = None
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._sink_id: int | None = None

    # ---------------- 日志 ----------------
    def attach_logs(self) -> None:
        """把面板接进 loguru 管线（幂等）。"""
        if self._sink_id is not None:
            return
        self._sink_id = logger.add(
            self._sink,
            level="DEBUG",
            format="{message}",
            catch=True,
            backtrace=False,
            diagnose=False,
        )

    def detach_logs(self) -> None:
        if self._sink_id is None:
            return
        try:
            logger.remove(self._sink_id)
        except ValueError:  # 已被别处移除
            pass
        self._sink_id = None

    def _sink(self, message: Any) -> None:
        record = message.record
        self.log(
            record["message"],
            level=record["level"].name,
            ts=record["time"].timestamp(),
        )

    def log(self, text: str, *, level: str = "INFO", ts: float | None = None) -> dict[str, Any]:
        self._seq += 1
        item = {"seq": self._seq, "ts": ts or time.time(), "level": level, "text": text}
        self.logs.append(item)
        self.publish("log", item)
        return item

    def logs_after(self, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        items = [i for i in self.logs if i["seq"] > after]
        if limit > 0:
            items = items[-limit:]
        return items

    @property
    def last_seq(self) -> int:
        return self._seq

    # ---------------- 订阅（SSE） ----------------
    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(queue)

    def publish(self, event: str, data: Any) -> None:
        payload = {"type": event, "data": data}
        for queue in list(self._subs):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # 慢客户端：扔最旧的一条，保证最新状态（任务进度）能进去
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    # ---------------- 任务 ----------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def busy_error(self) -> str | None:
        if not self.running:
            return None
        job = self.job
        return f"已有任务在跑：{job.label}" if job else "已有任务在跑"

    def start_job(
        self,
        kind: str,
        label: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        params: dict[str, Any] | None = None,
    ) -> Job:
        """登记并启动一个任务；调用方负责先判 ``busy_error()``。"""
        self._job_seq += 1
        job = Job(
            id=self._job_seq,
            kind=kind,
            label=label,
            created_at=time.time(),
            params=dict(params or {}),
        )
        self.job = job
        self.publish("job", job.to_dict())
        self._task = asyncio.create_task(self._runner(job, factory), name=f"cx-web-{kind}-{job.id}")
        return job

    async def _runner(self, job: Job, factory: Callable[[], Awaitable[Any]]) -> None:
        job.status = "running"
        job.started_at = time.time()
        self.publish("job", job.to_dict())
        try:
            job.result = await factory()
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error = "已手动停止"
        except Exception as exc:
            logger.exception(f"面板任务失败：{job.label}")
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()
            job.progress["message"] = job.progress.get("message") or ""
            self.history.appendleft(job)
            self.publish("job", job.to_dict())
            self._task = None

    def stop(self) -> bool:
        task = self._task
        if task is None or task.done():
            return False
        logger.warning("面板请求停止当前任务")
        task.cancel()
        return True

    def jobs_list(self, limit: int = 20) -> list[dict[str, Any]]:
        """当前任务 + 历史任务（新的在前，按 id 去重）。"""
        seen: set[int] = set()
        out: list[dict[str, Any]] = []
        for job in ([self.job] if self.job is not None else []) + list(self.history):
            if job is None or job.id in seen:
                continue
            seen.add(job.id)
            out.append(job.to_dict())
        return out[: max(1, limit)]

    def on_engine_event(self, kind: str, payload: dict[str, Any]) -> None:
        """Engine 的进度回调：把事件翻译成 job.progress。"""
        job = self.job
        if job is None or job.kind != kind:
            return
        prog = job.progress
        stage = str(payload.get("stage") or "")
        total = payload.get("total")
        if total is not None:
            try:
                prog["total"] = int(total)
            except (TypeError, ValueError):
                pass
        if kind == "scan":
            title = payload.get("title") or ""
            if stage in {"course", "course_error"}:
                idx = int(payload.get("index") or 0)
                prog["done"] = idx
                prog["message"] = f"抓取课程 {idx + 1}/{payload.get('total') or '?'} {title}".strip()
            elif stage == "course_done":
                prog["done"] = int(payload.get("index") or 0)
                prog["message"] = (
                    f"{prog['done']}/{payload.get('total') or '?'} 门课程完成，"
                    f"累计任务点 {payload.get('tasks', 0)}"
                )
            elif stage == "courses":
                prog["done"] = 0
                prog["message"] = f"发现 {payload.get('total')} 门课程"
            elif stage == "login":
                prog["message"] = "校验登录态…"
            elif stage == "error":
                prog["message"] = str(payload.get("error") or "抓取失败")
        elif kind == "run":
            title = payload.get("title") or ""
            if stage == "start":
                prog["done"] = 0
                prog["message"] = f"待处理任务点 {prog['total']} 个"
            elif stage == "task":
                try:
                    prog["done"] = int(payload.get("index") or 0)
                except (TypeError, ValueError):
                    pass
                ok = "OK" if payload.get("ok") else "FAIL"
                prog["message"] = f"{prog['done']}/{prog['total']} {ok} {title}".strip()
            elif stage == "end":
                prog["done"] = prog["total"]
                prog["message"] = (
                    f"完成 {payload.get('done', 0)} / 跳过 {payload.get('skipped', 0)} / "
                    f"失败 {payload.get('failed', 0)}"
                )
        self.publish("job", job.to_dict())

    async def shutdown(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # 收尾阶段不看结果
                pass
        self.detach_logs()
