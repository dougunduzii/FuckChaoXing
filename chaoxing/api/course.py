"""课程 / 章节 / 任务点卡片抓取与解析。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from lxml import html as lhtml
from loguru import logger

from ..session import CxSession
from ..utils import text_of

MOOC_HOST = "https://mooc1.chaoxing.com"
MOOC2_HOST = "https://mooc2-ans.chaoxing.com"

COURSE_LIST_URL = f"{MOOC2_HOST}/mooc2-ans/visit/courselistdata"
COURSE_INTERACTION_URL = f"{MOOC2_HOST}/mooc2-ans/visit/interaction"
# ---------------------------------------------------------------- 目录 / 章节
_KID_RE = re.compile(r"^cur(\d+)$")

CHAPTER_URL = f"{MOOC2_HOST}/mooc2-ans/mycourse/studentcourse"
CARDS_URL = f"{MOOC_HOST}/mooc-ans/knowledge/cards"
EMPTY_PAGE_URL = f"{MOOC_HOST}/mooc-ans/mycourse/studentstudyAjax"

COURSE_LIST_REFERER = (
    f"{MOOC2_HOST}/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans"
)
CARDS_VERSION = "2025-0424-1038-3"


def _cls(tag: str, cls: str) -> str:
    return f'.//{tag}[contains(concat(" ", normalize-space(@class), " "), " {cls} ")]'


def _first_text(node, cls: str, attr: str = "title") -> str:
    res = node.xpath(
        f'.//*[contains(concat(" ", normalize-space(@class), " "), " {cls} ")]/@{attr}'
    )
    return res[0].strip() if res else ""


def _first_value(node, cls: str) -> str:
    res = node.xpath(
        f'.//*[contains(concat(" ", normalize-space(@class), " "), " {cls} ")]/@value'
    )
    return res[0].strip() if res else ""


# ---------------------------------------------------------------- 课程列表
def parse_courses(body: str) -> list[dict[str, Any]]:
    doc = lhtml.fromstring(body)
    out: list[dict[str, Any]] = []
    for div in doc.xpath(_cls("div", "course")):
        if div.xpath('.//*[contains(@class,"not-open-tip")]'):
            continue
        clazz_id = _first_value(div, "clazzId")
        course_id = _first_value(div, "courseId")
        if not (clazz_id and course_id):
            continue
        hrefs = div.xpath(".//a/@href")
        href = hrefs[0] if hrefs else ""
        m = re.search(r"cpi=([^&\"']+)", href)
        cpi = m.group(1) if m else ""
        if not cpi:
            info = div.get("info") or ""
            cpi = info.split("&")[-1] if info else ""
        out.append(
            {
                "course_id": course_id,
                "clazz_id": clazz_id,
                "cpi": cpi,
                "title": _first_text(div, "course-name") or _text(div, "span"),
                "teacher": _first_text(div, "color3"),
                "roleid": div.get("roleid") or "",
            }
        )
    return out


def _text(node, tag: str) -> str:
    res = node.xpath(f".//{tag}//text()")
    return "".join(res).strip()


def parse_course_folders(body: str) -> list[dict[str, str]]:
    doc = lhtml.fromstring(body)
    out: list[dict[str, str]] = []
    for li in doc.xpath('.//ul[contains(@class,"file-list")]/li[@fileid]'):
        fid = li.get("fileid") or ""
        if not fid:
            continue
        names = li.xpath('.//input[contains(@class,"rename-input")]/@value')
        out.append({"id": fid, "name": names[0].strip() if names else fid})
    return out


async def fetch_courses(sess: CxSession, *, include_folders: bool = True) -> list[dict[str, Any]]:
    headers = {"Referer": COURSE_LIST_REFERER}
    data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
    resp = await sess.post(COURSE_LIST_URL, headers=headers, data=data)
    courses = parse_courses(text_of(resp))
    logger.debug(f"主目录解析到 {len(courses)} 门课程")

    if include_folders:
        iresp = await sess.get(COURSE_INTERACTION_URL, headers={"Referer": f"{MOOC2_HOST}/"})
        folders = parse_course_folders(text_of(iresp))
        for folder in folders:
            fdata = dict(data, courseFolderId=folder["id"])
            fresp = await sess.post(COURSE_LIST_URL, headers=headers, data=fdata)
            extra = parse_courses(text_of(fresp))
            logger.debug(f"文件夹「{folder['name']}」解析到 {len(extra)} 门课程")
            courses.extend(extra)

    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, Any]] = []
    for c in courses:
        key = (c["course_id"], c["clazz_id"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


# ---------------------------------------------------------------- 章节
def parse_chapters(body: str) -> list[dict[str, Any]]:
    """解析目录页。

    真实结构（2025-11 实测）是三层嵌套，知识点不一定挂在 `chapter_unit > ul > li` 下：

        div.chapter_unit
          div.chapter_item                     章（无 id）
          div.catalog_level > ul > li
            div.chapter_item#cur<knowledgeId>  知识点 ← 要的就是这个
              div.catalog_title
                a.clicktitle > span[title]
                input.knowledgeJobCount[value] 任务点数
                span.bntHoverTips              状态文案（已完成 / 未开放…）
            div.catalog_level > ul > li ...    第三级（可选，同样是 #cur）
    """
    doc = lhtml.fromstring(body)
    points: list[dict[str, Any]] = []
    seen: set[str] = set()

    for div in doc.xpath('.//div[starts-with(@id,"cur")]'):
        m = _KID_RE.match((div.get("id") or "").strip())
        if not m:
            continue
        kid = m.group(1)
        if kid in seen:
            continue
        has_count = bool(div.xpath('.//*[contains(@class,"knowledgeJobCount")]'))
        cls = div.get("class") or ""
        # 章级节点没有 id；万一别的模板给了 id，靠这两个特征区分
        if not has_count and "chapter_item" not in cls:
            continue
        seen.add(kid)

        title = (div.get("title") or "").strip()
        if not title:
            raw = "".join(div.xpath('.//a[contains(@class,"clicktitle")]//text()'))
            title = re.sub(r"^\s*[\d.]+\s*", "", raw.strip()).strip()

        count = _first_value(div, "knowledgeJobCount")
        tips = "".join(div.xpath('.//span[contains(@class,"bntHoverTips")]//text()'))
        if not tips:
            tips = "".join(div.xpath('.//*[contains(@class,"catalog_points")]//text()'))

        points.append(
            {
                "knowledge_id": kid,
                "title": title,
                "job_count": int(count) if count.isdigit() else 0,
                "finished": "已完成" in tips,
                "locked": ("解锁" in tips) or ("未开放" in tips),
            }
        )
    return points


async def fetch_chapters(sess: CxSession, course: dict[str, Any]) -> list[dict[str, Any]]:
    params = {
        "courseid": course["course_id"],
        "clazzid": course["clazz_id"],
        "cpi": course.get("cpi") or "",
        "ut": "s",
    }
    resp = await sess.get(CHAPTER_URL, params=params, headers={"Referer": f"{MOOC2_HOST}/"})
    return parse_chapters(text_of(resp))


# ---------------------------------------------------------------- 任务点卡片
_DOC_SUFFIXES = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt")
_NODE_RE = re.compile(r"nodeId_(.*?)-")
_MARG_RE = re.compile(r"mArg\s*=\s*")


def _brace_slice(text: str, start: int) -> str | None:
    """从 start（一个 `{`）开始按花括号配对切出完整 JSON 片段。"""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_marg(body: str) -> str | None:
    """抽取 mArg 的 JSON 原文。

    真实页面里 mArg 会被赋值两次：

        mArg = "";                      ← 先清空
        try { mArg = {"defaults":…,"attachments":[…]}; } catch(e){}

    所以不能只取第一处 `mArg=`，要跳过空串、拿那个真能 json.loads 成 dict 的。
    同时在原始文本上配对花括号（不预先压空白），避免把标题里的空格吃掉。
    """
    for m in _MARG_RE.finditer(body):
        start = m.end()
        if start >= len(body) or body[start] != "{":
            continue  # mArg = ""; 这种初始化
        raw = _brace_slice(body, start)
        if not raw:
            continue
        candidates = [raw]
        if raw.startswith("{{") and raw.endswith("}}"):
            candidates.append(raw[1:-1])  # 个别页面是 mArg={{...}}
        for cand in candidates:
            try:
                data = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and ("attachments" in data or "defaults" in data):
                return cand
    return None


def parse_cards(body: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """返回 (jobs, info)。info 含 ktoken/cpi/knowledgeid/notOpen 等。"""
    if "章节未开放" in body:
        return [], {"notOpen": True}

    raw = _extract_marg(body)
    if not raw:
        logger.debug("页面里没有可解析的 mArg")
        return [], {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("mArg JSON 解析失败")
        return [], {}

    defaults = data.get("defaults") or {}
    info: dict[str, Any] = {
        "ktoken": defaults.get("ktoken", ""),
        "mtEnc": defaults.get("mtEnc", ""),
        "reportTimeInterval": int(defaults.get("reportTimeInterval") or 60),
        "defenc": defaults.get("defenc", ""),
        "cardid": defaults.get("cardid", ""),
        "cpi": defaults.get("cpi", ""),
        "qnenc": defaults.get("qnenc", ""),
        "knowledgeid": str(defaults.get("knowledgeid", "")),
    }

    jobs: list[dict[str, Any]] = []
    for card in data.get("attachments") or []:
        job = _card_to_job(card, info)
        if job:
            # 已通过的任务点也要留在结果里，否则重扫时旧行永远停在 pending
            job["passed"] = bool(card.get("isPassed"))
            # 卡片带 videoFaceCaptureEnc = 这门课开了人脸识别/活体校验，
            # 该凭证只能由前端摄像头通过后生成，脚本只能原样透传
            if job.get("att", {}).get("videoFaceCaptureEnc"):
                job["face"] = True
            jobs.append(job)
    return jobs, info


def _card_to_job(card: dict[str, Any], info: dict[str, Any]) -> dict[str, Any] | None:
    prop = card.get("property") or {}
    ctype = str(card.get("type") or "").lower()
    other_info = str(card.get("otherInfo") or "").split("&")[0]
    job_id = str(card.get("jobid") or "")
    object_id = str(card.get("objectId") or prop.get("objectid") or "")

    is_live = (
        "live" in ctype
        or prop.get("liveId") is not None
        or prop.get("streamName") is not None
        or prop.get("vdoid") is not None
    )

    base = {
        "job_id": job_id,
        "object_id": object_id,
        "other_info": other_info,
        "jtoken": str(card.get("jtoken") or ""),
        "enc": str(card.get("enc") or ""),
        "play_time": int(card.get("playTime") or 0) // 1000,
        "att": {
            k: card[k]
            for k in ("attDuration", "attDurationEnc", "videoFaceCaptureEnc")
            if card.get(k)
        },
    }

    if is_live:
        base.update(type="live", title=str(prop.get("title") or prop.get("name") or "直播"))
        return base

    if not card.get("job") and not ctype and not card.get("mid"):
        # 真正的空页/占位卡片：没有类型、没有 mid、也不是任务点
        base.update(type="emptypage", title=str(prop.get("title") or "空章节"))
        return base

    # 文档优先：超星很多 PDF/PPT 卡片的 type 也写成 video，光看 ctype 会误判成视频，
    # 结果去请求 ananas/status 拿到的是 download/pdf 载荷，必然 dtoken 缺失。
    name = str(prop.get("name") or prop.get("title") or "")
    if ctype == "document" or name.lower().endswith(_DOC_SUFFIXES):
        base.update(type="document", title=name or "文档")
        m = _NODE_RE.search(other_info)
        base["knowledge_id"] = m.group(1) if m else info.get("knowledgeid", "")
        return base

    if ctype == "video" or card.get("mid"):
        base.update(
            type="video",
            title=name or "视频",
            rt=str(prop.get("rt") or ""),
            mid=str(card.get("mid") or ""),
        )
        m = _NODE_RE.search(other_info)
        base["knowledge_id"] = m.group(1) if m else info.get("knowledgeid", "")
        return base

    if ctype == "read" or not card.get("job"):
        base.update(type="read", title=str(prop.get("title") or "阅读"))
        return base

    if ctype == "workid":
        base.update(type="work", title=str(prop.get("name") or "章节测验"))
        base["att"].update(
            {
                k: info[k]
                for k in ("ktoken", "qnenc", "cpi", "knowledgeid")
                if info.get(k)
            }
        )
        return base

    logger.debug(f"未识别任务点类型: {ctype} {job_id}")
    return None


async def fetch_jobs(
    sess: CxSession,
    course: dict[str, Any],
    knowledge_id: str,
    *,
    expect: int = 0,
    max_num: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    seen: set[str] = set()

    for num in range(max_num + 1):
        params = {
            "clazzid": course["clazz_id"],
            "courseid": course["course_id"],
            "knowledgeid": knowledge_id,
            "num": num,
            "ut": "s",
            "cpi": course.get("cpi") or "",
            "v": CARDS_VERSION,
            "mooc2": 1,
        }
        resp = await sess.get(CARDS_URL, params=params, headers={"Referer": f"{MOOC2_HOST}/"})
        batch, batch_info = parse_cards(text_of(resp))
        if batch_info.get("notOpen"):
            return [], {"notOpen": True}
        if batch_info:
            info.update({k: v for k, v in batch_info.items() if v not in ("", None)})
        added = 0
        for job in batch:
            jid = job["job_id"] or f"{knowledge_id}-{job['type']}-{added}"
            if jid in seen:
                continue
            seen.add(jid)
            job["job_id"] = jid
            jobs.append(job)
            added += 1
        if added == 0 and num > 0:
            break
        if expect and len(jobs) >= expect:
            break

    interval = info.get("reportTimeInterval")
    if interval:
        for job in jobs:
            job.setdefault("report_time_interval", int(interval))
    return jobs, info


async def touch_empty_chapter(sess: CxSession, course: dict[str, Any], knowledge_id: str) -> bool:
    params = {
        "courseId": course["course_id"],
        "clazzid": course["clazz_id"],
        "chapterId": knowledge_id,
        "cpi": course.get("cpi") or "",
        "verificationcode": "",
        "mooc2": 1,
        "microTopicId": 0,
        "editorPreview": 0,
    }
    resp = await sess.get(EMPTY_PAGE_URL, params=params, headers={"Referer": f"{MOOC2_HOST}/"})
    return resp.status_code == 200
