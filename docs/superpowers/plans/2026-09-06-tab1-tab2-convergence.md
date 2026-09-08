# Tab1/Tab2 打标-沉淀解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「条文打标」与「词面沉淀」解耦——Tab1 主表批准只写列、不再连带为词建规则；词面沉淀收敛为 Tab2 唯一出口，并堵住反联覆写人工定案的洞。

**Architecture:** 改动集中在 rule_pending 查询/写回与 rules_routes 的 Tab1 动作语义；保留低置信兜底块与旧端点，不动表结构、不动 auto 分支。关键机制：`pending_clause_groups` 排除 queue 终态条文；`_backfill_by_status` 只写 queue `review` 条文；Tab1 `decide approve` = 写 queue ai_label 列 + done + 未勾选词 rejected；`inline_edit` 保留词不再 approve、new_label≠ai_label 时驳残留 pending 词；`process_feedback` 去 patterns 背书变纯打标。

**Tech Stack:** FastAPI + SQLite + pytest + HTMX + Jinja2 partials。

**Spec:** `docs/superpowers/specs/2026-09-06-tab1-tab2-convergence-design.md`（v2 收窄版——低置信块与旧端点保留，仅主表语义改造）。

## Global Constraints

- 数据库读写一律参数化，禁止字符串拼接 SQL（拼 `IN (...)` 占位符除外，须 `','.join('?'*len(xs))`）。
- Python 命令用 `D:/Python/python.exe`（禁用 `python3`）；测试用 `D:/Python/python.exe -m pytest`。
- Git Bash 语法；路径用 `/` 正斜杠。
- 中文注释与交流。
- TDD：每个 Task 先写失败测试再实现，跑通相关全部测试后原子 commit。
- Commit 信息 `type: 描述`（feat/fix/test/docs/refactor/chore）。
- 不得 stage `.claude/CLAUDE.md`（用户遗留未提交）与 `data/`。
- 每次改动后主动 commit；不主动安装依赖、不起后台服务。
- 服务重启（若需浏览器验证）须按项目 CLAUDE.md：全杀 reloader+worker 双进程 → 验证端口干净 → 启动 → 复验唯一监听。
- 测试跑批：`D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`（本 plan 主体）；最后 Task 全量 `tests/ -q`。

---
## 现状速览（Task 实现者必读）

关键现状函数与语义（已核实，行号供参考）：
- `rule_pending.pending_clause_groups`（rule_pending.py:214）：`WHERE rp.status='pending' GROUP BY dimension, clause_id` 聚合有 pending 词的条文，join clauses/specifications 取文本。**未排除 queue 终态条文** → auto_adopted 残留词条文误入主表。
- `rule_pending.rejected_clause_groups`（rule_pending.py:249）：全标签已驳且 queue 仍 review 的条文。
- `rule_pending._backfill_by_status`（rule_pending.py:317）：按 (dim,pattern,label,status) 取来源 `clause_id IS NOT NULL` 条文 → 无条件写列 + queue(review)→done。**无 review 守卫** → 会覆写已人工定案/改标条文。
- `rule_pending.backfill_and_close`（:339）= `_backfill_by_status(..., "pending")`；`approve_rule`（:364）bump confirmed 规则+复活停用碎片；`deactivate_fragment`（:349）停用 confirmed=0 碎片。
- `rules_routes.review_clause_decide`（rules_routes.py:559）：Tab1 行级反义批量。approve → checked 词 backfill+set approved+approve_rule；未勾 rejected+deactivate。reject 反向。
- `rules_routes.review_clause_inline_edit`（rules_routes.py:612）：removed → rejected+deactivate；keep → approve（回填+bump）；new_label → 只写列。
- `feedback.process_feedback`（feedback.py:29）：写列+done + patterns 词 approved 背书+bump。
- `batch_queue.apply_ai_results`（batch_queue.py:80）：已背书词 auto_adopted 写列；首见词 insert_pending+queue review。**本 plan 不动此函数**（C10 已满足）。
- 测试夹具：`tests/test_rule_pending.py` 的 `_db`（tmp 库）、`_seed_spec_clause(conn)`（建 spec+clause 返回 cid）、`_seed_rule`、`auth_client`（conftest 已登录 client）。多数 Tab1 测试用 `auth_client` + `get_db()` 直接造数据。

