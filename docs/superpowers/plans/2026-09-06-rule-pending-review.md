# AI 沉淀规则「首审 + 驳回黑名单」实施计划（rule-pending）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 自动沉淀的词面规则首次必须人工校核；approved 组合此后免审走半监督，rejected 组合进黑名单不再沉淀（可恢复）；废除「条文确认即整条文 top3 全背书」的夹带。

**Architecture:** 新表 `rule_pending`，裁决单元 = `(clause_id, dimension, pattern, label)` 四元组。`apply_ai_results` auto 分支按组合三态（approved/rejected/首次）裁决；条文人工确认与「AI 词面校核」「黑名单管理」共享同一组行、两个聚合视图；复核 UI 为 `/review` 三 Tab + 反义批量（checkbox 默认全勾）。

**Tech Stack:** FastAPI + SQLite（同项目），HTMX 事件链沿用。

**Spec:** `docs/superpowers/specs/2026-09-06-rule-pending-review-design.md`

## Global Constraints

1. 免审判定键 = 组合级 `(dimension, pattern, label)`。approved = `classification_rules.confirmed≥1` 同键 **或** `rule_pending.approved`；rejected = `rule_pending.rejected`；其余首次。
2. `apply_ai_results`：存在 ≥1 已背书候选词 → 该条 `auto_adopted`（写列 + 对已背书词 `bump` 命中递增）；存在 rejected 词跳过；全部首次 → 该条 `status='review'`、不写列、逐词插 `rule_pending(pending)`。
3. 条文人工确认（process_feedback）只写列 + 仅对勾选词 approved 背书（废除 top3 全 bump）；patterns 空 = 纯打标。
4. 反义批量：`approve` → 勾选 approved、同作用域未勾 rejected；`reject` → 勾选 rejected、同作用域未勾 approved。UI 按钮下明示反向语义小字。
5. 黑名单管理两档：rejected→pending（恢复待审）、rejected→approved（批准）。无物理删除入口。
6. 词面批准/驳回后：批准回填该组合全部 pending 来源条文的分类列 + 联动清 queue（该条文该维 review→done）；驳回不写列。
7. SQL 参数化；测试临时库 `monkeypatch DATABASE_PATH + init_db`；改 Python 后按 CLAUDE.md §三重启 dev。
8. 现状已回滚（HEAD=ceb3f59）：无 termdict 代码，判定以规则 confirmed 与 rule_pending 为准。
9. **Tab1 数据源 = `rule_pending` 按条文(clause)聚合**：同条文的多个候选标签（各自 pending 行）并排展示 + 反义批量 checkbox（D4）。`classification_queue` 降级为状态/终态引用 + 「无 pending 词的低置信条文」兜底源，不扩展多 label。
10. **驳回后 queue 处置（评审 D1）+ 新标签纯打标（评审 F7 用户裁决）**：驳回某组合后其来源条文**滞留 Tab1**，标注「词已驳回，请为条文输入新标签」；行内「编辑」展开该条文分类输入框（复用规范管理页 clause 分类编辑控件），预填「未驳回的候选标签」——删除某项=该标签驳回，保留=approved；**输入全新标签=只写列（纯打标），不做任何自动/显式规则沉淀**。依据（用户确认）：纯打标在分类树/检索按分类列即生效，无需规则承载；如需该 (词,标签) 成为规则，走既有 approved 通道（词面/Tab 对该标签 approve，或规则页手动建）。取消=不落任何改动。
11. 词/规则沉淀入口收敛：规则沉淀只来自 ① Tab 批量/行内 approve 的候选组合 ② 规则页人工建规则（+ AI auto 对已背书组合的命中递增）；**inline 新标签不沉淀**。任何路径都不再对未选词隐式沉淀。

## Outside Voice 评审修订（2026-09-06，codex 独立意见 F1–F6，覆盖上方 Task1/4/5 相抵触字句）

- **F1 / F2 — 裁决按「键」而非 rowid；approved 键最终都回填**：`decide_scope` 改为**键级作用域**——勾选 id 展开为它们的 `(dimension, pattern, label)` 键集合；approve：全部这些键的 pending 行（跨条文）置 approved，同 pattern 组内其它键置 rejected；reject 反向（勾选键 rejected，其余键 approved）。**任一动作后**，对「最终转为 approved 的键」统一执行 `backfill_and_close`（写列 + 其 queue review→done）+ `bump_rule(is_confirmed=True, new_rule_active=True, label=键.label)`（人工信号写回 confirmed，F4）。reject 清场不回填（只对键置 rejected）。Task1 的 `decide_scope`/`_key_all_pending_ids` 与本 Constraint 冲突处，以此为准；Task5/6 相应测试改键级断言。
- **F3 / F8 — approved 规则判定 = confirmed>0 且 is_active=1 且容忍 label 旧值**：`key_state` 判 `classification_rules` 侧 approved 改为：同 `(dimension, pattern)`、`confirmed>0`、`is_active=1`、且 `(rule.label IS NULL OR rule.label = ?)` 的行即 approved（旧/规则页手工建规则 label 为 NULL 也算背书；**被自动停用的历史确认规则不算背书**，AI 再提回首次待审）。同一 pattern 多 label 无法在规则表分行的限制沿用 bump_rule 现行「按 (dim,pattern) 取首行、写/回填其 label」语义并加注释。
- **F4 — 批准即 bump**：所有转 approved 的人工路径（词面 approve、Tab 行/批量 approve、黑名单 approve、inline 保留标签）统一触发该键 `bump_rule(is_confirmed=True)`（F1/F2 修订已含）；幂等（已存在 confirmed 规则时仅 hit/确认路径语义，见 bump 既有实现）。
- **F5 — 修正两处自相矛盾测试**：① `insert_pending` 同 `(clause,key)` 二次必 None——「第二行不同批 pending」断言改为「不同 clause 两行」；② Tab1 批准前把 queue 状态置 `review`（与真实首次流一致）再断言 `done`。
- **F6 — 并发幂等落库**：`rule_pending` 加 `UNIQUE(dimension, pattern, label, clause_id)`（见下建表修订），`insert_pending` 捕获 `IntegrityError` 返回 None；不依赖 SELECT+INSERT 防并发。

---

## File Structure

**新建**
- `app/classifier/rule_pending.py` — 状态查询/幂等插入/回填/反义批量/聚合查询/黑名单查询
- `app/templates/partials/review_word_panel.html`（Tab2 词面校核）、`app/templates/partials/review_blacklist.html`（Tab3）
- `tests/test_rule_pending.py`

