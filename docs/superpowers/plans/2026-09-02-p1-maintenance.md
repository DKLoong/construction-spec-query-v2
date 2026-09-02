# P1 知识库维护工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供「🛠 维护」界面（`/maintenance`），实现知识库健康检查（6 项 + 修复 + 重建向量索引）、导出/备份（SQLite 备份/规则导出/复核队列导出）、FTS5 optimize。

**Architecture:** 健康检查逻辑抽为纯函数模块 `app/maintenance/health_check.py`（各检查项独立函数、返回统一 dict 结构），由 `maintenance_routes.py` 路由包装成 HTTP 端点；`log_action` 工具先建最小版供健康检查写 `system_logs`；导出用附件下载 JSON / `VACUUM INTO`；`/maintenance` 页面三 Tab 用 Alpine 切换。VectorStore 新增只读 `get_orphans()` 与 `index_missing()`（复用 `sync_with_db` 的比对逻辑，检查与修复分离）。

**Tech Stack:** FastAPI / SQLite / LanceDB / HTMX + Alpine.js / Pico.css。测试用 pytest + `auth_client` fixture。

**Spec:** `docs/superpowers/specs/2026-08-27-maintenance-and-lifecycle-design.md`（§5 维护工具 + §3.3 health_check_snapshots + D10/D11/D12）

## Global Constraints

- Python 一律用 `D:/Python/python.exe`（**禁用 python3**）；测试命令 `D:/Python/python.exe -m pytest <file> -v`
- 提交规范：`feat:`/`fix:`/`test:` 前缀，单 task 原子提交；**只 add 实际改动文件**（工作区有无关未提交改动，严禁 `git add .`）
- 数据库操作全部参数化（`?`）；代码注释用中文；不做 P1 无关重构（P2/P3 不在本计划）
- 静态文件/模板改动后 base.html 版本号递增；数据变更后调 `clear_search_cache()`
- 测试用现有 `auth_client` fixture（临时库 + admin/test123 登录）；健康检查/导出为后端逻辑，前端只做模板渲染断言

---

### Task 1: log_action 日志工具（最小版）

**Files:**
- Create: `app/logging_util.py`
- Test: `tests/test_logging_util.py`

**Interfaces:**
- Produces:
  - `app.logging_util.log_action(category: str, level: str, action: str, detail: str | None = None, username: str | None = None, duration_ms: int | None = None) -> None` — 写 `system_logs`（表已由 P0 schema 迁移建好）。category 取值参考：`maintenance`/`spec`/`import` 等。异常静默（不打断主流程，记 warning 日志）
- Consumes: `app.database.get_db`；`system_logs` 表

- [ ] **Step 1: 写失败测试**

Create `tests/test_logging_util.py`:

```python
"""log_action 日志工具测试"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "log.db"))
    init_db()


def test_log_action_writes_system_logs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.logging_util import log_action
    log_action("maintenance", "INFO", "健康检查", detail='{"checks":1}', username="admin")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM system_logs WHERE action = '健康检查'"
        ).fetchone()
    assert row is not None
    assert row["category"] == "maintenance"
    assert row["level"] == "INFO"
    assert row["detail"] == '{"checks":1}'
    assert row["username"] == "admin"


def test_log_action_level_warn(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.logging_util import log_action
    log_action("spec", "WARN", "孤立条文", detail="2")
    with get_db() as conn:
        row = conn.execute(
            "SELECT level FROM system_logs WHERE action = '孤立条文'"
        ).fetchone()
    assert row["level"] == "WARN"
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_logging_util.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.logging_util'`

- [ ] **Step 3: 实现 log_action**

Create `app/logging_util.py`:

```python
"""结构化行为日志工具（写 system_logs 表）

P1 先建最小版供维护工具（健康检查/导出）记录；P3 日志管理将扩展为全量
埋点工具（导入/删除/规则/复核等），本模块签名保持稳定。
"""
import logging

logger = logging.getLogger(__name__)


def log_action(category: str, level: str, action: str,
               detail: str | None = None, username: str | None = None,
               duration_ms: int | None = None) -> None:
    """写入一条 system_logs 记录（表不存在/失败时静默降级，不打断主流程）"""
    try:
        from app.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO system_logs (category, level, action, detail, username, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (category, level, action, detail, username, duration_ms),
            )
    except Exception:
        logger.warning("system_logs 写入失败 (category=%s, action=%s)", category, action)
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_logging_util.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/logging_util.py tests/test_logging_util.py
git commit -m "feat: log_action 结构化日志工具（写 system_logs，供维护工具使用）"
```

---

### Task 2: 健康检查逻辑模块 health_check.py

**Files:**
- Create: `app/maintenance/__init__.py`
- Create: `app/maintenance/health_check.py`
- Modify: `app/search/vector_search.py`（新增 `get_orphans()`、`index_missing()`，重构 `sync_with_db` 复用）
- Test: `tests/test_health_check.py`

