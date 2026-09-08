# Tab1/Tab2 打标-沉淀解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「条文打标」与「词面沉淀」解耦——Tab1 主表批准只写列、不再连带为词建规则；词面普通沉淀只经 Tab2；堵住反联/低置信覆写人工定案的洞，并用条件更新落地并发守卫。

**Architecture:** 改动集中在 rule_pending 查询/写回与 rules_routes 的 Tab1/Tab2 动作语义。保留低置信兜底块与旧端点，不动表结构、不动 apply_ai_results auto 分支。关键机制：`pending_clause_groups` 排除终态+无 queue 行条文；`_backfill_by_status`/`_confirm_clause` 用带 `EXISTS(queue review)` 的条件更新 + rowcount 守卫（C14）；Tab1 `decide approve` = body 传 dimension + 只写列 + 去勾词 rejected（C13）；inline 同口径收口；`process_feedback` 去 patterns 背书变纯打标并加 review 守卫（C15）；低置信 confirm/reject 查询加 `status='review'`。

**Tech Stack:** FastAPI + SQLite + pytest + HTMX + Jinja2 partials。

**Spec:** `docs/superpowers/specs/2026-09-06-tab1-tab2-convergence-design.md`（v3 收窄 + 评审修订 C11-C18）。

## Global Constraints

- 数据库读写一律参数化，禁止字符串拼接 SQL（拼 `IN (...)` 占位符除外，须 `','.join('?'*len(xs))`）。
- Python 命令用 `D:/Python/python.exe`（禁用 `python3`）；测试用 `D:/Python/python.exe -m pytest`。
- Git Bash 语法；路径用 `/` 正斜杠。
- 中文注释与交流。
- **TDD + 每 Task 绿提交**：语义改动 Task 必须**在 Task 内**同步适配被它破坏的旧断言，跑通 `tests/test_rule_pending.py -q` 及相关文件再 commit；**禁止**留下中间红提交等后续 Task 收尾（评审修订 C-TDD）。Task7 只做全量回归扫尾与文档。
- Commit 信息 `type: 描述`（feat/fix/test/docs/refactor/chore）。
- 不得 stage `.claude/CLAUDE.md`（用户遗留未提交）与 `data/`。
- 每次改动后主动 commit；不主动安装依赖、不起后台服务。
- 服务重启（若需浏览器验证）须按项目 CLAUDE.md：全杀 reloader+worker 双进程 → 验证端口干净 → 启动 → 复验唯一监听。
- **前置条件（spec C11/C18）**：目标 dev 库须已清到 locked 种子基准（dim4/5/6 active=21/23/23，无 confirmed 碎片）。本 plan 不改存量碎片；若目标库未清，先跑既有清理脚本再实施。
- 测试跑批：语义 Task 用 `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`；Task 5 用 `tests/test_rule_feedback_loop.py tests/test_review_batch.py tests/test_logs.py`；最终全量 `tests/ -q`。

---
## 现状速览（Task 实现者必读）

关键现状函数与语义（已核实，行号供参考）：
- `rule_pending.pending_clause_groups`（rule_pending.py:214）：`WHERE rp.status='pending' GROUP BY dimension, clause_id` 聚合有 pending 词的条文，join clauses/specifications。**未排除终态/无 queue 行**。
- `rule_pending.rejected_clause_groups`（rule_pending.py:249）：全标签已驳且 queue 仍 review 的条文（D1，词全驳待重标）。**仅 inline 删光词不输新标签可达**（新语义下 approve 会写列 done 使行消失）。
- `rule_pending._backfill_by_status`（rule_pending.py:317）：按 (dim,pattern,label,status) 取来源条文 → 无条件写列 + queue(review)→done。**无 review 守卫**。
- `rule_pending.backfill_and_close`（:339）= `_backfill_by_status(..., "pending")`；`approve_rule`（:364）bump confirmed 规则（例外路径，保留）；`deactivate_fragment`（:349）停用 confirmed=0 碎片。
- `rule_pending.pending_counts`（:292）：红点计数。clause 段把 pending 行按条文计——**须随 C5 改口径（C16）**。
- `rules_routes.review_clause_decide`（rules_routes.py:559）：Tab1 行级反义批量，approve 展开词 approve_rule。
- `rules_routes.review_clause_inline_edit`（rules_routes.py:612）：removed→rejected；keep→approve；new_label→只写列。**保留 id/clause 反查维度兜底——须收口（C13）**。
- `rules_routes.confirm_review`（:311）/`reject_review`（:343）：queue 查询 `WHERE id=?` **未带 status='review'**——须加守卫（C15）。
- `feedback.process_feedback`（feedback.py:29）：写列+done + patterns 词背书+bump——去背书变纯打标 + review 守卫。
- `batch_queue.apply_ai_results`（batch_queue.py:80）：已背书词 auto_adopted 写列（例外，保留 C10）；首见词 insert_pending+queue review。**本 plan 不动此函数**。
- 测试夹具：`tests/test_rule_pending.py` 的 `_db`、`_seed_spec_clause(conn)`、`_seed_rule`、`auth_client`（conftest）。**多数 Tab1 测试直插 pending + try_enqueue 建 queue review**；直插 pending 无 queue 的 seed 需按 C17 补 queue。