**修改**
- `app/database.py`：`SCHEMA_SQL` 追加 `rule_pending` 表 + 索引
- `app/classifier/batch_queue.py`：`apply_ai_results` 裁决流
- `app/classifier/feedback.py`：`process_feedback` 写列+勾选背书
- `app/routes/rules_routes.py`：review 页三 Tab、confirm/batch_confirm 带勾选词、词面/黑名单端点
- `app/templates/partials/review_list.html`：Tab1 候选词 checkbox + 双按钮（行内）
- `tests/test_batch_queue.py`、`tests/test_rule_feedback_loop.py`、`tests/test_classifier_ai.py`、`tests/test_logs.py`、`tests/test_review_batch.py`：语义回归适配

---

### Task 1: `rule_pending` 建表 + 核心层 `app/classifier/rule_pending.py`

**Files:**
- Modify: `app/database.py`（SCHEMA_SQL）
- Create: `app/classifier/rule_pending.py`
- Test: `tests/test_rule_pending.py`（新建）

**Interfaces:**
- Produces:
  - `key_state(conn, dimension, pattern, label) -> str`：`"approved" | "rejected" | "none"`
  - `insert_pending(conn, clause_id, dimension, pattern, label, confidence, batch_id) -> int|None`（幂等：同 `(clause,key)` 已有任一状态行则 None）
  - `set_status(conn, ids: list[int], status: str) -> int`
  - `decide_scope(conn, ids: list[int], action: str) -> dict`：action=`approve|reject`；作用域由 ids 取整批「该词面该分组」去重为集合 S（实现：ids 行各自 (dimension,pattern) 扩张为同组全 pending id 集），勾选=ids 交集，未勾=S−ids；approve→勾选 approved/未勾 rejected，reject 反向；返回 `{"approved": n, "rejected": m}`。**注意**：若调用方已给「完整作用域 id 集」，本函数不再扩张（见各调用点说明，参数 `expand=True` 控制）。
  - `pending_groups(dimension: str|None) -> list[dict]`：`status='pending'` 按 `(dimension, pattern)` 聚合 → `{dimension, pattern, labels:[{id,label,confidence,n_clauses}], clause_count, avg_conf}`，供 Tab2。
  - `pending_clause_groups(dimension: str|None) -> list[dict]`：`status='pending'` 按 `(dimension, clause_id)` 聚合，join clauses/specifications 带条文文本 → 每条文 `{dimension, clause_id, spec_code, clause_no, content, candidates:[{id,pattern,label,confidence}]}`，供 **Tab1 条文多标签视图**（queue 兜底的低置信项由路由另取并合成）。
  - `blacklist_rows() -> list[dict]`：`status='rejected'` 行（词面/标签/clause 数/最近驳回），供 Tab3。
  - `clause_pending_ids(conn, clause_id, dimension) -> list[int]`：某条文该维全部 pending 行 id（Tab1 行内编辑/行内确认的作用域）。
  - `backfill_and_close(conn, dimension, pattern, label) -> int`：该键全部 pending 来源条文写列 + 其 queue(review) 置 done（见 Task4，本函数同文件实现，Task1 先实现写列与 close 两段）
  - `KEY_DIMS = ("dim4", "dim5", "dim6")`

- [ ] **Step 1: 写失败测试** `tests/test_rule_pending.py`

```python
import pytest
from app.database import init_db, get_db
from app.classifier import rule_pending as rp


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()


def _seed_clause(conn, spec="GB1"):
    conn.execute("INSERT INTO specifications (code,title) VALUES (?, 'x')", (spec,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id,clause_no,content) VALUES (?, '1.1', '钢筋 条文')", (sid,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_key_state_none_then_approved_by_rule_confirmed(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "none"
        conn.execute("INSERT INTO classification_rules (dimension,pattern,label,threshold,confirmed,is_active)"
                     " VALUES ('dim6','钢筋','钢筋',0.6,1,1)")
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "approved"


def test_key_state_rejected_priority_over_rule(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "试验", "钢筋", 0.9, "b1")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "试验", "钢筋"), "rejected")
        assert rp.key_state(conn, "dim6", "试验", "钢筋") == "rejected"


def test_insert_pending_idempotent(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        a = rp.insert_pending(conn, cid, "dim6", "检验", "钢筋", 0.9, "b1")
        b = rp.insert_pending(conn, cid, "dim6", "检验", "钢筋", 0.8, "b1")
        assert a is not None and b is None


def test_decide_scope_approve_rejects_unchecked(monkeypatch, tmp_path):
    """approve 且勾选 1/3 → 勾选 approved、其余 2 rejected。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        ids = []
        for lab in ("钢筋", "混凝土", "水泥"):
            ids.append(rp.insert_pending(conn, cid, "dim6", "检验", lab, 0.9, "b1"))
        # expand=False：ids 即完整作用域
        r = rp.decide_scope(conn, [ids[0]], "approve", expand=False)
        assert r == {"approved": 1, "rejected": 2}
        assert rp.key_state(conn, "dim6", "检验", "钢筋") == "approved"
        assert rp.key_state(conn, "dim6", "检验", "混凝土") == "rejected"
        assert rp.key_state(conn, "dim6", "检验", "水泥") == "rejected"


def test_pending_groups_and_blacklist(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.7, "b2")
        rp.insert_pending(conn, c1, "dim6", "试验", "检验", 0.6, "b1")
    groups = rp.pending_groups("dim6")
    assert {g["pattern"] for g in groups} == {"钢筋", "试验"}
    gw = next(g for g in groups if g["pattern"] == "钢筋")
    assert gw["clause_count"] == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -v`
Expected: FAIL（`rule_pending` 模块/表不存在）

- [ ] **Step 3: 建表** `app/database.py`

在 `SCHEMA_SQL` 词库段后、收尾 `"""` 前追加（复用 term_labels 曾占位处，现该块已被回滚，确认无残留）：

```python
CREATE TABLE IF NOT EXISTS rule_pending (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension     TEXT NOT NULL,
    pattern       TEXT NOT NULL,
    label         TEXT NOT NULL,
    clause_id     INTEGER NOT NULL,
    ai_confidence REAL,
    batch_id      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rule_pending_key
    ON rule_pending(dimension, pattern, label, status);
CREATE INDEX IF NOT EXISTS idx_rule_pending_status ON rule_pending(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_pending_uniq
    ON rule_pending(dimension, pattern, label, clause_id);
```

- [ ] **Step 4: 实现** `app/classifier/rule_pending.py`

