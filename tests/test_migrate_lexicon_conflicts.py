"""词库同词跨组冲突的清理迁移测试（scripts/migrate_lexicon_conflicts.py）

用合成数据覆盖四类动作，断言「清理后无同词跨组」——即读侧 _check_equiv_unique
能通过，词库不再被整表作废。

裁决按**词**表达（不按行 id），保证测试可复现、迁移可重跑。
"""
import sqlite3

import pytest


def _make_db(tmp_path, rows):
    """rows: [(kind, canonical, variants)] → 建表并插入"""
    from app.database import init_db

    conn = sqlite3.connect(str(tmp_path / "mig.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lexicon_entries ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, canonical TEXT NOT NULL,"
        " variants TEXT, distinguish TEXT, note TEXT, is_active INTEGER DEFAULT 1,"
        " created_at TEXT DEFAULT (datetime('now','localtime')),"
        " updated_at TEXT DEFAULT (datetime('now','localtime')))")
    # ⚠️ 必须与生产库同索引：否则同样的 SQL 返回不同行序。
    #    2026-09-20 教训——缺此索引时测试库按 rowid 返回 synonym 行（含"安全绳"），
    #    真实库按 (kind,canonical,variants) 返回 alias 行（不含），
    #    结果"专项选中错行"的 bug 只在真实数据上暴露，dry-run 才拦下。
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lexicon_kind_canonical_variants "
                 "ON lexicon_entries(kind, canonical, variants)")
    for kind, canonical, variants in rows:
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES (?,?,?)",
                     (kind, canonical, variants))
    conn.commit()
    return conn


# 合成冲突集：每一类动作各一组
_ROWS = [
    # ① SAME_SET：词集相同、方向相反 → 保留 alias 侧
    ("synonym", "截流井", "截留井"),
    ("alias", "截流井", "截留井"),
    # ② CHAIN：同 canonical、variants 不同 → 并集
    ("synonym", "酸碱度", "pH值"),
    ("alias", "酸碱度", "pH,pH值"),
    # ③ 人工裁决·合并：canonical=顶棚
    ("synonym", "顶棚", "天棚,天花板"),
    ("alias", "吊顶", "天花,天花板"),
    # ④ 人工裁决·拆分：安全带与安全绳是两个不同产品
    #    ⚠️ 形状必须与真实数据一致：**两行同 canonical=安全带**且只有一行含「安全绳」
    #    （2026-09-20 教训：2 行 fixture 掩盖了「专项选中错行 → 通用合并把安全绳并走」的 bug）
    ("synonym", "安全带", "保险带,安全绳"),
    ("alias", "安全带", "保险带"),
    ("alias", "安全绳", "生命绳"),
    # ⑤ 人工裁决·专项：删除 canonical=石子 且 variants 含碎石 的行
    ("synonym", "石子", "碎石,卵石"),
    ("alias", "粗骨料", "石子,粗集料"),
    ("alias", "碎石", "轧碎岩石"),
]


def _word_owner_map(conn):
    """词 → 拥有它的行 id 集合；>1 即同词跨组"""
    owner = {}
    for r in conn.execute("SELECT id, canonical, variants FROM lexicon_entries "
                          "WHERE kind IN ('synonym','alias') AND is_active=1"):
        words = {r["canonical"]} | {v.strip() for v in (r["variants"] or "").split(",") if v.strip()}
        for w in words:
            owner.setdefault(w, set()).add(r["id"])
    return owner


def _rows_by_canonical(conn, canonical):
    return conn.execute(
        "SELECT * FROM lexicon_entries WHERE canonical=?", (canonical,)).fetchall()


def test_resolve_leaves_no_cross_group_word(tmp_path):
    """清理后不得有任何词同时属于两行（这是词库能否加载的唯一条件）"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    broken = {w: sorted(ids) for w, ids in _word_owner_map(conn).items() if len(ids) > 1}
    conn.close()
    assert not broken, f"仍有同词跨组: {broken}"


def test_same_set_keeps_alias_side(tmp_path):
    """同词汇反向：保留 alias 侧（规范词方向），删冗余行"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    rows = _rows_by_canonical(conn, "截流井")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["kind"] == "alias"


def test_chain_unions_variants(tmp_path):
    """同 canonical：variants 取并集，不留重复行"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    rows = _rows_by_canonical(conn, "酸碱度")
    conn.close()
    assert len(rows) == 1
    variants = {v.strip() for v in rows[0]["variants"].split(",")}
    assert variants == {"pH", "pH值"}


def test_approved_merge_uses_decided_canonical(tmp_path):
    """人工裁决的合并：canonical 用裁决指定的词，variants 收全部同义面"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    top = _rows_by_canonical(conn, "顶棚")
    ceiling = _rows_by_canonical(conn, "吊顶")
    conn.close()
    assert len(top) == 1 and not ceiling, "吊顶 应并入 顶棚 组"
    assert {v.strip() for v in top[0]["variants"].split(",")} == {"天棚", "天花板", "吊顶", "天花"}


def test_safety_rope_is_split_not_merged(tmp_path):
    """安全带/安全绳 是两个不同产品：安全绳不得并入安全带组，且自身独立成组"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    belt = _rows_by_canonical(conn, "安全带")
    rope = _rows_by_canonical(conn, "安全绳")
    conn.close()
    assert len(belt) == 1, "安全带 组应只剩一行"
    assert "安全绳" not in belt[0]["variants"], "安全绳 必须从安全带组移除"
    assert {v.strip() for v in belt[0]["variants"].split(",")} == {"保险带"}
    assert len(rope) == 1 and rope[0]["variants"] == "生命绳", "安全绳 应独立成组"


def test_stone_row_deleted_not_merged(tmp_path):
    """石子=碎石,卵石 是上下位误当同义：删该行，另两组各自保留"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    stone = _rows_by_canonical(conn, "石子")
    coarse = _rows_by_canonical(conn, "粗骨料")
    crushed = _rows_by_canonical(conn, "碎石")
    conn.close()
    assert not stone, "canonical=石子 的组应被删除"
    assert len(coarse) == 1 and len(crushed) == 1


def test_resolve_is_idempotent(tmp_path):
    """可重跑：第二次执行不改变任何行"""
    from scripts.migrate_lexicon_conflicts import resolve_conflicts

    conn = _make_db(tmp_path, _ROWS)
    resolve_conflicts(conn)
    snap1 = [tuple(r) for r in conn.execute(
        "SELECT id,kind,canonical,variants FROM lexicon_entries ORDER BY id")]
    resolve_conflicts(conn)
    snap2 = [tuple(r) for r in conn.execute(
        "SELECT id,kind,canonical,variants FROM lexicon_entries ORDER BY id")]
    conn.close()
    assert snap1 == snap2, "重复执行不得有任何变化"
