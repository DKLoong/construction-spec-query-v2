"""Tab1 条文待审面板（partials/review_clause_panel.html）渲染契约回归。

背景：该模板此前仅靠人工 QA，漏掉了与后端 decide 端点的契约漂移——
旧前端发送「勾选」的词 id、不传 dimension、且带 action='reject'（后端已 400）。
本测试锁住 Task 6 收口后的新交互：删除「批量驳回」、删除旧函数名 clauseDecide、
确认走 clauseConfirm(clauseId, dimension) 且必传 dimension、并带「去勾=黑名单」提示。
任何人把旧按钮/旧函数名改回来，套件立即红。
"""
from app.database import get_db
from app.classifier import rule_pending as rp
from app.classifier import batch_queue as bq


def _seed_review_clause(conn) -> int:
    """seed 一条带 pending 词 + queue.status='review' 的条文（Tab1 主表命中条件）"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB1', 'x')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id, clause_no, content) "
                 "VALUES (?, '1.1', '含 钢筋 的条文内容')", (sid,))
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
    bq.try_enqueue(conn, cid, "dim6", 0.0)
    conn.execute("UPDATE classification_queue SET status='review', batch_id='bT', "
                 "ai_label='钢筋' WHERE clause_id=?", (cid,))
    return cid


def test_tab1_panel_uses_new_confirm_contract(auth_client):
    """渲染断言：新按钮组 + 新函数名 + 去勾黑名单提示 + dimension 必传"""
    with get_db() as conn:
        cid = _seed_review_clause(conn)

    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    html = resp.text

    # 旧交互必须彻底消失（删按钮 + 删函数名）
    assert "批量驳回" not in html, "❌ 批量驳回 按钮应已删除"
    assert "clauseDecide(" not in html, "旧函数名 clauseDecide 不得残留"
    assert "action: 'reject'" not in html, "已废弃的 reject action 不得残留"

    # 新交互必须存在
    assert "确认标签" in html, "应有 ✅ 确认标签 按钮"
    assert "clauseConfirm(" in html, "确认按钮应调用 clauseConfirm"
    assert "未勾选的词将进入黑名单" in html, "应有「未勾选的词将进入黑名单」提示文案"
    # dimension 必传：按钮实参须带上该分组的 dimension（旧前端正是漏传此参 → 400）
    assert "clauseConfirm('%d', 'dim6')" % cid in html, \
        "确认按钮应传 clauseId 与该分组 dimension"
