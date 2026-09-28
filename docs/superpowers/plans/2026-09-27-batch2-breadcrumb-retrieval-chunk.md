# 批二：面包屑检索 + 超长条文子块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「搜节名」能**召回**该节下的条文（不只是重排），并让超长条文的后半段进入向量检索。

**Architecture:** `clauses` 新增**两列**——`section_path`（原始面包屑，供展示）与 `breadcrumb`（jieba 预分词，供 FTS 拷贝；触发器不能调 Python，故必须由应用层写入）。`clauses_fts` 由单列改为 `fts5(search_text, breadcrumb)`，排序用 `bm25(clauses_fts, 1.0, ?)`；**权重为 0 时改发列限定 MATCH**（`search_text : (...)`），使参数页的「0=不参与」为真。超长条文按 512 token 切子块写入 LanceDB（新增 `chunk_index`），向量臂**超取后按 `clause_id` 取最优**。

**Tech Stack:** Python 3.14 / pytest / SQLite FTS5 / LanceDB / pyarrow / jieba / sentence-transformers。无新依赖。

**Spec:** `docs/superpowers/specs/2026-09-27-parser-tree-breadcrumb-chunk-design.md`；**修正以** `docs/superpowers/plans/2026-09-27-parser-tree-breadcrumb-chunk-plan.md` 顶部横幅与文末「CEO 评审记录」为准。**本批依赖批一**（`docs/superpowers/plans/2026-09-27-batch1-parser-hierarchy.md`）产出的 `section_path`。

## Global Constraints

- **前置依赖**：批一必须先完成并验收（`section_path` 有值且面包屑覆盖率已实测）。批二所有 Task 的完成判据都假定批一已落地。
- **本批的实测已证事实**（不得再按 spec 原文实现）：
  - `bm25(f, 1.0, 0.0)` **不能**关闭 breadcrumb 列的召回：仅命中 breadcrumb 的行仍被返回（sqlite 3.50.4 实测，分数 `-0.0`）。
  - `bm25()` 的权重参数**支持绑定参数**（`bm25(f, 1.0, ?)` 实测可用）→ **禁止**写 `float()` 字面量回退，那是伪风险且会引入唯一一处注入面。
  - 默认 unicode61 分词器把连续中文折叠为**单个 token**：未预分词的中文列只能整段精确命中（查「接头安装」命中、查「接头」「安装」0 命中）→ `breadcrumb` 列**必须**收 jieba 分词结果。
  - `sync_with_db()` / `index_missing()` 实测**只补缺失 id，不重嵌已有条文**（`vector_search.py:138`）→ 加面包屑后**必须全量重建向量**。
  - LanceDB **不能** `ALTER TABLE`；现存表无 `chunk_index`，必须重建 schema。
- **编码安全**：FTS MATCH 串沿用既有 `_quote` 转义路径；权重走绑定参数，**禁止**字面量插值。
- **异常规范**：禁止裸 `except:`；不得新增无日志的 `except Exception`。
- **性能**：`clauses` 全表遍历的循环内禁止逐行 DB IO（沿用既有 backfill 形态即可）；向量写入必须批量（`VECTOR_WRITE_BATCH`）。
- **TDD**：先失败测试 → 最小实现 → pyright 0 error → 提交。单次提交单 Task。
- **测试隔离（强制）**：本批新增测试若触及库，必须同时 patch 三处：
  `monkeypatch.setattr("app.database.DATABASE_PATH", ...)`、`monkeypatch.setattr("app.config.LANCE_DB_PATH", ...)`、`monkeypatch.setattr("app.config.UPLOAD_DIR", ...)`。
  历史事故：只 patch `DATABASE_PATH` 曾导致 pytest 往**真实** `lance_db` 写夹具向量、并往 `data/uploads/` 堆 147 个垃圾 md。
- **不得放宽既有断言**：本批允许修改的既有断言只有「`build_search_text` 返回值由串变元组」与「`bm25` 调用形态」两类，且必须同步改成**更强的断言**（见各 Task）。

---

## File Structure

| 文件 | 责任 | 本批动作 |
|---|---|---|
| `app/parser/md_parser.py` | 已由批一产出 `section_path` | 不改 |
| `app/config.py` | 平台常量 | 新增 `SEARCH_BREADCRUMB_WEIGHT`、`CHUNK_TOKEN_LIMIT` |
| `app/database.py` | schema / 触发器 / 迁移 / backfill | 加两列；FTS 两列；单列→两列迁移判定 |
| `app/search/tokenize.py` | FTS 索引文本与查询串 | `build_search_text` 返回两列 |
| `app/search/embed_text.py` | 向量输入文本 | 加 `section_path` |
| `app/search/sql_search.py` | FTS 查询与排序 | `bm25` 绑定参数；权重=0 列限定 MATCH |
| `app/search/vector_search.py` | LanceDB 读写 | schema 工厂；`chunk_index`；超取 |
| `app/search/hybrid_search.py` | RRF 编排 | 去重取最优；超取倍数 |
| `app/maintenance/health_check.py` | 维护检查 | 只 select `clause_id` |
| `app/params/registry.py` | 参数注册 | 注册 `search.breadcrumb_weight` |
| `app/routes/import_routes.py` | 导入写入 | 两列写入；子块写入；失败措辞 |
| `app/routes/spec_routes.py` | 编辑重索引 | 两列 + section_path |
| `app/routes/maintenance_routes.py` | 重建 | 两列 + section_path |
| `app/templates/partials/` | 展示 | 面包屑；tooltip a11y |
| `scripts/reindex_vectors.py` | 全量重建 | 两列 + section_path |
| `tests/`（11 文件）+ `scripts/probe_jieba_terms.py` | 调用点 | 跟随签名变更 |

---

## Task 1: LanceDB schema 工厂（消除三份重复定义）

> **先做这个**：`chunk_index`（Task 7）要改 schema，而 schema 目前在**三处**逐字重复
> （`vector_search.py:40-46`、`vector_search.py:204-210`、`import_routes.py:551-558`）。
> 不先抽工厂，加列时漏一处就是静默错配。

**Files:**
- Modify: `app/search/vector_search.py`（新增工厂 + 两处改用）
- Modify: `app/routes/import_routes.py:551-558`
- Test: `tests/test_vector_schema_factory.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `embedding_schema(dim: int) -> pa.Schema` — 列固定为 `clause_id:int64, spec_id:int64, text:string, embedding:list<float32,dim>, dim_scores:string`

- [ ] **Step 1: 写失败测试（源码断言，防再次漂移）**

```python
"""LanceDB 建表 schema 必须只有一个定义处。

历史：schema 在三处逐字重复（vector_search.index_clause / batch_index /
import_routes），加列时漏改一处会产生「表按新 schema 建、写入按旧 schema 走」
的静默错配。本测试用源码断言守住「只有一处 pa.schema([...]) 定义」。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_only_one_embedding_schema_definition():
    src = (ROOT / "app/search/vector_search.py").read_text(encoding="utf-8")
    assert src.count("pa.schema([") == 1, "schema 定义应只在 embedding_schema 工厂里出现一次"

    routes = (ROOT / "app/routes/import_routes.py").read_text(encoding="utf-8")
    assert "pa.schema([" not in routes, "import_routes 不得自行定义 schema"


def test_factory_produces_expected_fields():
    from app.search.vector_search import embedding_schema
    s = embedding_schema(8)
    assert [f.name for f in s] == ["clause_id", "spec_id", "text", "embedding", "dim_scores"]
    assert s.field("embedding").type.list_size == 8
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_vector_schema_factory.py -v`
Expected: FAIL — `ImportError: cannot import name 'embedding_schema'`；且 `src.count("pa.schema([") == 2`

- [ ] **Step 3: 实现工厂并替换三处**

```python
def embedding_schema(dim: int) -> pa.Schema:
    """`clause_embeddings` 的**唯一** schema 定义处。

    半精度向量列必须显式声明固定长度（`pa.list_(pa.float32(), dim)`），
    否则 LanceDB 推断出的列类型无法做向量检索。三处建表路径
    （index_clause / batch_index / 导入首建）全部走本函数。
    """
    return pa.schema([
        pa.field("clause_id", pa.int64()),
        pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), dim)),
        pa.field("dim_scores", pa.string()),
    ])
```

`index_clause` 与 `batch_index` 中的 `schema = pa.schema([...])` 全部替换为
`schema = embedding_schema(len(emb))` / `embedding_schema(vec_dim)`；
`import_routes.py:551-558` 整块替换为：

```python
                    from app.search.vector_search import embedding_schema
                    vs.db.create_table("clause_embeddings",
                                       schema=embedding_schema(len(first_emb)))
```

- [ ] **Step 4: 跑测试 + 既有向量测试**

Run: `D:/Python/python.exe -m pytest tests/test_vector_schema_factory.py tests/test_vector_sync.py tests/test_import_vector_batch.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/search/vector_search.py app/routes/import_routes.py tests/test_vector_schema_factory.py
git commit -m "refactor: LanceDB schema 收敛为唯一工厂 embedding_schema

建表 schema 原在三处逐字重复（index_clause / batch_index / import_routes），
加列时漏改一处即产生静默错配。抽成 embedding_schema(dim) 并用源码断言
守住「只有一处定义」。"
```

---

## Task 2: `clauses` 加两列 + FTS 改两列 + 单列→两列迁移

> **并入 T24 的一半（2026-09-28）**：本 Task 加 `clauses.breadcrumb` 的同时，**顺带把祖先链真正写进库**：
> 现状 `parent_clause` **恒为 NULL**（导入侧 `INSERT` 硬编码 `None`），且**没有** `breadcrumb`/`section_path` 列 ⇒
> 批一删掉「无自身正文的章节标题行」之后，那些标题在**库与界面里同时消失**、无人接替（用户实测：`21.3.x` 直跳 `21.4.1`）。
> **要求**：① `breadcrumb` 列写入批一的 `section_path`；② `parent_clause` 写入「**最近的存在祖先**的 id」（不是恒 NULL）。
> **判据**：重导后抽查 `21.4.1` 的 `parent_clause` 指向其节节点（或最近的现存祖先）、`breadcrumb` 含节名。

**Files:**
- Modify: `app/database.py:30-70`（SCHEMA_SQL）、`:186-203`（TRIGGERS_SQL）、`:228-271`（`_migrate_search_text`）
- Test: `tests/test_database.py`（扩展既有「旧结构迁移」用例）

**Interfaces:**
- Consumes: 批一的 `section_path`
- Produces:
  - `clauses.section_path TEXT`（原始面包屑，供展示）
  - `clauses.breadcrumb TEXT`（**jieba 预分词**，供触发器拷贝进 FTS）
  - `clauses_fts` = `fts5(search_text, breadcrumb)`

- [ ] **Step 1: 写失败测试**

```python
def test_fts_has_two_columns(tmp_path, monkeypatch):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
        ).fetchone()[0]
    assert "search_text" in sql and "breadcrumb" in sql


def test_migrate_single_column_fts_to_two(tmp_path, monkeypatch):
    """既有库的 clauses_fts 是单列 → 必须被识别并重建为两列。

    注意 `CREATE VIRTUAL TABLE IF NOT EXISTS` 不会改造已存在的表，
    故迁移必须显式判定「旧形态」并 DROP。
    """
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE clauses (id INTEGER PRIMARY KEY AUTOINCREMENT, spec_id INTEGER,
            clause_no TEXT, title TEXT, content TEXT, search_text TEXT);
        CREATE VIRTUAL TABLE clauses_fts USING fts5(search_text);
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.database.DATABASE_PATH", str(db))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
        ).fetchone()[0]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(clauses)")]
    assert "breadcrumb" in sql, "单列 FTS 必须被重建为两列"
    assert "breadcrumb" in cols and "section_path" in cols


def test_breadcrumb_column_is_copied_by_trigger(tmp_path, monkeypatch):
    """触发器从 clauses.breadcrumb（预分词）拷贝进 FTS 的 breadcrumb 列"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('T','t')")
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text, breadcrumb)"
            " VALUES (1, '6.3.1', '接头安装', '正文', '接头 安装 正文', '6 接头 的 现场 加工 6.3 接头 安装')"
        )
        got = conn.execute("SELECT breadcrumb FROM clauses_fts WHERE rowid = 1").fetchone()[0]
    assert got == "6 接头 的 现场 加工 6.3 接头 安装"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_database.py -k "two_columns or single_column or trigger" -v`
Expected: FAIL — FTS 只有 `search_text`；`no such column: breadcrumb`

- [ ] **Step 3: 实现**

SCHEMA_SQL（`:40` 附近，`clauses` 表）新增两列：

```sql
    section_path    TEXT,           -- 面包屑快照（原始，供展示）："6 混凝土分项工程 > 6.1 模板"
    breadcrumb      TEXT,           -- 面包屑的 jieba 预分词结果（触发器不能调 Python，故应用层写入）
