"""节流与错峰的单元测试。"""

from __future__ import annotations

import asyncio
import time

from chaoxing.config import load_settings
from chaoxing.session import CxSession, Pacer, TokenBucket
from chaoxing.task.base import BaseTask


class _FakeSess:
    uid = "900000001"


async def test_pacer_enforces_gap():
    p = Pacer(0.05, 0.0)
    t0 = time.monotonic()
    for _ in range(3):
        await p.wait()
    assert time.monotonic() - t0 >= 0.10


async def test_pacer_lanes_are_independent():
    """两条通道各自计时，不该互相排队：共用会要 0.8s，独立约 0.4s。"""
    a, b = Pacer(0.2, 0.0), Pacer(0.2, 0.0)
    t0 = time.monotonic()
    await asyncio.gather(*(a.wait() for _ in range(2)), *(b.wait() for _ in range(2)))
    assert time.monotonic() - t0 < 0.6


async def test_token_bucket_throttles_burst():
    bucket = TokenBucket(20.0, burst=2)
    t0 = time.monotonic()
    for _ in range(8):
        await bucket.acquire()
    assert time.monotonic() - t0 >= 0.25


async def test_token_bucket_disabled_when_rate_zero():
    bucket = TokenBucket(0)
    t0 = time.monotonic()
    for _ in range(50):
        await bucket.acquire()
    assert time.monotonic() - t0 < 0.1


async def test_lane_cache_is_per_key():
    sess = CxSession(load_settings(), cookies={})
    try:
        assert sess.lane("a") is sess.lane("a")
        assert sess.lane("a") is not sess.lane("b")
        assert sess.lane(None) is sess.pacer
    finally:
        await sess.close()


def test_stagger_spreads_lanes():
    base_iv = 60
    offsets = []
    for i in range(6):
        t = BaseTask(_FakeSess(), None, None, lane_index=i, lane_total=6)  # type: ignore[arg-type]
        t.lane_key = f"video:job-{i}"
        offsets.append(t.stagger(base_iv))
    assert all(0 <= o < base_iv for o in offsets), offsets
    assert max(offsets) - min(offsets) > base_iv / 6

    single = BaseTask(_FakeSess(), None, None, lane_index=0, lane_total=1)  # type: ignore[arg-type]
    assert single.stagger(60) == 0.0


async def test_risk_blocked_raises_on_9010():
    """202 + 验证码页必须当成风控抛出去，而不是当普通响应返回。"""
    import httpx

    import pytest

    from chaoxing.session import RiskBlocked

    async def handler(request: httpx.Request) -> httpx.Response:
        body = '<html><title>提示页面</title>【9010】操作异常，请输入图片中的验证码<form action="/html/processVerify.ac"></form>'
        return httpx.Response(202, text=body)

    s = load_settings()
    sess = CxSession(s, repo=None, label="t")
    sess._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess.bucket.enabled = False
    sess.pacer.min_gap = 0.0
    sess.pacer.jitter = 0.0
    with pytest.raises(RiskBlocked):
        await sess.request("GET", "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards")
    await sess.close()
