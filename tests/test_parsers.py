"""解析层与加密层的离线单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoxing.api import course as course_api
from chaoxing.api import quiz as quiz_api
from chaoxing.crypto import aes, enc

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 加密
def test_aes_roundtrip():
    for text in ("13800000000", "p@ssw0rd!中文", "x"):
        assert aes.decrypt(aes.encrypt(text)) == text


def test_aes_known_length():
    # PKCS7 后一定整块，base64 长度应为 24 的倍数
    out = aes.encrypt("13800000000")
    assert len(out) % 24 == 0


def test_enc_v2_shape():
    got = enc.enc_v2("1234", "88", "job-1", "obj-1", 60, 600)
    expect = enc.md5("[1234][88][job-1][obj-1][60000][d_yHJ!$pdA~5][600000][0_600]")
    assert got == expect
    assert len(got) == 32


def test_enc_v1_differs_from_v2():
    a = enc.enc_v1("1234", "88", "job-1", "obj-1", 60, 600)
    b = enc.enc_v2("1234", "88", "job-1", "obj-1", 60, 600)
    assert a != b
    assert a == enc.md5("[1234][88][job-1][obj-1][60000][0_600]")


def test_enc_respects_playing_time():
    a = enc.enc_v2("1", "2", "3", "4", 10, 100)
    b = enc.enc_v2("1", "2", "3", "4", 11, 100)
    assert a != b


def test_clip_time():
    assert enc.clip_time(600) == "0_600"
    assert enc.clip_time(600, 30) == "30_600"


# ---------------------------------------------------------------- 课程
def test_parse_courses_skips_not_open():
    courses = course_api.parse_courses(read("courses.html"))
    assert len(courses) == 2
    first = courses[0]
    assert first["course_id"] == "5678"
    assert first["clazz_id"] == "1234"
    assert first["cpi"] == "99999"
    assert first["title"] == "高等数学（上）"
    assert first["teacher"] == "张三"
    assert all(c["title"] != "未开放课程" for c in courses)


def test_parse_chapters_states():
    chapters = course_api.parse_chapters(read("chapters.html"))
    by_kid = {c["knowledge_id"]: c for c in chapters}
    # 章级节点（div.chapter_item 无 id）必须被排除，只留 #cur 知识点
    assert set(by_kid) == {"1001", "1002", "1003", "1004"}

    assert by_kid["1001"]["finished"] and by_kid["1001"]["job_count"] == 3
    assert by_kid["1001"]["title"] == "1.1 行列式的定义"

    assert not by_kid["1002"]["finished"] and not by_kid["1002"]["locked"]
    assert by_kid["1002"]["job_count"] == 5

    assert by_kid["1003"]["locked"]

    # 第三级嵌套目录也要抓出来
    assert by_kid["1004"]["job_count"] == 1
    assert by_kid["1004"]["title"] == "1.2.1 子式与余子式"


# ---------------------------------------------------------------- 任务点
def test_parse_cards_jobs():
    jobs, info = course_api.parse_cards(read("cards.html"))
    assert info["ktoken"] == "KT"
    assert info["reportTimeInterval"] == 90
    assert info["knowledgeid"] == "1002"

    by_type = {j["type"]: j for j in jobs}
    assert set(by_type) == {"video", "document", "work", "read"}, by_type.keys()
    assert len(jobs) == 5  # 已通过的也保留，仅打 passed 标记
    assert [j["passed"] for j in jobs if j["job_id"] == "job-done-1"] == [True]

    video = by_type["video"]
    video = next(j for j in jobs if j["job_id"] == "job-video-1")
    assert video["play_time"] == 120
    assert video["object_id"] == "obj001"
    assert video["other_info"] == "nodeId_1002-rt_d-1"  # 只保留 & 前
    assert video["att"]["attDuration"] == 600
    assert video["att"]["attDurationEnc"] == "adenc1"
    assert video["att"]["videoFaceCaptureEnc"] == "vf1"

    assert by_type["work"]["job_id"] == "work-123456"
    assert by_type["read"]["job_id"] == "job-read-1"
    assert by_type["document"]["other_info"] == "nodeId_1002-rt_d-2"


def test_parse_cards_not_open():
    jobs, info = course_api.parse_cards("<div>该章节未开放</div>")
    assert jobs == []
    assert info["notOpen"] is True


def test_parse_cards_garbage():
    jobs, info = course_api.parse_cards("<html>nothing here</html>")
    assert jobs == []
    assert info == {}


# ---------------------------------------------------------------- 测验
def test_parse_work_questions():
    parsed = quiz_api.parse_work(read("work.html"))
    assert parsed.form["courseId"] == "5678"
    assert parsed.form["workId"] == "123456"
    assert not any("answer" in k for k in parsed.form)

    assert len(parsed.questions) == 3
    q1, q2, q3 = parsed.questions
    assert q1.id == "7001" and q1.qtype == "single"
    assert "中国的首都" in q1.title
    assert len(q1.options) == 4
    assert q1.options[0] == "A.北京"
    assert q2.qtype == "multiple"
    assert q3.qtype == "judgement"


def test_qkey_stable_against_whitespace():
    from chaoxing.utils import md5, norm_text

    a = quiz_api.Question(id="1", qtype="single", type_code="0", title="中国的首都是？", options=[])
    b = quiz_api.Question(id="2", qtype="single", type_code="0", title=" 中国的首都是 ？ ", options=[])
    assert a.qkey == b.qkey
    assert a.qkey == md5(f"single|{norm_text('中国的首都是？')}")


def test_parse_work_no_form():
    parsed = quiz_api.parse_work("<html>没有表单</html>")
    assert parsed.questions == []
    assert parsed.form == {}


@pytest.mark.parametrize("mark", ["教师未创建完成该测验", "该测验暂未开放"])
def test_not_ready_marks(mark):
    from chaoxing.task.quiz import _NOT_READY_MARKS

    assert any(m in mark for m in _NOT_READY_MARKS)
