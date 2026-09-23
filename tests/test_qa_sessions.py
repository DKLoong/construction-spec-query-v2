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

    这是事实断言而非期望断言——SQLite 默认 PRAGMA foreign_keys=OFF，
    因此级联删除**不可依赖**，delete_session 必须应用层显式删（T5 覆盖）。
    本用例存在的意义：若将来有人开启外键，这里会失败并提醒复核 T5。
    """
    with get_db() as conn:
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_on == 0, "外键状态变了：请复核 sessions.delete_session 的显式删除是否仍必要"
