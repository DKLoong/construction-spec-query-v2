# 审核界面卡顿治理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/review` 审核界面「条文待审」「词面校核」两页的单次操作耗时从约 2.4–3.3 秒降到 100 ms 量级，并解决规范管理页「点查看条文后面板落在长列表下方、需手动滚动」的问题。

**Architecture:** 三层治理——① 数据库补三条缺失索引，消掉相关子查询的全表扫描与临时索引重建；② 两个待审列表加分页（默认 50 条 + 「加载更多」），把单次渲染的 HTML 从 10.9 MB 压到约 350 KB；③ 前端把「每个面板各自监听事件」改为常驻总控，隐藏面板收到刷新请求只置脏标记，切回时才拉（懒加载 + 可见性门控）。
规范页走另一条路（实测支撑）：50 本规范全量只有 1 077 个 DOM 节点 / 20 ms，远轻于用户已在用且体感流畅的 887 条条文框（11 972 节点 / 293 ms），故**不做分页**（分页会引入跨页漏选），改用限高滚动框 + 点击后自动滚到面板。

**Tech Stack:** Python 3.14 / FastAPI / SQLite / Jinja2 / HTMX 2.0.10 / Alpine 3 / pytest

**Spec:** `docs/superpowers/specs/2026-10-01-review-panel-performance-design.md`

## Global Constraints

- 所有回复、注释、提交信息用中文（zh-CN）。
- Python 统一用 `D:/Python/python.exe`（禁用 `python3`）。
- 执行测试前 `cd /d/CC-Workspace/construction-spec-query-v2`；带中文输出先 `export PYTHONUTF8=1`。
- 数据库操作必须参数化；外部输入（含 query 参数 `limit`）必须做类型与范围校验。
- 禁止裸 `except:`；保持既有错误处理风格。
- 每个 Task 结束跑**本次变更关联的增量测试** + `pyright`（不得新增 error），然后 `git commit`，信息格式 `type: 描述`。
- 静态资源版本号：Task 1–7 只改 Jinja 模板内联 JS，不涉及 `static/`，无需递增；**Task 8 会改 `static/app.css`，必须把 `base.html` 的 `app.css?v=31` 递增为 `?v=32`**——否则浏览器复用缓存旧 CSS，改动看不出效果。
- 性能改动必须用**实测**验证，禁止用推算数字报告结果（见项目记忆「验证方法的 12 个陷阱」）。
- **共享测试文件的追加约定**：Task 1–7 依次向同一个 `tests/test_review_pagination.py` 追加用例。追加前**先读该文件**；新增 import 一律并入文件**顶部**已有的 import 块（不要落在文件中部）；已存在的 helper（`_db` / `_seed_clause` / `_seed_review_clause`）**不得重复定义**——直接用。

---

## 文件结构

| 文件 | 职责 | 本计划中的改动 |
| --- | --- | --- |
| `app/database.py` | 表结构与索引 DDL、迁移 | 补 3 条索引；重建迁移同步补建 |
| `app/classifier/rule_pending.py` | 待审数据的唯一查询出口 | 提取 COUNT helper；groups 支持 limit；新增 total |
| `app/routes/rules_routes.py` | 审核相关 HTTP 端点 | 接 `limit` 参数、下发 `total` |
| `app/templates/partials/review_tabs.html` | 三 Tab 容器 | **改为刷新总控**（懒加载 + 可见性门控） |
| `app/templates/partials/review_clause_panel.html` | Tab1 面板 | 刷新调用换总控；加分页 UI |
| `app/templates/partials/review_word_panel.html` | Tab2 面板 | 同上 |
| `app/templates/partials/review_blacklist.html` | Tab3 面板 | 刷新调用换总控 |
| `app/templates/partials/review_list.html` | Tab1 低置信兜底块 | 刷新调用换总控 |
| `app/templates/partials/tree_panel.html` | 左侧树 + 宫格红点 | 红点刷新去重 |
| `app/templates/partials/specs_table.html` | 规范列表 | **滚动框布局** + 全选 tooltip + 点击后滚到面板 |
| `app/templates/partials/lexicon_row.html` | 词库行 | 去掉与局部替换重复的全表重拉 |
| `static/app.css` | 全局样式 | 新增 `.spec-table-wrapper`；**改后必须递增 `base.html` 的 `?v=31` → `?v=32`** |
| `tests/test_review_pagination.py` | 本计划新增用例 | 新建 |
| `tests/test_specs_scroll_panel.py` | 规范页滚动框用例 | 新建 |

**关键不变量（贯穿全部 Task）：** 现有 `pending_clause_groups(dimension)` / `pending_groups(dimension)` 的**无 limit 调用必须保持返回全量**——`tests/test_rule_pending.py` 与 `tests/test_review_clause_panel.py` 都依赖它。

---

### Task 1: 补三条数据库索引

**Files:**
- Modify: `app/database.py`（`SCHEMA_SQL` 的 `classification_queue` / `clauses` 建表段、`rule_pending` 索引段；`_migrate_rule_pending_clause_nullable`）
- Test: `tests/test_review_pagination.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: 索引 `idx_cq_clause_dim_status`、`idx_rp_clause_dim_status`、`idx_clauses_spec_id`（前两条为 Task 2 的性能前提；第三条服务于规范列表的 `clauses.spec_id` JOIN）

**背景：** `classification_queue` 表当前**没有任何索引**（`PRAGMA index_list` 返回空），`rule_pending` 的四个索引也全部以 `(dimension, pattern, …)` 或 `(status)` 打头。这导致 Tab1 主查询里 `EXISTS (SELECT 1 FROM classification_queue q WHERE q.clause_id=… AND q.status='review')` 对 4 138 个 pending 行逐行全表扫 4 625 行，实测 375 ms。

`clauses` 表同样**没有任何索引**，而规范列表要 `LEFT JOIN clauses c ON c.spec_id = s.id`。目前全靠 SQLite 的 `AUTOMATIC COVERING INDEX` 临时兜底（`EXPLAIN QUERY PLAN` 可见 `SEARCH c USING AUTOMATIC COVERING INDEX (spec_id=?) LEFT-JOIN`）——该临时索引**每次查询都要重建**，成本随 `clauses` 行数线性累加。加手工索引实测 3.5 ms → 0.3 ms。

- [ ] **Step 1: 找到 `classification_queue` 建表语句的位置**

```bash
grep -n "classification_queue" app/database.py | head
```

预期看到 `CREATE TABLE IF NOT EXISTS classification_queue (...)` 及其后续几行（`rule_pending` 的建表紧跟其后，其索引 DDL 在第 172–179 行附近）。

- [ ] **Step 2: 写失败测试**

新建 `tests/test_review_pagination.py`：

```python
"""审核界面性能治理：索引存在性 + 分页契约。

背景：`classification_queue` 此前零索引，相关子查询退化为全表扫描，
Tab1 主查询实测 375 ms（生产库 4 138 pending / 4 625 queue 行）。
"""
from app.database import init_db, get_db


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()


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
```

- [ ] **Step 3: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v
```

预期：三条 FAIL（`AssertionError`，索引不在列表中）。

- [ ] **Step 4: 在 `SCHEMA_SQL` 里补索引**

在 `app/database.py` 的 `CREATE TABLE IF NOT EXISTS classification_queue (...)` 语句**之后**、`rule_pending` 建表之前插入：

