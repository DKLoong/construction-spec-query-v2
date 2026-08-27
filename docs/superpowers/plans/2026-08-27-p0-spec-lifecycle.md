# P0 规范生命周期管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通规范「现行/废止/修订中」生命周期：导入时 AI 校核打标（含编号/名称规范化回填）、检索/QA 按状态过滤、废止/被替代规范三处提示，分类树支持同维多选。

**Architecture:** 复用现有 `specifications.status` 字段（值域现行/废止/修订中）作为单一状态源。检索/QA 通过新增 `status_filter` 过滤参数打通；导入侧新增 `POST /import/validate-version` 调用 AI 一次性返回状态+被替代编号+规范化命名，前端以「建议 + 用户确认应用」方式回填；编号前缀知识抽取为共享模块 `app/parser/spec_prefix.py`（单一知识源），`normalize_spec_code` 做机械规范化（全角→半角、推荐变体 `GBT→GB/T` 补斜杠）。

**Tech Stack:** FastAPI / SQLite / HTMX + Alpine.js / Pico.css / Paddle 无关。测试用 pytest + `fastapi.testclient`（`auth_client` fixture）。

**Spec:** `docs/superpowers/specs/2026-08-27-maintenance-and-lifecycle-design.md`（§2 决策 D1/D3/D5/D6/D7/D8/D9/D18/D19/D20，§4 全章）

## Global Constraints

- Python 一律用 `D:/Python/python.exe`（**禁用 `python3`**，指向 Windows Store 空壳）；运行测试 `cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest <file> -v`
- 提交规范：`feat:` / `fix:` / `test:` / `docs:` / `refactor:` 前缀，单 task 原子提交
- 静态文件/模板改动后：`base.html` 中对应 `<script src="...?v=N">` 版本号**必须递增**，浏览器 `Ctrl+F5` 强刷
- 数据变更后调用 `hybrid_search.clear_search_cache()`，否则检索结果串缓存
- 前端交互无自动化测试：以路由/模板断言（`response.text`）+ 手动验证为准，JS 逻辑不写 pytest
- 数据库操作全部走 `get_db()` 上下文管理器，参数化查询，禁止字符串拼接 SQL（占位符 `?`）
- 不做与 P0 无关的重构（P1/P2/P3 功能不在本计划内）

---

### Task 1: 共享前缀模块 spec_prefix.py（抽取 + normalize_spec_code）

**Files:**
- Create: `app/parser/spec_prefix.py`
- Modify: `app/routes/import_routes.py:24-31`（删 `_PREFIX_WHITELIST` 改为引用）、`:423-507`（删 `_detect_hierarchy`/`_detect_nature` 本地函数）、`:54`（`_PREFIX_WHITELIST` → `PREFIX_WHITELIST`）、`:248-249`（调用点改用 `detect_hierarchy`/`detect_nature`）
- Test: `tests/test_spec_prefix.py`

**Interfaces:**
- Produces:
  - `app.parser.spec_prefix.normalize_spec_code(code: str) -> str` — 全角符号转半角、推荐变体前缀补 `/T`、空格规整。示例：`'GB 50010—2010'`→`'GB 50010-2010'`，`'GBT 50010-2010'`→`'GB/T 50010-2010'`
  - `app.parser.spec_prefix.detect_hierarchy(code: str) -> str` — 行为与旧 `import_routes._detect_hierarchy` 完全一致
  - `app.parser.spec_prefix.detect_nature(code: str) -> str` — 行为一致，**唯一差异**：推荐变体集合扩展 `JGJT→JGJ/T`（修复旧漏判，旧实现对 `JGJT` 误判强制性）
  - `app.parser.spec_prefix.PREFIX_WHITELIST: set[str]` — 值 = 旧 `_PREFIX_WHITELIST` 全集
  - `app.parser.spec_prefix.RECOMMENDED_T: dict[str, str]` — 9 个变体：`GBT→GB/T`、`JTT→JT/T`、`JTGT→JTG/T`、`JGT→JG/T`、`CJT→CJ/T`、`JBT→JB/T`、`NYT→NY/T`、`DBT→DB/T`、`JGJT→JGJ/T`
- Consumes: 无（纯模块）

- [ ] **Step 1: 写失败测试**

Create `tests/test_spec_prefix.py`:

```python
"""规范编号前缀共享模块测试"""
import pytest
from app.parser.spec_prefix import (
    normalize_spec_code, detect_hierarchy, detect_nature, PREFIX_WHITELIST, RECOMMENDED_T,
)


# ── normalize_spec_code 机械规范化 ──

def test_normalize_fullwidth_dash():
    assert normalize_spec_code("GB 50010—2010") == "GB 50010-2010"


def test_normalize_fullwidth_connect():
    assert normalize_spec_code("GB 50010－2010") == "GB 50010-2010"


def test_normalize_recommended_prefix_gbt():
    """GBT 无斜杠写法 → GB/T 标准写法（D20 复用判别知识）"""
    assert normalize_spec_code("GBT 50010-2010") == "GB/T 50010-2010"


def test_normalize_recommended_prefix_jgjt():
    assert normalize_spec_code("JGJT 162-2008") == "JGJ/T 162-2008"


def test_normalize_recommended_prefix_dbt():
    assert normalize_spec_code("DBT 11/1234-2020") == "DB/T 11/1234-2020"


def test_normalize_already_standard_idempotent():
    """已规范写法幂等不改"""
    assert normalize_spec_code("GB/T 50107-2010") == "GB/T 50107-2010"


def test_normalize_extra_spaces_compressed():
    assert normalize_spec_code("GB   50010   -  2010") == "GB 50010-2010"


def test_normalize_empty():
    assert normalize_spec_code("") == ""
    assert normalize_spec_code("  ") == ""


# ── detect_hierarchy / detect_nature 回归 ──

def test_hierarchy_gt():
    assert detect_hierarchy("GBT 50010-2010") == "国家标准"


def test_hierarchy_jgj():
    assert detect_hierarchy("JGJ 162-2008") == "建筑工程"


def test_hierarchy_longest_prefix_first():
    assert detect_hierarchy("JTG D40-2011") == "公路工程"
    assert detect_hierarchy("JT T 001-2012") == "交通运输"


def test_nature_gb_is_mandatory():
    assert detect_nature("GB 50010-2010") == "强制性"


def test_nature_gbt_recommended():
    assert detect_nature("GB/T 50107-2010") == "推荐性"


def test_nature_gbt_no_slash_recommended():
    """GBT 无斜杠写法仍识别为推荐性（用户常写 GBT）"""
    assert detect_nature("GBT 50107-2010") == "推荐性"


def test_nature_jgjt_recommended_fix():
    """修复：JGJT 旧实现漏判为强制性，现应为推荐性"""
    assert detect_nature("JGJ/T 231-2010") == "推荐性"


def test_nature_yz_recommended():
    assert detect_nature("YZ 5002-2015") == "推荐性"


def test_prefix_whitelist_contains_legacy():
    assert "GB" in PREFIX_WHITELIST and "GBT" in PREFIX_WHITELIST
    assert "JGJT" in PREFIX_WHITELIST and "DBT" in PREFIX_WHITELIST
    assert len(RECOMMENDED_T) == 9
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_spec_prefix.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.parser.spec_prefix'`

- [ ] **Step 3: 实现 spec_prefix.py**

Create `app/parser/spec_prefix.py`:

