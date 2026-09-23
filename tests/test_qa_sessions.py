"""会话与消息：表结构（T4）+ 持久化层 app.qa.sessions（T5）。"""
from app.database import get_db
from app.qa import sessions as S


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


def test_derive_title_truncates_long_question(qa_db):
    """正常场景：超长问题截断到 TITLE_MAX_CHARS。"""
    q = "混凝土强度等级应如何评定" * 5
    assert len(S.derive_title(q)) == S.TITLE_MAX_CHARS


def test_derive_title_keeps_short_question(qa_db):
    """边界场景：短问题取全文。"""
    assert S.derive_title("那检验批怎么划分") == "那检验批怎么划分"


def test_derive_title_strips_whitespace(qa_db):
    """边界场景：首尾空白被清理，全空白退化为兜底名。"""
    assert S.derive_title("  混凝土强度  ") == "混凝土强度"
    assert S.derive_title("   ") == "新会话"


def test_create_and_get_session(qa_db):
    """正常场景：建会话后可按 id 取回。"""
    sid = S.create_session("混凝土强度")
    got = S.get_session(sid)
    assert got is not None and got["title"] == "混凝土强度"


def test_get_session_missing_returns_none(qa_db):
    """异常场景：不存在的 id 返回 None，不抛异常。"""
    assert S.get_session(999999) is None


def test_append_message_and_read_back(qa_db):
    """正常场景：消息落库并可读回，sources 反序列化为 list。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "问题")
    S.append_message(sid, "assistant", "答案",
                     sources=[{"code": "GB 50204", "clause_no": "8.2.1"}])
    msgs = S.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["sources"][0]["code"] == "GB 50204"


def test_append_message_records_filters(qa_db):
    """正常场景：当轮生效筛选随助手消息落库，回看时可还原（D5）。

    筛选不入库则该信息不可逆丢失——同一个问题按「混凝土」专业筛与不筛，
    答案来源完全不同，事后无法推断。
    """
    sid = S.create_session("s")
    S.append_message(sid, "assistant", "答案",
                     filters={"dim4_specialty": ["混凝土"], "status_filter": "现行"})
    msgs = S.get_messages(sid)
    assert msgs[0]["filters"]["dim4_specialty"] == ["混凝土"]


def test_get_messages_filters_default_empty_dict(qa_db):
    """边界场景：未记录筛选时返回空 dict（而非 None 或缺失键），前端免判空。"""
    sid = S.create_session("s")
    S.append_message(sid, "assistant", "答案")
    assert S.get_messages(sid)[0]["filters"] == {}


def test_get_messages_tolerates_bad_filters_json(qa_db):
    """异常场景：filters_json 脏数据退化为 {}，不抛异常中断整条会话。"""
    sid = S.create_session("s")
    S.append_message(sid, "assistant", "答案")
    with get_db() as conn:
        conn.execute("UPDATE qa_messages SET filters_json = ? WHERE session_id = ?",
                     ("not-json", sid))
    assert S.get_messages(sid)[0]["filters"] == {}


def test_concurrent_appends_same_session_do_not_lose_or_mix(qa_db):
    """异常场景（并发）：同一会话并发追加消息不重不漏、不错位。

    开启多轮与流式后，一次问答可持续数秒到数十秒；同一会话可能被两个
    标签页（或手快连点两次）同时写入。本用例验证上层已依赖的不变量：
    **消息按 id 顺序落库、互不覆盖**。

    注：`get_db()` 的 `sqlite3.connect(..., timeout=30)` 负责把并发写
    串行化，本用例同时是那个 timeout 的守卫（取消它会让此测试报
    "database is locked"）。
    """
    from concurrent.futures import ThreadPoolExecutor

    sid = S.create_session("并发")

    def write(i: int) -> None:
        S.append_message(sid, "user", f"q{i}")
        S.append_message(sid, "assistant", f"a{i}")

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(write, range(8)))

    msgs = S.get_messages(sid)
    assert len(msgs) == 16, f"并发写入丢消息或多消息（实际 {len(msgs)} 条）"
    assert {m["content"] for m in msgs} == (
        {f"q{i}" for i in range(8)} | {f"a{i}" for i in range(8)}
    ), "并发写入内容错乱或被覆盖"
    assert [m["id"] for m in msgs] == sorted(m["id"] for m in msgs), \
        "消息未按 id 升序返回"


def test_append_message_bumps_updated_at(qa_db):
    """边界场景：追加消息必须刷新 updated_at，否则列表排序不反映活跃度。

    断言用**严格大于**：微秒精度下追加必然晚于建会话，相等即说明
    append_message 根本没碰 updated_at（用 `>=` 会让本用例对
    「不刷时间戳」这个它名字里的缺陷也能通过）。
    """
    sid = S.create_session("s")
    before = S.get_session(sid)
    assert before is not None
    S.append_message(sid, "user", "q")
    after = S.get_session(sid)
    assert after is not None
    assert after["updated_at"] > before["updated_at"]


def test_touch_session_bumps_updated_at_without_adding_message(qa_db):
    """正常场景：无新消息时也能把会话顶上去（会话管理栏的置顶/续聊依赖它）。

    守卫两点：时间戳确实被刷新（排序生效），且不产生任何消息（它不是
    「写一条空消息」的假刷新）。
    """
    sid = S.create_session("s")
    before = S.get_session(sid)
    assert before is not None
    S.touch_session(sid)
    after = S.get_session(sid)
    assert after is not None
    assert after["updated_at"] > before["updated_at"]
    assert S.get_messages(sid) == []


def test_list_sessions_orders_by_recent_activity(qa_db):
    """正常场景：列表按最近活跃倒序。"""
    a = S.create_session("A")
    b = S.create_session("B")
    S.append_message(a, "user", "让 A 变活跃")
    assert S.list_sessions()[0]["id"] == a


def test_list_sessions_reports_message_count(qa_db):
    """正常场景：列表带消息数（前端可判断空会话）。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    S.append_message(sid, "assistant", "a")
    assert S.list_sessions()[0]["msg_count"] == 2


