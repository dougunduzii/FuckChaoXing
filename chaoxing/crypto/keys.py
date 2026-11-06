"""运行时解析平台加密参数：登录 AES key、视频 enc 盐值。

源码里不落任何平台字面量，取值优先级：

1. 环境变量 ``CX_AES_KEY`` / ``CX_ENC_SALT``
2. 本地缓存 ``<data_dir>/state/crypto.json``（``cx crypto probe`` / ``cx crypto set`` 写入）
3. 现场探测：登录页引用的 ``fanya/login.js`` 里的 ``transferKey``、播放器
   bundle 里 format 模板的盐值字面量

三级都取不到时抛 :class:`KeyMissing`，文案直接给出可执行命令。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

AES_KEY = "aes_key"
ENC_SALT = "enc_salt"
SLOTS: tuple[str, ...] = (AES_KEY, ENC_SALT)
ENV_VARS = {AES_KEY: "CX_AES_KEY", ENC_SALT: "CX_ENC_SALT"}
LABELS = {AES_KEY: "登录 AES key", ENC_SALT: "enc 盐值"}

LOGIN_URL = "https://passport2.chaoxing.com/login?fid=&refer=https%3A%2F%2Fi.chaoxing.com"
PLAYER_BUNDLE = "https://mooc1.chaoxing.com/ananas/videojs-ext/videojs-ext.min.js"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_LOGIN_JS_RE = re.compile(r"(?:https?:)?//passport2-static\.chaoxing\.com/[^\"'\s>]*login\.js[^\"'\s>]*")
_TRANSFER_RE = re.compile(r"""transferKey\s*[:=]\s*["']([^"']{8,32})["']""")
# 播放器 bundle 是字符串表混淆的：只取单引号短字面量，再按「含数字 + 字母 + 强符号」
# 过滤掉 DOM 选择器/字段名，剩下的就是 format 模板里那段盐值。
_JS_LITERAL_RE = re.compile(r"'([^']{4,32})'")
_SECRET_CHARSET_RE = re.compile(r"[A-Za-z0-9!@$%^&*_~.]{8,24}")
_STRONG_SYMBOLS = set("!$%^&*~")


class KeyMissing(RuntimeError):
    """常量既没配也没探测到，需要用户显式提供。"""

    def __init__(self, name: str, reason: str = "") -> None:
        self.name = name
        self.reason = reason
        super().__init__(self._message())

    def _message(self) -> str:
        label = LABELS.get(self.name, self.name)
        lines = [f"缺少{label}（{self.name}）"]
        if self.reason:
            lines.append(f"原因：{self.reason}")
        lines += [
            "源码不内置平台常量，需要显式提供其一：",
            f"  cx crypto probe            # 现场探测并缓存",
            f"  cx crypto set {self.name} <值>",
            f"  {ENV_VARS.get(self.name, 'CX_*')}=<值> 环境变量",
        ]
        return "\n".join(lines)


class BadSecret(ValueError):
    """人工填的常量格式不对。"""


def mask(value: str) -> str:
    """打码后再给人看：保留首尾各 2 个字符。"""
    if not value:
        return ""
    if len(value) <= 6:
        return value[0] + "***"
    return f"{value[:2]}***{value[-2:]}"


def extract_transfer_key(text: str) -> str | None:
    """从登录页 JS 里取 ``transferKey``（AES key 由页面下发给前端）。"""
    match = _TRANSFER_RE.search(text or "")
    return match.group(1) if match else None


def salt_candidates(js: str) -> list[str]:
    """从播放器 bundle 里挑出盐值候选（去重、保序）。"""
    out: list[str] = []
    for match in _JS_LITERAL_RE.finditer(js or ""):
        cand = match.group(1)
        if not _SECRET_CHARSET_RE.fullmatch(cand):
            continue
        if not (re.search(r"[A-Za-z]", cand) and re.search(r"[0-9]", cand)):
            continue
        if not (set(cand) & _STRONG_SYMBOLS):
            continue
        if cand not in out:
            out.append(cand)
    return out


def _validate(name: str, value: str, *, origin: str) -> str:
    text = (value or "").strip()
    if not text:
        raise BadSecret(f"{origin} 给的是空值")
    if name == AES_KEY:
        if len(text.encode("utf-8")) != 16:
            raise BadSecret(f"{origin} 的 AES key 必须是 16 字节，当前 {len(text.encode('utf-8'))} 字节")
    elif not 4 <= len(text) <= 32:
        raise BadSecret(f"{origin} 的盐值长度应在 4~32，当前 {len(text)}")
    return text


def _fetch(client: httpx.Client, url: str) -> str:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise KeyMissing("aes_key", f"拉取 {url} 失败：{exc}") from exc
    return resp.text


class KeyStore:
    """一个 data_dir 对应一份缓存；不联网也能用（env + 缓存）。"""

    def __init__(self, data_dir: Path | str | None = None, *, timeout: float = 20.0) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self.timeout = timeout
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- 缓存文件
    @property
    def data_dir(self) -> Path:
        if self._data_dir is None:
            from ..config import load_settings

            self._data_dir = Path(load_settings().data_dir)
        return self._data_dir

    @property
    def path(self) -> Path:
        return self.data_dir / "state" / "crypto.json"

    def _read(self) -> dict[str, Any]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def cached(self, name: str) -> str | None:
        entry = self._read().get(name)
        if isinstance(entry, dict):
            value = entry.get("value")
        else:
            value = entry
        return str(value) if value else None

    def set(self, name: str, value: str, *, source: str = "manual") -> str:
        if name not in SLOTS:
            raise BadSecret(f"未知常量 {name}，可选 {', '.join(SLOTS)}")
        text = _validate(name, value, origin="输入值")
        with self._lock:
            data = self._read()
            data[name] = {"value": text, "source": source, "ts": int(time.time())}
            self._write(data)
        return text

    def clear(self, name: str | None = None) -> list[str]:
        with self._lock:
            data = self._read()
            if name is None:
                cleared = [key for key in data if key in SLOTS]
                data = {k: v for k, v in data.items() if k not in SLOTS}
            else:
                cleared = [name] if name in data else []
                data.pop(name, None)
            self._write(data)
        return cleared

    # ---------------------------------------------------------------- 解析
    def source(self, name: str) -> str | None:
        if os.environ.get(ENV_VARS[name], "").strip():
            return "env"
        return "cache" if self.cached(name) else None

    def peek(self, name: str) -> str | None:
        """当前生效值（不触发探测）。"""
        env = os.environ.get(ENV_VARS[name], "").strip()
        return env or self.cached(name)

    def get(self, name: str, *, allow_probe: bool = True) -> str:
        value = self.peek(name)
        if value:
            try:
                return _validate(name, value, origin=self.source(name) or "缓存")
            except BadSecret:
                if self.source(name) != "env":
                    # 缓存脏了就丢掉，走探测/报错
                    self.clear(name)
                else:
                    raise
        if not allow_probe:
            raise KeyMissing(name, "环境变量与缓存里都没有")
        found = self.probe(name)
        self.set(name, found, source="probe")
        return found

    # ---------------------------------------------------------------- 探测
    def probe(self, name: str, *, client: httpx.Client | None = None) -> str:
        if name not in SLOTS:
            raise BadSecret(f"未知常量 {name}，可选 {', '.join(SLOTS)}")
        if client is None:
            with httpx.Client(
                timeout=self.timeout, follow_redirects=True, headers={"User-Agent": UA}
            ) as owned:
                return self.probe(name, client=owned)
        return self._probe_aes(client) if name == AES_KEY else self._probe_salt(client)

    def _probe_aes(self, client: httpx.Client) -> str:
        page = _fetch(client, LOGIN_URL)
        match = _LOGIN_JS_RE.search(page)
        if match:
            url = match.group(0)
            if url.startswith("//"):
                url = "https:" + url
            found = extract_transfer_key(_fetch(client, url))
            if found:
                return _validate(AES_KEY, found, origin="登录页 JS")
        found = extract_transfer_key(page)
        if found:
            return _validate(AES_KEY, found, origin="登录页")
        raise KeyMissing(AES_KEY, "登录页与 fanya/login.js 里都没有 transferKey（页面结构可能变了）")

    def _probe_salt(self, client: httpx.Client) -> str:
        js = _fetch(client, PLAYER_BUNDLE)
        cands = salt_candidates(js)
        if len(cands) == 1:
            return _validate(ENC_SALT, cands[0], origin="播放器 bundle")
        if not cands:
            raise KeyMissing(ENC_SALT, "播放器 bundle 里没有候选盐值（可能改成动态下发了）")
        joined = " / ".join(cands)
        raise KeyMissing(
            ENC_SALT, f"播放器 bundle 里有 {len(cands)} 个候选：{joined}；用 cx crypto set enc_salt <值> 指定"
        )

    # ---------------------------------------------------------------- 展示
    def report(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in SLOTS:
            value = self.peek(name)
            out[name] = {
                "name": name,
                "label": LABELS[name],
                "env": ENV_VARS[name],
                "present": bool(value),
                "source": self.source(name),
                "masked": mask(value) if value else "",
            }
        return out


_STORE: KeyStore | None = None


def store() -> KeyStore:
    """进程内单例，省得每次算 enc 都读一遍文件。"""
    global _STORE
    if _STORE is None:
        _STORE = KeyStore()
    return _STORE


def reset_store() -> None:
    """丢掉单例（测试与 ``cx crypto`` 改完缓存后用）。"""
    global _STORE
    _STORE = None


def get_secret(name: str, *, allow_probe: bool = True) -> str:
    return store().get(name, allow_probe=allow_probe)