```python
"""规范编号前缀知识共享模块（层级/性质/编号规范化，单一知识源）

抽取自 import_routes._detect_hierarchy / _detect_nature，供导入校核、检索、
版本识别统一引用。行为与旧实现保持一致，仅一处修复：推荐变体集合补 JGJT
（旧实现漏判，将 JGJ/T 误判为强制性）。
"""
import re

# 前缀 → 层级（按长度降序，最长前缀优先匹配；已去斜杠写法）
PREFIX_MAP: list[tuple[str, str]] = [
    ("JTGT", "公路工程"), ("JTG", "公路工程"),
    ("JTT", "交通运输"), ("JT", "交通运输"),
    ("JGT", "建筑工业"), ("JG", "建筑工业"),
    ("JGJ", "建筑工程"),
    ("CJT", "城镇建设"), ("CJ", "城镇建设"),
    ("JBT", "机械"), ("JB", "机械"),
    ("NYT", "农业"), ("NY", "农业"),
    ("GBT", "国家标准"), ("GB", "国家标准"),
    ("TB", "铁路"), ("MH", "民用航空"),
    ("YZ", "邮政"), ("DB", "地方标准"),
]

# 推荐性变体（无斜杠写法 → 标准带 /T 代号）。归一化与性质判别共用一份。
RECOMMENDED_T: dict[str, str] = {
    "GBT": "GB/T", "JTT": "JT/T", "JTGT": "JTG/T", "JGT": "JG/T",
    "CJT": "CJ/T", "JBT": "JB/T", "NYT": "NY/T", "DBT": "DB/T",
    "JGJT": "JGJ/T",
}

# 合法前缀白名单（含无推荐变体的 GBZ/JGJ/CJJ 等），供文件名解析校验
PREFIX_WHITELIST: set[str] = {
    "JTGT", "JTG", "JTT", "JT", "JGT", "JG", "JGJ", "CJT", "CJ",
    "JBT", "JB", "NYT", "NY", "GBT", "GB", "TB", "MH", "YZ",
    "DB", "DBT", "T", "Q", "CJJ", "GJB", "GBZ", "GBJ", "JJG",
    "JJF", "XB", "QC", "JGJT",
}

# 推荐变体集合（供 detect_nature 判断；keys 即合法推荐变体）
_RECOMMENDED_KEYS: set[str] = set(RECOMMENDED_T.keys())


def normalize_spec_code(code: str) -> str:
    """规范化规范编号：全角符号→半角、推荐变体补斜杠、空格规整。

    处理顺序：全角修正 → 推荐变体前缀补 /T → 压缩连续空格。
    示例：'GB 50010—2010' → 'GB 50010-2010'；'GBT 50010-2010' → 'GB/T 50010-2010'。
    """
    if not code:
        return ""
    c = code.strip()
    c = c.replace("—", "-").replace("－", "-").replace("–", "-").replace("　", " ")
    # 推荐变体前缀补斜杠：仅当行首纯字母段精确等于某推荐变体
    m = re.match(r"^([A-Za-z]+)", c)
    if m:
        letters = m.group(1).upper()
        if letters in _RECOMMENDED_KEYS:
            c = RECOMMENDED_T[letters] + c[m.end():]
    # 压缩连续空白为单个半角空格（代号 顺序号-年份 之间恰好一个空格）
    c = re.sub(r"\s+", " ", c).strip()
    return c


def detect_hierarchy(code: str) -> str:
    """根据规范编号前缀判断规范层级（去斜杠后按最长前缀匹配）"""
    if not code:
        return ""
    c = code.replace("/", "").strip()
    if not c:
        return ""
    for prefix, hierarchy in PREFIX_MAP:
        if c.startswith(prefix):
            return hierarchy
    if c.startswith("T"):
        return "团体标准"
    if c.startswith("Q"):
        return "企业标准"
    return ""


def detect_nature(code: str) -> str:
    """根据规范编号判断强制性/推荐性"""
    if not code:
        return ""
    c = code.replace("/", "").strip()
    if not c:
        return ""
    if c.startswith("Q"):
        return ""  # 企业标准无强制/推荐之分
    if re.match(r"^T($|\s|\d)", c):
        return "推荐性"  # 团体标准（T 后不能紧跟字母，以区分 TB）
    if c.startswith("YZ"):
        return "推荐性"  # 邮政标准始终推荐性
    m = re.match(r"^([A-Za-z]+)", c)
    if not m:
        return "强制性"
    letters = m.group(1)
    if letters in _RECOMMENDED_KEYS:
        return "推荐性"
    if "/T" in code:
        return "推荐性"  # DB13/T 这类非规范写法
    return "强制性"
```

- [ ] **Step 4: 更新 import_routes 引用共享模块**

Modify `app/routes/import_routes.py`:
- 删除 `_PREFIX_WHITELIST` 定义（原 L24-31）
- 删除 `_detect_hierarchy`（原 L423-466）、`_detect_nature`（原 L470-507）两个本地函数
- 文件顶部（`from app.parser.md_parser import ...` 附近）加：
  ```python
  from app.parser.spec_prefix import (
      detect_hierarchy, detect_nature, normalize_spec_code, PREFIX_WHITELIST,
  )
  ```
- `_parse_filename_to_code_title` 中 `if prefix not in _PREFIX_WHITELIST:` → `if prefix not in PREFIX_WHITELIST:`
- Step 3 中 `dim1_hierarchy = _detect_hierarchy(code)` → `dim1_hierarchy = detect_hierarchy(code)`；`dim1_nature = _detect_nature(code)` → `dim1_nature = detect_nature(code)`

- [ ] **Step 5: 运行全部相关测试确认回归通过**

Run: `D:/Python/python.exe -m pytest tests/test_spec_prefix.py tests/test_parse_filename.py tests/test_import.py -v`
Expected: PASS（`_parse_filename`/`_import` 依赖 `_PREFIX_WHITELIST`/`_detect_*` 行为不变）

- [ ] **Step 6: Commit**

```bash
git add app/parser/spec_prefix.py app/routes/import_routes.py tests/test_spec_prefix.py
git commit -m "feat: 抽取规范编号前缀共享模块 spec_prefix.py（层级/性质/规范化，含 GBT→GB/T 归一化）"
```

---

### Task 2: Schema 迁移（replace_by / spec_version / 日志与健康快照表）

**Files:**
- Modify: `app/database.py:214-239`（`init_db` 末尾追加迁移与建表）
- Test: `tests/test_schema_lifecycle.py`

**Interfaces:**
- Produces:
  - `specifications.replace_by_spec_id INTEGER REFERENCES specifications(id)`（可空，被替代者引用新规范 id）
  - `specifications.spec_version TEXT`（可空，版本标识预留）
  - 表 `system_logs(category, level, action, detail, username, duration_ms, created_at)` + 索引
  - 表 `health_check_snapshots(result, created_at)`
- Consumes: 无

- [ ] **Step 1: 写失败测试**

Create `tests/test_schema_lifecycle.py`:

```python
"""生命周期相关 schema 迁移测试（幂等 + 可读写）"""
from app.database import init_db, get_db


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_schema_migration_adds_lifecycle_columns(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "specifications")
        assert "replace_by_spec_id" in cols
        assert "spec_version" in cols


def test_schema_migration_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t2.db"))
    init_db()
    init_db()  # 二次执行不报错
    with get_db() as conn:
        cols = _columns(conn, "specifications")
        assert "replace_by_spec_id" in cols


def test_schema_creates_system_logs_table(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t3.db"))
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "system_logs")
        for c in ("category", "level", "action", "detail", "username",
                  "duration_ms", "created_at"):
            assert c in cols


def test_schema_system_logs_writable(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t4.db"))
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO system_logs (category, level, action, username) VALUES (?,?,?,?)",
            ("spec", "INFO", "测试动作", "tester"),
        )
        row = conn.execute(
            "SELECT * FROM system_logs WHERE action = '测试动作'"
        ).fetchone()
        assert row["category"] == "spec" and row["level"] == "INFO"


def test_schema_creates_health_snapshot_table(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t5.db"))
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "health_check_snapshots")
        assert "result" in cols and "created_at" in cols
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_schema_lifecycle.py -v`
Expected: FAIL（`system_logs` 表不存在 → sqlite3.OperationalError）

- [ ] **Step 3: 实现迁移**

Modify `app/database.py` 的 `init_db()`，在现有 `clause_is_non` 迁移之后追加：

```python
        # 迁移：为已有数据库添加 replace_by_spec_id（被替代关系引用）与 spec_version（预留）
        try:
            conn.execute(
                "ALTER TABLE specifications ADD COLUMN replace_by_spec_id INTEGER "
                "REFERENCES specifications(id)"
            )
        except Exception:
            pass  # 列已存在
        try:
            conn.execute("ALTER TABLE specifications ADD COLUMN spec_version TEXT")
        except Exception:
            pass  # 列已存在
        # 日志表 + 健康检查快照表（P1 维护工具先建表，P3 日志界面消费）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                level       TEXT NOT NULL,
                action      TEXT NOT NULL,
                detail      TEXT,
                username    TEXT,
                duration_ms INTEGER,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_system_logs_category ON system_logs(category);
            CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);
            CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs(created_at);
            CREATE TABLE IF NOT EXISTS health_check_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                result      TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            """
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_schema_lifecycle.py tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_schema_lifecycle.py
git commit -m "feat: schema 迁移——specifications 加 replace_by_spec_id/spec_version，新建 system_logs 与 health_check_snapshots 表"
```

---

### Task 3: 检索 status_filter 后端过滤

**Files:**
- Modify: `app/models.py:174-189`（`SearchQuery` 加 `status_filter`）
- Modify: `app/search/sql_search.py:14-47`（`_build` 加 status 条件）
- Modify: `app/search/hybrid_search.py:112-143`（向量回查加 status 条件）、`:29-43`（缓存 key 加 status_filter）
- Modify: `app/routes/search_routes.py:26-90`（`/search` 加 `status_filter: str` 参数，解析传 SearchQuery；`all_empty` 判断不变）
- Test: `tests/test_status_filter_search.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SearchQuery.status_filter: list[str] = []` — 状态白名单，空列表 = 不过滤（含废止/被替代）
  - search_routes 请求参数 `status_filter: str = Query("")` — 逗号分隔（如 `现行,修订中`），空串 = 不过滤；路由内 `sq.status_filter = [s.strip() for s in status_filter.split(",") if s.strip()]`

- [ ] **Step 1: 写失败测试**

Create `tests/test_status_filter_search.py`（复用 conftest 的 `setup_search_data`，其规范 status 默认 '现行'，再补一条废止规范条文）：