**Interfaces:**
- Consumes: Task 1 的 `log_action`；`specifications`/`clauses`/`clauses_fts`/`classification_queue` 表；`VectorStore`
- Produces:
  - `app.maintenance.health_check.run_health_check() -> dict` — 返回 `{"checks": [{key,label,count,severity,fixable}...]}`；无问题项 severity='ok'
  - `app.maintenance.health_check.fix_issue(key: str) -> dict` — 单项修复，返回 `{key, fixed: bool, detail: str}`
  - `app.maintenance.health_check.fix_all() -> list[dict]` — 遍历可修复项逐一修复
  - `VectorStore.get_orphans() -> list[int]` — 向量表有而 SQLite 无的 clause_id（只读，不删）
  - `VectorStore.index_missing() -> int` — SQLite 有而向量表无的条文补索引（返回补数）
  - 检查项 key：`orphan_parent` / `empty_content` / `bad_classification` / `vector_orphan` / `vector_missing` / `fts_mismatch`

- [ ] **Step 1: 写失败测试**

Create `tests/test_health_check.py`（构造脏数据验证检查与修复）:

```python
"""健康检查逻辑测试（检查项判定 + 修复动作）"""
import json
from app.database import init_db, get_db
from app.search.tokenize import build_search_text


def _setup_db(monkeypatch, tmp_path):
    db_path = tmp_path / "hc.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?,?,?)",
            ("GB 50010", "混凝土规范", "现行"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 正常条文（带分类）
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, dim4_specialty,
               dim5_location, dim6_material, ai_classified, search_text) VALUES (?,?,?,?,?,?,?,?,?)""",
            (spec_id, "5.1.1", "正常", "正常内容", "结构专业", "主体结构", "钢筋", 1,
             build_search_text("5.1.1", "正常", "正常内容")),
        )
        # 孤立条文：parent_clause 指向不存在的 id
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, parent_clause, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.2", "孤立内容", 99999, build_search_text("5.1.2", "", "孤立内容")),
        )
        # 空内容条文
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, search_text)
               VALUES (?,?,?,?)""",
            (spec_id, "5.1.3", "   ", build_search_text("5.1.3", "", "")),
        )
        # 分类异常：ai_classified=1 但六维空
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, ai_classified, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.4", "异常内容", 1, build_search_text("5.1.4", "", "异常内容")),
        )
    return db_path


def test_run_health_check_detects_issues(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import run_health_check
    result = run_health_check()
    checks = {c["key"]: c for c in result["checks"]}
    assert checks["orphan_parent"]["count"] == 1
    assert checks["empty_content"]["count"] == 1
    assert checks["bad_classification"]["count"] == 1
    assert checks["vector_orphan"]["fixable"] is True
    assert checks["fts_mismatch"]["count"] == 0  # 全部 search_text 已同步 FTS
    # 写快照表
    with get_db() as conn:
        snap = conn.execute("SELECT result FROM health_check_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    assert snap is not None and "orphan_parent" in snap["result"]


def test_fix_issue_orphan_parent(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance2"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    fix_issue("orphan_parent")
    with get_db() as conn:
        orphan = conn.execute(
            "SELECT parent_clause FROM clauses WHERE clause_no = '5.1.2'"
        ).fetchone()
    assert orphan["parent_clause"] is None  # 悬空引用已置空


def test_fix_issue_bad_classification(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance3"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    fix_issue("bad_classification")
    with get_db() as conn:
        row = conn.execute(
            "SELECT ai_classified, needs_review FROM clauses WHERE clause_no = '5.1.4'"
        ).fetchone()
    assert row["ai_classified"] == 0
    assert row["needs_review"] == 1


def test_fix_issue_empty_content_not_fixable(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance4"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    result = fix_issue("empty_content")
    assert result["fixed"] is False  # 仅报告不自动删
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_health_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.maintenance.health_check'`

- [ ] **Step 3: 实现 health_check.py 与 VectorStore 扩展**

Create `app/maintenance/__init__.py`（空文件）.

Create `app/maintenance/health_check.py`:

