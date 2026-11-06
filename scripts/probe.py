"""排障探针：把真实页面抓下来存盘，并打印结构特征，用来对拍解析器。

用法：
    python scripts/probe.py chapters            # 每门课各抓一次章节页
    python scripts/probe.py chapters --course 1  # 只抓 course 表 id=1
    python scripts/probe.py cards --course 1 --kid 123456
    python scripts/probe.py urls --course 1      # 只试各种 URL 变体，看哪个有数据
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chaoxing import auth  # noqa: E402
from chaoxing.api import course as course_api  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.session import CxSession  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402
from chaoxing.utils import text_of  # noqa: E402

OUT = Path("data/debug")

MARKERS = [
    "chapter_unit",
    "knowledgeJobCount",
    "bntHoverTips",
    "cur1",
    "任务点",
    "已完成",
    "登录",
    "验证",
    "未开放",
    "课程不存在",
    "没有权限",
    'id="cur',
]


def session_for(settings, repo, account) -> CxSession:
    sess = CxSession(
        settings,
        cookies=json.loads(account["cookies"] or "{}"),
        repo=repo,
        account_id=int(account["id"]),
        label=str(account["name"]),
    )
    sess.uid = str(account.get("puid") or "")
    sess.fid = str(account.get("fid") or "")
    return sess


def describe(html: str) -> str:
    lines = [f"len={len(html)}"]
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    lines.append(f"title={title.group(1).strip()[:80] if title else '(none)'}")
    hits = [f"{m}×{html.count(m)}" for m in MARKERS if m in html]
    lines.append("markers: " + (", ".join(hits) or "(none)"))
    ids = re.findall(r'id="cur(\d+)"', html)
    lines.append(f"cur数字id: {len(ids)} 个 {ids[:6]}")
    div_ids = re.findall(r'<div[^>]+id="([^"]{1,40})"', html)
    lines.append(f"div#id 样本: {div_ids[:12]}")
    return "\n  ".join(lines)


def save(name: str, html: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(html, encoding="utf-8")
    return p


async def cmd_chapters(settings, repo, account, args) -> int:
    courses = repo.list_courses(account_id=int(account["id"]))
    if args.course:
        courses = [c for c in courses if int(c["id"]) == args.course]
    if not courses:
        print("库里没有课程，先跑 cx scan")
        return 1

    async with session_for(settings, repo, account) as sess:
        for c in courses:
            course = {
                "course_id": c["course_id"],
                "clazz_id": c["clazz_id"],
                "cpi": c["cpi"],
                "title": c["title"],
            }
            params = {
                "courseid": c["course_id"],
                "clazzid": c["clazz_id"],
                "cpi": c["cpi"] or "",
                "ut": "s",
            }
            resp = await sess.get(
                course_api.CHAPTER_URL,
                params=params,
                headers={"Referer": "https://mooc2-ans.chaoxing.com/"},
                audit=False,
            )
            html = text_of(resp)
            path = save(f"chapters-{c['course_id']}-{c['clazz_id']}.html", html)
            print(f"\n=== {c['title']} (course={c['course_id']} clazz={c['clazz_id']} cpi={c['cpi']})")
            print(f"  status={resp.status_code} url={resp.request.url}")
            print(f"  {describe(html)}")
            print(f"  saved -> {path}")
            parsed = course_api.parse_chapters(html)
            print(f"  parse_chapters -> {len(parsed)} 条 {parsed[:3]}")
    return 0


async def cmd_cards(settings, repo, account, args) -> int:
    courses = repo.list_courses(account_id=int(account["id"]))
    if args.course:
        courses = [c for c in courses if int(c["id"]) == args.course]
    if not courses:
        print("库里没有课程")
        return 1
    c = courses[0]
    course = {"course_id": c["course_id"], "clazz_id": c["clazz_id"], "cpi": c["cpi"], "title": c["title"]}
    async with session_for(settings, repo, account) as sess:
        for kid in args.kid:
            params = {
                "clazzid": c["clazz_id"],
                "courseid": c["course_id"],
                "knowledgeid": kid,
                "num": 0,
                "ut": "s",
                "cpi": c["cpi"] or "",
                "v": course_api.CARDS_VERSION,
                "mooc2": 1,
            }
            resp = await sess.get(
                course_api.CARDS_URL,
                params=params,
                headers={"Referer": "https://mooc2-ans.chaoxing.com/"},
                audit=False,
            )
            body = text_of(resp)
            path = save(f"cards-{kid}.html", body)
            print(f"\n=== kid={kid} status={resp.status_code}")
            print(f"  {describe(body)}")
            print(f"  saved -> {path}")
            jobs, info = course_api.parse_cards(body)
            print(f"  parse_cards -> {len(jobs)} 个任务点, info={list(info)[:10]}")
    return 0


async def cmd_urls(settings, repo, account, args) -> int:
    """同一门课试多种 URL/参数变体，看哪种真能拿到章节。"""
    courses = repo.list_courses(account_id=int(account["id"]))
    if args.course:
        courses = [c for c in courses if int(c["id"]) == args.course]
    c = courses[0]
    variants = [
        ("mooc2 studentcourse", "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse",
         {"courseid": c["course_id"], "clazzid": c["clazz_id"], "cpi": c["cpi"] or "", "ut": "s"},
         {"Referer": "https://mooc2-ans.chaoxing.com/"}),
        ("mooc1 studentcourse", "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentcourse",
         {"courseid": c["course_id"], "clazzid": c["clazz_id"], "cpi": c["cpi"] or "", "ut": "s"},
         {"Referer": "https://mooc1.chaoxing.com/"}),
        ("studentcourse 无 ut", "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse",
         {"courseid": c["course_id"], "clazzid": c["clazz_id"], "cpi": c["cpi"] or ""},
         {"Referer": "https://mooc2-ans.chaoxing.com/"}),
        ("studentcourse 带 enc", "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse",
         {"courseid": c["course_id"], "clazzid": c["clazz_id"], "cpi": c["cpi"] or "", "ut": "s", "enc": ""},
         {"Referer": "https://mooc2-ans.chaoxing.com/"}),
        ("interaction", "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction",
         {"courseid": c["course_id"], "clazzid": c["clazz_id"], "cpi": c["cpi"] or ""},
         {"Referer": "https://mooc2-ans.chaoxing.com/"}),
    ]
    async with session_for(settings, repo, account) as sess:
        for label, url, params, headers in variants:
            try:
                resp = await sess.get(url, params=params, headers=headers, audit=False)
            except Exception as exc:  # noqa: BLE001
                print(f"\n--- {label}: 请求失败 {exc}")
                continue
            html = text_of(resp)
            parsed = course_api.parse_chapters(html)
            print(f"\n--- {label}  status={resp.status_code} len={len(html)} 解析出 {len(parsed)} 章")
            print(f"    url={resp.request.url}")
            print("    " + describe(html).replace("\n  ", "\n    "))
            if parsed:
                save(f"hit-{label.replace(' ', '_')}.html", html)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["chapters", "cards", "urls"])
    ap.add_argument("--account", default="me")
    ap.add_argument("--course", type=int)
    ap.add_argument("--kid", nargs="*", default=[])
    args = ap.parse_args()

    settings = load_settings()
    repo = Repo(settings.db_path)
    account = repo.get_account(args.account)
    if account is None:
        print(f"没有账号 {args.account}")
        return 1

    async def run() -> int:
        sess = session_for(settings, repo, account)
        try:
            msg = await auth.ensure_login(sess, args.account, repo)
        finally:
            await sess.close()
        if msg is None:
            print("会话失效，重新 cx login")
            return 1
        print(f"登录态 OK puid={msg.get('puid')} name={msg.get('name', '')}")
        handler = {"chapters": cmd_chapters, "cards": cmd_cards, "urls": cmd_urls}[args.cmd]
        account2 = repo.get_account(args.account)
        return await handler(settings, repo, account2, args)

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