---

### Task 1: pending_clause_groups 排除终态+无 queue 行 + pending_counts 口径同步（C5/C16/C17）

**Files:**
- Modify: `app/classifier/rule_pending.py` — `pending_clause_groups`、`pending_counts`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: 无（独立查询改造）
- Produces: `pending_clause_groups(dimension=None) -> list[dict]`：**仅返回存在 queue review 行**的条文（终态 done/auto_adopted/rejected 与无 queue 行均排除）。`pending_counts()` clause 口径 = `len(pending_clause_groups()) + len(rejected_clause_groups()) + 低置信兜底 count`。

- [ ] **Step 1: 写失败测试**

```python
def test_pending_clause_groups_excludes_terminal_and_no_queue(auth_client):
    """queue 终态(auto_adopted) 或无 queue 行的 pending 条文都不进 Tab1 主表。"""
    with get_db() as conn:
        # 终态：auto_adopted 但有残留 pending 词
        c_term = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_term, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_term, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='auto_adopted', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_term,))
        # 无 queue：直插 pending
        c_noq = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_noq, "dim6", "钢筋", "钢筋", 0.9, "bT")
        # review：应保留
        c_rev = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_rev, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_rev, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (c_rev,))
    groups = rp.pending_clause_groups()
    cids = {g["clause_id"] for g in groups}
    assert c_rev in cids
    assert c_term not in cids and c_noq not in cids


def test_pending_counts_aligned_with_tab1_source(auth_client):
    """pending_counts.clause = 主表组 + 词全驳组 + 低置信兜底（已确认 done 的残留 pending 词不计入）。"""
    with get_db() as conn:
        c_rev = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_rev, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_rev, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_rev,))
        # 已确认 done 的条文，其勾选词仍 pending → 不计 clause 待审
        c_done = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_done, "dim6", "混凝土", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_done, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='done', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_done,))
        # 低置信兜底：review 无词
        c_low = _seed_spec_clause(conn)
        bq.try_enqueue(conn, c_low, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_low,))
    counts = rp.pending_counts()
    assert counts["clause"] == 2   # c_rev + 低置信 c_low；c_done 不计
    assert counts["word"] == 2     # 钢筋 + 混凝土 两个词面组
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "excludes_terminal_and_no_queue or counts_aligned_with_tab1" -v`
Expected: FAIL（现终态/无 queue 行仍返回；counts 含 c_done）。

- [ ] **Step 3: 实现**

`pending_clause_groups` 的 sql 改为 join 过滤「存在 review queue 行」：

```python
def pending_clause_groups(dimension: str | None = None) -> list[dict]:
    """Tab1 条文多标签视图：status='pending' 按 (dimension, clause_id) 聚合。

    仅返回存在 queue review 行的条文（C5/C17）：queue 终态(done/auto_adopted/rejected)
    或完全无 queue 行的 pending 条文都是死行/已定案，不进「条文待审」。真实主链
    insert_pending 必伴随 queue review。
    """
    from app.database import get_db
    sql = ("SELECT rp.dimension, rp.clause_id, s.code AS spec_code, c.clause_no, c.content "
           "FROM rule_pending rp "
           "JOIN clauses c ON c.id = rp.clause_id "
           "JOIN specifications s ON s.id = c.spec_id "
           "WHERE rp.status='pending' "
           "AND EXISTS ("
           "  SELECT 1 FROM classification_queue q "
           "  WHERE q.clause_id = rp.clause_id AND q.dimension = rp.dimension "
           "  AND q.status = 'review')")
    args = []
    if dimension:
        sql += " AND rp.dimension=?"
        args.append(dimension)
    sql += " GROUP BY rp.dimension, rp.clause_id ORDER BY rp.dimension, c.clause_no"
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        groups = []
        for r in rows:
            cands = conn.execute(
                "SELECT id, pattern, label, ai_confidence FROM rule_pending "
                "WHERE clause_id=? AND dimension=? AND status='pending' ORDER BY id",
                (r["clause_id"], r["dimension"])).fetchall()
            groups.append({
                "dimension": r["dimension"], "clause_id": r["clause_id"],
                "spec_code": r["spec_code"], "clause_no": r["clause_no"],
                "content": r["content"],
                "candidates": [dict(x) for x in cands],
            })
    return groups
```

`pending_counts` 的 clause 段改为与主表同口径：