```python
"""知识库健康检查（纯逻辑，供维护路由包装）

检查项与修复动作：
- orphan_parent        孤立无父级条文（parent_clause 悬空）→ 置 NULL
- empty_content        空内容条文 → 仅报告（不自动删）
- bad_classification   ai_classified=1 但 dim4/5/6 全空 → 重置 ai_classified=0, needs_review=1
- vector_orphan        向量有而 SQLite 无 → 调 VectorStore.sync_with_db 删孤儿
- vector_missing       SQLite 有而向量无 → 调 VectorStore.index_missing 补索引
- fts_mismatch         clauses_fts 缺 rowid → 增量补插 search_text
"""
from app.database import get_db
from app.logging_util import log_action

LABELS = {
    "orphan_parent": "孤立无父级条文",
    "empty_content": "空内容条文",
    "bad_classification": "分类标签异常",
    "vector_orphan": "向量孤儿记录",
    "vector_missing": "缺失向量索引",
    "fts_mismatch": "FTS 索引缺失",
}


def _count_orphan_parent() -> int:
    with get_db() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM clauses c
               WHERE c.parent_clause IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM clauses p WHERE p.id = c.parent_clause)"""
        ).fetchone()[0]


def _count_empty_content() -> int:
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE content IS NULL OR TRIM(content) = ''"
        ).fetchone()[0]


def _count_bad_classification() -> int:
    with get_db() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM clauses
               WHERE ai_classified = 1
                 AND COALESCE(dim4_specialty, '') = ''
                 AND COALESCE(dim5_location, '') = ''
                 AND COALESCE(dim6_material, '') = ''"""
        ).fetchone()[0]


def _vector_ids() -> set[int]:
    """读取向量表全部 clause_id（表不存在返回空集）"""
    try:
        from app.search.vector_search import VectorStore
        vs = VectorStore()
        if not vs._table_exists():
            return set()
        tbl = vs._get_table()
        return {int(v) for v in tbl.to_arrow().column("clause_id").to_pylist()}
    except Exception:
        return set()


def _count_vector_orphan() -> int:
    vids = _vector_ids()
    if not vids:
        return 0
    with get_db() as conn:
        db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
    return len(vids - db_ids)


def _count_vector_missing() -> int:
    vids = _vector_ids()
    with get_db() as conn:
        db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
    return len(db_ids - vids) if vids else len(db_ids)


def _count_fts_mismatch() -> int:
    with get_db() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM clauses c
               WHERE NOT EXISTS (SELECT 1 FROM clauses_fts f WHERE f.rowid = c.id)"""
        ).fetchone()[0]


def run_health_check() -> dict:
    """执行全部检查，写 system_logs + health_check_snapshots，返回结果 dict"""
    counts = {
        "orphan_parent": _count_orphan_parent(),
        "empty_content": _count_empty_content(),
        "bad_classification": _count_bad_classification(),
        "vector_orphan": _count_vector_orphan(),
        "vector_missing": _count_vector_missing(),
        "fts_mismatch": _count_fts_mismatch(),
    }
    checks = []
    for key, label in LABELS.items():
        count = counts[key]
        fixable = key != "empty_content"
        checks.append({
            "key": key, "label": label, "count": count,
            "severity": "ok" if count == 0 else ("warn" if fixable else "error"),
            "fixable": fixable,
        })
    result = {"checks": checks}
    # 持久化：写日志 + 快照（表由 P0 schema 迁移建好）
    import json
    log_action("maintenance", "INFO", "健康检查", detail=json.dumps(result))
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO health_check_snapshots (result) VALUES (?)",
                (json.dumps(result),),
            )
    except Exception:
        pass
    return result


def fix_issue(key: str) -> dict:
    """单项修复，返回 {key, fixed, detail}"""
    if key == "orphan_parent":
        with get_db() as conn:
            n = conn.execute(
                """UPDATE clauses SET parent_clause = NULL
                   WHERE parent_clause IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM clauses p WHERE p.id = clauses.parent_clause)"""
            ).rowcount
        log_action("maintenance", "INFO", "修复孤立条文", detail=str(n))
        return {"key": key, "fixed": n > 0, "detail": f"已置空 {n} 条悬空引用"}
    if key == "empty_content":
        # 仅报告，不自动删（人工决定）
        return {"key": key, "fixed": False, "detail": "空内容条文仅报告，不自动删除"}
    if key == "bad_classification":
        with get_db() as conn:
            n = conn.execute(
                """UPDATE clauses SET ai_classified = 0, needs_review = 1
                   WHERE ai_classified = 1
                     AND COALESCE(dim4_specialty, '') = ''
                     AND COALESCE(dim5_location, '') = ''
                     AND COALESCE(dim6_material, '') = ''"""
            ).rowcount
        log_action("maintenance", "INFO", "重置分类异常", detail=str(n))
        return {"key": key, "fixed": n > 0, "detail": f"已重置 {n} 条进入复核"}
    if key == "vector_orphan":
        from app.search.vector_search import VectorStore
        removed = VectorStore().sync_with_db()
        log_action("maintenance", "INFO", "清理孤儿向量", detail=str(removed))
        return {"key": key, "fixed": removed > 0, "detail": f"已清理 {removed} 条孤儿向量"}
    if key == "vector_missing":
        from app.search.vector_search import VectorStore
        added = VectorStore().index_missing()
        log_action("maintenance", "INFO", "补齐缺失向量", detail=str(added))
        return {"key": key, "fixed": added > 0, "detail": f"已补齐 {added} 条向量"}
    if key == "fts_mismatch":
        with get_db() as conn:
            n = conn.execute(
                """INSERT INTO clauses_fts(rowid, search_text)
                   SELECT c.id, COALESCE(c.search_text, '') FROM clauses c
                   WHERE NOT EXISTS (SELECT 1 FROM clauses_fts f WHERE f.rowid = c.id)"""
            ).rowcount
        log_action("maintenance", "INFO", "补齐 FTS 索引", detail=str(n))
        return {"key": key, "fixed": n > 0, "detail": f"已补齐 {n} 条 FTS 索引"}
    return {"key": key, "fixed": False, "detail": f"未知检查项: {key}"}


def fix_all() -> list[dict]:
    """一键修复全部可修复项"""
    results = []
    for key in LABELS:
        results.append(fix_issue(key))
    return results
```

