import asyncio

from chaoxing.api import course as capi
from chaoxing.config import load_settings
from chaoxing.engine import Engine
from chaoxing.store.repo import Repo


async def main() -> None:
    s = load_settings()
    r = Repo(s.db_path)
    acc = r.get_account("me")
    e = Engine(s, r)
    c = r.list_courses(acc["id"])[0]
    chapters = r._q("SELECT knowledge_id, title, job_count FROM chapter ORDER BY id")
    async with e.session_for(acc) as sess:
        tot = 0
        for ch in chapters[:8]:
            jobs, info = await capi.fetch_jobs(sess, c, ch["knowledge_id"], expect=ch["job_count"])
            types = [j["type"] for j in jobs]
            tot += len(jobs)
            print(f"kid={ch['knowledge_id']} job_count={ch['job_count']} -> {len(jobs)} {types}")
        print("合计:", tot)


asyncio.run(main())
