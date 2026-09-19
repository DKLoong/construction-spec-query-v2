"""Tab1 条文待审面板（partials/review_clause_panel.html）渲染契约回归。

背景：该模板此前仅靠人工 QA，漏掉了与后端 decide 端点的契约漂移——
旧前端发送「勾选」的词 id、不传 dimension、且带 action='reject'（后端已 400）。
本测试锁住 Task 6 收口后的新交互：删除「批量驳回」、删除旧函数名 clauseDecide、
确认走 clauseConfirm(clauseId, dimension) 且必传 dimension、并带「去勾=黑名单」提示。
任何人把旧按钮/旧函数名改回来，套件立即红。
"""
import re

from app.database import get_db
from app.classifier import rule_pending as rp
from app.classifier import batch_queue as bq


def _extract_js_func(html: str, name: str) -> str:
    """从模板内联 script 抠出 `function <name>(...) {...}` 源码（按花括号配平）。

    模板是纯字符串资源，pytest 无 JS 引擎，只能对函数源码做结构断言；
    故用「守卫语句必须出现在副作用调用之前」这类位置断言替代执行断言。
    """
    start = html.index("function " + name + "(")
    i = html.index("{", start)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError("函数 %s 花括号未配平" % name)


def _seed_review_clause(conn) -> int:
    """seed 一条带 pending 词 + queue.status='review' 的条文（Tab1 主表命中条件）"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB1', 'x')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id, clause_no, content) "
                 "VALUES (?, '1.1', '含 钢筋 的条文内容')", (sid,))
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
    # 另加一个已驳回词：主表渲染「词已驳回」标记（D1 路径），且不影响 pending 候选
    rid = rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.7, "bT")
    rp.set_status(conn, [rid], "rejected")
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
    # 旧调用形态 clauseDecide(id, dim, 'reject') 渲染出的字面量（真实旧样式；
    # 不要写成 "action: 'reject'"——旧模板从无该字面量，那种断言永远不会变红）
    assert "', 'reject')" not in html, "旧 reject 调用形态不得残留"

    # 新交互必须存在
    assert "确认标签" in html, "应有 ✅ 确认标签 按钮"
    assert "clauseConfirm(" in html, "确认按钮应调用 clauseConfirm"
    assert "未勾选的词将进入黑名单" in html, "应有「未勾选的词将进入黑名单」提示文案"
    # dimension 必传：按钮实参须带上该分组的 dimension（旧前端正是漏传此参 → 400）
    assert "clauseConfirm('%d', 'dim6')" % cid in html, \
        "确认按钮应传 clauseId 与该分组 dimension"

    # 候选词 checkbox 默认全勾（C3：勾选=留 pending 进 Tab2；去勾才是黑名单）
    checks = re.findall(r'<input[^>]*class="cand-check"[^>]*>', html)
    assert checks, "该组应渲染出候选词 checkbox"
    assert all("checked" in c for c in checks), "候选词 checkbox 应默认全勾"


def test_tab1_confirm_guards_group_without_candidates(auth_client):
    """D1 行（候选已全驳、candidates 恒空）点「确认标签」须被前端守卫拦下。

    该行无任何 checkbox，去掉旧 `!checked.length` 守卫后点按钮会 POST ids:[] →
    后端按「纯确认」写 ai_label + queue done，抹掉人工「请为条文输入新标签」待办，
    而确认框还谎称「未勾选的词将进入黑名单」。守卫须早于 confirm()/fetch()。
    """
    with get_db() as conn:
        cid = _seed_review_clause(conn)
        # 把该条剩余 pending 词一并驳回 → 该 (clause, dim) 无 pending，D1 组（候选区空）
        rp.set_status(conn, rp.clause_pending_ids(conn, cid, "dim6"), "rejected")

    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    html = resp.text
    assert "词已驳回" in html, "全驳条文应留在主表 D1 行"
    # 该行确无 checkbox（正是守卫要拦的场景）
    assert '<input type="checkbox" class="cand-check"' not in html

    fn = _extract_js_func(html, "clauseConfirm")
    assert "该条无候选词，请用编辑输入新标签" in fn, "应有无候选词守卫提示"
    # 守卫判空用的是「候选区无 .cand-check」（不带 :not(:checked)，
    # 否则「有候选词但全勾」的合法纯确认路径会被误伤）
    guard_at = fn.index("该条无候选词")
    guard = fn[:guard_at]
    assert ".cand-check'" in guard, "守卫应按「候选区无 checkbox」判空"
    # 分组作用域：选择器必须锚定本组候选区（'#candidates-' + clauseId + '-' + dimension）。
    # 若退化成全局 '.cand-check'，多分组页面里别组的 checkbox 会让 D1 行守卫误判为「有候选」
    # → 守卫静默失效（本组恰恰没有 checkbox）。去空白后比对，免受换行/缩进写法差异影响。
    assert "'#candidates-'+clauseId+'-'+dimension" in re.sub(r"\s+", "", guard), \
        "守卫选择器必须锚定本分组候选区，不得退化为全局 .cand-check"
    assert "alert(" in guard, "守卫应 alert 提示"
    assert ":not(:checked)" not in guard, "判空不得复用去勾选择器（会误伤全勾纯确认）"
    # 守卫必须早于 confirm()/fetch()，否则空 ids 已经发出去了
    assert guard_at < fn.index("fetch("), "守卫必须在 fetch 之前"
    assert guard_at < fn.index("confirm('确认该标签"), "守卫必须在 confirm 之前"


def test_tab1_edit_panel_copy_matches_new_semantics(auth_client):
    """inline 编辑面板文案：× = 进黑名单；保留 ≠ 批准成规则（Task 4 后保留词只留 pending）"""
    with get_db() as conn:
        _seed_review_clause(conn)

    html = auth_client.get("/review/clause-pending").text

    assert "保留=批准" not in html, "旧文案「保留=批准」已不成立（保留词只留 pending，不建规则）"
    assert "点 × 进黑名单" in html, "× 按钮应说明为进黑名单"
    assert "不代表已成规则" in html, "应说明保留 ≠ 批准成规则"
    # 仍准确的文案不得被误改：新标签只写分类列、词已驳回标记
    assert "只写分类列，不沉淀规则" in html, "新标签输入框说明本就准确，应保留"
    assert "词已驳回" in html, "全标签已驳条文的标记文案应保留"
