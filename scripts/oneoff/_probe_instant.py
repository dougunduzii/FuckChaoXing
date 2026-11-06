import asyncio, json
from chaoxing.api import video as vapi
from chaoxing.config import load_settings
from chaoxing.store.repo import Repo
from chaoxing.engine import Engine


async def main():
    s = load_settings(); r = Repo(s.db_path); acc = r.get_account("me")
    e = Engine(s, r)
    tasks = [t for t in r.iter_tasks(account_id=acc["id"], types=("video",)) if t["state"] == "pending"][:2]
    async with e.session_for(acc) as sess:
        for t in tasks:
            st = await vapi.fetch_status(sess, t["object_id"], sess.fid)
            if not st:
                print("no status", t["title"][:20]); continue
            dur = int(st.get("duration") or t["duration"])
            att = json.loads(t["att_json"] or "{}")
            params = vapi.build_params(
                clazz_id=t["clazz_id"], course_id=t["course_id"], jobid=t["job_id"],
                object_id=t["object_id"], other_info=t["other_info"] or "",
                userid=str(sess.uid), playing_time=dur, duration=dur,
                dtype="Video", version="v2", att=att, rt=1,
            )
            ok, code, body = await vapi.report(
                sess, cpi=t["cpi"], dtoken=st["dtoken"], params=params, dtype="Video",
                lane="probe", audit_task=int(t["id"]),
            )
            print(f"{t['title'][:24]} dur={dur} -> {code} {str(body)[:120]}")

asyncio.run(main())
