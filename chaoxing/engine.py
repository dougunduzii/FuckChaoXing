"""调度层：抓树（scan）与跑任务（run），含断点续跑与账号并发。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from loguru import logger

from . import auth
from .api import course as course_api
from .config import Settings
from .session import CxSession, RiskBlocked
from .store.repo import Repo
from .task import HANDLERS, TaskResult


@dataclass(slots=True)
class ScanReport:
    account: str = ""
    courses: int = 0
    chapters: int = 0
    tasks: int = 0
    skipped_locked: int = 0
    faces: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class RunReport:
    account: str = ""
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, settings: Settings, repo: Repo | None = None) -> None:
        self.settings = settings
        self.repo = repo or Repo(settings.db_path)

    # ---------------- 会话 ----------------
    def session_for(self, account: dict[str, Any]) -> CxSession:
        cookies = json.loads(account.get("cookies") or "{}")
        sess = CxSession(
            self.settings,
            cookies=cookies,
            repo=self.repo,
            account_id=int(account["id"]),
            label=str(account["name"]),
        )
        sess.uid = str(account.get("puid") or "")
        sess.fid = str(account.get("fid") or "")
        return sess

    # ---------------- 抓树 ----------------
    async def scan(self, name: str, *, include_folders: bool = True) -> ScanReport:
        self.repo.ensure_account(name)
        account = self.repo.get_account(name)
        assert account is not None
        report = ScanReport(account=name)

        async with self.session_for(account) as sess:
            msg = await auth.ensure_login(sess, name, self.repo)
            if msg is None:
                report.errors.append("会话失效，需要重新登录")
                return report
            account = self.repo.get_account(name)

            courses = await course_api.fetch_courses(sess, include_folders=include_folders)
            report.courses = len(courses)
            logger.info(f"[{name}] 课程 {len(courses)} 门")

            for c in courses:
                try:
                    await self._scan_course(sess, account, c, report)
                except Exception as exc:  # 单课失败不影响其它
                    logger.warning(f"[{name}] 课程「{c.get('title')}」抓取失败: {exc}")
                    report.errors.append(f"{c.get('title')}: {exc}")

        logger.info(
            f"[{name}] 抓取完成：{report.courses} 课 / {report.chapters} 章 / "
            f"{report.tasks} 任务点 / 跳过锁定 {report.skipped_locked}"
        )
        return report

    async def _scan_course(
        self, sess: CxSession, account: dict[str, Any], c: dict[str, Any], report: ScanReport
    ) -> None:
        course_pk = self.repo.upsert_course(
            int(account["id"]),
            course_id=c["course_id"],
            clazz_id=c["clazz_id"],
            cpi=c.get("cpi") or "",
            title=c.get("title") or "",
            teacher=c.get("teacher") or "",
        )
        chapters = await course_api.fetch_chapters(sess, c)
        report.chapters += len(chapters)

        for ch in chapters:
            kid = ch["knowledge_id"]
            # 章节标"已完成"也照样抓任务点：只有抓到才知道这一章到底有几个任务点，
            # 直接 continue 会让这些任务点整批不进库（总数少算），而且本地状态无从对齐。
            ch_pk = self.repo.upsert_chapter(
                course_pk,
                knowledge_id=kid,
                title=ch["title"],
                job_count=ch["job_count"],
                state="done" if ch["finished"] else "pending",
            )
            if ch["locked"] and not ch["finished"]:
                self.repo.set_chapter_state(ch_pk, "skip_locked")
                report.skipped_locked += 1
                continue

            # expect 传 0：目录里的 knowledgeJobCount 只是"待完成"计数，拿它当上限会
            # 在第一批就提前收摊，后面批次的文档/阅读点整批丢掉。
            jobs, info = await course_api.fetch_jobs(sess, c, kid)
            if info.get("notOpen"):
                self.repo.set_chapter_state(ch_pk, "skip_closed")
                report.skipped_locked += 1
                continue
            if not jobs:
                await course_api.touch_empty_chapter(sess, c, kid)
                if ch["job_count"]:
                    # 目录说有任务点却一个都没解析出来：多半是页面结构又变了，
                    # 不要静默当成空章节，否则会把 124 个任务点跑成 25 个空页打卡。
                    logger.warning(
                        f"[课程 {c.get('title')}] 章节「{ch['title']}」"
                        f"目录标称 {ch['job_count']} 个任务点，但 cards 页解析到 0 个"
                    )
                self.repo.upsert_task(
                    ch_pk,
                    {"job_id": f"empty-{kid}", "type": "emptypage", "title": ch["title"]},
                )
                report.tasks += 1
                continue

            # 上一轮解析失败时留下的空章节占位行要清掉
            self.repo.delete_task(f"empty-{kid}")

            for job in jobs:
                passed = bool(job.pop("passed", False))
                face = bool(job.pop("face", False))
                if not job.get("duration"):
                    job["duration"] = int((job.get("att") or {}).get("attDuration") or 0)
                pk = self.repo.upsert_task(ch_pk, job)
                if face:
                    report.faces += 1
                    logger.warning(
                        f"[{report.account}] 任务点「{job.get('title', '')}」带人脸凭证"
                        f"（videoFaceCaptureEnc），只能原样透传；服务端若要求实时活体，"
                        f"需用 cx face 人工过一次"
                    )
                if passed:
                    # 服务端已判过：本地直接对齐成 done，避免重扫后又跑一遍
                    self.repo.set_task_state(pk, "done")
                report.tasks += 1

    # ---------------- 跑任务 ----------------
    async def run(
        self,
        names: Iterable[str],
        *,
        course_pk: int | None = None,
        types: Iterable[str] | None = None,
        max_retries: int | None = None,
        dry_run: bool = False,
        concurrency: int = 3,
        task_concurrency: int = 1,
        limit: int | None = None,
        speed: float | None = None,
    ) -> dict[str, RunReport]:
        names = list(names)
        sem = asyncio.Semaphore(max(1, concurrency))
        results: dict[str, RunReport] = {}

        async def one(name: str) -> None:
            async with sem:
                try:
                    results[name] = await self._run_account(
                        name,
                        course_pk=course_pk,
                        types=types,
                        max_retries=max_retries,
                        dry_run=dry_run,
                        task_concurrency=task_concurrency,
                        limit=limit,
                        speed=speed,
                    )
                except RiskBlocked as exc:
                    # 风控是全账号级的：这条路停下就够了，不要再往上抛把别的账号带崩
                    results[name] = RunReport(account=name, errors=[str(exc)])

        await asyncio.gather(*(one(n) for n in names), return_exceptions=False)
        return results

    async def _run_account(
        self,
        name: str,
        *,
        course_pk: int | None,
        types: Iterable[str] | None,
        max_retries: int | None,
        dry_run: bool,
        task_concurrency: int = 1,
        limit: int | None = None,
        speed: float | None = None,
    ) -> RunReport:
        account = self.repo.get_account(name)
        if account is None:
            return RunReport(account=name, errors=["账号不存在，先执行 cx scan"])
        report = RunReport(account=name)
        retries = self.settings.http.retries if max_retries is None else max_retries
        started = time.monotonic()

        budget = self.settings.risk.daily_cap_hours * 3600
        spent = self._usage_seconds(int(account["id"]))

        async with self.session_for(account) as sess:
            msg = await auth.ensure_login(sess, name, self.repo)
            if msg is None:
                report.errors.append("会话失效，需要重新登录")
                return report

            selected = [t for t in types] if types else list(self.settings.types)
            tasks = self.repo.iter_tasks(
                account_id=int(account["id"]), course_pk=course_pk, types=selected
            )
            if limit:
                tasks = tasks[: max(0, limit)]
            report.total = len(tasks)
            logger.info(f"[{name}] 待处理任务点 {len(tasks)} 个，今日已用 {spent / 60:.0f} 分钟")

            counter = {"idx": 0}
            counter_lock = asyncio.Lock()
            stop = asyncio.Event()
            # 并发上限：超星看的是"同时有多少个不同任务点在动"，不是纯请求速率。
            # 实测本账号 70 秒内出现 68 个不同 objectId 就被 9010 拦下，所以这里
            # 默认按 risk.max_lanes 夹住，需要更大先自己跑 scripts/rate_probe.py 摸顶。
            hard_cap = max(1, int(getattr(self.settings.risk, "max_lanes", 8)))
            if task_concurrency > hard_cap:
                logger.warning(
                    f"[{name}] --task-concurrency {task_concurrency} 超过 max_lanes={hard_cap}，"
                    f"按 {hard_cap} 路跑（要改上限见 config.toml 的 [risk].max_lanes）"
                )
                task_concurrency = hard_cap
            sem = asyncio.Semaphore(max(1, task_concurrency))

            async def one_task(task: dict[str, Any], lane: int) -> None:
                async with sem:
                    if stop.is_set():
                        return
                    if budget and spent + (time.monotonic() - started) >= budget:
                        if not stop.is_set():
                            logger.warning(
                                f"[{name}] 触达每日时长上限 {self.settings.risk.daily_cap_hours}h，停止"
                            )
                            report.errors.append("触达每日时长上限")
                        stop.set()
                        return
                    try:
                        await self._run_task(
                            name, sess, task, retries=retries, dry_run=dry_run,
                            report=report, started=started, counter=counter,
                            counter_lock=counter_lock, total=len(tasks),
                            lane_index=lane, lane_total=task_concurrency,
                            speed=speed,
                        )
                    except RiskBlocked as exc:
                        if not stop.is_set():
                            logger.error(f"[{name}] {exc}")
                            report.errors.append(str(exc))
                        stop.set()
                        raise

            await asyncio.gather(
                *(one_task(t, i) for i, t in enumerate(tasks)), return_exceptions=False
            )

        report.seconds = time.monotonic() - started
        self._add_usage(int(account["id"]), int(report.seconds))
        logger.info(
            f"[{name}] 结束：完成 {report.done} / 跳过 {report.skipped} / "
            f"失败 {report.failed}，用时 {report.seconds / 60:.1f} 分钟" + ("（演练，未提交任何请求）" if dry_run else "")
        )
        return report

    # ---------------- 内部 ----------------
    async def _run_task(
        self,
        name: str,
        sess: Any,
        task: dict[str, Any],
        *,
        retries: int,
        dry_run: bool,
        report: RunReport,
        started: float,
        counter: dict[str, int],
        counter_lock: asyncio.Lock,
        total: int,
        lane_index: int = 0,
        lane_total: int = 1,
        speed: float | None = None,
    ) -> None:
        handler_cls = HANDLERS.get(task["type"])
        if handler_cls is None:
            self.repo.set_task_state(int(task["id"]), "skip", err="无处理器")
            async with counter_lock:
                report.skipped += 1
            return

        if not dry_run:
            self.repo.set_task_state(int(task["id"]), "running")
        handler = handler_cls(
            sess, self.repo, self.settings, dry_run=dry_run,
            lane_index=lane_index, lane_total=lane_total, speed=speed,
        )
        result = TaskResult.retry("未执行")

        for attempt in range(retries + 1):
            try:
                result = await handler.run(task)
            except Exception as exc:
                logger.exception(f"[{name}] 任务异常 {task['job_id']}: {exc}")
                result = TaskResult.retry(f"异常: {type(exc).__name__}: {exc}")

            if result.ok or not result.retryable:
                break
            if attempt < retries:
                wait = min(300, 30 * (attempt + 1))
                logger.info(f"[{name}] {task['title']} 失败({result.detail})，{wait}s 后重试")
                await asyncio.sleep(wait)
                if not dry_run:
                    self.repo.set_task_state(int(task["id"]), "running", inc_attempt=True)

        async with counter_lock:
            if result.ok:
                if dry_run:
                    # 演练：不动库里的状态，否则 DB 会显示全部完成而服务端毫无变化
                    self.repo.set_task_state(int(task["id"]), "pending")
                    report.done += 1
                elif result.skipped:
                    self.repo.set_task_state(int(task["id"]), "skip")
                    report.skipped += 1
                else:
                    self.repo.set_task_state(int(task["id"]), "done")
                    report.done += 1
            else:
                if dry_run:
                    self.repo.set_task_state(int(task["id"]), "pending", err=result.detail)
                else:
                    self.repo.set_task_state(
                        int(task["id"]), "failed", err=result.detail, inc_attempt=True
                    )
                report.failed += 1
                report.errors.append(f"{task['title']}: {result.detail}")
            counter["idx"] += 1
            idx = counter["idx"]

        if result.ok and not dry_run and not result.skipped:
            self._maybe_finish_chapter(int(task["chapter_pk"]))

        elapsed = time.monotonic() - started
        logger.info(
            f"[{name}] ({idx}/{total}) {task['type']} {task['title']} -> "
            f"{'OK' if result.ok else 'FAIL'} {result.detail} [{elapsed / 60:.1f}min]"
        )

    def _maybe_finish_chapter(self, chapter_pk: int) -> None:
        if self.repo.chapter_pending_count(chapter_pk) == 0:
            self.repo.set_chapter_state(chapter_pk, "done")

    def _usage_key(self, account_id: int) -> str:
        return f"usage:{date.today().isoformat()}:{account_id}"

    def _usage_seconds(self, account_id: int) -> int:
        raw = self.repo.meta_get(self._usage_key(account_id), "0")
        try:
            return int(raw or 0)
        except ValueError:
            return 0

    def _add_usage(self, account_id: int, seconds: int) -> None:
        total = self._usage_seconds(account_id) + max(0, seconds)
        self.repo.meta_set(self._usage_key(account_id), str(total))
