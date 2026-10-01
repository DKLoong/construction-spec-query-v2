"""审核界面性能治理：索引存在性 + 分页契约。

背景：`classification_queue` 此前零索引，相关子查询退化为全表扫描，
Tab1 主查询实测 375 ms（生产库 4 138 pending / 4 625 queue 行）。
"""
from app.database import init_db, get_db
from app.classifier import rule_pending as rp
from app.classifier import batch_queue as bq


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()


def _seed_clause(conn, spec="GB1"):
    conn.execute("INSERT INTO specifications (code, title) VALUES (?, 'x')", (spec,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, '1.1', '钢筋 条文')", (sid,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_review_clause(conn, spec="GB1"):
    """seed 一条进 Tab1 主表的条文（pending 词 + queue review）。"""
    cid = _seed_clause(conn, spec)
    rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
    bq.try_enqueue(conn, cid, "dim6", 0.0)
    conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
    return cid


def test_classification_queue_has_lookup_index(monkeypatch, tmp_path):
    """queue 表必须有 (clause_id, dimension, status) 索引，否则 EXISTS 子查询全表扫。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        names = [r["name"] for r in conn.execute("PRAGMA index_list(classification_queue)")]
    assert "idx_cq_clause_dim_status" in names


def test_rule_pending_has_clause_lookup_index(monkeypatch, tmp_path):
    """rule_pending 必须有 clause_id 打头的索引，否则 NOT EXISTS 子查询全表扫。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        names = [r["name"] for r in conn.execute("PRAGMA index_list(rule_pending)")]
    assert "idx_rp_clause_dim_status" in names


def test_clauses_has_spec_id_index(monkeypatch, tmp_path):
    """clauses 必须有 spec_id 索引：规范列表 LEFT JOIN 目前靠 AUTOMATIC COVERING INDEX
    临时兜底，该临时索引每次查询重建，成本随条文总量线性累加。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        names = [r["name"] for r in conn.execute("PRAGMA index_list(clauses)")]
    assert "idx_clauses_spec_id" in names


# ── 分页契约（limit=None = 全量，向后兼容）────────────────────────

def test_pending_clause_groups_limit_caps_groups(monkeypatch, tmp_path):
    """limit 截断组数，且被截断的组不触发候选词查询。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cids = [_seed_review_clause(conn, f"GB{i}") for i in range(5)]
    all_groups = rp.pending_clause_groups(None)
    assert len(all_groups) == 5
    limited = rp.pending_clause_groups(None, limit=2)
    assert len(limited) == 2
    # 每个保留组仍带完整候选词（截断不得破坏组内数据）
    assert all(g["candidates"] for g in limited)
    assert {g["clause_id"] for g in limited} <= set(cids)


def test_pending_clause_groups_limit_none_returns_all(monkeypatch, tmp_path):
    """limit=None 保持全量语义（既有测试依赖）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        for i in range(3):
            _seed_review_clause(conn, f"GB{i}")
    assert len(rp.pending_clause_groups(None, limit=None)) == 3
    assert len(rp.pending_clause_groups(None)) == 3


def test_clause_group_total_counts_both_segments(monkeypatch, tmp_path):
    """total = 主表段 + 全驳段，且不受 limit 影响。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_review_clause(conn, "GB1")
        c2 = _seed_review_clause(conn, "GB2")
        # 把 c2 的 pending 词全驳 → 落进「全驳段」
        rp.set_status(conn, rp.clause_pending_ids(conn, c2, "dim6"), "rejected")
    assert rp.clause_group_total(None) == 2
    # 全驳段自身也受 limit 约束
    assert len(rp.rejected_clause_groups(None, limit=1)) == 1


def test_word_group_total_and_limit(monkeypatch, tmp_path):
    """Tab2：total 是词面组数；limit 截断组数。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "混凝土", "混凝土", 0.8, "b2")
        rp.insert_pending(conn, c2, "dim6", "模板", "模板", 0.7, "b2")
    assert rp.word_group_total(None) == 3
    assert len(rp.pending_groups(None, limit=1)) == 1
    assert len(rp.pending_groups(None, limit=2)) == 2
    assert len(rp.pending_groups(None)) == 3


# ── 路由接 limit 并下发 total（HTTP 契约）────────────────────────

def test_clause_pending_respects_limit(auth_client):
    """端点接 limit：只约束渲染条数。

    ⚠ 此处**不**断言 total 文案——「已显示 X / 共 N 条」由 Task 5 的模板改动产生，
    本 Task 时尚不存在。total 的**数值**正确性由 Task 2 的
    `test_clause_group_total_counts_both_segments` 覆盖，**渲染**由 Task 5 覆盖。
    """
    with get_db() as conn:
        for i in range(5):
            _seed_review_clause(conn, f"GB{i}")
    resp = auth_client.get("/review/clause-pending?limit=2")
    assert resp.status_code == 200
    assert resp.text.count("clause-row-") == 2, "limit=2 应只渲染 2 个条文卡片"


def test_clause_pending_rejects_out_of_range_limit(auth_client):
    """limit 是外部输入：越界必须被挡下（项目规则 1.1）。"""
    assert auth_client.get("/review/clause-pending?limit=0").status_code == 422
    assert auth_client.get("/review/clause-pending?limit=501").status_code == 422
    assert auth_client.get("/review/clause-pending?limit=abc").status_code == 422


def test_clause_pending_default_limit_is_50(auth_client):
    """不传 limit 时默认 50（首屏不再全量渲染）。"""
    with get_db() as conn:
        for i in range(55):
            _seed_review_clause(conn, f"GB{i}")
    html = auth_client.get("/review/clause-pending").text
    assert html.count("clause-row-") == 50


def test_word_pending_respects_limit(auth_client):
    """Tab2 同样受 limit 约束（total 文案断言见 Task 5，理由同上）。"""
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        for word in ["钢筋", "混凝土", "模板", "砂浆", "涂料"]:
            rp.insert_pending(conn, c1, "dim6", word, word, 0.9, "b1")
    resp = auth_client.get("/review/word-pending?limit=2")
    assert resp.status_code == 200
    assert resp.text.count('<tr id="word-group-') == 2


# ── 刷新总控：懒加载 + 可见性门控（Task 4）────────────────────────

def test_review_tabs_has_resident_refresh_controller(auth_client):
    """三面板刷新总控必须常驻在 review_tabs.html（面板内联 script 每次 swap 会重定义）。"""
    html = auth_client.get("/review").text
    for fn in ("reviewLoad", "reviewEnsure", "reviewRefresh", "reviewSwitchTab", "reviewLoadMore"):
        assert ("window.%s = function" % fn) in html, "缺少总控函数 %s" % fn
    # 懒加载：面板容器不得再有 hx-trigger="load"（首屏只拉可见的 Tab1）
    assert 'hx-trigger="load, reviewClausePending' not in html
    assert 'hx-trigger="load, reviewWordPending' not in html
    assert 'hx-trigger="load, reviewBlacklist' not in html


def test_panels_call_refresh_controller_not_raw_events(auth_client):
    """各操作回调必须走 reviewRefresh，不得再裸 dispatch 三个事件。"""
    with get_db() as conn:
        _seed_review_clause(conn)
    clause_html = auth_client.get("/review/clause-pending").text
    assert "reviewRefresh(" in clause_html
    assert "refreshReviewPanels" not in clause_html, "旧聚合函数应已删除"

    word_html = auth_client.get("/review/word-pending").text
    assert "reviewRefresh(" in word_html
    assert "dispatchEvent(new CustomEvent('reviewWordPending')" not in word_html


def test_list_fallback_uses_refresh_controller(auth_client):
    """低置信兜底块的操作回调同样走总控。

    ⚠ 必须 seed：兜底块的 <script> 在 `{% if items %}` 内，空库时整块不渲染，
    不 seed 的断言会在「无低置信待审项」占位文案上假失败（也会掩盖真实回归）。
    """
    with get_db() as conn:
        cid = _seed_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
    html = auth_client.get("/review/list").text
    assert "fallback-row-" in html, "前置：兜底块应已渲染（否则下面的断言无意义）"
    assert "reviewRefresh(" in html
    assert "dispatchEvent" not in html


# ── 分页 UI：加载更多 + 已显示/共 N 条（Task 5）────────────────────

def test_clause_panel_renders_load_more_when_truncated(auth_client):
    """有更多时渲染「加载更多」，并把下一页 limit 传给总控。"""
    with get_db() as conn:
        for i in range(55):
            _seed_review_clause(conn, f"GB{i}")
    html = auth_client.get("/review/clause-pending?limit=50").text
    assert "加载更多" in html
    assert "reviewLoadMore('clause', 100)" in html
    # 「主表」是必需的限定词：total 只含主表两段（pending+全驳），不含低置信兜底，
    # 故会小于宫格红点（红点含兜底）。不加限定词用户会以为两处数字打架。
    assert "主表已显示 50 / 共 55 条" in html


def test_clause_panel_hides_load_more_when_complete(auth_client):
    """已全部显示时不渲染「加载更多」。"""
    with get_db() as conn:
        _seed_review_clause(conn)
    html = auth_client.get("/review/clause-pending?limit=50").text
    assert "加载更多" not in html
    assert "主表已显示 1 / 共 1 条" in html


def test_word_panel_renders_load_more(auth_client):
    """Tab2 同样有分页控件。"""
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        for word in ["钢筋", "混凝土", "模板"]:
            rp.insert_pending(conn, c1, "dim6", word, word, 0.9, "b1")
    html = auth_client.get("/review/word-pending?limit=2").text
    assert "加载更多" in html
    assert "reviewLoadMore('word', 52)" in html


# ── 单次加载上限：步进钳制 + 到顶提示（Task 5 修复轮 1）─────────────
#
# 缺陷：按钮写死 `limit + 50`，端点上限却是 500 → limit=500 时再点会发
# `?limit=550` 触发 422，HTMX 对 4xx 不 swap，用户侧就是「点了没反应」。
# 修法：上限定义为 `rules_routes.REVIEW_PAGE_MAX` 单一来源，端点 le 与模板
# 下发的 max_limit 都取自它，模板用 min(limit+50, max_limit) 钳制步进。
# 三个字面量 500 同时出现在端点校验（既有测试已钉 501→422）与模板渲染断言里，
# 谁单方面改动都会让另一边红，即「同源」这一契约由测试锁定。

def test_clause_panel_shows_cap_hint_instead_of_button_at_max_limit(auth_client):
    """limit 到顶且仍未显示完时：不渲染「加载更多」，改提示已达上限（不得静默隐藏）。"""
    with get_db() as conn:
        for i in range(501):
            _seed_review_clause(conn, f"GB{i}")
    html = auth_client.get("/review/clause-pending?limit=500").text
    assert "主表已显示 500 / 共 501 条" in html
    assert "加载更多" not in html, "到顶后仍渲染按钮，再点即发 ?limit=550 → 422 死路"
    assert "已达单次加载上限" in html and "500 条" in html
    # 提示语给出的那个档位必须真能请求（否则提示本身就是死路提示）
    assert auth_client.get("/review/clause-pending?limit=500").status_code == 200
    assert auth_client.get("/review/clause-pending?limit=501").status_code == 422


def test_clause_panel_clamps_next_limit_to_max_limit(auth_client):
    """中间档：下一步被钳到上限 500，而不是 limit+50=540。

    ⚠ 探针值必须选 limit>450：取 450 时 limit+50 恰好等于 500，改前的
    「写死 limit+50」也会渲染出 500，本测试会**为错误的原因通过**。
    """
    with get_db() as conn:
        for i in range(501):
            _seed_review_clause(conn, f"GB{i}")
    html = auth_client.get("/review/clause-pending?limit=490").text
    assert "reviewLoadMore('clause', 500)" in html
    assert "reviewLoadMore('clause', 540)" not in html, "步进未被上限钳制"


def test_word_panel_shows_cap_hint_instead_of_button_at_max_limit(auth_client):
    """Tab2 同一规则；且 Tab2 无低置信兜底块，提示不得带「主表」限定词。"""
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        # 词面组按 (dimension, pattern) 聚合，故同一条文塞 501 个不同 pattern 即 501 组
        for i in range(501):
            rp.insert_pending(conn, c1, "dim6", f"词{i}", f"词{i}", 0.9, "b1")
    html = auth_client.get("/review/word-pending?limit=500").text
    assert "已显示 500 / 共 501 条" in html
    assert "加载更多" not in html
    assert "已达单次加载上限" in html and "500 条" in html
    assert "主表" not in html


def test_word_panel_clamps_next_limit_to_max_limit(auth_client):
    """Tab2 中间档同样被钳到 500（探针值同上取 limit>450 的理由）。"""
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        for i in range(501):
            rp.insert_pending(conn, c1, "dim6", f"词{i}", f"词{i}", 0.9, "b1")
    html = auth_client.get("/review/word-pending?limit=490").text
    assert "reviewLoadMore('word', 500)" in html
    assert "reviewLoadMore('word', 540)" not in html, "步进未被上限钳制"


# ── 宫格红点刷新去重（Task 6）────────────────────────────────────

def test_badge_refresh_is_coalesced(auth_client):
    """宫格红点：同一轮触发的多个事件必须合并为一次 refresh，不得各刷一遍。"""
    html = auth_client.get("/review").text
    assert "scheduleBadgeRefresh" in html, "红点应经去重调度器刷新"
    assert "setTimeout" in html, "去重应基于宏任务合并（同一轮的多个事件同步触发）"