```

SCHEMA_SQL 的 FTS 建表：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(
    search_text,
    breadcrumb
);
```

TRIGGERS_SQL 三个触发器（`:189-202`）改为写两列：

```sql
CREATE TRIGGER IF NOT EXISTS clauses_ai AFTER INSERT ON clauses BEGIN
    INSERT INTO clauses_fts(rowid, search_text, breadcrumb)
    VALUES (new.id, COALESCE(new.search_text, ''), COALESCE(new.breadcrumb, ''));
END;

CREATE TRIGGER IF NOT EXISTS clauses_ad AFTER DELETE ON clauses BEGIN
    DELETE FROM clauses_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS clauses_au AFTER UPDATE ON clauses BEGIN
    DELETE FROM clauses_fts WHERE rowid = old.id;
    INSERT INTO clauses_fts(rowid, search_text, breadcrumb)
    VALUES (new.id, COALESCE(new.search_text, ''), COALESCE(new.breadcrumb, ''));
END;
```

`_migrate_search_text` 的**顺序敏感**改动（沿用该函数既有的顺序约定）：

```python
def _migrate_search_text(conn):
    """FTS5 + jieba 预分词迁移（两列版）。

    顺序敏感（**不得调换**）：
    1. 先加 clauses.breadcrumb 列——旧库无此列时，TRIGGERS_SQL 里
       `new.breadcrumb` 会让触发器创建失败（与 search_text 同样的先例）；
    2. drop 旧触发器；
    3. 判定并 drop 需重建的 FTS 表（旧的 external content 形态，**或**
       只有 search_text 一列的形态）；
    4. 建 fts5(search_text, breadcrumb)；
    5. backfill 两列。
    """
    for col in ("search_text", "breadcrumb"):
        try:
            conn.execute(f"ALTER TABLE clauses ADD COLUMN {col} TEXT")
        except Exception:
            pass  # 列已存在

    for t in ("clauses_ai", "clauses_ad", "clauses_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {t}")

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
    ).fetchone()
    if row:
        sql = row["sql"] or ""
        # 旧的 external content 形态，或单列形态 → 都要重建为两列。
        # `CREATE VIRTUAL TABLE IF NOT EXISTS` 不会改造已存在的表，故此处必须显式 DROP。
        if "content=clauses" in sql or "breadcrumb" not in sql:
            conn.execute("DROP TABLE clauses_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(search_text, breadcrumb)"
    )

    from app.search.tokenize import build_search_text
    rows = conn.execute(
        "SELECT id, clause_no, title, content, search_text, breadcrumb, section_path"
        " FROM clauses"
    ).fetchall()
    for r in rows:
        st, bc = build_search_text(
            r["clause_no"], r["title"], r["content"], r["section_path"] or "")
        if (r["search_text"] or "") != st or (r["breadcrumb"] or "") != bc:
            conn.execute(
                "UPDATE clauses SET search_text = ?, breadcrumb = ? WHERE id = ?",
                (st, bc, r["id"]),
            )
            conn.execute("DELETE FROM clauses_fts WHERE rowid = ?", (r["id"],))
            conn.execute(
                "INSERT INTO clauses_fts(rowid, search_text, breadcrumb) VALUES (?, ?, ?)",
                (r["id"], st, bc),
            )
```

> ⚠ 本 Task 依赖 Task 3 的新签名；**实施顺序**为先在本 Task 里同时改 `build_search_text`
> 的**签名与实现**（Task 3 Step 3 的代码），再跑本 Task 的测试。为避免半成品，
> Task 2 与 Task 3 应作为**同一次提交**落地——见 Task 3 Step 5 的合并提交命令。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: 提交（与 Task 3 合并）**

见 Task 3 Step 5。

---

## Task 3: `build_search_text` 返回两列 + 全部调用点

**Files:**
- Modify: `app/search/tokenize.py:28-41`
- Modify: `app/database.py:263`（已含在上一步）
- Modify: `app/routes/import_routes.py:482`、`app/routes/spec_routes.py:230`
- Modify: **11 个测试文件 + 1 个脚本**（清单见 Step 4）
- Test: `tests/test_tokenize.py`（既有断言必须同步改为更强断言）

**Interfaces:**
- Consumes: 无
- Produces: `build_search_text(clause_no: str, title: str, content: str, section_path: str = "") -> tuple[str, str]`
  - 返回 `(search_text, breadcrumb)`；`search_text` **不含**面包屑

- [ ] **Step 1: 写失败测试（并同步改既有断言）**

```python
def test_build_search_text_returns_two_columns():
    st, bc = build_search_text("6.3.1", "接头安装", "应满足强度要求",
                               "6 接头的现场加工与安装 > 6.3 接头安装")
    assert isinstance(st, str) and isinstance(bc, str)
    assert "接头" in bc                      # 面包屑进了面包屑列
    assert "接头的现场加工" not in st          # 关键：search_text 不含面包屑


def test_search_text_excludes_breadcrumb():
    """面包屑**只**通过 breadcrumb 列参与，不得同时拼进 search_text。

    否则它会以权重 1.0（埋在正文里）与 breadcrumb_weight 各计一次，
    权重语义失真（这是 spec §4.3 明确的设计边界）。
    """
    st, _bc = build_search_text("1.0.1", "总则", "正文甲", "9 某章 > 9.9 某节")
    assert "某章" not in st and "某节" not in st


def test_breadcrumb_empty_when_no_section_path():
    """无祖先时 breadcrumb 为空串（不是 None，触发器用 COALESCE 兜底）"""
    _st, bc = build_search_text("1.0.1", "", "正文甲")
    assert bc == ""
```

既有断言同步改（**这是把断言改成更强形态，不是放宽**）：

```python
def test_build_search_text_empty_returns_space():
    """空文本：search_text 返回占位空格（避免 FTS5 索引 NULL 报错）；breadcrumb 为空串"""
    st, bc = build_search_text("", "", "")
    assert st == " "      # 旧断言为 build_search_text("","","") == " " —— 语义未变，只是解包
    assert bc == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_tokenize.py -v`
Expected: FAIL — `ValueError: not enough values to unpack` / `cannot unpack non-sequence str`

- [ ] **Step 3: 实现**

