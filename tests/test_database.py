import sqlite3
from app.database import get_connection, get_db, init_db, SCHEMA_SQL


def test_get_connection_returns_sqlite_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    conn = get_connection()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_get_db_context_manager(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    with get_db() as conn:
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM test").fetchone()
        assert row["id"] == 1


def test_init_db_creates_all_tables(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "specifications" in names
        assert "clauses" in names
        assert "classification_rules" in names
        assert "classification_queue" in names
        assert "users" in names


def test_init_db_creates_fts5(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t["name"] for t in tables]
        fts_tables = [n for n in names if "fts" in n.lower()]
        assert len(fts_tables) >= 1


def test_init_db_is_idempotent(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    init_db()  # 不应报错


def test_clauses_table_has_clause_is_non_column(monkeypatch, tmp_path):
    """clauses 表应有 clause_is_non 列，且默认 0（存量 INSERT 未列新列也可兜底）"""
    db_path = tmp_path / "test_noncol.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(clauses)")]
        assert "clause_is_non" in cols
        # 存量 INSERT（未显式给出 clause_is_non）默认落库为 0
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-T', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "内容"),
        )
        row = conn.execute("SELECT clause_is_non FROM clauses WHERE clause_no='1.0.1'").fetchone()
        assert row["clause_is_non"] == 0


def test_synonym_map_table_exists(monkeypatch, tmp_path):
    """init_db 应创建 synonym_map 表"""
    db_path = tmp_path / "test_syn_table.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "synonym_map" in names


def test_synonym_map_seed_idempotent(monkeypatch, tmp_path):
    """init_db 预置常用同义词且幂等（重复初始化不产生重复行）"""
    db_path = tmp_path / "test_syn_seed.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    init_db()  # 再次初始化，验证幂等
    with get_db() as conn:
        rows = conn.execute(
            "SELECT source, target, is_active FROM synonym_map ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        sources = {r["source"] for r in rows}
        assert sources == {"砼", "箍筋"}
        assert all(r["is_active"] == 1 for r in rows)


# ═══════════════════════════════════════════
# FTS5 + jieba 预分词 Schema（search_text 列 / 独立表 / 触发器同步 / 旧库迁移）
# ═══════════════════════════════════════════

def test_clauses_table_has_search_text_column(monkeypatch, tmp_path):
    """clauses 应有 search_text 列；存量 INSERT 未给该列时兜底不报错"""
    db_path = tmp_path / "test_st.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(clauses)")]
        assert "search_text" in cols
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-T', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "钢筋 内容"),
        )  # 不带 search_text → NULL 兜底，不应报错


def test_clauses_fts_is_independent_table(monkeypatch, tmp_path):
    """clauses_fts 应为独立 fts5 表（索引 search_text），而非 external content 表"""
    db_path = tmp_path / "test_ftsi.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        fts_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
        ).fetchone()["sql"]
        assert "search_text" in fts_sql, "FTS 表应索引 search_text 列"
        assert "content=clauses" not in fts_sql, "不应再是 external content 表"


def test_fts_trigger_syncs_search_text(monkeypatch, tmp_path):
    """INSERT/UPDATE/DELETE clauses 时 FTS 表随 search_text 同步"""
    db_path = tmp_path / "test_fts_sync.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-T', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # INSERT（带 search_text）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "钢筋 内容", "钢筋 内容 1.0.1"),
        )
        cid = conn.execute("SELECT id FROM clauses WHERE clause_no='1.0.1'").fetchone()[0]
        fts = conn.execute(
            "SELECT search_text FROM clauses_fts WHERE rowid=?", (cid,)
        ).fetchone()
        assert fts and "钢筋" in fts["search_text"], "INSERT 后 FTS 应同步 search_text"
        # UPDATE search_text
        conn.execute("UPDATE clauses SET search_text='更新后文本 1.0.1' WHERE id=?", (cid,))
        fts = conn.execute(
            "SELECT search_text FROM clauses_fts WHERE rowid=?", (cid,)
        ).fetchone()
        assert fts and fts["search_text"] == "更新后文本 1.0.1", "UPDATE 后 FTS 应同步"
        # DELETE
        conn.execute("DELETE FROM clauses WHERE id=?", (cid,))
        fts = conn.execute(
            "SELECT search_text FROM clauses_fts WHERE rowid=?", (cid,)
        ).fetchone()
        assert fts is None, "DELETE 后 FTS 应移除对应行"


def test_fts_trigger_null_search_text_uses_empty(monkeypatch, tmp_path):
    """INSERT 不带 search_text（存量/测试路径）→ FTS 存空串不报错"""
    db_path = tmp_path / "test_fts_null.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-T', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "内容"),
        )
        cid = conn.execute("SELECT id FROM clauses WHERE clause_no='1.0.1'").fetchone()[0]
        fts = conn.execute(
            "SELECT search_text FROM clauses_fts WHERE rowid=?", (cid,)
        ).fetchone()
        assert fts is not None, "NULL search_text 应兜底为空串，不报 datatype mismatch"


def test_migrates_old_external_fts(monkeypatch, tmp_path):
    """旧 external content FTS 表 → init_db 迁移为独立 search_text 表并 backfill 存量"""
    db_path = tmp_path / "old_fts.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE specifications (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, title TEXT);
        CREATE TABLE clauses (id INTEGER PRIMARY KEY AUTOINCREMENT, spec_id INTEGER,
            clause_no TEXT, title TEXT, content TEXT);
        CREATE VIRTUAL TABLE clauses_fts USING fts5(clause_no, title, content,
            content=clauses, content_rowid=id);
        CREATE TRIGGER IF NOT EXISTS clauses_ai AFTER INSERT ON clauses BEGIN
            INSERT INTO clauses_fts(rowid, clause_no, title, content)
            VALUES (new.id, new.clause_no, new.title, new.content); END;
        INSERT INTO specifications (code, title) VALUES ('GB-T', '旧规范');
        INSERT INTO clauses (spec_id, clause_no, title, content)
        VALUES (1, '1.0.1', '总则', '钢筋 相关旧内容');
    """)
    conn.close()
    init_db()  # 触发迁移
    with get_db() as conn:
        fts_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
        ).fetchone()["sql"]
        assert "content=clauses" not in fts_sql, "迁移后应为独立表"
        assert "search_text" in fts_sql, "迁移后 FTS 表应索引 search_text"
        # 存量条文被 backfill 生成 search_text
        row = conn.execute(
            "SELECT search_text FROM clauses WHERE clause_no='1.0.1'"
        ).fetchone()
        assert row["search_text"] and "钢筋" in row["search_text"], "存量条文应 backfill search_text"
        # FTS 表已回填该条文
        fts_row = conn.execute("SELECT search_text FROM clauses_fts").fetchone()
        assert fts_row is not None, "迁移后 FTS 表应有存量数据"