```sql
-- 查询侧索引：Tab1 主表 / 兜底段的 EXISTS 子查询按 (clause_id, dimension, status)
-- 逐行探测；无此索引时对 4138 个 pending 行全表扫本表（实测 375 ms → 19 ms）。
CREATE INDEX IF NOT EXISTS idx_cq_clause_dim_status
    ON classification_queue(clause_id, dimension, status);
```

在 `rule_pending` 现有四个索引 DDL（`idx_rule_pending_rule_key` 之后）追加：

```sql
-- 查询侧索引：pending_counts 全驳段 / 兜底段的 NOT EXISTS 子查询按 clause_id 探测
-- rule_pending；现有四个索引均以 (dimension, pattern…) 或 (status) 打头，帮不上
-- （实测该段 280 ms → 0.3 ms）。
CREATE INDEX IF NOT EXISTS idx_rp_clause_dim_status
    ON rule_pending(clause_id, dimension, status);
```

再在 `CREATE TABLE IF NOT EXISTS clauses (...)` 语句**之后**插入：

```sql
-- 规范列表 LEFT JOIN / 条文列表 WHERE spec_id 都要按 spec_id 定位条文。
-- 无此索引时 SQLite 靠 AUTOMATIC COVERING INDEX 临时兜底，每次查询重建（实测
-- /specs/list 3.5 ms → 0.3 ms）。clauses 的其它列（parent_clause 等）不在本计划范围。
CREATE INDEX IF NOT EXISTS idx_clauses_spec_id ON clauses(spec_id);
```

- [ ] **Step 5: 同步补进重建迁移**

`_migrate_rule_pending_clause_nullable()` 会 `DROP` 四个索引 → `RENAME` 旧表 → 重建 → 重挂索引。若不补，走该迁移路径的老库会**永久丢失新索引**。

在该函数的 `for idx in (...)` 解包处加入新索引名：

```python
    for idx in ("idx_rule_pending_key", "idx_rule_pending_status",
                "idx_rule_pending_uniq", "idx_rule_pending_rule_key",
                "idx_rp_clause_dim_status"):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
```

并在该函数末尾（`idx_rule_pending_rule_key` 的 `CREATE UNIQUE INDEX` 之后）追加：

```python
    conn.execute("CREATE INDEX idx_rp_clause_dim_status "
                 "ON rule_pending(clause_id, dimension, status)")
```

- [ ] **Step 6: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v
```

预期：3 passed。

- [ ] **Step 7: 跑关联回归**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_database.py tests/test_rule_pending.py -v
```

预期：全绿（索引是纯增量 DDL，不应影响任何行为）。

- [ ] **Step 8: pyright 与提交**

```bash
D:/Python/python.exe -m pyright app/database.py
git add app/database.py tests/test_review_pagination.py
git commit -m "perf: 补 classification_queue / rule_pending / clauses 查询侧索引"
```

---

### Task 2: `rule_pending` 分页支持（groups + total）

**Files:**
- Modify: `app/classifier/rule_pending.py`
- Test: `tests/test_review_pagination.py`

**Interfaces:**
- Consumes: Task 1 的索引（性能前提）
- Produces:
  - `pending_clause_groups(dimension: str | None = None, limit: int | None = None) -> list[dict]`
  - `rejected_clause_groups(dimension: str | None = None, limit: int | None = None) -> list[dict]`
  - `pending_groups(dimension: str | None = None, limit: int | None = None) -> list[dict]`
  - `clause_group_total(dimension: str | None = None) -> int`（主表段 + 全驳段）
  - `word_group_total(dimension: str | None = None) -> int`（Tab2 词面组数）
  - `limit=None` 语义 = 不分页返回全量（向后兼容）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_review_pagination.py`：

```python
from app.classifier import rule_pending as rp
from app.classifier import batch_queue as bq


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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v -k "limit or total"
```

预期：FAIL，`TypeError: pending_clause_groups() got an unexpected keyword argument 'limit'` 及 `AttributeError: module ... has no attribute 'clause_group_total'`。

- [ ] **Step 3: 提取 COUNT helper 并新增 total 函数**

在 `app/classifier/rule_pending.py` 中，`pending_counts()` **之前**插入：

```python
def _count_clause_main(conn, dimension: str | None = None) -> int:
    """Tab1 主表段组数（谓词与 pending_clause_groups 逐字同口径）。"""
    sql = ("SELECT COUNT(*) FROM ("
           "  SELECT 1 FROM rule_pending rp "
           "  JOIN clauses c ON c.id = rp.clause_id "
           "  JOIN specifications s ON s.id = c.spec_id "
           "  WHERE rp.status='pending' "
           "  AND EXISTS ("
           "    SELECT 1 FROM classification_queue q "
           "    WHERE q.clause_id = rp.clause_id AND q.dimension = rp.dimension "
           "    AND q.status = 'review')")
    args: list = []
    if dimension:
        sql += " AND rp.dimension=?"
        args.append(dimension)
    sql += " GROUP BY rp.clause_id, rp.dimension)"
    return conn.execute(sql, args).fetchone()[0]


def _count_clause_rejected(conn, dimension: str | None = None) -> int:
    """Tab1 全驳段组数（谓词与 rejected_clause_groups 逐字同口径）。"""
    sql = ("SELECT COUNT(*) FROM ("
           "  SELECT 1 FROM rule_pending rp "
           "  JOIN clauses c ON c.id = rp.clause_id "
           "  JOIN specifications s ON s.id = c.spec_id "
           "  WHERE rp.status = 'rejected' "
           "  AND NOT EXISTS ("
           "    SELECT 1 FROM rule_pending p2 WHERE p2.clause_id = rp.clause_id "
           "      AND p2.dimension = rp.dimension AND p2.status = 'pending') "
           "  AND EXISTS ("
           "    SELECT 1 FROM classification_queue q WHERE q.clause_id = rp.clause_id "
           "      AND q.dimension = rp.dimension AND q.status = 'review')")
    args: list = []
    if dimension:
        sql += " AND rp.dimension = ?"
        args.append(dimension)
    sql += " GROUP BY rp.clause_id, rp.dimension)"
    return conn.execute(sql, args).fetchone()[0]


def clause_group_total(dimension: str | None = None) -> int:
    """Tab1 待审组数（主表段 + 全驳段），供分页显示「共 N 条」。

    刻意只做纯 COUNT，不物化 group 对象——后者会带来逐组查候选词的 N+1。
    """
    from app.database import get_db
    with get_db() as conn:
        return _count_clause_main(conn, dimension) + _count_clause_rejected(conn, dimension)


def word_group_total(dimension: str | None = None) -> int:
    """Tab2 待校核词面组数，供分页显示「共 N 条」。"""
    from app.database import get_db
    sql = ("SELECT COUNT(*) FROM (SELECT 1 FROM rule_pending WHERE status='pending'")
    args: list = []
    if dimension:
        sql += " AND dimension=?"
        args.append(dimension)
    sql += " GROUP BY dimension, pattern)"
    with get_db() as conn:
        return conn.execute(sql, args).fetchone()[0]
