"""小工具：响应解码、文本归一化。"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import httpx

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s,，.。;；:：、!！?？\"'“”‘’()（）\[\]【】<>《》/\\|~`\-—_]+")


def text_of(resp: httpx.Response) -> str:
    if resp.encoding:
        try:
            return resp.content.decode(resp.encoding, "ignore")
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8", "gb18030"):
        try:
            return resp.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return resp.text


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def norm_text(s: str) -> str:
    """去空白、全角转半角、统一小写，用于题目指纹。"""
    s = unicodedata.normalize("NFKC", s or "")
    return _WS_RE.sub("", s).strip().lower()


def strip_punct(s: str) -> str:
    return _PUNCT_RE.sub("", unicodedata.normalize("NFKC", s or "")).strip().lower()
