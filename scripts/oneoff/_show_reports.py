import datetime as dt
from collections import Counter

from chaoxing.config import load_settings
from chaoxing.store.repo import Repo

s = load_settings()
r = Repo(s.db_path)
rows = r._q(
    "SELECT ts, task_pk, status FROM runs WHERE path LIKE '%multimedia/log%' "
    "ORDER BY id DESC LIMIT 40"
)
print("最近 40 条上报：")
for x in reversed(rows):
    t = dt.datetime.fromtimestamp(x["ts"]).strftime("%H:%M:%S")
    print(f"  {t}  task={x['task_pk']}  http={x['status']}")

print()
print("按 task 分组计数:", Counter(x["task_pk"] for x in rows))