```

- [ ] **Step 4: 让 `pending_counts()` 复用 helper（消除重复 SQL）**

把 `pending_counts()` 中 `n_clause` 的两段内联 SQL 替换为 helper 调用，**保留第三段（兜底段）原样**：

```python
    with get_db() as conn:
        # 主表段 / 全驳段：与 pending_clause_groups、rejected_clause_groups 同谓词，
        # 由 clause_group_total 的两个 helper 提供（原为内联 SQL，分页需求出现后抽公共）。
        n_clause = _count_clause_main(conn) + _count_clause_rejected(conn)
        # 兜底段：低置信 queue review 且无 pending/rejected 关联
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

同时把该函数 docstring 中「谓词与三个 group 函数逐字同口径（绝不调用它们）」一句改为：

```
    已确认(done)条文的残留 pending 词不计 clause。此处刻意只做单连接纯 COUNT，
    谓词与三个 group 函数逐字同口径（绝不调用 **group 物化函数**；只复用其 COUNT
    helper）。红点端点 /review/pending-count 被前端事件驱动 + 5 分钟兜底刷新，
    物化 group 对象会带来逐组查候选词的 N+1（项目规则 1.3）。
```

- [ ] **Step 5: 给三个 group 函数加 limit**

`pending_groups()`：把 SQL 组装与循环改为

```python
    sql = ("SELECT dimension, pattern, COUNT(DISTINCT clause_id) AS clause_count, "
           "ROUND(AVG(ai_confidence), 3) AS avg_conf "
           "FROM rule_pending WHERE status='pending'")
    args = []
    if dimension:
        sql += " AND dimension=?"
        args.append(dimension)
    sql += " GROUP BY dimension, pattern ORDER BY dimension, clause_count DESC, pattern"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
```
（函数签名同步改为 `def pending_groups(dimension: str | None = None, limit: int | None = None) -> list[dict]:`；其余循环体不变——被 LIMIT 截断的组不会进入循环，N+1 次数随之下降。）

`pending_clause_groups()` 与 `rejected_clause_groups()` 做同样处理：在 `sql += " GROUP BY ... ORDER BY ..."` 之后追加

```python
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
```

并同步函数签名为 `(dimension: str | None = None, limit: int | None = None) -> list[dict]`。

- [ ] **Step 6: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py tests/test_rule_pending.py -v
```

预期：全绿。

- [ ] **Step 7: 用生产库副本实测加速比（必须在第 3 节记录真实数字）**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe - <<'PY'
import time, sys, shutil, tempfile, pathlib
sys.path.insert(0, ".")
import app.database as db
copy = pathlib.Path(tempfile.gettempdir()) / "perf_plan.db"
shutil.copy("data/spec_query.db", copy)
db.DATABASE_PATH = str(copy)
from app.classifier import rule_pending as rp
for label, fn in [
    ("pending_clause_groups(limit=None)", lambda: rp.pending_clause_groups(None)),
    ("pending_clause_groups(limit=50)  ", lambda: rp.pending_clause_groups(None, 50)),
    ("clause_group_total()             ", lambda: rp.clause_group_total(None)),
]:
    ts = []
    for _ in range(3):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    print(f"  {label} {min(ts)*1000:8.1f} ms")
copy.unlink(missing_ok=True)
PY
```

把实测值填入本计划末尾的「实测记录」小节。

- [ ] **Step 8: pyright 与提交**

```bash
D:/Python/python.exe -m pyright app/classifier/rule_pending.py
git add app/classifier/rule_pending.py tests/test_review_pagination.py
git commit -m "perf: 待审列表查询支持 limit 并提供分组总数"
```

---

### Task 3: 路由接 limit 参数并下发 total

**Files:**
- Modify: `app/routes/rules_routes.py`（`review_word_pending`、`review_clause_pending`）
- Test: `tests/test_review_pagination.py`

**Interfaces:**
- Consumes: Task 2 的 `pending_clause_groups(dimension, limit)`、`pending_groups(dimension, limit)`、`clause_group_total(dimension)`、`word_group_total(dimension)`
- Produces: HTTP 契约
  - `GET /review/clause-pending?limit=50` → 模板上下文新增 `limit`(int)、`total`(int)
  - `GET /review/word-pending?limit=50` → 同上
  - `limit` 允许范围 `1..500`，非法值返回 422（由 FastAPI Query 校验）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_review_pagination.py`：

```python
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
```

> 注：`auth_client` fixture 已在 `tests/conftest.py` 提供，会 monkeypatch `DATABASE_PATH` 到 tmp_path 并 `init_db()`，与 `_db()` 互斥，**不要在同一用例里混用**。

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v -k "limit"
```

预期：FAIL——`?limit=2` 仍渲染全部卡片；`共 N 条` 文案不存在；`limit=0` 返回 200 而非 422。

- [ ] **Step 3: 改 `review_word_pending`**

```python
@router.get("/review/word-pending")
async def review_word_pending(
    request: Request,
    dimension: str = "",
    limit: int = Query(50, ge=1, le=500),
):
    """词面校核聚合（Tab2 数据源，供 Task5 UI 接）。

    limit 为**外部输入**（项目规则 1.1）：由 Query(ge/le) 收口范围，
    越界或非整数由 FastAPI 直接 422，不进业务逻辑。
    """
    from app.main import templates
    groups = rule_pending.pending_groups(dimension or None, limit)
    total = rule_pending.word_group_total(dimension or None)
    return templates.TemplateResponse(request, "partials/review_word_panel.html", {
        "groups": groups, "total": total, "limit": limit,
        "dimension": dimension, "dim_labels": dim_labels})
```

在该文件顶部的 fastapi 导入中追加 `Query`：

```python
from fastapi import APIRouter, Request, Form, Query
```

> ⚠ 现有导入是 `from fastapi import APIRouter, Request, Form`——**必须保留 `Form`**，
> 只在末尾追加 `Query`。写成 `APIRouter, Query, Request` 会把 `Form` 删掉，
> 导致同文件其它端点（如 `/lexicon/create` 式的 `Form(...)` 参数）在导入期直接报错。

- [ ] **Step 4: 改 `review_clause_pending`**

```python
@router.get("/review/clause-pending")
async def review_clause_pending(
    request: Request,
    dimension: str = "",
    limit: int = Query(50, ge=1, le=500),
):
    """Tab1 条文待审：pending_clause_groups（条文多标签）+ 全标签已驳条文（D1）+ 低置信 queue 兜底。

    limit 只约束**主表两段**（pending + 全驳）的组数；低置信兜底块维持自身 LIMIT 50 不变。
    total 与 limit 无关，供前端显示「已显示 X / 共 N 条」。
    """
    from app.main import templates
    groups = rule_pending.pending_clause_groups(dimension or None, limit)
    with get_db() as conn:
        # 单条聚合取全部 rejected 标签，按 (clause_id, dimension) 分组，避免逐条文查询（N+1）
        rej_rows = conn.execute(
            "SELECT clause_id, dimension, label FROM rule_pending "
            "WHERE status='rejected'").fetchall()
        rej_map: dict[tuple[int, str], list[str]] = {}
        for r in rej_rows:
            rej_map.setdefault((r["clause_id"], r["dimension"]), []).append(r["label"])
        for g in groups:
            g["rejected_labels"] = rej_map.get((g["clause_id"], g["dimension"]), [])
        items = _fetch_review_items(conn)
    # D1：候选全 reject（无 pending）的条文并入主表（带「词已驳回」标记 + 行内编辑），不进兜底块
    # 剩余额度给全驳段，合并后最多 limit 条
    rejected = rule_pending.rejected_clause_groups(dimension or None, limit)
    groups += rejected[:max(0, limit - len(groups))]
    total = rule_pending.clause_group_total(dimension or None)
    return templates.TemplateResponse(request, "partials/review_clause_panel.html", {
        "groups": groups, "items": items, "total": total, "limit": limit,
        "dimension": dimension, "dim_labels": dim_labels,
    })
```

