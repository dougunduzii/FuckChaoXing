"""同账号并发跑两个视频，验证多路进度上报会不会互相干扰。

用法：
    python scripts/concurrency_probe.py --workers 2

只挑最短的几个待办视频，跑完打印每条的结果与总墙钟耗时。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.config import load_settings  # noqa: E402
from chaoxing.engine import Engine  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402
from chaoxing.task.video import VideoTask  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="me")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    settings = load_settings()
    repo = Repo(settings.db_path)
    account = repo.get_account(args.account)
    if account is None:
        raise SystemExit(f"账号 {args.account} 不存在")

    rows = [t for t in repo.iter_tasks(types=("video",)) if t["state"] != "done"]
    rows.sort(key=lambda t: t["duration"] or 10**9)
    picked = rows[: args.workers]
    if not picked:
        raise SystemExit("没有可测的视频")

    engine = Engine(settings, repo)
    sem = asyncio.Semaphore(max(1, args.workers))

    async with engine.session_for(account) as sess:
        async def one(task: dict) -> tuple[str, bool, str, float]:
            async with sem:
                t0 = time.monotonic()
                res = await VideoTask(sess, repo, settings, dry_run=False).run(task)
                return task["title"], res.ok, res.detail, time.monotonic() - t0

        t0 = time.monotonic()
        out = await asyncio.gather(*(one(t) for t in picked))
        wall = time.monotonic() - t0

    content = sum(int(t["duration"] or 0) for t in picked)
    for title, ok, detail, used in out:
        print(f"  {'OK ' if ok else 'FAIL'} {title[:28]:30s} {used:6.1f}s  {detail}")
    print(f"合计墙钟 {wall:.1f}s，内容 {content}s，聚合倍速 {content / wall:.2f}x")


if __name__ == "__main__":
    asyncio.run(main())