```python
"""AI 沉淀规则「首审 + 黑名单」核心层。

裁决单元 = (clause_id, dimension, pattern, label) 四元组（rule_pending 一行）。
免审键 = (dimension, pattern, label)：
  approved  = 分类规则同键 confirmed≥1  或  rule_pending.status='approved'
  rejected  = rule_pending.status='rejected'（黑名单）
  其余      = 首次 → 待人工。
写路径（裁决/确认/驳回/恢复）必须经本模块函数，禁止散落 SQL。
"""
KEY_DIMS = ("dim4", "dim5", "dim6")


def _dim_column(dim: str) -> str:
    return {"dim4": "dim4_specialty", "dim5": "dim5_location",
            "dim6": "dim6_material"}.get(dim, dim)


def key_state(conn, dimension: str, pattern: str, label: str) -> str:
    """组合免审状态：approved > rejected > none。"""
    row = conn.execute(
        "SELECT 1 FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
        "AND status IN ('approved','rejected') LIMIT 1",
        (dimension, pattern, label)).fetchone()
    if row:
        st = conn.execute(
            "SELECT status FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
            "AND status IN ('approved','rejected') ORDER BY (status='approved') DESC, id DESC LIMIT 1",
            (dimension, pattern, label)).fetchone()["status"]
        return st
    if conn.execute(
        "SELECT 1 FROM classification_rules WHERE dimension=? AND pattern=? AND label=? "
        "AND confirmed > 0 LIMIT 1",
        (dimension, pattern, label)).fetchone():
        return "approved"
    return "none"


def insert_pending(conn, clause_id: int, dimension: str, pattern: str, label: str,
                   confidence: float | None = None, batch_id: str = "") -> int | None:
    """AI 首次提案词 → pending 行。同 (clause, key) 已有任一状态行则不重插。"""
    if conn.execute(
        "SELECT 1 FROM rule_pending WHERE clause_id=? AND dimension=? AND pattern=? "
        "AND label=? LIMIT 1", (clause_id, dimension, pattern, label)).fetchone():
        return None
    cur = conn.execute(
        "INSERT INTO rule_pending (dimension, pattern, label, clause_id, ai_confidence, batch_id)"
        " VALUES (?,?,?,?,?,?)",
        (dimension, pattern, label, clause_id, confidence, batch_id))
    return cur.lastrowid


def pending_ids(conn, dimension: str, pattern: str, label: str) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
        "AND status='pending'", (dimension, pattern, label)).fetchall()
    return [r["id"] for r in rows]


def set_status(conn, ids: list[int], status: str) -> int:
    if not ids:
        return 0
    cur = conn.execute(
        f"UPDATE rule_pending SET status=?, updated_at=datetime('now','localtime') "
        f"WHERE id IN ({','.join('?' * len(ids))})",
        [status, *ids])
    return cur.rowcount


def decide_scope(conn, ids: list[int], action: str, expand: bool = True) -> dict:
    """反义批量：勾选=ids，作用域=同 (dimension,pattern) 分组全 pending（expand=True 时扩张）。

    action='approve' → 勾选 approved、未勾 rejected；
    action='reject'  → 勾选 rejected、未勾 approved。
    expand=False 时 ids 即完整作用域（调用方已给出全组）。
    """
    if not ids:
        return {"approved": 0, "rejected": 0}
    scope = list(ids)
    if expand:
        key_rows = conn.execute(
            f"SELECT dimension, pattern FROM rule_pending WHERE id IN ({','.join('?' * len(ids))})",
            ids).fetchall()
        for k in key_rows:
            extra = conn.execute(
                "SELECT id FROM rule_pending WHERE dimension=? AND pattern=? AND status='pending'",
                (k["dimension"], k["pattern"])).fetchall()
            scope = list(dict.fromkeys(scope + [r["id"] for r in extra]))
    checked = set(ids)
    approved_ids = [i for i in scope if (action == "approve") == (i in checked)]
    rejected_ids = [i for i in scope if (action == "approve") != (i in checked)]
    a = set_status(conn, approved_ids, "approved")
    r = set_status(conn, rejected_ids, "rejected")
    return {"approved": a, "rejected": r}


def pending_groups(dimension: str | None = None) -> list[dict]:
    """Tab2 词面聚合：status='pending' 按 (dimension, pattern)。"""
    from app.database import get_db
    sql = ("SELECT dimension, pattern, COUNT(DISTINCT clause_id) AS clause_count, "
           "ROUND(AVG(ai_confidence),3) AS avg_conf "
           "FROM rule_pending WHERE status='pending'")
    args = []
    if dimension:
        sql += " AND dimension=?"
        args.append(dimension)
    sql += " GROUP BY dimension, pattern ORDER BY dimension, clause_count DESC, pattern"
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        groups = []
        for r in rows:
            labs = conn.execute(
                "SELECT id, label, ai_confidence, COUNT(*) AS n FROM rule_pending "
                "WHERE dimension=? AND pattern=? AND status='pending' GROUP BY label ORDER BY n DESC",
                (r["dimension"], r["pattern"])).fetchall()
            groups.append({
                "dimension": r["dimension"], "pattern": r["pattern"],
                "clause_count": r["clause_count"], "avg_conf": r["avg_conf"],
                "labels": [dict(x) for x in labs],
            })
    return groups


def blacklist_rows() -> list[dict]:
    from app.database import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT dimension, pattern, label, COUNT(*) AS n, "
            "MAX(updated_at) AS last_rejected "
            "FROM rule_pending WHERE status='rejected' "
            "GROUP BY dimension, pattern, label ORDER BY last_rejected DESC").fetchall()
    return [dict(r) for r in rows]


def backfill_and_close(conn, dimension: str, pattern: str, label: str) -> int:
    """组合被人工批准：其全部 pending 来源条文写列 + 联动清 Tab1 review 项。

    返回写列条文数。queue 联动：该条文该维 status='review' 置 'done'。
    """
    col = _dim_column(dimension)
    rows = conn.execute(
        "SELECT DISTINCT clause_id FROM rule_pending "
        "WHERE dimension=? AND pattern=? AND label=? AND status='pending'",
        (dimension, pattern, label)).fetchall()
    cids = [r["clause_id"] for r in rows]
    if cids:
        ph = ','.join('?' * len(cids))
        conn.execute(f"UPDATE clauses SET {col}=?, ai_classified=1 WHERE id IN ({ph})",
                     [label, *cids])
        conn.execute(
            f"UPDATE classification_queue SET status='done' WHERE clause_id IN ({ph}) "
            f"AND dimension=? AND status='review'", [*cids, dimension])
    return len(cids)
```

> 注：`pending_groups` / `blacklist_rows` 自开连接（读），裁决类函数由调用方持写连接传入。