- [ ] **Step 5: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v
```

预期：全绿。

- [ ] **Step 6: 跑关联回归**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_rule_pending.py tests/test_review_clause_panel.py -v
```

预期：全绿。若 `test_review_clause_panel.py` 因其 seed 数据只有 1 条而通过，属正常；该文件断言的是**契约与文案**，本 Task 不动它们。

- [ ] **Step 7: pyright 与提交**

```bash
D:/Python/python.exe -m pyright app/routes/rules_routes.py
git add app/routes/rules_routes.py tests/test_review_pagination.py
git commit -m "perf: 待审端点接受 limit 参数并下发总数"
```

---

### Task 4: 前端刷新改为常驻总控（懒加载 + 可见性门控）

**Files:**
- Modify: `app/templates/partials/review_tabs.html`（重写）
- Modify: `app/templates/partials/review_clause_panel.html`（刷新调用换总控）
- Modify: `app/templates/partials/review_word_panel.html`（同上）
- Modify: `app/templates/partials/review_blacklist.html`（同上）
- Modify: `app/templates/partials/review_list.html`（同上）
- Test: `tests/test_review_pagination.py`

**Interfaces:**
- Consumes: Task 3 的端点。
- Produces: 四个全局函数，供各面板内联 JS 调用
  - `reviewLoad(which)` —— 强制重拉某面板（`which ∈ {'clause','word','blacklist'}`）
  - `reviewEnsure(which)` —— 未加载过才拉（懒加载）
  - `reviewRefresh(list)` —— 操作后的刷新请求；可见的立即拉，隐藏的置为「待重载」
  - `reviewLoadMore(which, nextLimit)` —— 提高 limit 后重拉
  - 状态：`window.reviewTab`、`window.reviewPaneLimit`、`window.reviewLoaded`

**背景：** 现存机制是每个面板各自 `hx-trigger="load, reviewXxx from:body"`，且 Tab1 监听全部三个事件。实测在 Tab2 点一次「批准」，隐藏的 Tab1 会被完整重拉（3.3 s）并重建 55 521 个 DOM 节点。本 Task 把刷新决策权上收到常驻 script，按 `window.reviewTab` 判断可见性。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_review_pagination.py`：

```python
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
    """低置信兜底块的操作回调同样走总控。"""
    html = auth_client.get("/review/list").text
    assert "reviewRefresh(" in html
    assert "dispatchEvent" not in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v -k "controller or raw_events or fallback"
```

预期：FAIL（总控函数不存在；旧 dispatch 仍在）。

- [ ] **Step 3: 重写 `review_tabs.html`**

```html
<!-- /review 三 Tab 容器（条文待审 / 词面校核 / 黑名单） -->
<div x-data="{ tab: 'clause' }" id="review-tabs"
     x-init="$watch('tab', v => reviewSwitchTab(v))">
    <div role="tablist"
         style="display:flex;gap:0.5rem;margin-bottom:0.75rem;border-bottom:1px solid var(--pico-muted-border-color, #ddd);padding-bottom:0.5rem">
        <button type="button" @click="tab='clause'"
                :class="tab==='clause' ? '' : 'outline'"
                :aria-selected="tab==='clause' ? 'true' : 'false'"
                style="font-size:0.85rem">📄 条文待审</button>
        <button type="button" @click="tab='word'"
                :class="tab==='word' ? '' : 'outline'"
                :aria-selected="tab==='word' ? 'true' : 'false'"
                style="font-size:0.85rem">🔤 词面校核</button>
        <button type="button" @click="tab='blacklist'"
                :class="tab==='blacklist' ? '' : 'outline'"
                :aria-selected="tab==='blacklist' ? 'true' : 'false'"
                style="font-size:0.85rem">🚫 黑名单管理</button>
    </div>

    <div x-show="tab==='clause'" id="clause-pending-list">
        <p style="color:var(--pico-muted-color);padding:1rem">加载中...</p>
    </div>

    <div x-show="tab==='word'" id="word-pending-list">
        <p style="color:var(--pico-muted-color);padding:1rem">加载中...</p>
    </div>

    <div x-show="tab==='blacklist'" id="blacklist-list">
        <p style="color:var(--pico-muted-color);padding:1rem">加载中...</p>
    </div>
</div>

<script>
/* 审核三面板的加载与刷新总控。
   ⚠ 必须常驻在 review_tabs.html（不随面板 swap 消失）：面板自身的 <script> 每次被
   HTMX 替换都会重新定义，无法承载跨面板状态（已加载标记、当前 Tab）。
   背景：此前每个面板各自 hx-trigger="load, reviewXxx from:body"，且条文待审面板监听
   全部三个事件——实测在「词面校核」点一次批准会连带重拉隐藏的条文待审全量
   （服务端 3.3 s + 重建 55 521 个 DOM 节点）。 */
(function () {
    var PANES = {
        clause:    { url: '/review/clause-pending', el: 'clause-pending-list', paged: true },
        word:      { url: '/review/word-pending',   el: 'word-pending-list',   paged: true },
        blacklist: { url: '/review/blacklist',      el: 'blacklist-list',      paged: false }
    };
    // 面板名 → 旧 DOM 事件名。宫格红点（tree_panel.html）监听这三个事件，
    // 面板刷新虽已改由本总控接管，事件派发仍须保留，否则红点只剩 5 分钟兜底轮询。
    var PANE_EVENT = {
        clause: 'reviewClausePending',
        word: 'reviewWordPending',
        blacklist: 'reviewBlacklist'
    };
    var PAGE_SIZE = 50;

    window.reviewTab = 'clause';
    window.reviewPaneLimit = { clause: PAGE_SIZE, word: PAGE_SIZE };
    window.reviewLoaded = { clause: false, word: false, blacklist: false };

    window.reviewLoad = function (which) {
        var cfg = PANES[which];
        if (!cfg) return;
        var url = cfg.url;
        if (cfg.paged) url += '?limit=' + window.reviewPaneLimit[which];
        window.reviewLoaded[which] = true;
        htmx.ajax('GET', url, { target: '#' + cfg.el, swap: 'innerHTML' });
    };

    /* 懒加载：首次进入（或脏标记置位后切回）才真正请求 */
    window.reviewEnsure = function (which) {
        if (!window.reviewLoaded[which]) window.reviewLoad(which);
    };

    /* 操作后的刷新请求：可见的立即重拉；隐藏的**只置脏**，等切回该 Tab 时再拉。
       这是消除「改 Tab2 连带重拉 Tab1」的关键——语义上 Tab1 确实需要更新
       （Tab2 批准会回填条文），但没必要在用户看不见它的时候付这份代价。
       ⚠ 面板刷新改由本函数接管后，仍须**照旧派发 DOM 事件**：宫格红点靠它即时刷新，
       摘掉会让红点退化成 5 分钟才更新一次（Task 6 会给该监听加同轮去重）。 */
    window.reviewRefresh = function (list) {
        list.forEach(function (which) {
            if (window.reviewTab === which) window.reviewLoad(which);
            else window.reviewLoaded[which] = false;
        });
        list.forEach(function (which) {
            document.body.dispatchEvent(new CustomEvent(PANE_EVENT[which]));
        });
    };

    window.reviewSwitchTab = function (t) {
        window.reviewTab = t;
        window.reviewEnsure(t);
    };

    window.reviewLoadMore = function (which, nextLimit) {
        window.reviewPaneLimit[which] = nextLimit;
        window.reviewLoad(which);
    };

    window.reviewEnsure('clause');
})();
</script>
```

- [ ] **Step 4: 改 `review_clause_panel.html` 的刷新调用**

删除 `refreshReviewPanels` 整个函数（第 89–93 行），并把三个回调改为：

```javascript
function clauseConfirm(clauseId, dimension) {
    // ...（守卫与 confirm 保持不变）
    fetch('/review/clause-pending/' + clauseId + '/decide', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ dimension: dimension, ids: rejected, action: 'approve' }),
    }).then(function (resp) {
        if (resp.ok) window.reviewRefresh(['clause', 'word', 'blacklist']);
        else resp.text().then(function (t) { alert('操作失败：' + t); });
    }).catch(function (e) { alert('操作失败：' + e.message); });
}

