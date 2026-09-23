"""会话与消息表结构。"""
from app.database import get_db


def test_qa_sessions_table_exists(qa_db):
    """正常场景：会话表建好且含预期列。"""
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(qa_sessions)")}
    assert {"id", "title", "created_at", "updated_at"} <= cols


def test_qa_messages_table_exists(qa_db):
    """正常场景：消息表建好且含预期列。"""
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(qa_messages)")}
    assert {"id", "session_id", "role", "content", "sources_json",
            "confusable_json", "filters_json", "mode", "created_at"} <= cols


def test_qa_messages_index_exists(qa_db):
    """边界场景：按会话取消息的索引存在（历史会话列表/详情依赖）。"""
    with get_db() as conn:
        idx = {r[1] for r in conn.execute("PRAGMA index_list(qa_messages)")}
    assert "idx_qa_messages_session" in idx


def test_qa_foreign_keys_state_is_documented(qa_db):
    """异常场景：记录 SQLite 外键实际状态。

    **事实断言而非期望断言**（首版计划此处写反了，经 Task 4 实测更正）：
    本项目在 `app/database.py` 的 `get_db()` 里执行 `PRAGMA foreign_keys=ON`
    （自 `b1d08fe` 起就有，非本计划引入），因此 `ON DELETE CASCADE`
    **是生效的** —— 见下文 delete_session 的说明。

    本用例存在的意义：它会在**有人移除该 PRAGMA** 时失败，而那正是级联删除
    静默失效的时刻，需要复核 T5 的显式删除路径。
    """
    with get_db() as conn:
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_on == 1, (
        "外键被关闭了（PRAGMA foreign_keys 不再是 ON）——"
        "级联删除将静默失效，请复核 sessions.delete_session 的显式删除是否仍在"
    )