```python
"""检索状态过滤（status_filter）测试"""
from tests.conftest import setup_search_data
from app.database import get_db, init_db


def _setup_with_obsolete(conn):
    setup_search_data(conn)
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
        ("GBJ 10-1989", "旧混凝土规范", "废止"),
    )
    sid = conn.execute("SELECT id FROM specifications WHERE code = 'GBJ 10-1989'").fetchone()["id"]
    conn.execute(
        "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
        (sid, "1.0.1", "旧条文", "本条文来自已废止规范。"),
    )


def _setup_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _setup_with_obsolete(conn)
    return auth_client


def test_search_status_filter_current_excludes_obsolete(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf1.db")
    resp = client.get("/search?all=1&status_filter=现行")
    assert "旧条文" not in resp.text
    assert "钢筋" in resp.text


def test_search_status_filter_current_and_revising(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf2.db")
    conn_ctx = None
    with get_db() as conn:
        conn.execute("UPDATE specifications SET status = '修订中' WHERE code = 'GB 50204'")
    resp = client.get("/search?all=1&status_filter=现行,修订中")
    # 现行(GB 50204 现改修订中) 被过滤 → 只剩废止的 GBJ 10-1989
    assert "旧条文" in resp.text
    assert "钢筋" not in resp.text


def test_search_status_filter_empty_returns_all(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf3.db")
    resp = client.get("/search?all=1&status_filter=")
    assert "旧条文" in resp.text
    assert "钢筋" in resp.text


def test_search_no_status_filter_param_backward_compat(auth_client, monkeypatch, tmp_path):
    """不带 status_filter 参数 → 不过滤（旧行为兼容）"""
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf4.db")
    resp = client.get("/search?all=1")
    assert "旧条文" in resp.text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_status_filter_search.py -v`
Expected: FAIL（`SearchQuery` 无 `status_filter` 字段 → AttributeError/Pydantic 报错）

- [ ] **Step 3: 实现 models + sql_search + hybrid_search + search_routes**

`app/models.py` `SearchQuery` 加字段：

```python
    # 状态过滤白名单（如 ['现行','修订中']）。空列表 = 不过滤（含废止/被替代）。
    status_filter: list[str] = []
```

`app/search/sql_search.py` `_build` 内（`spec_filters` 循环之后、`return conditions, params, joins` 之前）加：

```python
            if query.status_filter:
                ph = ",".join("?" * len(query.status_filter))
                conditions.append(f"s.status IN ({ph})")
                params.extend(query.status_filter)
```

`app/search/hybrid_search.py`：
- `_cache_key`（L29-43）返回值追加 `tuple(query.status_filter)`（元组化 list 才能哈希）
- 向量回查（L122-133）`spec_filters` 循环之后加：

```python
                if query.status_filter:
                    ph = ",".join("?" * len(query.status_filter))
                    dim_conditions.append(f"s.status IN ({ph})")
                    dim_params.extend(query.status_filter)
```

`app/routes/search_routes.py`：
- 签名加 `status_filter: str = Query("")`
- 构造 `sq` 时加：`status_filter=[s.strip() for s in status_filter.split(",") if s.strip()]`

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_status_filter_search.py tests/test_search.py tests/test_search_routes.py tests/test_hybrid_search.py -v`
Expected: PASS（现有检索测试回归不破）

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/search/sql_search.py app/search/hybrid_search.py app/routes/search_routes.py tests/test_status_filter_search.py
git commit -m "feat: 检索 status_filter 状态过滤（现行/修订中白名单，空串兼容旧行为）"
```

---

### Task 4: 检索前端「仅现行/修订中」复选框 + 轻提示

**Files:**
- Modify: `app/templates/partials/tree_panel.html:4-19`（搜索框区加两复选框）
- Modify: `static/components/tree.js:5-11`（searchState store 加 `statusCurrent`/`statusRevising`）、`:49-77`（dispatchSearch 加 status_filter 参数与轻提示触发）
- Modify: `static/components/search.js`（`searchBox` 加复选框状态绑定与 `status_filter` 参数；顶部轻提示复用）
- Test: `tests/test_status_filter_ui.py`（模板渲染断言）

**Interfaces:**
- Consumes: Task 3 的 `status_filter` 请求参数
- Produces: 前端复选框组合语义（全不勾 → 不过滤 + 4s 顶部轻提示「注意：当前展示结果未过滤非现行规范」）

- [ ] **Step 1: 写失败测试**

Create `tests/test_status_filter_ui.py`:

```python
"""检索状态复选框模板渲染测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def test_search_page_has_status_checkboxes(auth_client, monkeypatch, tmp_path):
    """左侧面板包含「仅现行」「修订中」两个复选框"""
    db_path = tmp_path / "sfu1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.get("/")
    assert "仅现行" in resp.text
    assert "修订中" in resp.text


def test_search_results_with_status_filter_param(auth_client, monkeypatch, tmp_path):
    """/search 携带 status_filter 参数正常返回结果（复选框在左侧面板，不在此断言）"""
    db_path = tmp_path / "sfu2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.get("/search?keyword=钢筋&status_filter=现行")
    assert resp.status_code == 200
    assert "钢筋" in resp.text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_status_filter_ui.py -v`
Expected: FAIL（模板无「仅现行」文案 → AssertionError）

- [ ] **Step 3: 实现复选框 + store + dispatchSearch**

`app/templates/partials/tree_panel.html` 搜索框区（CE 精排复选框 label 之后、`x-data="searchBox()"` 容器内）加一行：

```html
        <label style="display:flex;align-items:center;gap:0.4rem;margin-top:0.3rem;line-height:1">
            <input type="checkbox" x-model="statusCurrent" @change="onStatusChange()"
                   style="width:0.875rem;height:0.875rem;flex:none;margin:0;padding:0">
            <span style="font-size:0.8rem;line-height:0.875rem;white-space:nowrap">仅现行</span>
        </label>
        <label style="display:flex;align-items:center;gap:0.4rem;margin-top:0.3rem;line-height:1">
            <input type="checkbox" x-model="statusRevising" @change="onStatusChange()"
                   style="width:0.875rem;height:0.875rem;flex:none;margin:0;padding:0">
            <span style="font-size:0.8rem;line-height:0.875rem;white-space:nowrap">修订中</span>
        </label>
```

`static/components/tree.js` searchState store 加两个状态 + 一个纯计算 `buildStatusFilter()`（两个组件共享；全不勾返回 `null` 表示不过滤）：

```js
        statusCurrent: true,     // 「仅现行」默认勾选（D3）
        statusRevising: false,
        // 状态过滤组合：仅现行→'现行'；仅现行+修订中→'现行,修订中'；仅修订中→'修订中'；全不勾→null（不过滤+轻提示）
        buildStatusFilter() {
            const c = this.$store.searchState.statusCurrent;
            const r = this.$store.searchState.statusRevising;
            if (c && r) return '现行,修订中';
            if (c) return '现行';
            if (r) return '修订中';
            return null;
        },
```

`static/components/tree.js` `dispatchSearch` 中，`ce_rerank` 参数之后加：

```js
                const sf = this.$store.searchState.buildStatusFilter();
                if (sf) {
                    params.append('status_filter', sf);
                }
```

`static/components/search.js`：
- `searchBox()` 加 getter/setter 代理（与 includeNonClause 同模式）：
  ```js
        get statusCurrent() { return this.$store.searchState.statusCurrent; },
        set statusCurrent(v) { this.$store.searchState.statusCurrent = v; },
        get statusRevising() { return this.$store.searchState.statusRevising; },
        set statusRevising(v) { this.$store.searchState.statusRevising = v; },
  ```
- 加 `onStatusChange()`：全不勾时复用现有 `showSearchToast`（挂 body + 4s 自动移除，**不新写轻提示**）：
  ```js
        onStatusChange() {
            if (!this.statusCurrent && !this.statusRevising) {
                showSearchToast('注意：当前展示结果未过滤非现行规范', 4000);
            }
            this.search();
        },
  ```
- `search()` 中 `ce_rerank` 参数之后追加：
  ```js
            const sf = this.$store.searchState.buildStatusFilter();
            if (sf) params.append('status_filter', sf);
  ```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_status_filter_ui.py tests/test_search.py tests/test_search_routes.py -v`
Expected: PASS

- [ ] **Step 5: 递增静态版本号 + Commit**

`app/templates/base.html`：`<script src="/static/components/tree.js?v=2">` → `?v=3`；`search.js?v=5` → `?v=6`。

```bash
git add app/templates/partials/tree_panel.html static/components/tree.js static/components/search.js app/templates/base.html tests/test_status_filter_ui.py
git commit -m "feat: 检索侧「仅现行/修订中」复选框 + 全不勾选时 4s 顶部轻提示"
```

---

### Task 5: 分类树全局同维多选（前端多选 + 后端多值 OR）

**Files:**
- Modify: `static/components/tree.js:34-47`（`selectFilter` 数组化）、`:49-77`（dispatchSearch 多值参数）、`:78-82`（active 判定 isArray）
- Modify: `app/templates/partials/tree_panel.html:80-98`（节点 active class 用 `includes`）
- Modify: `app/models.py:174-189`（SearchQuery 维度字段 `str → list[str]`）、`:199-213`（QaRequest 维度字段 `str|None → list[str] = []`）
- Modify: `app/routes/search_routes.py:30-36`（维度 Query 参数 `str → list[str] = Query([])`）、`:77-90`（sq 构造直传 list）
- Modify: `app/search/sql_search.py:27-47`（dim_filters/spec_filters 多值 → OR 组）
- Modify: `app/search/hybrid_search.py:112-133`（向量回查多值 OR 组）、`:38-43`（缓存 key 维度元组化）
- Modify: `app/routes/qa_routes.py:166-177`（sq 构造适配 list 维度）
- Modify: `app/templates/partials/result_content.html:22-28`（翻页链接多值参数）
- Test: `tests/test_search_multiselect.py` + 适配现有测试

**Interfaces:**
- Consumes: Task 3 的 status_filter 机制（本 task 沿用同一 OR 组模式）
- Produces: `SearchQuery.dimX_*: list[str]`；同维多值语义 = OR（`(col LIKE ? OR col LIKE ? ...)`）；QA 维度字段同步 list

- [ ] **Step 1: 写失败测试**

Create `tests/test_search_multiselect.py`:

```python
"""分类树同维多选（OR 语义）测试"""
from tests.conftest import setup_search_data
from app.database import get_db, init_db