---

### Task 1: pending_clause_groups 排除终态条文（C5）

**Files:**
- Modify: `app/classifier/rule_pending.py` — `pending_clause_groups`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: 无（独立查询改造）
- Produces: `pending_clause_groups(dimension=None) -> list[dict]`，返回结构与现一致 `[{dimension, clause_id, spec_code, clause_no, content, candidates:[{id, pattern, label, ai_confidence}], rejected_labels}]`；语义变化：queue 已是 done/auto_adopted/rejected 的条文不再返回。

- [ ] **Step 1: 写失败测试**（验证已 auto_adopted 但有残留 pending 词的条文不出现在主表；无 queue 行的条文仍出现以兼容既有种子）

```python
def test_pending_clause_groups_excludes_terminal_queue(auth_client):
    """queue 已 auto_adopted/done 但有残留 pending 词的条文不再进 Tab1 主表。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='auto_adopted', batch_id='bT', "
            "ai_label='钢筋' WHERE clause_id=?", (cid,))
    groups = rp.pending_clause_groups()
    assert all(g["clause_id"] != cid for g in groups)


def test_pending_clause_groups_keeps_no_queue_rows(auth_client):
    """无 queue 行的 pending 条文（既有种子/直插测试数据）仍出现在主表（不破坏兼容）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
    groups = rp.pending_clause_groups()
    assert any(g["clause_id"] == cid for g in groups)
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "excludes_terminal or keeps_no_queue" -v`
Expected: 1 FAIL（auto_adopted 条文仍被返回）、1 PASS（无 queue 兼容本就成立）。

- [ ] **Step 3: 实现**

修改 `pending_clause_groups` 的 `sql`：在 `WHERE rp.status='pending'` 后追加排除「该 (clause,dimension) 存在 queue 终态行」：

```python
def pending_clause_groups(dimension: str | None = None) -> list[dict]:
    """Tab1 条文多标签视图：status='pending' 按 (dimension, clause_id) 聚合，
    join clauses/specifications 带条文文本。

    终态排除（C5）：queue 该 (clause,dimension) 已是 done/auto_adopted/rejected 的
    条文不再返回——已 auto_adopted 但有残留 pending 词的条文不应出现在「条文待审」。
    无 queue 行的 pending 条文（既有种子/直插测试）仍返回，兼容不破坏。
    """
    from app.database import get_db
    sql = ("SELECT rp.dimension, rp.clause_id, s.code AS spec_code, c.clause_no, c.content "
           "FROM rule_pending rp "
           "JOIN clauses c ON c.id = rp.clause_id "
           "JOIN specifications s ON s.id = c.spec_id "
           "WHERE rp.status='pending' "
           "AND NOT EXISTS ("
           "  SELECT 1 FROM classification_queue q "
           "  WHERE q.clause_id = rp.clause_id AND q.dimension = rp.dimension "
           "  AND q.status IN ('done','auto_adopted','rejected'))")
    args = []
    if dimension:
        sql += " AND rp.dimension=?"
        args.append(dimension)
    sql += " GROUP BY rp.dimension, rp.clause_id ORDER BY rp.dimension, c.clause_no"
    # 下方 rows/cands 循环不变
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

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "excludes_terminal or keeps_no_queue" -v`
Expected: 2 PASS。

