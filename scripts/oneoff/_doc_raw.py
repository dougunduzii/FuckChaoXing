"""排障：打印某章 cards 页里 document 卡片的原始 JSON 字段。用法：python scripts/_doc_raw.py [kid]"""

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


async def main(kid: str) -> None:
    settings = load_settings()
    repo = Repo(settings.db_path)
    acc = repo.get_account("me")
    sess = CxSession(settings, cookies=json.loads(acc["cookies"]), repo=repo, account_id=acc["id"], label="me")
    sess.uid, sess.fid = acc["puid"], acc["fid"]
    c = repo.list_courses(account_id=acc["id"])[0]

    if not kid:
        row = repo._q(
            """SELECT ch.knowledge_id kid FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
               WHERE t.type='document' AND t.state!='done' LIMIT 1"""
        )[0]
        kid = row["kid"]
    print("chapter:", kid)

    for num in range(0, 3):
        url = (
            f"{capi.CARDS_URL}?clazzid={c['clazz_id']}&courseid={c['course_id']}"
            f"&knowledgeid={kid}&num={num}&ut=s&cpi={c['cpi']}&v={capi.CARDS_VERSION}&mooc2=1"
        )
        resp = await sess.get(url, headers={"Referer": capi.COURSE_LIST_REFERER})
        body = text_of(resp)
        raw = capi._extract_marg(body)
        if not raw:
            continue
        marg = json.loads(raw)
        atts = marg.get("attachments") or []
        if not atts:
            continue
        print(f"num={num} attachments={len(atts)}")
        for a in atts:
            prop = a.get("property") or {}
            name = prop.get("name") or a.get("name") or ""
            if not str(name).lower().endswith(capi._DOC_SUFFIXES):
                continue
            print("\n--- 文档卡片 ---")
            print(json.dumps(a, ensure_ascii=False, indent=2)[:1800])
            print("defaults keys:", list((marg.get("defaults") or {}).keys()))
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