def _setup_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)  # dim4: 结构专业/建筑专业
    return auth_client


def test_search_dim4_multiple_values_or(auth_client, monkeypatch, tmp_path):
    """同维多选 → OR 语义：结构专业 或 建筑专业 都命中"""
    client = _setup_db(auth_client, monkeypatch, tmp_path, "ms1.db")
    resp = client.get("/search?dim4_specialty=结构专业&dim4_specialty=建筑专业")
    assert "钢筋" in resp.text and "屋面防水" in resp.text


def test_search_dim4_single_value_compat(auth_client, monkeypatch, tmp_path):
    """单值参数向后兼容"""
    client = _setup_db(auth_client, monkeypatch, tmp_path, "ms2.db")
    resp = client.get("/search?dim4_specialty=结构专业")
    assert "钢筋" in resp.text
    assert "屋面防水" not in resp.text


def test_search_dim6_multiple_or(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "ms3.db")
    resp = client.get("/search?dim6_material=模板工程&dim6_material=防水材料")
    assert "模板设计" in resp.text
    assert "屋面防水" in resp.text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_search_multiselect.py -v`
Expected: FAIL（`dim4_specialty: str` 不接受 list → 422 或断言失败）

- [ ] **Step 3: 后端多值化（models + routes + sql_search + hybrid_search + qa_routes）**

`app/models.py`：
- `SearchQuery` 维度字段改为 `list[str] = []`（7 个：dim1_hierarchy/dim1_nature/dim2_stage/dim3_usage/dim4_specialty/dim5_location/dim6_material）
- `QaRequest` 维度字段改为 `list[str] = []`（7 个）

`app/routes/search_routes.py`：
- 维度 Query 参数改 `list[str] = Query([])`（7 个）
- `sq` 构造直接传 list；`all_empty` 判断改为 `all(not v for v in [keyword, *[getattr(sq, f) for f in ('dim1_hierarchy','dim1_nature','dim2_stage','dim3_usage','dim4_specialty','dim5_location','dim6_material')]])`

`app/search/sql_search.py` `_build` 中 dim_filters/spec_filters 循环改：

```python
            def _add_multi(cond_list, params_list, col, vals):
                if not vals:
                    return
                ors = []
                for v in vals:
                    ors.append(f"{col} LIKE ?")
                    params_list.append(f"%{v}%")
                cond_list.append("(" + " OR ".join(ors) + ")")
```

并替换原两段循环为 `_add_multi(conditions, params, "c.dim4_specialty", query.dim4_specialty)` 等 7 处调用。

`app/search/hybrid_search.py` 向量回查：同样用 OR 组（`dim_conditions.append("(" + " OR ".join(f"c.{col} LIKE ?" for _ in vals) + ")")`，`dim_params.extend(f"%{v}%" for v in vals)`）。缓存 key 中维度字段改 `tuple(query.dim4_specialty or ())` 等。

`app/routes/qa_routes.py` sq 构造（L170-177）：维度字段直接传 `body.dim1_hierarchy`（现已是 list）。

- [ ] **Step 4: 适配现有受影响测试**

grep 现有测试中直接构造 `SearchQuery(dim1_hierarchy="...")` 单值的地方，改为 list。受影响文件（以实际 grep 为准）：
- `tests/test_hybrid_search.py`、`tests/test_sql_search.py`（若有）、`tests/test_search_routes.py`、`tests/test_qa_routes.py`

修改方式：单值 `dim4_specialty="结构专业"` → `dim4_specialty=["结构专业"]`。

- [ ] **Step 5: 前端多选（tree.js + tree_panel.html + result_content.html）**

`static/components/tree.js`：
- `selectFilter(dimension, value)` 改为数组 toggle：
  ```js
  selectFilter(dimension, value) {
      const next = { ...this.$store.searchState.filters };
      const arr = Array.isArray(next[dimension]) ? next[dimension].slice() : [];
      const idx = arr.indexOf(value);
      if (idx >= 0) arr.splice(idx, 1); else arr.push(value);
      if (arr.length) next[dimension] = arr; else delete next[dimension];
      this.$store.searchState.filters = next;
      this.dispatchSearch();
  },
  ```
- `dispatchSearch` 中参数构造改：
  ```js
  for (const [k, v] of Object.entries(this.$store.searchState.filters)) {
      if (Array.isArray(v)) v.forEach(x => params.append(k, x));
      else params.append(k, v);
  }
  ```
- store `filters: {}` 不变（各维值为数组）

`app/templates/partials/tree_panel.html` 节点 `active` 判定改（两处，父/子节点）：
```html
:class="{ active: (Array.isArray(activeFilters[dim.key]) ? activeFilters[dim.key].includes(node.value) : activeFilters[dim.key] === node.value) }"
```

`app/templates/partials/result_content.html` 翻页链接：维度参数由单值模板改循环（`{% for d in dim4_specialty %}&dim4_specialty={{ d|urlencode }}{% endfor %}`），需在路由模板上下文传入维度 list。search_routes 模板上下文补传 `dim4_specialty=dim4_specialty` 等（已是 sq 来源的 list）。

- [ ] **Step 6: 运行全部检索/QA/搜索相关测试**

Run: `D:/Python/python.exe -m pytest tests/test_search_multiselect.py tests/test_search.py tests/test_search_routes.py tests/test_hybrid_search.py tests/test_qa_routes.py tests/test_status_filter_search.py tests/test_status_filter_ui.py -v`
Expected: PASS

- [ ] **Step 7: 递增版本号 + Commit**

`base.html`：`tree.js?v=3`→`?v=4`。

```bash
git add app/models.py app/routes/search_routes.py app/search/sql_search.py app/search/hybrid_search.py app/routes/qa_routes.py static/components/tree.js app/templates/partials/tree_panel.html app/templates/partials/result_content.html app/templates/base.html tests/test_search_multiselect.py tests/test_hybrid_search.py tests/test_qa_routes.py
git commit -m "feat: 分类树全局同维多选（OR 语义，SearchQuery/QaRequest 维度字段 list 化）"
```

---

### Task 6: 规范页状态列 + 状态修改接口

**Files:**
- Modify: `app/routes/spec_routes.py`（新增 `PUT /specs/{id}/status`）
- Modify: `app/templates/partials/specs_table.html:3-57`（加状态列 + 行内切换 + 状态样式）
- Test: `tests/test_spec_status.py`

**Interfaces:**
- Consumes: Task 2 的 `status` 字段（已有，值域 现行/废止/修订中）
- Produces:
  - `PUT /specs/{spec_id}/status`（Form `status`）→ 更新 `specifications.status` + 调用 `clear_search_cache()` + 返回更新后状态标签 HTML（`hx-swap`）
  - `specs_table` 状态列三色标签：现行=绿 / 修订中=橙 / 废止=红

- [ ] **Step 1: 写失败测试**

Create `tests/test_spec_status.py`:

```python
"""规范状态修改路由测试"""
from app.database import get_db, init_db


def _setup(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES (?, ?)", ("GB 50204", "混凝土规范"))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_update_spec_status(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "废止"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT status FROM specifications WHERE id = ?", (sid,)).fetchone()
        assert row["status"] == "废止"


def test_update_spec_status_invalid_value_rejected(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "无效状态"})
    assert resp.status_code == 400