- [ ] **Step 5: 全 Task 相关测试 + 提交**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`
Expected: PASS（可能个别既有测试若原本断言 auto_adopted 残留条文在 Tab1 会失败——若失败，检查该测试意图，属预期语义变化，转到最后 Task 适配清单；此处先跑通新增两条）。
Commit: `git add app/classifier/rule_pending.py tests/test_rule_pending.py && git commit -m "feat: pending_clause_groups 排除 queue 终态条文（auto_adopted 残留不入主表）"`

---

### Task 2: _backfill_by_status 加 review 守卫（C8）

**Files:**
- Modify: `app/classifier/rule_pending.py` — `_backfill_by_status`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: 无
- Produces: `_backfill_by_status(conn, dimension, pattern, label, status) -> int`，返回「实际写列条文数」；仅对 queue 仍 `review` 的来源条文写列，queue 终态/已改标条文跳过。

- [ ] **Step 1: 写失败测试**

```python
def test_backfill_skips_terminal_queue_clause(auth_client):
    """反联守卫：queue 已 done 的来源条文不被词面批准覆写（列保持原值），规则照常沉淀。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bC")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='done', batch_id='bC', ai_label='钢筋' "
            "WHERE clause_id=?", (cid,))
        # 人工已定案为别的标签
        conn.execute("UPDATE clauses SET dim6_material='混凝土' WHERE id=?", (cid,))
    n = 0
    with get_db() as conn:
        n = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
    assert n == 0
    assert c == "混凝土"   # 未被反联覆写
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k backfill_skips_terminal -v`
Expected: FAIL（当前 `_backfill_by_status` 无条件写列 → n==1、c=='钢筋'）。

- [ ] **Step 3: 实现**

修改 `_backfill_by_status`：取来源条文后，仅保留「queue 该 (clause_id,dimension) 状态为 review」的条文写列：

```python
def _backfill_by_status(conn, dimension: str, pattern: str, label: str,
                        status: str) -> int:
    """该键在指定 status 下的来源条文写列 + 其 queue(review) 置 done。返回写列条文数。

    C8 反联守卫：仅对 queue 仍 status='review' 的来源条文写列——已 done/auto_adopted/
    rejected（Tab1 已定案、inline 已改标）的条文跳过，防词面反联覆写人工决定。
    无 queue 行的来源条文不写列（无「待 AI 打标」事实）。
    """
    col = _dim_column(dimension)
    rows = conn.execute(
        "SELECT DISTINCT rp.clause_id FROM rule_pending rp "
        "JOIN classification_queue q ON q.clause_id = rp.clause_id AND q.dimension = rp.dimension "
        "WHERE rp.dimension=? AND rp.pattern=? AND rp.label=? AND rp.status=? "
        "AND rp.clause_id IS NOT NULL AND q.status='review'",
        (dimension, pattern, label, status)).fetchall()
    cids = [r["clause_id"] for r in rows]
    if cids:
        ph = ','.join('?' * len(cids))
        conn.execute(
            f"UPDATE clauses SET {col}=?, ai_classified=1 WHERE id IN ({ph})",
            [label, *cids])
        conn.execute(
            f"UPDATE classification_queue SET status='done' WHERE clause_id IN ({ph}) "
            f"AND dimension=? AND status='review'",
            [*cids, dimension])
    return len(cids)
```

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k backfill_skips_terminal -v`
Expected: PASS。

- [ ] **Step 5: 回归既有反联测试 + 提交**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "backfill or decide or word" -q`
说明：既有词面批准反联测试（`test_decide_approve_key_level_backfills_and_bumps` 等）在 seed 时把 queue 置为 review → 仍会写列，应通过。若个别 seed 未建 queue review 而断言写列，需在测试 seed 里补 `try_enqueue` + 置 review（属预期语义收紧）。
Commit: `git add app/classifier/rule_pending.py tests/test_rule_pending.py && git commit -m "feat: 词面反联加 review 守卫——已定案/改标条文不被覆写(C8)"`

---

### Task 3: Tab1 decide approve 改只写列 + 去勾词 rejected（C1/C3/C4）

**Files:**
- Modify: `app/routes/rules_routes.py` — `review_clause_decide`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: `rule_pending.clause_pending_ids(conn, clause_id, dimension)`、`rule_pending.resolve_keys`、`rule_pending.set_status`、`rule_pending.deactivate_fragment`、`_DIM_COLUMN`
- Produces: `POST /review/clause-pending/{clause_id}/decide` body `{ids:[...], action:"approve"}` 新语义：ids = 去勾（要驳回）的词 id；approve = 写该条 queue ai_label 列 + queue done + ids 词 rejected（deactivate_fragment）+ 其余 pending 词保持不动。action="reject" 返回 400（按钮已删）。响应头 `HX-Trigger: reviewClausePending, reviewWordPending, reviewBlacklist`。

- [ ] **Step 1: 写失败测试**

```python
def test_tab1_approve_writes_col_keeps_checked_rejects_unchecked(auth_client):
    """Tab1 确认 = 只写 queue ai_label 列 + queue done；勾选词留 pending(不 approved 不 bump)；
    去勾词 rejected。不再为任何词建规则。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.7, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review', batch_id='bT', "
            "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        # 去勾「混凝土」→ 应驳回；「钢筋」勾选 → 留 pending
        keep = [r["id"] for r in conn.execute(
            "SELECT id FROM rule_pending WHERE clause_id=? AND pattern='钢筋'", (cid,)).fetchall()]
        reject = [r["id"] for r in conn.execute(
            "SELECT id FROM rule_pending WHERE clause_id=? AND pattern='混凝土'", (cid,)).fetchall()]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"ids": reject, "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["pattern"]: r["status"] for r in conn.execute(
            "SELECT pattern, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert st["钢筋"] == "pending" and st["混凝土"] == "rejected"   # 勾选保留 pending，去勾驳
    assert n_rules == 0                                              # 不沉淀任何规则


