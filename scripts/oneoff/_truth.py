"""对账：服务端 cards 页的真实 isPassed 统计 vs 本地库。

只读，不改库。用法：python scripts/_truth.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.api import course as capi  # noqa: E402
from chaoxing.config import load_settings  # noqa: E402
from chaoxing.session import CxSession  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402


async def main() -> None:
    settings = load_settings()
    repo = Repo(settings.db_path)
    acc = repo.get_account("me")
    sess = CxSession(settings, cookies=json.loads(acc["cookies"]), repo=repo, account_id=acc["id"], label="me")
    sess.uid, sess.fid = acc["puid"], acc["fid"]

    courses = repo.list_courses(account_id=acc["id"])
    grand = Counter()
    per_type_pass: Counter = Counter()
    per_type_all: Counter = Counter()
    titles_missing: list[str] = []

    for c in courses:
        chapters = repo.list_chapters(c["id"])
        print(f"课程 {c['title']}：{len(chapters)} 章")
        for ch in chapters:
            jobs, info = await capi.fetch_jobs(sess, c, ch["knowledge_id"])
            if not jobs:
                print(f"  章 {ch['knowledge_id']} 解析 0 个（job_count={ch['job_count']}）")
                continue
            passed = sum(1 for j in jobs if j.get("passed"))
            for j in jobs:
                per_type_all[j["type"]] += 1
                if j.get("passed"):
                    per_type_pass[j["type"]] += 1
            grand["all"] += len(jobs)
            grand["passed"] += passed
            if passed != len(jobs):
                print(f"  章 {ch['knowledge_id']}: {passed}/{len(jobs)} 已过")
            for j in jobs:
                if j.get("passed"):
                    row = repo._q("SELECT state FROM task_point WHERE job_id=?", (j["job_id"],))
                    if not row:
                        titles_missing.append(f"{ch['knowledge_id']} {j['title']}")

    print("\n服务端真实统计：")
    print(f"  任务点合计 {grand['all']}，isPassed {grand['passed']}")
    print(f"  按类型合计 {dict(per_type_all)}")
    print(f"  按类型已过 {dict(per_type_pass)}")

    rows = repo._q(
        "SELECT type, state, COUNT(*) n FROM task_point GROUP BY type, state ORDER BY type, state"
    )
    print("\n本地库：")
    for r in rows:
        print(f"  {r['type']:<10} {r['state']:<8} {r['n']}")
    print(f"  本地行数 {repo._q('SELECT COUNT(*) n FROM task_point')[0]['n']}")
    if titles_missing:
        print(f"\n服务端已过、但库里没有的任务点 {len(titles_missing)} 个，前 5 个：")
        for t in titles_missing[:5]:
            print("   ", t)
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