Modify `app/search/vector_search.py`：

- 新增只读孤儿检测 `get_orphans()`（供检查与 sync_with_db 复用）：

```python
    def get_orphans(self) -> list[int]:
        """返回向量表有而 SQLite 无的孤儿 clause_id（只读，不删除）"""
        if not self._table_exists():
            return []
        try:
            rows = self._get_table().to_arrow()
        except Exception as e:
            logger.warning("读取向量表 clause_id 失败: %s", e)
            return []
        if rows.num_rows == 0:
            return []
        vector_ids = {int(v) for v in rows.column("clause_id").to_pylist()}
        with get_db() as conn:
            db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
        return sorted(vector_ids - db_ids)
```

- 重构 `sync_with_db` 复用 `get_orphans`：

```python
    def sync_with_db(self) -> int:
        """对比向量表与数据库现存条文，删除孤儿向量（返回清理条数）"""
        orphans = self.get_orphans()
        if not orphans:
            return 0
        tbl = self._get_table()
        removed = 0
        for cid in orphans:
            try:
                tbl.delete(f"clause_id = {cid}")
                removed += 1
            except Exception as e:
                logger.warning("清理孤儿向量 clause_id=%s 失败: %s", cid, e)
        return removed
```

- 新增 `index_missing()` 补齐缺失向量：

```python
    def index_missing(self) -> int:
        """SQLite 有而向量表无的条文补索引（返回补齐数）。

        复用导入链路的 embedding 文本构造（code/title/[clause_no]/title/content）。
        """
        if not self._table_exists():
            # 向量表不存在：无可补（全量重建走 rebuild-vectors）
            return 0
        try:
            rows = self._get_table().to_arrow()
            vector_ids = {int(v) for v in rows.column("clause_id").to_pylist()} if rows.num_rows else set()
        except Exception as e:
            logger.warning("读取向量表失败: %s", e)
            return 0
        with get_db() as conn:
            missing = conn.execute(
                """SELECT c.id, c.clause_no, c.title, c.content,
                          s.code, s.title as spec_title
                   FROM clauses c JOIN specifications s ON c.spec_id = s.id
                   WHERE c.id NOT IN ({})
                   ORDER BY c.id""".format(
                    ",".join("?" * len(vector_ids)) if vector_ids else "0"
                ),
                list(vector_ids) if vector_ids else [],
            ).fetchall()
        added = 0
        for r in missing:
            embed_text = f"{r['code'] or ''} {r['spec_title'] or ''} [{r['clause_no']}] {r['title'] or ''} {r['content']}"
            try:
                self.index_clause(r["id"], r["spec_id"], embed_text)
                added += 1
            except Exception as e:
                logger.warning("补齐向量 clause_id=%s 失败: %s", r["id"], e)
        return added
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_health_check.py tests/test_vector_sync.py -v`
Expected: PASS（`test_vector_sync` 回归：sync_with_db 行为不变）

- [ ] **Step 5: Commit**

```bash
git add app/maintenance/__init__.py app/maintenance/health_check.py app/search/vector_search.py tests/test_health_check.py
git commit -m "feat: 健康检查逻辑模块（6 项检查 + 修复，VectorStore 拆出 get_orphans/index_missing）"
```

---

### Task 3: 维护路由 — 健康检查端点

**Files:**
- Create: `app/routes/maintenance_routes.py`
- Create: `app/templates/partials/maintenance_health_result.html`（健康结果片段，路由渲染目标）
- Modify: `app/main.py:88-110`（注册 maintenance_router）
- Test: `tests/test_maintenance_routes.py`

**Interfaces:**
- Consumes: Task 2 的 `run_health_check`/`fix_issue`/`fix_all`
- Produces:
  - `GET /maintenance` → `maintenance.html` 页面（center_content）
  - `POST /maintenance/health-check` → `partials/maintenance_health_result.html` 片段（结果列表 + 分项修复/一键修复/重建向量按钮）
  - `POST /maintenance/fix/{key}` → 更新后的结果片段
  - `POST /maintenance/fix-all` → 更新后的结果片段
  - `POST /maintenance/rebuild-vectors` → 结果片段（重建完成后）
  - 所有端点返回 HTML 片段（htmx 局部刷新）

- [ ] **Step 1: 写失败测试**

Create `tests/test_maintenance_routes.py`:

