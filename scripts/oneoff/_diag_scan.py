import asyncio
from collections import Counter

from chaoxing.config import load_settings
from chaoxing.engine import Engine
from chaoxing.store.repo import Repo


async def main() -> None:
    s = load_settings()
    r = Repo(s.db_path)
    e = Engine(s, r)
    rep = await e.scan("me")
    print("scan:", rep)
    rows = r.iter_tasks(states=("pending", "done", "failed", "skip_locked", "running"))
    print(Counter((t["type"], t["state"]) for t in rows))


asyncio.run(main())