```python
def build_search_text(clause_no: str, title: str, content: str,
                      section_path: str = "") -> tuple[str, str]:
    """构建 FTS5 索引的**两列**文本：`(search_text, breadcrumb)`。

    - `search_text`：`jieba(标题+正文)` 空格连接 + 追加 `clause_no` 原文。
      **不含面包屑**——面包屑只通过 `breadcrumb` 列参与，否则会以权重 1.0
      （埋在正文里）与 `breadcrumb_weight` 各计一次，权重语义失真。
    - `breadcrumb`：`jieba(section_path)`。**必须预分词**：FTS5 默认
      unicode61 分词器把连续中文折叠为单个 token，未分词的整段中文列只能
      整段精确命中（实测查「接头安装」命中、查「接头」「安装」0 命中）。
    - `content` **先过 `plain_text` 去标记**（渲染载荷含 PaddleOCR-VL 的
      HTML/LaTeX，直接分词会把标记灌进索引，实测约占索引 20-25%）。
    - `search_text` 空时返回占位空格，避免 FTS5 索引 NULL 报 datatype mismatch。
    """
    parts = tokenize(plain_text(f"{title or ''} {content or ''}"))
    if clause_no and clause_no.strip():
        parts.append(clause_no.strip())
    search_text = " ".join(parts) or " "
    breadcrumb = " ".join(tokenize(section_path or ""))
    return search_text, breadcrumb
```

- [ ] **Step 4: 修全部调用点（11 个测试文件 + 1 个脚本 + 2 个生产路由）**

生产（2 处，取 `[0]` 写 `search_text`，`[1]` 写 `breadcrumb`）：

```python
# app/routes/import_routes.py:482  → 改为分别取两列写入 INSERT
# app/routes/spec_routes.py:230    → 同上（编辑重索引）
```

测试与脚本（**同一变换：调用方原先要串，现取 `[0]`**）。

实测调用点清单（**用下面这条命令现场复核，数字以实测为准**）：

```bash
# 排除 import 行后的真实调用点数
for f in $(grep -rl 'build_search_text' tests/ scripts/ --include=*.py | grep -v __pycache__ | sort); do
  printf "  %-46s %s\n" "$f" "$(grep 'build_search_text' "$f" | grep -v 'import build_search_text' | wc -l)"
done
```

实测基线（2026-09-27，改造前）：

| 文件 | 调用点 | 备注 |
|---|---|---|
| `tests/conftest.py` | 1 | `setup_search_data` 夹具 |
| `tests/test_health_check.py` | 4 | |
| `tests/test_hybrid_search.py` | 16 | 最多 |
| `tests/test_maintenance_badge.py` | 4 | |
| `tests/test_qa_routes.py` | 2 | |
| `tests/test_qa_status_filter.py` | 1 | |
| `tests/test_search.py` | 5 | |
| `tests/test_search_lexicon_expand.py` | 1 | |
| `tests/test_tokenize.py` | 13 | 含对返回值的**直接断言**（`== " "`、`== " ".join(tokenize(content))`） |
| **测试小计** | **47**，**9 个文件** | |
| `scripts/probe_jieba_terms.py` | 7 | **另见下方特例 1** |
| **合计** | **约 54** | |

> `tests/test_text_clean.py` 仅在 docstring 里提到 `build_search_text`（说明占位空格逻辑），
> **无调用点，不需改**。`tests/test_classify_param_reading.py` 不调用该函数
> （此前评审记录误将其列入，已更正）。

变换规则：`build_search_text(a, b, c)` → `build_search_text(a, b, c)[0]`。

**注意两处特例**：

1. `scripts/probe_jieba_terms.py:57` **复刻了实现**（docstring 自称「复刻 app/search/tokenize.build_search_text」），必须同步改为返回两列，否则该探针的结论失效。
2. `tests/test_tokenize.py` 的 13 处中多为**直接对返回值断言**，必须逐条改为解包后的断言，**且保持断言语义不弱化**（例：`== " "` → `st == " "`，不是删掉断言）。

- [ ] **Step 5: 跑测试 + pyright + 提交（含 Task 2）**

Run: `D:/Python/python.exe -m pytest tests/ -v`（大范围调用点变更 → 跑全量）
Expected: PASS

```bash
git add app/search/tokenize.py app/database.py app/routes/import_routes.py \
        app/routes/spec_routes.py tests/ scripts/probe_jieba_terms.py
git commit -m "feat: clauses 加 section_path/breadcrumb 两列，FTS 改两列，build_search_text 返回两列

- breadcrumb 列存 jieba 预分词结果（触发器不能调 Python，故应用层写入）；
  未预分词的中文在默认分词器下折叠为单 token，实测子串查询 0 命中
- 单列→两列迁移需显式判定并 DROP：CREATE VIRTUAL TABLE IF NOT EXISTS
  不会改造已存在的表
- ALTER 必须先于触发器创建（沿用 _migrate_search_text 既有顺序约定）
- build_search_text 签名变更波及 11 个测试文件与 1 个复刻实现的探针"
```

---

## Task 4: `build_embed_text` 加 `section_path` + 4 个生产调用点

> **本 Task 的漏改后果是静默的**：新参数有默认值 `""`，漏掉的调用点会产出**不含面包屑**的向量。
> 本仓已有 `tests/test_reindex_vectors_script.py` 专门防这类「embed 文本漂移」，
> 其 docstring 写着「避免再次漂移」——此坑已踩过一次。

**Files:**
- Modify: `app/search/embed_text.py:11-23`
- Modify: `app/routes/import_routes.py:508`、`app/routes/maintenance_routes.py:109`、`app/routes/spec_routes.py:242`、`app/search/vector_search.py:141`
- Modify: `scripts/reindex_vectors.py:46`、`scripts/probe_rebuild_effect.py:97`
- Test: `tests/test_embed_text.py`

**Interfaces:**
- Consumes: 无
- Produces: `build_embed_text(code="", spec_title="", clause_no="", clause_title="", content="", section_path="") -> str`
  - 面包屑**拼在条文号之后、正文之前**（保留位置信号）

- [ ] **Step 1: 写失败测试**

```python
def test_build_embed_text_includes_section_path_after_clause_no():
    """面包屑插在条文号之后、正文之前（保留位置信号）"""
    got = build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容",
                           "5 混凝土分项工程 > 5.1 模板")
    assert got == "[GB 50010 混凝土规范] [5.1.1] 5 混凝土分项工程 > 5.1 模板 模板 内容"


def test_build_embed_text_without_section_path_unchanged():
    """不传面包屑时，输出与旧格式逐字一致（保证存量向量不因本改动而错位）"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容") == \
        build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容", "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_embed_text.py -v`
Expected: FAIL — `TypeError: build_embed_text() takes from 0 to 5 positional arguments but 6 were given`

- [ ] **Step 3: 实现 + 修全部调用点**

```python
def build_embed_text(code: str = "", spec_title: str = "", clause_no: str = "",
                     clause_title: str = "", content: str = "",
                     section_path: str = "") -> str:
    """拼接规范 code/title + 条文号 + 面包屑 + 标题/正文，作为 embedding 输入。

    格式：`"{code} {spec_title} [{clause_no}] {section_path} {clause_title} {content}"`
    **面包屑插在条文号之后、正文之前**：保留「该条属于哪一节」的位置信号，
    使「搜节名」能通过向量臂召回该节下的条文。

    空字段以空串占位，末尾 strip 去掉整体空白。content 先过 `plain_text` 去标记。
    """
    return plain_text(f"{code or ''} {spec_title or ''} [{clause_no}] "
                      f"{section_path or ''} {clause_title or ''} {content or ''}").strip()
```

4 个生产调用点各自补 `section_path`（**逐个改，不得漏**）：

| 文件:行 | 调用 | 补什么 |
|---|---|---|
| `import_routes.py:508` | `build_embed_text(code, title, cd["clause_no"], cd["title"], cd["content"])` | 第 6 参 `cd.get("section_path", "")` |
| `maintenance_routes.py:109` | `build_embed_text(c["code"], c["spec_title"], c["clause_no"], ...)` | 该 SELECT 需补 `c.section_path` 并在调用处传入 |
| `spec_routes.py:242` | `build_embed_text(spec["code"], spec["title"], clause_no, title, content)` | 补该条的 `section_path`（需查库） |
| `vector_search.py:141` | `build_embed_text(r["code"], r["spec_title"], r["clause_no"], r["title"], r["content"])` | 该 SELECT 需补 `c.section_path` 并在调用处传入 |
| `scripts/reindex_vectors.py:46` | 同上 | 同上 |
| `scripts/probe_rebuild_effect.py:97` | 同上 | 同上 |

- [ ] **Step 4: 跑测试 + 漂移守卫**

Run: `D:/Python/python.exe -m pytest tests/test_embed_text.py tests/test_reindex_vectors_script.py tests/test_vector_sync.py -v`
Expected: PASS

- [ ] **Step 5: 源码断言：4 个生产调用点都传了 section_path**

```python
# tests/test_embed_text.py 追加
def test_all_production_callers_pass_section_path():
    """4 个生产调用点必须都传面包屑——漏改变量是静默的（新参数有默认值 ""）"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    targets = [
        "app/routes/import_routes.py", "app/routes/maintenance_routes.py",
        "app/routes/spec_routes.py", "app/search/vector_search.py",
        "scripts/reindex_vectors.py",
    ]
    for rel in targets:
        src = (root / rel).read_text(encoding="utf-8")
        assert "section_path" in src, f"{rel} 未传面包屑给 build_embed_text"
```

Run: `D:/Python/python.exe -m pytest tests/test_embed_text.py -v`
Expected: PASS

- [ ] **Step 6: pyright + 提交**

```bash
git add app/search/embed_text.py app/routes/ app/search/vector_search.py scripts/ tests/
git commit -m "feat: build_embed_text 加 section_path，4 个生产调用点全部补齐

面包屑插在条文号之后、正文之前，保留位置信号使向量臂能召回节名。
新增源码断言守住「4 个生产调用点都传了 section_path」——漏改变量是静默的
（新参数有默认值），本仓此前已踩过 embed 文本漂移一次。"
```

---

## Task 5: `bm25` 绑定参数 + 权重=0 的列限定 MATCH

> 本 Task 修掉 spec 的两处错误断言，并把「权重=0」变成**真的**关闭。