```python
"""维护路由健康检查端点测试"""
import json
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path, name="m.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / f"lance-{name}"))
    init_db()
    return db_path


def test_health_check_endpoint_returns_result(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.post("/maintenance/health-check")
    assert resp.status_code == 200
    assert "孤立无父级条文" in resp.text  # 渲染结果标签


def test_fix_all_endpoint(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/maintenance/fix-all")
    assert resp.status_code == 200


def test_health_check_writes_snapshot(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    auth_client.post("/maintenance/health-check")
    with get_db() as conn:
        row = conn.execute(
            "SELECT result FROM health_check_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None and "checks" in row["result"]
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_maintenance_routes.py -v`
Expected: FAIL（404 — 路由未注册）

- [ ] **Step 3: 实现路由 + 结果模板 + main.py 注册**

Create `app/routes/maintenance_routes.py`:

```python
"""维护工具路由：健康检查 / 导出备份 / FTS optimize / 日志界面（P3）"""
import time
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.logging_util import log_action

router = APIRouter()


def _render_health_result(request: Request, result: dict):
    from app.main import templates
    return templates.TemplateResponse(request, "partials/maintenance_health_result.html", {
        "checks": result.get("checks", []),
    })


@router.post("/maintenance/health-check")
async def health_check_run(request: Request):
    """运行健康检查，返回结果片段"""
    from app.maintenance.health_check import run_health_check
    start = time.time()
    result = run_health_check()
    log_action("maintenance", "INFO", "运行健康检查",
               detail=str(result), duration_ms=int((time.time() - start) * 1000))
    return _render_health_result(request, result)


@router.post("/maintenance/fix/{key}")
async def health_fix_one(request: Request, key: str):
    """单项修复后返回更新结果"""
    from app.maintenance.health_check import fix_issue, run_health_check
    fix_issue(key)
    return _render_health_result(request, run_health_check())


@router.post("/maintenance/fix-all")
async def health_fix_all(request: Request):
    """一键修复全部可修复项"""
    from app.maintenance.health_check import fix_all, run_health_check
    fix_all()
    return _render_health_result(request, run_health_check())


@router.post("/maintenance/rebuild-vectors")
async def rebuild_vectors(request: Request):
    """全量重建向量索引（清空 + 全部条文重新 embedding）"""
    from app.database import get_db as _get_db
    from app.search.vector_search import VectorStore
    from app.search.tokenize import build_search_text  # noqa: F401

    vs = VectorStore()
    vs.clear_all()
    with _get_db() as conn:
        clauses = conn.execute(
            """SELECT c.id, c.spec_id, c.clause_no, c.title, c.content,
                      s.code, s.title as spec_title
               FROM clauses c JOIN specifications s ON c.spec_id = s.id
               ORDER BY c.id"""
        ).fetchall()
    records = []
    for c in clauses:
        embed_text = (f"{c['code'] or ''} {c['spec_title'] or ''} "
                      f"[{c['clause_no']}] {c['title'] or ''} {c['content']}")
        records.append({"clause_id": c["id"], "spec_id": c["spec_id"],
                        "text": embed_text, "dim_scores": ""})
    vs.batch_index(records)
    log_action("maintenance", "INFO", "重建向量索引", detail=str(len(records)))
    from app.main import templates
    return HTMLResponse(f"""<p style="color:green;margin-top:0.5rem">✅ 向量索引已重建：{len(records)} 条</p>
    <div hx-post="/maintenance/health-check" hx-trigger="load" hx-swap="outerHTML"></div>""")
```

Modify `app/main.py`（路由注册区，`synonym_router` 之后）：

```python
from app.routes.maintenance_routes import router as maintenance_router
app.include_router(maintenance_router)
```

Create `app/templates/partials/maintenance_health_result.html`（健康结果片段：各检查项 + 分项修复 + 一键修复 + 重建向量）：

```html
<!-- 健康检查结果片段：各检查项 + 分项修复 + 一键修复 + 重建向量 -->
{% if checks %}
<table class="striped" style="font-size:0.85rem">
    <thead><tr><th>检查项</th><th>数量</th><th>状态</th><th>操作</th></tr></thead>
    <tbody>
    {% for c in checks %}
    <tr>
        <td>{{ c.label }}</td>
        <td>{{ c.count }}</td>
        <td>
            {% if c.severity == 'ok' %}
            <span style="color:#1a7a1a">✅ 正常</span>
            {% elif c.fixable %}
            <span style="color:#a06500">⚠️ 可修复</span>
            {% else %}
            <span style="color:#b00000">⛔ 需人工</span>
            {% endif %}
        </td>
        <td>
            {% if c.count > 0 and c.fixable %}
            <button class="outline" style="font-size:0.75rem;padding:0.1rem 0.4rem"
                    hx-post="/maintenance/fix/{{ c.key }}" hx-target="#health-result" hx-swap="innerHTML">修复</button>
            {% endif %}
        </td>
    </tr>
    {% endfor %}
    </tbody>
</table>
{% if checks|selectattr('count','gt',0)|selectattr('fixable','equalto',true)|list %}
<button hx-post="/maintenance/fix-all" hx-target="#health-result" hx-swap="innerHTML"
        style="margin-top:0.5rem;background:var(--pico-primary-background);border-color:var(--pico-primary-background);color:var(--pico-primary-inverse)">🧹 一键修复全部</button>
{% endif %}
<button hx-post="/maintenance/rebuild-vectors" hx-target="#health-result" hx-swap="innerHTML"
        hx-confirm="将清空并全量重建向量索引（耗时较长），确定继续？"
        class="outline" style="margin-top:0.5rem">🔄 重建向量索引</button>
{% else %}
<p style="color:var(--pico-muted-color)">点击「运行健康检查」开始。</p>
{% endif %}
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_maintenance_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/maintenance_routes.py app/main.py tests/test_maintenance_routes.py
git commit -m "feat: 维护路由——健康检查/分项修复/一键修复/重建向量索引端点"
```

