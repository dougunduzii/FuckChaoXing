"""最小 Web 面板：看进度、点抓取、点运行。

启动：python -m chaoxing.web.app  → http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

from ..config import Settings, load_settings
from ..engine import Engine
from ..store.repo import Repo

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>超星刷课 · 面板</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; max-width: 1100px; }
  h1 { font-size: 20px; margin: 0 0 16px; }
  section { border: 1px solid #d0d0d5; border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }
  h2 { font-size: 15px; margin: 0 0 10px; font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #e5e5ea; }
  button, select, input { font: inherit; padding: 5px 10px; border-radius: 7px; border: 1px solid #c5c5cc; background: #fff; }
  button { cursor: pointer; }
  button.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
  button:disabled { opacity: .5; cursor: default; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  pre { background: #0f172a; color: #d1e0ff; padding: 12px; border-radius: 8px; height: 260px; overflow: auto; font-size: 12px; margin: 0; }
  .tag { font-size: 12px; padding: 1px 6px; border-radius: 99px; background: #eef2ff; color: #3730a3; }
  .muted { color: #888; }
</style>
</head>
<body>
<h1>超星刷课 · 面板 <span id="state" class="tag">空闲</span></h1>

<section>
  <h2>操作</h2>
  <div class="row">
    <select id="account"><option value="">全部账号</option></select>
    <button id="btn-scan">抓取课程结构</button>
    <button id="btn-run" class="primary">开始刷课</button>
    <button id="btn-dry">试跑（不发上报）</button>
  </div>
  <p class="muted" id="hint">先「抓取课程结构」，把课程/章节/任务点写进库，再「开始刷课」。</p>
</section>

<section>
  <h2>任务点进度</h2>
  <table id="tasks"><thead><tr><th>类型</th><th>状态</th><th>数量</th></tr></thead><tbody></tbody></table>
</section>

<section>
  <h2>课程</h2>
  <table id="courses"><thead><tr><th>id</th><th>课程</th><th>章节</th><th>完成/总数</th></tr></thead><tbody></tbody></table>
</section>

<section>
  <h2>日志</h2>
  <pre id="log"></pre>
</section>

<script>
const $ = (id) => document.getElementById(id);
let busy = false;

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  };
  const r = await fetch(path, opt);
  return r.json();
}

function render(s) {
  $("state").textContent = s.running ? "运行中" : "空闲";
  $("state").style.background = s.running ? "#dcfce7" : "#eef2ff";
  $("state").style.color = s.running ? "#166534" : "#3730a3";
  ["btn-scan", "btn-run", "btn-dry"].forEach(id => $(id).disabled = s.running || busy);

  const sel = $("account");
  const cur = sel.value;
  const names = s.accounts.map(a => a.name);
  sel.innerHTML = '<option value="">全部账号</option>' + names.map(n => `<option>${n}</option>`).join("");
  sel.value = names.includes(cur) ? cur : "";

  $("tasks").tBodies[0].innerHTML = s.tasks.map(t =>
    `<tr><td>${t.type}</td><td>${t.state}</td><td>${t.n}</td></tr>`).join("")
    || '<tr><td colspan="3" class="muted">还没有数据</td></tr>';

  $("courses").tBodies[0].innerHTML = s.courses.map(c =>
    `<tr><td>${c.id}</td><td>${c.title || "-"}</td><td>${c.chapters}</td><td>${c.done}/${c.total}</td></tr>`).join("")
    || '<tr><td colspan="4" class="muted">还没有数据</td></tr>';

  const log = $("log");
  log.textContent = s.logs.join("\\n");
  log.scrollTop = log.scrollHeight;
}

async function refresh() {
  try { render(await api("/api/state")); } catch (e) {}
}

async function act(kind, dry) {
  const name = $("account").value;
  busy = true; refresh();
  const body = {name: name || null};
  if (kind === "run") { body.dry_run = !!dry; }
  const res = await api("/api/" + kind, body);
  busy = false;
  if (res.error) alert(res.error);
  refresh();
}

$("btn-scan").onclick = () => act("scan");
$("btn-run").onclick = () => act("run", false);
$("btn-dry").onclick = () => act("run", true);
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
"""


class ActionBody(BaseModel):
    name: str | None = None
    dry_run: bool = False
    course_pk: int | None = None
    types: list[str] | None = None


class Hub:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.logs: deque[str] = deque(maxlen=400)
        self.task: asyncio.Task | None = None
        self._sink_id: int | None = None

    def attach(self) -> None:
        if self._sink_id is None:
            self._sink_id = logger.add(
                lambda msg: self.logs.append(str(msg).rstrip()), level="INFO", format="{time:HH:mm:ss} {message}"
            )

    def detach(self) -> None:
        if self._sink_id is not None:
            try:
                logger.remove(self._sink_id)
            except ValueError:
                pass
            self._sink_id = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def spawn(self, coro) -> None:
        if self.running:
            return
        self.task = asyncio.create_task(coro)

    def state(self) -> dict[str, Any]:
        repo = self.engine.repo
        accounts = repo.list_accounts()
        courses = repo._q(
            """SELECT c.id, c.title,
                      (SELECT COUNT(*) FROM chapter ch WHERE ch.course_pk=c.id) AS chapters,
                      (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                        WHERE ch.course_pk=c.id AND t.state='done') AS done,
                      (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                        WHERE ch.course_pk=c.id) AS total
               FROM course c ORDER BY c.id"""
        )
        return {
            "running": self.running,
            "accounts": [{"name": a["name"], "status": a.get("status")} for a in accounts],
            "tasks": repo.task_stats(),
            "courses": courses,
            "logs": list(self.logs),
            "enc_version": repo.meta_get("enc_version", "v2"),
        }


def _lifespan(hub: "Hub"):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm(app):  # noqa: ANN001
        hub.attach()
        try:
            yield
        finally:
            hub.detach()

    return _cm


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    repo = Repo(settings.db_path)
    repo.recover_running()
    engine = Engine(settings, repo)
    hub = Hub(engine)

    app = FastAPI(title="超星刷课面板", lifespan=_lifespan(hub))

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(hub.state())

    @app.post("/api/scan")
    async def api_scan(body: ActionBody) -> JSONResponse:
        if hub.running:
            return JSONResponse({"error": "已有任务在跑"})
        names = [body.name] if body.name else [a["name"] for a in repo.list_accounts()]
        if not names:
            return JSONResponse({"error": "还没有账号，先执行 cx login <name>"})

        async def job() -> None:
            for n in names:
                await engine.scan(n)

        hub.spawn(job())
        return JSONResponse({"ok": True, "accounts": names})

    @app.post("/api/run")
    async def api_run(body: ActionBody) -> JSONResponse:
        if hub.running:
            return JSONResponse({"error": "已有任务在跑"})
        names = [body.name] if body.name else [a["name"] for a in repo.list_accounts()]
        if not names:
            return JSONResponse({"error": "还没有账号"})

        async def job() -> None:
            await engine.run(
                names,
                course_pk=body.course_pk,
                types=body.types,
                dry_run=body.dry_run,
                concurrency=3,
            )

        hub.spawn(job())
        return JSONResponse({"ok": True, "dry_run": body.dry_run, "accounts": names})

    @app.post("/api/stop")
    async def api_stop() -> JSONResponse:
        if hub.task and not hub.task.done():
            hub.task.cancel()
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "没有在跑的任务"})

    @app.get("/api/runs")
    async def api_runs(limit: int = 50) -> JSONResponse:
        return JSONResponse({"items": repo.recent_runs(limit)})

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("chaoxing.web.app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