def test_specs_table_shows_status_column(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
        conn.execute("UPDATE specifications SET status = '废止' WHERE id = ?", (sid,))
    resp = auth_client.get("/specs/list")
    assert "状态" in resp.text
    assert "废止" in resp.text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_spec_status.py -v`
Expected: FAIL（路由 404 / 模板无状态列）

- [ ] **Step 3: 实现状态路由 + 状态列**

`app/routes/spec_routes.py` 新增：

```python
# 规范状态合法值域（与 spec 生命周期决策一致）
SPEC_STATUS_ALLOWED = {"现行", "废止", "修订中"}


@router.put("/specs/{spec_id}/status")
async def update_spec_status(request: Request, spec_id: int, status: str = Form("")):
    """更新规范状态（现行/废止/修订中），供规范页行内切换与 AI 校验兜底"""
    from fastapi.responses import JSONResponse
    from app.search.hybrid_search import clear_search_cache

    if status not in SPEC_STATUS_ALLOWED:
        return JSONResponse({"detail": f"非法状态: {status}"}, status_code=400)
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
        if not existing:
            return JSONResponse({"detail": "规范不存在"}, status_code=404)
        conn.execute(
            "UPDATE specifications SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (status, spec_id),
        )
    clear_search_cache()  # 状态影响检索过滤，必须清缓存
    return HTMLResponse(f"""<div id="spec-status-{spec_id}" hx-swap-oob="true">
        <span class="spec-status-tag status-{_status_class(status)}">{status}</span>
    </div>""")


def _status_class(status: str) -> str:
    return {"现行": "current", "修订中": "revising", "废止": "obsolete"}.get(status, "current")
```

`app/templates/partials/specs_table.html`：
- `<thead>` 增加 `<th>状态</th>`（条文数 之前）
- 每行 `<td>` 增加状态列（可切换下拉）：
  ```html
  <td>
      <select class="spec-status-select" data-id="{{ s.id }}"
              style="font-size:0.75rem;padding:0.15rem 0.3rem"
              onchange="window.dispatchEvent(new CustomEvent('spec-status-change', {detail:{id:{{ s.id }}, status:this.value}}))">
          {% for st in ['现行', '修订中', '废止'] %}
          <option value="{{ st }}" {% if s.status == st %}selected{% endif %}>{{ st }}</option>
          {% endfor %}
      </select>
  </td>
  ```

`static/components/specs.js`（新建，或用 search.js 全局事件监听）——为最小侵入，在 `static/components/specs.js` 实现监听器并引入 base.html：

```js
document.addEventListener('spec-status-change', (e) => {
    const { id, status } = e.detail;
    const resp = fetch(`/specs/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ status }),
    });
    resp.then(r => {
        if (r.ok) {
            // 轻量成功提示
            const el = document.querySelector(`.spec-status-select[data-id="${id}"]`);
            if (el) el.style.outline = '2px solid var(--pico-primary)';
            setTimeout(() => { if (el) el.style.outline = ''; }, 800);
        } else {
            alert('状态更新失败');
        }
    });
});
```

`app/templates/base.html` 引入 `<script src="/static/components/specs.js?v=1"></script>`；`app.css` 加三色标签样式：

```css
.spec-status-tag { padding:0.15rem 0.5rem; border-radius:4px; font-size:0.75rem; }
.status-current { background:#d8f5d8; color:#1a7a1a; }
.status-revising { background:#ffe8c2; color:#a06500; }
.status-obsolete { background:#fbd8d8; color:#b00000; }
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_spec_status.py tests/test_spec_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/spec_routes.py app/templates/partials/specs_table.html static/components/specs.js app/templates/base.html static/app.css tests/test_spec_status.py
git commit -m "feat: 规范页状态列 + PUT /specs/{id}/status 行内切换（值域校验+清检索缓存）"
```

---

### Task 7: 导入校核后端（validate-version + 入库打标/反向联动）

**Files:**
- Modify: `app/ai/prompts.py`（新增 `build_version_check_prompt`）
- Modify: `app/routes/import_routes.py`（新增 `POST /import/validate-version`；`upload_file` 加 Form `status`/`replaced_by_code`；`_process_import`/`_process_import_phase2` 传 status；入库写 status + 反向联动）
- Test: `tests/test_validate_version.py`、`tests/test_import_status.py`

**Interfaces:**
- Consumes: Task 1 的 `normalize_spec_code`/`detect_nature`/`detect_hierarchy`；Task 2 的 `replace_by_spec_id` 列
- Produces:
  - `POST /import/validate-version`，body JSON `{code, title}` → JSON `{status, replaced_by_code, corrected_code, corrected_title, ai_available}`；AI 不可用/异常 → `{status:'现行', ai_available:false, ...}`（不抛错）
  - `upload_file` 新增 Form：`status`（默认现行）、`replaced_by_code`（可空）
  - 入库时 `code` 过 `normalize_spec_code`；写 `status`；`replaced_by_code` 命中库中已存在 code → 反写旧规范 `status='废止'` + `replace_by_spec_id=新id`

- [ ] **Step 1: 写失败测试**

Create `tests/test_validate_version.py`（mock AI 后端返回固定结果，验证解析与兜底）:

```python
"""导入版本校核接口测试（AI 输出解析 + 不可用兜底）"""
from unittest.mock import MagicMock
from app.database import get_db, init_db


class _FakeResp:
    def __init__(self, content): self.content, self.success, self.error = content, True, None


def _mock_backend(monkeypatch, content):
    """patch app.ai.cli_client.get_backend（validate_version 函数内 from cli_client import）"""
    import app.ai.cli_client as cc
    fake = MagicMock()
    fake.is_available.return_value = True
    fake.ask = MagicMock(return_value=_FakeResp(content))
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    return fake


def _setup_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    return auth_client


def test_validate_version_returns_status(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "vv1.db")
    _mock_backend(monkeypatch, '{"status":"废止","replaced_by_code":"GB 50010-2015","corrected_code":"GBT 50107-2010","corrected_title":"燃气工程制图标准","ai_available":true}')
    resp = client.post("/import/validate-version", json={"code": "GBT 50107-2010", "title": "燃气工程制图标准"})
    data = resp.json()
    assert data["status"] == "废止"
    assert data["replaced_by_code"] == "GB 50010-2015"
    # corrected_code 应已被 normalize_spec_code 兜底规范
    assert data["corrected_code"] == "GB/T 50107-2010"


def test_validate_version_ai_unavailable_fallback(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "vv2.db")
    import app.ai.cli_client as cc
    fake = MagicMock()
    fake.is_available.return_value = False
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    resp = client.post("/import/validate-version", json={"code": "GB 50010", "title": "x"})
    data = resp.json()
    assert data["status"] == "现行"
    assert data["ai_available"] is False


def test_validate_version_ai_exception_fallback(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "vv3.db")
    import app.ai.cli_client as cc
    fake = MagicMock()
    fake.is_available.return_value = True
    fake.ask.side_effect = RuntimeError("timeout")
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    resp = client.post("/import/validate-version", json={"code": "GB 50010", "title": "x"})
    data = resp.json()
    assert data["status"] == "现行"
    assert data["ai_available"] is False
```

Create `tests/test_import_status.py`（入库打标 + 反向联动）：

```python
"""导入入库时 status 打标与 replace_by 反向联动测试"""
from app.database import get_db, init_db


def test_import_phase2_writes_status(monkeypatch, tmp_path):
    """入库时按表单 status 写入，code 过 normalize"""
    db_path = tmp_path / "is1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    import app.routes.import_routes as ir
    ir.progress_store["task1"] = {"status": "processing", "progress": 0}  # phase2 内部 update 依赖
    ir.progress_store["task1"]["md_text"] = "# 第1章\n5.1.1 条文内容测试\n"
    from app.routes.import_routes import _process_import_phase2
    _process_import_phase2(
        "task1", "# 第1章\n5.1.1 条文内容测试\n", "GBT 50010-2010", "混凝土规范",
        str(tmp_path / "f.md"), "hash1",
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT code, status FROM specifications WHERE file_hash = 'hash1'"
        ).fetchone()
    assert row["code"] == "GB/T 50010-2010"   # 归一化
    assert row["status"] == "现行"             # 表单未传 → 默认现行


def test_import_reverse_link_obsolete_spec(monkeypatch, tmp_path):
    """replaced_by_code 命中库中旧规范 → 反写旧规范废止 + 关联"""
    db_path = tmp_path / "is2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
            ("GB 50010-2011", "旧规范", "现行"),
        )
    import app.routes.import_routes as ir
    ir.progress_store["task2"] = {"status": "processing", "progress": 0}
    ir.progress_store["task2"]["md_text"] = "# 第1章\n5.1.1 新条文内容\n"
    from app.routes.import_routes import _process_import_phase2
    # 导入新规范 GB 50010-2015，界面校核出它替代 GB 50010-2011 → 传 replaced_by_code
    _process_import_phase2(
        "task2", "# 第1章\n5.1.1 新条文内容\n", "GB 50010-2015", "混凝土新规范",
        str(tmp_path / "f2.md"), "hash2", status="现行", replaced_by_code="GB 50010-2011",
    )
    with get_db() as conn:
        new = conn.execute("SELECT id FROM specifications WHERE code = 'GB 50010-2015'").fetchone()
        old = conn.execute(
            "SELECT status, replace_by_spec_id FROM specifications WHERE code = 'GB 50010-2011'"
        ).fetchone()
    assert old["status"] == "废止"
    assert old["replace_by_spec_id"] == new["id"]
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_validate_version.py tests/test_import_status.py -v`
Expected: FAIL（路由 404 / 无 status 写库逻辑）

- [ ] **Step 3: 实现 prompt + validate-version + 入库改造**

`app/ai/prompts.py` 新增：

```python
def build_version_check_prompt(code: str, title: str) -> str:
    """构造规范版本/命名校核 prompt，要求严格 JSON 输出。

    输出字段：
    - status: 现行 | 废止 | 修订中（判断规范当前是否有效）
    - replaced_by_code: 被替代的规范编号（废止/修订中时若有），无则空串
    - corrected_code / corrected_title: 规范化命名，与输入一致时原样返回
    命名规则：代号[/T] 顺序号-发布年份，顺序号与年份间半角短横线 -，
    代号与顺序号间半角空格。推荐性规范代号带 /T（GB/T、JGJ/T、CJ/T），
    用户常把 GB/T 误写为 GBT、JGJ/T 误写为 JGJT，需识别并规范回填。
    示例：GB 50010-2010（强制性）、GB/T 50107-2010《燃气工程制图标准》（推荐性）。
    """
    return (
        "你是工程规范编号与版本校核助手。请对给定规范编号和名称判断其有效性状态，"
        "并校核编号、名称是否符合规范命名规则。\n"
        f"输入规范编号：{code}\n"
        f"输入规范名称：{title}\n"
        "只输出一个 JSON 对象，不要任何其他文字：\n"
        '{"status": "现行|废止|修订中", "replaced_by_code": "被替代编号或空串", '
        '"corrected_code": "规范化编号", "corrected_title": "规范化名称"}'
    )
```

`app/routes/import_routes.py` 新增（放在 `parse_filename` 路由附近）：

```python
@router.post("/import/validate-version")
async def validate_version(body: dict):
    """AI 校核规范版本与命名：返回 {status, replaced_by_code, corrected_code, corrected_title, ai_available}

    AI 不可用/异常/输出非 JSON → 兜底返回 status=现行、ai_available=False（不抛错）。
    corrected_code 一律再过 normalize_spec_code 兜底（D19 第一级正则）。
    """
    from app.ai.prompts import build_version_check_prompt
    from app.ai.cli_client import get_backend

    code = (body.get("code") or "").strip()
    title = (body.get("title") or "").strip()
    fallback = {
        "status": "现行", "replaced_by_code": "", "ai_available": False,
        "corrected_code": normalize_spec_code(code), "corrected_title": title,
    }
    if not code:
        return fallback
    try:
        backend = get_backend()
        if not backend.is_available():
            return fallback
        import json as _json
        from app.config import WORKSPACE_DIR
        prompt = build_version_check_prompt(code, title)
        resp = await backend.ask(prompt, context="", system_prompt="", work_dir=WORKSPACE_DIR)
        if not (resp.success and resp.content.strip()):
            return fallback
        parsed = _json.loads(resp.content)
        return {
            "status": parsed.get("status", "现行") if parsed.get("status") in ("现行", "废止", "修订中") else "现行",
            "replaced_by_code": (parsed.get("replaced_by_code") or "").strip(),
            "corrected_code": normalize_spec_code(parsed.get("corrected_code") or code),
            "corrected_title": (parsed.get("corrected_title") or title).strip(),
            "ai_available": True,
        }
    except Exception:
        return fallback
```

`upload_file` 签名加 `status: str = Form("现行")`、`replaced_by_code: str = Form("")`；传入 `_process_import`（background_tasks 调用处 L116-118）。

`_process_import` 签名加 `status: str = "现行"`、`replaced_by_code: str = ""`：
- MD 路径（L154）调用 `_process_import_phase2(..., status=status, replaced_by_code=replaced_by_code)`（已传参）
- PDF/OCR 路径：status/replaced_by_code 存入 `progress_store`（L181-183 附近追加）：
  ```python
  progress_store[task_id]["status"] = status
  progress_store[task_id]["replaced_by_code"] = replaced_by_code
  ```

`confirm_review`（L584-596）：从 task 读取并透传 phase2：
```python
    status = task.get("status", "现行")
    replaced_by_code = task.get("replaced_by_code", "")
    ...
    background_tasks.add_task(
        _process_import_phase2, task_id, content, title, code, file_path, file_hash,
        status, replaced_by_code,
    )
```

`_process_import_phase2` 签名加 `status: str = "现行"`、`replaced_by_code: str = ""`；入库段改造：

```python
        # Step 3: 规范级分类（code 先归一化，再 detect 层级/性质）
        code = normalize_spec_code(code) or Path(file_path).stem
        dim1_hierarchy = detect_hierarchy(code)
        dim1_nature = detect_nature(code)
```

INSERT（原 L281-289）改为带 status，且 INSERT 后做反向联动：

```python
        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature,
               dim2_stage, dim3_usage, source_path, output_dir, file_hash, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code or Path(file_path).stem, title or Path(file_path).stem,
             dim1_hierarchy, dim1_nature,
             dim2_stage, dim3_usage,
             file_path, output_dir, file_hash, status),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 反向联动：replaced_by_code 命中库中旧规范 → 反写旧规范废止 + 关联
        if replaced_by_code:
            norm_old = normalize_spec_code(replaced_by_code)
            old = conn.execute(
                "SELECT id FROM specifications WHERE code = ?", (norm_old,)
            ).fetchone()
            if old:
                conn.execute(
                    "UPDATE specifications SET status = '废止', replace_by_spec_id = ? WHERE id = ?",
                    (spec_id, old["id"]),
                )
```

`_detect_nature`/`_detect_hierarchy` 调用点已由 Task 1 改为 `detect_nature`/`detect_hierarchy`。

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_validate_version.py tests/test_import_status.py tests/test_import.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/prompts.py app/routes/import_routes.py tests/test_validate_version.py tests/test_import_status.py
git commit -m "feat: 导入版本校核 validate-version + 入库状态打标与 replace_by 反向联动"
```

---

### Task 8: 导入校核前端（校核按钮 + 标签下拉 + 规范化回填）

**Files:**
- Modify: `app/templates/partials/tree_panel.html:22-52`（导入对话框加校核按钮 + 结果容器 + 状态下拉 + 被替代编号 input）
- Modify: `static/components/import.js`（`markManual` 相关加校核逻辑：`runVersionCheck`、渲染标签、`applySuggestion`/`ignoreSuggestion`、提交带 status/replaced_by_code）
- Test: `tests/test_import_ui.py`（模板渲染断言：校核按钮与状态下拉存在）

**Interfaces:**
- Consumes: Task 7 的 `POST /import/validate-version` 响应结构
- Produces: 导入表单提交时携带 `status`/`replaced_by_code` 字段（供 Task 7 upload_file 读取）；「应用/忽略」回填逻辑

- [ ] **Step 1: 写失败测试**

Create `tests/test_import_ui.py`:

```python
"""导入对话框校核 UI 模板渲染测试"""
from app.database import init_db


def test_import_dialog_has_version_check_button(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "iui1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.get("/")
    assert "校核有效性" in resp.text
    assert "仅现行" in resp.text  # 检索侧复选框同页存在（与 Task 4 并存不冲突）
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_import_ui.py -v`
Expected: FAIL（模板无「校核有效性」）

- [ ] **Step 3: 实现模板 + import.js**

`app/templates/partials/tree_panel.html` 导入对话框，在「规范名称」input 之后加：

```html
                    <button type="button" class="outline" style="width:100%;margin-top:0.3rem"
                            @click="runVersionCheck()" :disabled="checking">
                        <span x-text="checking ? '校核中...' : '🔍 校核有效性'"></span>
                    </button>
                    <div id="version-check-result" x-show="checkResult" x-cloak style="margin-top:0.5rem">
                        <div style="display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
                            <span style="font-size:0.8rem">状态：</span>
                            <select x-model="checkResult.status" style="font-size:0.8rem;padding:0.15rem 0.3rem;width:auto">
                                <option value="现行">现行</option>
                                <option value="修订中">修订中</option>
                                <option value="废止">废止</option>
                            </select>
                            <small x-show="!checkResult.ai_available" style="color:var(--pico-del-color, #c44);font-size:0.75rem">⚠️ AI 校验不可用，请手动确认状态</small>
                        </div>
                        <template x-if="checkResult.corrected && (checkResult.corrected.code !== codeInput || checkResult.corrected.title !== titleInput)">
                            <div style="margin-top:0.3rem;font-size:0.8rem">
                                <small>AI 建议：<code x-text="checkResult.corrected.code"></code> <span x-text="checkResult.corrected.title"></span></small>
                                <button type="button" class="outline" style="font-size:0.75rem;padding:0.1rem 0.4rem" @click="applySuggestion()">应用</button>
                                <button type="button" class="secondary" style="font-size:0.75rem;padding:0.1rem 0.4rem" @click="checkResult.corrected = null">忽略</button>
                            </div>
                        </template>
                        <label style="margin-top:0.4rem;font-size:0.8rem">
                            被替代编号
                            <input type="text" x-model="checkResult.replacedBy" placeholder="如 GB 50010-2011（可留空）"
                                   style="font-size:0.8rem;padding:0.2rem 0.4rem">
                        </label>
                    </div>
                    <small x-show="contentEdited && checkResult" style="color:var(--pico-muted-color);font-size:0.75rem">
                        内容已修改，建议重新校核
                    </small>
```

表单 input 需绑定 `x-model="codeInput"` / `x-model="titleInput"`（现有 `markManual` 保留），提交时在 `handleUpload` 的 FormData 里追加：

```js
fd.append('status', this.checkResult ? this.checkResult.status : '现行');
fd.append('replaced_by_code', (this.checkResult && this.checkResult.replacedBy) || '');
```

`static/components/import.js` 的 `importDialog()` 数据加：

```js
        codeInput: '',
        titleInput: '',
        checking: false,
        checkResult: null,   // {status, replacedBy, ai_available, corrected: {code,title}|null}
        contentEdited: false,
        markManual(field) {
            if (field === 'code') this.codeInput = document.querySelector('input[name=code]').value;
            if (field === 'title') this.titleInput = document.querySelector('input[name=title]').value;
            this.contentEdited = true;  // 修改后提示重新校核（不自动触发）
        },
        async runVersionCheck() {
            const code = this.codeInput.trim();
            const title = this.titleInput.trim();
            if (!code || !title) { alert('请先填写规范编号与名称'); return; }
            this.checking = true;
            try {
                const resp = await fetch('/import/validate-version', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code, title }),
                });
                const data = await resp.json();
                const corrected = (data.corrected_code && data.corrected_code !== code)
                    || (data.corrected_title && data.corrected_title !== title)
                    ? { code: data.corrected_code, title: data.corrected_title } : null;
                this.checkResult = {
                    status: data.status, replacedBy: data.replaced_by_code || '',
                    ai_available: data.ai_available, corrected,
                };
                this.contentEdited = false;
            } catch (e) {
                alert('校核失败，请稍后重试');
            } finally {
                this.checking = false;
            }
        },
        applySuggestion() {
            if (!this.checkResult || !this.checkResult.corrected) return;
            document.querySelector('input[name=code]').value = this.checkResult.corrected.code;
            document.querySelector('input[name=title]').value = this.checkResult.corrected.title;
            this.codeInput = this.checkResult.corrected.code;
            this.titleInput = this.checkResult.corrected.title;
            this.checkResult.corrected = null;
            this.contentEdited = false;
        },
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_import_ui.py -v`
Expected: PASS

- [ ] **Step 5: 递增版本号 + Commit**

`base.html`：`import.js` 加 `?v=1`。

```bash
git add app/templates/partials/tree_panel.html static/components/import.js app/templates/base.html tests/test_import_ui.py
git commit -m "feat: 导入对话框「校核有效性」手动按钮 + 状态下拉/被替代编号/规范化回填应用忽略"
```

---

### Task 9: QA 状态过滤（status_filter 传参 + qa_panel 复选框）

**Files:**
- Modify: `app/models.py:199-213`（`QaRequest` 加 `status_filter: str = ""`）
- Modify: `app/routes/qa_routes.py:198-204`（status_allow 由 UI 覆盖；全不勾 → include_invalid）
- Modify: `app/templates/partials/qa_panel.html`（加「仅现行/修订中」复选框）
- Modify: `static/components/qa.js:22-30`（send 携带 status_filter）
- Test: `tests/test_qa_status_filter.py`

**Interfaces:**
- Consumes: Task 3 检索过滤；现有 `filter_by_metadata`/`get_qa_str("meta.status_allow")`
- Produces: `QaRequest.status_filter`（逗号分隔，空 = 不过滤）；`status_allow` 逻辑：`status_filter` 非空 → 用它；空 → `include_invalid=True`

- [ ] **Step 1: 写失败测试**

Create `tests/test_qa_status_filter.py`:

```python
"""QA 状态过滤测试（status_filter 覆盖默认 status_allow）"""
from unittest.mock import MagicMock
from app.database import get_db, init_db


class _FakeResp:
    def __init__(self, content="回答"): self.content, self.success, self.error = content, True, None


def _mock_backend(monkeypatch):
    """patch cli_client.get_backend（qa_ask 函数内 from cli_client import）+ 替换 _rerank_scored"""
    import app.ai.cli_client as cc
    import app.routes.qa_routes as qr
    fake = MagicMock()
    fake.is_available.return_value = True
    fake.ask = MagicMock(return_value=_FakeResp())
    fake.command = "fake"
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    # rerank 不真正加载模型（替换 qa_routes 模块内全局名）
    monkeypatch.setattr(qr, "_rerank_scored", lambda q, c: ([(d, 0.9) for d in c]))
    return fake


def _setup(auth_client, monkeypatch, tmp_path):
    from tests.conftest import setup_search_data
    db_path = tmp_path / "qsf.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
            ("GBJ 10-1989", "旧规范", "废止"),
        )
        sid = conn.execute("SELECT id FROM specifications WHERE code='GBJ 10-1989'").fetchone()["id"]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (sid, "1.0.1", "旧条文", "旧条文内容。"),
        )