- [ ] **Step 5: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -v`
Expected: PASS（含 `pending_ids` 被测试用到，已在 Step1 中隐式引入——若缺该方法则在 Step4 文件补：见上方 `pending_ids` 已提供）

- [ ] **Step 6: Commit**

```bash
git add app/database.py app/classifier/rule_pending.py tests/test_rule_pending.py
git commit -m "feat: rule_pending 表 + 核心层（免审键三态/幂等插/反义批量/聚合/黑名单/回填）"
```

---

### Task 2: `apply_ai_results` 裁决流改造

**Files:**
- Modify: `app/classifier/batch_queue.py`
- Test: `tests/test_rule_pending.py`（追加裁决用例）
- 回归适配：`tests/test_batch_queue.py`、`tests/test_classifier_ai.py`、`tests/test_logs.py`

**Interfaces:**
- Consumes: `rule_pending.key_state / insert_pending`
- 语义（Global Constraint 2）：结果行每维度，对其 `extract_keywords(content, 3)` 逐词裁决：
  - 至少 1 词 approved → `auto_adopted`：写列 + 仅对 approved 词 `bump`（命中递增，不新建未背书规则）；
  - rejected 词跳过（不沉淀/不插）；
  - 首次词 → `insert_pending`。
  - 无任一 approved 词 → 该条 `status='review'`（不写列、不入 auto 计数）。
  - 纯打标特例：若内容提词为空（extract 无 ≥2 词）但 conf 高——维持原「写列」能力，允许 auto 写列且不沉淀词（词为空无夹带）。裁决时 approved 词集合为空但 kw 列表亦为空 → 直接 auto 写列。
- auto 汇总计数 = 实际 `auto_adopted` 行数（沿用现局部累计）。

- [ ] **Step 1: 追加失败测试** `tests/test_rule_pending.py`

```python
from app.classifier import batch_queue as bq


def _seed_spec_clause(conn):
    conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','x')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id,clause_no,content) "
                 "VALUES (?, '1.1', '含 钢筋 与 试验 的条文内容')", (sid,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _run_batch(monkeypatch, tmp_path, conf=0.9):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bX', status='ai_processing' "
                     "WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bX", [{"clause_id": cid, "label": "钢筋", "confidence": conf}])
    return cid


def test_first_time_word_goes_pending_not_auto(monkeypatch, tmp_path):
    """内容词首次（无规则 confirmed 无 pending approved）→ 该条 review 不写列，词入 pending。"""
    cid = _run_batch(monkeypatch, tmp_path)
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        p = conn.execute("SELECT COUNT(*) n FROM rule_pending WHERE status='pending'").fetchone()["n"]
    assert q["status"] == "review"
    assert (c["dim6_material"] or "") == ""
    assert p >= 1