```python
def pending_counts() -> dict:
    """宫格「审核」红点计数（C16）：clause 与 Tab1 主表同口径。

    clause = pending_clause_groups() 组数 + rejected_clause_groups() 组数（全驳待重标）
             + 低置信 queue review 且无 pending/rejected 关联的兜底条数；
    word   = pending_groups() 词面组数。已确认(done)条文的残留 pending 词不计 clause。
    """
    from app.database import get_db
    n_clause = len(pending_clause_groups())
    n_clause += len(rejected_clause_groups())
    with get_db() as conn:
        n_clause += conn.execute(
            "SELECT COUNT(*) FROM classification_queue q WHERE q.status='review' "
            "AND NOT EXISTS (SELECT 1 FROM rule_pending rp "
            "  WHERE rp.clause_id=q.clause_id AND rp.dimension=q.dimension "
            "  AND rp.status IN ('pending','rejected'))").fetchone()[0]
        n_word = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM rule_pending WHERE status='pending' "
            "GROUP BY dimension, pattern)").fetchone()[0]
    return {"clause": n_clause, "word": n_word}
```

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "excludes_terminal_and_no_queue or counts_aligned_with_tab1" -v`
Expected: 2 PASS。

- [ ] **Step 5: 相关测试绿 + 提交（本 Task 内适配受影响 seed/断言，不留红）**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py tests/test_review_batch.py tests/test_logs.py -q`
说明：既有测试若直插 pending 无 queue 且断言在主表，需在 seed 里补 `try_enqueue`+置 review，或改断言（C17）。凡此类 seed 本 Task 内一并修。
Commit: `git add app/classifier/rule_pending.py tests/test_rule_pending.py tests/test_review_batch.py tests/test_logs.py && git commit -m "feat: pending_clause_groups 排除终态/无 queue 行 + pending_counts 同口径(C5/C16/C17)"`

---

### Task 2: _backfill_by_status 条件更新 + review 守卫（C8/C14）

**Files:**
- Modify: `app/classifier/rule_pending.py` — `_backfill_by_status`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: 无
- Produces: `_backfill_by_status(conn, dimension, pattern, label, status) -> int`：clause 列 UPDATE 带 `AND EXISTS(queue review)` + 检查 rowcount；命中才置 queue done；未命中（已并发 done/改标）跳过。返回实际写列条文数。

- [ ] **Step 1: 写失败测试**

```python
def test_backfill_skips_terminal_and_writes_review(auth_client):
    """反联守卫：queue 已 done 来源条文不覆写（列保持原值）；queue 仍 review 的写列。"""
    with get_db() as conn:
        c_done = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_done, "dim6", "钢筋", "钢筋", 0.9, "bC")
        bq.try_enqueue(conn, c_done, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='done', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_done,))
        conn.execute("UPDATE clauses SET dim6_material='混凝土' WHERE id=?", (c_done,))
        c_rev = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_rev, "dim6", "钢筋", "钢筋", 0.9, "bC")
        bq.try_enqueue(conn, c_rev, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_rev,))
    n = 0
    with get_db() as conn:
        n = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
    with get_db() as conn:
        vals = {r["id"]: r["dim6_material"] for r in conn.execute(
            "SELECT id, dim6_material FROM clauses").fetchall()}
        statuses = {r["clause_id"]: r["status"] for r in conn.execute(
            "SELECT clause_id, status FROM classification_queue").fetchall()}
    assert n == 1                          # 只写 c_rev
    assert vals[c_done] == "混凝土"         # 不被反联覆写
    assert vals[c_rev] == "钢筋"
    assert statuses[c_rev] == "done"
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k backfill_skips_terminal_and_writes_review -v`
Expected: FAIL（现无条件写列 → n==2、c_done 被改成 钢筋）。

- [ ] **Step 3: 实现**

```python
def _backfill_by_status(conn, dimension: str, pattern: str, label: str,
                        status: str) -> int:
    """该键在指定 status 下的来源条文写列 + queue(review)→done。返回实际写列条文数。

    C8/C14 反联守卫：clause 列 UPDATE 带 EXISTS(queue review) 并检查 rowcount——
    已 done/auto_adopted/rejected（已定案/改标）或并发已被处理的条文跳过，防覆写。
    """
    col = _dim_column(dimension)
    rows = conn.execute(
        "SELECT DISTINCT rp.clause_id FROM rule_pending rp "
        "JOIN classification_queue q ON q.clause_id = rp.clause_id AND q.dimension = rp.dimension "
        "WHERE rp.dimension=? AND rp.pattern=? AND rp.label=? AND rp.status=? "
        "AND rp.clause_id IS NOT NULL AND q.status='review'",
        (dimension, pattern, label, status)).fetchall()
    cids = [r["clause_id"] for r in rows]
    written = 0
    for cid in cids:
        cur = conn.execute(
            f"UPDATE clauses SET {col}=?, ai_classified=1 WHERE id=? "
            f"AND EXISTS (SELECT 1 FROM classification_queue q "
            f"WHERE q.clause_id=? AND q.dimension=? AND q.status='review')",
            (label, cid, cid, dimension))
        if cur.rowcount:
            written += 1
            conn.execute(
                "UPDATE classification_queue SET status='done' "
                "WHERE clause_id=? AND dimension=? AND status='review'",
                (cid, dimension))
    return written
```

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k backfill_skips_terminal_and_writes_review -v`
Expected: PASS。

- [ ] **Step 5: 回归既有反联测试 + 提交**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`
说明：既有词面批准反联测试 seed 把 queue 置 review → 仍写列应通过。若个别 seed 未建 queue review 而断言写列，Task 内补 queue（C17 同法）。
Commit: `git add app/classifier/rule_pending.py tests/test_rule_pending.py && git commit -m "feat: 词面反联条件更新+review 守卫(C8/C14)"`