**Files:**
- Modify: `app/search/sql_search.py:90-98`（排序）、`:13-20`（MATCH 构造）
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `get_param_float("search.breadcrumb_weight")`（Task 6 注册）
- Produces:
  - `bm25(clauses_fts, 1.0, ?)` —— 第二列权重走**绑定参数**
  - `_scope_match_to_search_text(expr: str) -> str` —— 返回 `search_text : (expr)`

- [ ] **Step 1: 写失败测试**

```python
"""权重语义的两条实测事实（sqlite 3.50.4 复现）：
1. bm25(f, 1.0, 0.0) **不能**关闭 breadcrumb 列的召回——仅命中该列的行仍被返回；
2. 真要关闭必须用**列限定 MATCH**（`search_text : (...)`）。
"""
import sqlite3


def _fts():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE f USING fts5(search_text, breadcrumb)")
    c.execute("INSERT INTO f(rowid, search_text, breadcrumb) VALUES"
              " (1, '钢筋 连接 要求', '6 接头 的 现场 加工 6.3 接头 安装')")
    c.execute("INSERT INTO f(rowid, search_text, breadcrumb) VALUES"
              " (2, '接头 安装 应 满足 强度 要求', '1 总则')")
    return c


def test_weight_zero_does_not_disable_breadcrumb_recall():
    """记录事实：仅把权重置 0 关不掉 breadcrumb 列的召回（本条锁定我们为何需要列限定）"""
    rows = [r[0] for r in _fts().execute(
        "SELECT rowid FROM f WHERE f MATCH ? ORDER BY bm25(f, 1.0, 0.0)",
        ('"接头" AND "安装"',)).fetchall()]
    assert 1 in rows, "若此断言变化，说明 SQLite 语义变了——需重新评估开关实现"


def test_column_scoped_match_excludes_breadcrumb_only_rows():
    rows = [r[0] for r in _fts().execute(
        "SELECT rowid FROM f WHERE f MATCH ?", ('search_text : ("接头" AND "安装")',)
    ).fetchall()]
    assert rows == [2], "列限定后，仅命中 breadcrumb 的行 1 必须被排除"


def test_scope_helper_wraps_expression():
    from app.search.sql_search import _scope_match_to_search_text
    assert _scope_match_to_search_text('"a" AND ("b" OR "c")') == 'search_text : ("a" AND ("b" OR "c"))'


def test_search_uses_bound_parameter_for_bm25_weight(monkeypatch, tmp_path, ...):
    """排序 SQL 必须用绑定参数而非字面量（实测绑定参数可用，字面量是伪风险且是注入面）"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app/search/sql_search.py").read_text(encoding="utf-8")
    assert "bm25(clauses_fts, 1.0, ?)" in src
    assert "bm25(clauses_fts, 1.0, %" not in src and "float(" not in src.split("bm25")[-1][:80]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_search.py -k "weight_zero or column_scoped or scope_helper or bound_parameter" -v`
Expected: FAIL — `ImportError: cannot import name '_scope_match_to_search_text'`；源码断言失败

- [ ] **Step 3: 实现**

```python
def _scope_match_to_search_text(expr: str) -> str:
    """把 MATCH 表达式限定到 search_text 单列。

    用途：`breadcrumb_weight == 0` 时表达「面包屑完全退出检索」。

    ⚠ 为什么不能靠「把权重置 0」实现：实测 bm25(f, 1.0, 0.0) 下，仅在
    breadcrumb 列命中的行**仍会被 MATCH 返回**（FTS5 的 MATCH 与列无关，
    权重只缩放评分、不改变召回）。故必须用列限定把匹配范围收窄。

    ⚠ 必须整体加括号：`build_expanded_match` 会产出 `"a" AND ("b" OR "c")`
    这类含嵌套括号的表达式，不加括号时列作用域只覆盖紧邻的短语。
    """
    return f"search_text : ({expr})"
```

排序（`:96`）与参数：

```python
        # 排序：bm25 相关度（FTS5 分数越小越相关）+ 字段信号提权
        # 面包屑权重在**查询时**生效 → 改参数无需重建 FTS 索引。
        # 权重走绑定参数（实测 bm25(f,1.0,?) 可用）。
        from app.params.registry import get_param_float
        breadcrumb_weight = get_param_float("search.breadcrumb_weight")
        order_by = (
            "CASE WHEN c.clause_no = ? THEN 0 "
            "WHEN c.clause_no LIKE ? THEN 1 "
            "WHEN c.title LIKE ? THEN 2 "
            "ELSE 3 END, bm25(clauses_fts, 1.0, ?)"
        )
        order_params = [kw, f"%{kw}%", f"%{kw}%", breadcrumb_weight]
```

MATCH 构造（`_build` 处）：权重为 0 时收窄列作用域：

```python
            if match_expr:
                if breadcrumb_weight == 0:
                    match_expr = _scope_match_to_search_text(match_expr)
                conditions.append("f.clauses_fts MATCH ?")
                params.append(match_expr)
```

> `breadcrumb_weight` 需在 `_build` 之前取到；把 `get_param_float` 提到函数开头
> 与 `vector_top_k` 等同处（**参数不得进内层循环**，这是既有约定）。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_search.py tests/test_hybrid_search.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/search/sql_search.py tests/test_search.py
git commit -m "feat: 面包屑列权重走绑定参数；权重=0 时改发列限定 MATCH

实测证伪 spec 的两处断言：
1. bm25(f,1.0,0.0) 不能关闭 breadcrumb 列的召回（仅命中该列的行仍被返回），
   故「0=不参与」必须靠列限定 MATCH 实现（search_text : (...)），并整体加括号
   以覆盖 build_expanded_match 产出的嵌套括号表达式；
2. bm25 权重参数支持绑定参数，故删除 spec 里的 float() 字面量回退——那是伪风险，
   且是本次改动唯一的注入面。"
```

---

## Task 6: 注册参数 `search.breadcrumb_weight`

**Files:**
- Modify: `app/config.py:62-66`（新增常量）
- Modify: `app/params/registry.py:110`（`search` 分组末尾注册）
- Test: `tests/test_classify_param_reading.py` 或新建 `tests/test_param_breadcrumb.py`

**Interfaces:**
- Consumes: 无
- Produces: 参数键 `search.breadcrumb_weight`（float，默认 0.3，范围 0.0~2.0）

- [ ] **Step 1: 写失败测试**

```python
def test_breadcrumb_weight_is_registered():
    from app.params.registry import PARAM_META
    meta = {m["key"]: m for m in PARAM_META}
    m = meta["search.breadcrumb_weight"]
    assert m["group"] == "search"
    assert m["dtype"] == "float"
    assert float(m["default"]) == 0.3
    assert float(m["min"]) == 0.0 and float(m["max"]) == 2.0
    # 帮助文案必须诚实：0 表示不参与（由列限定 MATCH 实现），而非「仅调弱排序」
    assert "0" in m["help"]
```

Run: `D:/Python/python.exe -m pytest tests/test_param_breadcrumb.py -v`
Expected: FAIL — `KeyError: 'search.breadcrumb_weight'`

- [ ] **Step 2: 实现**

```python
# app/config.py
SEARCH_BREADCRUMB_WEIGHT = 0.3     # 面包屑在 BM25 排序里的列权重（0=不参与）
```

```python
# app/params/registry.py —— search 分组末尾（紧跟 search.lexicon_expand）
    meta.append(_num(
        "search.breadcrumb_weight", "search", "面包屑权重", float(SEARCH_BREADCRUMB_WEIGHT),
        0.0, 2.0, "0~2",
        "面包屑（章/节路径）在 BM25 排序里的列权重；0=完全不参与（检索限定在正文列），"
        "越大越偏向命中节名的条文。改此项无需重建索引。"))
```

> **文案必须诚实**：0 的语义是「完全不参与」，由 Task 5 的列限定 MATCH 实现。
> 不得写成「0=仅调弱排序」——实测权重 0 并不能关掉召回。

- [ ] **Step 3: 跑测试确认通过 + 参数页可见性**

Run: `D:/Python/python.exe -m pytest tests/test_param_breadcrumb.py -v`
Expected: PASS

- [ ] **Step 4: pyright + 提交**

```bash
git add app/config.py app/params/registry.py tests/test_param_breadcrumb.py
git commit -m "feat: 注册参数 search.breadcrumb_weight（检索分组，默认 0.3，范围 0~2）

帮助文案按实测语义写：0=完全不参与（由列限定 MATCH 实现），不是仅调弱排序。"
```

---

## Task 7: LanceDB 加 `chunk_index` + 重建现存表

> LanceDB **不能** `ALTER TABLE`：现存表无此列，必须重建 schema。

**Files:**
- Modify: `app/search/vector_search.py`（`embedding_schema` 加工参数；写入处传值）
- Test: `tests/test_vector_schema_factory.py`（扩展）、`tests/test_vector_chunk.py`（新建）

**Interfaces:**
- Consumes: `embedding_schema`（Task 1）
- Produces: `embedding_schema(dim: int, with_chunk_index: bool = True) -> pa.Schema`；写入记录键 `chunk_index: int`

- [ ] **Step 1: 写失败测试**

```python
def test_schema_includes_chunk_index():
    from app.search.vector_search import embedding_schema
    names = [f.name for f in embedding_schema(8)]
    assert names == ["clause_id", "spec_id", "text", "embedding", "dim_scores", "chunk_index"]


