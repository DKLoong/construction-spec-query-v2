"""知识库健康检查（纯逻辑，供维护路由包装）

检查项与修复动作：
- orphan_parent        孤立无父级条文（parent_clause 悬空）→ 置 NULL
- empty_content        空内容条文 → 仅报告（不自动删）
- bad_classification   ai_classified=1 但 dim4/5/6 全空 → 重置 ai_classified=0, needs_review=1
- vector_orphan        向量有而 SQLite 无 → 调 VectorStore.sync_with_db 删孤儿
- vector_missing       SQLite 有而向量无 → 调 VectorStore.index_missing 补索引
- fts_mismatch         clauses_fts 缺 rowid → 增量补插 search_text
"""
from app.database import get_db
from app.logging_util import log_action

LABELS = {
    "orphan_parent": "孤立无父级条文",
    "empty_content": "空内容条文",
    "bad_classification": "分类标签异常",
    "vector_orphan": "向量孤儿记录",
    "vector_missing": "缺失向量索引",
    "fts_mismatch": "FTS 索引缺失",
}


def _count_orphan_parent() -> int:
    with get_db() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM clauses c
               WHERE c.parent_clause IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM clauses p WHERE p.id = c.parent_clause)"""
        ).fetchone()[0]


def _count_empty_content() -> int:
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE content IS NULL OR TRIM(content) = ''"
        ).fetchone()[0]


def _count_bad_classification() -> int:
    with get_db() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM clauses
               WHERE ai_classified = 1
                 AND COALESCE(dim4_specialty, '') = ''
                 AND COALESCE(dim5_location, '') = ''
                 AND COALESCE(dim6_material, '') = ''"""
        ).fetchone()[0]


def _vector_ids() -> set[int] | None:
    """读取向量表全部 clause_id；表不存在/读失败返回 None（区别于空集）"""
    try:
        from app.search.vector_search import VectorStore
        vs = VectorStore()
        if not vs._table_exists():
            return None
        tbl = vs._get_table()
        return {int(v) for v in tbl.to_arrow().column("clause_id").to_pylist()}
    except Exception:
        return None


def _count_vector_orphan(vids: set[int] | None = None) -> int:
    if vids is None:
        vids = _vector_ids()
    if not vids:
        return 0
    with get_db() as conn:
        db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
    return len(vids - db_ids)


def _count_vector_missing(vids: set[int] | None = None) -> int:
    if vids is None:
        vids = _vector_ids()
    if vids is None:
        # 向量表不存在：缺失数按 0 计，语义由 run_health_check 标 error（待重建）
        return 0
    with get_db() as conn:
        db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
    return len(db_ids - vids)


def _count_fts_mismatch() -> int:
    with get_db() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM clauses c
               WHERE NOT EXISTS (SELECT 1 FROM clauses_fts f WHERE f.rowid = c.id)"""
        ).fetchone()[0]


def run_health_check() -> dict:
    """执行全部检查，写 system_logs + health_check_snapshots，返回结果 dict"""
    vector_ids = _vector_ids()  # None 表示向量表不存在/读失败（区别于空集）
    counts = {
        "orphan_parent": _count_orphan_parent(),
        "empty_content": _count_empty_content(),
        "bad_classification": _count_bad_classification(),
        "vector_orphan": _count_vector_orphan(vector_ids),
        "vector_missing": _count_vector_missing(vector_ids),
        "fts_mismatch": _count_fts_mismatch(),
    }
    checks = []
    for key, label in LABELS.items():
        count = counts[key]
        fixable = key != "empty_content"
        severity = "ok" if count == 0 else ("warn" if fixable else "error")
        if key == "vector_missing" and vector_ids is None:
            # 向量表不存在：语义为「待重建」，非「可单项修复」，标 error 走「需人工」分支
            count = 0
            fixable = False
            severity = "error"
        checks.append({
            "key": key, "label": label, "count": count,
            "severity": severity,
            "fixable": fixable,
        })
    result = {"checks": checks}
    # 持久化：写日志 + 快照（表由 P0 schema 迁移建好）
    import json
    log_action("maintenance", "INFO", "健康检查", detail=json.dumps(result))
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO health_check_snapshots (result) VALUES (?)",
                (json.dumps(result),),
            )
    except Exception:
        pass
    return result


def fix_issue(key: str) -> dict:
    """单项修复，返回 {key, fixed, detail}"""
    if key == "orphan_parent":
        with get_db() as conn:
            n = conn.execute(
                """UPDATE clauses SET parent_clause = NULL
                   WHERE parent_clause IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM clauses p WHERE p.id = clauses.parent_clause)"""
            ).rowcount
        log_action("maintenance", "INFO", "修复孤立条文", detail=str(n))
        return {"key": key, "fixed": n > 0, "detail": f"已置空 {n} 条悬空引用"}
    if key == "empty_content":
        # 仅报告，不自动删（人工决定）
        return {"key": key, "fixed": False, "detail": "空内容条文仅报告，不自动删除"}
    if key == "bad_classification":
        with get_db() as conn:
            n = conn.execute(
                """UPDATE clauses SET ai_classified = 0, needs_review = 1
                   WHERE ai_classified = 1
                     AND COALESCE(dim4_specialty, '') = ''
                     AND COALESCE(dim5_location, '') = ''
                     AND COALESCE(dim6_material, '') = ''"""
            ).rowcount
        log_action("maintenance", "INFO", "重置分类异常", detail=str(n))
        return {"key": key, "fixed": n > 0, "detail": f"已重置 {n} 条进入复核"}
    if key == "vector_orphan":
        from app.search.vector_search import VectorStore
        removed = VectorStore().sync_with_db()
        log_action("maintenance", "INFO", "清理孤儿向量", detail=str(removed))
        return {"key": key, "fixed": removed > 0, "detail": f"已清理 {removed} 条孤儿向量"}
    if key == "vector_missing":
        from app.search.vector_search import VectorStore
        added = VectorStore().index_missing()
        if added < 0:
            # 向量表不存在/读失败：单项补齐无意义，需全量重建
            log_action("maintenance", "WARN", "补齐缺失向量失败（向量表不存在）")
            return {"key": key, "fixed": False, "detail": "向量表不存在，请使用「重建向量索引」"}
        log_action("maintenance", "INFO", "补齐缺失向量", detail=str(added))
        return {"key": key, "fixed": added > 0, "detail": f"已补齐 {added} 条向量"}
    if key == "fts_mismatch":
        with get_db() as conn:
            n = conn.execute(
                """INSERT INTO clauses_fts(rowid, search_text)
                   SELECT c.id, COALESCE(c.search_text, '') FROM clauses c
                   WHERE NOT EXISTS (SELECT 1 FROM clauses_fts f WHERE f.rowid = c.id)"""
            ).rowcount
        log_action("maintenance", "INFO", "补齐 FTS 索引", detail=str(n))
        return {"key": key, "fixed": n > 0, "detail": f"已补齐 {n} 条 FTS 索引"}
    return {"key": key, "fixed": False, "detail": f"未知检查项: {key}"}


def fix_all() -> list[dict]:
    """一键修复全部可修复项"""
    results = []
    for key in LABELS:
        results.append(fix_issue(key))
    return results