function cancelClauseEdit(clauseId, dimension) {
    // 取消（查看态/编辑态共用）：仅重拉本面板（读请求，无后端状态变更），
    // 恢复初始全勾 chips、退出编辑态；本次去勾改动一并丢弃。
    // 此处必须用 reviewLoad（强制重拉）而非 reviewEnsure——面板已加载时
    // ensure 不会发请求，编辑态就退不出去。
    window.reviewLoad('clause');
}

function saveClauseEdit(clauseId, dimension) {
    // ...（取值与 fetch 保持不变）
    }).then(function (resp) {
        if (resp.ok) window.reviewRefresh(['clause', 'word']);
        else resp.text().then(function (t) { alert('保存失败：' + t); });
    }).catch(function (e) { alert('保存失败：' + e.message); });
}
```

> `saveClauseEdit` 原本经 `refreshReviewPanels()` 刷三个面板；改为两个是**收紧**：该操作只写分类列与词状态，不产生黑名单条目（未勾选的词才进黑名单，那是 `clauseConfirm` 的产物）。

- [ ] **Step 5: 改 `review_word_panel.html`**

```javascript
    function wordDecide(rowId, action) {
        var ids = Array.from(document.querySelectorAll('#' + rowId + ' .word-check:checked'))
            .map(function (c) { return parseInt(c.value, 10); });
        if (!ids.length) { alert('请至少勾选一个标签'); return; }
        var verb = action === 'approve' ? '批准' : '驳回';
        var anti = action === 'approve' ? '驳回' : '通过';
        if (!confirm(verb + '所选标签？未勾选标签将自动' + anti + '。')) return;
        fetch('/review/word-pending/decide', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ids: ids, action: action }),
        }).then(function (resp) {
            if (resp.ok) {
                window.reviewRefresh(['clause', 'word', 'blacklist']);
            } else {
                resp.text().then(function (t) { alert('操作失败：' + t); });
            }
        }).catch(function (e) { alert('操作失败：' + e.message); });
    }
```

- [ ] **Step 6: 改 `review_blacklist.html`**

```javascript
    function blacklistAction(pid, action) {
        var verb = action === 'restore' ? '恢复待审' : '批准';
        if (!confirm(verb + '该黑名单组合？')) return;
        fetch('/review/blacklist/' + pid + '/' + action, { method: 'POST' }).then(function (resp) {
            if (resp.ok) window.reviewRefresh(['clause', 'word', 'blacklist']);
        }).catch(function (e) { alert('操作失败：' + e.message); });
    }
```

- [ ] **Step 7: 改 `review_list.html`**

`confirmFallback` 与 `rejectFallback` 的第 43、49 行改为：

```javascript
            if (resp.ok) { window.reviewRefresh(['clause']); }
```

- [ ] **Step 8: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py tests/test_review_clause_panel.py -v
```

预期：全绿。`test_review_clause_panel.py` 断言的是确认/编辑契约，本 Task 未改其模板结构，应保持通过。

- [ ] **Step 9: 浏览器实测门控生效（关键验证，不许跳过）**

按项目 CLAUDE.md §三 启动隔离实例（副本库 + **独立端口 8011**），用 Playwright 复现本计划 Spec 的 2.5 节场景，确认 Tab2 操作**不再**发出 `/review/clause-pending` 请求：

```bash
export PYTHONUTF8=1 && D:/Python/python.exe - <<'PY'
import sys, time
sys.path.insert(0, ".")
from app.auth import create_access_token
from playwright.sync_api import sync_playwright

token = create_access_token({"sub": "admin"})
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context()
    ctx.add_cookies([{"name": "access_token", "value": token,
                      "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.accept())
    # 拦掉写请求：前端照常走刷新分支，但不改库
    pg.route("**/review/word-pending/decide", lambda r: r.fulfill(status=200, body=""))
    reqs = []
    pg.on("request", lambda r: reqs.append(r.url.split("127.0.0.1:8011")[-1]))

    pg.goto("http://127.0.0.1:8011/review", wait_until="load", timeout=180000)
    pg.wait_for_timeout(6000)
    print("首屏请求:", [u for u in reqs if "review" in u])

    pg.click("button:has-text('词面校核')")
    pg.wait_for_timeout(3000)
    reqs.clear()
    pg.click("text=✅ 批准")
    pg.wait_for_timeout(8000)
    hits = [u for u in reqs if "review" in u]
    print("Tab2 批准后的请求:", hits)
    assert not any("clause-pending" in u for u in hits), \
        "❌ 隐藏的条文待审面板仍被重拉（可见性门控未生效）"
    print("✅ 门控生效：Tab1 未被连带刷新")
    ctx.close(); b.close()
PY
```

同时复测首屏：`nodes = pg.evaluate('document.getElementsByTagName("*").length')` 应显著低于 22 613+T。

- [ ] **Step 10: pyright 与提交**

```bash
git add app/templates/partials/review_tabs.html app/templates/partials/review_clause_panel.html \
        app/templates/partials/review_word_panel.html app/templates/partials/review_blacklist.html \
        app/templates/partials/review_list.html tests/test_review_pagination.py
git commit -m "perf: 审核三面板改为懒加载 + 可见性门控刷新"
```

---

### Task 5: Tab1 / Tab2 分页 UI

**Files:**
- Modify: `app/templates/partials/review_clause_panel.html`
- Modify: `app/templates/partials/review_word_panel.html`
- Test: `tests/test_review_pagination.py`

**Interfaces:**
- Consumes: Task 3 的 `limit`/`total` 模板变量；Task 4 的 `window.reviewLoadMore(which, nextLimit)`
- Produces: 「加载更多」按钮 + 「已显示 X / 共 N 条」计数

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_review_pagination.py`：

```python
def test_clause_panel_renders_load_more_when_truncated(auth_client):
    """有更多时渲染「加载更多」，并把下一页 limit 传给总控。"""
    with get_db() as conn:
        for i in range(55):
            _seed_review_clause(conn, f"GB{i}")
    html = auth_client.get("/review/clause-pending?limit=50").text
    assert "加载更多" in html
    assert "reviewLoadMore('clause', 100)" in html
    assert "已显示 50 / 共 55 条" in html