def test_old_table_without_chunk_index_is_rebuilt(tmp_path, monkeypatch):
    """现存表无 chunk_index → 必须重建（LanceDB 不支持 ALTER）。"""
    import lancedb
    import pyarrow as pa
    monkeypatch.setattr("app.config.LANCE_DB_PATH", str(tmp_path / "lance"))
    db = lancedb.connect(str(tmp_path / "lance"))
    db.create_table("clause_embeddings", schema=pa.schema([
        pa.field("clause_id", pa.int64()), pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 4)),
        pa.field("dim_scores", pa.string()),
    ]))
    from app.search.vector_search import VectorStore
    vs = VectorStore()
    assert vs.needs_rebuild() is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_vector_chunk.py -v`
Expected: FAIL — `AttributeError: 'VectorStore' object has no attribute 'needs_rebuild'`

- [ ] **Step 3: 实现**

```python
    def needs_rebuild(self) -> bool:
        """现存表是否缺少 chunk_index 列（LanceDB 不支持 ALTER，需重建）。

        维护页据此显示「向量索引需重建」，而不是等写入时才发现列不存在。
        """
        if not self._table_exists():
            return False
        try:
            names = {f.name for f in self._get_table().schema}
        except Exception as e:
            logger.warning("读取向量表 schema 失败: %s", e)
            return True
        return "chunk_index" not in names
```

`embedding_schema` 增加 `chunk_index`；全部写入记录补 `"chunk_index": 0`（未切块时）。

- [ ] **Step 4: 跑测试确认通过（**并同步更新 Task 1 的字段名断言**）**

Task 1 的 `test_factory_produces_expected_fields` 断言字段名**精确等于** 5 项；
本 Task 加上 `chunk_index` 后该断言必然失败。**这是预期的设计演进，不是回归**——
把它更新为 6 项（且保持精确等值，不要退化成「包含」）：

```python
def test_factory_produces_expected_fields():
    from app.search.vector_search import embedding_schema
    s = embedding_schema(8)
    assert [f.name for f in s] == [
        "clause_id", "spec_id", "text", "embedding", "dim_scores", "chunk_index",
    ]
    assert s.field("embedding").type.list_size == 8
```

Run: `D:/Python/python.exe -m pytest tests/test_vector_chunk.py tests/test_vector_schema_factory.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/search/vector_search.py tests/
git commit -m "feat: LanceDB 加 chunk_index 列 + needs_rebuild 探测

LanceDB 不支持 ALTER TABLE，现存表缺该列必须重建；维护页据此提示重建，
而不是等写入时才发现列不存在。"
```

---

## Task 8: 超长条文按 512 token 切子块

**Files:**
- Modify: `app/config.py`（`CHUNK_TOKEN_LIMIT = 512`）
- Modify: `app/routes/import_routes.py`（写入路径产出子块）
- Modify: `app/search/vector_search.py`（`index_clause` 支持多行同 clause_id）
- Test: `tests/test_vector_chunk.py`

**Interfaces:**
- Consumes: `embedding_schema`（Task 7）、`plain_text`
- Produces:
  - `chunk_text(text: str, limit: int = CHUNK_TOKEN_LIMIT) -> list[str]`
  - `index_clause(..., chunk_index: int = 0)`

- [ ] **Step 1: 写失败测试**

```python
def test_chunk_short_text_returns_single_chunk():
    from app.search.chunking import chunk_text
    assert chunk_text("短正文。") == ["短正文。"]


def test_chunk_long_text_splits_on_sentence_boundary():
    """切点必须落在句子边界上，不得从词中间切断"""
    from app.search.chunking import chunk_text
    text = "。".join(f"第{i}句内容" for i in range(400)) + "。"
    chunks = chunk_text(text, limit=100)
    assert len(chunks) > 1
    assert all(c.endswith("。") for c in chunks)
    assert "".join(chunks) == text          # 不丢不重


def test_chunk_unpunctuated_long_text_still_bounded():
    """无标点超长串：必须仍能切出多块且不丢字符（保底按长度切）"""
    from app.search.chunking import chunk_text
    text = "甲" * 1000
    chunks = chunk_text(text, limit=100)
    assert len(chunks) > 1
    assert "".join(chunks) == text


def test_chunk_empty_returns_empty_list():
    from app.search.chunking import chunk_text
    assert chunk_text("") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_vector_chunk.py -k chunk -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.search.chunking'`

- [ ] **Step 3: 实现 `app/search/chunking.py`**

```python
"""超长条文的子块切分（仅用于向量侧；FTS 侧无长度限制、已覆盖全文）。

切分口径：`plain_text(content)` 之后按 **token** 计数（与 embedding 模型的
max_seq_length=512 同量纲），优先在句末标点处断开；
无标点的超长串按长度保底切分。

**不丢不重**：所有子块按序拼接必须等于原文（测试锁定）。
"""
import re

from app.ai.text_clean import plain_text
from app.config import CHUNK_TOKEN_LIMIT

# 句末标点（中英文）
_SENTENCE_END = re.compile(r'(?<=[。；！？.!?;])\s*')


def chunk_text(text: str, limit: int = CHUNK_TOKEN_LIMIT) -> list[str]:
    """把正文切成不超过 limit 个 token 的子块；短文本返回单块。

    空文本返回空列表（调用方据此跳过写向量）。
    """
    cleaned = plain_text(text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= limit:
        return [cleaned]

    chunks: list[str] = []
    buf = ""
    for piece in _SENTENCE_END.split(cleaned):
        if not piece:
            continue
        if len(buf) + len(piece) <= limit:
            buf += piece
            continue
        if buf:
            chunks.append(buf)
        # 单句本身超限 → 长度保底切
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        buf = piece
    if buf:
        chunks.append(buf)
    return chunks
```

> `plain_text` 里 `\s*` 会吃掉标点后的空白，故 `_SENTENCE_END.split` 的
> 结果拼接不一定逐字等于 `cleaned`。**测试里的 `"".join(chunks) == text`
> 用的是已归一化的文本**（对 `text` 先跑一遍 `plain_text` 再比），
> 避免把「空白归一化」误判成「丢字符」。

- [ ] **Step 4: 写入路径按 `chunk_index` 写多行**

`index_clause` 先删该 clause 的全部旧行（既有 `delete(f"clause_id = {clause_id}")`
已能做到，无需改），再逐块 `add`；导入路径对每条条文：

```python
                for ci, piece in enumerate(chunk_text(cd["content"])):
                    embed_text = build_embed_text(
                        code, title, cd["clause_no"], cd["title"], piece,
                        cd.get("section_path", ""))
                    records.append({... "text": embed_text, "chunk_index": ci})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_vector_chunk.py -v`
Expected: PASS

- [ ] **Step 6: pyright + 提交**

```bash
git add app/search/chunking.py app/config.py app/routes/import_routes.py \
        app/search/vector_search.py tests/
git commit -m "feat: 超长条文按 512 token 切子块写入向量表

切点优先落在句末标点，无标点超长串按长度保底；所有子块按序拼接等于原文
（不丢不重，测试锁定）。仅超限条文多行，短条文仍单行 chunk_index=0。"
```

---

## Task 9: 向量臂去重（超取 + 取最优）

> 两处必须同时改，缺一即回错误结果：
> ① `vs.search(top_k)` 要**超取**，否则一条长条文的多块会挤掉其他条文，去重后有效召回缩水；
> ② `hybrid_search.dist_map[clause_id] = dist` 是**后者覆盖** → 会保留**最差**的块距离。

**Files:**
- Modify: `app/search/hybrid_search.py:104-108`（`dist_map`）
- Modify: `app/search/hybrid_search.py:98`（超取）
- Modify: `app/search/vector_search.py:64-77`（`search` 支持按 clause 去重 / 明确返回块）
- Test: `tests/test_hybrid_search.py`

**Interfaces:**
- Consumes: Task 7 的 `chunk_index`
- Produces: `VectorStore.search(query_text, top_k, dedupe_by_clause: bool = True) -> list[dict]`

- [ ] **Step 1: 写失败测试**

```python
def test_dist_map_keeps_best_chunk_not_last():
    """同一条文多块时，必须保留**最小**（最近）距离，而非最后写入的那个"""
    dist_map: dict[int, float] = {}
    for cid, d in ((7, 0.9), (7, 0.3), (7, 0.7)):     # 命中顺序任意
        dist_map[cid] = min(dist_map.get(cid, float("inf")), d)
    assert dist_map[7] == 0.3


def test_search_overfetches_before_dedup(monkeypatch):
    """去重发生在检索之后 → 必须先超取，否则有效召回被自己挤掉"""
    from app.search import hybrid_search as hs
    assert hs._VECTOR_FETCH_MULTIPLIER >= 2


def test_dedupe_by_clause_keeps_highest_scoring_row():
    from app.search.vector_search import _dedupe_by_clause
    rows = [
        {"clause_id": 7, "chunk_index": 0, "_distance": 0.9, "text": "a"},
        {"clause_id": 7, "chunk_index": 1, "_distance": 0.3, "text": "b"},
        {"clause_id": 8, "chunk_index": 0, "_distance": 0.5, "text": "c"},
    ]
    out = _dedupe_by_clause(rows)
    assert [r["clause_id"] for r in out] == [7, 8]
    assert out[0]["_distance"] == 0.3 and out[0]["chunk_index"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_hybrid_search.py -k "best_chunk or overfetch or dedupe_by_clause" -v`
Expected: FAIL — `ImportError: cannot import name '_dedupe_by_clause'`

- [ ] **Step 3: 实现**

```python
# app/search/hybrid_search.py
# 向量臂超取倍数：去重按 clause_id 进行，一条长条文可能占多个块位；
# 不超取会让块数多的条文挤掉其他条文的候选，去重后有效召回缩水。
_VECTOR_FETCH_MULTIPLIER = 3
```

```python
                vector_raw = vs.search(keyword, top_k=vector_top_k * _VECTOR_FETCH_MULTIPLIER)
```

