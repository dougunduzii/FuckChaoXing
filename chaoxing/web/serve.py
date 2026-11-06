"""面板启动器：依赖检查、端口挑选、已在运行检测、局域网地址、优雅退出。

旧实现的问题（重构原因）：写死 8765、端口被占直接抛 ``[Errno 10048]``、重复启动
会再起一个实例把端口抢掉、``0.0.0.0`` 裸奔没有任何口令。这里逐条处理：

1. 端口被占 → 自动向后找可用端口（``--no-port-auto`` 时直接报错退出）；
2. 占端口的就是面板自己（``/api/health`` 回 ``chaoxing-web``）→ 不重复启动，
   只把地址打出来并打开浏览器；
3. 绑非回环地址 → 自动生成一次性 token，URL 里带上，中间件校验；
4. ``--reload`` 时必须走 import string，所以 token 用 ``CX_WEB_TOKEN`` 传递。
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from typing import Any

APP_ID = "chaoxing-web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def _line_buffered() -> None:
    """终端里行缓冲，重定向到文件/管道时也不会卡住启动横幅。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def banner(text: str) -> None:
    line = "─" * max(28, len(text) + 4)
    print(f"\n{line}\n  {text}\n{line}")


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK


def port_free(host: str, port: int) -> bool:
    if not 1 <= port <= 65535:
        return False
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    # 不能开 SO_REUSEADDR：Windows 上它会允许"抢占"已在监听的端口，
    # 探测就永远返回"空闲"，于是照样撞上真正的 [Errno 10048]。
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, port: int, *, auto: bool = True, span: int = 20) -> int:
    """返回可用端口；``port=0`` 交给系统分配。"""
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host if host not in {"0.0.0.0", "::"} else "0.0.0.0", 0))
            return int(sock.getsockname()[1])
    if port_free(host, port):
        return port
    if not auto:
        raise OSError(f"端口 {port} 已被占用")
    for candidate in range(port + 1, port + span + 1):
        if port_free(host, candidate):
            return candidate
    raise OSError(f"端口 {port}~{port + span} 都不可用")


def probe_panel(host: str, port: int, *, timeout: float = 0.8) -> dict[str, Any] | None:
    """如果这个端口上跑的是本项目面板，返回它的 health 信息。"""
    for probe_host in ("127.0.0.1", "localhost", "[::1]"):
        url = f"http://{probe_host}:{port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("app") == APP_ID:
            return data
    return None


def url_for(host: str, port: int, token: str = "") -> str:
    shown = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    url = f"http://{shown}:{port}/"
    return f"{url}?token={token}" if token else url


def lan_urls(port: int, token: str = "") -> list[str]:
    """尽力枚举本机 IPv4，给同局域网设备访问用。"""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith("127."):
                ips.add(addr)
    except OSError:
        pass
    try:  # 兜底：UDP connect 拿到默认出口地址（不真的发包）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.2)
            sock.connect(("8.8.8.8", 80))
            addr = sock.getsockname()[0]
            if addr and not addr.startswith("127."):
                ips.add(addr)
    except OSError:
        pass
    suffix = f"?token={token}" if token else ""
    return [f"http://{ip}:{port}/{suffix}" for ip in sorted(ips)]


def new_token() -> str:
    return secrets.token_urlsafe(18)


def _open_later(url: str, delay: float = 1.2) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def run_panel(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    reload: bool = False,
    token: str | None = None,
    port_auto: bool = True,
    log_level: str = "info",
) -> int:
    """启动面板；返回进程退出码（0 正常，1 依赖缺失，2 端口冲突）。"""
    _line_buffered()
    try:
        import uvicorn
    except ImportError:
        print("缺少依赖：pip install 'fastapi' 'uvicorn[standard]'")
        return 1

    host = (host or DEFAULT_HOST).strip() or DEFAULT_HOST
    # 已在运行就没必要再起一份（再起只会抢端口或让两份进程争同一个 sqlite）
    if port and not port_free(host, port):
        health = probe_panel(host, port)
        if health is not None:
            existing = health.get("server") or {}
            if existing.get("token_required") and not token:
                banner("面板已经在运行，但它需要访问口令")
                print(f"  地址：{url_for(host, port)}")
                print("  口令由当时启动的进程持有；带 --token 再跑一次可自动拼上")
                if open_browser:
                    _open_later(url_for(host, port), 0.2)
                return 0
            banner("面板已经在运行，直接打开现有实例")
            shown = url_for(host, port, token or "")
            print(f"  地址：{shown}")
            if open_browser:
                _open_later(shown, 0.2)
            return 0

    try:
        port = pick_port(host, port, auto=port_auto)
    except OSError as exc:
        print(f"{exc}（用 --port 0 让系统分配，或 --port 其它端口）")
        return 2

    explicit_token = bool(token)
    if not token and not is_loopback(host):
        # 绑到局域网上还不要口令，等于把账号会话共享给整个网段
        token = new_token()
    token = token or ""

    url = url_for(host, port, token)
    mode = "（热重载）" if reload else ""
    banner(f"cx 网页面板{mode}")
    print(f"  本机：{url}")
    if not is_loopback(host):
        for item in lan_urls(port, token):
            print(f"  局域网：{item}")
        if explicit_token:
            print("  口令：由 --token 指定")
        else:
            print("  口令：已自动生成（绑定非回环地址时强制开启）")
    print(f"  监听：{host}:{port}   日志级别：{log_level}")
    print("  Ctrl+C 退出\n")

    if open_browser:
        _open_later(url)

    kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "reload": reload,
        "log_level": log_level,
        "access_log": False,
    }
    if token:
        os.environ["CX_WEB_TOKEN"] = token
    # 热重载时 uvicorn 在子进程里重新 import 应用，拿不到这里的参数，只能走环境变量
    os.environ["CX_WEB_HOST"] = host
    os.environ["CX_WEB_PORT"] = str(port)

    try:
        if reload:
            kwargs["factory"] = True
            uvicorn.run("chaoxing.web.app:create_app", **kwargs)
        else:
            from .app import create_app

            app = create_app(
                token=token or None,
                server={"host": host, "port": port, "token_required": bool(token)},
            )
            uvicorn.run(app, **kwargs)
    except KeyboardInterrupt:  # pragma: no cover - 终端交互
        print("\n已停止面板")
        return 0
    return 0