def test_endorsed_word_autos_and_writes(monkeypatch, tmp_path):
    """同键规则 confirmed≥1 → auto_adopted 写列；首次词（试验）仍入池。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute("INSERT INTO classification_rules (dimension,pattern,label,threshold,confirmed,is_active)"
                     " VALUES ('dim6','钢筋','钢筋',0.6,1,1)")
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bY', status='ai_processing' WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bY", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        p = conn.execute("SELECT pattern FROM rule_pending WHERE status='pending'").fetchall()
    assert q["status"] == "auto_adopted"
    assert c["dim6_material"] == "钢筋"
    assert any(r["pattern"] != "钢筋" for r in p)  # 试验 入池，钢筋 已背书不插


def test_rejected_word_skipped_no_write(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "z")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "钢筋", "钢筋"), "rejected")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bZ', status='ai_processing' WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bZ", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
    assert q["status"] == "review"
    assert (c["dim6_material"] or "") == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "pending or endorsed or rejected_word" -v`
Expected: 新用例 FAIL（现状全 auto 写列）

- [ ] **Step 3: 实现** `app/classifier/batch_queue.py::apply_ai_results`

替换函数体为裁决流（保留 `_dim_to_column` / `_DIM_SUB_FIELD` / auto 日志汇总）：

```python
def apply_ai_results(batch_id: str, results: list[dict]):
    from app.params.registry import get_param_float
    from app.classifier.rule_pending import key_state, insert_pending
    conf_threshold = get_param_float("classify.ai_confidence_threshold")
    auto_count = 0
    with get_db() as conn:
        for r in results:
            q_row = conn.execute(
                "SELECT dimension FROM classification_queue WHERE clause_id = ? AND batch_id = ?",
                (r["clause_id"], batch_id)).fetchone()
            dim = q_row["dimension"] if q_row else ""
            adopted = False
            if r["confidence"] >= conf_threshold and dim:
                content_row = conn.execute(
                    "SELECT content FROM clauses WHERE id = ?", (r["clause_id"],)).fetchone()
                content = (content_row["content"] or "") if content_row else ""
                from app.classifier.feedback import extract_keywords
                kws = extract_keywords(content, top_n=3)
                approved_kws: list[str] = []
                for kw in kws:
                    st = key_state(conn, dim, kw, r["label"] or "")
                    if st == "approved":
                        approved_kws.append(kw)
                    elif st == "rejected":
                        continue                       # 黑名单：不沉淀不插
                    else:
                        insert_pending(conn, r["clause_id"], dim, kw,
                                       r["label"] or "", r["confidence"], batch_id)
                # 写列条件：≥1 已背书词，或条文无提词（无词即无夹带，允许高置信直接标）
                adopted = bool(approved_kws) or not kws
            status = "auto_adopted" if adopted else "review"
            if status == "auto_adopted":
                auto_count += 1
            conn.execute(
                """UPDATE classification_queue
                   SET ai_label = ?, ai_confidence = ?, status = ?
                   WHERE batch_id = ? AND clause_id = ?""",
                (r["label"], r["confidence"], status, batch_id, r["clause_id"]),
            )
            if status == "auto_adopted":
                col = _dim_to_column(dim)
                conn.execute(
                    f"UPDATE clauses SET {col} = ?, ai_classified = 1 WHERE id = ?",
                    (r["label"], r["clause_id"]),
                )
                # 沉淀只针对已背书词（命中递增；不新建未背书规则）
                if adopted:
                    content_row = conn.execute(
                        "SELECT content FROM clauses WHERE id = ?", (r["clause_id"],)).fetchone()
                    content = (content_row["content"] or "") if content_row else ""
                    from app.classifier.feedback import extract_keywords
                    from app.classifier.rule_sink import bump_rule
                    for kw in extract_keywords(content, top_n=3):
                        if key_state(conn, dim, kw, r["label"] or "") == "approved":
                            bump_rule(conn, dim, kw, _DIM_SUB_FIELD.get(dim, ""),
                                      is_confirmed=False, new_rule_active=True,
                                      label=r["label"])
    log_action("classify", "INFO", "AI分类结果入库",
               detail=json_detail({"batch_id": batch_id, "total": len(results),
                                   "auto_adopted": auto_count,
                                   "review": len(results) - auto_count}))
```

> **评审 D5 采纳（单遍收集）**：实现须**单遍** `extract_keywords(content, top_n=3)`，并逐词一次 `key_state` 得 `decisions: list[tuple[kw, state]]`（approved/rejected/首次），判定段与沉淀段**共用** `decisions`，禁止二次 extract 或重复 key_state。上述代码块按此精神改写为单遍实现。

- [ ] **Step 4: 回归适配既有测试**

预期破坏点（run `D:/Python/python.exe -m pytest tests/test_batch_queue.py tests/test_classifier_ai.py tests/test_logs.py -v` 定位）：
- `test_batch_queue` 原有「高置信 auto 写列+沉淀」用例：无 confirmed 规则 → 现在走 review。适配 = 在用例预置一条同键 `confirmed≥1` 规则（`classification_rules` INSERT），或改断言为 review+pending。优先预置规则保留「auto 仍可用」意图。
- `test_classifier_ai` 断言 candidate_labels 传给 backend：不受裁决流影响，若它断言 queue 终态 auto 则同样预置 confirmed 规则。
- `test_logs.py` auto 计数：现 auto 需背书词；对无规则空库的旧断言会变化——造真实规则使该批 auto 或改期望计数。
每处适配保持用例原意图（验证什么不变），改动最小并在 commit message 说明。

- [ ] **Step 5: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py tests/test_batch_queue.py tests/test_classifier_ai.py tests/test_logs.py -q`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add app/classifier/batch_queue.py tests/test_rule_pending.py \
        tests/test_batch_queue.py tests/test_classifier_ai.py tests/test_logs.py
git commit -m "feat: apply_ai_results 裁决流——首次词入池待审、背书词 auto、驳回跳过；回归适配"
```

---

### Task 3: `process_feedback` 写列 + 勾选背书（废除 top3 全 bump）

**Files:**
- Modify: `app/classifier/feedback.py`
- Test: `tests/test_rule_pending.py`（追加）+ `tests/test_rule_feedback_loop.py` 适配

**Interfaces:**
- Produces 签名变更：`process_feedback(clause_id, dimension, confirmed_label, source_conf=0.0, patterns: list[str] | None = None)`。`patterns` = 人工勾选要背书的词面（None/空 → 只写列不沉淀）。
- 语义（Constraint 3）：写列照旧；仅对 `patterns` 中词做 approve 背书：若该词该键无 approved（规则 confirmed≥1 或 pending approved），INSERT `rule_pending` approved 行（人工显式背书免先 pending）并 `bump_rule(is_confirmed=True, label=confirmed_label)`。已 rejected 的组合：人工显式批准 → 覆盖为 approved（insert pending approved / 若已有 rejected pending 行 → set approved + 更新）。不在 patterns 的词一律不沉淀。

- [ ] **Step 1: 追加失败测试** `tests/test_rule_pending.py`

```python
def test_confirm_writes_col_and_only_endorses_checked_words(monkeypatch, tmp_path):
    from app.classifier.feedback import process_feedback
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)   # content '钢筋 条文'
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95, patterns=["钢筋"])
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rule = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert n_rule == 1                       # 仅勾选词沉淀


def test_confirm_no_patterns_writes_only(monkeypatch, tmp_path):
    from app.classifier.feedback import process_feedback
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95)   # patterns=None → 纯打标
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rule = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "钢筋"
    assert n_rule == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k confirm -v`
Expected: 首例 FAIL（现状 patterns 被忽略、无规则仍 0）

- [ ] **Step 3: 实现** `app/classifier/feedback.py`

替换 `process_feedback`：

```python
def process_feedback(clause_id: int, dimension: str, confirmed_label: str,
                     source_conf: float = 0.0, patterns: list[str] | None = None):
    """人工确认：写列 + 只对勾选词面 patterns 背书（废除原 top3 全 bump 的隐式夹带）。

    patterns=None/空 → 纯打标（写列、不沉淀任何词）。patterns 中的词对
    (dimension, pattern, confirmed_label) 组合做 approved 背书并 bump(confirmed=1)。
    """
    from app.classifier.rule_pending import KEY_DIMS, insert_pending, key_state
    from app.classifier.rule_sink import bump_rule
    from app.params.registry import get_param_float
    patterns = [p for p in (patterns or []) if p and p.strip()]
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
        if dimension in KEY_DIMS:
            enable_conf = get_param_float("classify.rule_auto_enable_conf")
            sub_field = {"dim4": "specialty", "dim5": "location", "dim6": "material"}.get(dimension, "")
            for kw in patterns:
                # 人工显式背书：rejected 覆盖为 approved（rejected 行 set approved）；
                # 无行则直插 approved；已 approved 则幂等不动
                st = key_state(conn, dimension, kw, confirmed_label)
                if st == "rejected":
                    conn.execute(
                        "UPDATE rule_pending SET status='approved', "
                        "updated_at=datetime('now','localtime') "
                        "WHERE dimension=? AND pattern=? AND label=? AND status='rejected'",
                        (dimension, kw, confirmed_label))
                elif st == "none":
                    insert_pending(conn, clause_id, dimension, kw, confirmed_label,
                                   None, "manual-confirm")
                    conn.execute(
                        "UPDATE rule_pending SET status='approved', "
                        "updated_at=datetime('now','localtime') "
                        "WHERE clause_id=? AND dimension=? AND pattern=? AND label=?",
                        (clause_id, dimension, kw, confirmed_label))
                bump_rule(conn, dimension, kw, sub_field,
                          is_confirmed=True,
                          new_rule_active=(source_conf >= enable_conf),
                          label=confirmed_label)
```

> 占位 import 应删除（上例中 `app.termdict_noop` 不存在——以真实无依赖实现为准，勿引入不存在的模块）。同一 with 事务内完成；`bump_rule` 会新建规则（带 confirmed=1），此刻无 rule_pending 校验冲突（rule_pending 不拦 bump）。`key_state` 在 approved 分支幂等跳过重 bump？已 approved 词 bump 会再 hit++/confirmed++——人工重复确认场景少见，保留 bump（与旧行为一致）。

- [ ] **Step 4: 适配调用方 + 回归**

调用方（`app/routes/rules_routes.py` 的 confirm_review / batch_confirm）暂以默认 `patterns=None`（纯打标）调用——勾选词从 UI 传入在 Task 5 接上。跑 `tests/test_rule_feedback_loop.py` 与 `tests/test_review_batch.py` 定位破坏（原断言确认后生成规则数>0）→ 适配为「确认带 patterns=内容词」或改断言（按用例原意图保留「确认仍能沉淀」的路径：在用例显式传 patterns）。

- [ ] **Step 5: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py tests/test_rule_feedback_loop.py tests/test_review_batch.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/classifier/feedback.py app/routes/rules_routes.py \
        tests/test_rule_pending.py tests/test_rule_feedback_loop.py tests/test_review_batch.py
git commit -m "feat: process_feedback 写列+勾选背书（废除 top3 隐式全沉淀），人工显式批准覆盖 rejected"
```

### Task 4: 词面校核 & 黑名单管理端点（后端）

**Files:**
- Modify: `app/routes/rules_routes.py`（review 相关区追加端点）
- Test: `tests/test_rule_pending.py`（追加端点测试，auth_client）

**Interfaces:**
- Consumes: `rule_pending.pending_groups / blacklist_rows / decide_scope / backfill_and_close / set_status / pending_ids`
- Produces 端点（全部挂 `router`，复用现有 review 区）：
  - `GET /review/word-pending` → partial `review_word_panel.html`（Tab2 数据）
  - `POST /review/word-pending/decide` body `{"ids": [...], "action": "approve"|"reject"}`：action=approve → **先对勾选 id 行的 `(dim,pattern,label)` 逐键 `backfill_and_close`（此时仍 pending）**，再 `decide_scope(ids, "approve", expand=True)`；action=reject → 直接 `decide_scope(ids, "reject", expand=True)`（不写列）。返回更新后的 word-pending partial。
  - `GET /review/blacklist` → partial `review_blacklist.html`（Tab3）
  - `POST /review/blacklist/{pid}/restore`：该 rejected 键全部行 → `pending`（黑名单管理档1，恢复待审）
  - `POST /review/blacklist/{pid}/approve`：该 rejected 键全部行 → 先 `backfill_and_close`（键行内 clause 写列+queue done）再置 `approved`（档2，立即批准）
- 驳回语义确认：decide reject 不改任何 clauses 列（黑名单不写列）。

- [ ] **Step 1: 追加失败测试** `tests/test_rule_pending.py`

```python
def test_decide_approve_backfills_and_closes_queue(monkeypatch, tmp_path):
    """词面批准 → 关联条文列回填 + queue review→done + 未勾词驳回。"""
    from app.routes import rules_routes
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
        rp.insert_pending(conn, cid, "dim6", "钢筋", "混凝土", 0.7, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bA', status='review', "
                     "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid1 = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
        gid2 = conn.execute("SELECT id FROM rule_pending WHERE label='混凝土'").fetchone()["id"]
    # 只勾选 钢筋
    resp = client.post("/review/word-pending/decide",
                       json={"ids": [gid1], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["label"]: r["status"] for r in conn.execute(
            "SELECT label, status FROM rule_pending").fetchall()}
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert st["钢筋"] == "approved" and st["混凝土"] == "rejected"


def test_blacklist_restore_and_approve(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "试验", "钢筋", 0.9, "bB")
        pid = rp.pending_ids(conn, "dim6", "试验", "钢筋")[0]
        rp.set_status(conn, [pid], "rejected")
    assert client.post(f"/review/blacklist/{pid}/restore").status_code == 200
    with get_db() as conn:
        assert conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"] == "pending"
    rp_pid = pid
    with get_db() as conn:
        rp.set_status(conn, [rp_pid], "rejected")
    assert client.post(f"/review/blacklist/{rp_pid}/approve").status_code == 200
    with get_db() as conn:
        assert conn.execute("SELECT status FROM rule_pending WHERE id=?", (rp_pid,)).fetchone()["status"] == "approved"
        assert conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"] == "钢筋"
```

> `client`/`auth_client` fixture：本文件测试需登录态调路由 → 用 conftest `auth_client`（client 需先 `_db` 于 auth_client 同库——auth_client 已建独立 tmp 库并登录；测试内不再 `_db`，改直接 `with get_db()` 在同一 patch 库插种子）。Step 3 实现时会按此整理 fixture。

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "decide_approve or blacklist" -v`
Expected: FAIL（端点 404 / 无 partial）

- [ ] **Step 3: 实现端点**（`rules_routes.py`，追加于 review 区末尾；并在顶部引入 `rule_pending` helpers）

```python
def _clauses_kw_from(conn, queue_id):
    """队列项 → 候选词(extract) 供 Tab1 确认勾选（Task5 用，本 Task 先提供）。"""
    from app.classifier.feedback import extract_keywords
    row = conn.execute(
        "SELECT q.clause_id, c.content FROM classification_queue q "
        "JOIN clauses c ON c.id = q.clause_id WHERE q.id = ?", (queue_id,)).fetchone()
    return extract_keywords(row["content"] or "", top_n=3) if row else []


@router.get("/review/word-pending")
async def review_word_pending(request: Request, dimension: str = ""):
    from app.main import templates
    groups = rule_pending.pending_groups(dimension or None)
    return templates.TemplateResponse(request, "partials/review_word_panel.html", {
        "groups": groups, "dimension": dimension, "dim_labels": _DIM_LABELS})


@router.post("/review/word-pending/decide")
async def review_word_decide(request: Request, body: dict):
    from fastapi.responses import JSONResponse as _JR
    ids = body.get("ids") or []
    action = body.get("action")
    if action not in ("approve", "reject") or not isinstance(ids, list) or not ids:
        return _JR({"detail": "ids/action 不合法"}, status_code=400)
    with get_db() as conn:
        if action == "approve":
            keys = conn.execute(
                f"SELECT DISTINCT dimension, pattern, label FROM rule_pending "
                f"WHERE id IN ({','.join('?' * len(ids))}) AND status='pending'", ids).fetchall()
            for k in keys:
                rule_pending.backfill_and_close(conn, k["dimension"], k["pattern"], k["label"])
        stats = rule_pending.decide_scope(conn, ids, action, expand=True)
    log_action("review", "INFO", f"词面{'批准' if action=='approve' else '驳回'}",
               detail=json_detail({"ids": ids, **stats}),
               username=getattr(request.state, "username", ""))
    return await review_word_pending(request, dimension="")


@router.get("/review/blacklist")
async def review_blacklist(request: Request):
    from app.main import templates
    rows = rule_pending.blacklist_rows()
    return templates.TemplateResponse(request, "partials/review_blacklist.html",
                                      {"rows": rows, "dim_labels": _DIM_LABELS})


def _key_all_pending_ids(conn, pid: int, target_status: str) -> list[int]:
    row = conn.execute("SELECT dimension, pattern, label FROM rule_pending WHERE id=?", (pid,)).fetchone()
    if not row:
        return []
    rows = conn.execute(
        "SELECT id FROM rule_pending WHERE dimension=? AND pattern=? AND label=? AND status=?",
        (row["dimension"], row["pattern"], row["label"], target_status)).fetchall()
    return [r["id"] for r in rows]


@router.post("/review/blacklist/{pid}/restore")
async def review_blacklist_restore(request: Request, pid: int):
    with get_db() as conn:
        ids = _key_all_pending_ids(conn, pid, "rejected")
        n = rule_pending.set_status(conn, ids, "pending") if ids else 0
    log_action("review", "INFO", "黑名单恢复待审", detail=json_detail({"ids": ids, "n": n}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})


@router.post("/review/blacklist/{pid}/approve")
async def review_blacklist_approve(request: Request, pid: int):
    with get_db() as conn:
        row = conn.execute("SELECT dimension, pattern, label FROM rule_pending WHERE id=?", (pid,)).fetchone()
        if row:
            # 先对该键下所有 rejected 来源条文回填（按原 pending clause），再转 approved
            rule_pending.backfill_rejected_clauses(conn, row["dimension"], row["pattern"], row["label"])
            ids = _key_all_pending_ids(conn, pid, "rejected")
            n = rule_pending.set_status(conn, ids, "approved") if ids else 0
        else:
            n = 0
    log_action("review", "INFO", "黑名单批准", detail=json_detail({"pid": pid, "n": n}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})
```

> 需在 `rule_pending.py` 补 `backfill_rejected_clauses(conn, dimension, pattern, label)`：对 `status='rejected'` 且同键来源 clause 写列 + queue(review)→done（复用 Task1 的写列/close 逻辑，把查询 status 从 `'pending'` 换成参数化来源状态）。Task1 里实现 `backfill_and_close` 时把它做成内部 `_backfill_by_status(conn, dim, pattern, label, status)`，`backfill_and_close` 传 `'pending'`，本函数传 `'rejected'`。
> `_DIM_LABELS = {"dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}`（rules_routes 内若无则补常量）。

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "decide_approve or blacklist" -v`
Expected: PASS（若 fixture 冲突按 auth_client 同库整理）

- [ ] **Step 5: Commit**

```bash
git add app/classifier/rule_pending.py app/routes/rules_routes.py tests/test_rule_pending.py
git commit -m "feat: 词面校核/黑名单端点（批准回填+联动队列、恢复待审/批准两档）"
```

---

### Task 5: `/review` 三 Tab UI + Tab1 条文多标签（评审 D1/D4/D2 对齐版）

**Files:**
- Create: `app/templates/partials/review_tabs.html`、`review_clause_panel.html`（Tab1）、`review_word_panel.html`（Tab2）、`review_blacklist.html`（Tab3）
- Modify: `app/routes/rules_routes.py`（review_page 三 Tab 容器；Tab1 列表聚合端点 + 行内编辑/批量端点；confirm/batch 接收 patterns 兼容兜底）、`app/templates/partials/review_list.html`（Queue 兜底视图，供「无 pending 词的低置信条文」，复用原 review 逻辑）
- Test: `tests/test_rule_pending.py`（UI 接线 + auth 冒烟）

**Interfaces:**
- `review_page`：center_content = `partials/review_tabs.html`——三 Tab 按钮 + 三容器。Alpine `tab` 切换 + 各容器 `x-show`；进入时各自 `hx-get` 拉取。
- **Tab1 数据源 = `pending_clause_groups()`**（Global Constraint 9）：每行 = 一条条文 + 该维候选标签 chips（各含 pattern 源、默认全勾的 checkbox）+ 来源信息；行尾按钮「批量确认」「批量驳回」「编辑」。另把「有 queue review 但无 pending 词」的低置信条文（`review_list.html` 渲染）并排同一容器，标注「低置信（无候选词）」。
  - 行批量：作用域 = `clause_pending_ids(clause,dim)`，反义批量（D4），文案「未勾选标签将自动驳回/通过」；approve 前先对该行 approved 标签键 `backfill_and_close`（写列+清 queue review）。
- **行内「编辑」**（Global Constraint 10）：点击把该行候选标签区变成可编辑 chips + 新标签输入框 + 保存/取消。预填 = 该条文未驳回候选标签；保存语义：
  - 删除某预填标签 → 该 `(pattern,label)` 组合 reject（其 pending 行置 rejected）；
  - 保留的标签 → approve（等效行批量确认这些标签）；
  - 新增非空且非既有标签值 → 只写该维分类列该新值（**纯打标，不沉淀规则**；沉淀走 approved 通道）；
  - 取消 → 不改任何状态。
  - 复用规范管理页 clause 分类编辑的表单/控件外观（three-column dim 编辑样式），后端走 review 路由新端点（不串 spec_routes）。
- Tab2 `#word-pending-list`（词面聚合 `pending_groups`）、Tab3 `#blacklist-list`（`blacklist_rows`）同原计划；事件 `reviewWordPending/reviewBlacklist`。
- 端点增补（rules_routes）：
  - `GET /review/clause-pending` → Tab1 clause 面板 partial
  - `POST /review/clause-pending/{clause_id}/decide` body `{ids, action}`（行批量；作用域=clause_pending_ids）
  - `POST /review/clause-pending/{clause_id}/inline-edit` body `{label_ids: [...], removed_label_ids: [...], new_label: str|None}`（保存语义如上；`new_label` 仅写列不沉淀；实现可用更细粒度 body，行为须等价于本契约）
  - 原 `confirm_review` 保留 patterns 兼容（兜底视图用）；`reviewWord/Blacklist` 端点沿用 Task4。

- [ ] **Step 1: 追加失败测试**（Tab1 条文多标签 + inline）

```python
def test_tab1_clause_multi_label_approve_partial(monkeypatch, tmp_path, auth_client):
    """同条文两候选标签：只勾 A → A approved+写列+queue done，B rejected 不写列。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        for lab in ("钢筋", "混凝土"):
            rp.insert_pending(conn, cid, "dim6", "钢筋", lab, 0.9, "bT")
            bq.try_enqueue(conn, cid, "dim6", 0.0) or None
        ids = rp.clause_pending_ids(conn, cid, "dim6")
    # 作用域=ids；只保留 label=钢筋 的行作勾选
    keep = [i for i in ids if _pending_label(i) == "钢筋"]
    resp = auth_client.post("/review/clause-pending/1/decide",
                            json={"ids": keep, "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        st = {r["label"]: r["status"] for r in conn.execute(
            "SELECT label,status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert c["dim6_material"] == "钢筋"
    assert st["钢筋"] == "approved" and st["混凝土"] == "rejected"
    assert q["status"] == "done"


def test_tab1_inline_new_label_writes_col_only(monkeypatch, tmp_path, auth_client):
    """驳回后 inline 编辑输入新标签 → 只写列（纯打标），不沉淀任何规则（评审 F7 用户裁决）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)               # content 含 钢筋
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
        rp.set_status(conn, [pid], "rejected")      # 已驳 → 留在 Tab1
    resp = auth_client.post("/review/clause-pending/1/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "混凝土"
    assert n_rules == 0     # 纯打标不沉淀
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py -k "tab1 or inline" -v`
Expected: FAIL（端点 404 / clause_pending_ids 不存在）

- [ ] **Step 3: 实现**（`rules_routes.py` 端点 + `pending_clause_groups/clause_pending_ids` helpers（Task1 已列接口，此 Task 补实现）+ review_tabs/review_clause_panel 模板 + Alpine tab 切换 + reviewBatch 语义迁移到 clause 面板；原 `review_list.html` 保留为「无 pending 词低置信」兜底区块）。`clause_id` 参数用 path int；测试中 `_pending_label` 小工具按需定义于测试文件顶部。
- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_pending.py tests/test_review_batch.py tests/test_rules_routes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/rules_routes.py app/templates/partials/review_tabs.html \
        app/templates/partials/review_clause_panel.html \
        app/templates/partials/review_word_panel.html app/templates/partials/review_blacklist.html \
        app/classifier/rule_pending.py app/templates/partials/review_list.html tests/test_rule_pending.py
git commit -m "feat: /review 三 Tab + Tab1 条文多标签（inline 编辑删=驳/新标签纯打标、queue 兜底）"
```

---

### Task 6: 一致性回填 & 双入口状态 edge + 全量回归

**Files:** 主要测试补充 + 任何一致性修复

- [ ] **Step 1: 补一致性用例**（追加 `tests/test_rule_pending.py`）

```python
def test_word_approve_then_clause_queue_already_done_idempotent(monkeypatch, tmp_path):
    """词面批准后 Tab1 该条文已 done；再批（重复 approve）不重复写列/不炸。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bC")
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bD")
        pid = rp.pending_ids(conn, "dim6", "钢筋", "钢筋")[0]
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bC', status='review' WHERE clause_id=?", (cid,))
    with get_db() as conn:
        rule_pending.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
        # 剩余一行仍 pending（不同批）→ 二次批准走 decide
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert q["status"] == "done"
    assert c["dim6_material"] == "钢筋"
```

（校验词面批准幂等：写列 UPDATE 重复无副作用、queue done 幂等、后续新 pending 行仍可各自批准。）

> **评审 Tests 补测清单（追加 `tests/test_rule_pending.py`）**：
> 1. `test_lowconf_clause_without_pending_still_in_tab1` — 低置信、无 pending 词的 queue review 条文仍出现在 Tab1（queue 兜底），且 confirm 走纯打标（patterns 空）后列写入、无规则沉淀（回归既有低置信确认路径不破）。
> 2. `test_inline_cancel_noop` — inline-edit cancel（或仅读请求）不改变任何 pending/queue/列状态。
> 3. `test_clause_decide_idempotent` — 同 clause 同作用域重复 approve：第二次无新副作用（approved 键重复 backfill/queue done 幂等，pending 计数不变）。
> 4. `test_pending_clause_groups_joins_text` — `pending_clause_groups` 返回含 spec_code/clause_no/content 且按维度过滤正确。
> 5. `test_tab2_approve_then_tab1_gone` — Tab2 词面批准某组合后，Tab1 对应条文（该组合来源）消失（queue done + 回填）。
> 6. `test_reject_keeps_clause_in_tab1_marked` — 驳回后条文仍在 Tab1 且带「词已驳回」标记字段（D1）。

- [ ] **Step 2: 全量回归 + 修复**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: 全绿（含前几 Task 回归适配）。若有红：按 systematic-debugging 定位——重点怀疑 `test_import.py`（导入流程内 classify/AI 裁决依赖）与 `test_review_*`；修至绿灯后并入本 Task commit。预期新增用例 ~45、基线 679 → ~724 波动后收敛。

- [ ] **Step 3: headless 冒烟**（一次性脚本，跑完即删，不入库）

用 Playwright（channel=chrome）对临时库起服务：登录 → `/review` 三 Tab 切换 → 造一条 pending 词 → 词面确认 → 断言列回填与 queue done → 黑名单恢复/批准往返。脚本放 `.superpowers/` 或 `scripts/_tmp_smoke_rule_pending.py`，验证后删除。

- [ ] **Step 4: Commit**

```bash
git add tests/test_rule_pending.py   # + 修复过的文件
git commit -m "test: 词面批准幂等/双入口一致性 + 全量回归收敛"
```

---

## Self-Review 对照

- **Spec 覆盖**：§四 表（T1）、§五 裁决流（T2）、§七 process_feedback（T3）、§六 词面/黑名单端点（T4）、§六 UI 三 Tab（T5）、§八 一致性（T6）、§九 回归（T2/T3/T6 适配）、§十 已定衔接（T4 backfill_rejected / T6）。无缺项。
- **Placeholder**：无 TBD；Task4 模板结构描述给实现要点、Task5 给出文件清单与接口契约——UI 行布局细节按既有 review_list/Pico 风格实现，不引入第二套样式。
- **类型一致**：`rule_pending.key_state/insert_pending/set_status/decide_scope/pending_groups/blacklist_rows/backfill_and_close/pending_ids/_key_all_pending_ids/backfill_rejected_clauses` 命名全程一致；`process_feedback(..., patterns)` 签名统一；`HX-Trigger: reviewWordPending/reviewBlacklist` 事件链贯穿三 Tab。
- **Known tradeoff**：Task4 `backfill_rejected_clauses` 需把 Task1 的 backfill 泛化（`_backfill_by_status`）；Task3 approved 词 bump 会重复 confirmed++ 的场景已注明与旧行为一致。

## Execution Handoff

计划已保存至 `docs/superpowers/plans/2026-09-06-rule-pending-review.md`。两种执行方式：

**1. Subagent-Driven（推荐）**——每 Task 派发全新子代理 + 任务间评审
**2. Inline Execution**——当前会话内 executing-plans 分批执行 + 检查点

选哪种？

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ISSUES — all adjudicated | 8 findings (F1–F8), 6 mechanical ratified + 2 preference settled |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | Arch-1(驳回queue处置→inline编辑)、D1 模型对齐、D2 沉淀词、D4/D5、Code Quality 1(单遍提取)、Test 补 6 例 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CROSS-MODEL:** Eng review 与 codex 在核心机制一致；codex 补充了批准粒度(rowid→key)、reject 未勾回填、label NULL/停用规则背书、人工信号写回 confirmed、并发 UNIQUE、行内新标签夹带等 6+2 点，全部并入 plan（Global Constraint 9–11 与 Outside Voice 修订节）。
- **VERDICT:** ENG + OUTSIDE VOICE CLEARED — 计划已按评审修订（Task1/4/5 语义与建表、Task5 三 Tab inline、测试补 6）。可进入实现（Subagent-Driven 或 Inline）。

NO UNRESOLVED DECISIONS