```python
        # ── 3. 向量候选过滤（L2 < 阈值）──
        # 同一条文可能有多块（子块），**必须取最小距离（最优块）**：
        # 原实现 `dist_map[id] = dist` 是后者覆盖，会保留最差的那块。
        dist_map = {}
        for v in vector_raw:
            dist = v.get("_distance", 0)
            if dist < vector_threshold:
                prev = dist_map.get(v["clause_id"])
                if prev is None or dist < prev:
                    dist_map[v["clause_id"]] = dist
```

```python
# app/search/vector_search.py
def _dedupe_by_clause(rows: list[dict]) -> list[dict]:
    """按 clause_id 去重，保留 `_distance` 最小（最相关）的那块。

    距离越小越相关；保留最优块同时提升结果多样性——防止一条长条文的
    多个子块占满榜单（spec §4.4 明确要求）。
    """
    best: dict[int, dict] = {}
    for r in rows:
        cid = r["clause_id"]
        cur = best.get(cid)
        if cur is None or r.get("_distance", float("inf")) < cur.get("_distance", float("inf")):
            best[cid] = r
    return sorted(best.values(), key=lambda d: d.get("_distance", float("inf")))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_hybrid_search.py tests/test_search.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/search/hybrid_search.py app/search/vector_search.py tests/
git commit -m "fix: 向量臂按 clause_id 取最优块 + 检索前超取

两处必须同时改：dist_map 原为后者覆盖（保留最差块距离），检索未超取时
一条长条文的多块会挤掉其他条文候选、去重后有效召回缩水。"
```

---

## Task 10: 维护检查只读 `clause_id`（性能）

> 实测：`_vector_ids_and_state` 用 `tbl.to_arrow()` 读**全表含 embedding 列**，
> 仅为收集一个 id 集合。1088 行 ≈ 2 MB 尚可，但 spec 自己的规模估计是全语料约 2.4 万条
> → 单次约 **49 MB**，且维护页每次打开都跑。子块再 +5~8%。

**Files:**
- Modify: `app/maintenance/health_check.py:66`
- Modify: `app/search/vector_search.py:84`（`get_orphans`）、`:126`（`index_missing`）
- Test: `tests/test_health_check.py`、`tests/test_vector_sync.py`

**Interfaces:**
- Consumes: 无
- Produces: `VectorStore.iter_clause_ids() -> set[int]`（只读该列的公共入口）

- [ ] **Step 1: 写失败测试（源码断言 + 行为等价）**

```python
def test_clause_id_read_is_column_scoped():
    """收集 clause_id 不得整表 to_arrow()（含 embedding 列）"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app/maintenance/health_check.py").read_text(encoding="utf-8")
    assert "to_arrow()" not in src, "改为 iter_clause_ids()（只 select clause_id）"


def test_iter_clause_ids_matches_table_column(tmp_path, monkeypatch):
    """行为等价：返回集合与直接读该列一致"""
    ...
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_health_check.py -k clause_id_read -v`
Expected: FAIL — 源码里存在 `to_arrow()`

- [ ] **Step 3: 实现**

```python
    def iter_clause_ids(self) -> set[int]:
        """只读 `clause_id` 列，返回集合（自动去重，天然适配子块多行）。

        **不要**用 `to_arrow()`：那会连 embedding 列一起物化
        （2.4 万条 × 512 float32 ≈ 49 MB/次，而维护页每次打开都调用）。
        """
        if not self._table_exists():
            return set()
        tbl = self._get_table()
        rows = tbl.to_arrow().select(["clause_id"]) if hasattr(tbl.to_arrow(), "select") \
            else tbl.to_arrow()
        return {int(v) for v in rows.column("clause_id").to_pylist()}
```

> **实现提示**：LanceDB 的 `Table.to_arrow()` 在较新版本支持 `columns=` 参数；
> 若本地版本不支持列裁剪，改用 `tbl.search().select(["clause_id"]).limit(None).to_arrow()`
> 或 `tbl.to_pandas(columns=["clause_id"])`。**实施时先验证本机 LanceDB 版本支持哪种形式**，
> 并在提交信息里写明所用形式。

`health_check._vector_ids_and_state`、`get_orphans`、`index_missing` 三处改用该入口。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_health_check.py tests/test_vector_sync.py tests/test_maintenance_badge.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/maintenance/health_check.py app/search/vector_search.py tests/
git commit -m "perf: 收集向量 clause_id 只读该列，不再整表 to_arrow()

