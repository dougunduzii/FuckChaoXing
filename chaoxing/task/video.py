"""视频 / 音频任务点执行器。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from loguru import logger

from ..api import video as video_api
from .base import BaseTask, TaskResult


def _face_blocked(body: dict[str, Any] | None) -> str:
    """上报响应里出现人脸/活体要求时返回命中的字段名。"""
    if not isinstance(body, dict):
        return ""
    for key, value in body.items():
        if "face" in str(key).lower() and value:
            return str(key)
    msg = str(body.get("msg") or body.get("message") or "")
    if "人脸" in msg or "face" in msg.lower():
        return "msg"
    return ""


class VideoTask(BaseTask):
    type = "video"

    async def run(self, task: dict[str, Any]) -> TaskResult:
        object_id = task.get("object_id") or ""
        if not object_id:
            return TaskResult.fatal("缺少 objectId")
        self.sess.task_pk = int(task["id"])
        self.lane_key = f"video:{task.get('job_id') or task['id']}"
        if self.att(task).get("videoFaceCaptureEnc"):
            logger.info(
                f"[{self.sess.label}] 「{task.get('title', '')}」带人脸凭证，"
                f"按卡片原样透传 videoFaceCaptureEnc"
            )

        status = await video_api.fetch_status(self.sess, object_id, self.sess.fid)
        if not status:
            # status 端点对文档类 objectId 返回的是 download/pdf 载荷，不是视频元数据。
            # 分不清就只能报"dtoken/duration 缺失"，这里先点明是分类问题。
            raw = await video_api.fetch_status_raw(self.sess, object_id, self.sess.fid)
            if raw and any(k in raw for k in ("pdf", "download", "filename")):
                return TaskResult.fatal(
                    f"objectId {object_id} 其实是文档（返回 {','.join(k for k in ('pdf','download') if k in raw)} 载荷），"
                    "请重新 cx scan 让类型判定归位"
                )
            return TaskResult.retry("ananas/status 未返回 success")
        dtoken = str(status.get("dtoken") or "")
        duration = int(status.get("duration") or 0)
        if not dtoken or duration <= 0:
            return TaskResult.fatal("dtoken/duration 缺失")

        start = int(task.get("play_time") or 0)
        self._progress(int(task["id"]), min(start, duration), duration)

        res = await self._drive(task, dtoken, duration, start, "Video")
        if res.ok:
            return res
        if self.settings.video.audio_fallback and res.retryable:
            logger.info(f"[{self.sess.label}] 视频通道失败，改用 Audio 重试: {res.detail}")
            res2 = await self._drive(task, dtoken, duration, min(start, duration), "Audio")
            if res2.ok:
                return res2
        return res

    # ---------------- 内部 ----------------
    def _progress(self, task_pk: int, pt: int, duration: int) -> None:
        """dry-run 只演练，不允许把本地进度写进库（否则库里看着全完成、服务端一动不动）。"""
        if self.dry_run:
            return
        self.repo.set_task_progress(task_pk, pt, duration)

    def _gap(self, interval: int) -> float:
        j = max(0.0, self.settings.video.jitter)
        return max(10.0, interval * (1 + random.uniform(-j, j)))

    async def _report(
        self,
        task: dict[str, Any],
        dtoken: str,
        pt: int,
        duration: int,
        dtype: str,
        version: str,
    ) -> tuple[bool, int, dict[str, Any] | None]:
        att = self.att(task)
        params = video_api.build_params(
            clazz_id=task["clazz_id"],
            course_id=task["course_id"],
            jobid=task["job_id"],
            object_id=task["object_id"],
            other_info=task.get("other_info") or "",
            userid=self.uid,
            playing_time=pt,
            duration=duration,
            dtype=dtype,
            version=version,
            att=att,
            rt=str(att["rt"]) if att.get("rt") else None,
        )
        if self.dry_run:
            logger.info(f"[dry-run] {dtype} 上报 {pt}/{duration}s enc={params['enc'][:8]}…")
            return True, 200, {"isPassed": True}
        return await video_api.report(
            self.sess,
            cpi=task.get("cpi") or "",
            dtoken=dtoken,
            params=params,
            dtype=dtype,
            lane=self.lane_key,
            audit_task=int(task["id"]),
        )

    async def _drive(
        self,
        task: dict[str, Any],
        dtoken: str,
        duration: int,
        start: int,
        dtype: str,
    ) -> TaskResult:
        version = self.repo.meta_get("enc_version", "v2") or "v2"
        interval = int(self.att(task).get("report_time_interval") or self.settings.video.interval)
        speed = max(0.5, float(self.speed or self.settings.video.speed))
        task_pk = int(task["id"])

        pt = max(0, min(start, duration))

        # 错峰：N 路并发时把心跳均匀铺开，避免所有通道在同一秒上报
        offset = self.stagger(interval)
        if offset > 0:
            logger.debug(f"[{self.sess.label}] {self.lane_key} 错峰 {offset:.0f}s")
            await asyncio.sleep(offset)

        # 1) 秒过探测：先按当前进度，再直接顶到 duration
        probes = [pt]
        if self.settings.video.instant_skip and pt < duration:
            probes.append(duration)
        for probe in probes:
            passed, code, body = await self._report(task, dtoken, probe, duration, dtype, version)
            if passed:
                self._progress(task_pk, duration, duration)
                return TaskResult.done(f"{dtype} 秒过 {probe}s")
            face = _face_blocked(body)
            if face:
                return TaskResult.fatal(
                    f"{dtype} 服务端要求人脸识别/活体（响应字段 {face}），"
                    f"用 `cx face {self.sess.label}` 在浏览器里过一次"
                )
            if code in (401, 403):
                return TaskResult.retry(f"{dtype} 首个探测被拒 {code}")

        # 2) 正常推进
        last_pt = pt
        last_ts = time.monotonic()
        gap = self._gap(interval)
        rounds = 0
        rejects = 0

        while True:
            rounds += 1
            if rounds > self.settings.video.max_rounds:
                return TaskResult.fatal(f"{dtype} 上报轮次超限({rounds})")

            now = time.monotonic()
            remain = gap - (now - last_ts)
            if remain > 0:
                await asyncio.sleep(min(2.0, remain))
                continue

            advanced = int(last_pt + (now - last_ts) * speed)
            pt = min(duration, max(advanced, last_pt + 1))
            passed, code, body = await self._report(task, dtoken, pt, duration, dtype, version)

            if passed:
                self._progress(task_pk, duration, duration)
                return TaskResult.done(f"{dtype} 完成 {duration}s / {rounds}轮")

            face = _face_blocked(body)
            if face:
                return TaskResult.fatal(
                    f"{dtype} 服务端要求人脸识别/活体（响应字段 {face}），"
                    f"用 `cx face {self.sess.label}` 在浏览器里过一次"
                )

            if code in (401, 403):
                rejects += 1
                if rejects > 3:
                    return TaskResult.retry(f"{dtype} 连续被拒 {code}")
                logger.warning(f"[{self.sess.label}] {code} 刷新 dtoken 后重试")
                await asyncio.sleep(min(60, 15 * rejects))
                status = await video_api.fetch_status(self.sess, task["object_id"], self.sess.fid)
                if not status:
                    return TaskResult.retry("刷新 dtoken 失败")
                dtoken = str(status.get("dtoken") or dtoken)
                duration = int(status.get("duration") or duration)
                last_ts = time.monotonic()
                gap = self._gap(interval)
                continue

            if code != 200:
                return TaskResult.retry(f"{dtype} HTTP {code} pt={pt}")

            rejects = 0
            self._progress(task_pk, pt, duration)
            logger.debug(f"[{self.sess.label}] {dtype} {pt}/{duration}s")

            if pt >= duration:
                # 到达末尾仍未 isPassed，多半是间隔太短/倍速过高，放缓再补两轮
                for _ in range(2):
                    await asyncio.sleep(self._gap(min(interval, 30)))
                    passed, code, _ = await self._report(task, dtoken, duration, duration, dtype, version)
                    if passed:
                        self._progress(task_pk, duration, duration)
                        return TaskResult.done(f"{dtype} 末轮补报通过")
                return TaskResult.retry(f"{dtype} 播完未判过 duration={duration}")

            last_pt = pt
            last_ts = time.monotonic()
            gap = self._gap(interval)