---

### Task 3: Tab1 decide approve 只写列 + 去勾词 rejected（C1/C3/C4/C13）+ _confirm_clause helper（C14）

**Files:**
- Modify: `app/classifier/rule_pending.py` — 新增 `_confirm_clause`
- Modify: `app/routes/rules_routes.py` — `review_clause_decide`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: `clause_pending_ids`、`resolve_keys`、`set_status`、`deactivate_fragment`、`_confirm_clause`、`_DIM_COLUMN`
- Produces: `_confirm_clause(conn, clause_id, dimension, label) -> bool`（条件更新+rowcount，C14）；`POST /review/clause-pending/{clause_id}/decide` body `{dimension: str 必填, ids:[...], action:"approve"}`。ids=去勾词（可空=纯确认）；approve=写 ai_label 列 + queue done + ids 词 rejected + 其余 pending 不动。action='reject'→400。无 review queue→400；ai_label 空→400（C13）。

- [ ] **Step 1: 写失败测试**

```python
def test_tab1_approve_writes_col_keeps_checked_rejects_unchecked(auth_client):
    """确认=只写列+queue done；勾选词留 pending；去勾词 rejected；不建规则。dimension 必填。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.7, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='bT', "
                     "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        reject = [r["id"] for r in conn.execute(
            "SELECT id FROM rule_pending WHERE clause_id=? AND pattern='混凝土'", (cid,)).fetchall()]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": reject, "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["pattern"]: r["status"] for r in conn.execute(
            "SELECT pattern, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "钢筋" and q["status"] == "done"
    assert st["钢筋"] == "pending" and st["混凝土"] == "rejected"
    assert n_rules == 0


def test_tab1_approve_empty_ids_pure_confirm(auth_client):
    """ids 空=纯确认：只写列+done，无词驳回、无规则。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='bT', "
                     "ai_label='钢筋' WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c == "钢筋" and q == "done" and st == "pending" and n_rules == 0


def test_tab1_decide_reject_action_now_400(auth_client):
    """reject action 已删 → 400。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [pid], "action": "reject"})
    assert resp.status_code == 400


def test_tab1_approve_no_review_queue_400(auth_client):
    """无 review queue 项 → 400，不写列不改列。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [], "action": "approve"})
    assert resp.status_code == 400
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
    assert (c or "") == ""


def test_tab1_approve_empty_ai_label_400(auth_client):
    """review 存在但 ai_label 为空 → 400（避免静默 no-op 页面卡死）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label=NULL "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [], "action": "approve"})
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "writes_col_keeps_checked or empty_ids_pure_confirm or reject_action_now_400 or no_review_queue_400 or empty_ai_label_400" -v`
Expected: FAIL（现 approve=词 approved+bump、无 dimension 校验、reject 成功、ai_label 空无守卫）。

- [ ] **Step 3: 实现**

**3a. `rule_pending.py` 新增 `_confirm_clause`（Task 3/4 共用；C14 条件更新）：**

```python
def _confirm_clause(conn, clause_id: int, dimension: str, label: str) -> bool:
    """写该条分类列 + queue review→done（Tab1 确认/inline 共用打标出口）。

    条件更新（C14）：clause 列 UPDATE 带 EXISTS(queue review)，命中才置 queue done，
    未命中（已 done/改标/并发）返回 False no-op。返回是否实际写列。
    """
    col = _dim_column(dimension)
    cur = conn.execute(
        f"UPDATE clauses SET {col}=?, ai_classified=1, needs_review=0 WHERE id=? "
        f"AND EXISTS (SELECT 1 FROM classification_queue q "
        f"WHERE q.clause_id=? AND q.dimension=? AND q.status='review')",
        (label, clause_id, clause_id, dimension))
    if not cur.rowcount:
        return False
    conn.execute(
        "UPDATE classification_queue SET status='done' "
        "WHERE clause_id=? AND dimension=? AND status='review'",
        (clause_id, dimension))
    return True
```