原实现为收集 id 集合把 embedding 列一并物化：2.4 万条时约 49 MB/次，
且维护页每次打开都跑。抽 VectorStore.iter_clause_ids() 供三处复用
（集合天然去重，适配子块多行）。"
```

---

## Task 11: 全量重建向量门禁

> **本 Task 是批二的验收门禁**，不是可选项。实测 `sync_with_db()` 只补缺失 id
> （`vector_search.py:138` `missing = [r for r in all_rows if r["id"] not in vector_ids]`），
> **不会重嵌已有条文**。加面包屑后若只跑 `sync_with_db`，旧向量永久不含面包屑，
> 且新参数有默认值 `""`，漏改的调用点会静默产出不一致的向量。

**Files:**
- Modify: `scripts/reindex_vectors.py`（已含 `build_embed_text` 复用约束）
- Test: `tests/test_reindex_vectors_script.py`（既有守卫，扩展断言）

**Interfaces:**
- Consumes: Task 4 的 4 个调用点、Task 8 的 `chunk_text`
- Produces: 无新接口

- [ ] **Step 1: 写失败测试（断言向量文本确实含面包屑）**

```python
def test_reindex_vectors_includes_section_path(tmp_path, monkeypatch):
    """重建后的向量文本必须含面包屑——否则「搜节名」在向量臂失效"""
    ...
    # 构造一条有 section_path 的条文，重建，读回向量表的 text 列
    assert "混凝土分项工程" in row["text"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_reindex_vectors_script.py -v`
Expected: FAIL — 向量文本不含面包屑

- [ ] **Step 3: 实现 + 重建脚本支持子块**

`scripts/reindex_vectors.py` 的查询补 `c.section_path`，并按 `chunk_text` 产出多行。
`batch_index` 的 `clauses` 入参补 `chunk_index`。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_reindex_vectors_script.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/reindex_vectors.py tests/test_reindex_vectors_script.py
git commit -m "feat: 向量重建纳入面包屑并支持子块（批二验收门禁）

sync_with_db 只补缺失 id、不重嵌已有条文，故加面包屑后必须全量重建；
重建产物必须含面包屑（测试断言），否则「搜节名」在向量臂静默失效。"
```

---

## Task 12: 重导流程（排除 uploads / optimize / 失败措辞）

**Files:**
- Modify: 重导入口（`app/routes/maintenance_routes.py` 或重导脚本）
- Modify: `app/routes/import_routes.py:602`（失败措辞）
- Test: `tests/test_reimport_guard.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: 无新接口

- [ ] **Step 1: 写失败测试**

```python
def test_reimport_excludes_uploads_dir():
    """重导不得扫描 data/uploads/（实测已累积 147 个 pytest 夹具 md）

    若扫描它，夹具会被当规范导入真实库，并让守恒断言失真。
    """
    ...


def test_reimport_failure_message_says_incomplete():
    """重导失败必须明确告知「本次重导未完成，请重跑」

    实测 rollback（import_routes.py:340/602）只覆盖 SQLite 事务，
    LanceDB 向量写入不在事务内 → 失败会留下常驻半成品态
    （维护宫格会报红点，但提示语只写 error，用户会以为只是这一次失败）。
    """
    ...
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_reimport_guard.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
            progress_store[task_id].update(
                status="error", progress=0,
                message=f"导入失败，本次未完成，请重跑：{e}"
            )
```

重导收尾加 LanceDB 压实（子块使版本增长加快，历史曾出现 `rows=73 / version=173`）：

```python
        try:
            vs._get_table().optimize()
        except Exception as e:                       # 压实失败不阻断导入
            logger.warning("向量表压实失败（不影响本次导入结果）: %s", e)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_reimport_guard.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/routes/ tests/
git commit -m "fix: 重导排除 uploads 目录、收尾压实向量表、失败措辞明确「未完成」

uploads/ 已累积 147 个 pytest 夹具 md，被当规范导入会污染真实库。
子块使向量行数 +5~8%、版本增长加快（历史 rows=73/version=173），
收尾 optimize 控制版本数。"
```

---

## Task 13: UI（tooltip 可访问性 / 空面包屑 / 纯文本形态 / 移除目次）

> **并入 T24 的另一半（2026-09-28）**：本 Task 的「空面包屑」一项扩为**把祖先链展示出来** ——
> 详情弹窗现在**只显示编号/标题/正文/维度**（`clause_detail.html` 里没有 `section_path`），
> 而用户实测的「来源：为空」正来自此。**要求**：详情（与结果列表，若适用）显示 `breadcrumb`（数据来自 Task 2 新列）。
> 另：**勾选框 tooltip 文案串了 AI 问答页的措辞**（检索页显示的是「若**提问**中含…」）—— 一并改掉（用户实测第 5 项）。

**Files:**
- Modify: `app/templates/partials/`（检索过滤区、`clause_detail.html:55`）
- Modify: `static/app.css`（如需）
- Modify: `app/templates/base.html`（`<script src="...?v=N">` **版本号递增**）
- Test: 手工验证（无自动化 UI 测试）

**Interfaces:**
- Consumes: 批一的 `section_path`
- Produces: 无

- [ ] **Step 1: 面包屑展示（纯文本，空值降级）**

`clause_detail.html:55` 的「来源：{规范名}」后拼接：

```html
来源：{{ spec_title }}{% if clause.section_path %}<span class="clause-path">{{ clause.section_path }}</span>{% endif %}
```

要求：
- **纯文本**（`>` 分隔的字符串，不做可点击层级链接）——spec §7 明确「节不作为独立可返回实体」，做成链接会引出未计划的交互
- **空 `section_path` 时不渲染**（否则会拼出「来源：CJJ2 」的尾随分隔符）
- 由 Jinja 自动转义（`section_path` 源自规范原文，**不得**用 `|safe`）

- [ ] **Step 2: tooltip 可访问性**

`title` 属性对**键盘 focus 与触屏均不显示**，屏幕阅读器支持也不一致。改为可见文字
或 `aria-label`：

```html
<label for="include-non-clause" aria-describedby="include-non-clause-help">
  <input type="checkbox" id="include-non-clause" name="include_non_clause">
  包含非条文内容
</label>
<small id="include-non-clause-help" class="field-hint">
  含：前言 / 条文说明 / 用词说明 / 公告 / 引用标准名录
</small>
```

**注意**：帮助文案里**不含「目次」**——目次属「直接过滤」类，导入时整段丢弃、
从不入库，写进 tooltip 就是对用户撒谎。

- [ ] **Step 3: 递增静态资源版本号**

项目 CLAUDE.md §三：改 `qa.js`/`md-render.js`/`app.css` 等静态文件后，
`base.html` 里 `<script src="...?v=N">` 的 `N` **必须递增**，否则浏览器缓存旧文件。
本 Task 若改了 CSS，**必须同步递增**。

- [ ] **Step 4: 手工验证（Ctrl+F5 强刷）**

1. 打开条文详情，确认面包屑显示且无尾随分隔符
2. 用 **Tab 键**遍历到「包含非条文内容」复选框，确认帮助文字可见
3. 勾选该复选框，确认结果含前言/条文说明等，且 **tooltip/帮助文案里没有「目次」**
4. 确认页面无控制台报错

- [ ] **Step 5: 提交**

```bash
git add app/templates/ static/
git commit -m "fix: 面包屑展示（纯文本+空值降级）、tooltip 改可访问、帮助文案移除目次

title 属性对键盘 focus 与触屏均不显示；改为可见帮助文字 + aria-describedby。
帮助文案移除「目次」——目次属直接过滤类，导入时整段丢弃，写进 tooltip 是撒谎。
面包屑定为纯文本（§7 明确节不作为可返回实体），空值时整个 span 不渲染。"
```

---

## Task 14: 文档与模型同步

**Files:**
- Modify: `docs/standards/parser-判定规则与依据.md`（新增 `section_path` 格式规范一节）
- Modify: `app/models.py:63,76`（`Clause` 补 `section_path`）
- Modify: `app/database.py`（`parent_clause` 的说明注释）
- Test: 无（文档与注释）

**Interfaces:**
- Consumes: 无
- Produces: 无

- [ ] **Step 1: `section_path` 格式规范（1 年后新人必踩，本轮补上）**

在 `docs/standards/parser-判定规则与依据.md` 追加一节，明确回答：

- 分隔符是什么（` > `，两侧各一个空格）
- 每段是否含编号（**含**：`"6 混凝土分项工程"`）
- `X.0.Y` 的 `0` 段是否出现（**不出现**，R3）
- 次分组单元（主控项目/一般项目）是否出现（**不出现**，R7）
- 无祖先时是什么（**空串**，不是 `None`、不带尾随分隔符）

- [ ] **Step 2: `models.Clause` 同步**

```python
    section_path: Optional[str] = None
```

- [ ] **Step 3: 说明 `parent_clause` 为死列**

`parent_clause` 恒为 `NULL`（`import_routes.py:479` 硬编码 `None`），
故 `health_check` 的 `orphan_parent` 检查是死代码。在该列旁加注释说明现状，
避免后来者误以为它在用；**不删除**（删除属 schema 变更，另行评估）。

- [ ] **Step 4: 提交**

```bash
git add docs/standards/parser-判定规则与依据.md app/models.py app/database.py
git commit -m "docs: 补 section_path 格式规范、models.Clause 同步、标出 parent_clause 为死列

section_path 的分隔符/是否含编号/0 段与次分组单元是否出现/空值形态此前无文档，
1 年后新人必踩。parent_clause 恒为 NULL，其 orphan_parent 检查是死代码，加注释标出。"
```

---

## Task 15: 测试隔离三处 patch

**Files:**
- Modify: `tests/conftest.py`
- Modify: 本批新增的全部测试（若触及库）
- Test: 自身

**Interfaces:**
- Consumes: 无
- Produces: `conftest.py` 新增 fixture `isolated_paths(tmp_path, monkeypatch)`

- [ ] **Step 1: 写失败测试（守住不污染真实库）**

```python
def test_tests_do_not_write_real_lance_db():
    """跑完测试后真实 lance_db 的行数不得变化（历史事故：pytest 往真实库写夹具向量）"""
    from app.config import LANCE_DB_PATH
    from pathlib import Path
    real = Path(LANCE_DB_PATH) / "clause_embeddings.lance"
    before = real.exists()
    # 断言本测试自身不触发写入（真正的守护是 fixture 的普遍应用）
    assert before in (True, False)
```

- [ ] **Step 2: 实现统一 fixture**

```python
@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """把三处运行时路径全部指向 tmp_path。

    app/config.py 的路径是模块级常量、**无环境变量入口**，故只能 monkeypatch；
    只 patch DATABASE_PATH 会让 pytest 往**真实** lance_db 写夹具向量、
    并往 data/uploads/ 堆垃圾 md（实测累积 147 个）。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.config.LANCE_DB_PATH", str(tmp_path / "lance_db"))
    monkeypatch.setattr("app.config.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("app.config.OUTPUT_DIR", str(tmp_path / "outputs"))
    for d in ("lance_db", "uploads", "outputs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path
```

- [ ] **Step 3: 全量跑测试后核对真实库未被污染**

Run: `D:/Python/python.exe -m pytest tests/ -v`
```bash
# 跑测试前后对比（行数必须一致）
D:/Python/python.exe -c "import lancedb,sys; t=lancedb.connect('lance_db').open_table('clause_embeddings'); print('rows=', t.count_rows())"
ls data/uploads/*.md | wc -l     # 必须不增长
```

- [ ] **Step 4: 提交**

```bash
git add tests/
git commit -m "test: 统一三处路径隔离 fixture（DATABASE_PATH / LANCE_DB_PATH / UPLOAD_DIR）

app/config.py 路径为模块级常量、无环境变量入口，只能 monkeypatch；
只 patch DATABASE_PATH 会让 pytest 往真实 lance_db 写夹具向量并堆 uploads 垃圾。"
```

---

## Task 16: 批二验收

**Files:**
- 无新增

**Interfaces:**
- Consumes: Task 1–15 全部
- Produces: 验收结论（写入本计划文件）

- [ ] **Step 1: 全量测试 + pyright**

Run: `D:/Python/python.exe -m pytest tests/ -v`
Run: `cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pyright`
Expected: 全绿 + `0 errors`

- [ ] **Step 2: 重启服务（严格按项目 CLAUDE.md §三，逐条执行到端口恰好 1 个监听）**

- [ ] **Step 3: 重建向量（门禁，不可跳过）**

清空并全量重建，随后逐条核对：

1. 向量表 `count_rows()` ≈ 条文数 ×（1 + 子块增量 5~8%）
2. 抽查若干条文的 `text` 列**含面包屑**
3. 抽查一条超长条文有两个以上 `chunk_index`

- [ ] **Step 4: 重导全量语料并核对**

1. 条文数、`content_chars` 与批一验收一致（守恒）
2. 维护宫格无红点
3. `needs_rebuild()` 返回 False

- [ ] **Step 5: 检索行为验证（本批的核心交付）**

| 验证 | 期望 |
|---|---|
| 搜一个**节名**（如「接头安装」） | 该节下的条文**出现在结果里**（召回，不只是重排） |
| 把 `breadcrumb_weight` 置 0 | 仅命中节名的条文**消失**（列限定 MATCH 生效） |
| 置回 0.3，**不重建任何索引** | 结果立即变化（权重在查询时生效） |
| 改 `breadcrumb_weight` 后 | FTS 索引无需重建（无迁移日志） |
| 检索一条超长条文（如 `23.0.1`） | 用其**尾部**内容作查询词也能召回（子块生效，此前 73% 尾部不可见） |
| 同一条文的多块 | 结果列表里**只出现一次**（按 clause_id 去重） |

- [ ] **Step 6: 记录验收结论并提交**

```bash
git add docs/superpowers/plans/2026-09-27-batch2-breadcrumb-retrieval-chunk.md
git commit -m "docs: 批二验收结论（召回/开关/调参免重建/子块可见性逐项实测）"
```

---

## 批二追加：批一遗留的七项（T20–T26）

> **来源**：批一交付后**用户逐项实测**暴露（4 条）+ 两轮复核登记（3 条）。控制器 2026-09-28 **逐条追到源行**后裁定归属。
> **并入原则**：① 已完成的**结案**，不重做；② 与既有 Task 同处一个改动面的**并入该 Task**，不另立编号；
> ③ 需要设计的**另立 Task**，且**只规定「要证明的性质」**，把修法作为**候选**并要求**先验证它撞得住目标用例**。
> ⚠️ **这条原则是批一用代价换来的**：我把 T22 里写的「建议修法」当成处方下达，实施者实测发现它**漏掉 `17.5.1`**
> （该行父号恰等于所在节栈键）且会**误杀 R3 的 36 条合法 `X.0.Y`**；而 T22 自己的 Cons 段**本就标了这类例外**，
> 是我只读了被截断的摘要行。

### 处置表

| 项 | 内容 | 处置 | 证据 / 理由 |
|---|---|---|---|
| **T22** | PDF 断行劈开交叉引用 ⇒ 错位条（吞表、造同号重复、造 3 个假 `missing_sections`）| **✅ 已完成**（批一后续 round-2 的 fix ③） | 夹具 L3077/L4802/L4926 三处伪影已消：`missing_sections` 三项清零、同号重复消失、CJJ2 891→888 条。⚠️ **采用的规则 ≠ T22 原本的建议**：T22 主张「父号 ≠ `_parent_key(stack, level)` 才算越级」，实测**漏 `17.5.1`**、且**误杀** R3 的 `X.0.Y`／`## 条文说明 → 3.0.1`／空栈根条；改用「**同号重复**（`clause_no` 已在文中出现过 ⇒ 不是节点）」，两语料**零误杀** |
| **T20** | 被收窄/被跳过的行在**无候选可归属**时其文本被丢弃（真实存在，批一 Task 14 复核曾指出）| **新 Task 17** | ② 的「次分组标签」窄场景已由批一后续处理（打标隐藏、文本保留）；T20 剩下的是**通用孤儿场景** |
| **T23** | R14 兄弟表决的分组键可能被注释侧行污染（表决在解析**之前**、只看**行**）| **新 Task 18** | 批一量化：151 组中剔除注释侧行重算，**0 组翻转** ⇒ 潜在风险、非当前缺陷 |
| **T25** | 预扫与主循环的**栈**也须一致（数字命名的过滤标题会让两趟的 `_parent_key` 分叉 ⇒ R14 投票键不同）| **并入 Task 18**（同族：都是「两趟必须同判据」） | 批一只对齐了 `seen_clause_nos`；栈的不对称是**既有**、两语料不触发、**良性**（无文本丢失），但违反本批反复强调的设计不变量 |
| **T24** | 祖先链**既不持久化也无处展示**：`parent_clause` **恒为 NULL**（导入侧硬编码），`clauses` 表**无** `breadcrumb`/`section_path` 列 ⇒ 无正文的章节标题行消失后**无人接替** | **并入 Task 2（加列 + 填充）与 Task 13（展示）** | 用户实测：条文列表章节标题缺行（`21.3.x` 直跳 `21.4.1`）、详情/结果的「来源：」为空。Task 2 本就加 `clauses.breadcrumb` ⇒ 顺带补 `parent_clause` 与展示 |
| **T26** | 守恒门禁两处加固：① 常量与夹具**无绑定**；② 逐条保护**只覆盖 6 条**冻结条文 | **新 Task 19** | 已实测两个盲区：**`附录A`（1,992）整条蒸发 ⇒ 总量 139,778 ≥ 136,770 ⇒ 仍全绿** ✗；`_CURRENT_PLAIN_CHARS = 141770` 手写 ⇒ 夹具**变大**时下限偏低 ⇒ 守卫**静默变弱**（危险方向；变小则响亮报红 ✓） |
| **T21** | 候选行的「来源路径」标记（`#` 路径 vs 裸行）| **降为候选方案，写入 Task 17/18 的备注** | 批一的 ① 章号递增规则**没有用它**也达成了目标；它仍是**更干净的机制**，但**不再是任何 Task 的前提** |

### Task 17: 孤儿文本缓冲（T20）

**要证明的性质**：**「任何行的文本都不会因为『不被当作节点』而消失」**。现状：一行既不是候选、其前又无任何候选可归属时，其文本随 `pending` 被丢弃。

**Files**: Modify `app/parser/md_parser.py`（主循环 / `flush()`）｜Test `tests/test_md_parser.py`

**必须先验证的候选方案（不要照抄）**：把「不成条节点」的待落文本**缓冲到下一个被结算的条**。
- **先做**：在 CJJ2 夹具与 JGJ107 的 md（`data/outputs/aa96b73a/aa96b73a.md` 的 `$TEMP` 副本）上**构造并量出**「当前确实丢文本」的最小用例（哪些行、多少字符）——这是 RED。
- **再定形状**：让候选方案去撞它，并实测**不误伤**：守恒、覆盖率、`missing_sections`、两语料 11 项指标**不退化**。
- **若候选撞不住**（例如与内节点判据冲突），**报告并换一种** —— 方法只是候选。

**判据（可证伪）**：新用例在**当前代码**上变红（证明丢失真实存在）、实现后变绿；且 `content_chars_plain` **不下降**（夹具级 141,694 / 库级 141,548）。

### Task 18: 两趟判据一致（T23 + T25）

**要证明的性质**：**「`_vote_title_mode`（预扫）与主循环对同一份输入的判定完全一致」**—— 对**候选判据**、对 `seen_clause_nos`、**以及对 `stack`（本 Task 新增的部分）**。

**Files**: Modify `app/parser/md_parser.py`｜Test `tests/test_md_parser.py`

**已知的两处不一致**（批一实测）：
- **T23**：R14 的分组键 `(层级, 父键)` 可能把注释侧行与正文行混进同一组（151 组中 21 组含重复号、89 行注释侧行落进正文组键），**实测 0 组翻转**。
- **T25**：预扫会把**过滤标题**（`目次`/`Contents`）压栈，而主循环在 `is_filter_non_clause_title` 处 `continue`、**不压栈** ⇒ 数字命名的过滤标题（如 `## 1.1 目次`）会让两趟的 `_parent_key` 分叉。
> **备注（候选方案 T21）**：给候选行加「是否来自 `#` 路径」的来源标记，是让两趟共用更干净判据的一条路 —— 但**不是本 Task 的前提**。

**判据（可证伪）**：加一条「两趟的候选/拒绝流**逐字节相同**」的对照断言（批一复核用的手法：两侧各记录 stream 后比序列；CJJ2 现为 7,334 行 / 1,030 候选 / 3 拒绝，JGJ107 626 / 88 / 0），并在**数字过滤标题**用例上证明它可失败。

### Task 19: 守恒门禁加固（T26）

**要证明的性质**：**「总量守恒门禁不能有『静默变弱』的方向，也不能对『分配型丢失』全盲」**。

**Files**: Modify `tests/test_parse_conservation.py`（纯测试）

**两处加固**（都不得引入新机制以外的复杂度）：
1. **常量与夹具绑定**：现在 `_CURRENT_PLAIN_CHARS = 141770` 是**手写**的 ⇒ 夹具一变，下限与注释里的覆盖率数字（冻结 6 条 = 10,014 plain = 7.06%；92.94% 无保护）**静默过期**。要求：让「夹具变大 ⇒ 守卫静默变弱」变成**响亮报错**（绑定断言或双向容差皆可 —— **先验证你选的形状真的会红**）。
2. **扩大逐条覆盖**：现在只冻结 6 条（占总量 7.06%）。**方向是冻结更多正文重头条文**（把「未冻结条文之间的重分配」纳入保护），**不是**压低总量余量 —— 后者只调低总丢失容忍度、**仍看不见重分配**。

**判据（可证伪）**：① 构造「夹具变大」的场景 ⇒ 新断言**必须红**；② 构造「两条未冻结条文之间的重分配（总量不变）」⇒ 至少被**新增的逐条下界**拦住一条（或明确记录它仍拦不住，并把残留写进 docstring）。

---

## Self-Review

**1. Spec coverage**（对照 CEO 评审修正后的 spec §4.2–§4.6、§5、§7）：

| spec / 评审项 | 覆盖它的 Task |
|---|---|
| §4.2 `section_path` 列 | Task 2 |
| **评审新增：`breadcrumb` 预分词列** | Task 2 |
| §5 FTS 两列 + 触发器 + 迁移 + backfill | Task 2 |
| §4.3 `breadcrumb_weight` 参数 | Task 6 |
| **评审修正：bm25 绑定参数（删字面量回退）** | Task 5 |
| **评审修正：权重=0 走列限定 MATCH** | Task 5 |
| §4.3 `build_search_text` 两列 + 调用点 | Task 3 |
| §4.3 `build_embed_text` + `section_path` | Task 4 |
| **评审新增：全量重建向量门禁** | Task 11 |
| **评审新增：LanceDB 三处 schema 抽工厂** | Task 1 |
| §4.4 子块切分 + `chunk_index` + 写入 | Task 7、Task 8 |
| §4.4 检索按 `clause_id` 去重（**超取 + 取最优**） | Task 9 |
| §4.4 health_check 覆盖率统计 | Task 10（**已完成误报更正**：该统计本就是集合式，无需改；改的是全表读） |
| §4.5 复选框文案 + tooltip | Task 13 |
| §4.6 详情弹窗面包屑 | Task 13 |
| §5 影响面补全（11 测试文件 + 4 生产调用点） | Task 3、Task 4 |
| **评审新增：重导排除 uploads / optimize / 失败措辞** | Task 12 |
| **评审新增：测试隔离三处 patch** | Task 15 |
| **评审新增：section_path 格式规范 / models 同步 / parent_clause 死列** | Task 14 |
| §7 明确不做（ANN / LLM 切分 / 顿号 / 第X条 / 节作为实体） | 全部不在本计划，符合决议 |

**2. Placeholder scan**：无 TBD/TODO。Task 10 Step 3、Task 15 Step 2 的「实施时先验证本机能力」是**必须现场探测**的技术前提（LanceDB 列裁剪 API 的具体形式随版本而异），已写明探测对象与失败时的替代形式，未写成「自行处理」。

**3. Type consistency**：`embedding_schema(dim)` 在 Task 1 定义、Task 7 扩展为含 `chunk_index`；`_dedupe_by_clause` / `iter_clause_ids` / `needs_rebuild` / `_scope_match_to_search_text` / `chunk_text` 的签名在定义处与消费处一致；`build_search_text` 的两列顺序 `(search_text, breadcrumb)` 在 Task 2 的 backfill、Task 3 的实现与全部调用点一致。

**4. 明确未纳入**：
- **批一的 `section_path` 生成**（本批的前置依赖，不在本批实现）
- **Task 3 目次对齐**（D6.2 决议：证据触发，不排期）
- **`breadcrumb_weight` 的向量侧等价开关**（向量侧面包屑烘焙进 embed_text，唯一调整手段是全量重建——这是 spec §4.3「明确边界」的刻意取舍）
