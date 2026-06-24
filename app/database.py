import sqlite3
from contextlib import contextmanager
from app.config import DATABASE_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS specifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    title           TEXT NOT NULL,
    short_name      TEXT,
    dim1_hierarchy  TEXT,
    dim1_nature     TEXT,
    dim1_sys_level  TEXT,
    dim1_spec_type  TEXT,
    dim2_stage      TEXT,
    dim3_usage      TEXT,
    dim3_construction TEXT,
    dim3_scale      TEXT,
    status          TEXT DEFAULT '现行',
    source_path     TEXT,
    output_dir      TEXT,
    clause_count    INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS clauses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id         INTEGER NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
    clause_no       TEXT NOT NULL,
    title           TEXT,
    content         TEXT NOT NULL,
    parent_clause   INTEGER REFERENCES clauses(id),
    dim4_specialty  TEXT,
    dim5_location   TEXT,
    dim6_material   TEXT,
    ai_classified   INTEGER DEFAULT 0,
    needs_review    INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(
    clause_no, title, content,
    dim4_specialty, dim5_location, dim6_material,
    content=clauses, content_rowid=id
);

CREATE TABLE IF NOT EXISTS classification_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension       TEXT NOT NULL,
    sub_field       TEXT,
    pattern         TEXT NOT NULL,
    match_type      TEXT DEFAULT 'keyword',
    priority        INTEGER DEFAULT 0,
    threshold       REAL NOT NULL,
    hit_count       INTEGER DEFAULT 0,
    confirmed       INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS classification_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    clause_id       INTEGER NOT NULL REFERENCES clauses(id) ON DELETE CASCADE,
    dimension       TEXT NOT NULL,
    keyword_score   REAL,
    batch_id        TEXT,
    ai_label        TEXT,
    ai_confidence   REAL,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
"""

TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS clauses_ai AFTER INSERT ON clauses BEGIN
    INSERT INTO clauses_fts(rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES (new.id, new.clause_no, new.title, new.content,
        new.dim4_specialty, new.dim5_location, new.dim6_material);
END;

CREATE TRIGGER IF NOT EXISTS clauses_ad AFTER DELETE ON clauses BEGIN
    INSERT INTO clauses_fts(clauses_fts, rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES ('delete', old.id, old.clause_no, old.title, old.content,
        old.dim4_specialty, old.dim5_location, old.dim6_material);
END;

CREATE TRIGGER IF NOT EXISTS clauses_au AFTER UPDATE ON clauses BEGIN
    INSERT INTO clauses_fts(clauses_fts, rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES ('delete', old.id, old.clause_no, old.title, old.content,
        old.dim4_specialty, old.dim5_location, old.dim6_material);
    INSERT INTO clauses_fts(rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES (new.id, new.clause_no, new.title, new.content,
        new.dim4_specialty, new.dim5_location, new.dim6_material);
END;
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(TRIGGERS_SQL)
