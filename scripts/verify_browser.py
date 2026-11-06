"""在真实浏览器里过超星的风控验证码（9010 操作异常）。

流程：
    1) 用库里的 cookie 起一个系统 Chrome
    2) 打开 cards 页，如果被拦，截取验证码图片
    3) 轮询 data/code.txt，读到 4 位验证码后填入并提交
    4) 把浏览器里的 cookie 全量导回 data/cookies.json，供 cx login --cookie-file 使用

用法：
    python scripts/verify_browser.py [--url URL] [--headless]
"""

from __future__ import annotations

import argparse
import base64
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing import auth  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402

CODE_FILE = Path("data/code.txt")
SHOT = Path("data/browser-captcha.png")
COOKIE_OUT = Path("data/cookies.json")


async def main(url: str, headless: bool, attempts: int = 6) -> None:
    from playwright.async_api import async_playwright

    s = load_settings()
    r = Repo(s.db_path)
    acc = r.get_account("me")
    if not acc:
        raise SystemExit("还没有账号：cx login me --cookie \"...\"")
    cookies = json.loads(acc["cookies"])

    CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODE_FILE.unlink(missing_ok=True)

    async with async_playwright() as p:
        browser = await auth.launch_browser(p, headless=headless)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        await ctx.add_cookies(
            [
                {"name": k, "value": v, "domain": ".chaoxing.com", "path": "/"}
                for k, v in cookies.items()
            ]
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        html = await page.content()
        title = await page.title()
        print(f"页面标题: {title} | 需要验证: {'9010' in html or 'processVerify' in html}")

        if "processVerify" in html or "9010" in html:
            for attempt in range(1, attempts + 1):
                img = page.locator("#ccc")
                if await img.count():
                    await img.screenshot(path=str(SHOT))
                    # 元素截图会按 CSS 尺寸缩水（104x45），原图是 140x50 更清楚：
                    # 直接用页面内 fetch 把那根 src 的字节抓下来
                    src = await img.get_attribute("src") or ""
                    if src:
                        if src.startswith("/"):
                            src = "https://mooc1.chaoxing.com" + src
                        try:
                            b64 = await page.evaluate(
                                """async (u) => {
                                    const r = await fetch(u, {credentials: 'include'});
                                    const buf = await r.arrayBuffer();
                                    const a = new Uint8Array(buf);
                                    let s = '';
                                    for (let i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
                                    return btoa(s);
                                }""",
                                src,
                            )
                            (Path("data/browser-captcha-raw.png")).write_bytes(base64.b64decode(b64))
                        except Exception as exc:  # pragma: no cover
                            print("抓原图失败:", exc)
                    print(f"[第 {attempt} 次] 验证码已截图: {SHOT}（原图 data/browser-captcha-raw.png）")
                else:
                    await page.screenshot(path=str(SHOT), full_page=True)
                    print(f"[第 {attempt} 次] 未找到 #ccc，整页截图: {SHOT}")

                CODE_FILE.unlink(missing_ok=True)
                print(f"把 4 位验证码写进 {CODE_FILE}（只写 4 个字符）")
                code = ""
                for _ in range(180):
                    await asyncio.sleep(1)
                    if CODE_FILE.exists():
                        code = CODE_FILE.read_text(encoding="utf-8").strip()
                        if len(code) >= 4:
                            break
                if len(code) < 4:
                    print("没等到验证码，退出")
                    await browser.close()
                    return
                print(f"提交验证码: {code}")
                await page.fill("#ucode", code)
                await page.press("#ucode", "Enter")
                await page.wait_for_timeout(4000)
                html = await page.content()
                blocked = "processVerify" in html or "9010" in html
                print("提交后是否仍在验证页:", blocked)
                if not blocked:
                    break

        jar = await ctx.cookies()
        data = {c["name"]: c["value"] for c in jar if "chaoxing" in (c.get("domain") or "")}
        COOKIE_OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导出 {len(data)} 个 cookie -> {COOKIE_OUT}")
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--attempts", type=int, default=6)
    a = ap.parse_args()

    if not a.url:
        cfg = load_settings()
        repo = Repo(cfg.db_path)
        acc = repo.get_account("me")
        c = repo.list_courses(acc["id"])[0]
        kid = repo._q("SELECT knowledge_id FROM chapter ORDER BY id LIMIT 1")[0]["knowledge_id"]
        a.url = (
            "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
            f"?clazzid={c['clazz_id']}&courseid={c['course_id']}&knowledgeid={kid}"
            f"&num=0&ut=s&cpi={c['cpi']}&v=2025-0424-1038-3&mooc2=1"
        )
    asyncio.run(main(a.url, a.headless, a.attempts))
