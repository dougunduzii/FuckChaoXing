"""超星风控验证码（9010 操作异常）处理。

用法：
    python scripts/verify_captcha.py fetch --out data/captcha.png
    python scripts/verify_captcha.py submit --code ab12
"""

from __future__ import annotations

import argparse
import re
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.config import load_settings  # noqa: E402
from chaoxing.engine import Engine  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402
from chaoxing.utils import text_of  # noqa: E402

PNG_URL = "https://mooc1.chaoxing.com/processVerifyPng.ac"
POST_URL = "https://mooc1.chaoxing.com/html/processVerify.ac"


async def _session():
    s = load_settings()
    r = Repo(s.db_path)
    acc = r.get_account("me")
    if not acc:
        raise SystemExit("还没有账号：cx login me --cookie \"...\"")
    engine = Engine(s, r)
    return s, r, engine.session_for(acc)


async def fetch(out: Path) -> None:
    _, _, sess = await _session()
    async with sess:
        resp = await sess.get(PNG_URL, params={"t": 1234567})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
        print(f"已保存验证码图片：{out}（{len(resp.content)} 字节，HTTP {resp.status_code}）")


async def submit(code: str) -> None:
    _, _, sess = await _session()
    async with sess:
        resp = await sess.post(
            POST_URL,
            data={"app": "0", "ucode": code, "refer": ""},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://mooc1.chaoxing.com/",
            },
        )
        body = text_of(resp)
        print("HTTP", resp.status_code)
        plain = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        print("页面文字:", plain[:300])
        Path("data/verify-resp.html").write_text(body, encoding="utf-8")

        # 验证码过了没有，用一次 cards 请求探一下
        r = Repo(load_settings().db_path)
        c = r.list_courses(r.get_account("me")["id"])[0]
        kid = r._q("SELECT knowledge_id FROM chapter ORDER BY id LIMIT 1")[0]["knowledge_id"]
        probe = await sess.get(
            "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards",
            params={
                "clazzid": c["clazz_id"],
                "courseid": c["course_id"],
                "knowledgeid": kid,
                "num": 0,
                "ut": "s",
                "cpi": c["cpi"],
                "v": "2025-0424-1038-3",
                "mooc2": 1,
            },
        )
        text = text_of(probe)
        ok = "mArg" in text and "验证码" not in text
        print(f"探测 cards：HTTP {probe.status_code}，len {len(text)}，mArg {'有' if 'mArg' in text else '无'} -> {'通过' if ok else '仍需验证'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", default="data/captcha.png")
    s = sub.add_parser("submit")
    s.add_argument("--code", required=True)
    args = ap.parse_args()
    if args.cmd == "fetch":
        asyncio.run(fetch(Path(args.out)))
    else:
        asyncio.run(submit(args.code))


if __name__ == "__main__":
    main()