def test_clause_panel_hides_load_more_when_complete(auth_client):
    """已全部显示时不渲染「加载更多」。"""
    with get_db() as conn:
        _seed_review_clause(conn)
    html = auth_client.get("/review/clause-pending?limit=50").text
    assert "加载更多" not in html
    assert "已显示 1 / 共 1 条" in html


def test_word_panel_renders_load_more(auth_client):
    """Tab2 同样有分页控件。"""
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        for word in ["钢筋", "混凝土", "模板"]:
            rp.insert_pending(conn, c1, "dim6", word, word, 0.9, "b1")
    html = auth_client.get("/review/word-pending?limit=2").text
    assert "加载更多" in html
    assert "reviewLoadMore('word', 52)" in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v -k "load_more"
```

预期：FAIL（文案与按钮不存在）。

- [ ] **Step 3: 在 `review_clause_panel.html` 主表块末尾加分页条**

把模板中 `{% endif %}`（`clause-groups` 块闭合）之前的结尾改为：

```html
        {% endfor %}
    </div>

    {% set shown = groups|length %}
    <div style="display:flex;gap:0.6rem;align-items:center;justify-content:center;padding:0.6rem 0">
        <small style="color:var(--pico-muted-color)">已显示 {{ shown }} / 共 {{ total }} 条</small>
        {% if shown < total %}
        <button type="button" class="outline"
                style="margin:0;font-size:0.8rem;padding:0.25rem 0.7rem"
                onclick="reviewLoadMore('clause', {{ limit + 50 }})">加载更多</button>
        {% endif %}
    </div>
    {% endif %}
```

> 位置说明：该分页条必须放在 `{% if groups %}` 块**内部**、`{% endif %}` 之前——没有待审项时不该出现「共 0 条 + 加载更多」。

- [ ] **Step 4: 在 `review_word_panel.html` 加分页条**

在表格 `</table>` 之后、`{% else %}` 之前插入：

```html
    {% set shown = groups|length %}
    <div style="display:flex;gap:0.6rem;align-items:center;justify-content:center;padding:0.6rem 0">
        <small style="color:var(--pico-muted-color)">已显示 {{ shown }} / 共 {{ total }} 条</small>
        {% if shown < total %}
        <button type="button" class="outline"
                style="margin:0;font-size:0.8rem;padding:0.25rem 0.7rem"
                onclick="reviewLoadMore('word', {{ limit + 50 }})">加载更多</button>
        {% endif %}
    </div>
```

- [ ] **Step 5: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v
```

预期：全绿。

- [ ] **Step 6: 浏览器实测分页交互**

在隔离实例（端口 8011）上验证：首屏渲染条数、点「加载更多」后条数与请求 URL 的 `limit` 参数、审核一条后列表位置不回到第一页。

- [ ] **Step 7: 提交**

```bash
git add app/templates/partials/review_clause_panel.html app/templates/partials/review_word_panel.html \
        tests/test_review_pagination.py
git commit -m "perf: 条文待审/词面校核列表加分页与加载更多"
```

---

### Task 6: 宫格红点刷新去重

**Files:**
- Modify: `app/templates/partials/tree_panel.html`
- Test: `tests/test_review_pagination.py`

**Interfaces:**
- Consumes: 无
- Produces: 无（红点外部行为不变：仍随三个事件刷新、仍有 5 分钟兜底）

**背景：** `tree_panel.html` 把三个事件都绑到同一个 `refresh`，而 `reviewRefresh` 的调用方大多一次传三个面板——同一轮里 `refresh` 被同步调用 3 次，每次一条 769 ms（补索引前）的 SQL。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_review_pagination.py`：

```python
def test_badge_refresh_is_coalesced(auth_client):
    """宫格红点：同一轮触发的多个事件必须合并为一次 refresh，不得各刷一遍。"""
    html = auth_client.get("/review").text
    assert "scheduleBadgeRefresh" in html, "红点应经去重调度器刷新"
    assert "setTimeout" in html, "去重应基于宏任务合并（同一轮的多个事件同步触发）"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v -k badge
```

预期：FAIL。

- [ ] **Step 3: 加去重调度器**

在 `tree_panel.html` 的 `refresh()` 定义之后、`refresh();` 调用之前插入：

```javascript
        // 事件去重：一次审核操作会同步派发多个事件（如 reviewRefresh 传三个面板），
        // 若每个事件各刷一遍，同一条 SQL 会被跑 3 次（实测 3 × 769 ms）。
        // 用 setTimeout(0) 把同一轮宏任务里的多次触发合并成一次。
        var badgeTimer = null;
        function scheduleBadgeRefresh() {
            if (badgeTimer) return;
            badgeTimer = setTimeout(function () { badgeTimer = null; refresh(); }, 0);
        }
```

并把事件监听改为：

```javascript
        ['reviewClausePending', 'reviewWordPending', 'reviewBlacklist'].forEach(function (ev) {
            document.body.addEventListener(ev, scheduleBadgeRefresh);
        });
```

- [ ] **Step 4: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v
```

预期：全绿。

- [ ] **Step 5: pyright 与提交**

```bash
git add app/templates/partials/tree_panel.html tests/test_review_pagination.py
git commit -m "perf: 宫格审核红点刷新去重，一轮操作只查一次计数"
```

---

### Task 7: 词库列表去掉重复的全表重拉

**Files:**
- Modify: `app/templates/partials/lexicon_row.html`
- Test: `tests/test_review_pagination.py`

**Interfaces:**
- Consumes: 无
- Produces: 无（`lexiconUpdated` 事件本身保留——新增/导入/编辑等端点仍经 `HX-Trigger` 发它）

**背景：** `lexicon_row.html` 的「启用/禁用」按钮同时做了两件事——局部替换该行（`hx-target="closest tr"`，返回值 `lexicon_row.html` 已含最新 `is_active`），**又** dispatch `lexiconUpdated`，而 `#lexicon-table`（`lexicon.html:115-116`）正是监听它整表重拉的。结果局部替换刚完成就被整表重拉覆盖。实测词库 6000 条时单次重拉 497 ms / 4.8 MB，纯属白费。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_review_pagination.py`：

```python
def test_lexicon_toggle_does_not_reload_whole_table(auth_client):
    """启停按钮靠局部替换 tr 即可，不得再触发整表重拉。

    lexicon_row.html 是独立模板，直接读文件断言比走 HTTP 更直接
    （该行由多个端点共用，且需 kind/columns 上下文）。
    """
    from pathlib import Path
    src = Path("app/templates/partials/lexicon_row.html").read_text(encoding="utf-8")
    assert "hx-target=\"closest tr\"" in src, "启停应局部替换该行"
    assert "lexiconUpdated" not in src, \
        "启停不得再 dispatch lexiconUpdated（会整表重拉，覆盖刚完成的局部替换）"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v -k lexicon_toggle
```

预期：FAIL（`lexiconUpdated` 仍在源码中）。

- [ ] **Step 3: 删掉多余的 dispatch**

把 `app/templates/partials/lexicon_row.html` 的启停按钮改为：

```html
        <button class="outline" style="font-size:0.75rem;padding:0.1rem 0.4rem"
                hx-post="/lexicon/{{ row.id }}/toggle"
                hx-target="closest tr" hx-swap="outerHTML">{{ '禁用' if row.is_active else '启用' }}</button>
