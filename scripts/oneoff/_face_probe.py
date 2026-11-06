"""排障：看看 studentstudy 页到底加载了什么（frames / video / 播放按钮）。

用法：python scripts/_face_probe.py --url "https://..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.auth import launch_browser  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402

STUDY_URL = "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudy"


def url_of(row) -> str:
    kid = row["ch_kid"]
    return (
        f"{STUDY_URL}?chapterId={kid}&courseId={row['course_id']}"
        f"&clazzid={row['clazz_id']}&cpi={row['cpi']}&ut=s&mooc2=1"
    )


async def main(url: str, seconds: int) -> None:
    settings = load_settings()
    repo = Repo(settings.db_path)
    row = repo.get_account("me")
    cookies = json.loads(row["cookies"])

    t = repo._q(
        """SELECT t.*, ch.course_pk, ch.knowledge_id AS ch_kid, c.course_id, c.clazz_id, c.cpi
           FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
           JOIN course c ON c.id=ch.course_pk
           WHERE t.state != 'done' AND t.type='video' ORDER BY t.id LIMIT 1"""
    )[0]

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
        target = url or url_of(t)
        print("goto:", target)
        await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(seconds * 1000)
        print("final url:", page.url)
        print("title:", await page.title())
        html = await page.content()
        print("main html len:", len(html))
        for m in ("人脸", "扫码", "video", "iframe", "验证码", "登录"):
            print(f"  marker {m}: {html.count(m)}")
        for i, fr in enumerate(page.frames):
            try:
                fh = await fr.content()
            except Exception as exc:  # noqa: BLE001
                print(f"frame[{i}] {fr.url[:90]} <err {exc}>")
                continue
            print(
                f"frame[{i}] {fr.url[:110]} len={len(fh)} "
                f"video={fh.count('<video')} play={fh.count('play-button')} "
                f"face={fh.count('人脸') + fh.count('capFace')}"
            )
        await page.screenshot(path="data/face-probe.png", full_page=False)
        print("screenshot: data/face-probe.png")
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--seconds", type=int, default=8)
    a = ap.parse_args()
    asyncio.run(main(a.url, a.seconds))
