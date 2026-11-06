"""排障：打开一节课的 cards 页，列出每个 frame 的 URL/DOM 特征，找播放器与人脸弹窗容器。

用法：
    python scripts/_cards_probe.py            # 用库里第一个待办视频的章节
    python scripts/_cards_probe.py --kid 123  # 指定 knowledgeid
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.api.course import CARDS_URL, CARDS_VERSION  # noqa: E402
from chaoxing.auth import launch_browser  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402


def pick(repo: Repo, kid: str) -> dict:
    where = "WHERE t.state != 'done' AND t.type='video'"
    if kid:
        where = f"WHERE ch.knowledge_id = '{kid}'"
    return repo._q(
        f"""SELECT t.*, ch.course_pk, ch.knowledge_id AS ch_kid,
                   c.course_id, c.clazz_id, c.cpi
            FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
            JOIN course c ON c.id=ch.course_pk {where} ORDER BY t.id LIMIT 1"""
    )[0]


async def main(kid: str, seconds: int) -> None:
    settings = load_settings()
    repo = Repo(settings.db_path)
    row = repo.get_account("me")
    cookies = json.loads(row["cookies"])
    t = pick(repo, kid)
    url = (
        f"{CARDS_URL}?clazzid={t['clazz_id']}&courseid={t['course_id']}"
        f"&knowledgeid={t['ch_kid']}&num=0&ut=s&cpi={t['cpi']}&v={CARDS_VERSION}&mooc2=1"
    )
    print("cards url:", url)

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await launch_browser(p, headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        await ctx.add_cookies(
            [
                {"name": k, "value": v, "domain": ".chaoxing.com", "path": "/"}
                for k, v in cookies.items()
            ]
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(seconds * 1000)
        print("title:", await page.title())
        html = await page.content()
        print("main html len:", len(html))
        for m in ("人脸", "扫码", "capFace", "face", "验证"):
            print(f"  main marker {m}: {html.count(m)}")
        for i, fr in enumerate(page.frames):
            try:
                fh = await fr.content()
            except Exception as exc:  # noqa: BLE001
                print(f"frame[{i}] <err {exc}> {fr.url[:80]}")
                continue
            print(
                f"frame[{i}] {fr.url[:120]}\n"
                f"    len={len(fh)} video={fh.count('<video')} "
                f"play={fh.count('play-button')} face={fh.count('capFace') + fh.count('人脸')}"
            )
        await page.screenshot(path="data/cards-probe.png", full_page=False)
        print("screenshot: data/cards-probe.png")
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kid", default="")
    ap.add_argument("--seconds", type=int, default=8)
    a = ap.parse_args()
    asyncio.run(main(a.kid, a.seconds))
