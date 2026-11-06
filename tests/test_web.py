"""面板 API 测试：状态聚合、鉴权、任务生命周期、错误契约。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from chaoxing.config import Settings
from chaoxing.engine import RunReport, ScanReport
from chaoxing.store.repo import Repo
from chaoxing.web.app import create_app
from chaoxing.web.hub import Hub, Job


@pytest.fixture()
def settings(tmp_path) -> Settings:
    s = Settings(data_dir=tmp_path, db_path=tmp_path / "cx.db", log_path=tmp_path / "cx.log")
    s.ensure_dirs()
    return s


@pytest.fixture()
def seeded(settings: Settings) -> Settings:
    repo = Repo(settings.db_path)
    aid = int(repo.ensure_account("me")["id"])
    course = repo.upsert_course(
        aid, course_id="5678", clazz_id="1234", cpi="", title="高数", teacher="张老师"
    )
    ch1 = repo.upsert_chapter(course, knowledge_id="1002", title="第一章", job_count=2, state="pending")
    ch2 = repo.upsert_chapter(course, knowledge_id="1003", title="第二章", job_count=1, state="pending")
    pk1 = repo.upsert_task(ch1, {"job_id": "job-1", "type": "video", "title": "视频一", "duration": 600})
    repo.upsert_task(ch1, {"job_id": "job-2", "type": "document", "title": "文档一"})
    repo.upsert_task(ch2, {"job_id": "job-3", "type": "video", "title": "视频二", "duration": 300})
    repo.set_task_state(pk1, "done")
    repo.audit(account_id=aid, task_pk=pk1, method="POST", path="/x", status=200, elapsed_ms=12)
    repo.meta_set("enc_version", "v1")
    repo.close()
    return settings


@pytest.fixture()
def client(seeded: Settings):
    with TestClient(create_app(seeded)) as c:
        yield c


def _wait_job(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get("/api/state").json()["job"]
        if job and job["status"] in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("任务没有在超时内结束")


# ---------------------------------------------------------------- 只读接口
def test_health_reports_app_id(client):
    body = client.get("/api/health").json()
    assert body["app"] == "chaoxing-web"
    assert body["ok"] is True


def test_state_aggregates_accounts_and_courses(client):
    body = client.get("/api/state").json()
    assert body["summary"]["total"] == 3
    assert body["summary"]["done"] == 1
    assert body["summary"]["pending"] == 2
    assert body["accounts"][0]["name"] == "me"
    assert body["accounts"][0]["tasks"] == {"total": 3, "done": 1}
    course = body["courses"][0]
    assert course["title"] == "高数"
    assert (course["chapters"], course["total"], course["done"]) == (2, 3, 1)
    video = next(row for row in body["by_type"] if row["type"] == "video")
    assert video["total"] == 2 and video["done"] == 1
    assert body["enc_version"] == "v1"
    assert body["server"]["token_required"] is False


def test_state_reports_account_last_error(settings):
    repo = Repo(settings.db_path)
    aid = int(repo.ensure_account("me")["id"])
    course = repo.upsert_course(
        aid, course_id="1", clazz_id="2", cpi="", title="线代", teacher=""
    )
    ch = repo.upsert_chapter(course, knowledge_id="9", title="第一章", job_count=1, state="pending")
    pk = repo.upsert_task(ch, {"job_id": "job-x", "type": "video", "title": "视频"})
    repo.set_task_state(pk, "failed", err="enc 签名过期")
    repo.close()

    with TestClient(create_app(settings)) as c:
        body = c.get("/api/state").json()
    assert body["accounts"][0]["last_err"] == "enc 签名过期"
    assert body["summary"]["failed"] == 1


def test_state_unknown_account_404(client):
    resp = client.get("/api/state", params={"account": "nobody"})
    assert resp.status_code == 404
    assert "nobody" in resp.json()["error"]


def test_tasks_pagination_and_filters(client):
    body = client.get("/api/tasks", params={"limit": 2}).json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["items"][0]["course"] == "高数"
    assert body["items"][0]["chapter"] == "第一章"

    done = client.get("/api/tasks", params={"state": "done"}).json()
    assert done["total"] == 1 and done["items"][0]["title"] == "视频一"

    docs = client.get("/api/tasks", params={"type": "document"}).json()
    assert docs["total"] == 1

    kw = client.get("/api/tasks", params={"q": "视频二"}).json()
    assert kw["total"] == 1 and kw["items"][0]["type"] == "video"

    page2 = client.get("/api/tasks", params={"limit": 2, "offset": 2}).json()
    assert len(page2["items"]) == 1


def test_runs_and_quiz_stats(client):
    runs = client.get("/api/runs").json()["items"]
    assert runs and runs[0]["method"] == "POST"
    stats = client.get("/api/quiz/stats").json()
    assert stats["total"] == 0


def test_logs_after_cursor(client):
    first = client.get("/api/logs").json()
    assert "logs" in first and "last" in first
    again = client.get("/api/logs", params={"after": first["last"]}).json()
    assert again["logs"] == []


# ---------------------------------------------------------------- 写接口
def test_enc_switch_and_validation(client):
    ok = client.post("/api/enc", json={"version": "v2"})
    assert ok.status_code == 200 and ok.json()["enc_version"] == "v2"
    assert client.get("/api/state").json()["enc_version"] == "v2"

    bad = client.post("/api/enc", json={"version": "v9"})
    assert bad.status_code == 400
    assert "error" in bad.json()


def test_reset_marks_pending(client):
    body = client.post("/api/reset", json={"account": "me"}).json()
    assert body["ok"] is True and body["reset"] >= 1
    summary = client.get("/api/state").json()["summary"]
    assert summary["done"] == 0 and summary["total"] == 3


def test_account_create_and_delete(client):
    created = client.post("/api/accounts", json={"name": "second"})
    assert created.status_code == 200
    assert any(a["name"] == "second" for a in client.get("/api/state").json()["accounts"])

    assert client.delete("/api/accounts/second").status_code == 200
    assert client.delete("/api/accounts/second").status_code == 404


def test_account_login_requires_cookie(client):
    assert client.post("/api/accounts/ghost/login", json={"cookie": "x"}).status_code == 404
    resp = client.post("/api/accounts/me/login", json={"cookie": "  "})
    assert resp.status_code == 400
    assert "cookie" in resp.json()["error"].lower()


# ---------------------------------------------------------------- 任务生命周期
def test_scan_job_runs_and_rejects_concurrent(client, monkeypatch):
    calls: list[str] = []

    async def fake_scan(name, *, include_folders=True):
        calls.append(name)
        await __import__("asyncio").sleep(0.15)
        return ScanReport(account=name, courses=1, chapters=2, tasks=3)

    engine = client.app.state.engine
    monkeypatch.setattr(engine, "scan", fake_scan)

    first = client.post("/api/scan", json={"account": "me"})
    assert first.status_code == 200
    job = first.json()
    assert job["kind"] == "scan" and job["status"] in {"queued", "running"}

    second = client.post("/api/scan", json={"account": "me"})
    assert second.status_code == 409
    assert "已有任务" in second.json()["error"]

    done = _wait_job(client)
    assert done["status"] == "done"
    assert done["result"][0]["tasks"] == 3
    assert calls == ["me"]


def test_run_job_serializes_reports(client, monkeypatch):
    async def fake_run(names, **kwargs):
        return {"me": RunReport(account="me", total=3, done=2, skipped=1, failed=0, seconds=1.5)}

    engine = client.app.state.engine
    monkeypatch.setattr(engine, "run", fake_run)

    resp = client.post("/api/run", json={"account": "me", "types": ["video"], "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["params"]["dry_run"] is True

    done = _wait_job(client)
    assert done["status"] == "done"
    assert done["result"]["me"]["done"] == 2


def test_run_without_accounts_400(settings):
    with TestClient(create_app(settings)) as c:
        resp = c.post("/api/run", json={})
        assert resp.status_code == 400
        assert "账号" in resp.json()["error"]


def test_stop_without_job_409(client):
    resp = client.post("/api/stop")
    assert resp.status_code == 409


def test_stop_cancels_running_job(client, monkeypatch):
    async def slow_scan(name, *, include_folders=True):
        await __import__("asyncio").sleep(5)
        return ScanReport(account=name)

    monkeypatch.setattr(client.app.state.engine, "scan", slow_scan)
    client.post("/api/scan", json={"account": "me"})
    assert client.post("/api/stop").json()["ok"] is True
    done = _wait_job(client)
    assert done["status"] == "cancelled"
    assert done["error"]


# ---------------------------------------------------------------- 鉴权
def test_token_required_when_configured(seeded):
    with TestClient(create_app(seeded, token="secret")) as c:
        assert c.get("/api/health").status_code == 200
        denied = c.get("/api/state")
        assert denied.status_code == 401
        assert "令牌" in denied.json()["error"]
        assert c.get("/api/state", params={"token": "secret"}).status_code == 200
        assert c.get("/api/state", headers={"X-CX-Token": "secret"}).status_code == 200
        assert c.get("/api/state", headers={"X-CX-Token": "wrong"}).status_code == 401
        # 带 token 打开过一次后，cookie 生效
        assert c.get("/api/state").status_code == 200


def test_root_serves_panel_or_clear_error(client):
    resp = client.get("/")
    assert resp.status_code in {200, 500}
    if resp.status_code == 500:
        assert "index.html" in resp.text


def test_static_assets_served_from_root_and_static_mount(client):
    # 面板 HTML 用的是 app.js / style.css 这种相对路径，两种前缀都要能取到
    for prefix in ("/", "/static/"):
        assert client.get(prefix + "app.js").status_code == 200
        assert client.get(prefix + "style.css").status_code == 200


def test_scan_honours_include_folders_alias(client, monkeypatch):
    seen: list[bool] = []

    async def fake_scan(name, *, include_folders=True):
        seen.append(include_folders)
        return ScanReport(account=name)

    monkeypatch.setattr(client.app.state.engine, "scan", fake_scan)

    assert client.post("/api/scan", json={"include_folders": False}).status_code == 200
    _wait_job(client)
    assert client.post("/api/scan", json={"folders": False}).status_code == 200
    _wait_job(client)
    assert seen == [False, False]


def test_events_stream_opens_with_retry_and_log_backlog(client):
    with client.stream("GET", "/api/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache, no-transform"
        chunk = ""
        for piece in resp.iter_text():
            chunk += piece
            if "retry: 2000" in chunk and "event: log" in chunk:
                break
            if len(chunk) > 20000:
                break
    assert "retry: 2000" in chunk
    assert "event: log" in chunk


# ---------------------------------------------------------------- 进度回调
def test_hub_translates_engine_events_into_progress():
    hub = Hub()
    hub.job = Job(id=1, kind="scan", label="抓取 me")
    hub.on_engine_event("scan", {"stage": "login"})
    assert hub.job.progress["message"] == "校验登录态…"

    hub.on_engine_event("scan", {"stage": "courses", "total": 3})
    assert hub.job.progress["total"] == 3

    hub.on_engine_event("scan", {"stage": "course", "index": 1, "total": 3, "title": "高数"})
    assert hub.job.progress["done"] == 1
    assert "高数" in hub.job.progress["message"]

    # 换个 job，旧类型的事件不该再改它
    hub.job = Job(id=2, kind="run", label="执行任务点")
    hub.on_engine_event("scan", {"stage": "courses", "total": 99})
    assert hub.job.progress["total"] == 0

    hub.on_engine_event("run", {"stage": "start", "total": 10})
    hub.on_engine_event(
        "run", {"stage": "task", "index": 4, "total": 10, "ok": True, "title": "视频一"}
    )
    assert hub.job.progress["done"] == 4
    assert "4/10" in hub.job.progress["message"] and "OK" in hub.job.progress["message"]

    hub.on_engine_event("run", {"stage": "end"})
    assert hub.job.progress["done"] == 10