**3b. 替换 `review_clause_decide`：**

```python
@router.post("/review/clause-pending/{clause_id}/decide")
async def review_clause_decide(request: Request, clause_id: int, body: dict):
    """Tab1 主表确认标签（C1/C3/C4/C13）：只写该条 queue ai_label 列 + queue done。

    body: {dimension: str 必填, ids:[去勾词面 id], action:"approve"}。ids 可为空=纯确认。
    approve → 写 ai_label 列 + queue done（_confirm_clause）；ids 词 rejected（停用碎片）；
    其余 pending 词保持 pending。action="reject" → 400。
    """
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("ids") or []
    action = body.get("action")
    dimension = body.get("dimension")
    if action != "approve" or not isinstance(ids, list):
        return _JR({"detail": "仅支持 approve（批量驳回已移除）"}, status_code=400)
    if dimension not in _DIM_COLUMN:
        return _JR({"detail": "dimension 不合法"}, status_code=400)

    with get_db() as conn:
        scope = rule_pending.clause_pending_ids(conn, clause_id, dimension)
        if ids and not set(ids) <= set(scope):
            return _JR({"detail": "勾选 id 不属于该条文待审作用域"}, status_code=400)
        q = conn.execute(
            "SELECT ai_label FROM classification_queue "
            "WHERE clause_id=? AND dimension=? AND status='review'",
            (clause_id, dimension)).fetchone()
        if not q:
            return _JR({"detail": "该条文该维无 review 队列项"}, status_code=400)
        label = q["ai_label"]
        if not label:
            return _JR({"detail": "该队列项无 AI 标签，请用编辑输入"}, status_code=400)
        rule_pending._confirm_clause(conn, clause_id, dimension, label)
        if ids:
            reject_keys = rule_pending.resolve_keys(conn, ids)
            rule_pending.set_status(conn, ids, "rejected")
            for d, p, l in sorted(reject_keys):
                rule_pending.deactivate_fragment(conn, d, p, l)

    log_action("review", "INFO", "条文批准(打标解耦)",
               detail=json_detail({"clause_id": clause_id, "dimension": dimension,
                                   "label": label, "rejected_word_ids": ids}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewClausePending, reviewWordPending, reviewBlacklist"})
```

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "writes_col_keeps_checked or empty_ids_pure_confirm or reject_action_now_400 or no_review_queue_400 or empty_ai_label_400" -v`
Expected: 5 PASS。

- [ ] **Step 5: 相关测试绿 + 提交（本 Task 内适配被打破的旧 Tab1 断言，不留红）**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`
本 Task 内同步改：`test_tab1_clause_multi_label_approve_partial`（→勾选 pending/未勾 rejected/无规则/补 dimension）、`test_word_approve_then_clause_queue_already_done_idempotent`（补 dimension 语义核对）、其余断言 approve→approved+bump 的用例同 Task 内适配。
Commit: `git add app/classifier/rule_pending.py app/routes/rules_routes.py tests/test_rule_pending.py && git commit -m "feat: Tab1 approve 只写列+去勾词进黑名单，dimension 收口(C1/C3/C4/C13)"`

---

### Task 4: inline_edit 保留词不 approve、new_label 驳残留旧词、dimension 收口（C9/C13）

**Files:**
- Modify: `app/routes/rules_routes.py` — `review_clause_inline_edit`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: 同 Task 3（含 `_confirm_clause`）+ `set_status`
- Produces: `POST /review/clause-pending/{clause_id}/inline-edit` body `{label_ids:[保留], removed_label_ids:[驳], new_label?, dimension?}`。**dimension 收口（C13）**：优先 body，缺失回退「该 clause 唯一 review queue」，仍歧义才 400；不再从任意 pending id 反查。保留词=保持 pending；removed→rejected；new_label≠原 ai_label 且写列成功→驳残留 pending 词。new_label 无 review queue→`_confirm_clause` False→no-op 不报错。

- [ ] **Step 1: 写失败测试**