def test_qa_status_filter_only_current_excludes_obsolete(auth_client, monkeypatch, tmp_path):
    _mock_backend(monkeypatch)
    _setup(auth_client, monkeypatch, tmp_path)
    # status_filter=现行 → 废止条文不进候选 → 上下文不含旧条文 → 回答不含它
    resp = auth_client.post("/qa/ask", json={
        "question": "混凝土施工要求", "status_filter": "现行",
    })
    assert resp.status_code == 200
    # 断言 metadata 过滤生效：通过 trace 不可直接断言，改断言 sources 不含旧条文
    assert all("GBJ 10-1989" not in (s.get("code") or "") for s in resp.json().get("sources", []))


def test_qa_status_filter_empty_passes_invalid(auth_client, monkeypatch, tmp_path):
    _mock_backend(monkeypatch)
    _setup(auth_client, monkeypatch, tmp_path)
    # status_filter 为空 → include_invalid 语义（不过滤），废止条文可进入
    resp = auth_client.post("/qa/ask", json={
        "question": "旧规范规定", "status_filter": "",
    })
    assert resp.status_code == 200
    # 无法保证旧条文一定命中 → 断言请求成功且 status_filter 被接受即可
    assert resp.json().get("answer")
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_status_filter.py -v`
Expected: FAIL（`QaRequest` 无 `status_filter` → 422）

- [ ] **Step 3: 实现**

`app/models.py` `QaRequest` 加：

```python
    # 状态过滤：逗号分隔白名单（如 '现行' / '现行,修订中'）。空 = 不过滤（含废止）。
    status_filter: str = ""