---

### Task 4: 导出/备份（SQLite 备份 + 规则导出 + 复核队列导出）

**Files:**
- Modify: `app/config.py:16-20`（加 `BACKUP_DIR`）
- Modify: `app/routes/maintenance_routes.py`（加导出端点）
- Test: `tests/test_maintenance_export.py`

**Interfaces:**
- Consumes: 现有 `classification_rules`/`classification_queue` 表；Task 2 的 log_action
- Produces:
  - `POST /maintenance/backup` → `data/backups/spec_query_YYYYMMDD_HHMMSS.db`（VACUUM INTO，目录自动创建），返回备份路径提示
  - `GET /maintenance/export/rules` → 全部分类规则 JSON 附件下载（`attachment; filename=classification_rules_<ts>.json`）
  - `GET /maintenance/export/review-queue` → 待复核队列（status='review'）JSON 附件下载（含条文内容/AI 标签/置信度）

- [ ] **Step 1: 写失败测试**

Create `tests/test_maintenance_export.py`:

```python
"""导出/备份端点测试"""
import json
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "me.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.config.BACKUP_DIR", str(tmp_path / "backups"))
    init_db()


def test_backup_creates_sqlite_file(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.post("/maintenance/backup")
    assert resp.status_code == 200
    backup_files = list((tmp_path / "backups").glob("*.db"))
    assert len(backup_files) == 1
    assert backup_files[0].stat().st_size > 0


def test_export_rules_returns_json(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
               VALUES ('dim4', 'specialty', '钢筋', 'keyword', 1, 0.6, 1)"""
        )
    resp = auth_client.get("/maintenance/export/rules")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert len(data) == 1 and data[0]["pattern"] == "钢筋"


def test_export_review_queue_json(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
        clause = conn.execute("SELECT id FROM clauses LIMIT 1").fetchone()
        conn.execute(
            """INSERT INTO classification_queue (clause_id, dimension, status, ai_label, ai_confidence)
               VALUES (?, 'dim4', 'review', '结构专业', 0.82)""",
            (clause["id"],),
        )
    resp = auth_client.get("/maintenance/export/review-queue")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ai_label"] == "结构专业"
    assert "content" in data[0]  # 含条文内容
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_maintenance_export.py -v`
Expected: FAIL（404 — 端点未实现）

- [ ] **Step 3: 实现 config 与导出端点**

Modify `app/config.py`（路径区，`WORKSPACE_DIR` 之后）：

```python
BACKUP_DIR = os.getenv("BACKUP_DIR", str(BASE_DIR / "data" / "backups"))
```

并把 `BACKUP_DIR` 加入启动 mkdir 循环（原 L23-24）：

```python
for d in [UPLOAD_DIR, OUTPUT_DIR, WORKSPACE_DIR, LANCE_DB_PATH, BACKUP_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)
```

Modify `app/routes/maintenance_routes.py` 末尾加导出端点：

