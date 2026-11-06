"""FastAPI 应用工厂：REST + SSE + 静态面板。

分层：``app.py`` 只负责协议（路由、序列化、鉴权），``hub.py`` 持有任务与日志，
``serve.py`` 负责监听、端口与浏览器。这样测试可以直接 ``create_app(settings)``
拿 TestClient 打，不必真的起 uvicorn。

安全边界：绑定非回环地址时 serve 会生成 token，中间件校验
``X-CX-Token`` 头 / ``?token=`` 查询参数 / ``cx_token`` cookie 三者之一。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from .. import auth
from ..config import Settings, load_settings
from ..engine import Engine
from ..store.repo import Repo
from . import queries
from .hub import Hub

APP_ID = "chaoxing-web"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TASK_STATES = queries.TASK_STATES


# ---------------------------------------------------------------- 请求体
class ScanBody(BaseModel):
    name: str | None = None
    account: str | None = None
    folders: bool = True
    # 前端早期用的是 include_folders，这里两个名字都收，谁显式给了用谁
    include_folders: bool | None = None

    @property
    def with_folders(self) -> bool:
        return self.folders if self.include_folders is None else self.include_folders


class RunBody(BaseModel):
    name: str | None = None
    account: str | None = None
    course_pk: int | None = None
    types: list[str] | None = None
    dry_run: bool = False
    limit: int | None = None
    speed: float | None = None
    retries: int | None = None
    concurrency: int = 3
    task_concurrency: int = 1


class ResetBody(BaseModel):
    name: str | None = None
    account: str | None = None
    course_pk: int | None = None
    types: list[str] | None = None


class EncBody(BaseModel):
    version: str


class AccountBody(BaseModel):
    name: str


class LoginBody(BaseModel):
    cookie: str = ""


def _pick_name(*candidates: str | None) -> str | None:
    for item in candidates:
        if item and item.strip():
            return item.strip()
    return None


def _split(values: str | None) -> list[str] | None:
    if not values:
        return None
    items = [v.strip() for v in values.replace("|", ",").split(",") if v.strip()]
    return items or None


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# ---------------------------------------------------------------- 状态快照
def build_state(
    repo: Repo,
    settings: Settings,
    hub: Hub,
    *,
    account: str | None = None,
    server: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """面板总览数据。``account`` 给定时所有统计都收敛到该账号。"""
    account_row = repo.get_account(account) if account else None
    account_id = int(account_row["id"]) if account_row else None

    accounts: list[dict[str, Any]] = []
    for row in repo.list_accounts():
        aid = int(row["id"])
        totals = queries.task_totals(repo, aid)
        accounts.append(
            {
                "name": row["name"],
                "status": row.get("status") or "",
                "puid": row.get("puid") or "",
                "last_login_ts": row.get("last_login_ts"),
                "courses": len(repo.list_courses(aid)),
                "tasks": {"total": int(totals.get("total", 0)), "done": int(totals.get("done", 0))},
                "last_err": queries.last_error(repo, aid),
            }
        )

    totals = queries.task_totals(repo, account_id)
    summary = {state: int(totals.get(state, 0)) for state in TASK_STATES}
    summary["total"] = int(totals.get("total", 0))

    by_type: dict[str, dict[str, int]] = {}
    for row in queries.task_stats(repo, account_id):
        bucket = by_type.setdefault(
            row["type"], {**{state: 0 for state in TASK_STATES}, "total": 0}
        )
        bucket[row["state"]] = int(row["n"])
        bucket["total"] += int(row["n"])
    by_type_list = [{"type": k, **v} for k, v in sorted(by_type.items())]

    return {
        "running": hub.running,
        "job": hub.job.to_dict() if hub.job is not None else None,
        "jobs": hub.jobs_list(),
        "accounts": accounts,
        "summary": summary,
        "by_type": by_type_list,
        "courses": queries.course_overview(repo, account_id),
        "enc_version": repo.meta_get("enc_version", "v2") or "v2",
        "settings": {
            "speed": settings.video.speed,
            "task_concurrency": 1,
            "types": list(settings.types),
            "ai_enabled": bool(settings.ai.enabled),
            "ai_model": settings.ai.model,
            "daily_cap_hours": settings.risk.daily_cap_hours,
            "max_lanes": settings.risk.max_lanes,
        },
        "usage": {"today_minutes": queries.usage_minutes(repo, account_id)},
        "server": server or {},
    }


def create_app(
    settings: Settings | None = None,
    *,
    token: str | None = None,
    server: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    server_info = dict(server or {})
    if not server_info:
        # --reload 时应用是子进程重新 import 出来的，监听信息只能从环境变量取
        env_host = os.environ.get("CX_WEB_HOST")
        if env_host:
            env_port = os.environ.get("CX_WEB_PORT", "")
            server_info = {
                "host": env_host,
                "port": int(env_port) if env_port.isdigit() else None,
            }
    token = token or os.environ.get("CX_WEB_TOKEN") or ""
    server_info.setdefault("token_required", bool(token))

    repo = Repo(settings.db_path)
    repo.recover_running()
    hub = Hub()
    engine = Engine(settings, repo, on_event=hub.on_engine_event)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        hub.attach_logs()
        logger.info("网页面板已就绪")
        try:
            yield
        finally:
            await hub.shutdown()

    app = FastAPI(
        title="cx web panel",
        version="2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.boot = time.monotonic()
    app.state.settings = settings
    app.state.repo = repo
    app.state.hub = hub
    app.state.engine = engine
    app.state.token = token
    app.state.server = server_info

    # ---------------- 鉴权 ----------------
    @app.middleware("http")
    async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not token:
            return await call_next(request)
        path = request.url.path
        # 静态资源放行（页面本身不含数据）；数据接口一律校验
        if path == "/api/health" or not path.startswith("/api/"):
            return await call_next(request)
        given = (
            request.headers.get("x-cx-token")
            or request.query_params.get("token")
            or request.cookies.get("cx_token")
            or ""
        )
        if given != token:
            return JSONResponse({"error": "缺少或错误的访问令牌（?token=…）"}, status_code=401)
        response = await call_next(request)
        if request.query_params.get("token"):
            # 首次带 token 打开页面后种 cookie，之后的 fetch 不用每次拼 query
            response.set_cookie("cx_token", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
        return response

    # ---------------- 错误统一成 {"error": ...} ----------------
    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = (exc.errors() or [{}])[0]
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return JSONResponse(
            {"error": f"参数不合法 {where}: {first.get('msg', '')}".strip()}, status_code=422
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        logger.exception(f"面板未处理异常: {exc}")
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    # ---------------- 基础 ----------------
    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        return {
            "app": APP_ID,
            "version": "2.0",
            "ok": True,
            "pid": os.getpid(),
            "running": hub.running,
            "uptime": round(time.monotonic() - app.state.boot, 1),
            "server": server_info,
        }

    @app.get("/api/state")
    async def api_state(account: str | None = Query(None)) -> dict[str, Any]:
        name = _pick_name(account)
        if name and repo.get_account(name) is None:
            raise HTTPException(404, f"账号不存在：{name}")
        return build_state(repo, settings, hub, account=name, server=server_info)

    @app.get("/api/logs")
    async def api_logs(
        after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)
    ) -> dict[str, Any]:
        return {"logs": hub.logs_after(after, limit), "last": hub.last_seq}

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        queue = hub.subscribe()
        # 先取水位再补历史：水位之前的走回放，之后的走队列，避免同一条日志发两遍
        watermark = hub.last_seq
        backlog = [x for x in hub.logs_after(max(0, watermark - 40), 40) if x["seq"] <= watermark]
        hub.log(f"SSE 客户端接入（当前 {len(hub._subs)} 个）", level="DEBUG")

        async def stream():
            try:
                yield "retry: 2000\n\n"
                for item in backlog:
                    yield f"event: log\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                if hub.job is not None:
                    yield f"event: job\ndata: {json.dumps(hub.job.to_dict(), ensure_ascii=False)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    body = json.dumps(payload["data"], ensure_ascii=False)
                    yield f"event: {payload['type']}\ndata: {body}\n\n"
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------------- 任务 ----------------
    def _reject_if_busy() -> None:
        message = hub.busy_error()
        if message:
            raise HTTPException(409, message)

    def _resolve_names(name: str | None) -> list[str]:
        if name:
            if repo.get_account(name) is None:
                raise HTTPException(404, f"账号不存在：{name}")
            return [name]
        names = [row["name"] for row in repo.list_accounts()]
        if not names:
            raise HTTPException(400, "还没有账号，先在「设置」里添加并登录")
        return names

    @app.post("/api/scan")
    async def api_scan(body: ScanBody | None = None) -> dict[str, Any]:
        body = body or ScanBody()
        _reject_if_busy()
        name = _pick_name(body.name, body.account)
        names = _resolve_names(name)
        folders = body.with_folders
        params = {"accounts": names, "folders": folders}

        async def work() -> Any:
            reports = []
            for item in names:
                report = await engine.scan(item, include_folders=folders)
                reports.append(_jsonable(report))
            return reports

        label = f"抓取 {names[0]}" if len(names) == 1 else f"抓取全部（{len(names)} 个账号）"
        job = hub.start_job("scan", label, work, params=params)
        return job.to_dict()

    @app.post("/api/run")
    async def api_run(body: RunBody | None = None) -> dict[str, Any]:
        body = body or RunBody()
        _reject_if_busy()
        name = _pick_name(body.name, body.account)
        names = _resolve_names(name)
        types = [t for t in (body.types or []) if t] or None
        params = {
            "accounts": names,
            "course_pk": body.course_pk,
            "types": types or list(settings.types),
            "dry_run": body.dry_run,
            "limit": body.limit,
            "speed": body.speed,
        }

        async def work() -> Any:
            results = await engine.run(
                names,
                course_pk=body.course_pk,
                types=types,
                max_retries=body.retries,
                dry_run=body.dry_run,
                concurrency=max(1, body.concurrency),
                task_concurrency=max(1, body.task_concurrency),
                limit=body.limit,
                speed=body.speed,
            )
            return {k: _jsonable(v) for k, v in results.items()}

        label = "演练 dry-run" if body.dry_run else "执行任务点"
        label += f"（{names[0]}）" if len(names) == 1 else f"（{len(names)} 个账号）"
        job = hub.start_job("run", label, work, params=params)
        return job.to_dict()

    @app.post("/api/stop")
    async def api_stop() -> dict[str, Any]:
        stopped = hub.stop()
        if not stopped:
            raise HTTPException(409, "当前没有正在跑的任务")
        return {"ok": True, "stopped": True}

    @app.post("/api/reset")
    async def api_reset(body: ResetBody | None = None) -> dict[str, Any]:
        body = body or ResetBody()
        _reject_if_busy()
        name = _pick_name(body.name, body.account)
        names = _resolve_names(name)
        types = [t for t in (body.types or []) if t] or None
        total = 0
        for item in names:
            row = repo.get_account(item)
            if row is None:
                continue
            total += repo.reset_tasks(
                account_id=int(row["id"]), course_pk=body.course_pk, types=types
            )
        hub.log(f"已重置 {total} 个任务点（{', '.join(names)}）", level="WARNING")
        return {"ok": True, "reset": total, "accounts": names}

    @app.post("/api/enc")
    async def api_enc(body: EncBody) -> dict[str, Any]:
        version = (body.version or "").strip().lower()
        if version not in {"v1", "v2"}:
            raise HTTPException(400, "只支持 v1 / v2")
        repo.meta_set("enc_version", version)
        hub.log(f"enc 版本切换为 {version}", level="WARNING")
        return {"ok": True, "enc_version": version}

    # ---------------- 账号 ----------------
    @app.post("/api/accounts")
    async def api_account_create(body: AccountBody) -> dict[str, Any]:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "账号名不能为空")
        repo.ensure_account(name)
        hub.log(f"账号已登记：{name}")
        row = repo.get_account(name) or {}
        return {"ok": True, "account": {"name": name, "status": row.get("status") or ""}}

    @app.delete("/api/accounts/{name}")
    async def api_account_delete(name: str) -> dict[str, Any]:
        if repo.get_account(name) is None:
            raise HTTPException(404, f"账号不存在：{name}")
        queries.delete_account(repo, name)
        hub.log(f"账号已删除：{name}", level="WARNING")
        return {"ok": True}

    @app.post("/api/accounts/{name}/login")
    async def api_account_login(name: str, body: LoginBody | None = None) -> dict[str, Any]:
        body = body or LoginBody()
        account = repo.get_account(name)
        if account is None:
            raise HTTPException(404, f"账号不存在：{name}")
        raw = (body.cookie or "").strip()
        if not raw:
            raise HTTPException(400, "cookie 不能为空")
        sess = engine.session_for(account)
        try:
            if raw.startswith("{"):
                try:
                    sess.set_cookies({str(k): str(v) for k, v in json.loads(raw).items()})
                except json.JSONDecodeError as exc:
                    raise HTTPException(400, f"cookie JSON 解析失败：{exc}") from exc
            else:
                await auth.import_cookies(sess, raw)
            info = await auth.ensure_login(sess, name, repo)
        finally:
            await sess.close()
        if info is None:
            raise HTTPException(400, "cookie 无效或需要验证码，请重新获取")
        hub.log(f"[{name}] cookie 登录成功")
        return {"ok": True, "account": name, "puid": info.get("puid", "")}

    @app.post("/api/accounts/{name}/browser-login")
    async def api_account_browser_login(name: str) -> dict[str, Any]:
        _reject_if_busy()
        repo.ensure_account(name)

        async def work() -> Any:
            account = repo.get_account(name)
            if account is None:
                raise RuntimeError("账号不存在")
            sess = engine.session_for(account)
            try:
                cookies = await auth.browser_login(settings.data_dir / "cookie.json")
                sess.set_cookies(cookies)
                info = await auth.ensure_login(sess, name, repo)
            finally:
                await sess.close()
            if info is None:
                raise RuntimeError("浏览器登录后会话校验失败")
            return {"ok": True, "puid": info.get("puid", "")}

        job = hub.start_job("browser-login", f"浏览器登录 {name}", work, params={"account": name})
        return job.to_dict()

    # ---------------- 查询 ----------------
    @app.get("/api/tasks")
    async def api_tasks(
        account: str | None = Query(None),
        state: str | None = Query(None),
        type: str | None = Query(None),
        course_pk: int | None = Query(None),
        q: str | None = Query(None),
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        account_id = None
        name = _pick_name(account)
        if name:
            row = repo.get_account(name)
            if row is None:
                raise HTTPException(404, f"账号不存在：{name}")
            account_id = int(row["id"])
        states = _split(state) if state and state not in {"all", "*"} else None
        types = _split(type) if type and type not in {"all", "*"} else None
        kwargs = {
            "account_id": account_id,
            "course_pk": course_pk,
            "types": types,
            "states": states,
            "keyword": (q or "").strip() or None,
        }
        items = queries.list_tasks(repo, **kwargs, limit=limit, offset=offset)
        return {
            "items": items,
            "total": queries.count_tasks(repo, **kwargs),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/runs")
    async def api_runs(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        return {"items": repo.recent_runs(limit)}

    @app.get("/api/quiz/stats")
    async def api_quiz_stats() -> dict[str, Any]:
        return repo.quiz_stats()

    # ---------------- 静态面板 ----------------
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isdir(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> Any:
        if os.path.isfile(index_path):
            return FileResponse(index_path, media_type="text/html; charset=utf-8")
        return HTMLResponse(
            "<h1>面板静态资源缺失</h1><p>缺少 chaoxing/web/static/index.html，"
            "请重新安装或从仓库恢复该目录。</p>",
            status_code=500,
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Any:
        icon = os.path.join(STATIC_DIR, "favicon.ico")
        if os.path.isfile(icon):
            return FileResponse(icon)
        return JSONResponse({"error": "no favicon"}, status_code=404)

    # 面板 HTML 里用的是 app.js / style.css 这类相对路径，这里兜住根路径下的静态文件
    if os.path.isdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=False), name="assets")

    return app


def main() -> None:  # pragma: no cover
    """``python -m chaoxing.web.app``：等价于 ``cx web``。"""
    from .serve import run_panel

    raise SystemExit(run_panel())


if __name__ == "__main__":  # pragma: no cover
    main()
