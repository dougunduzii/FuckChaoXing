"""文档 / 阅读 / 空页任务点。"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..api import course as course_api
from ..api import document as doc_api
from .base import BaseTask, TaskResult


class DocumentTask(BaseTask):
    type = "document"

    async def run(self, task: dict[str, Any]) -> TaskResult:
        self.sess.task_pk = int(task["id"])
        kind = task.get("type") or "document"
        jobid = task["job_id"]
        knowledge_id = (
            task.get("knowledge_id")
            or doc_api.knowledge_from_other_info(task.get("other_info") or "", task.get("knowledgeid") or "")
        )
        course = {
            "course_id": task["course_id"],
            "clazz_id": task["clazz_id"],
            "cpi": task.get("cpi") or "",
        }

        if self.dry_run:
            logger.info(f"[dry-run] {kind} 打卡 jobid={jobid}")
            return TaskResult.done("dry-run")

        if kind == "emptypage":
            ok = await course_api.touch_empty_chapter(self.sess, course, knowledge_id)
            return TaskResult.done("空章节已访问") if ok else TaskResult.retry("空章节访问失败")

        if kind == "read":
            ok, msg = await doc_api.report_read(
                self.sess,
                jobid=jobid,
                knowledge_id=knowledge_id,
                course_id=course["course_id"],
                clazz_id=course["clazz_id"],
                jtoken=task.get("jtoken") or "",
            )
            return TaskResult.done(f"阅读已打卡（{msg}）") if ok else TaskResult.retry(f"阅读打卡失败：{msg}")

        ok, msg = await doc_api.report_document(
            self.sess,
            jobid=jobid,
            knowledge_id=knowledge_id,
            course_id=course["course_id"],
            clazz_id=course["clazz_id"],
            jtoken=task.get("jtoken") or "",
        )
        return TaskResult.done(f"文档已打卡（{msg}）") if ok else TaskResult.retry(f"文档打卡失败：{msg}")