```

`app/routes/qa_routes.py` 元数据过滤段（L198-204）改为：

```python
    # ② 元数据过滤（RRF 后、CrossEncoder 前；默认过滤废止/已替代规范）
    # UI 传 status_filter 覆盖默认 status_allow；全不勾（空）→ 放行非现行
    if body.status_filter.strip():
        status_allow = tuple(
            s.strip() for s in body.status_filter.split(",") if s.strip()
        )
        eff_include_invalid = body.include_invalid
    else:
        status_allow = tuple(
            s.strip() for s in get_qa_str("meta.status_allow").split(",") if s.strip()
        )
        eff_include_invalid = True  # 全不勾 → 不过滤状态
    candidates = filter_by_metadata(
        candidates, eff_include_invalid, status_allow=status_allow,
    )
```

`app/templates/partials/qa_panel.html` 顶部（原文摘抄复选框下方）加：

```html
    <div style="display:flex;gap:0.75rem;margin-bottom:0.35rem;font-size:0.75rem">
        <label style="display:flex;align-items:center;gap:0.25rem;margin:0;cursor:pointer">
            <input type="checkbox" x-model="statusCurrent" @change="onStatusChange()" style="width:0.875rem;height:0.875rem">
            仅现行
        </label>
        <label style="display:flex;align-items:center;gap:0.25rem;margin:0;cursor:pointer">
            <input type="checkbox" x-model="statusRevising" @change="onStatusChange()" style="width:0.875rem;height:0.875rem">
            修订中
        </label>
        <small x-show="!statusCurrent && !statusRevising" style="color:var(--pico-del-color, #c44)">
            ⚠️ 未过滤非现行规范
        </small>
    </div>
```

`static/components/qa.js` `qaView` 数据加 `statusCurrent: true, statusRevising: false`；`send()` body 加：

```js
                    body: JSON.stringify({
                        question: q, mode: this.mode, ...filters,
                        status_filter: this.buildStatusFilter(),
                    }),
