"""端到端离线联调：MockTransport 冒充超星，跑通 scan → run 全链路。"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from chaoxing.api.quiz import parse_work
from chaoxing.config import HttpCfg, QuizCfg, RiskCfg, Settings, VideoCfg
from chaoxing.engine import Engine
from chaoxing.session import CxSession
from chaoxing.store.repo import Repo
from chaoxing.task import video as video_mod
from chaoxing.task.video import VideoTask

FIX = Path(__file__).parent / "fixtures"
COURSES_HTML = (FIX / "courses.html").read_text(encoding="utf-8")
CHAPTERS_HTML = (FIX / "chapters.html").read_text(encoding="utf-8")
CARDS_HTML = (FIX / "cards.html").read_text(encoding="utf-8")
WORK_HTML = (FIX / "work.html").read_text(encoding="utf-8")


class Net:
    """极简假服务端：按 URL 片段分派。"""

    def __init__(self) -> None:
        self.reports: list[dict[str, str]] = []
        self.submits: list[dict[str, list[str]]] = []
        self.calls: list[str] = []
        self.pass_at: int = 0          # 视频：playingTime ≥ 此值才算通过
        self.duration: int = 600
        self.work_ready: bool = True
        self.face_required: bool = False   # 上报时返回 faceCapture，模拟人脸识别拦截

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)

        if "userLogin4Uname.do" in url:
            return httpx.Response(200, json={"result": 1, "msg": {"puid": "88", "fid": "4444", "name": "测试"}})
        if "courselistdata" in url:
            return httpx.Response(200, text=COURSES_HTML)
        if "studentcourse" in url:
            return httpx.Response(200, text=CHAPTERS_HTML)
        if "knowledge/cards" in url:
            return httpx.Response(200, text=CARDS_HTML)
        if "/ananas/status/" in url:
            return httpx.Response(
                200, json={"status": "success", "dtoken": "dt-1", "duration": self.duration}
            )
        if "multimedia/log/a/" in url:
            qs = parse_qs(request.url.query.decode())
            flat = {k: v[0] for k, v in qs.items()}
            self.reports.append(flat)
            pt = int(flat.get("playingTime") or 0)
            if self.face_required:
                return httpx.Response(200, json={"isPassed": False, "faceCapture": True})
            return httpx.Response(200, json={"isPassed": pt >= self.pass_at})
        if "ananas/job/document" in url or "ananas/job/readv2" in url:
            return httpx.Response(200, json={"status": True})
        if "studentstudyAjax" in url:
            return httpx.Response(200, text="ok")
        if "addStudentWorkNew" in url:
            qs = parse_qs(request.content.decode(), keep_blank_values=True)
            self.submits.append({k: v for k, v in qs.items()})
            return httpx.Response(200, json={"status": True, "code": 1})
        if "mooc-ans/api/work" in url:
            if not self.work_ready:
                return httpx.Response(200, text="<html>教师未创建完成该测验</html>")
            return httpx.Response(200, text=WORK_HTML)
        return httpx.Response(404, text=f"unmocked: {url}")


@pytest.fixture()
def env(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "cx.db",
        log_path=tmp_path / "data" / "cx.log",
        http=HttpCfg(timeout=5, retries=0, backoff=0, min_gap=0, jitter=0, global_rate=0, audit=False),
        video=VideoCfg(speed=5.0, interval=1, jitter=0.0, instant_skip=True),
        quiz=QuizCfg(submit_threshold=0.8),
        risk=RiskCfg(daily_cap_hours=24),
    )
    settings.ensure_dirs()
    repo = Repo(settings.db_path)
    repo.ensure_account("me")
    repo.save_account("me", {"_uid": "88", "_d": "x"}, puid="88", fid="4444", status="ok")

    net = Net()
    engine = Engine(settings, repo)

    def session_for(account):
        sess = CxSession(
            settings, cookies={"_uid": "88", "_d": "x"}, repo=repo,
            account_id=int(account["id"]), label=str(account["name"]),
        )
        sess.uid = "88"
        sess.fid = "4444"
        sess._client = httpx.AsyncClient(
            transport=httpx.MockTransport(net), follow_redirects=True, timeout=5
        )
        return sess

    engine.session_for = session_for  # type: ignore[method-assign]
    yield engine, repo, net, settings
    repo.close()


async def test_scan_builds_tree(env):
    engine, repo, net, _ = env
    report = await engine.scan("me")

    assert report.errors == []
    assert report.courses == 2
    # 两门课共用同一份章节 fixture：2×(3 个一级知识点 + 1 个第三级) = 8 章
    assert report.chapters == 8
    assert report.skipped_locked == 2
    # 每章 4 个 attachments，job_id 全局唯一，两门课各章合并后剩 4 条真实任务点。
    # 标"已完成"的章节现在也会抓（不然总数漏算），所以比只抓未完成章节时更多。
    assert report.tasks == 30  # 含服务端已判过的任务点
    assert report.faces == 6   # 卡片带 videoFaceCaptureEnc 的会被标出来
    done = repo.iter_tasks(states=("done",))
    assert done and all(t["state"] == "done" for t in done), "服务端已过的任务点应直接对齐为 done"

    tasks = repo.iter_tasks()
    assert len(tasks) == 4
    assert {t["type"] for t in tasks} == {"video", "document", "work", "read"}
    video = next(t for t in tasks if t["type"] == "video")
    assert video["play_time"] == 120
    assert json.loads(video["att_json"])["rt"] == "0.9"

    states: dict[str, set[str]] = {}
    for row in repo._q("SELECT knowledge_id, state FROM chapter"):
        states.setdefault(row["knowledge_id"], set()).add(row["state"])
    assert states["1001"] == {"done"}
    assert states["1002"] == {"pending"}
    assert states["1003"] == {"skip_locked"}


async def test_scan_then_run_dry(env):
    engine, repo, net, _ = env
    await engine.scan("me")
    results = await engine.run(["me"], dry_run=True)
    r = results["me"]
    assert r.total == 4
    assert r.done == 4, r.errors
    assert r.failed == 0
    assert net.reports == []  # dry-run 不发上报

    states = {t["type"]: t["state"] for t in repo.iter_tasks(states=("pending",))}
    assert set(states) == {"video", "document", "work", "read"}, "dry-run 不应把任务落成 done"
    # 进度也不能被演练推进（120s 是服务端原值，演练后应保持不变）
    prog = {t["type"]: t["play_time"] for t in repo.iter_tasks(states=("pending",))}
    assert prog["video"] == 120, prog


async def test_run_reports_video_progress(env):
    engine, repo, net, _ = env
    net.pass_at = 600
    await engine.scan("me")
    results = await engine.run(["me"], types=["video"])
    r = results["me"]
    assert r.done == 1, r.errors
    assert net.reports, "应该有上报"
    first = net.reports[0]
    # job_id 唯一约束让这条任务最后归属第二门课
    assert first["clazzId"] == "5678"
    assert first["userid"] == "88"
    assert first["dtype"] == "Video"
    assert first["clipTime"] == "0_600"
    assert first["isdrag"] == "3"
    assert len(net.reports) >= 2


async def test_quiz_submits_when_coverage_high(env):
    engine, repo, net, _ = env
    parsed = parse_work(WORK_HTML)
    for q in parsed.questions:
        repo.quiz_put(q.qkey, title=q.title, options="", qtype=q.qtype, answer="A", source="seed")

    await engine.scan("me")
    results = await engine.run(["me"], types=["work"])
    assert results["me"].done == 1, results["me"].errors
    assert len(net.submits) == 1

    form = net.submits[0]
    assert form["pyFlag"] == [""]
    assert form["answer7001"] == ["A"]
    assert form["answertype7002"] == ["1"]
    assert form["answerwqbid"][0].startswith("7001,")


async def test_quiz_skips_without_guessing_when_no_ai(env):
    """题库没命中、AI 又没启用时：不猜、不提交，直接跳过（会打 ERROR 日志）。"""
    engine, repo, net, _ = env
    await engine.scan("me")
    results = await engine.run(["me"], types=["work"])
    rep = results["me"]
    assert rep.skipped == 1
    assert rep.done == 0
    assert net.submits == []


async def test_quiz_retries_when_ai_unreachable(env, monkeypatch):
    """AI 配了但连不上：返回 retry 并把错误写进日志/结果，仍不提交。"""
    from chaoxing.ai import AiError
    from chaoxing.task import quiz as quiz_task

    engine, repo, net, _ = env
    engine.settings.ai.enabled = True
    engine.settings.ai.api_key = "sk-x"

    async def boom(solver, questions, *, work=""):
        raise AiError("网络错误：ConnectError")

    monkeypatch.setattr(quiz_task.AiSolver, "solve", boom)
    await engine.scan("me")
    results = await engine.run(["me"], types=["work"], max_retries=0)
    rep = results["me"]
    assert rep.done == 0
    assert net.submits == []
    assert any("AI" in e for e in rep.errors), rep.errors


async def test_quiz_saves_draft_when_ai_answers_not_counted(env, monkeypatch):
    """题目都答上了，但 AI 答案不计入覆盖率（count_ai_as_known=false）→ 只存草稿。"""
    from chaoxing.task import quiz as quiz_task

    engine, repo, net, _ = env
    engine.settings.ai.enabled = True
    engine.settings.ai.api_key = "sk-x"
    engine.settings.quiz.count_ai_as_known = False

    async def fake_solve(self, questions, *, work=""):
        return {q.id: "A" for q in questions}

    monkeypatch.setattr(quiz_task.AiSolver, "solve", fake_solve)
    await engine.scan("me")
    results = await engine.run(["me"], types=["work"])
    assert results["me"].done == 1
    assert net.submits[0]["pyFlag"] == ["1"]
    assert net.submits[0]["answer7001"] == ["A"]


async def test_quiz_not_ready_is_pass(env):
    engine, repo, net, _ = env
    net.work_ready = False
    await engine.scan("me")
    results = await engine.run(["me"], types=["work"])
    assert results["me"].done == 1
    assert net.submits == []


async def test_video_loop_advances_until_passed(env, monkeypatch):
    engine, repo, net, settings = env
    net.duration = 5
    net.pass_at = 5
    settings.video.instant_skip = False
    monkeypatch.setattr(VideoTask, "_gap", lambda self, interval: 0.0)

    await engine.scan("me")
    task = {**next(t for t in repo.iter_tasks() if t["type"] == "video"), "play_time": 0}
    sess = engine.session_for(repo.get_account("me"))
    result = await VideoTask(sess, repo, settings).run(task)
    assert result.ok, result.detail
    assert net.reports[-1]["playingTime"] == "5"
    assert len(net.reports) >= 5


async def test_speed_override_changes_step(env, monkeypatch):
    """--speed 覆盖生效：每轮把 playingTime 推进 用时 × speed。"""
    engine, repo, net, settings = env
    net.duration = 1000
    net.pass_at = 600
    settings.video.instant_skip = False
    settings.video.speed = 1.0  # 配置里是 1 倍
    monkeypatch.setattr(VideoTask, "_gap", lambda self, interval: 0.0)

    clock = itertools.count(0, 100)  # 每轮真实耗时 100 秒
    monkeypatch.setattr(video_mod.time, "monotonic", lambda: next(clock))

    await engine.scan("me")
    task = {**next(t for t in repo.iter_tasks() if t["type"] == "video"), "play_time": 0}
    sess = engine.session_for(repo.get_account("me"))
    result = await VideoTask(sess, repo, settings, speed=3.0).run(task)

    assert result.ok, result.detail
    steps = [r["playingTime"] for r in net.reports]
    assert "300" in steps  # 100s × 3.0，而不是默认倍速下的 100
    assert steps[-1] == "600"


async def test_video_audio_fallback(env, monkeypatch):
    engine, repo, net, settings = env
    net.pass_at = 999  # Video 永远不过
    monkeypatch.setattr(VideoTask, "_gap", lambda self, interval: 0.0)

    await engine.scan("me")
    task = next(t for t in repo.iter_tasks() if t["type"] == "video")
    sess = engine.session_for(repo.get_account("me"))
    result = await VideoTask(sess, repo, settings).run(task)
    assert not result.ok
    assert {r["dtype"] for r in net.reports} == {"Video", "Audio"}


async def test_need_login_marks_account(env):
    engine, repo, net, _ = env
    net.calls.clear()

    def bad(request: httpx.Request) -> httpx.Response:
        if "userLogin4Uname.do" in str(request.url):
            return httpx.Response(200, json={"result": 0, "msg": "未登录"})
        return httpx.Response(404)

    engine.session_for = lambda account: _sess_with(engine.settings, repo, account, bad)  # type: ignore
    report = await engine.scan("me")
    assert report.errors and "会话失效" in report.errors[0]
    assert repo.get_account("me")["status"] == "need_login"


def _sess_with(settings, repo, account, handler) -> CxSession:
    sess = CxSession(settings, cookies={}, repo=repo, account_id=int(account["id"]), label="me")
    sess._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return sess


async def test_video_stops_on_face_requirement(env):
    """服务端在上报响应里要人脸：不硬刚、不空转，直接判 fatal 并提示 cx face。"""
    engine, repo, net, settings = env
    net.face_required = True
    settings.video.instant_skip = False
    await engine.scan("me")
    results = await engine.run(["me"], types=["video"], max_retries=0)
    rep = results["me"]
    assert rep.done == 0
    assert any("人脸" in e for e in rep.errors), rep.errors
    # 只应有首个探测那一次上报，之后立刻收手
    assert len(net.reports) == 1, net.reports