```python
import time as _time
from fastapi.responses import JSONResponse, StreamingResponse


@router.post("/maintenance/backup")
async def backup_db(request: Request):
    """SQLite 单文件备份：VACUUM INTO data/backups/spec_query_<ts>.db

    注意：VACUUM INTO 的目标路径必须是字面量（不支持 ? 参数绑定），且目标文件
    已存在时会报错。路径由 BACKUP_DIR + 服务端时间戳生成（非用户输入），单引号
    转义后安全拼接；同秒重复备份追加 _2 序号避免冲突。
    """
    import sqlite3
    from app.database import DATABASE_PATH  # database 模块路径（测试 monkeypatch 该处）
    from app.config import BACKUP_DIR
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    base = _time.strftime("%Y%m%d_%H%M%S")
    target = Path(BACKUP_DIR) / f"spec_query_{base}.db"
    seq = 2
    while target.exists():
        target = Path(BACKUP_DIR) / f"spec_query_{base}_{seq}.db"
        seq += 1
    escaped = str(target).replace("'", "''")
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(f"VACUUM INTO '{escaped}'")
    finally:
        conn.close()
    log_action("maintenance", "INFO", "SQLite 备份", detail=str(target))
    return HTMLResponse(f"""<p style="color:green;margin-top:0.5rem">✅ 已备份至：<code>{target.name}</code></p>""")


@router.get("/maintenance/export/rules")
async def export_rules(request: Request):
    """导出全部分类规则 JSON（附件下载）"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM classification_rules ORDER BY dimension, id").fetchall()
    data = [dict(r) for r in rows]
    ts = _time.strftime("%Y%m%d_%H%M%S")
    log_action("maintenance", "INFO", "导出规则", detail=str(len(data)))
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="classification_rules_{ts}.json"'
    })


@router.get("/maintenance/export/review-queue")
async def export_review_queue(request: Request):
    """导出待复核队列 JSON（含条文内容/AI 标签/置信度）"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.id as queue_id, q.clause_id, q.dimension, q.keyword_score,
                      q.ai_label, q.ai_confidence, q.status, q.created_at,
                      c.clause_no, c.title as clause_title, c.content,
                      s.code as spec_code, s.title as spec_title
               FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               JOIN specifications s ON c.spec_id = s.id
               WHERE q.status = 'review'
               ORDER BY q.created_at DESC"""
        ).fetchall()
    data = [dict(r) for r in rows]
    ts = _time.strftime("%Y%m%d_%H%M%S")
    log_action("maintenance", "INFO", "导出复核队列", detail=str(len(data)))
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="review_queue_{ts}.json"'
    })
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_maintenance_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/routes/maintenance_routes.py tests/test_maintenance_export.py
git commit -m "feat: 导出备份——SQLite 备份(VACUUM INTO)/导出规则/导出复核队列 JSON"
```

---

### Task 5: FTS5 optimize（启动时 + 手动端点）

**Files:**
- Modify: `app/main.py`（startup 加后台线程执行 optimize）
- Modify: `app/routes/maintenance_routes.py`（加 `POST /maintenance/fts-optimize`）
- Test: `tests/test_fts_optimize.py`

**Interfaces:**
- Consumes: `clauses_fts`（FTS5 表）
- Produces:
  - `POST /maintenance/fts-optimize` → 执行 `INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')`，返回提示
  - 启动时后台线程执行同操作（不阻塞启动）

- [ ] **Step 1: 写失败测试**

Create `tests/test_fts_optimize.py`:

```python
"""FTS5 optimize 端点测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "fts.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)


def test_fts_optimize_endpoint(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/maintenance/fts-optimize")
    assert resp.status_code == 200
    assert "optimize" in resp.text.lower() or "完成" in resp.text


def test_fts_optimize_sql_runs(monkeypatch, tmp_path):
    """optimize 命令真实可执行"""
    _setup(monkeypatch, tmp_path)
    from app.database import get_connection
    conn = get_connection()
    try:
        conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')")
        conn.commit()
    finally:
        conn.close()
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clauses_fts").fetchone()[0]
    assert total >= 0
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_fts_optimize.py -v`
Expected: FAIL（404 — 端点未实现）

- [ ] **Step 3: 实现端点与启动优化**

Modify `app/routes/maintenance_routes.py` 加：

```python
@router.post("/maintenance/fts-optimize")
async def fts_optimize(request: Request):
    """SQLite FTS5 定期 optimize：合并碎片，提升检索性能"""
    with get_db() as conn:
        conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')")
    log_action("maintenance", "INFO", "FTS optimize")
    return HTMLResponse('<p style="color:green;margin-top:0.5rem">✅ FTS5 optimize 完成</p>')
```

Modify `app/main.py`（新增启动后台线程，`_startup_warmup_embedding` 之后加定义，`startup()` 中调用）：

```python
def _startup_fts_optimize():
    """后台线程执行 FTS5 optimize（合并碎片），不阻塞应用启动"""
    import threading

    def _run():
        try:
            from app.database import get_db
            with get_db() as conn:
                conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')")
            logger.info("FTS5 optimize 完成")
        except Exception as e:
            logger.warning("FTS5 optimize 失败: %s", e)

    threading.Thread(target=_run, daemon=True).start()
```

并在 `startup()`（L51-55）中加 `_startup_fts_optimize()`。

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_fts_optimize.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routes/maintenance_routes.py tests/test_fts_optimize.py
git commit -m "feat: FTS5 optimize——启动后台执行 + 维护界面手动端点"
```

---

### Task 6: /maintenance 页面（三 Tab）+ 左侧维护按钮

**Files:**
- Create: `app/templates/partials/maintenance.html`
- Modify: `app/routes/maintenance_routes.py`（加 `GET /maintenance` 页面路由）
- Modify: `app/templates/partials/tree_panel.html:95-112`（功能格加「🛠 维护」按钮）
- Test: `tests/test_maintenance_ui.py`

**Interfaces:**
- Consumes: Task 3/4/5 的端点（htmx 调用）
- Produces: 维护页面三 Tab（Alpine 切换：健康检查 / 导出备份 / 日志管理-占位）；结果片段带分项修复/一键修复/重建向量按钮

- [ ] **Step 1: 写失败测试**

Create `tests/test_maintenance_ui.py`:

```python
"""维护界面 UI 模板渲染测试"""
from app.database import init_db


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "ui.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()


