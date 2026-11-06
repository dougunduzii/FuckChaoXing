import asyncio

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
    kid = r._q("SELECT knowledge_id FROM chapter ORDER BY id LIMIT 1")[0]["knowledge_id"]
    print("course:", c["title"], "| kid:", kid)
    async with e.session_for(acc) as sess:
        params = {
            "clazzid": c["clazz_id"],
            "courseid": c["course_id"],
            "knowledgeid": kid,
            "num": 0,
            "ut": "s",
            "cpi": c["cpi"],
            "v": capi.CARDS_VERSION,
            "mooc2": 1,
        }
        resp = await sess.get(capi.CARDS_URL, params=params)
        body = text_of(resp)
        print("HTTP", resp.status_code, "len", len(body))
        print("mArg 出现次数:", body.count("mArg"))
        jobs, info = capi.parse_cards(body)
        print("jobs:", len(jobs), "| info keys:", sorted(info)[:8])
        print("attachments 次数:", body.count("attachments"))
        idx = body.find("attachments")
        print("TEXT>", __import__("re").sub(r"\s+", " ", __import__("re").sub(r"<(script|style)[\s\S]*?</\1>", " ", body))[:900], "<TEXT")
        print("正文:", __import__("re").sub(r"<[^>]+>", " ", body)[:600])
        print("片段:", body[max(0, idx - 200) : idx + 200].replace("\n", " ")[:400])


asyncio.run(main())
