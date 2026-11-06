"""登录与会话校验。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from .crypto import aes
from .session import CxSession

LOGIN_URL = "https://passport2.chaoxing.com/fanyalogin"
VERIFY_URL = "https://sso.chaoxing.com/apis/login/userLogin4Uname.do"
LOGIN_REFERER = "https://i.chaoxing.com"
BROWSER_LOGIN_URL = "https://passport2.chaoxing.com/login?fid=&refer=https%3A%2F%2Fi.chaoxing.com"

COOKIE_KEYS = ("_uid", "_d", "UID", "uf", "vc3", "fid")


async def login_password(sess: CxSession, uname: str, password: str) -> dict[str, Any]:
    data = {
        "fid": "-1",
        "uname": aes.encrypt(uname),
        "password": aes.encrypt(password),
        "refer": "https%3A%2F%2Fi.chaoxing.com",
        "t": "true",
        "forbidotherlogin": "0",
        "validate": "",
        "doubleFactorLogin": "0",
        "independentId": "0",
    }
    resp = await sess.post(
        LOGIN_URL,
        data=data,
        headers={"Referer": LOGIN_REFERER, "Origin": "https://passport2.chaoxing.com"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {}
    ok = bool(isinstance(body, dict) and body.get("status"))
    return {
        "ok": ok,
        "msg": (body or {}).get("msg") or (body or {}).get("message") or f"HTTP {resp.status_code}",
        "cookies": sess.cookies,
        "needs_captcha": bool(isinstance(body, dict) and body.get("url")),
    }


async def verify(sess: CxSession) -> dict[str, Any] | None:
    """返回 msg 字典（含 puid）表示已登录；None 表示无效。"""
    data = await sess.get_json(
        VERIFY_URL, headers={"Referer": "https://i.chaoxing.com/base"}
    )
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if result in (1, "1", True):
        msg = data.get("msg") or {}
        return msg if isinstance(msg, dict) else {}
    return None


async def ensure_login(sess: CxSession, account_name: str, repo) -> dict[str, Any] | None:
    """校验会话并回填 puid/fid 到库。"""
    msg = await verify(sess)
    if msg is None:
        repo.set_account_status(account_name, "need_login")
        return None
    puid = str(msg.get("puid") or msg.get("uid") or "")
    fid = str(msg.get("fid") or "")
    cookies = sess.cookies
    repo.save_account(account_name, cookies, puid=puid or None, fid=fid or None, status="ok")
    sess.uid = puid
    sess.fid = fid
    return msg


async def import_cookies(sess: CxSession, cookie_str: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        cookies[k.strip()] = v.strip()
    sess.set_cookies(cookies)
    return cookies


SYSTEM_CHANNELS = ("chrome", "msedge")

_SYSTEM_EXES = {
    "chrome": [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ],
    "msedge": [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ],
}


async def launch_browser(p, *, headless: bool = False):
    """按「内置 chromium → 系统 Chrome → 系统 Edge」顺序尝试启动。

    playwright 的内置浏览器是按版本号锁定目录的（1.58 要 chromium-1208），
    换版本或目录被清理就会 `Executable doesn't exist`。系统里已有 Chrome/Edge 时
    直接用 channel 启动，省掉一次 180MB 下载。
    """
    errors: list[str] = []
    for kwargs in ({}, *( {"channel": c} for c in SYSTEM_CHANNELS )):
        try:
            browser = await p.chromium.launch(headless=headless, **kwargs)
            if kwargs:
                logger.info("内置 chromium 不可用，改用系统 {}", kwargs["channel"])
            return browser
        except Exception as exc:  # noqa: BLE001 - 逐个探针，最后一个才是真失败
            tag = kwargs.get("channel") or "bundled"
            errors.append(f"{tag}: {str(exc).splitlines()[0][:120]}")

    raise RuntimeError(
        "启动浏览器失败，已依次尝试 内置 chromium / 系统 Chrome / 系统 Edge：\n  "
        + "\n  ".join(errors)
        + "\n修法：python -m playwright install chromium，或先装 Chrome，"
        "或者完全绕开浏览器——cx login <name> --cookie \"_uid=..; _d=..\""
    )


def browser_candidates() -> list[tuple[str, str, bool]]:
    """探测可用浏览器，供 doctor 展示。返回 [(名称, 路径/说明, 是否可用)]。"""
    out: list[tuple[str, str, bool]] = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = p.chromium.executable_path
        out.append(("playwright 内置 chromium", exe, Path(exe).exists()))
    except Exception as exc:  # noqa: BLE001
        out.append(("playwright 内置 chromium", f"不可用（{type(exc).__name__}）", False))

    for channel, paths in _SYSTEM_EXES.items():
        hit = next((p for p in paths if p.exists()), None)
        out.append((f"系统 {channel}", str(hit or paths[0]), hit is not None))
    return out


async def browser_login(
    out_cookies: Path | None = None,
    *,
    timeout: float = 180.0,
    headless: bool = False,
) -> dict[str, str]:
    """弹出真实浏览器手动登录（扫码/密码均可），登录后抓 cookie 落盘。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("需要 playwright: pip install playwright && playwright install chromium") from exc

    cookies: dict[str, str] = {}
    async with async_playwright() as p:
        browser = await launch_browser(p, headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(BROWSER_LOGIN_URL, wait_until="domcontentloaded")
        logger.info("请在弹出的浏览器里完成登录，最长等待 %ss", int(timeout))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await context.cookies()
            cookies = {c["name"]: c["value"] for c in raw if c.get("name")}
            if any(k in cookies for k in ("_uid", "UID")) and "_d" in cookies:
                logger.info("检测到登录 cookie，继续抓取页面…")
                await asyncio.sleep(3)
                raw = await context.cookies()
                cookies = {c["name"]: c["value"] for c in raw if c.get("name")}
                break
            await asyncio.sleep(1.5)
        else:
            cookies = {}

        await browser.close()

    if not cookies:
        raise RuntimeError("未获得有效 cookie（登录超时）")
    if out_cookies:
        out_cookies.parent.mkdir(parents=True, exist_ok=True)
        out_cookies.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    return cookies

# 只保留这些会话相关 cookie，其余大概率是风控/埋点标记
# 会话/路由必需：清掉这些会直接掉登录或落到错误的机房节点
CORE_COOKIES = (
    "_uid", "UID", "_d", "fid", "uf", "vc3", "p_auth_token",
    "jrose", "route", "k8s", "JSESSIONID", "KI4SO_SERVER_EC",
    "cx_login_prompt", "__puus", "__pus", "X_UID", "cx_uid",
)


def cookie_report(sess: "CxSession") -> list[tuple[str, int, bool]]:
    """返回 (名字, 值长度, 是否白名单)。用长度而不是明文，避免把 cookie 打到终端。"""
    jar = {c.name: c.value for c in sess._client.cookies.jar}
    return [(k, len(v or ""), k in CORE_COOKIES) for k, v in sorted(jar.items())]


async def prune_cookies(sess: "CxSession", *, keep: tuple[str, ...] = CORE_COOKIES) -> tuple[list[str], bool]:
    """按白名单清 cookie，清完立刻验一次登录；验证失败自动还原。

    返回 (被清掉的名字, 是否仍然登录有效)。
    """
    jar = {c.name: c.value for c in sess._client.cookies.jar}
    dropped = [k for k in jar if k not in keep]
    if not dropped:
        return [], True
    backup = dict(jar)
    for name in dropped:
        sess._client.cookies.delete(name, domain=".chaoxing.com")
        sess._client.cookies.delete(name)
    msg = await verify(sess)
    if msg is None:
        # 清错了，原样放回
        for k, v in backup.items():
            sess._client.cookies.set(k, v, domain=".chaoxing.com")
        return [], False
    return dropped, True
