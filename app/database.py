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
    file_hash       TEXT,
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
    clause_is_non   INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS synonym_map (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,     -- 原词（待替换）
    target      TEXT NOT NULL,     -- 目标词（规范用词）
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- source 唯一索引：保证预置同义词 INSERT OR IGNORE 的幂等性
CREATE UNIQUE INDEX IF NOT EXISTS idx_synonym_map_source ON synonym_map(source);
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
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
        # 迁移：为已有数据库添加 file_hash 列
        try:
            conn.execute("ALTER TABLE specifications ADD COLUMN file_hash TEXT")
        except Exception:
            pass  # 列已存在
        # 迁移：为已有数据库添加 clause_is_non 列（INTEGER DEFAULT 0，允许 NULL）
        try:
            conn.execute("ALTER TABLE clauses ADD COLUMN clause_is_non INTEGER DEFAULT 0")
        except Exception:
            pass  # 列已存在
        # 预置常用同义词（幂等：依赖 source 唯一索引 + INSERT OR IGNORE）
        conn.execute(
            "INSERT OR IGNORE INTO synonym_map (source, target, is_active) VALUES (?, ?, 1)",
            ("砼", "混凝土"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO synonym_map (source, target, is_active) VALUES (?, ?, 1)",
            ("箍筋", "钢筋"),
        )