def test_tab1_decide_reject_action_now_400(auth_client):
    """Tab1 批量驳回按钮已删 → reject action 返回 400（防旧前端误调）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"ids": [pid], "action": "reject"})
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "writes_col_keeps_checked or reject_action_now_400" -v`
Expected: FAIL（现 approve=词 approved+bump → n_rules 非 0 / 勾选词被 approved；reject 现成功）。

- [ ] **Step 3: 实现**（整段替换 `review_clause_decide`）

```python
@router.post("/review/clause-pending/{clause_id}/decide")
async def review_clause_decide(request: Request, clause_id: int, body: dict):
    """Tab1 主表确认标签（C1/C3/C4）：只写该条 queue ai_label 列 + queue done。

    body: {ids:[词面 id], action:"approve"}。ids = 去勾（要驳回）的词面 id（可为空=纯确认）；
    approve → 写 ai_label 列 + queue done；ids 词置 rejected（停用碎片）；
    作用域内其余 pending 词保持 pending（流入 Tab2 池，不标 approved、不 approve_rule）。
    action="reject" 已废弃（批量驳回按钮删除）→ 400。
    """
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("ids") or []
    action = body.get("action")
    if action != "approve" or not isinstance(ids, list):
        return _JR({"detail": "仅支持 approve（批量驳回已移除）"}, status_code=400)

    with get_db() as conn:
        # 作用域维度：有去勾 ids 时由 ids 反查；空 ids（纯确认）由 queue review 反查
        if ids:
            dims = {r["dimension"] for r in conn.execute(
                f"SELECT DISTINCT dimension FROM rule_pending "
                f"WHERE id IN ({','.join('?' * len(ids))}) AND status='pending'", ids).fetchall()}
            if not dims:
                return _JR({"detail": "无有效 pending 勾选"}, status_code=400)
            if len(dims) != 1:
                return _JR({"detail": "勾选须同维度"}, status_code=400)
            dimension = next(iter(dims))
        else:
            qd = conn.execute(
                "SELECT dimension FROM classification_queue "
                "WHERE clause_id=? AND status='review' LIMIT 1", (clause_id,)).fetchone()
            if not qd:
                return _JR({"detail": "该条文无 review 队列项"}, status_code=400)
            dimension = qd["dimension"]

        scope = rule_pending.clause_pending_ids(conn, clause_id, dimension)
        if ids and not set(ids) <= set(scope):
            return _JR({"detail": "勾选 id 不属于该条文待审作用域"}, status_code=400)

        # 写列：取该条 queue 的 ai_label（review 条文由 apply_ai_results 写入，应存在）
        q = conn.execute(
            "SELECT ai_label FROM classification_queue "
            "WHERE clause_id=? AND dimension=? AND status='review'",
            (clause_id, dimension)).fetchone()
        label = q["ai_label"] if q else None
        col = _DIM_COLUMN.get(dimension)
        if label and col:
            conn.execute(
                f"UPDATE clauses SET {col}=?, ai_classified=1, needs_review=0 WHERE id=?",
                (label, clause_id))
            conn.execute(
                "UPDATE classification_queue SET status='done' "
                "WHERE clause_id=? AND dimension=? AND status='review'",
                (clause_id, dimension))

        # 去勾词 → rejected（黑名单 + 停用碎片）；作用域内其余 pending 词不动
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

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "writes_col_keeps_checked or reject_action_now_400" -v`
Expected: 2 PASS。

- [ ] **Step 5: 回归既有 Tab1 测试（预期需同步修改断言）+ 提交**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`
Expected: 与「Tab1 approve 词 approved+bump」相关测试失败——这些断言是旧语义，按 Task 7 适配清单逐条改（本 Task 先改新增两条通过，回归失败项登记不阻塞提交，但**本 Task 提交前应先把直接冲突的 2-3 个用例改到新语义**，例如 `test_tab1_clause_multi_label_approve_partial` 改为断言「勾选词 pending / 未勾 rejected / 无规则」）。
Commit: `git add app/routes/rules_routes.py tests/test_rule_pending.py && git commit -m "feat: Tab1 approve 只写列+去勾词进黑名单，删批量驳回语义(C1/C3/C4)"`

---

### Task 4: inline_edit 保留词不 approve、new_label 驳残留旧词（C9）

**Files:**
- Modify: `app/routes/rules_routes.py` — `review_clause_inline_edit`
- Test: `tests/test_rule_pending.py`

**Interfaces:**
- Consumes: 同 Task 3 + `rule_pending.set_status`
- Produces: `POST /review/clause-pending/{clause_id}/inline-edit` body `{label_ids:[保留], removed_label_ids:[驳], new_label?, dimension?}` 新语义：保留词=保持 pending（**不再 approve/回填/bump**）；removed → rejected；new_label 非空 → 写该列 + queue done，且若 new_label ≠ 原 queue ai_label 则把该 (clause,dimension) 残留 pending 词全部 rejected（防 Tab2 反嚼覆写）。

- [ ] **Step 1: 写失败测试**

```python
def test_tab1_inline_keep_words_stay_pending_no_rule(auth_client):
    """inline 保留词不再 approve：保持 pending、不写列、不 bump 规则（词面沉淀仅 Tab2）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        pid = rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review', ai_label='钢筋' WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [pid], "removed_label_ids": [],
                                  "new_label": None})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert st == "pending"
    assert n_rules == 0


