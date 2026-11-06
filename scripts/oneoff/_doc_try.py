"""排障：对同一个文档任务点尝试多种上报方式，每次之后回读 isPassed。

用法：python scripts/_doc_try.py [job_id]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.api import course as capi  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.session import CxSession  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402
from chaoxing.utils import text_of  # noqa: E402

DOC = "https://mooc1.chaoxing.com/ananas/job/document"
READ = "https://mooc1.chaoxing.com/ananas/job/readv2"


def variants(base: dict, extra: dict) -> list[tuple[str, str, dict]]:
    b = base
    return [
        ("A 现状 /ananas/job/document", DOC, {**b}),
        ("B 带 objectid+mid+enc", DOC, {**b, **extra}),
        ("C 带 objectid 无 enc", DOC, {**b, "objectid": extra.get("objectid", "")}),
        ("D /ananas/job/readv2", READ, {k: v for k, v in b.items() if k != "_dc"}),
        ("E 带 nodeId", DOC, {**b, "nodeId": extra.get("nodeid", "")}),
    ]


async def main(job_id: str) -> None:
    s = load_settings()
    r = Repo(s.db_path)
    a = r.get_account("me")
    sess = CxSession(s, cookies=json.loads(a["cookies"]), repo=r, account_id=a["id"], label="me")
    sess.uid, sess.fid = a["puid"], a["fid"]
    c = r.list_courses(account_id=a["id"])[0]

    row = r._q(
        """SELECT t.*, ch.knowledge_id kid FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
           WHERE t.type='document' AND t.state!='done'
             AND (? = '' OR t.job_id = ?) ORDER BY t.id LIMIT 2""",
        (job_id, job_id),
    )
    for t in row:
        att = json.loads(t["att_json"] or "{}")
        print(f"\n===== {t['title']} (job {t['job_id']}) =====")
        base = {
            "jobid": t["job_id"],
            "knowledgeid": t["kid"],
            "courseid": str(c["course_id"]),
            "clazzid": str(c["clazz_id"]),
            "jtoken": t["jtoken"] or "",
            "_dc": str(int(time.time() * 1000)),
        }
        extra = {
            "objectid": att.get("objectid") or "",
            "mid": att.get("mid") or "",
            "enc": att.get("enc") or "",
            "nodeid": (t["other_info"] or "").split("-")[0].replace("nodeId_", ""),
        }
        print("base:", base)
        print("extra:", extra)

        for name, url, params in variants(base, extra):
            resp = await sess.get(url, params=params, headers={"Referer": "https://mooc1.chaoxing.com/"},
                                  retries=0)
            body = text_of(resp)[:160].replace("\n", " ")
            jobs, _ = await capi.fetch_jobs(sess, c, t["kid"])
            me = [j for j in jobs if j["job_id"] == t["job_id"]]
            passed = me[0].get("passed") if me else None
            print(f"  {name:<28} http={resp.status_code} passed={passed} body={body}")
            await asyncio.sleep(1.0)
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
