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
    dim1_industry   TEXT,           -- 规范所属行业（JGJ→建筑工程，仅行业标准类有值）
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
    search_text     TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- FTS5 独立表：索引 jieba 预分词后的 search_text（rowid 即 clause id）。
-- 不再是 external content 表——外部内容表只能索引 clauses 原列（存的必须是
-- 原始文本供渲染），无法索引「分词后文本」，而 SQLite 触发器又不能调 Python，
-- 故 jieba 分词结果由应用层写入 search_text 列，FTS 表只索引该列。
CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(
    search_text
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
    label           TEXT,           -- 规则赋值标签（命中后写入分类列）；NULL 时兼容回退用 pattern
    locked          INTEGER DEFAULT 0,  -- 锁定后不纳入僵尸规则判断（预置/需长期保留的规则）
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

CREATE TABLE IF NOT EXISTS qa_request_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question        TEXT,
    mode            TEXT,
    backend         TEXT,
    include_invalid INTEGER DEFAULT 0,
    rrf_total       INTEGER,
    pool_size       INTEGER,
    after_meta      INTEGER,
    after_threshold INTEGER,
    select_target   INTEGER,
    high_count      INTEGER,
    low_count       INTEGER,
    context_tokens  INTEGER,
    budget          INTEGER,
    dropped_overflow INTEGER,
    context_empty   INTEGER DEFAULT 0,
    rerank_used     TEXT,
    duration_ms     INTEGER,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS lexicon_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    canonical    TEXT NOT NULL,
    variants     TEXT NOT NULL DEFAULT '',
    distinguish  TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lexicon_kind_canonical_variants
    ON lexicon_entries(kind, canonical, variants);
"""

TRIGGERS_SQL = """
-- FTS 表 rowid 有唯一约束；FTS5 的 'delete' 命令在本环境报 SQL logic error，
-- 故同步删除用标准 DELETE FROM fts WHERE rowid（对不存在的 rowid 是 no-op）。
CREATE TRIGGER IF NOT EXISTS clauses_ai AFTER INSERT ON clauses BEGIN
    INSERT INTO clauses_fts(rowid, search_text)
    VALUES (new.id, COALESCE(new.search_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS clauses_ad AFTER DELETE ON clauses BEGIN
    DELETE FROM clauses_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS clauses_au AFTER UPDATE ON clauses BEGIN
    DELETE FROM clauses_fts WHERE rowid = old.id;
    INSERT INTO clauses_fts(rowid, search_text)
    VALUES (new.id, COALESCE(new.search_text, ''));
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


def _migrate_search_text(conn):
    """FTS5 + jieba 预分词迁移：加 search_text 列、旧 external FTS 表重建为独立表、backfill。

    顺序敏感：
    1. 先加 search_text 列（旧库无此列时，触发器引用 new.search_text 会失败）；
    2. drop 旧触发器 + 旧 external content FTS 表；
    3. 建独立 fts5(search_text) 表；
    4. backfill 存量条文（生成 search_text 并回填 FTS，此时无触发器干扰）。
    幂等：已迁移库再次 init_db，FTS 表已是独立表、存量 search_text 非空则跳过。
    """
    try:
        conn.execute("ALTER TABLE clauses ADD COLUMN search_text TEXT")
    except Exception:
        pass  # 列已存在

    # drop 旧触发器（无论新旧先删，避免旧定义残留；重建在 TRIGGERS_SQL 中）
    for t in ("clauses_ai", "clauses_ad", "clauses_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {t}")

    # 旧库 external content FTS 表 → drop 重建为独立表
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
    ).fetchone()
    if row and "content=clauses" in (row["sql"] or ""):
        conn.execute("DROP TABLE clauses_fts")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(search_text)")

    # backfill：search_text 为空的行生成分词并回填 FTS（无触发器，手动同步）。
    # 先 DELETE 再 INSERT：若该行已通过触发器写入 FTS（如 INSERT 不带 search_text
    # 存了空串行），直接 INSERT 同 rowid 会触发 FTS rowid 唯一约束冲突。
    from app.search.tokenize import build_search_text
    rows = conn.execute(
        "SELECT id, clause_no, title, content, search_text FROM clauses"
    ).fetchall()
    for r in rows:
        st = build_search_text(r["clause_no"], r["title"], r["content"])
        if (r["search_text"] or "") != st:
            conn.execute("UPDATE clauses SET search_text = ? WHERE id = ?", (st, r["id"]))
            conn.execute("DELETE FROM clauses_fts WHERE rowid = ?", (r["id"],))
            conn.execute(
                "INSERT INTO clauses_fts(rowid, search_text) VALUES (?, ?)",
                (r["id"], st),
            )


def _migrate_legacy_synonyms(conn):
    """幂等迁移：把旧 synonym_map 行拷贝进 lexicon（不 DROP，DDL 删除见收尾单元）。

    分类规则：
      - source='箍筋' and target='钢筋' → confusable(箍筋 × 钢筋)
      - 其余行 → alias(canonical=target, variants=该 target 全部 source 逗号合并)
    仅当 synonym_map 表存在时执行（新库无此表 → 直接跳过）。
    """
    tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='synonym_map'"
    ).fetchone()
    if not tbl:
        return
    rows = conn.execute(
        "SELECT source, target, is_active FROM synonym_map ORDER BY id"
    ).fetchall()
    # 聚合：按 target 分组 source（保留顺序、去重）
    by_target: dict[str, list[str]] = {}
    for r in rows:
        if r["source"] == "箍筋" and r["target"] == "钢筋":
            conn.execute(
                "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,is_active) "
                "VALUES ('confusable','箍筋','钢筋',"
                "'箍筋是钢筋加工成型的构造钢筋（子类），用于约束核心混凝土，不等同于全部钢筋',"
                "'由旧 synonym_map 迁移',?)",
                (r["is_active"],))
            continue
        by_target.setdefault(r["target"], [])
        if r["source"] not in by_target[r["target"]]:
            by_target[r["target"]].append(r["source"])
    for target, sources in by_target.items():
        if not sources:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,note,is_active) "
            "VALUES ('alias',?,?,?,?)",
            (target, ",".join(sources), "由旧 synonym_map 迁移",
             1 if sources else 1))


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        # FTS5 + jieba 迁移必须在建触发器之前（触发器引用 search_text 列）
        _migrate_search_text(conn)
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
        # 迁移：为已有数据库添加 replace_by_spec_id（被替代关系引用）与 spec_version（预留）
        try:
            conn.execute(
                "ALTER TABLE specifications ADD COLUMN replace_by_spec_id INTEGER "
                "REFERENCES specifications(id)"
            )
        except Exception:
            pass  # 列已存在
        try:
            conn.execute("ALTER TABLE specifications ADD COLUMN spec_version TEXT")
        except Exception:
            pass  # 列已存在
        # 迁移：为已有数据库的 classification_rules 添加 label 列（规则赋值标签，NULL 回退 pattern）
        try:
            conn.execute("ALTER TABLE classification_rules ADD COLUMN label TEXT")
        except Exception:
            pass  # 列已存在
        # 迁移：locked 列（锁定后不纳入僵尸规则判断）
        try:
            conn.execute("ALTER TABLE classification_rules ADD COLUMN locked INTEGER DEFAULT 0")
        except Exception:
            pass  # 列已存在
        # 迁移：specifications 加 dim1_industry（规范所属行业，层级归并后单独承载行业）
        try:
            conn.execute("ALTER TABLE specifications ADD COLUMN dim1_industry TEXT")
        except Exception:
            pass  # 列已存在
        # 日志表 + 健康检查快照表（P1 维护工具先建表，P3 日志界面消费）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                level       TEXT NOT NULL,
                action      TEXT NOT NULL,
                detail      TEXT,
                username    TEXT,
                duration_ms INTEGER,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_system_logs_category ON system_logs(category);
            CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);
            CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs(created_at);
            CREATE TABLE IF NOT EXISTS health_check_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                result      TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            """
        )
        # 参数设置方案表（默认方案为虚拟 id=0，不落库）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS param_profiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                is_system   INTEGER NOT NULL DEFAULT 0,
                values_json TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                updated_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            """
        )
        # ── 词库子系统 ─────────────────────────────────────────────
        # 预置种子（幂等：全行唯一键 + INSERT OR IGNORE；本库无 synonym_map 时也建演示对）
        conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,note,is_active) "
            "VALUES ('alias','混凝土','砼','预置种子',1)")
        conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,is_active) "
            "VALUES ('confusable','箍筋','钢筋',"
            "'箍筋是钢筋加工成型的构造钢筋（子类），用于约束核心混凝土，不等同于全部钢筋',"
            "'预置种子',1)")
        _migrate_legacy_synonyms(conn)
        # 预置常用同义词（保留至 Task 8 收尾删表；synonym_routes 与旧检索归一化仍消费 synonym_map）
        conn.execute(
            "INSERT OR IGNORE INTO synonym_map (source, target, is_active) VALUES (?, ?, 1)",
            ("砼", "混凝土"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO synonym_map (source, target, is_active) VALUES (?, ?, 1)",
            ("箍筋", "钢筋"),
        )