```

（即：补上原先缺失的 `hx-target`/`hx-swap`，删掉 `hx-on::after-request` 那行。原按钮只挂了 dispatch、没有 target——所以之前是「整表重拉」在真正干活，局部替换并未生效。）

> ⚠ 执行时先确认原按钮到底有没有 `hx-target`：若已有 `hx-target="closest tr"`，则只删 `hx-on::after-request` 一行；若没有，则按上面补全两个属性 + 删 dispatch。**两种情况下 `lexiconUpdated` 都必须从本文件消失。**

- [ ] **Step 4: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py -v
```

预期：全绿。

- [ ] **Step 5: 浏览器实测**

在隔离实例上：打开词库页 → 点某行「禁用」→ 断言只发出 `POST /lexicon/{id}/toggle`，**没有**跟随的 `GET /lexicon/list`，且该行文案变为「启用」、其余行不变。

- [ ] **Step 6: 提交**

```bash
git add app/templates/partials/lexicon_row.html tests/test_review_pagination.py
git commit -m "perf: 词库启停改为纯行级替换，去掉整表重拉"
```

---

### Task 8: 规范页改为独立滚动框

**Files:**
- Modify: `app/templates/partials/specs_table.html`
- Modify: `static/app.css`（新增 `.spec-table-wrapper`）
- Modify: `app/templates/base.html`（`app.css?v=31` → `?v=32`）
- Test: `tests/test_specs_scroll_panel.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: CSS 类 `.spec-table-wrapper`

**背景（实测数据，见 Spec 附录）：**

| 场景 | HTML | DOM 节点 | DOMContentLoaded |
| --- | --- | --- | --- |
| 条文框 887 条（**已在用、体感流畅**） | 2 362.8 KB | 11 972 | 293 ms |
| 规范列表 50 本 | 115.5 KB | 1 077 | **20 ms** |
| 规范列表 500 本 | 1 133.1 KB | 10 527 | 232 ms |

规范列表 50 本只有 1 077 个节点 / 20 ms，比已在用的条文框轻 11 倍；即使 500 本也仍**低于**条文框。且规范列表的三个行操作（查看条文 `/specs/{id}/clauses`、分类 `/specs/{id}/edit-class`、删除）**全部是局部替换，没有任何路径重拉整表**——「渲染一次 + 原生滚动」的模式成立，这正是条文框不卡的机制。

**因此不做分页**：分页会引入跨页选择状态（用户明确指出「按页选择意义极小、容易漏选」），代价高于收益。

**顺带解决的问题：** 当前「查看条文」「分类」填充的 `#clause-detail-area` / `#spec-class-area` 挂在列表**之后**（`specs_list.html:8-9`），规范一多就需手动滚到底。列表限高后，两个面板紧贴框下方，一屏可见。

**实测尺寸：** 表头 72.5 px、每行 74.1 px → 容纳 5 本 = **443 px ≈ 27.8 rem**。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_specs_scroll_panel.py`：

```python
"""规范列表滚动框（替代分页）：布局契约 + 全选语义。

背景：规范数增长后，全量列表会把「查看条文」/「分类」面板推到屏幕外，
需手动滚动。实测 50 本仅 1 077 个 DOM 节点 / 20 ms（远轻于已在用的
887 条条文框），故用限高滚动框而非分页——分页会引入跨页选择漏选问题。
"""
from pathlib import Path

TEMPLATE = "app/templates/partials/specs_table.html"
CSS = "static/app.css"


def test_spec_table_is_wrapped_in_scroll_container():
    """表格必须被 .spec-table-wrapper 包裹，否则长列表会推走下方面板。"""
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    assert 'class="spec-table-wrapper"' in src, "缺少滚动容器包裹"


def test_scroll_container_height_fits_five_specs():
    """滚动框高度按「表头 + 5 行」定（实测 443px ≈ 27.8rem）。"""
    css = Path(CSS).read_text(encoding="utf-8")
    assert ".spec-table-wrapper" in css
    block = css.split(".spec-table-wrapper", 1)[1].split("}", 1)[0]
    assert "max-height" in block, "滚动框必须有 max-height"
    assert "27.8rem" in block, "高度应为容纳 5 本规范的 27.8rem"


def test_select_all_declares_whole_table_scope():
    """全选是全表语义，且必须显式告知——列表在滚动框内时「可见」≠「全部」。"""
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    assert "勾选范围为全表" in src, "全选框需要 tooltip 说明作用范围"


def test_panel_areas_follow_the_spec_table_host():
    """两个面板区域必须排在列表宿主之后（限高后它们才落在一屏内）。

    ⚠ 这两个 div 定义在 `specs_list.html`，**不在** `specs_table.html`——
    后者是 `/specs/list` 返回的片段（只有表格），面板宿主在页面骨架里。
    """
    src = Path("app/templates/partials/specs_list.html").read_text(encoding="utf-8")
    host_at = src.index('id="specs-table"')
    assert src.index('id="spec-class-area"') > host_at
    assert src.index('id="clause-detail-area"') > host_at
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_specs_scroll_panel.py -v
```

预期：4 FAIL（容器类名、CSS 块、tooltip 文案都不存在）。

- [ ] **Step 3: 加 CSS**

在 `static/app.css` 的 `.clause-table-wrapper` 规则**之后**插入：

```css
/* 规范列表滚动框：固定容纳「表头 + 5 本规范」，避免长列表把
   #spec-class-area / #clause-detail-area 推到屏幕外。
   27.8rem 的来历：实测表头 72.5px + 每行 74.1px × 5 = 443px ≈ 27.8rem。
   用限高滚动而非分页——分页会让「全选 + 批量重分类」出现跨页漏选；
   实测 50 本全量仅 1077 个 DOM 节点 / 20ms，远轻于已在用的 887 条条文框。 */