def test_tab1_inline_new_label_rejects_residual_words(auth_client):
    """inline 新标签 ≠ queue ai_label → 该条残留 pending 词全 rejected（防 Tab2 反嚼覆写）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.8, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review', ai_label='钢筋' WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["pattern"]: r["status"] for r in conn.execute(
            "SELECT pattern, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
    assert c["dim6_material"] == "混凝土"
    assert q["status"] == "done"
    assert st["钢筋"] == "rejected" and st["混凝土"] == "rejected"   # 残留全驳
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "keep_words_stay_pending or new_label_rejects_residual" -v`
Expected: FAIL（现 keep → approved+bump；new_label 只写列不驳残留）。

- [ ] **Step 3: 实现**（整段替换 `review_clause_inline_edit` 主体；保留 dimension 白名单校验）

```python
@router.post("/review/clause-pending/{clause_id}/inline-edit")
async def review_clause_inline_edit(request: Request, clause_id: int, body: dict):
    """Tab1 行内编辑（C1/C3/C9）：删 chip=驳词；保留词=保持 pending（不 approve）；
    新标签=写列 + queue done +（≠原 ai_label 时）驳残留 pending 词防反嚼覆写。

    body: {label_ids:[保留 pending id], removed_label_ids:[删除 pending id],
           new_label: str|None, dimension: str|None}。
    """
    from fastapi.responses import JSONResponse as _JR

    label_ids = body.get("label_ids") or []
    removed_label_ids = body.get("removed_label_ids") or []
    new_label = body.get("new_label")
    dimension = body.get("dimension")
    if not isinstance(label_ids, list) or not isinstance(removed_label_ids, list):
        return _JR({"detail": "label_ids/removed_label_ids 须为数组"}, status_code=400)

    with get_db() as conn:
        all_ids = list(label_ids) + list(removed_label_ids)
        if not dimension:
            if all_ids:
                d_row = conn.execute(
                    f"SELECT dimension FROM rule_pending "
                    f"WHERE id IN ({','.join('?' * len(all_ids))}) "
                    f"ORDER BY id DESC LIMIT 1", all_ids).fetchone()
                dimension = d_row["dimension"] if d_row else None
            if not dimension:
                dr = conn.execute(
                    "SELECT dimension FROM rule_pending WHERE clause_id=? "
                    "ORDER BY id DESC LIMIT 1", (clause_id,)).fetchone()
                dimension = dr["dimension"] if dr else None
        if dimension and dimension not in _DIM_COLUMN:
            return _JR({"detail": "dimension 不合法"}, status_code=400)

        if dimension:
            pending_set = set()
            if all_ids:
                pending_set = set(r["id"] for r in conn.execute(
                    f"SELECT id FROM rule_pending WHERE id IN ({','.join('?' * len(all_ids))}) "
                    f"AND status='pending'", all_ids).fetchall())
            rem = [i for i in removed_label_ids if i in pending_set]
            keep = [i for i in label_ids if i in pending_set]

            # 删除项 → rejected（黑名单 + 停用碎片）；保留项 → 保持 pending（不 approve）
            if rem:
                rem_keys = rule_pending.resolve_keys(conn, rem)
                rule_pending.set_status(conn, rem, "rejected")
                for d, p, l in sorted(rem_keys):
                    rule_pending.deactivate_fragment(conn, d, p, l)
            # keep：不 approve、不回填、不 bump——词面沉淀仅 Tab2

            col = _DIM_COLUMN[dimension]
            if new_label and str(new_label).strip():
                val = str(new_label).strip()
                # 取 queue 原 ai_label，判定是否人工改标
                q = conn.execute(
                    "SELECT ai_label FROM classification_queue "
                    "WHERE clause_id=? AND dimension=? AND status='review'",
                    (clause_id, dimension)).fetchone()
                orig = (q["ai_label"] if q else None)
                conn.execute(
                    f"UPDATE clauses SET {col}=?, ai_classified=1, needs_review=0 WHERE id=?",
                    (val, clause_id))
                conn.execute(
                    "UPDATE classification_queue SET status='done' "
                    "WHERE clause_id=? AND dimension=? AND status='review'",
                    (clause_id, dimension))
                # C9：新标签 ≠ 原 AI 标签 → 残留 pending 词全驳（防 Tab2 反嚼覆写）
                if orig is not None and val != orig:
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

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "keep_words_stay_pending or new_label_rejects_residual" -v`
Expected: 2 PASS。

- [ ] **Step 5: 回归 + 提交**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -q`
Expected: 旧「inline keep=approve」断言失败登记 Task 7 适配；本 Task 两条新通过后提交。
Commit: `git add app/routes/rules_routes.py tests/test_rule_pending.py && git commit -m "feat: inline 保留词不 approve、新标签≠ai_label 驳残留 pending 词(C9)"`

---

### Task 5: process_feedback 去 patterns 背书 → 纯打标（低置信 confirm 收敛）

**Files:**
- Modify: `app/classifier/feedback.py` — `process_feedback`
- Modify: `app/routes/rules_routes.py` — `_fetch_review_items`（去 extract 勾选词）、confirm/batch_confirm（停传 patterns）
- Modify: `app/templates/partials/review_list.html` — 去候选词勾选 UI
- Test: `tests/test_rule_feedback_loop.py`、`tests/test_rule_pending.py`、`tests/test_logs.py`、`tests/test_review_batch.py`

**Interfaces:**
- Consumes: 无
- Produces: `process_feedback(clause_id, dimension, confirmed_label, source_conf=0.0, patterns=None)` 语义：写列 + queue done；`patterns` 参数**忽略**（保留签名兼容，不背书不 bump）。词面沉淀仅 Tab2，低置信 confirm 不再产生规则。

- [ ] **Step 1: 写失败测试**

```python
def test_process_feedback_ignores_patterns_no_rule(auth_client):
    """低置信确认传 patterns 也不再沉淀词面——纯打标（词面沉淀仅 Tab2）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review', ai_label='钢筋' WHERE clause_id=?", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.5, patterns=["钢筋"])
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        st = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert c == "钢筋"
    assert n_rules == 0
    assert st == "done"
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_feedback_loop.py -k ignores_patterns -v`
Expected: FAIL（现 patterns 背书生成规则 → n_rules>0）。

- [ ] **Step 3: 实现**（process_feedback 去掉 patterns 背书分支）

```python
def process_feedback(clause_id: int, dimension: str, confirmed_label: str,
                     source_conf: float = 0.0, patterns: list[str] | None = None):
    """人工确认：纯打标写列 + queue done（C1 延伸——词面沉淀仅 Tab2）。

    patterns 参数保留仅为签名兼容，忽略（不再背书词面/不再 bump 规则）。
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_queue SET status = 'done' WHERE clause_id = ? AND dimension = ?",
            (clause_id, dimension),
        )
        col = _dim_to_column(dimension)
        conn.execute(
            f"UPDATE clauses SET {col} = ?, ai_classified = 1, needs_review = 0 WHERE id = ?",
            (confirmed_label, clause_id),
        )
```

同时：
- `_fetch_review_items`：去掉 `it["keywords"] = extract_keywords(...)`（不再提供勾选词背书入口）；若前端还需展示可留空列表。
- `review_list.html`：删「候选词（勾选背书）」表头列与 checkbox 渲染、`confirmFallback` 的 `patterns` 收集（POST body 传空或不传 patterns）。
- confirm / batch_confirm 路由：不再从 body 读 patterns 传给 process_feedback（或传 patterns=None）。

- [ ] **Step 4: 运行通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_feedback_loop.py tests/test_review_batch.py tests/test_logs.py -q`
Expected: 新测试 PASS；既有「确认背书生成规则」断言失败→按 Task 7 适配。

- [ ] **Step 5: 提交**

Commit: `git add app/classifier/feedback.py app/routes/rules_routes.py app/templates/partials/review_list.html tests/ && git commit -m "feat: process_feedback 去 patterns 背书——低置信确认纯打标(C1延伸)"`

---

### Task 6: 前端 review_clause_panel 交互收敛（删批量驳回、checkbox 去勾语义）

**Files:**
- Modify: `app/templates/partials/review_clause_panel.html`
- Test: 手工/Playwright 冒烟（无新增单测；改动纯 UI 文案与 JS 动作）

**Interfaces:**
- Consumes: Task 3/4 端点新语义
- Produces: Tab1 主表按钮组 = ✅ 确认 | ✏️ 编辑 | 取消（无 ❌ 批量驳回）；词 chip checkbox 默认全勾 + 小字「未勾选的词将进入黑名单」；JS `clauseDecide` 收集**未勾选词 id** 作为 `ids` 调 approve。

- [ ] **Step 1: 改模板**（review_clause_panel.html 主表按钮区 + JS）

现状按钮区（第 33-44 行附近）：
```html
<button ... onclick="clauseDecide('{{ g.clause_id }}', '{{ g.dimension }}', 'approve')">✅ 批量确认</button>
<button ... class="outline secondary" ... onclick="clauseDecide('{{ g.clause_id }}', '{{ g.dimension }}', 'reject')">❌ 批量驳回</button>
<button ... onclick="toggleClauseEdit(...)">✏️ 编辑</button>
<small ...>未勾选标签将自动驳回/通过</small>
```
改为：
```html
<button ... onclick="clauseConfirm('{{ g.clause_id }}', '{{ g.dimension }}')">✅ 确认标签</button>
<button class="outline" ... onclick="toggleClauseEdit('{{ g.clause_id }}', '{{ g.dimension }}')">✏️ 编辑</button>
<small style="color:var(--pico-muted-color);font-size:0.7rem">未勾选的词将进入黑名单，不再被提议/沉淀</small>
```
JS：`clauseDecide` 改为 `clauseConfirm(clauseId, dimension)`——收集 `#candidates-{cid}-{dim} .cand-check:not(:checked)` 的 value 作为 `ids`，调 `POST .../decide` body `{ids, action:'approve'}`；确认文案「确认该标签？未勾选的词将进入黑名单。」

- [ ] **Step 2: 冒烟验证**（起临时库 + headless Chrome，参照会话既有 playwright 冒烟模式；或仅手工强刷 dev 验证按钮/文案）

验证点：主表无「批量驳回」按钮；checkbox 默认全勾；确认后列写入、queue done、去勾词进黑名单页。
（此项不产单测；如环境不便起服务可跳过自动冒烟，改由用户在 dev 强刷人工确认。）

- [ ] **Step 3: 提交**

Commit: `git add app/templates/partials/review_clause_panel.html && git commit -m "feat: Tab1 主表删批量驳回、checkbox 去勾=黑名单交互"`

---

### Task 7: 全量回归与测试适配收尾

**Files:**
- Modify: `tests/test_rule_pending.py`、`tests/test_rule_feedback_loop.py`、`tests/test_review_batch.py`、`tests/test_logs.py`、`tests/test_rules_routes.py`（按失败清单）

**Interfaces:**
- Consumes: 前 6 Task 全部新语义
- Produces: 全量测试绿；无残留旧语义断言。

- [ ] **Step 1: 全量跑并收集失败**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: 失败集中在「Tab1 approve/inline/confirm = 词 approved / bump 规则 / 候选词背书」旧语义断言。

- [ ] **Step 2: 逐条适配**（清单，覆盖 spec §八）

- `test_tab1_clause_multi_label_approve_partial` → 改：勾选词 `pending`、未勾词 `rejected`、列写入 ai_label、queue done、无规则。
- `test_tab1_inline_remove_label_rejects` → 语义仍成立（removed → rejected），核对无 approve 副作用。
- `test_tab1_inline_new_label_writes_col_only` → 加断言残留 pending 词被驳（C9）。
- `test_tab1_inline_cancel_noop` → 应仍成立。
- `test_reject_keeps_clause_in_tab1_marked` → 原走 reject action 已删 → 改为「词全驳 + 仍留主表待重标」（用 approve + 全 ids 去勾模拟，或 inline 新标签路径），断言主表渲染「词已驳回」标记。
- `test_word_approve_then_clause_queue_already_done_idempotent` → queue done 后词面再批准不再写列但规则照常（Task 2 守卫），核对断言。
- `test_rule_feedback_loop.py` 背书用例 → 改纯打标断言。
- `test_logs.py`/`test_review_batch.py` → batch_confirm 不再传 patterns，若断言日志含背书词则去掉。
- `test_rules_routes.py` `/review/{qid}/reject` 用例 → queue rejected 语义保留（reject 端点未删）应仍通过，核对。

- [ ] **Step 3: 全量通过**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: 全绿（基线 693 → 预期 695+）。

- [ ] **Step 4: 提交**

Commit: `git add tests/ && git commit -m "test: 适配 Tab1 打标-沉淀解耦语义（全量回归绿）"`

---

## Self-Review 记录（写完即自查）

- **Spec 覆盖**：C1(§Task3/4/5) ✓；C2(不动 apply_ai_results) ✓；C3(Task3/6 checkbox 去勾) ✓；C4(Task3 reject 400 + Task6 删按钮) ✓；C5(Task1) ✓；C6(Tab1 主表+低置信保留，未动 review_list 结构) ✓；C7(chip 展示未动) ✓；C8(Task2) ✓；C9(Task4) ✓；C10(auto 分支不动，回归) ✓。低置信块保留、旧端点保留 ✓。
- **占位符**：无 TBD/TODO；Task 3 approve 的 ids 空/非空两分支已在同一函数体内给出可运行代码。各 Task Step 3 均为完整可运行实现。
- **类型一致**：`clause_pending_ids`/`resolve_keys`/`set_status`/`deactivate_fragment`/`_DIM_COLUMN` 沿用现有签名；`process_feedback` 保留 `patterns` 参数位。HX-Trigger 与前端事件名一致。
