"""排障：抓文档任务点真实上报请求。用法：python scripts/_doc_sniff.py [kid] [seconds]"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.api import course as capi  # noqa: E402
from chaoxing.auth import launch_browser  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402

KEYS = ("job", "log", "document", "readv2", "doc")


async def main(kid: str, seconds: int) -> None:
    settings = load_settings()
    repo = Repo(settings.db_path)
    acc = repo.get_account("me")
    c = repo.list_courses(account_id=acc["id"])[0]
    if not kid:
        kid = repo._q(
            """SELECT ch.knowledge_id kid FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
               WHERE t.type='document' AND t.state!='done' LIMIT 1"""
        )[0]["kid"]

    url = (
        f"{capi.CARDS_URL}?clazzid={c['clazz_id']}&courseid={c['course_id']}"
        f"&knowledgeid={kid}&num=0&ut=s&cpi={c['cpi']}&v={capi.CARDS_VERSION}&mooc2=1"
    )
    print("cards:", url)
    hits: list[str] = []

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await launch_browser(p, headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        await ctx.add_cookies(
            [
                {"name": k, "value": v, "domain": ".chaoxing.com", "path": "/"}
                for k, v in json.loads(acc["cookies"]).items()
            ]
        )
        page = await ctx.new_page()

        def on_req(req) -> None:
            u = req.url
            low = u.lower()
            if "chaoxing.com" not in low:
                return
            if "multimedia/log" in low or any(k in low.split("?")[0] for k in KEYS):
                body = ""
                try:
                    body = (req.post_data or "")[:200]
                except Exception:
                    pass
                hits.append(f"{req.method} {u[:300]}" + (f"  BODY={body}" if body else ""))

        page.on("request", on_req)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(6000)

        # 点开文档卡片：超星 cards 页左栏是附件列表，文档条目通常在 .prev_ul li 或 a 里
        clicked = 0
        for sel in (
            "li:has-text('.docx')",
            "li:has-text('.pdf')",
            ".prev_ul li",
            ".cards_box .item",
            "text=线性方程组与高斯消元法",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count():
                    await loc.click(timeout=5000)
                    clicked += 1
                    print("clicked:", sel)
                    break
            except Exception as exc:  # noqa: BLE001
                print("click fail:", sel, type(exc).__name__)
        print("clicked count:", clicked)

        # 也试着在 iframe 里滚一下文档容器，触发阅读上报
        for fr in page.frames[1:]:
            try:
                await fr.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass

        await page.wait_for_timeout(seconds * 1000)
        await page.screenshot(path="data/doc-sniff.png")
        await browser.close()

    print(f"\n共捕获 {len(hits)} 条：")
    for h in hits[:40]:
        print(" ", h)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "", int(sys.argv[2]) if len(sys.argv) > 2 else 25))
