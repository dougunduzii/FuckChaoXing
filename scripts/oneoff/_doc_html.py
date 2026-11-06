"""排障：把某章 cards 页存盘，并定位文档模块相关的 JS 片段。"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.api import course as capi  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.session import CxSession  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402
from chaoxing.utils import text_of  # noqa: E402

KID = sys.argv[1] if len(sys.argv) > 1 else "1223328291"


async def main() -> None:
    s = load_settings()
    r = Repo(s.db_path)
    a = r.get_account("me")
    sess = CxSession(s, cookies=json.loads(a["cookies"]), repo=r, account_id=a["id"], label="me")
    sess.uid, sess.fid = a["puid"], a["fid"]
    c = r.list_courses(account_id=a["id"])[0]
    url = (
        f"{capi.CARDS_URL}?clazzid={c['clazz_id']}&courseid={c['course_id']}"
        f"&knowledgeid={KID}&num=0&ut=s&cpi={c['cpi']}&v={capi.CARDS_VERSION}&mooc2=1"
    )
    resp = await sess.get(url, headers={"Referer": capi.COURSE_LIST_REFERER})
    body = text_of(resp)
    out = Path("data/cards-doc.html")
    out.write_text(body, encoding="utf-8")
    print("saved", out, len(body))

    for kw in ("insertdoc", "ananas/modules", "documentObj", "job/document", "readv2", "getDoc"):
        print(f"\n=== {kw}: {body.count(kw)} hits ===")
        for m in list(re.finditer(re.escape(kw), body))[:2]:
            print(repr(body[max(0, m.start() - 160) : m.start() + 200]))
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
