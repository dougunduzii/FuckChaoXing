"""命令行入口：cx ..."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from . import auth
from .config import DEFAULT_CONFIG_TOML, Settings, load_settings
from .engine import Engine
from .store.repo import Repo

console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=True, help="超星学习通任务点自动化工具")
account_app = typer.Typer(no_args_is_help=True, help="账号管理")
quiz_app = typer.Typer(no_args_is_help=True, help="题库管理")
ai_app = typer.Typer(no_args_is_help=True, help="AI 答题接口")
app.add_typer(account_app, name="account")
app.add_typer(quiz_app, name="quiz")
app.add_typer(ai_app, name="ai")

CONFIG_PATH = Path("config.toml")


# ---------------------------------------------------------------- 基础设施
def _boot(verbose: bool = False) -> tuple[Settings, Repo]:
    settings = load_settings(CONFIG_PATH if CONFIG_PATH.exists() else None)
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO", enqueue=False)
    logger.add(settings.log_path, level="DEBUG", rotation="5 MB", retention=5, encoding="utf-8")
    repo = Repo(settings.db_path)
    repo.recover_running()
    return settings, repo


def _engine(verbose: bool = False) -> Engine:
    settings, repo = _boot(verbose)
    return Engine(settings, repo)


@app.command("init")
def cmd_init(force: bool = typer.Option(False, "--force", help="覆盖已有配置")):
    """生成 config.toml 与 data 目录。"""
    if CONFIG_PATH.exists() and not force:
        console.print("[yellow]config.toml 已存在，用 --force 覆盖[/yellow]")
        return
    CONFIG_PATH.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    load_settings(CONFIG_PATH)
    console.print(f"[green]已生成 {CONFIG_PATH}[/green]")


@app.command("doctor")
def cmd_doctor(verbose: bool = typer.Option(False, "--verbose")):
    """环境自检。"""
    settings, repo = _boot(verbose)
    table = Table(title="环境自检")
    table.add_column("项")
    table.add_column("状态")

    table.add_row("data_dir", str(settings.data_dir))
    table.add_row("db", str(settings.db_path))

    for mod in ("httpx", "lxml", "bs4", "fontTools", "cryptography", "typer", "rich"):
        try:
            __import__(mod)
            table.add_row(f"依赖 {mod}", "[green]OK[/green]")
        except ImportError:
            table.add_row(f"依赖 {mod}", "[red]缺失[/red]")

    try:
        import playwright  # noqa: F401

        for name, path, ok in auth.browser_candidates():
            if ok:
                table.add_row(f"浏览器 {name}", f"[green]OK[/green] {path}")
            else:
                table.add_row(f"浏览器 {name}", f"[yellow]缺失[/yellow] {path}")
    except ImportError:
        table.add_row("playwright", "[yellow]未装（只能用 --cookie / --password 登录）[/yellow]")

    accounts = repo.list_accounts()
    table.add_row("账号数", str(len(accounts)))
    table.add_row("enc 版本", repo.meta_get("enc_version", "v2") or "v2")
    console.print(table)


# ---------------------------------------------------------------- 登录
@app.command("login")
def cmd_login(
    name: Annotated[str, typer.Argument(help="账号备注名，如 me")],
    password: bool = typer.Option(False, "--password", help="用账号密码登录"),
    cookie: Optional[str] = typer.Option(None, "--cookie", help="直接粘贴 cookie 字符串"),
    cookie_file: Optional[Path] = typer.Option(None, "--cookie-file", help="从文件读取 cookie"),
    browser: bool = typer.Option(False, "--browser", help="弹出浏览器手动登录（推荐）"),
    uname: Optional[str] = typer.Option(None, "--uname", help="手机号/账号"),
    verbose: bool = typer.Option(False, "--verbose"),
):
    """登录并把 cookie 存入数据库。"""
    engine = _engine(verbose)
    repo = engine.repo
    repo.ensure_account(name)

    async def run() -> None:
        account = repo.get_account(name)
        assert account is not None
        sess = engine.session_for(account)
        try:
            if cookie or cookie_file:
                raw = cookie or Path(cookie_file).read_text(encoding="utf-8")  # type: ignore[arg-type]
                if raw.strip().startswith("{"):
                    data = json.loads(raw)
                    for k, v in data.items():
                        sess.set_cookies({k: v})
                else:
                    await auth.import_cookies(sess, raw)
            elif browser:
                cookies = await auth.browser_login(engine.settings.data_dir / "cookie.json")
                sess.set_cookies(cookies)
            elif password:
                login_name = uname or typer.prompt("账号（手机号）")
                pwd = typer.prompt("密码", hide_input=True)
                res = await auth.login_password(sess, login_name, pwd)
                if not res["ok"]:
                    console.print(f"[red]登录失败：{res['msg']}[/red]")
                    raise typer.Exit(code=1)
            else:
                cookies = await auth.browser_login(engine.settings.data_dir / "cookie.json")
                sess.set_cookies(cookies)

            msg = await auth.ensure_login(sess, name, repo)
            if msg is None:
                console.print("[red]会话校验失败：cookie 无效或需要验证码[/red]")
                raise typer.Exit(code=1)
            console.print(
                f"[green]登录成功[/green] puid={msg.get('puid', '')} "
                f"name={msg.get('name', '') or msg.get('uname', '')}"
            )
        finally:
            await sess.close()

    asyncio.run(run())


@account_app.command("list")
def cmd_account_list():
    """列出账号。"""
    _, repo = _boot()
    table = Table(title="账号")
    for col in ("id", "name", "puid", "status", "cookies", "last_login"):
        table.add_column(col)
    for a in repo.list_accounts():
        n_cookie = len(json.loads(a.get("cookies") or "{}"))
        table.add_row(
            str(a["id"]), a["name"], a.get("puid") or "-", a.get("status") or "-",
            str(n_cookie), str(a.get("last_login_ts") or "-"),
        )
    console.print(table)


@account_app.command("remove")
def cmd_account_remove(name: str):
    """删除账号及其课程/任务点。"""
    _, repo = _boot()
    repo._x("DELETE FROM account WHERE name=?", (name,))
    console.print(f"[green]已删除 {name}[/green]")


# ---------------------------------------------------------------- 抓取 / 执行
@app.command("scan")
def cmd_scan(
    name: Optional[str] = typer.Argument(None, help="账号名，留空则全部账号"),
    folders: bool = typer.Option(True, "--folders/--no-folders", help="是否遍历课程文件夹"),
    verbose: bool = typer.Option(False, "--verbose"),
):
    """抓课程 → 章节 → 任务点入库。"""
    engine = _engine(verbose)
    names = [name] if name else [a["name"] for a in engine.repo.list_accounts()]
    if not names:
        console.print("[red]还没有账号，先 cx login <name>[/red]")
        raise typer.Exit(code=1)

    async def run() -> None:
        for n in names:
            report = await engine.scan(n, include_folders=folders)
            if report.errors:
                for e in report.errors:
                    console.print(f"[yellow]{n}: {e}[/yellow]")
            else:
                face_hint = f" / 人脸任务点 {report.faces}" if report.faces else ""
                console.print(
                    f"[green]{n}[/green] 课程 {report.courses} / 章节 {report.chapters} / "
                    f"任务点 {report.tasks} / 跳过 {report.skipped_locked}{face_hint}"
                )

    asyncio.run(run())


@app.command("run")
def cmd_run(
    name: Optional[str] = typer.Argument(None, help="账号名，留空则全部账号"),
    course: Optional[int] = typer.Option(None, "--course", help="只跑某门课（course 表 id）"),
    types: Optional[str] = typer.Option(None, "--types", help="任务类型，逗号分隔：video,document,read,work"),
    retries: Optional[int] = typer.Option(None, "--retries", help="单任务重试次数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只算不发上报请求"),
    concurrency: int = typer.Option(3, "--concurrency", help="账号并发数"),
    limit: Optional[int] = typer.Option(None, "--limit", help="本次最多处理几个任务点（试跑用）"),
    task_concurrency: int = typer.Option(
        1, "--task-concurrency", help="同账号内并行任务点数量（默认 1，稳妥；>4 易被判定多端播放）"
    ),
    speed: Optional[float] = typer.Option(
        None, "--speed", help="本次覆盖倍速（默认取 config.toml 的 [video] speed，建议 1.0~2.0）"
    ),
    verbose: bool = typer.Option(False, "--verbose"),
):
    """执行待完成任务点。"""
    engine = _engine(verbose)
    names = [name] if name else [a["name"] for a in engine.repo.list_accounts()]
    if not names:
        console.print("[red]还没有账号[/red]")
        raise typer.Exit(code=1)
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None

    async def run() -> None:
        results = await engine.run(
            names,
            course_pk=course,
            types=type_list,
            max_retries=retries,
            dry_run=dry_run,
            concurrency=concurrency,
            task_concurrency=task_concurrency,
            limit=limit,
            speed=speed,
        )
        table = Table(title="执行结果" + ("（演练 dry-run，未提交任何请求）" if dry_run else ""))
        for col in ("账号", "总数", "完成", "跳过", "失败", "耗时(min)"):
            table.add_column(col)
        for n, r in results.items():
            table.add_row(
                n, str(r.total), str(r.done), str(r.skipped), str(r.failed), f"{r.seconds / 60:.1f}"
            )
        console.print(table)
        if dry_run:
            console.print(
                "[yellow]注意：dry-run 只演练，服务端不会有任何进度变化；"
                "去掉 --dry-run 才是真跑。[/yellow]"
            )
        for n, r in results.items():
            for e in r.errors[:10]:
                console.print(f"[yellow]{n}: {e}[/yellow]")

    asyncio.run(run())


@app.command("reset")
def cmd_reset(
    name: Optional[str] = typer.Argument(None, help="账号名，留空则全部账号"),
    course: Optional[int] = typer.Option(None, "--course", help="只重置某门课（course 表 id）"),
    types: Optional[str] = typer.Option(None, "--types", help="只重置某类任务点"),
    verbose: bool = typer.Option(False, "--verbose"),
):
    """把任务点重置为待执行（清掉 done/failed 与本地进度），下次 scan 重新对齐服务端。"""
    _, repo = _boot(verbose)
    names = [name] if name else [a["name"] for a in repo.list_accounts()]
    if not names:
        console.print("[red]还没有账号[/red]")
        raise typer.Exit(code=1)
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    total = 0
    for n in names:
        acc = repo.get_account(n)
        if acc is None:
            console.print(f"[red]{n}: 账号不存在[/red]")
            continue
        c = repo.find_course(int(acc["id"]), course) if course else None
        pk = int(c["id"]) if c else None
        total += repo.reset_tasks(account_id=int(acc["id"]), course_pk=pk, types=type_list)
    console.print(f"[green]已重置 {total} 个任务点为 pending[/green]，重新执行 cx scan 可对齐服务端进度")


@app.command("cookies")
def cmd_cookies(
    name: Optional[str] = typer.Argument(None, help="账号名，留空则全部账号"),
    prune: bool = typer.Option(False, "--prune", help="按白名单清掉风控/埋点 cookie 后重新验证登录"),
    verbose: bool = typer.Option(False, "--verbose"),
):
    """查看（并可清理）会话 cookie。"""
    settings, repo = _boot(verbose)
    names = [name] if name else [a["name"] for a in repo.list_accounts()]
    if not names:
        console.print("[red]还没有账号[/red]")
        raise typer.Exit(code=1)

    async def run() -> None:
        engine = Engine(settings, repo)
        for n in names:
            acc = repo.get_account(n)
            if acc is None:
                console.print(f"[red]{n}: 账号不存在[/red]")
                continue
            async with engine.session_for(acc) as sess:
                rows = auth.cookie_report(sess)
                table = Table(title=f"{n} 的 cookie（{len(rows)} 个）")
                table.add_column("名字")
                table.add_column("值长度")
                table.add_column("白名单")
                for k, ln, core in rows:
                    table.add_row(k, str(ln), "是" if core else "否")
                console.print(table)
                if prune:
                    dropped, ok = await auth.prune_cookies(sess)
                    if not ok:
                        console.print("[yellow]清理后登录失效，已还原原 cookie[/yellow]")
                    else:
                        console.print(f"[green]已清掉 {len(dropped)} 个非白名单 cookie，登录仍然有效[/green]")
                        if dropped:
                            console.print("  " + ", ".join(dropped))
                        repo.save_account(n, cookies=sess.cookies, puid=acc.get("puid"), fid=acc.get("fid"), status="ok")

    asyncio.run(run())


@app.command("status")
def cmd_status():
    """查看任务点总体进度。"""
    engine = _engine()
    repo = engine.repo

    table = Table(title="任务点进度")
    for col in ("类型", "状态", "数量"):
        table.add_column(col)
    for row in repo.task_stats():
        table.add_row(row["type"], row["state"], str(row["n"]))
    console.print(table)

    courses = repo._q(
        """SELECT c.id, c.title, c.clazz_id,
                  (SELECT COUNT(*) FROM chapter ch WHERE ch.course_pk=c.id) AS chapters,
                  (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                    WHERE ch.course_pk=c.id AND t.state='done') AS done,
                  (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                    WHERE ch.course_pk=c.id) AS total
           FROM course c ORDER BY c.id"""
    )
    if courses:
        t2 = Table(title="课程")
        for col in ("id", "课程", "clazz", "章节", "完成/总任务点"):
            t2.add_column(col)
        for c in courses:
            t2.add_row(
                str(c["id"]), c["title"] or "-", c["clazz_id"], str(c["chapters"]),
                f"{c['done']}/{c['total']}",
            )
        console.print(t2)

    # 只有"真的被服务端用人脸拦住"过才提示。
    # 卡片上的 videoFaceCaptureEnc 几乎每张视频卡都有，只是课程开了人脸开关的标记，
    # 实测 84 个视频全程没人脸照样 isPassed，拿它当提示只会误导。
    face_n = repo._q(
        "SELECT COUNT(*) AS n FROM task_point WHERE last_err LIKE '%人脸%' "
        "OR last_err LIKE '%face%' OR last_err LIKE '%活体%'"
    )[0]["n"]
    if face_n:
        console.print(f"[yellow]有 {face_n} 个任务点被服务端要求过人脸 → 用 `cx face me` 过一次[/yellow]")

    doc_n = repo._q(
        "SELECT COUNT(*) AS n FROM task_point WHERE type IN ('document','read') AND state != 'done'"
    )[0]["n"]
    if doc_n:
        console.print(
            f"[dim]文档/阅读 {doc_n} 个待办：服务端 cards 不返回 isPassed，"
            "只能看打卡回执，`cx status` 的数字以本地 done 为准[/dim]"
        )


@app.command("enc")
def cmd_enc(version: Optional[str] = typer.Argument(None, help="v1 或 v2")):
    """查看/设置视频上报 enc 版本。"""
    engine = _engine()
    if version is None:
        console.print(f"当前 enc 版本：[cyan]{engine.repo.meta_get('enc_version', 'v2')}[/cyan]")
        return
    if version not in ("v1", "v2"):
        console.print("[red]只支持 v1 / v2[/red]")
        raise typer.Exit(code=1)
    engine.repo.meta_set("enc_version", version)
    console.print(f"[green]已设置 enc 版本 {version}[/green]")


# ---------------------------------------------------------------- 题库
@quiz_app.command("stats")
def cmd_quiz_stats():
    """题库统计。"""
    engine = _engine()
    stats = engine.repo.quiz_stats()
    console.print(f"题目总数：{stats['total']}")
    for row in stats["by_source"]:
        console.print(f"  {row['source']}: {row['n']}")


@quiz_app.command("export")
def cmd_quiz_export(out: Path = typer.Option(Path("data/quiz.json"), "--out")):
    """导出题库。"""
    engine = _engine()
    rows = engine.repo.quiz_export()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]已导出 {len(rows)} 条到 {out}[/green]")


@quiz_app.command("import")
def cmd_quiz_import(path: Path = typer.Argument(..., help="JSON 文件")):
    """导入题库（qkey/title/answer 必填）。"""
    engine = _engine()
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("items", [])
    n = engine.repo.quiz_import(rows)
    console.print(f"[green]已导入 {n} 条[/green]")


@quiz_app.command("add")
def cmd_quiz_add(
    title: str = typer.Argument(..., help="题干原文"),
    answer: str = typer.Argument(..., help="答案，如 A 或 ABC"),
    qtype: str = typer.Option("single", "--type", help="single/multiple/judgement/completion"),
):
    """手工录一道题。"""
    from .utils import md5, norm_text

    engine = _engine()
    qkey = md5(f"{qtype}|{norm_text(title)}")
    engine.repo.quiz_put(qkey, title=title, options="", qtype=qtype, answer=answer, source="manual")
    console.print(f"[green]已入库[/green] {qkey}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    app()

@ai_app.command("test")
def cmd_ai_test(
    prompt: str = typer.Option("用一句话自我介绍", "--prompt", help="测试用的提示词"),
):
    """验证 [ai] 配置是否可用（base_url / api_key / model）。"""
    import time

    import httpx

    from .ai import endpoint_of

    s = load_settings()
    cfg = s.ai
    if not cfg.api_key:
        console.print("[red]ai.api_key 还没填[/red]，先编辑 config.toml 的 [ai] 段")
        raise typer.Exit(code=1)
    url = endpoint_of(cfg.base_url)
    console.print(f"接口：{url}\n模型：{cfg.model}")
    body = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 64,
    }
    t0 = time.monotonic()
    try:
        r = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
            timeout=cfg.timeout,
        )
    except Exception as exc:
        console.print(f"[red]请求失败：{exc}[/red]")
        raise typer.Exit(code=1)
    dt = (time.monotonic() - t0) * 1000
    if r.status_code >= 400:
        console.print(f"[red]HTTP {r.status_code}：{r.text[:300]}[/red]")
        raise typer.Exit(code=1)
    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
    except Exception:
        console.print(f"[red]响应结构异常：{r.text[:300]}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]OK[/green] {dt:.0f} ms｜{content.strip()[:200]}")
    if usage:
        console.print(f"tokens: prompt {usage.get('prompt_tokens')} / completion {usage.get('completion_tokens')}")


@ai_app.command("ask")
def cmd_ai_ask(
    file: Path = typer.Argument(..., help="题目 JSON（cx quiz pull 导出或手写）"),
    out: Path = typer.Option(Path("data/ai-answers.json"), "--out"),
):
    """离线跑一批题：读题目 JSON → AI 作答 → 写出 {id: answer}，用于先验证准确率。"""
    import asyncio as _asyncio

    from .ai import AiSolver, AiError
    from .api.quiz import Question

    rows = json.loads(Path(file).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("questions", [])
    qs = [
        Question(
            id=str(r.get("id")),
            qtype=r.get("qtype") or "single",
            type_code=str(r.get("type_code") or "0"),
            title=r.get("title") or "",
            options=list(r.get("options") or []),
        )
        for r in rows
    ]
    s = load_settings()
    if not s.ai.enabled:
        console.print("[yellow]ai.enabled=false，仍会照常调用，仅提示一次[/yellow]")

    async def go():
        solver = AiSolver(s.ai)
        try:
            return await solver.solve(qs, work=Path(file).stem)
        except AiError as exc:
            console.print(f"[red]{exc}[/red]")
            return {}
        finally:
            await solver.close()

    got = _asyncio.run(go())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(got, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]{len(got)}/{len(qs)} 题已作答[/green] -> {out}")
    for q in qs:
        console.print(f"  {q.id} {q.qtype:<10} {got.get(q.id, '（空）')}")


@quiz_app.command("pull")
def cmd_quiz_pull(
    account: str = typer.Argument("me", help="账号名"),
    out: Path = typer.Option(Path("data/quiz-pull.json"), "--out", help="题面输出文件"),
    course: int = typer.Option(0, "--course", help="只拉某门课（course_pk），0 为全部"),
):
    """把待做测验的题面拉下来存成 JSON，喂给 `cx ai ask` 先验证 AI 准确率。"""
    import asyncio as _asyncio

    from .api import quiz as quiz_api
    from .task.quiz import work_params

    engine = _engine()
    tasks = [
        t
        for t in engine.repo.iter_tasks(
            account_id=engine.repo.get_account(account)["id"], course_pk=course or None, types=("work",)
        )
    ]
    if not tasks:
        console.print("[yellow]没有待做的测验任务点（work）[/yellow]")
        return

    async def go():
        sess = await engine.session_for(account)
        rows = []
        try:
            for t in tasks:
                params = work_params(dict(t), json.loads(t["att_json"] or "{}"))
                code, body = await quiz_api.fetch_work(sess, params)
                if code != 200:
                    console.print(f"[red]{t['title']} 取题失败 HTTP {code}[/red]")
                    continue
                parsed = quiz_api.parse_work(
                    body,
                    font_map=engine.settings.data_dir / "fonts" / "map.json",
                    unresolved_dir=engine.settings.data_dir / "unresolved",
                )
                for q in parsed.questions:
                    rows.append(
                        {
                            "id": q.id,
                            "qtype": q.qtype,
                            "type_code": q.type_code,
                            "title": q.title,
                            "options": q.options,
                            "work": t["title"],
                        }
                    )
                console.print(f"  {t['title']} → {len(parsed.questions)} 题")
        finally:
            await sess.close()
        return rows

    rows = _asyncio.run(go())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]共 {len(rows)} 题 -> {out}[/green]")
    console.print("下一步： cx ai ask " + str(out))


# cards 页会把播放器装进 iframe（ananas/modules/video/index.html），
# 真正的 video 元素和前端的 faceVerify 弹窗都长在这些 iframe 里。
# studentstudy 页需要 enc 参数（否则"enc校验失败"），所以默认走 cards。
_FACE_MARKS = (
    "人脸识别",
    "请扫码",
    "扫码验证",
    "活体检测",
    "刷脸",
    "faceVerify",
    "capFace",
    "face-check",
    "faceCheck",
)


@app.command("face")
def cmd_face(
    account: str = typer.Argument("me", help="账号名"),
    task: Optional[int] = typer.Option(None, "--task", help="任务点 id（cx status 里看），不填则挑第一个带人脸凭证的"),
    url: Optional[str] = typer.Option(None, "--url", help="你亲眼看到人脸弹窗的那个页面，贴进来就直接用它"),
    wait: int = typer.Option(120, "--wait", help="等弹窗出现的秒数（超时说明这个页面不会触发）"),
    timeout: int = typer.Option(600, "--timeout", help="弹窗出现后等你完成验证的秒数"),
    headless: bool = typer.Option(False, "--headless", help="无头模式（人脸基本用不到）"),
):
    """人工过人脸识别/扫码：打开播放页并自动点播放 → 弹窗出现后你去扫 → 自动导回 cookie。"""
    import asyncio as _asyncio

    from .api.course import CARDS_URL, CARDS_VERSION
    from .auth import launch_browser

    engine = _engine()
    repo = engine.repo
    row = repo.get_account(account)
    if not row:
        console.print(f"[red]没有账号 {account}[/red]")
        raise typer.Exit(code=1)

    if task:
        row_t = repo._q(
            """SELECT t.*, ch.course_pk, ch.knowledge_id AS ch_kid, c.course_id, c.clazz_id, c.cpi
               FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
               JOIN course c ON c.id=ch.course_pk WHERE t.id=?""",
            (task,),
        )
    else:
        row_t = repo._q(
            """SELECT t.*, ch.course_pk, ch.knowledge_id AS ch_kid, c.course_id, c.clazz_id, c.cpi
               FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
               JOIN course c ON c.id=ch.course_pk
               WHERE t.state != 'done' AND t.att_json LIKE '%videoFaceCaptureEnc%'
               ORDER BY t.id LIMIT 1"""
        )
    if not row_t:
        console.print("[yellow]没有找到待办的人脸任务点[/yellow]")
        return
    t = row_t[0]
    kid = t["ch_kid"] or t["knowledge_id"] or ""
    page_url = url or (
        f"{CARDS_URL}?clazzid={t['clazz_id']}&courseid={t['course_id']}"
        f"&knowledgeid={kid}&num=0&ut=s&cpi={t['cpi']}&v={CARDS_VERSION}&mooc2=1"
    )
    console.print(f"任务点：[cyan]{t['title']}[/cyan]  id={t['id']}")
    console.print(f"打开：{page_url}")

    async def go() -> tuple[dict, bool]:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await launch_browser(p, headless=headless)
            ctx = await browser.new_context()
            await ctx.add_cookies(
                [
                    {"name": k, "value": v, "domain": ".chaoxing.com", "path": "/"}
                    for k, v in json.loads(row["cookies"]).items()
                ]
            )
            page = await ctx.new_page()
            face_reqs: list[str] = []

            def on_req(req) -> None:
                u = req.url
                low = u.lower()
                # 我们自己上报的视频心跳参数里就带 videoFaceCaptureEnc，别误判
                if "multimedia/log" in low:
                    return
                path = low.split("?")[0]
                if "face" in path or "capface" in path:
                    face_reqs.append(u)

            page.on("request", on_req)
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)
            # cards 页把播放器装进 iframe，iframe 是异步加载的，先等它们长出来
            await page.wait_for_timeout(6000)

            async def hit() -> str:
                """逐个 frame 找弹窗特征——弹窗在播放器 iframe 里，主框架看不到。"""
                if face_reqs:
                    return f"网络请求 {face_reqs[-1][:60]}"
                for fr in page.frames:
                    try:
                        html = await fr.content()
                    except Exception:
                        continue
                    low = html.lower()
                    for mark in _FACE_MARKS:
                        if mark.lower() in low:
                            return mark
                    try:
                        dom = await fr.evaluate(
                            """() => {
                                const sel = '[class*="face"],[id*="face"],[class*="verify"],[id*="verify"]';
                                for (const el of document.querySelectorAll(sel)) {
                                    const t = (el.innerText || '').trim();
                                    if (t.length > 2 && t.length < 80) return t;
                                }
                                return '';
                            }"""
                        )
                    except Exception:
                        dom = ""
                    if dom:
                        return dom[:40]
                return ""

            async def press_play() -> int:
                """在每个 frame 里点播放按钮 / 直接 play()。"""
                total = 0
                for fr in page.frames:
                    try:
                        total += await fr.evaluate(
                            """() => {
                                let n = 0;
                                for (const v of document.querySelectorAll('video')) {
                                    try { v.muted = true; const r = v.play(); n++; } catch (e) {}
                                }
                                const sel = '.vjs-big-play-button, .ans-vjs-big-play-button, #videoPlay, .play-btn';
                                for (const b of document.querySelectorAll(sel)) {
                                    try { b.click(); n++; } catch (e) {}
                                }
                                return n;
                            }"""
                        )
                    except Exception:
                        continue
                return total

            n = await press_play()
            console.print(f"已尝试启动播放器（命中 {n} 个元素），开始盯弹窗…")

            seen, clean, waited = False, 0, 0
            while waited < timeout:
                await page.wait_for_timeout(5000)
                waited += 5
                if waited <= wait and waited % 15 == 0:
                    m = await press_play()
                    if m:
                        console.print(f"[dim]{waited}s：又点了 {m} 次播放[/dim]")
                mark = await hit()
                if mark:
                    if not seen:
                        seen = True
                        face_reqs.clear()  # 只用于"首次发现"，之后靠弹窗消失判定
                        try:
                            await page.screenshot(path="data/face-dialog.png", full_page=False)
                        except Exception:
                            pass
                        console.print(
                            f"[yellow]第 {waited}s 命中「{mark}」→ 截图 data/face-dialog.png，"
                            "现在请在浏览器窗口里扫码/刷脸[/yellow]"
                        )
                    continue
                if seen:
                    clean += 1
                    if clean >= 2:
                        console.print(f"[green]弹窗已消失（{waited}s），认为验证通过[/green]")
                        break
            else:
                pass

            ok = seen and clean >= 2
            cookies = {c["name"]: c["value"] for c in await ctx.cookies() if "chaoxing" in c["domain"]}
            await browser.close()
            return cookies, ok

    cookies, ok = _asyncio.run(go())
    if cookies:
        repo.save_account(account, cookies, status="ok")
        console.print(f"[green]已更新 {len(cookies)} 个 cookie[/green]")
    if not ok:
        console.print("[yellow]全程没确认到人脸/扫码弹窗，任务点状态未改动。[/yellow]")
        console.print(
            "[yellow]注意：弹窗只在「课程真的开了人脸识别」且「播放器在播」时才出现；"
            "cards 页/暂停状态都不会弹，所以看不到是正常的。[/yellow]"
        )
        console.print(
            "[yellow]要看弹窗：手动在刚打开的窗口里点视频播放并让它播几分钟，"
            "或者用 --url 传你亲眼见到弹窗的那个网址。[/yellow]"
        )
        return
    repo.reset_tasks(account_id=row["id"], course_pk=t["course_pk"])
    console.print("[green]该课程任务点已重置为待办，可重新 cx run[/green]")
