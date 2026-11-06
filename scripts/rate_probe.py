"""并发上限实测探针：把"同时在飞的任务点数"一级一级往上加，看在哪一级吃到 9010。

为什么要用它：超星的风控并不只看每秒请求数（我们的令牌桶始终压在 4 req/s），
它更看"同一个会话在短窗口里出现了多少个不同的任务点"。202 之前那 70 秒里
153 个请求只占 2 req/s，但同时出现了 68 个不同 objectId —— 这才是被抓的点。

用法：
    python scripts/rate_probe.py --lanes 2,4,6,8,12,16 --seconds 15

输出：每一级的 请求数 / 平均 rps / 不同 objectId / 是否出现 202。
一旦某一级出现 202 立即停止（并提示去跑 verify_browser.py 过验证码）。
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import Any

from loguru import logger

from chaoxing.api import video as vapi
from chaoxing.config import load_settings
from chaoxing.engine import Engine
from chaoxing.session import RiskBlocked
from chaoxing.store.repo import Repo


async def probe(sess: Any, tasks: list[dict[str, Any]], lanes: int, seconds: float) -> dict[str, Any]:
    """在 seconds 秒内用 lanes 个协程轮番打 ananas/status，统计覆盖面。"""
    oids: set[str] = set()
    sent = 0
    blocked = False
    stop_at = time.monotonic() + seconds
    cursor = {"i": 0}

    async def worker() -> None:
        nonlocal sent, blocked
        while time.monotonic() < stop_at and not blocked:
            i = cursor["i"]
            if i >= len(tasks):
                return
            cursor["i"] = i + 1
            task = tasks[i]
            try:
                await vapi.fetch_status(sess, task["object_id"], sess.fid)
                sent += 1
                oids.add(task["object_id"])
            except RiskBlocked:
                blocked = True
                return

    started = time.monotonic()
    await asyncio.gather(*(worker() for _ in range(lanes)))
    elapsed = time.monotonic() - started
    return {
        "lanes": lanes,
        "seconds": round(elapsed, 1),
        "requests": sent,
        "rps": round(sent / elapsed, 2) if elapsed else 0,
        "object_ids": len(oids),
        "blocked": blocked,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="me")
    ap.add_argument("--lanes", default="2,4,6,8,12,16")
    ap.add_argument("--seconds", type=float, default=15.0)
    args = ap.parse_args()

    s = load_settings()
    r = Repo(s.db_path)
    acc = r.get_account(args.account)
    if not acc:
        raise SystemExit("账号不存在，先 cx login")

    tasks = [t for t in r.iter_tasks(account_id=acc["id"], types=("video",)) if t["state"] == "pending"]
    if len(tasks) < 20:
        raise SystemExit(f"待办视频只有 {len(tasks)} 个，先 cx scan 或多留点样本")

    e = Engine(s, r)
    try:
        async with e.session_for(acc) as sess:
            for lanes in [int(x) for x in args.lanes.split(",") if x.strip()]:
                row = await probe(sess, tasks, lanes, args.seconds)
                logger.info(
                    f"lanes={row['lanes']:>2} | 请求 {row['requests']:>3} | "
                    f"{row['rps']:>4} rps | 覆盖 objectId {row['object_ids']:>3} | "
                    f"{'❌ 202 风控' if row['blocked'] else '✅ 正常'}"
                )
                if row["blocked"]:
                    logger.error(
                        "已触发 9010：先去过验证码 "
                        "python scripts/verify_browser.py，再 "
                        "cx login me --cookie-file data\\cookies.json"
                    )
                    break
    except RiskBlocked as exc:
        logger.error(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