def test_list_sessions_includes_empty_sessions(qa_db):
    """边界场景：零消息的会话仍出现在列表里，计数为 0。

    守卫 LEFT JOIN 的语义——写成 INNER JOIN 会静默丢掉所有空会话，
    而「草稿态会话」正是靠列表可见性管理的。
    """
    S.create_session("空会话")
    rows = S.list_sessions()
    assert len(rows) == 1
    assert rows[0]["msg_count"] == 0


def test_rename_session(qa_db):
    """正常场景：重命名生效。"""
    sid = S.create_session("旧名")
    assert S.rename_session(sid, "新名") is True
    got = S.get_session(sid)
    assert got is not None
    assert got["title"] == "新名"


def test_rename_missing_session_returns_false(qa_db):
    """异常场景：重命名不存在的会话返回 False。"""
    assert S.rename_session(999999, "x") is False


def test_delete_session_removes_messages(qa_db):
    """正常场景：删除会话时消息一并清除（应用层显式删，不依赖外键）。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    assert S.delete_session(sid) is True
    assert S.get_session(sid) is None
    assert S.get_messages(sid) == []


def test_delete_missing_session_returns_false(qa_db):
    """异常场景：删除不存在的会话返回 False。"""
    assert S.delete_session(999999) is False


def test_qa_messages_cascade_declaration_present(qa_db):
    """回归（级联的**声明侧**）：qa_messages 必须声明 ON DELETE CASCADE。

    为什么需要这条：T4 的 `test_qa_foreign_keys_state_is_documented` 只覆盖
    级联的**前提**（PRAGMA foreign_keys 为 ON）。若有人只删掉 schema 里的
    `REFERENCES qa_sessions(id) ON DELETE CASCADE`（**保留** PRAGMA），
    前提仍在、级联却已静默失效 —— 那边四个用例全绿，消息会残留为孤儿。
    两侧合起来才完整：前提侧管「外键有没有开」，声明侧管「开的是不是级联」。
    """
    with get_db() as conn:
        fks = conn.execute("PRAGMA foreign_key_list(qa_messages)").fetchall()
    cascades = [r for r in fks
                if r["table"] == "qa_sessions" and r["on_delete"] == "CASCADE"]
    assert cascades, (
        "qa_messages 未声明 ON DELETE CASCADE（或未指向 qa_sessions）——"
        "删除会话时消息会残留为孤儿"
    )


def test_search_messages_across_sessions(qa_db):
    """正常场景：跨会话搜消息，返回所属会话名与消息 id。"""
    a = S.create_session("混凝土")
    b = S.create_session("钢筋")
    S.append_message(a, "user", "混凝土强度等级如何评定")
    S.append_message(b, "user", "钢筋保护层厚度")
    hits = S.search_messages("混凝土")
    assert len(hits) == 1
    assert hits[0]["session_id"] == a
    assert hits[0]["session_title"] == "混凝土"


def test_search_messages_escapes_like_wildcards(qa_db):
    """异常场景：%、_、\\ 三个 LIKE 元字符都必须转义，否则退化为全表命中。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "普通内容")
    S.append_message(sid, "user", "含 100% 的内容")
    assert len(S.search_messages("普通内容")) == 1
    # 裸 % 若未转义会命中全部；转义后应命中 0 条
    assert S.search_messages("不存在的%串") == []
    # 反向验证「转义是精确转义」：含元字符的字面量本身仍必须能搜到
    assert [m["content"] for m in S.search_messages("100%")] == ["含 100% 的内容"]

    # _ 若不转义会匹配任意单字符：搜 a_b 将同时命中「aXb 干扰项」
    S.append_message(sid, "user", "a_b 字面下划线")
    S.append_message(sid, "user", "aXb 干扰项")
    assert [m["content"] for m in S.search_messages("a_b")] == ["a_b 字面下划线"]

    # \ 是 ESCAPE 字符本身：不先转义它，`c\d` 会被解析成「转义后的 d」而非字面反斜杠
    S.append_message(sid, "user", r"c\d 字面反斜杠")
    assert [m["content"] for m in S.search_messages(r"c\d")] == [r"c\d 字面反斜杠"]


def test_search_messages_empty_keyword_returns_empty(qa_db):
    """边界场景：空关键词返回空列表，不退化为全量。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "x")
    assert S.search_messages("") == []
    assert S.search_messages("   ") == []
