"""字体混淆标注台。

超星测验页的字体把字形重绘了，字符码位不变。这里把混淆字体里每个字形
渲染成一张对照图，人眼认出"这个字形其实是哪个字"，填进
data/fonts/mapping-template.json，之后 FontDecoder 就能自动还原。

用法：
    # 1) 从保存的 HTML 里提取
    python scripts/font_lab.py --html data/page.html

    # 2) 或者直接给一个测验页 URL（需要 cookie）
    python scripts/font_lab.py --url "https://mooc1.chaoxing.com/mooc-ans/api/work?api=1&..." \
        --cookie data/cookie.json

产物：
    data/fonts/<name>.ttf            混淆字体
    data/fonts/preview-<name>.png    16 列字形对照图（格下标注码位）
    data/fonts/mapping-template.json {"<glyph-hash>": ""}  待你填
把 mapping-template.json 填好后另存为 data/fonts/map.json 即可。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fontTools.ttLib import TTFont  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from chaoxing.crypto.font import _glyph_signature, extract_ttf  # noqa: E402

CELL = 72
COLS = 16
PAD_TOP = 18


def render_sheet(ttf_bytes: bytes, out_png: Path, codes: list[int]) -> None:
    font = ImageFont.truetype(io.BytesIO(ttf_bytes), 40)
    label_font = ImageFont.load_default()
    rows = (len(codes) + COLS - 1) // COLS
    width = COLS * CELL
    height = rows * (CELL + PAD_TOP)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    for i, code in enumerate(codes):
        r, c = divmod(i, COLS)
        x = c * CELL
        y = r * (CELL + PAD_TOP)
        draw.rectangle([x, y, x + CELL - 1, y + CELL - 1], outline="#dddddd")
        try:
            draw.text((x + 14, y + 8), chr(code), font=font, fill="black")
        except Exception:
            pass
        draw.text((x + 4, y + CELL), f"{code}", font=label_font, fill="#888888")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    print(f"对照图已生成: {out_png}  ({len(codes)} 个字形, {COLS} 列)")


def lab_from_html(html: str, name: str, out_dir: Path) -> int:
    ttf = extract_ttf(html)
    if not ttf:
        print("这个页面没有 cxSecretStyle 内嵌字体，无需解码")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    ttf_path = out_dir / f"{name}.ttf"
    ttf_path.write_bytes(ttf)
    print(f"字体已保存: {ttf_path}")

    font = TTFont(io.BytesIO(ttf))
    cmap = font.getBestCmap() or {}
    codes = sorted(cmap)
    render_sheet(ttf, out_dir / f"preview-{name}.png", codes)

    template: dict[str, str] = {}
    by_code: dict[str, str] = {}
    for code in codes:
        sig = _glyph_signature(font, cmap[code])
        if sig:
            template.setdefault(sig, "")
            by_code[str(code)] = sig

    tp = out_dir / "mapping-template.json"
    data = {"hash_to_char": template, "code_to_hash": by_code}
    tp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"映射模板已生成: {tp}")
    print("填 hash_to_char 里的空字符串，然后另存为 data/fonts/map.json")
    print("（hash_to_char 只保留 hash→字 的扁平结构也可以直接用）")
    return 0


async def fetch_html(url: str, cookie_file: Path) -> str:
    import httpx

    cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=30) as c:
        r = await c.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )
        return r.content.decode("utf-8", "ignore")


def main() -> int:
    ap = argparse.ArgumentParser(description="字体混淆标注台")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--html", help="本地 HTML 文件")
    src.add_argument("--url", help="测验页 URL")
    ap.add_argument("--cookie", default="data/cookie.json")
    ap.add_argument("--name", default="quiz", help="产物文件名前缀")
    ap.add_argument("--out", default="data/fonts")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8", errors="ignore")
    else:
        cookie = Path(args.cookie)
        if not cookie.exists():
            print(f"缺少 cookie: {cookie}")
            return 1
        html = asyncio.run(fetch_html(args.url, cookie))
    return lab_from_html(html, args.name, out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
