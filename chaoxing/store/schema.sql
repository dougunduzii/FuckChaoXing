PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS account (
    id            INTEGER PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    puid          TEXT,
    fid           TEXT,
    cookies       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'ok',   -- ok / cooldown / need_login / banned
    last_login_ts INTEGER,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS course (
    id         INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    course_id  TEXT NOT NULL,
    clazz_id   TEXT NOT NULL,
    cpi        TEXT,
    title      TEXT,
    teacher    TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (account_id, course_id, clazz_id)
);

CREATE TABLE IF NOT EXISTS chapter (
    id           INTEGER PRIMARY KEY,
    course_pk    INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL,
    title        TEXT,
    job_count    INTEGER DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'pending', -- pending/done/skip_locked/skip_closed
    UNIQUE (course_pk, knowledge_id)
);

CREATE TABLE IF NOT EXISTS task_point (
    id         INTEGER PRIMARY KEY,
    chapter_pk INTEGER NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
    job_id     TEXT UNIQUE NOT NULL,
    object_id  TEXT,
    type       TEXT NOT NULL,                    -- video/document/read/work/live
    title      TEXT,
    other_info TEXT,
    jtoken     TEXT,
    enc        TEXT,
    att_json   TEXT,                             -- attDuration / videoFaceCaptureEnc 等原样透传
    duration   INTEGER NOT NULL DEFAULT 0,
    play_time  INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL DEFAULT 'pending',  -- pending/running/done/failed/skip
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_err   TEXT,
    updated_ts INTEGER
);

CREATE INDEX IF NOT EXISTS idx_task_state ON task_point(state);
CREATE INDEX IF NOT EXISTS idx_task_chapter ON task_point(chapter_pk);

CREATE TABLE IF NOT EXISTS quiz (
    qkey       TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    options    TEXT,
    qtype      TEXT,
    answer     TEXT,
    source     TEXT,        -- manual / llm / api / random
    hit        INTEGER NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 0,
    updated_ts INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    account_id INTEGER,
    task_pk    INTEGER,
    ts         INTEGER,
    method     TEXT,
    path       TEXT,
    status     INTEGER,
    elapsed_ms INTEGER,
    snippet    TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
