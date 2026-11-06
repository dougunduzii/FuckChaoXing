"""面板专用只读查询层。

为什么不把这些 SQL 塞进 ``store/repo.py``：``cx web`` 只是 CLI 的一个可选子命令，
它需要的统计维度（按课程汇总、分页筛任务点、今日用量）只有面板用得到。放进 Repo
会让核心存储层跟着面板的需求长，而 CLI 侧完全用不上——所以这里借 Repo 已有的
连接与锁（``_q``/``_x``），保持 ``store/repo.py`` 一行不改。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from ..store.repo import Repo

TASK_STATES = ("pending", "running", "done", "failed", "skip")


def task_totals(repo: Repo, account_id: int | None = None) -> dict[str, int]:
    """按状态汇总任务点，附带 ``total``。"""
    where, args = "", ()
    if account_id is not None:
        where, args = "WHERE c.account_id=?", (account_id,)
    rows = repo._q(
        f"""SELECT t.state, COUNT(*) AS n
            FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
            JOIN course c ON c.id=ch.course_pk {where} GROUP BY t.state""",
        args,
    )
    out = {str(r["state"]): int(r["n"]) for r in rows}
    out["total"] = sum(out.values())
    return out


def task_stats(repo: Repo, account_id: int | None = None) -> list[dict[str, Any]]:
    where, args = "", ()
    if account_id is not None:
        where, args = "WHERE c.account_id=?", (account_id,)
    return repo._q(
        f"""SELECT t.type, t.state, COUNT(*) AS n
            FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
            JOIN course c ON c.id=ch.course_pk {where}
            GROUP BY t.type, t.state ORDER BY t.type, t.state""",
        args,
    )


def course_overview(repo: Repo, account_id: int | None = None) -> list[dict[str, Any]]:
    """课程 → 章节数 / 任务点总数 / 完成 / 失败。"""
    where, args = "", ()
    if account_id is not None:
        where, args = "WHERE c.account_id=?", (account_id,)
    return repo._q(
        f"""SELECT c.id, c.title, c.teacher, c.account_id, a.name AS account,
                   (SELECT COUNT(*) FROM chapter ch WHERE ch.course_pk=c.id) AS chapters,
                   (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                     WHERE ch.course_pk=c.id) AS total,
                   (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                     WHERE ch.course_pk=c.id AND t.state='done') AS done,
                   (SELECT COUNT(*) FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk
                     WHERE ch.course_pk=c.id AND t.state='failed') AS failed
            FROM course c JOIN account a ON a.id=c.account_id
            {where} ORDER BY c.id""",
        args,
    )


def _task_query(
    *,
    account_id: int | None,
    course_pk: int | None,
    types: Iterable[str] | None,
    states: Iterable[str] | None,
    keyword: str | None,
    count: bool,
) -> tuple[str, list[Any]]:
    head = (
        "SELECT COUNT(*) AS n FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk"
        " JOIN course c ON c.id=ch.course_pk"
        if count
        else
        "SELECT t.id, t.type, t.title, t.state, t.attempts, t.last_err, t.play_time,"
        " t.duration, t.updated_ts, c.id AS course_pk, c.title AS course, ch.title AS chapter"
        " FROM task_point t JOIN chapter ch ON ch.id=t.chapter_pk"
        " JOIN course c ON c.id=ch.course_pk"
    )
    sql = [head, "WHERE 1=1"]
    args: list[Any] = []
    if account_id is not None:
        sql.append("AND c.account_id=?")
        args.append(account_id)
    if course_pk is not None:
        sql.append("AND ch.course_pk=?")
        args.append(course_pk)
    if types:
        items = [t for t in types if t]
        if items:
            sql.append(f"AND t.type IN ({','.join('?' * len(items))})")
            args.extend(items)
    if states:
        items = [s for s in states if s]
        if items:
            sql.append(f"AND t.state IN ({','.join('?' * len(items))})")
            args.extend(items)
    if keyword:
        sql.append("AND (t.title LIKE ? OR c.title LIKE ? OR ch.title LIKE ?)")
        like = f"%{keyword}%"
        args.extend([like, like, like])
    return " ".join(sql), args


def list_tasks(
    repo: Repo,
    *,
    account_id: int | None = None,
    course_pk: int | None = None,
    types: Iterable[str] | None = None,
    states: Iterable[str] | None = None,
    keyword: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql, args = _task_query(
        account_id=account_id, course_pk=course_pk, types=types,
        states=states, keyword=keyword, count=False,
    )
    sql += " ORDER BY c.id, ch.id, t.id LIMIT ? OFFSET ?"
    args.extend([max(1, int(limit)), max(0, int(offset))])
    return repo._q(sql, args)


def count_tasks(
    repo: Repo,
    *,
    account_id: int | None = None,
    course_pk: int | None = None,
    types: Iterable[str] | None = None,
    states: Iterable[str] | None = None,
    keyword: str | None = None,
) -> int:
    sql, args = _task_query(
        account_id=account_id, course_pk=course_pk, types=types,
        states=states, keyword=keyword, count=True,
    )
    row = repo._one(sql, args)
    return int(row["n"]) if row else 0


def usage_minutes(repo: Repo, account_id: int | None = None) -> float:
    """今日已用分钟数（Engine 写的是 ``usage:<date>:<account_id>``）。"""
    prefix = f"usage:{date.today().isoformat()}:"
    total = 0
    for key, value in repo._q("SELECT key, value FROM meta WHERE key LIKE ?", (f"{prefix}%",)):
        if account_id is not None and not str(key).endswith(f":{account_id}"):
            continue
        try:
            total += int(value or 0)
        except ValueError:
            continue
    return round(total / 60, 1)


def delete_account(repo: Repo, name: str) -> int:
    return repo._x("DELETE FROM account WHERE name=?", (name,))


def last_error(repo: Repo, account_id: int) -> str:
    """该账号最近一条失败任务的错误，给面板当提示用。"""
    row = repo._one(
        """SELECT t.last_err, t.updated_ts
           FROM task_point t
           JOIN chapter ch ON ch.id=t.chapter_pk
           JOIN course c ON c.id=ch.course_pk
           WHERE c.account_id=? AND t.last_err IS NOT NULL AND TRIM(t.last_err) <> ''
           ORDER BY t.updated_ts DESC, t.id DESC LIMIT 1""",
        (account_id,),
    )
    return str(row["last_err"]) if row else ""