```python
def test_tab1_inline_keep_words_stay_pending_no_rule(auth_client):
    """inline 保留词不再 approve：保持 pending、不写列、不 bump（词面沉淀仅 Tab2）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        pid = rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [pid], "removed_label_ids": [],
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert st == "pending" and n_rules == 0


def test_tab1_inline_new_label_rejects_residual_words(auth_client):
    """new_label≠ai_label → 残留 pending 词全 rejected（防 Tab2 反嚼覆写）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.8, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["pattern"]: r["status"] for r in conn.execute(
            "SELECT pattern, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
    assert c["dim6_material"] == "混凝土" and q["status"] == "done"
    assert st["钢筋"] == "rejected" and st["混凝土"] == "rejected"


def test_tab1_inline_new_label_no_review_queue_noop(auth_client):
    """new_label 但无 review queue → no-op：不抛错、不改列、不驳词。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert (c or "") == "" and st == "pending"


def test_tab1_inline_cross_dim_ambiguous_400(auth_client):
    """dimension 缺失且该 clause 跨多 review 维 → 400（C13 不再从任意 id 反查）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        for dim in ("dim6", "dim5"):
            rp.insert_pending(conn, cid, dim, "钢筋", "钢筋", 0.9, "bU")
            bq.try_enqueue(conn, cid, dim, 0.0)
            conn.execute("UPDATE classification_queue SET status='review' "
                         "WHERE clause_id=? AND dimension=?", (cid, dim))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土"})  # 无 dimension
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "keep_words_stay_pending or new_label_rejects_residual or new_label_no_review_queue_noop or cross_dim_ambiguous_400" -v`
Expected: FAIL（现 keep→approved+bump；new_label 不驳残留；维度可任意推断）。

- [ ] **Step 3: 实现**（整段替换 `review_clause_inline_edit` 主体）

```python
@router.post("/review/clause-pending/{clause_id}/inline-edit")
async def review_clause_inline_edit(request: Request, clause_id: int, body: dict):
    """Tab1 行内编辑（C1/C3/C9/C13）：删 chip=驳词；保留词=保持 pending；新标签≠原
    ai_label 且写列成功 → 驳残留 pending 词防反嚼。

    body: {label_ids:[保留], removed_label_ids:[删除], new_label, dimension}。
    dimension 收口：优先 body；缺失回退「该 clause 唯一 review queue 的 dimension」，
    仍歧义才 400（不再从任意 pending id 反查）。
    """
    from fastapi.responses import JSONResponse as _JR

    label_ids = body.get("label_ids") or []
    removed_label_ids = body.get("removed_label_ids") or []
    new_label = body.get("new_label")
    dimension = body.get("dimension")
    if not isinstance(label_ids, list) or not isinstance(removed_label_ids, list):
        return _JR({"detail": "label_ids/removed_label_ids 须为数组"}, status_code=400)

    with get_db() as conn:
        # C13 dimension 收口
        if dimension not in _DIM_COLUMN:
            rows = conn.execute(
                "SELECT DISTINCT dimension FROM classification_queue "
                "WHERE clause_id=? AND status='review'", (clause_id,)).fetchall()
            if len(rows) == 1:
                dimension = rows[0]["dimension"]
            else:
                return _JR({"detail": "dimension 缺失或歧义（该条文跨多维或无 review 维）"},
                           status_code=400)

        pending_set = set()
        all_ids = list(label_ids) + list(removed_label_ids)
        if all_ids:
            pending_set = set(r["id"] for r in conn.execute(
                f"SELECT id FROM rule_pending WHERE id IN ({','.join('?' * len(all_ids))}) "
                f"AND status='pending'", all_ids).fetchall())
        rem = [i for i in removed_label_ids if i in pending_set]
        # keep 不 approve、不回填、不 bump——词面沉淀仅 Tab2

        if rem:
            rem_keys = rule_pending.resolve_keys(conn, rem)
            rule_pending.set_status(conn, rem, "rejected")
            for d, p, l in sorted(rem_keys):
                rule_pending.deactivate_fragment(conn, d, p, l)

        if new_label and str(new_label).strip():
            val = str(new_label).strip()
            q = conn.execute(
                "SELECT ai_label FROM classification_queue "
                "WHERE clause_id=? AND dimension=? AND status='review'",
                (clause_id, dimension)).fetchone()
            orig = (q["ai_label"] if q else None)
            written = rule_pending._confirm_clause(conn, clause_id, dimension, val)
            if written and orig is not None and val != orig:
                residual = rule_pending.clause_pending_ids(conn, clause_id, dimension)
                if residual:
                    rkeys = rule_pending.resolve_keys(conn, residual)
                    rule_pending.set_status(conn, residual, "rejected")
                    for d, p, l in sorted(rkeys):
                        rule_pending.deactivate_fragment(conn, d, p, l)

    log_action("review", "INFO", "条文行内编辑",
               detail=json_detail({"clause_id": clause_id, "dimension": dimension,
                                   "new_label": new_label, "kept": label_ids,
                                   "removed": removed_label_ids}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewClausePending, reviewWordPending, reviewBlacklist"})
```

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "keep_words_stay_pending or new_label_rejects_residual or new_label_no_review_queue_noop or cross_dim_ambiguous_400" -v`
Expected: 4 PASS。

- [ ] **Step 5: 相关测试绿 + 提交（本 Task 内适配被打破的旧 inline 断言）**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`
本 Task 内同步改：`test_tab1_inline_remove_label_rejects`（removed→rejected 仍成立，补 dimension）、`test_tab1_inline_new_label_writes_col_only`（加残留词被驳断言）、`test_tab1_inline_edit_rejects_invalid_dimension`（400 保留）、`test_tab1_inline_cancel_noop`（GET 取消无副作用仍成立）。
Commit: `git add app/routes/rules_routes.py tests/test_rule_pending.py && git commit -m "feat: inline 保留词不 approve、新标签驳残留、dimension 收口(C9/C13)"`