.spec-table-wrapper {
    max-height: 27.8rem;
    overflow: auto;
    border: 1px solid var(--pico-muted-border-color);
    border-radius: 4px;
}
.spec-table-wrapper table.striped {
    margin-bottom: 0;
}
/* 滚动时表头保持可见 */
.spec-table-wrapper thead th {
    position: sticky;
    top: 0;
    background: var(--pico-background-color, #fff);
    z-index: 1;
}
```

- [ ] **Step 4: 递增静态资源版本号**

`app/templates/base.html` 第 9 行：

```html
    <link rel="stylesheet" href="/static/app.css?v=32">
```

（`?v=31` → `?v=32`；不改这个，浏览器会用缓存的旧 CSS，改动看不出效果。）

- [ ] **Step 5: 包裹表格 + 全选 tooltip**

在 `app/templates/partials/specs_table.html` 中：

① 全选框加 `title`（第 6–7 行）：

```html
            <input type="checkbox" id="spec-select-all" style="width:0.9rem;height:0.9rem;margin:0;flex:none"
                   title="勾选范围为全表（含需滚动才能看到的规范）"
                   aria-label="全选全部规范：勾选范围为全表"
                   onchange="toggleSpecAll(this)"> 全选
```

② 用滚动容器包住表格——在 `<table class="striped" ...>` 之前插入 `<div class="spec-table-wrapper">`，在对应的 `</table>` 之后插入 `</div>`：

```html
    <div class="spec-table-wrapper">
    <table class="striped" style="font-size:0.85rem">
        ...
    </table>
    </div>
```

> 注意：**工具栏（全选 + 批量重分类）必须在滚动框之外**——它在 `{% if specs %}` 开头、`<table>` 之前，保持原位不动。

- [ ] **Step 6: 点击后面板自动滚入视野**

`#spec-class-area` / `#clause-detail-area` 由 htmx 填充（`hx-target` 在 `specs_table.html` 的按钮上）。在 `specs_table.html` 末尾的 `<script>` 块中追加：

```javascript
    // 面板（查看条文/分类）排在列表滚动框之后，规范多时可能在视口外。
    // 填充完成后自动滚入视野，避免用户以为「点了没反应」。
    document.body.addEventListener('htmx:afterSettle', function (evt) {
        var t = evt.detail && evt.detail.target;
        if (!t) return;
        if (t.id === 'spec-class-area' || t.id === 'clause-detail-area') {
            if (t.innerHTML.trim()) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
```

> 用 `htmx:afterSettle` 而非 `htmx:afterSwap`：内容已插入并稳定后再滚动，避免滚到半渲染的位置。`smooth` 让视线能跟上。

- [ ] **Step 7: 跑测试确认通过**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_specs_scroll_panel.py -v
```

预期：4 passed。

- [ ] **Step 8: 浏览器实测（关键，不许跳过）**

在隔离实例（副本库 + 端口 8011）上：

1. 造到 50 本规范，**强刷**（`Ctrl+F5`，因为改了 CSS）确认样式生效
2. 量滚动框高度 ≈ 443 px，且**恰好显示 5 行**（第 6 行需滚动）
3. 点第一行的「查看条文」→ 断言面板出现在视口内（`getBoundingClientRect().top < window.innerHeight`）
4. 点最后一行的「查看条文」→ 断言面板**仍在视口内**（这正是本次要解决的问题）
5. 勾「全选」→ 断言 `.spec-check:checked` 数量 = 全部规范数（不只可见的 5 本）
6. 硬刷后确认 `app.css?v=32` 生效（Network 面板无 304 复用旧版）

- [ ] **Step 9: 提交**

```bash
git add app/templates/partials/specs_table.html static/app.css app/templates/base.html \
        tests/test_specs_scroll_panel.py
git commit -m "perf: 规范列表改为限高滚动框并自动滚动到面板"
```

---

### Task 9: 端到端复测与收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-10-01-review-panel-performance-design.md`（把「预期效果」替换为实测值）
- Test: 全套增量

**Interfaces:**
- Consumes: Task 1–6 的全部产出
- Produces: 实测记录（本计划末尾小节）+ 应用本计划 Spec 的验证结论

- [ ] **Step 1: 在隔离实例上复测 Spec 第 2 节的每一项指标**

按项目 CLAUDE.md §三 的进程规范启动隔离实例（副本库 + 端口 8011），逐项实测：

| 指标 | 采集方式 |
| --- | --- |
| `/review/clause-pending?limit=50` 服务端耗时与 HTML 体积 | `curl -w "%{time_total} %{size_download}"`（需带登录 cookie） |
| `/review/word-pending?limit=50` 同上 | 同上 |
| `/review/pending-count` 耗时 | 同上 |
| 首屏 DOM 节点数 | Playwright `document.getElementsByTagName('*').length` |
| 首屏 DOMContentLoaded | `performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd` |
| Tab1 确认一条后的请求序列 | Playwright `request` 事件 |
| Tab2 批准一条后的请求序列 | 同上（断言无 `clause-pending`） |

- [ ] **Step 2: 把实测值写回 Spec**

编辑 `docs/superpowers/specs/2026-10-01-review-panel-performance-design.md` 的「五、预期效果」小节，在表格右侧新增「实测」列，填入 Step 1 采集的真实数字。**禁止用推算值代替实测值。**

- [ ] **Step 3: 跑全部关联测试**

```bash
export PYTHONUTF8=1 && D:/Python/python.exe -m pytest tests/test_review_pagination.py tests/test_rule_pending.py tests/test_review_clause_panel.py tests/test_database.py -v
```

预期：全绿。

- [ ] **Step 4: 清理临时文件**

```bash
rm -f "$TEMP/perf_plan.db" "$TEMP/perf3.db" /tmp/perf_tab*.html /tmp/tab1*.html
```

并确认 `git status` 中没有本计划之外的意外改动。

- [ ] **Step 5: 提交**

```bash
git add docs/superpowers/specs/2026-10-01-review-panel-performance-design.md
git commit -m "docs: 回填审核界面性能治理的实测数据"
```

---

## 实测记录

> 执行时用真实测量值填写，不要留空、不要写推算值。

### Task 2 Step 7

| 调用 | 实测耗时 |
| --- | --- |
| `pending_clause_groups(None)`（全量） | 待填 |
| `pending_clause_groups(None, 50)` | 待填 |
| `clause_group_total(None)` | 待填 |

### Task 4 Step 9

| 场景 | 请求序列 | 结论 |
| --- | --- | --- |
| 首屏 `/review` | 待填 | 只拉 Tab1 |
| Tab2 点「批准」后 | 待填 | 不含 `clause-pending` |

### Task 9 Step 1

| 指标 | 治理前（Spec 第 2 节） | 治理后实测 |
| --- | --- | --- |
| Tab1 服务端耗时 | 1 452 ms | 待填 |
| Tab1 HTML 体积 | 10.9 MB | 待填 |
| Tab2 服务端耗时 | 41 ms | 待填 |
| `pending-count` 耗时 | 769 ms | 待填 |
| 首屏 DOM 节点数 | 81 780（三面板合计） | 待填 |
| Tab1 确认一条的总耗时 | 约 2.4 s | 待填 |
| Tab2 批准一条的总耗时 | 约 3.3 s | 待填 |

### Task 7 Step 5（词库）

| 场景 | 请求序列 | 结论 |
| --- | --- | --- |
| 词库点「禁用」 | 待填 | 只有 `POST /lexicon/{id}/toggle`，无 `GET /lexicon/list` |

### Task 8 Step 8（规范页）

| 指标 | 目标 | 实测 |
| --- | --- | --- |
| 滚动框高度 | ≈ 443 px（5 行） | 待填 |
| 50 本规范 DOM 节点 | ≈ 1 077 | 待填 |
| 点最后一行「查看条文」后面板是否在视口内 | 是 | 待填 |
| 全选勾选数 = 全部规范数 | 是 | 待填 |

---

## 自检清单（写计划时已核对）

- **Spec 覆盖**：Spec 第 4.1 节的 L1→Task 1；L2→Task 2/3/5；L3→Task 4；L4→Task 4；L5→Task 6。本轮追加范围：规范页滚动框→Task 8；词库重复刷新→Task 7；`clauses(spec_id)` 索引→并入 Task 1。验证→Task 9。
- **明确不做**：规则页/词库页分页记入 `TODOS.md`（触发阈值：规则 > 800 条、词库 > 3000 条），本计划不含。
- **无占位符**：全部代码块为可执行内容；「待填」仅出现在实测记录表，且明确要求执行时采集。
- **类型一致**：`limit` 在后端全程 `int | None`（None=全量），在 HTTP 层为 `int`（1..500，默认 50）；`total` 全程 `int`；`reviewRefresh` 接收面板名列表，与 `PANES` 的键一致（`clause`/`word`/`blacklist`）。
