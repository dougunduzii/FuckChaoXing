"""SQLite 持久化：单连接 + 线程锁，够用且简单。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
TERMINAL_STATES = ("done", "skip")


def _now() -> int:
    return int(time.time())


def _att_of(job: dict[str, Any]) -> dict[str, Any]:
    """把任务点里需要原样回传的字段合并进 att，避免额外加列。"""
    att = dict(job.get("att") or {})
    for key in ("rt", "mid", "report_time_interval"):
        if job.get(key) and key not in att:
            att[key] = job[key]
    return att


class Repo:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 基础 ----------------
    def _q(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def _one(self, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self._q(sql, args)
        return rows[0] if rows else None

    def _x(self, sql: str, args: Sequence[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid or 0

    # ---------------- account ----------------
    def ensure_account(self, name: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM account WHERE name=?", (name,))
        if row:
            return row
        self._x("INSERT INTO account(name) VALUES(?)", (name,))
        return self._one("SELECT * FROM account WHERE name=?", (name,))  # type: ignore[return-value]

    def save_account(
        self,
        name: str,
        cookies: dict[str, str],
        *,
        puid: str | None = None,
        fid: str | None = None,
        status: str = "ok",
        note: str | None = None,
    ) -> None:
        self.ensure_account(name)
        self._x(
            """UPDATE account SET cookies=?, puid=COALESCE(?,puid), fid=COALESCE(?,fid),
                   status=?, last_login_ts=?, note=COALESCE(?,note) WHERE name=?""",
            (json.dumps(cookies, ensure_ascii=False), puid, fid, status, _now(), note, name),
        )

    def get_account(self, name: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM account WHERE name=?", (name,))

    def list_accounts(self) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM account ORDER BY id")

    def set_account_status(self, name: str, status: str, note: str | None = None) -> None:
        self._x("UPDATE account SET status=?, note=COALESCE(?,note) WHERE name=?", (status, note, name))

    # ---------------- course ----------------
    def upsert_course(
        self, account_id: int, *, course_id: str, clazz_id: str, cpi: str, title: str, teacher: str
    ) -> int:
        self._x(
            """INSERT INTO course(account_id,course_id,clazz_id,cpi,title,teacher)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(account_id,course_id,clazz_id) DO UPDATE SET
                 cpi=excluded.cpi, title=excluded.title, teacher=excluded.teacher""",
            (account_id, course_id, clazz_id, cpi, title, teacher),
        )
        row = self._one(
            "SELECT id FROM course WHERE account_id=? AND course_id=? AND clazz_id=?",
            (account_id, course_id, clazz_id),
        )
        return int(row["id"]) if row else 0

    def list_courses(self, account_id: int) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM course WHERE account_id=? ORDER BY id", (account_id,))

    def find_course(self, account_id: int, keyword: str) -> list[dict[str, Any]]:
        return self._q(
            "SELECT * FROM course WHERE account_id=? AND (title LIKE ? OR course_id=? OR clazz_id=?)",
            (account_id, f"%{keyword}%", keyword, keyword),
        )

    def set_course_status(self, course_pk: int, status: str) -> None:
        self._x("UPDATE course SET status=? WHERE id=?", (status, course_pk))

    # ---------------- chapter ----------------
    def upsert_chapter(
        self, course_pk: int, *, knowledge_id: str, title: str, job_count: int, state: str = "pending"
    ) -> int:
        self._x(
            """INSERT INTO chapter(course_pk,knowledge_id,title,job_count,state)
               VALUES(?,?,?,?,?)
               ON CONFLICT(course_pk,knowledge_id) DO UPDATE SET
                 title=excluded.title, job_count=excluded.job_count,
                 state=CASE WHEN chapter.state='done' THEN 'done' ELSE excluded.state END""",
            (course_pk, knowledge_id, title, job_count, state),
        )
        row = self._one(
            "SELECT id FROM chapter WHERE course_pk=? AND knowledge_id=?", (course_pk, knowledge_id)
        )
        return int(row["id"]) if row else 0

    def list_chapters(self, course_pk: int) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM chapter WHERE course_pk=? ORDER BY id", (course_pk,))

    def set_chapter_state(self, chapter_pk: int, state: str) -> None:
        self._x("UPDATE chapter SET state=? WHERE id=?", (state, chapter_pk))

    # ---------------- task_point ----------------
    def upsert_task(self, chapter_pk: int, job: dict[str, Any]) -> int:
        self._x(
            """INSERT INTO task_point(chapter_pk,job_id,object_id,type,title,other_info,
                                      jtoken,enc,att_json,duration,play_time,updated_ts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 chapter_pk=excluded.chapter_pk, object_id=excluded.object_id,
                 type=excluded.type, title=excluded.title, other_info=excluded.other_info,
                 jtoken=excluded.jtoken, enc=excluded.enc, att_json=excluded.att_json,
                 duration=MAX(task_point.duration, excluded.duration),
                 updated_ts=excluded.updated_ts""",
            (
                chapter_pk,
                job["job_id"],
                job.get("object_id", ""),
                job["type"],
                job.get("title", ""),
                job.get("other_info", ""),
                job.get("jtoken", ""),
                job.get("enc", ""),
                json.dumps(_att_of(job), ensure_ascii=False),
                int(job.get("duration") or 0),
                int(job.get("play_time") or 0),
                _now(),
            ),
        )
        row = self._one("SELECT id FROM task_point WHERE job_id=?", (job["job_id"],))
        return int(row["id"]) if row else 0

    def reset_tasks(
        self,
        *,
        account_id: int | None = None,
        course_pk: int | None = None,
        types: Iterable[str] | None = None,
    ) -> int:
        """把任务点重新置为待执行，并清掉本地进度（下次 cx scan 会跟服务端对齐）。"""
        where = []
        args: list[Any] = []
        if course_pk is not None:
            where.append("chapter_pk IN (SELECT id FROM chapter WHERE course_pk=?)")
            args.append(course_pk)
        elif account_id is not None:
            where.append(
                "chapter_pk IN (SELECT c.id FROM chapter c JOIN course co ON co.id=c.course_pk"
                " WHERE co.account_id=?)"
            )
            args.append(account_id)
        if types:
            tl = list(types)
            where.append("type IN (" + ",".join("?" * len(tl)) + ")")
            args.extend(tl)
        sql = (
            "UPDATE task_point SET state='pending', attempts=0, last_err=NULL, play_time=0"
            + (" WHERE " + " AND ".join(where) if where else "")
        )
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            n = int(cur.rowcount or 0)
        self._x("UPDATE chapter SET state='pending' WHERE state IN ('done','running')")
        return n

    def delete_task(self, job_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM task_point WHERE job_id=?", (job_id,))
            self._conn.commit()
            return int(cur.rowcount or 0)

    def get_task(self, task_pk: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM task_point WHERE id=?", (task_pk,))

    def iter_tasks(
        self,
        *,
        account_id: int | None = None,
        course_pk: int | None = None,
        types: Iterable[str] | None = None,
        states: Iterable[str] = ("pending", "failed", "running"),
    ) -> list[dict[str, Any]]:
        sql = [
            "SELECT t.*, c.course_id, c.clazz_id, c.cpi, c.account_id, c.title AS course_title,",
            "       ch.knowledge_id, ch.title AS chapter_title",
            "FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk",
            "JOIN course c ON c.id=ch.course_pk WHERE 1=1",
        ]
        args: list[Any] = []
        if account_id is not None:
            sql.append("AND c.account_id=?")
            args.append(account_id)
        if course_pk is not None:
            sql.append("AND ch.course_pk=?")
            args.append(course_pk)
        if types:
            tlist = list(types)
            sql.append(f"AND t.type IN ({','.join('?' * len(tlist))})")
            args.extend(tlist)
        if states:
            slist = list(states)
            sql.append(f"AND t.state IN ({','.join('?' * len(slist))})")
            args.extend(slist)
        sql.append("ORDER BY c.id, ch.id, t.id")
        return self._q(" ".join(sql), args)

    def set_task_state(
        self, task_pk: int, state: str, *, err: str | None = None, inc_attempt: bool = False
    ) -> None:
        self._x(
            """UPDATE task_point SET state=?, last_err=COALESCE(?,last_err),
                   attempts=attempts+?, updated_ts=? WHERE id=?""",
            (state, err, 1 if inc_attempt else 0, _now(), task_pk),
        )

    def set_task_progress(self, task_pk: int, play_time: int, duration: int | None = None) -> None:
        if duration is None:
            self._x("UPDATE task_point SET play_time=?, updated_ts=? WHERE id=?", (play_time, _now(), task_pk))
        else:
            self._x(
                "UPDATE task_point SET play_time=?, duration=?, updated_ts=? WHERE id=?",
                (play_time, duration, _now(), task_pk),
            )

    def recover_running(self) -> int:
        """上次异常退出留下的 running 重置为 pending，进度保留。"""
        with self._lock:
            cur = self._conn.execute("UPDATE task_point SET state='pending' WHERE state='running'")
            self._conn.commit()
            return cur.rowcount

    def task_stats(self) -> list[dict[str, Any]]:
        return self._q(
            """SELECT type, state, COUNT(*) AS n FROM task_point GROUP BY type, state ORDER BY type, state"""
        )

    def chapter_pending_count(self, chapter_pk: int) -> int:
        row = self._one(
            """SELECT COUNT(*) AS n FROM task_point
               WHERE chapter_pk=? AND state NOT IN ('done','skip')""",
            (chapter_pk,),
        )
        return int(row["n"]) if row else 0

    # ---------------- quiz ----------------
    def quiz_get(self, qkey: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM quiz WHERE qkey=?", (qkey,))

    def quiz_put(
        self, qkey: str, *, title: str, options: str, qtype: str, answer: str, source: str
    ) -> None:
        self._x(
            """INSERT INTO quiz(qkey,title,options,qtype,answer,source,updated_ts)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(qkey) DO UPDATE SET
                 answer=excluded.answer, source=excluded.source, updated_ts=excluded.updated_ts""",
            (qkey, title, options, qtype, answer, source, _now()),
        )

    def quiz_bump(self, qkey: str, *, ok: bool) -> None:
        self._x(
            "UPDATE quiz SET hit=hit+1, ok=ok+? WHERE qkey=?", (1 if ok else 0, qkey)
        )

    def quiz_export(self) -> list[dict[str, Any]]:
        return self._q("SELECT qkey,title,options,qtype,answer,source FROM quiz ORDER BY updated_ts DESC")

    def quiz_import(self, rows: Iterable[dict[str, Any]]) -> int:
        n = 0
        for r in rows:
            if not r.get("qkey") or not r.get("title"):
                continue
            self.quiz_put(
                r["qkey"],
                title=r["title"],
                options=r.get("options", ""),
                qtype=r.get("qtype", ""),
                answer=r.get("answer", ""),
                source=r.get("source", "import"),
            )
            n += 1
        return n

    def quiz_stats(self) -> dict[str, Any]:
        total = self._one("SELECT COUNT(*) AS n FROM quiz") or {"n": 0}
        by_src = self._q("SELECT source, COUNT(*) AS n FROM quiz GROUP BY source")
        return {"total": total["n"], "by_source": by_src}

    # ---------------- runs / meta ----------------
    def audit(
        self, *, account_id: int | None, task_pk: int | None, method: str, path: str,
        status: int, elapsed_ms: int, snippet: str = "",
    ) -> None:
        self._x(
            """INSERT INTO runs(account_id,task_pk,ts,method,path,status,elapsed_ms,snippet)
               VALUES(?,?,?,?,?,?,?,?)""",
            (account_id, task_pk, _now(), method, path[:300], status, elapsed_ms, snippet[:200]),
        )

    def recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))

    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self._x(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
