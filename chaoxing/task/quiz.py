"""章节测验执行器：取题 → 题库 / AI / 兜底 → 覆盖率判定 → 提交。"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from loguru import logger

from ..ai import AiError, AiSolver
from ..api import quiz as quiz_api
from ..api.quiz import ParsedWork, Question
from .base import BaseTask, TaskResult

_NOT_READY_MARKS = ("教师未创建完成该测验", "教师未创建", "该测验暂未开放")


def work_params(task: dict[str, Any], att: dict[str, Any] | None = None) -> dict[str, Any]:
    """拼 /mooc-ans/api/work 的取题参数（测验执行器与 cx quiz pull 共用）。"""
    att = att or {}
    jobid = task["job_id"]
    work_id = jobid.replace("work-", "").replace("workid-", "")
    return {
        "api": 1,
        "workId": work_id,
        "jobid": jobid,
        "originJobId": jobid,
        "needRedirect": "true",
        "skipHeader": "true",
        "knowledgeid": task.get("knowledge_id") or att.get("knowledgeid") or "",
        "ktoken": att.get("ktoken") or "",
        "cpi": att.get("cpi") or task.get("cpi") or "",
        "ut": "s",
        "clazzId": task["clazz_id"],
        "type": "",
        "enc": att.get("qnenc") or "",
        "mooc2": 1,
        "courseid": task["course_id"],
    }


class QuizTask(BaseTask):
    type = "work"

    async def run(self, task: dict[str, Any]) -> TaskResult:
        self.sess.task_pk = int(task["id"])
        params = self._build_params(task)
        code, body = await quiz_api.fetch_work(self.sess, params)
        if code != 200:
            return TaskResult.retry(f"取题 HTTP {code}")
        if any(m in body for m in _NOT_READY_MARKS):
            return TaskResult.done("教师未创建该测验，视为通过")

        parsed = quiz_api.parse_work(
            body,
            font_map=self.settings.data_dir / "fonts" / "map.json",
            unresolved_dir=self.settings.data_dir / "unresolved",
        )
        if not parsed.questions:
            return TaskResult.retry("未解析到题目")

        logger.info(
            f"[{self.sess.label}] 测验「{task.get('title', '')}」{len(parsed.questions)} 题"
            + ("（已套用字体映射）" if parsed.font_usable else "")
        )
        if parsed.font_unknown:
            logger.warning(f"字体映射缺 {parsed.font_unknown} 个字形，相关字符按原文处理")

        total = len(parsed.questions)
        answers, bank_hit = self._solve_bank(parsed.questions)      # ① 本地题库
        missing = [q for q in parsed.questions if not answers.get(q.id)]

        ai_hit = 0
        ai_answers: dict[str, str] = {}
        if missing:
            if not self.settings.ai.enabled:
                # 不猜：没配 AI 就如实报错，别拿随机答案糊弄
                logger.error(
                    f"[{self.sess.label}] AI 未启用（config.toml 的 [ai] enabled=false），"
                    f"题库只覆盖 {bank_hit}/{total} 题，剩余 {len(missing)} 题无法作答 → 本次跳过，不提交"
                )
                return TaskResult.skip(f"AI 未启用，题库仅覆盖 {bank_hit}/{total} 题")

            ai_hit, ai_answers, ai_err = await self._solve_ai(          # ② AI 作答
                missing, work=task.get("title") or ""
            )
            answers.update(ai_answers)
            if ai_err and not ai_hit:
                logger.error(
                    f"[{self.sess.label}] AI 无法连接或调用失败，本题不作答、不提交：{ai_err}"
                )
                if "401" in ai_err or "403" in ai_err:
                    # 鉴权错了重试没有意义，直接判死，等用户改 api_key
                    return TaskResult.fatal(f"AI 鉴权失败：{ai_err}")
                return TaskResult.retry(f"AI 调用失败：{ai_err}")
            if ai_hit < len(missing):
                logger.error(
                    f"[{self.sess.label}] AI 只答出 {ai_hit}/{len(missing)} 题，"
                    f"剩下 {len(missing) - ai_hit} 题留空（不猜）"
                )

        blank = [q for q in parsed.questions if not answers.get(q.id)]
        known = bank_hit + (ai_hit if self.settings.quiz.count_ai_as_known else 0)
        coverage = known / total if total else 0.0
        py_flag = "" if (not blank and coverage >= self.settings.quiz.submit_threshold) else "1"
        form = self._build_form(parsed, answers, py_flag)

        logger.info(
            f"[{self.sess.label}] 覆盖率 {coverage:.0%}"
            f"（题库 {bank_hit} / AI {ai_hit} / 空白 {len(blank)} / 共 {total}）"
            + ("→ 提交" if not py_flag else "→ 只存草稿")
        )

        if self.dry_run:
            logger.info(f"[dry-run] 提交测验 覆盖率 {coverage:.0%} pyFlag={py_flag or '提交'}")
            return TaskResult.done(f"dry-run 覆盖率 {coverage:.0%}")

        status, resp = await quiz_api.submit_work(self.sess, form)
        ok = status == 200 and _submit_ok(resp)
        if not ok:
            return TaskResult.retry(f"提交失败 HTTP {status} {str(resp)[:120]}")
        for q in parsed.questions:
            if q.qkey in self._known:
                self.repo.quiz_bump(q.qkey, ok=True)
        self._remember(parsed.questions, answers, ai_answers)
        if py_flag:
            return TaskResult.done(f"覆盖率 {coverage:.0%} 不足，已保存草稿")
        return TaskResult.done(f"已提交，覆盖率 {coverage:.0%}")

    # ---------------- 内部 ----------------
    _known: set[str]

    def _build_params(self, task: dict[str, Any]) -> dict[str, Any]:
        att = self.att(task)
        jobid = task["job_id"]
        work_id = jobid.replace("work-", "").replace("workid-", "")
        return {
            "api": 1,
            "workId": work_id,
            "jobid": jobid,
            "originJobId": jobid,
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": task.get("knowledge_id") or att.get("knowledgeid") or "",
            "ktoken": att.get("ktoken") or "",
            "cpi": att.get("cpi") or task.get("cpi") or "",
            "ut": "s",
            "clazzId": task["clazz_id"],
            "type": "",
            "enc": att.get("qnenc") or "",
            "mooc2": 1,
            "courseid": task["course_id"],
        }

    def _solve_bank(self, questions: list[Question]) -> tuple[dict[str, str], int]:
        """第一级：本地题库命中（qkey 对题干归一化后取 md5，跨测验复用）。"""
        answers: dict[str, str] = {}
        self._known = set()
        hit = 0
        for q in questions:
            row = self.repo.quiz_get(q.qkey)
            if row and row.get("answer"):
                answers[q.id] = row["answer"]
                self._known.add(q.qkey)
                hit += 1
        return answers, hit

    async def _solve_ai(
        self, questions: list[Question], *, work: str
    ) -> tuple[int, dict[str, str], str]:
        """第二级：把题库没覆盖的题丢给 AI，返回 (命中数, {题 id: 答案}, 错误文本)。"""
        if not questions:
            return 0, {}, ""
        solver = AiSolver(self.settings.ai)
        try:
            got = await solver.solve(questions, work=work)
        except AiError as exc:
            return 0, {}, str(exc)
        finally:
            await solver.close()
        clean = {}
        for q in questions:
            ans = got.get(q.id)
            if ans:
                clean[q.id] = ans
        return len(clean), clean, ""

    def _remember(
        self, questions: list[Question], answers: dict[str, str], ai_answers: dict[str, str]
    ) -> None:
        """把 AI 给出的答案落进题库（source=ai），下次同一题直接命中，不再花 token。"""
        for q in questions:
            if q.id not in ai_answers:
                continue
            answer = answers.get(q.id) or ai_answers[q.id]
            if not answer:
                continue
            try:
                self.repo.quiz_put(
                    q.qkey,
                    title=q.title,
                    options="\n".join(q.options),
                    qtype=q.qtype,
                    answer=answer,
                    source="ai",
                )
            except Exception as exc:  # pragma: no cover - 题库写失败不该影响任务结果
                logger.debug(f"题库写入失败：{exc}")

    @staticmethod
    def _build_form(parsed: ParsedWork, answers: dict[str, str], py_flag: str) -> dict[str, str]:
        form = dict(parsed.form)
        ids: list[str] = []
        for q in parsed.questions:
            form[f"answer{q.id}"] = answers.get(q.id, "")
            form[f"answertype{q.id}"] = q.type_code
            ids.append(q.id)
        form["pyFlag"] = py_flag
        form["answerwqbid"] = ",".join(ids) + ","
        return form


async def thinking_delay(lo: float, hi: float) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _submit_ok(resp: dict[str, Any] | None) -> bool:
    if not isinstance(resp, dict):
        return False
    if resp.get("status") is True:
        return True
    return resp.get("code") in (1, "1", 200)
