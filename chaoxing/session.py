"""HTTP 会话层：统一 cookie、限速、重试、审计。业务层不直接碰 httpx。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
from loguru import logger

from .config import Settings
from .store.repo import Repo

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="131", "Not(A:Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

XHR_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Site": "same-origin",
}


class Pacer:
    """单条"通道"的串行节流：保证这条通道上任意两次请求间隔 ≥ min_gap + 抖动。

    一条通道 = 一个并行任务点（一个视频/一个文档），互不阻塞。原先全账号共用
    一个 Pacer，16 路并发会被串成 0.6 秒一发，既没并发也像机器。
    """

    def __init__(self, min_gap: float, jitter: float = 0.0) -> None:
        self.min_gap = max(0.0, min_gap)
        self.jitter = max(0.0, jitter)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_gap - now
            if delay > 0:
                await asyncio.sleep(delay)
            if self.jitter:
                await asyncio.sleep(random.uniform(0, self.jitter))
            self._last = time.monotonic()


class RiskBlocked(RuntimeError):
    """命中超星风控（HTTP 202 +【9010】验证码页）。

    这不是普通的失败：继续发请求只会把风控时间窗越拉越长，所以整账号必须立刻停下，
    等人用真浏览器过验证码，然后把新的 cookie 整包导回库里。
    """

    def __init__(self, url: str, hint: str = "") -> None:
        self.url = url
        self.hint = hint
        super().__init__(
            f"命中超星风控（9010 验证码）：{url}。请先执行 "
            f"python scripts/verify_browser.py 过验证码，然后 "
            f"cx login <账号> --cookie-file data\\cookies.json"
        )


def _is_verify_page(resp: httpx.Response) -> bool:
    """202 + 提示页面 才是风控；正常接口不会用 202 返回业务数据。"""
    if resp.status_code != 202:
        return False
    try:
        body = resp.text[:20000]
    except Exception:  # pragma: no cover - 二进制响应
        return False
    return "processVerify" in body or "9010" in body or "验证码" in body


class TokenBucket:
    """跨通道的全局总闸：不管开多少路，整账号每秒最多放 rate 个请求。

    每路独立节流解决"通道互不阻塞"，总闸解决"16 路同一秒齐发"这个明显的机器特征。
    令牌桶允许短时突发（burst），长时间均值被压在 rate 上。
    """

    def __init__(self, rate: float, burst: float | None = None) -> None:
        # rate <= 0 表示关闭总闸（测试/单路场景用）
        self.enabled = rate > 0
        self.rate = max(0.1, rate)
        self.capacity = max(1.0, float(burst or max(2.0, self.rate)))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                need = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(min(0.5, max(0.01, need)))



class CxSession:
    def __init__(
        self,
        settings: Settings,
        *,
        cookies: dict[str, str] | None = None,
        repo: Repo | None = None,
        account_id: int | None = None,
        label: str = "",
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.account_id = account_id
        self.label = label
        self.task_pk: int | None = None
        self.uid: str = ""
        self.fid: str = ""
        self.pacer = Pacer(settings.http.min_gap, settings.http.jitter)
        self.bucket = TokenBucket(
            getattr(settings.http, "global_rate", 4.0),
            getattr(settings.http, "global_burst", 6.0),
        )
        self._lanes: dict[str, Pacer] = {}
        self._client = httpx.AsyncClient(
            headers=BASE_HEADERS,
            cookies=cookies or {},
            timeout=httpx.Timeout(settings.http.timeout, connect=15.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            proxy=settings.http.proxy or None,
        )

    # ---------------- 通道 ----------------
    def lane(self, key: str | None) -> Pacer:
        """取某条通道的节流器；key 为空则退回全账号通道。"""
        if not key:
            return self.pacer
        pacer = self._lanes.get(key)
        if pacer is None:
            pacer = Pacer(self.settings.http.min_gap, self.settings.http.jitter)
            self._lanes[key] = pacer
        return pacer

    # ---------------- 生命周期 ----------------
    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CxSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ---------------- cookie ----------------
    @property
    def cookies(self) -> dict[str, str]:
        try:
            return {c.name: c.value for c in self._client.cookies.jar}
        except Exception:  # pragma: no cover - 兼容其它 client 实现
            try:
                return dict(self._client.cookies)  # type: ignore[arg-type]
            except Exception:
                return {}

    def set_cookies(self, cookies: dict[str, str], domain: str = ".chaoxing.com") -> None:
        for k, v in cookies.items():
            self._client.cookies.set(k, v, domain=domain)

    def cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # ---------------- 请求 ----------------
    async def request(
        self,
        method: str,
        url: str,
        *,
        retries: int | None = None,
        backoff: float | None = None,
        headers: dict[str, str] | None = None,
        audit: bool = True,
        lane: str | None = None,
        audit_task: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        retries = self.settings.http.retries if retries is None else retries
        backoff = self.settings.http.backoff if backoff is None else backoff
        attempts = retries + 1
        last_exc: Exception | None = None
        pacer = self.lane(lane)

        for attempt in range(attempts):
            await pacer.wait()
            await self.bucket.acquire()
            started = time.monotonic()
            try:
                resp = await self._client.request(method, url, headers=headers, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    logger.warning(f"[{self.label}] {method} {_short(url)} 网络失败: {exc}")
                    raise
                wait = backoff * (2**attempt) + random.uniform(0, 1)
                logger.debug(f"[{self.label}] {method} {_short(url)} 重试 {attempt + 1}/{retries} in {wait:.1f}s")
                await asyncio.sleep(wait)
                continue

            elapsed = int((time.monotonic() - started) * 1000)
            if audit and self.repo and self.settings.http.audit:
                try:
                    self.repo.audit(
                        account_id=self.account_id,
                        task_pk=self.task_pk if audit_task is None else audit_task,
                        method=method,
                        path=str(resp.request.url),
                        status=resp.status_code,
                        elapsed_ms=elapsed,
                        snippet=_snippet(resp),
                    )
                except Exception:
                    pass

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                wait = backoff * (2**attempt) + random.uniform(0, 1)
                logger.debug(f"[{self.label}] HTTP {resp.status_code} 重试 in {wait:.1f}s: {_short(url)}")
                await asyncio.sleep(wait)
                continue

            if _is_verify_page(resp):
                # 风控页面：重试没有任何意义，直接把整次运行掐掉
                raise RiskBlocked(str(resp.request.url))

            return resp

        if last_exc:
            raise last_exc
        raise RuntimeError("request failed without response")  # pragma: no cover

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.get(url, **kwargs)
        try:
            return resp.json()
        except Exception:
            return None

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.post(url, **kwargs)
        try:
            return resp.json()
        except Exception:
            return None


def _short(url: str, limit: int = 110) -> str:
    url = url.split("?")[0]
    return url if len(url) <= limit else url[:limit] + "…"


def _snippet(resp: httpx.Response) -> str:
    try:
        return resp.text[:200]
    except Exception:
        return ""