```

`buildStatusFilter()`（qa.js 内，与 tree.js 语义一致）：

```js
        buildStatusFilter() {
            if (this.statusCurrent && this.statusRevising) return '现行,修订中';
            if (this.statusCurrent) return '现行';
            if (this.statusRevising) return '修订中';
            return '';
        },
        onStatusChange() {},
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_status_filter.py tests/test_qa_routes.py -v`
Expected: PASS

- [ ] **Step 5: 递增版本号 + Commit**

`base.html`：`qa.js?v=14` → `?v=15`。

```bash
git add app/models.py app/routes/qa_routes.py app/templates/partials/qa_panel.html static/components/qa.js app/templates/base.html tests/test_qa_status_filter.py
git commit -m "feat: QA 状态过滤——status_filter 覆盖 status_allow，全不勾放行非现行，qa_panel 复选框"
```

---

### Task 10: 三处状态标签与替代提示语（详情弹窗/检索列表/QA 高亮）

**Files:**
- Modify: `app/search/sql_search.py:89-98`（data_sql SELECT 加 replace_by 字段与 LEFT JOIN）
- Modify: `app/search/hybrid_search.py:136-143`（向量回查 SELECT 同）
- Modify: `app/routes/search_routes.py:122-129`（clause_detail SELECT 加 status/replace_by）
- Modify: `app/templates/partials/clause_detail.html:19-40`（footer 状态标签 + 替代/废止提示）
- Modify: `app/templates/partials/result_content.html:4-16`（结果条目状态标签）
- Modify: `app/routes/qa_routes.py:70-82`（`_extract_sources` 带 status）
- Modify: `static/components/qa.js`（`renderMarkdown` 链接后加废止/替代警告）
- Test: `tests/test_lifecycle_display.py`

**Interfaces:**
- Consumes: Task 2 `replace_by_spec_id`；Task 9 QA 过滤链路
- Produces: 候选 dict 增加 `replace_by_code`/`replace_by_title` 字段；clause_detail dict 增加 `spec_status`/`replace_by_code`/`replace_by_title`；QA sources 带 `status`

- [ ] **Step 1: 写失败测试**

Create `tests/test_lifecycle_display.py`:

```python
"""三处状态标签与替代提示语渲染测试"""
from app.database import get_db, init_db


def _setup_with_replaced(conn):
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
        ("GB 50010-2011", "旧混凝土规范", "废止"),
    )
    old_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
        ("GB 50010-2015", "混凝土结构工程施工质量验收规范", "现行"),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE specifications SET replace_by_spec_id = ? WHERE id = ?",
        (new_id, old_id),
    )
    conn.execute(
        "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
        (old_id, "1.0.1", "旧条文", "旧条文内容。"),
    )
    return old_id


def test_clause_detail_shows_replacement_notice(auth_client, monkeypatch, tmp_path):
    """详情弹窗底部显示「已被《新编号》替代」提示"""
    db_path = tmp_path / "ld1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        old_id = _setup_with_replaced(conn)
    resp = auth_client.get(f"/clause/{old_id}")
    assert "已被" in resp.text
    assert "GB 50010-2015" in resp.text


def test_clause_detail_shows_status_tag(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ld2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        old_id = _setup_with_replaced(conn)
    resp = auth_client.get(f"/clause/{old_id}")
    assert "废止" in resp.text


def test_search_results_show_obsolete_tag(auth_client, monkeypatch, tmp_path):
    """检索结果列表来自废止规范 → 显示「已废止」标识"""
    db_path = tmp_path / "ld3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        old_id = _setup_with_replaced(conn)
    resp = auth_client.get("/search?all=1&status_filter=")
    assert "已废止" in resp.text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_lifecycle_display.py -v`
Expected: FAIL（clause_detail 无 replace_by 信息 / 模板无提示）

- [ ] **Step 3: 后端 SELECT 扩展**

`app/search/sql_search.py` data_sql 改为（加 LEFT JOIN 取替代者）：

```python
        data_sql = f"""
            SELECT c.*, s.code as spec_code, s.title as spec_title,
                   s.status as spec_status, s.dim1_nature as spec_nature,
                   s.replace_by_spec_id, r.code as replace_by_code, r.title as replace_by_title
            FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            LEFT JOIN specifications r ON r.id = s.replace_by_spec_id
            {joins}
            {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
```

`app/search/hybrid_search.py` 向量回查 SELECT（L137-143）同样加 `s.replace_by_spec_id, r.code as replace_by_code, r.title as replace_by_title`，并加 `LEFT JOIN specifications r ON r.id = s.replace_by_spec_id`。

`app/routes/search_routes.py` `clause_detail` 查询改：

```python
        clause = conn.execute(
            """SELECT c.*, s.code as spec_code, s.title as spec_title,
                      s.status as spec_status, s.replace_by_spec_id,
                      r.code as replace_by_code, r.title as replace_by_title
               FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               LEFT JOIN specifications r ON r.id = s.replace_by_spec_id
               WHERE c.id = ?""",
            (clause_id,),
        ).fetchone()
```

- [ ] **Step 4: 详情弹窗 + 检索列表模板**

`app/templates/partials/clause_detail.html` footer（`dim-tags` 之后、来源 `small` 之前）加：

```html
            <div style="margin-top:0.6rem;display:flex;gap:0.4rem;flex-wrap:wrap;align-items:center">
                {% if clause.spec_status %}
                <span class="spec-status-tag status-{{ 'current' if clause.spec_status == '现行' else 'revising' if clause.spec_status == '修订中' else 'obsolete' }}"
                      style="font-size:0.75rem">{{ clause.spec_status }}</span>
                {% endif %}
            </div>
            {% if clause.spec_status == '废止' or clause.replace_by_code %}
            <div style="margin-top:0.5rem;padding:0.5rem;background:#fdecec;border:1px solid #f2c4c4;border-radius:6px;font-size:0.8rem;color:#b00000">
                {% if clause.replace_by_code %}
                本规范已被《{{ clause.replace_by_code }} {{ clause.replace_by_title or '' }}》替代，请以新规范的规定为准
                {% else %}
                本规范已废止，请查阅新版规范
                {% endif %}
            </div>
            {% endif %}
```

`app/templates/partials/result_content.html` 结果条目 `dim-tags` 上方（`clause-content` 之后）加：

```html
        {% if r.spec_status %}
        <div style="margin-top:0.25rem;display:flex;gap:0.4rem;align-items:center">
            <span class="spec-status-tag status-{{ 'current' if r.spec_status == '现行' else 'revising' if r.spec_status == '修订中' else 'obsolete' }}"
                  style="font-size:0.7rem">{{ r.spec_status }}</span>
            {% if r.spec_status == '废止' %}<span style="font-size:0.7rem;color:#b00000">已废止</span>{% endif %}
            {% if r.replace_by_code %}<span style="font-size:0.7rem;color:#a06500">已被《{{ r.replace_by_code }}》替代</span>{% endif %}
        </div>
        {% endif %}
```

- [ ] **Step 5: QA 来源带状态 + 前端高亮**

`app/routes/qa_routes.py` `_extract_sources`（L70-82）改为带 `status`：

```python
def _extract_sources(clauses: list[dict]) -> list[dict]:
    """从实际进入上下文的条文提取去重引文来源（含 clause_id / status 供前端弹详情与高亮）"""
    seen = set()
    sources = []
    for r in clauses:
        key = (r.get("spec_code"), r.get("clause_no"))
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            sources.append({
                "code": key[0], "clause_no": key[1],
                "clause_id": r.get("id"),
                "status": r.get("spec_status") or "",
                "replace_by_code": r.get("replace_by_code") or "",
            })
    return sources
```

`static/components/qa.js` `renderMarkdown` 中链接生成处（L78-84）加状态警告（src 有 status 时）：

```js
                        if (src && src.clause_id) {
                            let warn = '';
                            if (src.status === '废止' || src.replace_by_code) {
                                warn = '<span style="color:#b00000;font-size:0.7rem;margin-left:0.25rem">⚠️' +
                                    (src.replace_by_code ? `已被《${src.replace_by_code}》替代` : '已废止') + '</span>';
                            }
                            return `<a href="javascript:void(0)" style="color:var(--pico-primary);text-decoration:underline;cursor:pointer" onclick="window.dispatchEvent(new CustomEvent('view-clause',{detail:{id:${src.clause_id}}}))">${part}</a>${warn}`;
                        }
```

- [ ] **Step 6: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_lifecycle_display.py tests/test_qa_routes.py tests/test_search.py tests/test_hybrid_search.py -v`
Expected: PASS

- [ ] **Step 7: 递增版本号 + Commit**

`base.html`：`qa.js?v=15` → `?v=16`。

```bash
git add app/search/sql_search.py app/search/hybrid_search.py app/routes/search_routes.py app/routes/qa_routes.py app/templates/partials/clause_detail.html app/templates/partials/result_content.html static/components/qa.js app/templates/base.html tests/test_lifecycle_display.py
git commit -m "feat: 三处状态标签与替代提示语（详情弹窗/检索列表/QA 高亮）"
```

---

## 验收清单（P0 完成标准）

- [ ] `D:/Python/python.exe -m pytest tests/ -v` 全量通过（既有 400+ + 新增全部）
- [ ] 导入：乱命名文件（`GBT 50010—2010 混凝土规范.pdf`）→ 校核按钮 → AI 建议 `GB/T 50010-2010` → 应用 → 入库 code 规范、状态正确
- [ ] 检索：默认勾选「仅现行」→ 废止规范条文不出现；取消两个复选框 → 出现全部 + 4s 顶部轻提示
- [ ] 分类树：同维点选多项（如 结构专业+建筑专业）→ 两专业结果都返回
- [ ] QA：默认仅现行；取消后引用废止规范条文 → 输出 ⚠️ 已废止/被替代警告
- [ ] 规范页：状态列三色标签可行内切换，切换后检索过滤即时生效
- [ ] 详情弹窗：被替代规范条文底部显示替代提示语
- [ ] 人工兜底：AI 不可用时导入标签手动选状态仍可入库
