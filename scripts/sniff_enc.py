"""用真实浏览器抓一次视频上报请求，反推当前 enc 版本。

用法：
    python scripts/sniff_enc.py --cookie data/cookie.json \
        --url "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards?clazzid=..&courseid=..&knowledgeid=..&num=0&ut=s&cpi=..&v=2025-0424-1038-3&mooc2=1"

流程：注入 cookie → 打开发起页 → 你手动点一下播放 → 脚本捕获第一个
multimedia/log 请求，用它的参数分别按 v1/v2 重算 enc 并比对，命中即写入 meta。

也可以直接传 --url 为某节课的 cards 页；若只想验证登录，可加 --check-only。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chaoxing import auth  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.crypto import enc as enc_mod  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402

LOG_MARK = "multimedia/log/a/"


def parse_report_url(url: str) -> dict[str, str]:
    qs = parse_qs(urlparse(url).query)
    return {k: v[0] for k, v in qs.items() if v}


def match_version(params: dict[str, str]) -> tuple[str | None, dict[str, str]]:
    clazz = params.get("clazzId", "")
    uid = params.get("userid", "")
    jobid = params.get("jobid", "")
    oid = params.get("objectId", "")
    pt = int(params.get("playingTime") or 0)
    dur = int(params.get("duration") or 0)
    remote = (params.get("enc") or "").lower()
    candidates = enc_mod.candidate_encs(clazz, uid, jobid, oid, pt, dur)
    for name, value in candidates.items():
        if value == remote:
            return name, candidates
    return None, candidates


async def run_sniff(url: str, cookie_file: Path, timeout: float, headless: bool) -> int:
    from playwright.async_api import async_playwright

    cookies_raw = json.loads(Path(cookie_file).read_text(encoding="utf-8"))
    captured: asyncio.Future = asyncio.get_event_loop().create_future()

    async with async_playwright() as p:
        browser = await auth.launch_browser(p, headless=headless)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        await context.add_cookies(
            [
                {"name": k, "value": str(v), "domain": ".chaoxing.com", "path": "/"}
                for k, v in cookies_raw.items()
            ]
        )

        def on_request(req) -> None:
            if LOG_MARK in req.url and not captured.done():
                captured.set_result(req.url)

        page = await context.new_page()
        page.on("request", on_request)
        print(f"打开 {url}")
        await page.goto(url, wait_until="domcontentloaded")
        print("请在页面里点一下播放（若没有播放器，说明这页没有视频任务点）…")

        try:
            found = await asyncio.wait_for(captured, timeout=timeout)
        except asyncio.TimeoutError:
            print(f"{timeout}s 内没有捕获到上报请求，放弃")
            await browser.close()
            return 2
        await browser.close()

    params = parse_report_url(found)
    print("\n捕获上报参数：")
    for k in ("clazzId", "userid", "jobid", "objectId", "playingTime", "duration", "dtype", "enc"):
        print(f"  {k:>12} = {params.get(k, '')}")

    version, candidates = match_version(params)
    print("\n本地重算：")
    for name, value in candidates.items():
        print(f"  {name}: {value}  {'← 命中' if name == version else ''}")

    if not version:
        print("\n两个版本都没命中，超星可能又换算法了。上报参数已打印，可据此扩展 crypto/enc.py。")
        return 3

    settings = load_settings()
    repo = Repo(settings.db_path)
    repo.meta_set("enc_version", version)
    repo.close()
    print(f"\n已写入 meta: enc_version = {version}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="反推超星视频上报 enc 版本")
    ap.add_argument("--url", required=True, help="某节课的任务点页面 URL（含视频）")
    ap.add_argument("--cookie", default="data/cookie.json", help="cookie JSON 文件")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    cookie_path = Path(args.cookie)
    if not cookie_path.exists():
        print(f"找不到 cookie 文件 {cookie_path}，先执行 cx login <name> --browser")
        return 1
    return asyncio.run(run_sniff(args.url, cookie_path, args.timeout, args.headless))


if __name__ == "__main__":
    raise SystemExit(main())
