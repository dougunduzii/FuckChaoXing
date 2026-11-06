"""章节测验：取题、解析（含字体混淆）、提交。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString

from ..crypto.font import FontDecoder
from ..session import CxSession
from ..utils import md5, norm_text, text_of

WORK_URL = "https://mooc1.chaoxing.com/mooc-ans/api/work"
SUBMIT_URL = "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew"

QTYPE_MAP = {
    "0": "single",
    "1": "multiple",
    "2": "completion",
    "3": "judgement",
    "4": "shortanswer",
}


@dataclass(slots=True)
class Question:
    id: str
    qtype: str
    type_code: str
    title: str
    options: list[str] = field(default_factory=list)

    @property
    def qkey(self) -> str:
        return md5(f"{self.qtype}|{norm_text(self.title)}")


@dataclass(slots=True)
class ParsedWork:
    form: dict[str, str] = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)
    font_usable: bool = False
    font_unknown: int = 0


def parse_work(
    body: str,
    *,
    font_map: str | Path | None = None,
    unresolved_dir: str | Path | None = None,
) -> ParsedWork:
    soup = BeautifulSoup(body, "lxml")
    form_tag = soup.find("form")
    parsed = ParsedWork()
    if form_tag is None:
        return parsed

    decoder: FontDecoder | None = None
    if soup.find("style", id="cxSecretStyle") and font_map:
        decoder = FontDecoder(body, font_map, unresolved_dir)
        parsed.font_usable = decoder.usable
        parsed.font_unknown = len(decoder.unknown)

    for inp in form_tag.find_all("input"):
        name = inp.get("name")
        if not name or "answer" in name:
            continue
        parsed.form[name] = inp.get("value", "")

    for block in form_tag.find_all("div", class_="singleQuesId"):
        q = _parse_question(block, decoder)
        if q:
            parsed.questions.append(q)
    return parsed


def _parse_question(block, decoder: FontDecoder | None) -> Question | None:
    qid = block.get("data") or ""
    timu = block.find("div", class_="TiMu")
    type_code = (timu.get("data") if timu else "") or "0"
    title_div = block.find("div", class_="Zy_TItle")
    ul = block.find("ul")

    title = _collect_text(title_div, decoder)
    options: list[str] = []
    if ul is not None:
        for li in ul.find_all("li"):
            raw = li.get("aria-label") or li.get_text()
            if not raw:
                continue
            opt = _clean_option(_decode(raw, decoder))
            if opt:
                options.append(opt)

    if not title:
        return None
    return Question(
        id=qid,
        qtype=QTYPE_MAP.get(str(type_code), "unknown"),
        type_code=str(type_code),
        title=title,
        options=options,
    )


def _decode(text: str, decoder: FontDecoder | None) -> str:
    return decoder.decode(text) if decoder else text


def _collect_text(element, decoder: FontDecoder | None) -> str:
    if element is None:
        return ""
    parts: list[str] = []
    for item in element.descendants:
        if isinstance(item, NavigableString):
            parts.append(str(item))
        elif getattr(item, "name", None) == "img":
            src = item.get("src", "")
            parts.append(f'<img src="{src}">')
    raw = "".join(parts).replace("\r", "").replace("\t", "").replace("\n", "")
    return _decode(raw, decoder).strip()


def _clean_option(opt: str) -> str:
    opt = "".join(opt.split())
    if opt.endswith("选择"):
        opt = opt[:-2]
    return opt.strip()


async def fetch_work(sess: CxSession, params: dict[str, Any]) -> tuple[int, str]:
    resp = await sess.get(
        WORK_URL,
        params=params,
        headers={"Referer": "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"},
    )
    return resp.status_code, text_of(resp)


async def submit_work(sess: CxSession, form: dict[str, str]) -> tuple[int, dict[str, Any] | None]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://mooc1.chaoxing.com",
        "Referer": "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    resp = await sess.post(SUBMIT_URL, data=form, headers=headers)
    try:
        body = resp.json()
    except Exception:
        body = None
    return resp.status_code, body