---

### Task 5: process_feedback 纯打标 + review 守卫 + 低置信端点守卫（C1 延伸/C12/C15）

**Files:**
- Modify: `app/classifier/feedback.py` — `process_feedback`
- Modify: `app/routes/rules_routes.py` — `confirm_review`/`reject_review` 加 `status='review'` 守卫；`_fetch_review_items` 去 extract 勾选词
- Modify: `app/templates/partials/review_list.html` — 去候选词勾选 UI
- Test: `tests/test_rule_feedback_loop.py`、`tests/test_review_batch.py`、`tests/test_logs.py`、`tests/test_rules_routes.py`

**Interfaces:**
- Consumes: 无
- Produces: `process_feedback(clause_id, dimension, confirmed_label, source_conf=0.0, patterns=None)`：纯打标写列（review 守卫）+ queue done；patterns/source_conf 参数保留仅签名兼容，忽略。低置信 confirm/reject 端点对非 review 行 no-op。

- [ ] **Step 1: 写失败测试**

```python
def test_process_feedback_ignores_patterns_no_rule(auth_client):
    """低置信确认传 patterns 不再沉淀词面——纯打标（词面沉淀仅 Tab2）。"""
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.5, patterns=["钢筋"])
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        st = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert c == "钢筋" and n_rules == 0 and st == "done"


def test_process_feedback_no_review_queue_noop(auth_client):
    """queue 非 review（已 done）时 process_feedback 不覆写列（C15）。"""
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='done', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
        conn.execute("UPDATE clauses SET dim6_material='混凝土' WHERE id=?", (cid,))
    process_feedback(cid, "dim6", "钢筋")
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
    assert c == "混凝土"   # 不覆写人工定案
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_feedback_loop.py -k "ignores_patterns_no_rule or no_review_queue_noop" -v`
Expected: FAIL（现 patterns 背书生成规则；process_feedback 无条件写列会覆写 混凝土）。

- [ ] **Step 3: 实现**

**3a. `process_feedback` 整段替换为纯打标 + review 守卫（完整自含实现）：**

```python
def process_feedback(clause_id: int, dimension: str, confirmed_label: str,
                     source_conf: float = 0.0, patterns: list[str] | None = None):
    """人工确认：纯打标写列 + queue done（C1 延伸 / C12 / C15）。

    patterns/source_conf 保留仅为签名兼容，忽略（不再背书词面/不再 bump 规则——
    词面普通沉淀仅 Tab2，见 spec C12）。
    仅当 queue 仍 review 时写列（已 done/改标 no-op），闭环 C8「不被覆写」。
    """
    from app.database import get_db
    col = _dim_to_column(dimension)
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE clauses SET {col}=?, ai_classified=1, needs_review=0 WHERE id=? "
            f"AND EXISTS (SELECT 1 FROM classification_queue q "
            f"WHERE q.clause_id=? AND q.dimension=? AND q.status='review')",
            (confirmed_label, clause_id, clause_id, dimension))
        if cur.rowcount:
            conn.execute(
                "UPDATE classification_queue SET status='done' "
                "WHERE clause_id=? AND dimension=? AND status='review'",
                (clause_id, dimension))
```

**3b. `confirm_review`/`reject_review` queue 查询加 `status='review'`**：

```python
# confirm_review:
item = conn.execute(
    "SELECT clause_id, dimension, ai_label, ai_confidence "
    "FROM classification_queue WHERE id=? AND status='review'",
    (queue_id,)).fetchone()
# reject_review:
item = conn.execute(
    "SELECT clause_id, dimension FROM classification_queue "
    "WHERE id=? AND status='review'", (queue_id,)).fetchone()
```

