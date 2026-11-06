"""排障：发一次文档打卡，看服务端到底认不认。用法：python scripts/_doc_probe.py"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.api import course as capi  # noqa: E402
from chaoxing.api import document as dapi  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.session import CxSession  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402


async def main() -> None:
    settings = load_settings()
    repo = Repo(settings.db_path)
    acc = repo.get_account("me")
    sess = CxSession(settings, cookies=json.loads(acc["cookies"]), repo=repo, account_id=acc["id"], label="me")
    sess.uid, sess.fid = acc["puid"], acc["fid"]
    c = repo.list_courses(account_id=acc["id"])[0]

    t = repo._q(
        """SELECT t.*, ch.knowledge_id AS ch_kid FROM task_point t
           JOIN chapter ch ON ch.id=t.chapter_pk
           WHERE t.type='document' AND t.state!='done' ORDER BY t.id LIMIT 1"""
    )[0]
    att = json.loads(t["att_json"] or "{}")
    print("任务点:", t["title"], "| job_id:", t["job_id"], "| other_info:", t["other_info"])
    print("att keys:", list(att))

    jobs, info = await capi.fetch_jobs(sess, c, t["ch_kid"])
    me = [j for j in jobs if j["job_id"] == t["job_id"]]
    print("当前服务端该卡片:", {k: me[0].get(k) for k in ("job_id", "object_id", "type", "passed")} if me else "没找到")

    resp = await dapi.report_document(
        sess,
        course_id=str(c["course_id"]),
        clazz_id=str(c["clazz_id"]),
        knowledge_id=str(t["ch_kid"]),
        jobid=str(t["job_id"]),
        jtoken=str(t["jtoken"] or ""),
    )
    print("document 响应:", resp[0], str(resp[1])[:300])

    jobs2, _ = await capi.fetch_jobs(sess, c, t["ch_kid"])
    me2 = [j for j in jobs2 if j["job_id"] == t["job_id"]]
    print("打卡后 isPassed:", me2[0].get("passed") if me2 else "没找到")
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
