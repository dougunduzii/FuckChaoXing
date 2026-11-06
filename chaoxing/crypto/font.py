"""字体混淆解码（cxSecretStyle）。

超星把测验页字体替换成随机字形的 TTF，字符码位不变但字形被重绘。
思路：字形轮廓 → 归一化 → hash → 查人工映射表。未命中保留原字符并落盘待补。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path

from fontTools.ttLib import TTFont

_DATA_URI_RE = re.compile(r"data:[^;]*;base64,([A-Za-z0-9+/=\s]+)", re.S)
_SECRET_STYLE_RE = re.compile(r"<style[^>]*id=[\"']cxSecretStyle[\"'][^>]*>(.*?)</style>", re.S | re.I)


def extract_ttf(html: str) -> bytes | None:
    """从页面里捞出 cxSecretStyle 内嵌的 TTF 字节。"""
    m = _SECRET_STYLE_RE.search(html)
    css = m.group(1) if m else html
    dm = _DATA_URI_RE.search(css)
    if not dm:
        return None
    b64 = re.sub(r"\s+", "", dm.group(1))
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def _glyph_signature(font: TTFont, name: str) -> str | None:
    """取字形轮廓做归一化签名：相对首点平移 + 量化到 0.05。"""
    try:
        glyf = font["glyf"]
        g = glyf[name]
    except Exception:
        return None
    try:
        if g.numberOfContours <= 0:  # 复合字形 / 空字形不参与
            return None
        coords = list(g.coordinates)
        if not coords:
            return None
        x0, y0 = coords[0]
        norm = [(round((x - x0) * 20), round((y - y0) * 20)) for x, y in coords]
        payload = json.dumps(
            {"e": list(g.endPtsOfContours), "c": norm}, separators=(",", ":")
        )
        return hashlib.md5(payload.encode("utf-8")).hexdigest()
    except Exception:
        return None


class FontDecoder:
    """一次性解码器；未命中的签名会记录到 unresolved 目录。"""

    def __init__(self, html: str, map_path: str | Path, unresolved_dir: str | Path | None = None):
        self.map_path = Path(map_path)
        self.unresolved_dir = Path(unresolved_dir) if unresolved_dir else None
        self.mapping: dict[str, str] = {}
        self.table: dict[str, str] = {}      # 原始字符 → 真实字符
        self.unknown: list[str] = []
        self._load_map()
        self._build(html)

    def _load_map(self) -> None:
        if not self.map_path.exists():
            return
        try:
            data = json.loads(self.map_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict) and isinstance(data.get("hash_to_char"), dict):
            data = data["hash_to_char"]
        if isinstance(data, dict):
            self.mapping = {
                str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()
            }

    def _build(self, html: str) -> None:
        ttf = extract_ttf(html)
        if not ttf:
            return
        try:
            font = TTFont(io.BytesIO(ttf))
            cmap = font.getBestCmap() or {}
        except Exception:
            return

        for codepoint, glyph_name in cmap.items():
            sig = _glyph_signature(font, glyph_name)
            if not sig:
                continue
            real = self.mapping.get(sig)
            if real and real != chr(codepoint):
                self.table[chr(codepoint)] = real
            elif not real:
                self.unknown.append(sig)

        if self.unknown and self.unresolved_dir:
            self.unresolved_dir.mkdir(parents=True, exist_ok=True)
            dump = self.unresolved_dir / f"unknown-{len(self.unknown)}-{self.unknown[0][:8]}.json"
            if not dump.exists():
                dump.write_text(json.dumps(sorted(set(self.unknown)), indent=2), encoding="utf-8")

    def decode(self, text: str) -> str:
        if not self.table:
            return text
        return "".join(self.table.get(ch, ch) for ch in text)

    @property
    def usable(self) -> bool:
        return bool(self.table)

    @staticmethod
    def save_mapping(map_path: str | Path, mapping: dict[str, str]) -> None:
        p = Path(map_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
