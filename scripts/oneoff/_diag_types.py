import asyncio
import json

from chaoxing.api import course as capi
from chaoxing.config import load_settings
from chaoxing.engine import Engine
from chaoxing.store.repo import Repo
from chaoxing.utils import text_of


async def main() -> None:
    s = load_settings()
    r = Repo(s.db_path)
    acc = r.get_account("me")
    e = Engine(s, r)
    c = r.list_courses(acc["id"])[0]
    kids = [x["knowledge_id"] for x in r._q("SELECT knowledge_id FROM chapter ORDER BY id LIMIT 6")]
    async with e.session_for(acc) as sess:
        for kid in kids:
            jobs, info = await capi.fetch_jobs(sess, c, kid)
            detail = []
            for j in jobs:
                detail.append(f"{j['type']}:{j.get('title', '')[:18]}")
            print(kid, "->", len(jobs), detail)


asyncio.run(main())
