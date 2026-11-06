"""存储层：upsert 幂等、进度保留、断点恢复。"""

from __future__ import annotations

import pytest

from chaoxing.store.repo import Repo


@pytest.fixture()
def repo(tmp_path):
    r = Repo(tmp_path / "t.db")
    yield r
    r.close()


def _seed(repo: Repo) -> tuple[int, int, int]:
    acc = repo.ensure_account("me")
    cid = repo.upsert_course(
        acc["id"], course_id="5678", clazz_id="1234", cpi="99999", title="高数", teacher="张三"
    )
    ch = repo.upsert_chapter(cid, knowledge_id="1002", title="1.2", job_count=2)
    job = repo.upsert_task(
        ch,
        {
            "job_id": "job-1",
            "object_id": "obj-1",
            "type": "video",
            "title": "视频1",
            "other_info": "nodeId_1002-rt_d-1",
            "jtoken": "jt",
            "enc": "e",
            "att": {"attDuration": 600, "rt": "0.9"},
            "play_time": 30,
        },
    )
    return acc["id"], cid, job


def test_upsert_course_idempotent(repo: Repo):
    acc = repo.ensure_account("me")
    a = repo.upsert_course(
        acc["id"], course_id="1", clazz_id="2", cpi="c", title="A", teacher="T"
    )
    b = repo.upsert_course(
        acc["id"], course_id="1", clazz_id="2", cpi="c2", title="A2", teacher="T2"
    )
    assert a == b
    assert len(repo.list_courses(acc["id"])) == 1
    assert repo.list_courses(acc["id"])[0]["cpi"] == "c2"


def test_task_upsert_keeps_progress(repo: Repo):
    _, _, job_pk = _seed(repo)
    repo.set_task_progress(job_pk, 500, 600)
    repo.set_task_state(job_pk, "done")
    # 再抓一次，不应覆盖进度与状态
    ch = repo._q("SELECT id FROM chapter")[0]["id"]
    repo.upsert_task(
        ch,
        {
            "job_id": "job-1",
            "object_id": "obj-1",
            "type": "video",
            "title": "视频1改",
            "att": {"attDuration": 600},
            "play_time": 0,
        },
    )
    task = repo.get_task(job_pk)
    assert task["play_time"] == 500
    assert task["state"] == "done"
    assert task["title"] == "视频1改"


def test_iter_tasks_join_and_filters(repo: Repo):
    acc_id, cid, _ = _seed(repo)
    rows = repo.iter_tasks(account_id=acc_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["course_id"] == "5678"
    assert row["clazz_id"] == "1234"
    assert row["knowledge_id"] == "1002"
    assert row["course_title"] == "高数"

    assert repo.iter_tasks(account_id=acc_id, types=["document"]) == []
    assert len(repo.iter_tasks(account_id=acc_id, types=["video"])) == 1
    assert repo.iter_tasks(account_id=acc_id, course_pk=cid + 999) == []


def test_recover_running(repo: Repo):
    _, _, job_pk = _seed(repo)
    repo.set_task_state(job_pk, "running")
    assert repo.recover_running() == 1
    assert repo.get_task(job_pk)["state"] == "pending"
    assert repo.recover_running() == 0


def test_chapter_pending_and_auto_done(repo: Repo):
    _, _, job_pk = _seed(repo)
    ch = repo._q("SELECT id FROM chapter")[0]["id"]
    assert repo.chapter_pending_count(ch) == 1
    repo.set_task_state(job_pk, "done")
    assert repo.chapter_pending_count(ch) == 0


def test_quiz_roundtrip(repo: Repo):
    repo.quiz_put("k1", title="题干", options="A.对", qtype="judgement", answer="A", source="manual")
    row = repo.quiz_get("k1")
    assert row["answer"] == "A"
    repo.quiz_bump("k1", ok=True)
    assert repo.quiz_get("k1")["hit"] == 1
    assert repo.quiz_stats()["total"] == 1
    assert len(repo.quiz_export()) == 1

    n = repo.quiz_import([{"qkey": "k2", "title": "题干2", "answer": "B"}])
    assert n == 1
    assert repo.quiz_import([{"title": "无 key"}]) == 0


def test_meta(repo: Repo):
    assert repo.meta_get("enc_version", "v2") == "v2"
    repo.meta_set("enc_version", "v1")
    assert repo.meta_get("enc_version") == "v1"
    repo.meta_set("enc_version", "v2")
    assert repo.meta_get("enc_version") == "v2"


def test_audit(repo: Repo):
    repo.audit(account_id=1, task_pk=None, method="GET", path="/x", status=200, elapsed_ms=12)
    rows = repo.recent_runs(5)
    assert rows and rows[0]["path"] == "/x"