**3c. `_fetch_review_items`**：去掉 `it["keywords"] = extract_keywords(...)`（不再提供勾选词背书）；`review_list.html` 删「候选词」列与 checkbox、`confirmFallback` 的 patterns 收集。

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_feedback_loop.py tests/test_review_batch.py tests/test_logs.py tests/test_rules_routes.py -q`
Expected: 新测试 PASS；既有「确认背书生成规则」断言本 Task 内改纯打标。

- [ ] **Step 5: 提交**

Commit: `git add app/classifier/feedback.py app/routes/rules_routes.py app/templates/partials/review_list.html tests/ && git commit -m "feat: process_feedback 纯打标+review 守卫，低置信端点守卫(C1/C12/C15)"`

---

### Task 6: 前端 review_clause_panel 交互收敛（删批量驳回、checkbox 去勾语义、dimension 必传）

**Files:**
- Modify: `app/templates/partials/review_clause_panel.html`
- Test: 手工/Playwright 冒烟（无新增单测；纯 UI 文案与 JS）

**Interfaces:**
- Consumes: Task 3/4 端点新语义
- Produces: Tab1 主表按钮组 = ✅ 确认标签 | ✏️ 编辑 | 取消（无 ❌ 批量驳回）；词 chip checkbox 默认全勾 + 小字「未勾选的词将进入黑名单」；JS `clauseConfirm(clauseId, dimension)` 收集未勾选词 id 作 `ids` 并**必传 dimension**。

- [ ] **Step 1: 改模板**（按钮区 + JS）

按钮区现状 → 改为：
```html
<button ... onclick="clauseConfirm('{{ g.clause_id }}', '{{ g.dimension }}')">✅ 确认标签</button>
<button class="outline" ... onclick="toggleClauseEdit('{{ g.clause_id }}', '{{ g.dimension }}')">✏️ 编辑</button>
<small style="color:var(--pico-muted-color);font-size:0.7rem">未勾选的词将进入黑名单，不再被提议/沉淀</small>
```
JS `clauseDecide` → `clauseConfirm(clauseId, dimension)`：收集 `#candidates-{cid}-{dim} .cand-check:not(:checked)` 的 value 作 `ids`，`POST .../decide` body `{dimension, ids, action:'approve'}`；确认文案「确认该标签？未勾选的词将进入黑名单。」

- [ ] **Step 2: 冒烟验证**（临时库 + headless Chrome 或 dev 强刷）

验证点：无「批量驳回」按钮；checkbox 默认全勾；确认写列+done、去勾词进黑名单页；跨维条文两维独立可确认。

- [ ] **Step 3: 提交**

Commit: `git add app/templates/partials/review_clause_panel.html && git commit -m "feat: Tab1 主表删批量驳回、checkbox 去勾=黑名单、dimension 必传"`

---

### Task 7: 全量回归收尾 + 低置信块/旧端点回归 + 文档

**Files:**
- Modify: `tests/`（按失败清单）
- Modify: `docs/superpowers/specs/2026-09-06-tab1-tab2-convergence-design.md`（更新验收口径，若 Task 内实现偏离）

**Interfaces:**
- Consumes: 前 6 Task 全部新语义
- Produces: 全量测试绿；无残留旧语义断言；验收口径与实现一致。

- [ ] **Step 1: 全量跑并收集失败**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: 语义 Task 已逐 Task 适配，此处应近绿；剩余为遗漏的旧语义断言或 D1/inline 边角。

- [ ] **Step 2: 逐条适配遗留（清单）**

- `test_reject_keeps_clause_in_tab1_marked` → **inline 删光词不输新标签**模拟「词全驳+queue review」，断言主表渲染「词已驳回」标记（codex #4；approve 已写列 done 行消失，不可用）。
- `test_word_approve_then_clause_queue_already_done_idempotent` → queue done 后词面再批准不写列但规则照常（Task 2 守卫）。
- `test_lowconf_clause_without_pending_still_in_tab1` / `test_clause_pending_get_includes_queue_fallback` → 低置信兜底保留，断言不变；confirm 传 patterns 现被忽略（纯打标）。
- `test_rules_routes.py` `/review/{qid}/reject` → reject 端点保留但加 review 守卫，断言核对。
- D1 / inline 边角用例按 Task 3/4 已适配方向复核。

- [ ] **Step 3: 全量通过**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: 全绿（基线 693 → 预期 695+）。

- [ ] **Step 4: 提交**

Commit: `git add tests/ docs/ && git commit -m "test: 全量回归绿 + spec 验收口径复核（收尾）"`

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ISSUES → ALL RESOLVED | 9 条独立发现，全部经 AUQ 采纳并落进 spec C11-C18 / plan 各 Task |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 3 findings（dimension 收口、写列去重、错误路径测试）已落 plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**CODEX:** 9 findings absorbed — 存量碎片前置(C11/C18)、红点计数口径(C16)、无 queue 行排除(C17)、D1 适配走 inline 全删、并发条件更新(C14)、低置信端点守卫(C15)、「唯一出口」表述收窄+例外(C12)、inline dimension 收口(C13)、每 Task 绿提交(Global Constraints)。

**CROSS-MODEL:** Claude review 独立发现 dimension 歧义/去重/错误路径测试，codex 独立命中同三处并追加 6 处——无冲突，codex 意见全部采纳后与 Claude 结论一致。

**VERDICT:** ENG CLEARED（评审发现全部落进 spec C11-C18 与 plan 各 Task，无未决项）。CEO/Design/DX 本 plan 为语义重构非新产品/视觉/UX 变更，不触发。

NO UNRESOLVED DECISIONS