def test_tree_panel_has_maintenance_button(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/")
    assert "🛠 维护" in resp.text


def test_maintenance_page_has_three_tabs(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "健康检查" in resp.text
    assert "导出备份" in resp.text
    assert "日志管理" in resp.text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_maintenance_ui.py -v`
Expected: FAIL（无「🛠 维护」文案 / 维护页面无三 Tab 文案）

- [ ] **Step 3: 实现页面路由、模板与按钮**

Modify `app/routes/maintenance_routes.py` 加页面路由（放 `_render_health_result` 之后）：

```python
@router.get("/maintenance")
async def maintenance_page(request: Request):
    """维护界面（三 Tab：健康检查 / 导出备份 / 日志管理-占位）"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/maintenance.html",
    })
```

Create `app/templates/partials/maintenance.html`:

```html
<!-- 维护界面：三 Tab（健康检查 / 导出备份 / 日志管理占位） -->
<div x-data="{ tab: 'health' }" style="padding:1rem">
    <h2>🛠 知识库维护</h2>
    <div style="display:flex;gap:0.5rem;margin:1rem 0;flex-wrap:wrap">
        <button class="outline" style="font-size:0.8rem"
                :class="tab==='health' ? 'contrast' : ''" @click="tab='health'">📋 健康检查</button>
        <button class="outline" style="font-size:0.8rem"
                :class="tab==='export' ? 'contrast' : ''" @click="tab='export'">💾 导出备份</button>
        <button class="outline" style="font-size:0.8rem"
                :class="tab==='logs' ? 'contrast' : ''" @click="tab='logs'">📜 日志管理</button>
    </div>

    <!-- Tab 1: 健康检查 -->
    <div x-show="tab==='health'">
        <button hx-post="/maintenance/health-check" hx-target="#health-result" hx-swap="innerHTML"
                style="background:var(--pico-primary-background);border-color:var(--pico-primary-background);color:var(--pico-primary-inverse)">
            🔍 运行健康检查
        </button>
        <div id="health-result" style="margin-top:1rem"></div>
    </div>

    <!-- Tab 2: 导出备份 -->
    <div x-show="tab==='export'">
        <p style="color:var(--pico-muted-color);font-size:0.85rem">备份/导出为 JSON 或 SQLite 单文件，用于迁移环境或存档。</p>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.8rem">
            <button hx-post="/maintenance/backup" hx-target="#export-result" hx-swap="innerHTML"
                    hx-confirm="确定执行 SQLite 备份？" class="outline">💾 SQLite 备份</button>
            <a href="/maintenance/export/rules"><button class="outline">📋 导出分类规则</button></a>
            <a href="/maintenance/export/review-queue"><button class="outline">📝 导出复核队列</button></a>
            <button hx-post="/maintenance/fts-optimize" hx-target="#export-result" hx-swap="innerHTML"
                    class="outline">🧩 FTS5 optimize</button>
        </div>
        <div id="export-result" style="margin-top:0.8rem"></div>
    </div>

    <!-- Tab 3: 日志管理（占位，P3 实现） -->
    <div x-show="tab==='logs'">
        <p style="color:var(--pico-muted-color)">📜 日志管理将在后续版本提供（行为筛选/异常导出）。</p>
    </div>
</div>
```

Modify `app/templates/partials/tree_panel.html` 功能格（AI 问答按钮前）加：

```html
        <a href="/maintenance" style="text-decoration:none">
            <button class="outline" style="width:100%;font-size:0.8rem">🛠 维护</button>
        </a>
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_maintenance_ui.py tests/test_maintenance_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/partials/maintenance.html app/templates/partials/maintenance_health_result.html app/templates/partials/tree_panel.html tests/test_maintenance_ui.py
git commit -m "feat: /maintenance 维护界面三 Tab + 左侧🛠维护按钮"
```

---

## 验收清单（P1 完成标准）

- [ ] `D:/Python/python.exe -m pytest tests/ -v` 全量通过（既有 466 + 新增全部）
- [ ] 左侧功能区出现「🛠 维护」，点击进入 `/maintenance`
- [ ] 健康检查 Tab：「运行健康检查」→ 6 项检查结果（数量/状态/修复按钮）；孤立条文/分类异常可单项修复；空内容显示"需人工"
- [ ] 「一键修复全部」修完复查数量归零；「重建向量索引」确认后重建成功
- [ ] 导出备份 Tab：SQLite 备份生成 `data/backups/*.db`；导出规则/复核队列下载 JSON 附件；FTS5 optimize 提示完成
- [ ] 日志管理 Tab 显示"后续版本提供"占位
- [ ] 健康检查结果写入 `health_check_snapshots` + `system_logs`（可查）
- [ ] 服务重启后 FTS optimize 后台执行不阻塞启动
