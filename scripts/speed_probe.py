"""实测某个倍速在本账号上到底能不能过。

用法：
    python scripts/speed_probe.py --job 1234567890123456 --speed 2.0

只跑一个视频，跑完打印墙钟耗时、有效倍速（内容秒数/墙钟秒数）和服务端判定结果。
倍速越高越容易"播完不判过"，用这个脚本先量一下再决定 config.toml 里的 speed。
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chaoxing.config import load_settings  # noqa: E402
from chaoxing.engine import Engine  # noqa: E402
from chaoxing.store.repo import Repo  # noqa: E402
from chaoxing.task.video import VideoTask  # noqa: E402


def pick(repo: Repo, job: str | None, duration_hint: int | None) -> dict:
    rows = [t for t in repo.iter_tasks(types=("video",)) if t["state"] != "done"]
    if job:
        for t in rows:
            if t["job_id"] == job:
                return t
        raise SystemExit(f"没找到 job_id={job} 的待办视频")
    if duration_hint:
        rows = [t for t in rows if abs((t["duration"] or 0) - duration_hint) < 60]
    if not rows:
        raise SystemExit("没有可测的视频")
    return min(rows, key=lambda t: t["duration"] or 10**9)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="me")
    ap.add_argument("--job", default=None)
    ap.add_argument("--speed", type=float, default=2.0)
    ap.add_argument("--duration", type=int, default=None, help="按目标时长挑视频（±60 秒）")
    args = ap.parse_args()

    base = load_settings()
    settings = dataclasses.replace(
        base, video=dataclasses.replace(base.video, speed=args.speed, instant_skip=False)
    )
    repo = Repo(base.db_path)
    account = repo.get_account(args.account)
    if account is None:
        raise SystemExit(f"账号 {args.account} 不存在")

    task = pick(repo, args.job, args.duration)
    print(f"目标: {task['title']}  时长 {task['duration']}s  倍速 {args.speed}")

    engine = Engine(settings, repo)
    async with engine.session_for(account) as sess:
        handler = VideoTask(sess, repo, settings, dry_run=False)
        t0 = time.monotonic()
        res = await handler.run(task)
        wall = time.monotonic() - t0

    content = max(1, int(task["duration"] or 0))
    print(f"结果: ok={res.ok} {res.detail}")
    print(f"墙钟 {wall:.1f}s  有效倍速 {content / wall:.2f}x")


if __name__ == "__main__":
    asyncio.run(main())
