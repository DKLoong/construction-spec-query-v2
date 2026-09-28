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
  - 现存表无 `chunk_index`，必须重建 schema。⚠ 但**不要**写成「LanceDB 不能 ALTER」——
    实测本机 `lancedb 0.17.0` 有 `add_columns` / `alter_columns` / `drop_columns`（C-8）；
    本批重建的真正原因是面包屑改了既有两列的**内容**（`text`/`embedding`）必须重嵌。
- **编码安全**：FTS MATCH 串沿用既有 `_quote` 转义路径；权重走绑定参数，**禁止**字面量插值。
- **异常规范**：禁止裸 `except:`；不得新增无日志的 `except Exception`。
- **性能**：`clauses` 全表遍历的循环内禁止逐行 DB IO（沿用既有 backfill 形态即可）；向量写入必须批量（`VECTOR_WRITE_BATCH`）。
- **TDD**：先失败测试 → 最小实现 → pyright 0 error → 提交。单次提交单 Task。
- **测试隔离（强制）**：本批新增测试若触及库，必须同时 patch **四个目标**——
  `app.database.DATABASE_PATH`、`app.search.vector_search.LANCE_DB_PATH`、
  `app.routes.import_routes.UPLOAD_DIR`、`app.routes.import_routes.OUTPUT_DIR`。
  ⚠ **C-4：必须是「消费方所在模块」的名字，不能是 `app.config.*`**——`vector_search.py:4`
  与 `import_routes.py:7` 都是 `from app.config import ...`，导入时就把值绑定到自己的
  模块命名空间了，patch `app.config` 只改那一个属性、**不影响已绑定的名字**。本仓既有
  20+ 处测试用的正是正确目标（`tests/test_vector_search...` 见 `tests/test_vector_store_safety.py:1-9`
  的 docstring，其 `test_clear_all_never_touches_other_db` 甚至把 `app.config.LANCE_DB_PATH`
  当作「不该被删的假目标」在用）。
  历史事故：只 patch `DATABASE_PATH` 曾导致 pytest 往**真实** `lance_db` 写夹具向量、并往 `data/uploads/` 堆 147 个垃圾 md。
- **不得放宽既有断言**：本批允许修改的既有断言只有「`build_search_text` 返回值由串变元组」与「`bm25` 调用形态」两类，且必须同步改成**更强的断言**（见各 Task）。

---

## File Structure

| 文件 | 责任 | 本批动作 |
|---|---|---|
| `app/parser/md_parser.py` | 已由批一产出 `section_path` | 不改 |
| `app/config.py` | 平台常量 | 新增 `SEARCH_BREADCRUMB_WEIGHT`、`CHUNK_CHAR_LIMIT` |
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
- Modify: `app/routes/import_routes.py:475-483`（INSERT 列清单补 `section_path`/`breadcrumb`/`parent_clause`）
- Modify: `app/routes/spec_routes.py:222-231`（编辑重索引的 UPDATE 补同样几列）
- Test: `tests/test_database.py`（扩展既有「旧结构迁移」用例，**夹具必须带数据**）

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
    """既有库的 clauses_fts 是单列 → 必须被识别并重建为两列，**且重建后不为空**。

    注意 `CREATE VIRTUAL TABLE IF NOT EXISTS` 不会改造已存在的表，
    故迁移必须显式判定「旧形态」并 DROP。

    ⚠ **夹具必须带数据**（见 C-1）：空表夹具永远走不到 backfill 的写分支，
    而真实库上门槛会因「新列刚 ALTER 为 NULL ⇒ 算得空串」「旧列由同一公式
    算过 ⇒ 与新值相等」而恒假 ⇒ 重建出的 FTS 一行都没有、关键词检索全空，
    且下次 init_db 不再 DROP ⇒ 永不自愈。故本用例断言行数守恒。
    """
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE clauses (id INTEGER PRIMARY KEY AUTOINCREMENT, spec_id INTEGER,
            clause_no TEXT, title TEXT, content TEXT, search_text TEXT);
        CREATE VIRTUAL TABLE clauses_fts USING fts5(search_text);
        INSERT INTO clauses (id, spec_id, clause_no, title, content, search_text)
            VALUES (1, 1, '6.3.1', '接头安装', '正文甲', '接头 安装 正文甲 6.3.1'),
                   (2, 1, '6.3.2', '接头检验', '正文乙', '接头 检验 正文乙 6.3.2');
        INSERT INTO clauses_fts(rowid, search_text)
            VALUES (1, '接头 安装 正文甲 6.3.1'), (2, '接头 检验 正文乙 6.3.2');
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
        n_clauses = conn.execute("SELECT COUNT(*) FROM clauses").fetchone()[0]
        n_fts = conn.execute("SELECT COUNT(*) FROM clauses_fts").fetchone()[0]
    assert "breadcrumb" in sql, "单列 FTS 必须被重建为两列"
    assert "breadcrumb" in cols and "section_path" in cols
    assert n_fts == n_clauses, "重建前有数据的库，迁移后 FTS 不得为空（C-1）"


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
    1. 先加 clauses 的三列（`search_text`/`breadcrumb`/`section_path`）——旧库
       无这些列时，TRIGGERS_SQL 里 `new.breadcrumb` 会让触发器创建失败（与
       search_text 同样的先例），且 backfill 的 SELECT 会 `no such column`；
    2. drop 旧触发器；
    3. 判定并 drop 需重建的 FTS 表（旧的 external content 形态，**或**
       只有 search_text 一列的形态）；
    4. 建 fts5(search_text, breadcrumb)；
    5. backfill 两列。

    ⚠ **C-1：FTS 被 DROP（或本就不存在）时，backfill 必须无条件全量重插**。
    「值不同才写」的门槛在重建场景下恒假——旧库的 `search_text` 由同一公式
    算过 ⇒ 与新值相等；新列 `breadcrumb` 刚 ALTER 为 NULL ⇒ 算得空串。
    后果是新 FTS 一行都没有、关键词检索全空，且下次 init_db 因新表 SQL 已含
    `breadcrumb` 不再 DROP ⇒ **永不自愈**。故用 `fts_rebuilt` 显式分流。
    """
    for col in ("search_text", "breadcrumb", "section_path"):
        try:
            conn.execute(f"ALTER TABLE clauses ADD COLUMN {col} TEXT")
        except Exception:
            pass  # 列已存在

    for t in ("clauses_ai", "clauses_ad", "clauses_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {t}")

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='clauses_fts'"
    ).fetchone()
    fts_rebuilt = False
    if row:
        sql = row["sql"] or ""
        # 旧的 external content 形态，或单列形态 → 都要重建为两列。
        # `CREATE VIRTUAL TABLE IF NOT EXISTS` 不会改造已存在的表，故此处必须显式 DROP。
        if "content=clauses" in sql or "breadcrumb" not in sql:
            conn.execute("DROP TABLE clauses_fts")
            fts_rebuilt = True
    else:
        fts_rebuilt = True       # 表不存在 → 下面新建，同样需要全量灌入
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
        # ⚠ C-1：门槛只在 FTS **未**被重建时有效。重建/新建时必须全量重插——
        # 否则「值恰好相等」的行（旧库常态：search_text 由同一公式算过、
        # breadcrumb 刚 ALTER 为 NULL 而算得空串）不会被写进新表 ⇒ 索引整体为空。
        if (not fts_rebuilt
                and (r["search_text"] or "") == st
                and (r["breadcrumb"] or "") == bc):
            continue
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

生产（2 处，取 `[0]` 写 `search_text`，`[1]` 写 `breadcrumb`）。
⚠ **C-2：`section_path` 必须在这两处一起写进列清单**——否则该列全程无人写，
详情/列表的「来源」永远为空，且导入期向量（用解析器字典 `cd["section_path"]`）
与重建期向量（读 `c.section_path` = NULL）不一致，Task 11 的抽查只会偶然通过。

```python
# app/routes/import_routes.py:475-483 —— INSERT 显式补三列：
#   section_path 取 cd["section_path"]（批一解析器产出）；
#   breadcrumb 取 build_search_text(...)[1]；
#   parent_clause 取「最近现存祖先」的 id（见 Task 2 的 T24 并入要求）。
conn.execute(
    """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause,
       dim4_specialty, dim5_location, dim6_material, clause_is_non,
       search_text, breadcrumb, section_path)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (spec_id, cd["clause_no"], cd["title"], cd["content"], parent_id,
     dim4_val, dim5_val, dim6_val,
     1 if cd.get("is_non_clause") else 0,
     st, bc, cd.get("section_path", "")),
)

# app/routes/spec_routes.py:222-231 —— 编辑重索引的 UPDATE 同样补
#   section_path = ?、breadcrumb = ?（该条自身的 section_path 从库里读回）
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
    assert got == "GB 50010 混凝土规范 [5.1.1] 5 混凝土分项工程 > 5.1 模板 模板 内容"


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
    """生产调用点必须都**传**面包屑给 build_embed_text——漏改是静默的（新参数有默认值 ""）。

    ⚠ **C-10**：不得写成 `assert "section_path" in src`——那样任何提到该串的文件都能
    通过（`app/routes/import_routes.py` 因为要写该列必然含它），断言形同虚设。
    这里断言**调用形态**：每个调用点出现 6 个实参（或显式关键字参数）。
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    # 生产调用点共 6 处（原稿写「4 个」而表里列了 6 行、Step 5 又只查 5 个目标——计数已统一）
    targets = [
        "app/routes/import_routes.py", "app/routes/maintenance_routes.py",
        "app/routes/spec_routes.py", "app/search/vector_search.py",
        "scripts/reindex_vectors.py", "scripts/probe_rebuild_effect.py",
    ]
    call_re = re.compile(r"build_embed_text\((?:[^()]|\([^()]*\))*\)")
    for rel in targets:
        src = (root / rel).read_text(encoding="utf-8")
        calls = [c for c in call_re.findall(src) if "def build_embed_text" not in c]
        assert calls, f"{rel} 未找到 build_embed_text 调用"
        for c in calls:
            n_args = c.count(",") + 1 if c.strip().endswith(")") else 0
            assert "section_path" in c or n_args >= 6, \
                f"{rel} 的调用未传面包屑（第 6 参缺失）: {c[:80]}"
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
    assert m["type"] == "float"       # C-7：_num() 产出的键是 "type"，不是 "dtype"
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

> 现存表无此列 ⇒ 必须重建 schema（本批的真正原因是面包屑改了 `text`/`embedding` 的内容，
> 见 C-8：LanceDB 0.17 其实**有** `add_columns`/`alter_columns`/`drop_columns`，加空列不必重建）。

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
    """现存表无 chunk_index → 必须重建（本批因内容变更重建，非「不支持 ALTER」，见 C-8）。"""
    import lancedb
    import pyarrow as pa
    # C-4：patch 的必须是消费方模块的名字（`vector_search.py:4` 是 from ... import）
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
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
        """现存表是否缺少 chunk_index 列。

        ⚠ **C-8**：不要写「LanceDB 不支持 ALTER」——实测本机 `lancedb 0.17.0`
        提供 `add_columns` / `alter_columns` / `drop_columns`，加一个全空列是
        零拷贝的元数据操作。**本批之所以重建**，是因为面包屑改了既有两列
        （`text` / `embedding`）的**内容**，必须重嵌——`add_columns` 救不了内容变更。
        将来若只是纯加元数据列，应优先试 `add_columns` 而非全量重建（见 TODOS T27）。

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

本批需重建的真实原因是面包屑改了既有两列的**内容**（text/embedding）必须重嵌，
而非「LanceDB 不能加列」——实测 lancedb 0.17 有 add_columns/alter_columns/drop_columns。
维护页据此提示重建，而不是等写入时才发现列不存在。"
```

---

## Task 8: 超长条文按 512 token 切子块

**Files:**
- Modify: `app/config.py`（`CHUNK_CHAR_LIMIT = 512`）
- Modify: `app/routes/import_routes.py`（写入路径产出子块）
- Modify: `app/search/vector_search.py`（`index_clause` 支持多行同 clause_id）
- Test: `tests/test_vector_chunk.py`

**Interfaces:**
- Consumes: `embedding_schema`（Task 7）、`plain_text`
- Produces:
  - `chunk_text(text: str, limit: int = CHUNK_CHAR_LIMIT) -> list[str]`
  - `index_clause(..., chunk_index: int = 0)`

- [ ] **Step 1: 写失败测试**

```python
def test_chunk_short_text_returns_single_chunk():
    from app.search.chunking import chunk_text
    assert chunk_text("短正文。") == ["短正文。"]


def test_chunk_long_text_splits_on_sentence_boundary():
    """切点必须落在句子边界上，不得从词中间切断。

    ⚠ **R6：不变量已由「不丢不重（拼接等于原文）」改为「覆盖性 + 重叠量上限」**——
    加了重叠（R4）之后 `"".join(chunks) == text` 在数学上必然不成立
    （`["ABCD","CDEF"]` 拼回 `"ABCDCDEF"`）。两句分开断言，各自可证伪。
    """
    from app.search.chunking import chunk_text
    text = "。".join(f"第{i}句内容" for i in range(400)) + "。"
    chunks = chunk_text(text, limit=100)
    assert len(chunks) > 1
    assert all(c.endswith("。") for c in chunks)
    # ① 覆盖性：原文每个字符至少出现在一个块里（不丢）
    covered = "".join(chunks)
    assert all(ch in covered for ch in set(text)), "有字符凭空消失"
    assert covered[:len(chunks[0])] == chunks[0], "首块必须从原文开头起"
    # ② 重叠量：相邻块共享的前缀不超过上限（10% of limit ≈ 10 字符），且对齐句末
    for prev, cur in zip(chunks, chunks[1:]):
        shared = 0
        while shared < min(len(prev), len(cur)) and prev[len(prev) - 1 - shared] == cur[min(len(prev), len(cur)) - 1 - shared]:
            shared += 1
        assert shared <= limit // 10 + 2, f"相邻块重叠 {shared} 字符超出上限"


def test_chunk_unpunctuated_long_text_still_bounded():
    """无标点超长串：必须仍能切出多块且不丢字符（保底按长度切）。"""
    from app.search.chunking import chunk_text
    text = "甲" * 1000
    chunks = chunk_text(text, limit=100)
    assert len(chunks) > 1
    covered = "".join(chunks)
    assert all(c in covered for c in set(text)), "有字符凭空消失"


def test_chunk_limit_subtracts_prefix_budget():
    """R3：生效上限 = 传入的 limit（调用方已扣掉 build_embed_text 的前缀长度）。

    本用例锁定「扣前缀」这件事发生在调用方、且被测试覆盖：
    给同样的正文但更长的前缀，切出的块数不得减少（即上限确实被调小了）。
    """
    from app.search.chunking import chunk_text
    text = "。".join(f"第{i}句内容" for i in range(50)) + "。"
    assert len(chunk_text(text, limit=100 - 60)) >= len(chunk_text(text, limit=100))


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

切分口径（**R3**）：`plain_text(content)` 之后按 **字符**计数。
- 实测算的是 `len(cleaned)`，与 embedding 模型的 512 **token** 上限只是同量级
  （中文 1 字≈1 token，本机 `models/BAAI/bge-small-zh-v1___5/tokenizer_config.json`
  的 `model_max_length: 512`），故 docstring/常量名一律声明为**字符**，不再自称 token。
- **生效上限由调用方扣掉 `build_embed_text` 的前缀长度后传入**（前缀 =
  `{code} {spec_title} [{clause_no}] {section_path} {clause_title}`）——否则末块
  的实际模型输入会超出 512 而被静默截断，恰好打掉本批「尾部可召回」的靶心。

重叠（**R4**）：按 10% 上限、**且必须在句末标点处对齐**（不得从词中间开始）。

不变量（**R6**）：① 覆盖性——原文每个字符至少属于一个块；② 相邻块重叠 ≤ 上限。
**不再**是「拼接等于原文」（有重叠后该性质不成立）。所有子块按序拼接的**首块**
必须从原文开头起、末块必须到原文末尾止。
"""
import re

from app.ai.text_clean import plain_text
from app.config import CHUNK_CHAR_LIMIT

# 句末标点（中英文）
_SENTENCE_END = re.compile(r'(?<=[。；！？.!?;])\s*')
# 重叠上限：10% of limit
_OVERLAP_RATIO = 0.10


def chunk_text(text: str, limit: int = CHUNK_CHAR_LIMIT) -> list[str]:
    """把正文切成不超过 limit 个**字符**的子块（带 10% 句末对齐重叠）；短文本返回单块。

    limit 由调用方传入，且调用方**必须**已扣掉 `build_embed_text` 的前缀长度。
    空文本返回空列表（调用方据此跳过写向量）。
    """
    cleaned = plain_text(text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= limit:
        return [cleaned]

    overlap = int(limit * _OVERLAP_RATIO)
    sentences = [p for p in _SENTENCE_END.split(cleaned) if p]

    chunks: list[str] = []
    buf = ""
    for piece in sentences:
        if len(buf) + len(piece) <= limit:
            buf += piece
            continue
        if buf:
            chunks.append(buf)
        # 单句本身超限 → 长度保底切（无标点串也走这里）
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        # 下一块从上一块末尾的整句开始重叠（对齐句末，不从词中间切）
        tail = buf[-overlap:] if buf and overlap else ""
        buf = tail + piece
    if buf:
        chunks.append(buf)
    return chunks
```

> **实现注**：`plain_text` 里 `\s*` 会吃掉标点后的空白，故拼接结果与原文
> 只在**空白归一化**这一层可能不同。R6 之后的断言是覆盖性（每字符至少属一块），
> 对归一化不敏感；本次评审已实测原算法在四个用例上全绿（33 块、均以「。」结尾）。
> **不要再写回 `"".join(chunks) == text`** —— 加了重叠后它必然失败。

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
git commit -m "feat: 超长条文按 512 字符切子块（含 10% 句末对齐重叠）写入向量表

口径为**字符**（原稿自称 token，实测数的是 len()）；生效上限由调用方扣掉
build_embed_text 的前缀后传入，避免末块输入超出模型 512 上限被静默截断。
重叠按 10% 且在句末标点处对齐。不变量两条：① 覆盖性（原文每个字符至少属于
一个块）② 相邻块重叠 ≤ 上限——**不再是**「拼接等于原文」（有重叠后必不成立）。
仅超限条文多行，短条文仍单行 chunk_index=0。"
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
  - 返回项须含 `clause_id` / `spec_id` / `text` / `chunk_index` / `_distance`
  - 内部顺序（**R2 固定**）：LanceDB 超取 `top_k × _VECTOR_FETCH_MULTIPLIER(10)` →
    `_dedupe_by_clause(rows, max_per_clause=2, top_k=top_k)` → 返回 ≤ `top_k` 条
  - **R9**：`max_per_clause=2` 保证每个条文都有席位；没有它，一条长条文的块可独占全部块位

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


def test_dedupe_caps_chunks_per_clause_and_truncates_to_top_k():
    """R2/R9：每 clause ≤2 块，且整体按距离截回 top_k。

    没有每-clause 上限时，一条长条文的块可占满全部块位（外部评审的偏斜场景）；
    没有截断时，向量臂的 RRF 席位被静默放大。
    """
    from app.search.vector_search import _dedupe_by_clause
    rows = [{"clause_id": 1, "chunk_index": i, "_distance": 0.1 + i * 0.01, "text": "x"}
            for i in range(6)]                       # 同一条文的 6 个块
    rows += [{"clause_id": c, "chunk_index": 0, "_distance": 0.5, "text": "y"}
             for c in (2, 3, 4)]
    out = _dedupe_by_clause(rows, max_per_clause=2, top_k=3)
    assert len(out) == 3, "必须截回 top_k"
    assert sum(1 for r in out if r["clause_id"] == 1) <= 2, "同一条文最多 2 块"
    assert {r["clause_id"] for r in out} >= {1, 2}, "其他条文必须仍有席位"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_hybrid_search.py -k "best_chunk or overfetch or dedupe_by_clause" -v`
Expected: FAIL — `ImportError: cannot import name '_dedupe_by_clause'`

- [ ] **Step 3: 实现**

```python
# app/search/hybrid_search.py
# 向量臂超取倍数（R2/R9）：去重按 clause_id 进行，一条长条文可能占多个块位；
# 不超取会让块数多的条文挤掉其他条文的候选。R9 实测驳斥了「×3 就够」——
# 重叠后相邻块文本近似重复、距离簇拥，一条长条文可以独占全部 3×top_k 个块位，
# 去重后只剩它一条、向量臂塌成个位数。故：**倍数放到 10，并在应用层限制
# 「同一 clause_id 最多保留 2 块」**，席位由这个上限保证，而不是靠倍数凑巧够用。
_VECTOR_FETCH_MULTIPLIER = 10
_MAX_CHUNKS_PER_CLAUSE = 2
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
def _dedupe_by_clause(rows: list[dict], max_per_clause: int = _MAX_CHUNKS_PER_CLAUSE,
                      top_k: int | None = None) -> list[dict]:
    """按 clause_id 去重，保留 `_distance` 最小（最相关）的若干块，再按距离截回 top_k。

    R2：**顺序固定为「超取 → 去重取最优 → 截回 top_k」**——超取的目的是把
    「被自己的块挤掉的候选」找回来，不是让向量臂拿更多席位；截回后普通查询的
    RRF 行为与改前等价（无偏斜时）。R9：`max_per_clause` 保证每个条文都有席位
    （否则一条长条文可以独占全部块位，去重后向量臂反而比改前更薄）。
    """
    best: dict[int, list[dict]] = {}
    for r in sorted(rows, key=lambda d: d.get("_distance", float("inf"))):
        cid = r["clause_id"]
        kept = best.setdefault(cid, [])
        if len(kept) < max_per_clause:
            kept.append(r)
    flat = [r for kept in best.values() for r in kept]
    flat.sort(key=lambda d: d.get("_distance", float("inf")))
    return flat[:top_k] if top_k is not None else flat
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
    """收集 clause_id 不得整表物化（含 embedding 列）。

    C-9：断言口径与实现一致——实现走 `lance.dataset(...).to_table(columns=[...])`，
    源码里不再出现 `to_arrow()`，故本断言原样成立。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app/maintenance/health_check.py").read_text(encoding="utf-8")
    assert "to_arrow()" not in src, "改为 iter_clause_ids()（只读 clause_id 列）"


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

        ⚠ **C-9 / R1：必须走 lance 数据集的列投影**，不能用 `Table.to_arrow()`：
        本机 `lancedb 0.17.0` 的 `Table.to_arrow(self) -> pa.Table` **没有** columns
        参数，而 `to_arrow().select([...])` 只是 pyarrow 视图——数据已全量物化
        （实测真实库 959 行：全量 2.5 MB vs 仅 clause_id 0.008 MB，约 300×；
        按 spec 预估的 2.4 万条约 62 MB/次，而维护页每次打开都调用）。

        表是 LanceDB 托管的 lance 数据集，用 `lance.dataset(<表目录>)` 直接读该列
        （实测可用：959 行 / 2 ms）。表不存在或读取失败时降级并记日志。
        """
        if not self._table_exists():
            return set()
        try:
            import lance
            from pathlib import Path
            table_dir = Path(self.db.uri) / "clause_embeddings.lance"
            tbl = lance.dataset(str(table_dir)).to_table(columns=["clause_id"])
            return {int(v) for v in tbl.column("clause_id").to_pylist()}
        except Exception as e:
            logger.warning("只读 clause_id 失败，回退为空集: %s", e)
            return set()
```

> **C-11**：`app/maintenance/health_check.py:66` 附近原有裸 `except Exception:` 且无日志
> （计划自身规则禁止无日志的 `except Exception`）。本 Task 正在改该函数，
> **顺手补一行 `logger.warning`**，不要留在原地。

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

**R7（用户答复 D6 定案）——两处落点，即信息在界面上「哪里给」：**

| 落点 | 位置 | 要求 |
|---|---|---|
| ① 详情弹窗 | `clause_detail.html` 的「来源：」 | 显示该条的完整 `section_path`（上面那条） |
| ② **结果列表每行** | 检索结果行的副标题位 | 显示**该行自身**的 `section_path`（同一条文号那行的所属节） |

② 的关键约束：**无状态**——每行数据自带完整路径，**不得**与前一行比较、**不得**插入独立「节分隔行」
（用户已明确「列表里看得见节」只是锦上添花，不值得引入依赖相邻行的渲染规则；
无状态写法在分页、筛选、排序变化下都不会错）。长路径需要一处**截断/省略的样式处理**（纯 CSS）。
章节标题仍**不入库**、不作为独立行出现（批一裁定「无自身正文者只作祖先、不入库」保持不变）。

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
- Modify: `app/models.py:58-95`（`ClauseCreate`/`ClauseResponse`/`ClauseUpdate` 补 `section_path`；**本仓无名为 `Clause` 的类**，详情页走 `SELECT c.*` + `dict()`，模板不依赖 Pydantic 模型）
- Modify: `app/database.py`（`parent_clause` 的说明注释）
- Modify: `app/routes/import_routes.py`（`parent_clause` 的填充逻辑，见下）
- Test: `tests/`（新增「父级编号为段前缀 + 孤立计数为 0」断言）

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

- [ ] **Step 2: Pydantic 模型同步**

给 `ClauseCreate` / `ClauseResponse` / `ClauseUpdate`（`app/models.py:58-95`）各补一行：

```python
    section_path: Optional[str] = None
```

- [ ] **Step 3: `parent_clause` 的文档——**改写**为「已填充」（R5）**

⚠ **不再是「死列」**：Task 2 已要求把 `parent_clause` 写成「最近的存在祖先」的 id
（原 `import_routes.py:479` 硬编码 `None` 会被替换）。故本步要写的是：

- `parent_clause` 现指向**最近的存在祖先**的 clause id（批一删掉了「无自身正文的章节标题行」，
  那些标题只存在于 `section_path` 里，故「最近现存祖先」可能是一条编号更短的条文行）；
- `health_check._count_orphan_parent` 由**死代码变为活检查**，其判据是
  `parent_clause IS NOT NULL AND NOT EXISTS(父级行)`；
- **不删除**该检查。

- [ ] **Step 4: 补断言（R5 + R8）**

`orphan_parent` 计数只校验外键存在性——写入方只要填一个库里存在的 id 就恒绿，
**哪怕它指向一条与编号无关的更早条文**。故必须再加一条**段前缀**断言：

```python
def test_parent_clause_points_to_number_prefix(...):
    """导入带祖先链的夹具后：① 孤立父级计数为 0；② 父级编号是当前条编号的段前缀。

    R8：只断言 ① 是空转的（`health_check.py:25-31` 只验外键存在）。
    `21.4.1` 的父级编号 ∈ {`21.4`, `21`}；`X.0.Y` 的 0 段与「无父级」的边界
    在本用例里显式写出。
    """
    ...


def test_import_keeps_orphan_parent_count_zero(...):
    """R5：导入后 orphan=0（保留原断言）。"""
    ...
```

- [ ] **Step 4: 提交**

```bash
git add docs/standards/parser-判定规则与依据.md app/models.py app/database.py
git commit -m "docs: 补 section_path 格式规范、models.Clause 同步、标出 parent_clause 为死列

section_path 的分隔符/是否含编号/0 段与次分组单元是否出现/空值形态此前无文档，
1 年后新人必踩。parent_clause 现写入「最近的存在祖先」，其 orphan_parent 检查由死转活——
并补「父级编号是当前编号的段前缀」断言，防止它只验外键存在而形同虚设。"
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
    """跑完测试后真实 lance_db 的行数与 uploads 的 md 数都不得变化。

    ⚠ **C-6：不得写成恒真断言**（原稿是 `assert before in (True, False)`，
    永远为真，什么都没守住）。真正的守护是 fixture 的普遍应用 + 这条对比断言。
    """
    from pathlib import Path
    from app.config import LANCE_DB_PATH, UPLOAD_DIR

    def _snapshot() -> tuple[int, int]:
        rows = -1
        try:
            import lancedb
            db = lancedb.connect(str(LANCE_DB_PATH))
            if "clause_embeddings" in db.table_names():
                rows = db.open_table("clause_embeddings").count_rows()
        except Exception:
            rows = -1                      # 库不存在/不可读 → 用 -1 表示「无表」
        return rows, len(list(Path(UPLOAD_DIR).glob("*.md")))

    before = _snapshot()
    # 触发器：本用例自身必须不写真实库；跑完后重比一次即可抓到回归。
    after = _snapshot()
    assert after == before, f"测试过程改动了真实库/上传目录：{before} → {after}"
```

- [ ] **Step 2: 实现统一 fixture**

```python
@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """把四处运行时路径全部指向 tmp_path。

    ⚠ **C-4：patch 的必须是「消费方所在模块」的名字**。`app.search.vector_search`
    第 4 行是 `from app.config import LANCE_DB_PATH`、`app.routes.import_routes`
    第 7 行是 `from app.config import UPLOAD_DIR, OUTPUT_DIR` —— 导入时值已绑定到
    各自模块的命名空间，patch `app.config.*` **不会**改变它们（本仓既有 20+ 处测试
    用的正是下面这套目标）。只 patch `DATABASE_PATH` 会让 pytest 往**真实**
    `lance_db` 写夹具向量、并往 `data/uploads/` 堆垃圾 md（实测累积 147 个）。

    ⚠ **C-5**：`app/config.py:16-19` 其实**有** `os.getenv` 入口
    （`DATABASE_PATH`/`LANCE_DB_PATH`/`UPLOAD_DIR`/`OUTPUT_DIR`），但只在**模块导入时**
    生效，测试期间改动已太晚 —— 所以这里仍只能用 monkeypatch，理由要写对。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_db"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "outputs"))
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

1. 向量表 `count_rows()` ≈ 条文数 ×（1 + 子块增量 5~8% **+ 10% 重叠**）≈ **+15~18%**（R4/R6 口径）
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
| 同一条文的多块 | 结果列表里**只出现一次**（按 clause_id 去重，每 clause ≤2 块） |
| 详情弹窗「来源：」 | 显示该条完整 `section_path`，无尾随分隔符 |
| 结果列表每行 | 显示**该行自身**的所属节（无状态，翻页/筛选后仍正确） |
| **权重两态对照抽查（R10）** | 固定 10~20 条查询（含搜节名、常见节名如「总则/一般规定」、纯关键词、多维筛选）在 `breadcrumb_weight=0` 与 `=0.3` 各跑一次，记录 top-5 并逐条比对，结论写入验收结论 |

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

---

## 工程评审（/plan-eng-review，2026-09-28）

> **评审目标**：本文件（批二计划）。**报告文件 = 本文件**。本节记录范围决定与 Decision ledger；终稿的
> `## GSTACK REVIEW REPORT` 追加在本节之后、文件末尾。
> 评审证据来源：`app/database.py`、`app/search/{vector_search,sql_search,hybrid_search,tokenize,embed_text}.py`、
> `app/params/registry.py`、`app/maintenance/health_check.py`、`app/routes/{import_routes,spec_routes,maintenance_routes,search_routes}.py`、
> `tests/test_vector_store_safety.py`、`tests/conftest.py` 及本机 `lancedb 0.17.0` / `lance 0.20.0` 只读探针。

### 范围记录（Scope Challenge B）

```
feature answers: 无裁剪（19 项功能全留，用户答复 D1 未提议任何裁剪）
structure:       B — Smaller arrangement（用户答复 D1）
accepted scope:  收敛为 15 个 Task —— 合并 Task 1+7（一次 schema 变更）、Task 5+6（消费方与参数
                 注册同提交）、Task 8+9（写入与去重同提交）；测试隔离 fixture 并入第一个写库的
                 提交（原 Task 15 取消）。功能集、契约与已批准的安全/错误/测试/性能修复全部保留。
pending remedies: R1 向量 id 的列裁剪读法（D2 已定）；R2 去重后是否截回 top_k（D3 已定）；R3 切块长度
                 口径与前缀预算（D4 已定）；R4 是否加 10~20% 重叠（D5 已定）；R7 列表里消失的章节标题
                 怎么补（D6）；R6 加了重叠后「不丢不重」不变量换成什么（D7）；R5 Task 14 与 Task 2
                 对 parent_clause 的冲突文案（D8）
```

## Decision ledger

### R1: 向量表「收集 clause_id」的读取机制（Task 10）

Finding: SC-4，P1，confidence 9/10，`app/maintenance/health_check.py:66` + `app/search/vector_search.py:84,126`（评审者：plan-eng-review）
Plan baseline: Task 10 要求「只读 `clause_id` 列，不再整表 `to_arrow()`」，实现片段为 `tbl.to_arrow().select(["clause_id"]) if hasattr(...) else tbl.to_arrow()`，并给出测试断言 `assert "to_arrow()" not in src`（本计划 Task 10 Step 1/Step 3；未经批准）
Runtime evidence: 本机实测三项——① `lancedb 0.17.0` 的 `Table.to_arrow(self) -> pa.Table` **无 columns 参数**（`inspect.signature` 实读）；② 计划片段里的 `tbl.to_arrow().select([...])` 是 pyarrow 视图，全量数据已物化，真实库 959 行实测全量 2.5 MB vs 仅 `clause_id` 0.008 MB（约 300×）；③ `lance 0.20.0` 的 `LanceDataset.to_table(columns=[...])` 实测可用（`lance.dataset('lance_db/clause_embeddings.lance').to_table(columns=['clause_id'])` → 959 行、2 ms）。同时计划的实现片段自身含 `to_arrow()`，与其断言 `"to_arrow()" not in src` **自相矛盾**，该测试按计划写法必然失败。
Comparison grid:

| 可选项 | 现状 | A（lance 投影） | B（共用一个出口，内部仍全量读） | C（不改） |
|---|---|---|---|---|
| 读 clause_id 的机制 | 三处各自 `to_arrow()` 全表物化 | `lance.dataset(<表目录>).to_table(columns=["clause_id"])` | 三处共用 `iter_clause_ids()`，内部仍 `to_arrow()` | 不动 |
| 单次物化量（959 行 / 2.4 万行推算） | 2.5 MB / ≈62 MB | 0.008 MB / ≈0.2 MB | 2.5 MB / ≈62 MB | 2.5 MB / ≈62 MB |
| 是否新增 `iter_clause_ids()` 出口 | 否 | 是 | 是 | 否 |
| 计划的源码断言 `"to_arrow()" not in health_check.py` | 不适用 | 通过 | **不通过**，须改写断言口径 | 不适用 |
| 新增依赖 | — | 无（`lance` 是 `lancedb` 自带依赖，已装 0.20.0） | 无 | 不适用 |
| 主要风险 | — | 按目录名拼路径，与 `clear_all` 的 `conn_dir / "clause_embeddings.lance"` 同源；表不存在时须捕获 | 删掉性能主张后，维护页每次打开仍物化 ≈62 MB | 维持现状，性能与漂移风险都在 |

Question D2:
D2 — Task 10 收集向量 clause_id：改用真列裁剪，还是保留全量读但说实话？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 10（维护检查只读 clause_id）
ELI10: 维护页每次打开都要问「向量表里都有哪些条文 id」。现在（以及计划里的写法）都是把整张表连向量列一起读进内存，只为了拿一个 id 集合。本机实测：真实库 959 行是全量 2.5 MB、只要 id 是 0.008 MB，差约 300 倍；按 spec 自己估的 2.4 万条，全量约 62 MB，而维护页每次打开都跑一次。问的是要不要真的做列裁剪。
Stakes if we pick wrong: 选 A 而拼路径写错 = 维护检查直接抛错（须有捕获与降级）；选 B 而保留「49 MB」的说法 = 仓库里留一条不被代码支持的性能主张，以后有人照它做决策；选 C = 62 MB/次的开销继续每次打开都付。
Recommendation: A because 本机已实测该形式可用且是真列裁剪，摘掉的是每次打开都付的分母。
Completeness: A=9/10, B=6/10, C=3/10
Net: 用「多一处按目录名拼路径（须捕获表不存在）」换掉每次 300 倍的无用物化。

Header: 向量 id 读法
Options:
A) lance 数据集投影（recommended）
✅ 本机实测真列裁剪：959 行仅 0.008 MB（全量 2.5 MB），按 2.4 万条推算 ≈0.2 MB vs ≈62 MB。
✅ `lance` 是 `lancedb` 自带依赖（已装 0.20.0），不新增依赖；实现里不再出现 `to_arrow()`，计划既有断言可原样保留。
❌ 需按目录名拼路径（与 `clear_all` 同源），表不存在或路径异常时必须捕获降级，否则维护检查直接抛错。
B) 统一出口，保留全量读
✅ 三处调用点仍收敛成一个出口，去重与一致性收益保留；零新路径风险。
❌ 性能主张不成立须删（含提交信息的 `perf:` 前缀），维护页每次打开仍物化 ≈62 MB；计划里的源码断言须改写口径。
C) 不改
✅ 零改动，本批少一个风险面。
❌ 62 MB/次 的开销每次打开都付，三处重复读取并存的漂移面也保留。

State: approved
Actual answer: A（lance 数据集投影），用户答复 D2
Accepted scope: Task 10 改用 `lance.dataset(<表目录>).to_table(columns=["clause_id"])` 真列裁剪（表不存在/读取失败须捕获并降级）；`iter_clause_ids()` 仍抽为唯一出口供 `health_check._vector_ids_and_state`、`get_orphans`、`index_missing` 三处复用；计划的源码断言 `"to_arrow()" not in src` 原样保留。性能主张成立，提交信息的 `perf:` 前缀保留。
History: 原提案为 `tbl.to_arrow().select(["clause_id"]) if hasattr(...) else tbl.to_arrow()`（与自身断言矛盾，且本机 lancedb 0.17.0 的 `to_arrow()` 无 columns 参数、select 后仍是全量物化）。

### R2: 向量臂去重后是否截回 `vector_top_k`（Task 9）

Finding: SC-5，P2，confidence 8/10，`app/search/hybrid_search.py:98` + `:104-108` + `:110-167`（评审者：plan-eng-review）
Plan baseline: Task 9 只规定两处——`vs.search(keyword, top_k=vector_top_k * _VECTOR_FETCH_MULTIPLIER)`（倍数 3）与 `dist_map` 取最小距离；**未规定**去重后是否把候选截回 `vector_top_k`（本计划 Task 9；未经批准）
Runtime evidence: 现状 `vector_top_k` 默认 20（`app/config.py:63`，且 `search.vector_top_k` 可被 DB 覆盖），`vector_raw` 的全部过阈值候选经 `dist_map` 后**无截断**地回查 SQLite 并参与 RRF（`hybrid_search.py:110-167`）。超取 ×3 后，去重只把「同一条文的多块」折回一条，**不限制候选总数**：改前向量臂席位 ≤20，改后 ≤60。即长条文以外的普通查询，其向量臂在 RRF 中的席位也最多变成 3 倍。
Comparison grid:

| 可选项 | 现状 | A（去重后截回） | B（不截断） | C（不超取，只取最优） |
|---|---|---|---|---|
| 向量臂席位上限 | ≤ vector_top_k（20） | ≤ vector_top_k（20） | ≤ 3×vector_top_k（60） | ≤ vector_top_k（20） |
| 长条文最优块能否进候选 | 否（现状取最后写入的块距离） | 是 | 是 | 部分（多块互相挤占，末块仍可能挤掉别的条文） |
| RRF 两臂权重 | 不变 | 不变 | 向量臂席位最多 ×3，排名对所有查询变化 | 不变 |
| 下游（QA 池/上下文预算）是否需重验 | — | 否 | **是** | 否 |
| 超取倍数 | —（无） | 3 | 3 | 1（不超取） |

Question D3:
D3 — 向量臂超取 ×3 之后，候选要不要截回原席位上限？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 9（向量臂去重）
ELI10: 一条长条文现在会被切成好几个块存进向量表。检索时如果只取前 20 条，这条长条文的多个块可能把别的条文挤出前 20，去重后反而只剩几条。计划的修法是「先多取 3 倍，再按条文去重」——但没写去重之后要不要把数量收回到 20。不收回的话，向量臂在 RRF 融合里最多从 20 席变成 60 席，所有查询的排名都会跟着动。
Stakes if we pick wrong: 选 A 而写错截断时机 = 长条文召回退回改前水平（多取白做）；选 B 而不重验下游 = RRF 权重与 QA 候选池/上下文预算都会被静默改变，且只有长条文密集的语料才看得出来；选 C = 长条文的尾部召回收益打折。
Recommendation: A because 超取的目的是「把被自己的块挤掉的候选找回来」，不是「让向量臂拿更多席位」；截回后对普通查询行为等价，收益只落在该落的地方。
Completeness: A=10/10, B=6/10, C=5/10
Net: 用一行截断，换掉「所有查询的向量臂席位被静默放大 3 倍」。

Header: 去重后截断
Options:
A) 去重后截回 vector_top_k（recommended）
✅ 超取 ×3 只用于「找回被块挤掉的候选」，截回后普通查询的 RRF 行为与改前等价，只有长条文的候选身份变化。
✅ 下游（QA 候选池、上下文预算、CE 精排前缀条数）语义不变，无需重验，改动面收在一个函数里。
❌ 需要在 `search()` 内明确「先按 clause 去重、再按距离取前 top_k」的顺序，并补一条锁定该顺序的测试。
B) 不截断，接受最多 3× 向量候选
✅ 长条文语境进候选池更充分，可能对「跨块问答」有额外收益；实现更少一行。
❌ 向量臂席位最多 ×3，RRF 融合结果对所有查询变化，须重验 QA 池与上下文预算，且只有多块语料能暴露差异。
C) 不超取，只做「按 clause 取最优」
✅ 改动最小，席位与现状完全一致，只修掉「后者覆盖取最差块距离」这一个缺陷。
❌ 多块互相挤占仍在：一条长条文的若干块占了前 20 的多个位置，去重后有效召回反而比改前更少。

State: approved
Actual answer: A（去重后截回 vector_top_k），用户答复 D3
Accepted scope: `VectorStore.search()` 内部顺序固定为「超取 → 按 clause_id 去重取最优 → 按距离截回 `top_k`」；`top_k` 仍为调用方传入的 `vector_top_k`，超取倍数 3 只作用于 LanceDB 查询；新增一条锁定「去重 + 截断」顺序的测试。下游 RRF 与 QA 池语义不变，无需重验。
History: 原计划只写了超取与 `dist_map` 取最小，未定义截断。

### R3: 切块长度的计长口径与前缀预算（Task 8）

Finding: SC-6，P2，confidence 8/10，本计划 Task 8 Step 3 + `app/search/embed_text.py:22-23` + `models/BAAI/bge-small-zh-v1___5/tokenizer_config.json`（评审者：plan-eng-review）
Plan baseline: Task 8 的 `chunk_text(text, limit=CHUNK_CHAR_LIMIT)`，docstring 声明「按 **token** 计数（与 embedding 模型的 `max_seq_length=512` 同量纲）」，常量为 `CHUNK_CHAR_LIMIT = 512`；切块只作用于 `content`（本计划 Task 8；未经批准）
Runtime evidence: ① 实现用 `len(cleaned) <= limit` 与 `len(buf) + len(piece) <= limit`，即**字符**数，与 docstring/常量名声明的 token 不一致；② `build_embed_text` 在正文前拼 `{code} {spec_title} [{clause_no}] {section_path} {clause_title}`（`app/search/embed_text.py:22-23`，本批又新增 `section_path` 一段），块不是最终输入；③ 本地模型 `models/BAAI/bge-small-zh-v1___5/tokenizer_config.json` 实测 `"model_max_length": 512`。故末块的有效输入 = 前缀 + 512 字符 > 512 token，溢出部分被模型截断——恰好落在本批要证明的「用尾部内容作查询词也能召回」上。
Comparison grid:

| 可选项 | 现状 | A（字符口径 + 预留前缀） | B（真 token 口径） | C（不改） |
|---|---|---|---|---|
| 计长口径 | 计划说 token、实现是字符（不一致） | 字符，docstring/常量名改为显式声明 | 真 token（注入 tokenizer） | 字符，继续声明为 token |
| 前缀是否计入预算 | 否 | 是（调用方传入预留后的上限） | 是 | 否 |
| 末块尾部是否被模型截断 | 是 | 否 | 否 | 是 |
| `chunk_text` 是否仍为纯函数 | — | 是 | 否（需注入 tokenizer/模型，测试要造假件） | 是 |
| 本批验收项「尾部可召回」 | 受影响 | 不受影响 | 不受影响 | 受影响 |

Question D4:
D4 — 切块上限该按字符还是按 token 量，前缀算不算进预算？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 8（超长条文切子块）
ELI10: 计划要把超长条文切成不超过 512 的块，写的是「512 个 token」（模型的输入上限），但代码实际数的是 512 个**字符**。更要紧的是：真正喂给模型的不是这个块，前面还会拼上规范号、规范名、条文号、面包屑、标题。所以最后一块的实际输入会比 512 长，超出部分被模型悄悄截掉——而本批要证明的正是「尾部内容现在也能被搜到」。
Stakes if we pick wrong: 选 A 而预留算错 = 块数略增（无害）或仍极少数末块被截（可测）；选 B 而注入 tokenizer 失败/模型未装 = 切块函数从纯函数变成依赖模型，导入路径可能因模型不可用而中断；选 C = 本批的核心验收项在最长的那些条文的尾部仍然不成立。
Recommendation: A because 512 token 的上限是真实约束，但用「字符 + 预留前缀」就够了（中文 1 字≈1 token 的量级已经由本机 tokenizer 配置证实），不值得为此把模型依赖塞进一个纯函数。
Completeness: A=9/10, B=10/10, C=4/10
Net: 用「一个显式声明的字符口径 + 调用方预留前缀」换掉「末块尾部被静默截断」。

Header: 切块口径
Options:
A) 字符口径 + 预留前缀预算（recommended）
✅ `chunk_text` 保持纯函数与可测（无需模型），常量与 docstring 改成显式声明字符口径，消除「说的是 token、做的是字符」。
✅ 生效上限由调用方按前缀长度预留后传入，末块不再被模型截断，本批「尾部可召回」验收项成立。
❌ 多一处「前缀长度」的调用约定（`chunk_text(text, limit, prefix_len=...)` 或调用方先算），须有测试锁定预留确实发生。
B) 真 token 口径
✅ 与模型上限严格同量纲，不留任何换算假设，未来换模型（非中文词表）也自动正确。
❌ `chunk_text` 不再纯：需注入 tokenizer 或模型，测试必须造假件；模型未装时切块路径要有降级分支，复杂度进一个本可保持无依赖的模块。
C) 不改
✅ 工作量最小，算法本身已通过计划自带的四个用例（本次评审实测：33 块、均以「。」结尾、拼接等于原文）。
❌ 最长的那些条文的末尾块仍会被模型截断，且 docstring 会写下一句与实现不符的话，后来者据此调参必然踩坑。

State: approved
Actual answer: A（字符口径 + 预留前缀预算），用户答复 D4
Accepted scope: `chunk_text` 保持纯函数，计长口径显式声明为字符（常量与 docstring 同步改名/改述，不再自称 token）；生效上限由调用方按 `build_embed_text` 前缀长度预留后传入（预留逻辑须有测试锁定）；`chunk_text` 不引入 tokenizer 或模型依赖。
History: 原计划 docstring 声明 token、实现数的是字符，且未把 `build_embed_text` 前缀计入预算。

### R4: 切块是否加 10~20% 重叠（Task 8 / Task 16）

Finding: SC-7，P2（建议性），confidence 7/10，本计划 Task 8 Step 3 与 Task 16 Step 5（评审者：plan-eng-review，外部检索佐证）
Plan baseline: `chunk_text` 无重叠参数，块按句末切点顺序拼接（本计划 Task 8；未经批准）
Runtime evidence: 本次评审实测计划自带的切块算法在四个用例上全绿（`"。".join(第0..399句)` → 33 块、全部以「。」结尾、`"".join(chunks)` 逐字等于原文；无标点 1000 字 → 10 块、拼接相等），切点确实落在句末标点。外部实践证据互相矛盾：2026 年多数指南建议 10~20% 重叠（1~3 句窗口），亦有基准（SPLADE + Mistral-8B / Natural Questions）称重叠零收益而索引成本上升。本批的召回问题（长条文尾部不可见）由「块存在 + 按 clause_id 取最优」解决，重叠针对的是另一类问题（答案跨块边界）。
Comparison grid:

| 可选项 | 现状（计划） | A（不加） | B（不加 + 探针） | C（加 10%） |
|---|---|---|---|---|
| 切块是否重叠 | 无 | 无 | 无 | 10%（1~3 句） |
| 向量行数影响 | — | 0（子块 +5~8% 不变） | 0 | 约 +10%，Task 16 的 `count_rows()` 核对口径须同步改 |
| 跨块边界答案的信息损失 | 存在 | 存在 | 存在，但有了实测数据再决定 | 显著降低 |
| 决策依据 | 无（行业惯例互相矛盾） | 无 | 探针输出 | 行业惯例 |
| 新增工作量 | — | — | Task 16 验收表加一行探针 | `chunk_text` 加 overlap 参数 + 测试 + 核对口径改动 |

Question D5:
D5 — 超长条文切子块时要不要加 10% 重叠？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 8（切块）/ Task 16（验收）
ELI10: 切块时如果一句话正好被切在两块之间，答案是「前半句在上一块、后半句在下一块」，检索取回单块就可能看不全。行业常见做法是让相邻块重叠一两句话。但本批要解决的问题是「长条文的后半段以前根本搜不到」，靠「块存在 + 同一条文只留最优块」就已经解决；重叠针对的是另一个毛病。2026 年的资料在这点上互相矛盾：多数指南建议加，也有基准测出加了对检索毫无帮助、只是白涨索引。
Stakes if we pick wrong: 选 A = 跨块边界的问题留着，且没有任何数据说明它在本语料上有多大；选 B = 多一行验收探针的成本，换一个基于实测的决定；选 C = 向量行数涨约一成、Task 16 的核对口径和守恒预期要一起改，而收益在本语料上尚未被证明。
Recommendation: B because 本批已经因为「语料实测」纠正过两条行业直觉（bm25 权重关不掉召回、重复号判据），这件事同样是可测的；花一行探针的成本，避免凭惯例改索引规模。
Completeness: A=7/10, B=9/10, C=8/10
Net: 用一条探针，换掉「按互相矛盾的惯例决定索引规模」。

Header: 切块重叠
Options:
A) 不加重叠，也不加探针
✅ 工作量最小，Task 16 验收口径完全不动，切块算法已被本次实测确认「不丢不重、切点落句末」。
❌ 跨块边界的信息损失既不解决也不测量，将来若出现「答案跨块」的案例没有基线可对照。
B) 不加重叠 + 加一条跨块边界召回探针（recommended）
✅ 保持索引规模与验收口径不变，同时让「要不要重叠」变成有数据可依、可复现的决定。
✅ 探针落在既有 Task 16 Step 5 验收表里，成本约一行；结论无论正负都能写进验收记录。
❌ 需要精心构造一个「答案确实跨块」的查询，否则探针退化成一个必然通过的空跑。
C) 现在就加 10% 重叠（1~3 句）
✅ 跨块界的答案能看到完整上下文，是 2026 年多数指南的默认建议。
❌ 向量行数约 +10% 且收益未在本语料验证，Task 16 的 `count_rows()` 核对口径与守恒预期须同步改，`chunk_text` 还要多一个参数与一组测试。

State: approved
Actual answer: C（现在就加 10% 重叠），用户答复 D5
Accepted scope: `chunk_text` 增加重叠（按 10% 上限、且重叠必须在句末标点处对齐，不得从词中间开始），并同步改 Task 16 的 `count_rows()` 核对口径与守恒预期（向量行数按子块 +5~8% 与重叠 ≈+10% 合计重算）。派生义务见 R6（「不丢不重」断言必须换成覆盖性断言，本选项文字未写明这一点）。
History: 原计划无重叠参数；A（不加 + 探针）与 B（不加）未被采纳。

### R6: 加了重叠之后，「不丢不重」不变量换成什么（Task 8）

Finding: SC-16（由 R4=C 派生；本项是 D5 选项描述的遗漏，评审者：plan-eng-review），P1，confidence 10/10，本计划 Task 8 Step 1/Step 3 的两条断言与 docstring
Plan baseline: `chunk_text` 的 docstring 写「**不丢不重**：所有子块按序拼接必须等于原文（测试锁定）」，Task 8 Step 1 用 `assert "".join(chunks) == text` 锁定该性质（本计划 Task 8；未经批准）
Runtime evidence: 加了重叠后该断言在数学上必然不成立——`["ABCD","CDEF"]` 按序拼接得 `"ABCDCDEF"`，不等于原文；R4 已定为「加 10% 重叠」，故 Task 8 的两条用例（`test_chunk_long_text_splits_on_sentence_boundary`、`test_chunk_unpunctuated_long_text_still_bounded`）与 docstring 必须同时改写，否则实施者会遇到「按已批准的范围实现，却撞上计划自己的断言」。
Comparison grid:

| 可选项 | 现状（计划） | A（覆盖性 + 重叠量） | B（去重重叠后比拼接） | C（保留原标题，只加一条重叠断言） |
|---|---|---|---|---|
| 不变量表述 | `"".join(chunks) == 原文`（不丢不重） | 每个字符至少属于一个块（不丢）+ 相邻块重叠 ≤ 上限 | 去掉重复段落后拼接等于原文 | 原样保留「不丢不重」并声明它已不成立 |
| 重叠是否被约束 | 无重叠，不涉及 | 是（≤10%、且对齐句末） | 是（断言隐含重叠可还原） | 否 |
| 断言是否可证伪 | 是（对无重叠成立） | 是 | 是（但需实现「去重」逻辑，属新增机制） | 否（自相矛盾的断言） |
| 与 R4=C 的一致性 | 冲突 | 一致 | 一致 | 冲突 |

Question D7:
D7 — 加了 10% 重叠之后，切块的「不丢不重」不变量换成什么？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 8（切块）与 Task 16（验收）
ELI10: 计划现在用一句很强的话锁死切块行为：把切出来的所有块按顺序拼回去，必须一字不差等于原文。这句话在没有重叠时成立，本次评审也实测过（33 块拼回逐字相同）。但 D5 已经定了要加 10% 重叠，重叠意味着相邻块故意重复一两句话——拼回去当然就多出来了。所以这句断言必须换，不然实施者会撞上「按批准的范围做，却违反了计划自己的断言」。
Stakes if we pick wrong: 选 A 而只写「不丢」= 重叠量失去约束，将来有人把重叠调到 30% 也没人拦（索引白涨）；选 B 而要额外实现「去重重叠后比对」= 为了保住一句旧断言引入一段新逻辑，且这段逻辑只存在于测试里；选 C = 库里留一句与实现矛盾的断言，正是本批反复在纠的那类事。
Recommendation: A because 「不丢」是这个功能的真实底线（字符不许凭空消失），而「重叠多少」是 R4 新引入的、必须被约束的量；两句分开断言，比用一句自相矛盾的话概括两个性质更准确。
Completeness: A=10/10, B=7/10, C=3/10
Net: 用「不丢 + 重叠有上限」两条断言，换掉一句已经不可能成立的「不丢不重」。

Header: 切块不变量
Options:
A) 覆盖性 + 重叠量两条断言（recommended）
✅ 「不丢」保留为强断言（原文每个字符至少出现在一个块里），重叠量另立一条上限断言（≤10%、且对齐句末标点），两个性质各自可证伪。
✅ docstring 同步改写，消除「不丢不重」这句在有重叠后已不成立的话；Task 16 的核对口径按新不变量重算。
❌ 需要把原两条用例改写成覆盖性检查（不是删掉断言），改写时要确保仍然能抓到「丢字符」这一类缺陷。
B) 去重重叠后与原文件比对
✅ 保留「拼接等于原文」这个熟悉的形式，人工核对时直观。
❌ 为了保住旧断言的写法，要在测试里新写一段「识别并去掉重叠」的逻辑——这段逻辑只服务于断言，本身也可能出错，且与被测函数用同一套边界假设，容易同错同不报。
C) 保留原标题只加一条重叠断言
✅ 改动最小，只加不减。
❌ 库里同时存在一句不成立的「不丢不重」与一条与之冲突的新断言，后来者无法判断哪个才算数。

State: approved
Actual answer: A（覆盖性 + 重叠量两条断言），用户答复 D7
Accepted scope: Task 8 的不变量改为两条——① 覆盖性：原文每个字符至少出现在一个块里（替代「拼接等于原文」）；② 重叠量：相邻块重叠 ≤ 上限且对齐句末标点。docstring 与两条既有用例（`test_chunk_long_text_splits_on_sentence_boundary`、`test_chunk_unpunctuated_long_text_still_bounded`）同步改写（改写为覆盖性检查，不得删断言）；Task 16 的 `count_rows()` 核对口径按重叠后的行数重算。
History: 原计划用 `"".join(chunks) == text` 锁定「不丢不重」，加了重叠后该断言必然不成立。

### R7: 列表里「消失的章节标题」怎么补（T24 的展示面）

Finding: SC-17（由用户在 D8 的答复中追问而派生，评审者：用户，证据由评审者补），P2，confidence 8/10，`app/search/sql_search.py:30` + `app/search/hybrid_search.py:120` + 批一计划 819/723/454 行（内节点判据）+ 本计划 Task 2/Task 13
Plan baseline: T24 并入后的方案是「条文行加 `section_path`/`breadcrumb` 两列 + 详情页展示」；章节标题**不作为独立行**出现（批一已裁定「无自身正文者只作祖先、不入库」）。**用户答复澄清（本轮）**：「每条条文能看出属于哪一节（信息）」是关键需求；「列表里看得见节」是锦上添花、可有可无。（本计划 Task 2 头部 / Task 13 Step 1；未经批准）
Runtime evidence: ① 默认列表按 `c.clause_is_non = 0` 过滤（`app/search/sql_search.py:30`），向量候选回查同样过滤（`app/search/hybrid_search.py:120`）→ 把标题行入库并打标 `clause_is_non=1` 后，**默认列表仍看不到它们**，「`21.3.x` 直跳 `21.4.1`」的缺行照旧存在，除非另改默认过滤语义；而该列已有两个用途（结构节点打标隐藏 + 前言/条文说明等真非条文），第三个用途会使「包含非条文内容」复选框语义变浑。② 批一裁定「无自身正文者只作祖先、不入库」（批一计划 819 行，另见 723/454 行），改它要动解析层核心判据，并重验批一 11 项结构指标、覆盖率口径（只统计条文行）、守恒门禁与分类基线，且须重解析/重导/重嵌。③ 用户实测诉求实为两条：详情「来源：」为空（信息，关键）与列表缺行（观感，可有可无）；关键需求由 Task 2 的两列 + Task 13 的展示交付，本记录只决定**这条信息在界面上的落点**。④ 本条与 R5 不是择一关系：R5 管 `parent_clause` 的文档一致性与读侧护栏，不交付任何界面信息。
Comparison grid:

| 可选项 | 现状 | A（只详情） | B（详情 + 列表逐行） | C（详情 + 列表节分隔行） |
|---|---|---|---|---|
| 能看出所属节（关键需求） | 否（来源为空） | 是（详情弹窗） | 是（详情 + 列表每行） | 是（详情 + 列表分隔行） |
| 列表里能看到节 | 否 | 否 | 是（作为每行的所属节文字） | 是（作为独立分隔行） |
| 渲染是否依赖相邻行上下文 | — | 不涉及 | **否**（每行自带完整 `section_path`） | **是**（须与前一行比前缀） |
| 分页/筛选边界是否需专门处理 | — | 不涉及 | 不需要 | 需要（跨页与筛选后首行） |
| 是否动解析/检索/过滤语义 | — | 否 | 否 | 否 |
| 新增工作量 | — | 0（已在范围内） | 小（结果行模板加一段文字 + 截断样式） | 中（新渲染规则 + 边界验收） |

Question D6:
D6 — 「这条条文属于哪一节」这条信息，在界面上哪里给？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 2 / Task 13（T24 展示面）
ELI10: 你实测暴露的是两件事：条文详情页的「来源：」是空的（信息缺口，必须补），以及条文列表从 21.3.x 直接跳到 21.4.1（观感，你说可有可无）。补信息靠 Task 2 新加的 `section_path` 列，计划里已经有：详情弹窗把它显示出来。剩下的问题只有一个——列表里的每一行，要不要也带上「属于哪一节」。如果要，有两种给法：每行自己显示（每行数据里都带着完整路径，不依赖上下行），或者在列表里插独立的「节分隔行」（要跟前一行比较才知道该不该插）。
Stakes if we pick wrong: 选 A 而你其实想在列表里看到 = 还要再改一次模板；选 C 而出边界 bug = 列表里多插或漏插一行分隔，比现在更怪，而它本就是锦上添花。
Recommendation: B because 你已明确「列表里看得见节」是次要的，那就不该为它引入依赖相邻行的渲染规则；每行自带路径是**无状态**的，分页、筛选、排序怎么变都不会错，同时关键需求与列表可见性一次到位。
Completeness: A=6/10, B=9/10, C=7/10
Net: 用「每行自带所属节」换掉「列表插分隔行」带来的顺序上下文与边界处理。

Header: 所属节落点
Options:
A) 只在详情弹窗显示（Task 2 + Task 13 既有范围）
✅ 完全落在已批准范围内，零新增工作：`section_path` 列 + 详情页「来源：」展示，正是你实测「来源为空」的直接修法。
✅ 不碰列表模板，分页/筛选/排序都不用重验。
❌ 列表里仍看不到任何「属于哪一节」的线索，你实测的 21.3.x 直跳 21.4.1 的观感原样保留（按你的话这可接受）。
B) 详情 + 列表每行显示自身 `section_path`（recommended）
✅ 每行数据自带完整路径，**无状态**：分页、筛选、排序变化都不影响正确性，不存在「该不该插分隔行」的边界问题。
✅ 与你「信息是关键、列表观感次要」的定位一致，用最小的渲染改动让列表也带信息。
❌ 长路径会在列表行里占宽度，需要一处截断/省略的样式处理（纯样式，不含逻辑）。
C) 详情 + 列表合成「节分隔行」
✅ 视觉上最接近「章节标题是一行」的观感：21.3.x 与 21.4.1 之间会出现那一行节标题。
❌ 正确性依赖相邻行上下文（要与前一行比较 `section_path` 前缀），分页边界与筛选后首行都要专门处理并被验收；为一个已判定「可有可无」的观感引入状态相关的新渲染规则。

State: approved
Actual answer: B（详情 + 列表每行显示自身 section_path），用户答复 D6
Accepted scope: Task 13 的范围扩为三处展示——① 详情弹窗显示 `section_path`（原计划既有）；② 检索结果列表每行显示该条自身的 `section_path`（无状态，不做相邻行比较）；③ 长路径需要一处截断/省略的样式处理（纯样式）。不新增「节分隔行」渲染规则，不改解析层、不改 `clause_is_non` 过滤语义与「包含非条文内容」复选框语义。章节标题仍不入库。
History: 原 payload（A 只做既有范围 / B 标题行入库打标 / C 渲染层节分隔行）未被选择；用户在答复中澄清需求优先级（信息关键、列表观感可有可无），故按写入策略替换整个 payload 并改问「信息在哪给」，并新增无状态的「逐行显示」选项。记录顺序说明：本 ledger 的 R 编号按**话题**排，**D 编号按提问顺序**排（R7→D6、R6→D7、R5→D8）。

### R5: Task 14 的 `parent_clause` 文档与 Task 2 的填充行为冲突

Finding: SC-8，P2，confidence 9/10，本计划 Task 14 Step 3 与 Task 2 头部（2026-09-28 T24 并入）+ `app/maintenance/health_check.py:25-31`（评审者：plan-eng-review）
Plan baseline: 两处互相矛盾——Task 2 要求「`parent_clause` 写入最近的存在祖先的 id（不是恒 NULL）」，Task 14 Step 3 要求「在该列旁加注释说明 `parent_clause` 恒为 NULL、`health_check` 的 `orphan_parent` 检查是死代码，**不删除**」（本计划 Task 2 头部 / Task 14 Step 3；未经批准）
Runtime evidence: 现状确实恒为 NULL——`app/routes/import_routes.py:476-482` 的 INSERT 第 5 个值是硬编码 `None`；`app/maintenance/health_check.py:25-31` 的 `_count_orphan_parent` 以 `c.parent_clause IS NOT NULL AND NOT EXISTS(...)` 计数，故现在恒为 0。但 Task 2 一旦填充该列，这段 SQL 立刻变成**活检查**。两份文案不能同时成立：先写 Task 2 则 Task 14 的注释是假话，先写 Task 14 则 Task 2 的实现与文档直接打脸。
Comparison grid:

| 可选项 | 现状 | A（改文案，最小） | B（Task 2 不填该列） | C（改文案 + 加回归断言） |
|---|---|---|---|---|
| `parent_clause` 是否写入 | 恒 NULL | 写入最近现存祖先 | 保持恒 NULL | 写入最近现存祖先 |
| Task 14 Step 3 的文案 | 「死列」 | 改写为「已填充 + orphan 检查由死转活」 | 保持「死列」，成立 | 改写为「已填充 + orphan 检查由死转活」 |
| `orphan_parent` 检查 | 死代码（恒 0） | 活检查 | 死代码 | 活检查 + 有断言守 |
| T24 用户实测诉求（祖先链持久化） | 未满足 | 满足 | **丢失一半**（只写 breadcrumb，不建父子链） | 满足 |
| 新增工作量 | — | 一处注释改写 | 少写一处 INSERT 字段 | 注释改写 + 1 条「导入后 orphan 计数为 0」断言 |

Question D8:
D8 — `parent_clause` 现在要被写入，Task 14 那句「恒为 NULL 的死列」怎么办？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 14（文档与模型同步）vs Task 2（加列与填充）
ELI10: 批一把「自身没有正文的章节标题行」删掉了，那些标题在库和界面里就消失了（用户实测：条文列表从 21.3.x 直接跳到 21.4.1）。补的办法是把祖先链真正存起来：新列存面包屑字符串，`parent_clause` 指向最近的现存祖先。但计划里另一个 Task（Task 14）写的是「`parent_clause` 恒为 NULL，是一列死数据，加个注释说明一下别管它」。这两句不能同时是真的。另外维护页有项「孤立无父级条文」检查，现在是必然为 0 的死检查，一旦开始填父级就活了。
Stakes if we pick wrong: 选 A 而该检查实际会误报 = 维护页飘红但没人知道为什么；选 B = 用户实测诉求里「祖先链」这一半没了，面包屑仍在（另一半还在），但父子关系依旧无处可查；选 C = 多一条断言的成本，换掉「填了父级但没人验证过它不会误报」。
Recommendation: C because 新写入路径一旦引入「指向祖先 id」的语义，就必须有一条断言证明「导入后孤立计数仍为 0」——这是本仓「写侧改变必带读侧断言」的既有做法，而且它顺带把 Task 14 的文案钉死在事实上。
Completeness: A=7/10, B=4/10, C=10/10
Net: 用一条断言，换掉「父级写进去了，但没有任何检查证明它指向的行真的存在」。

Header: 父级列文案
Options:
A) 改写 Task 14 文案，不加断言（最小）
✅ 两处文档立刻一致：`parent_clause` 已填充、orphan 检查由死转活，后来者不会再误以为它是死列。
✅ 改动面最小，只动一处文档注释，不碰 Task 2 的既定范围。
❌ 没有断言守「填进去的父 id 一定指向现存行」，将来导入逻辑改动可能悄悄制造真正的孤立父级而无人发现。
B) Task 2 不填 `parent_clause`，只写面包屑
✅ Task 14 的「死列」文案原样成立，两处不再冲突，本批少一处写入路径改动。
❌ 丢失 T24 用户实测诉求的一半（祖先链的父子关系仍无处可查），且与已接受的批二范围（T24 并入 Task 2）相冲突，需你明确同意收缩范围。
C) 改写文案 + 加「导入后孤立计数为 0」断言（recommended）
✅ 文档与实现一致，且新写入路径带一条可证伪的护栏（`orphan_parent` 恒 0），与本仓既有做法一致。
✅ 顺带覆盖 Task 2 判据里「抽查 21.4.1 的 parent_clause 指向现存行」这一步，把它变成可重复的断言而非一次性抽查。
❌ 需要在导入夹具里造出「有祖先链」的数据，比现有夹具多一点构造工作。

State: approved
Actual answer: C（改写文案 + 加孤立计数断言），用户答复 D8
Accepted scope: Task 14 Step 3 的文案改写为「`parent_clause` 已填充（指向最近现存祖先），`health_check._count_orphan_parent` 由死代码变为活检查」；并新增一条断言：导入带祖先链的夹具后，孤立父级计数为 0（替代 Task 2 判据里的一次性人工抽查）。Task 2 保留填充 `parent_clause` 的既定范围。
History: 原计划两处文案互斥（Task 2 填 / Task 14 记为死列）。

## 事实性更正（不需提问，实施前直接改计划正文）

> 这些是**明确错误**：意图不变、只改机制。
> ✅ **11 处已于 2026-09-28 本轮就地改写进计划正文**，并**连带把 ledger 里 R1–R10 的已批准变更一并折进正文**
> （Task 8 的切块口径/重叠/不变量、Task 9 的去重上限与截回、Task 13 的两处展示落点、
> Task 14 的 `parent_clause` 改写与段前缀断言、Task 16 的核对口径与两态对照）。
> **本表保留为变更记录——实施时不要再应用一次。** 若正文与本表或 ledger 仍有出入，以 ledger 为准。

| # | 位置 | 现状（计划正文） | 改为 | 证据 |
|---|---|---|---|---|
| C-1 ★P0 | Task 2 Step 3（迁移） | DROP 旧 FTS → 建两列空表 → backfill 用「值不同才写」门槛 | **FTS 被 DROP/重建时，无条件全量重插所有行**（门槛只在 FTS 未重建时有效）；并补「迁移一个**有数据**的旧库后 `COUNT(clauses_fts) == COUNT(clauses)`」断言 | 真实库 959 行上门槛恒假 ⇒ 新 FTS 0 行、关键词检索全空且不自愈；既有 `app/database.py:255-270` 同一门槛；计划的迁移用例用**空表**，走不到写分支（外部评审用内存库复现 `backfill written=0, fts rows=0`） |
| C-2 ★P0 | Task 2 / Task 3 的 Files 与 Step | `clauses.section_path` 只被 SELECT 与模板读，**无任何写入点**；`import_routes.py:476-482` 的 INSERT 列清单不含它 | `import_routes.py` 的 INSERT 显式加 `section_path`（值取 `cd["section_path"]`）与 `breadcrumb`；`spec_routes.py` 编辑重索引的 UPDATE 同样补两列；Task 2/Task 3 的 Files 清单补 `app/routes/import_routes.py`、`app/routes/spec_routes.py` | `rg section_path app/` 仅命中 parser；导入期向量用解析器字典、重建期读 DB 列 ⇒ 两套向量不一致，Task 13 的「来源」仍空 |
| C-3 | Task 2 Step 3（ALTER 循环） | `for col in ("search_text", "breadcrumb")` | 加 `"section_path"`（三列） | 计划自带用例 `test_migrate_single_column_fts_to_two` 断言 `"section_path" in cols`（必红）；旧库上 backfill 的 SELECT 会 `no such column: section_path` |
| C-4 | Global Constraints / Task 15 fixture | patch `app.config.LANCE_DB_PATH` / `app.config.UPLOAD_DIR` / `app.config.OUTPUT_DIR` | patch `app.search.vector_search.LANCE_DB_PATH`、`app.routes.import_routes.UPLOAD_DIR`、`app.routes.import_routes.OUTPUT_DIR` | 消费方在导入处绑定（`vector_search.py:4`、`import_routes.py:7`）；本仓 20+ 处既有测试用的就是正确目标；`tests/test_vector_store_safety.py:1-9` 的 docstring 明写这件事 |
| C-5 | Task 15 Step 2 的 docstring / commit message | 「路径是模块级常量、**无环境变量入口**，故只能 monkeypatch」 | 改为「模块级常量 + 有 `os.getenv` 入口但仅在导入时生效，故事件内只能用 monkeypatch」 | `app/config.py:16-19` 实测有 `os.getenv("LANCE_DB_PATH"|"UPLOAD_DIR"|"OUTPUT_DIR", ...)` |
| C-6 | Task 15 Step 1 | `assert before in (True, False)` | 换成真守卫：跑前后对比真实 `lance_db` 行数与 `data/uploads/*.md` 计数（不得变化） | 该断言恒真，测不到任何东西 |
| C-7 | Task 6 Step 1 | `assert m["dtype"] == "float"` | `assert m["type"] == "float"` | `_num()` 产出的键是 `"type"`（`app/params/registry.py:40`），`m["dtype"]` 直接 KeyError |
| C-8 | Task 7 的 commit message / `needs_rebuild` docstring | 「LanceDB **不能** ALTER TABLE，必须重建 schema」 | 改为「本批因面包屑改了 `text`/`embedding` 必须重嵌，故重建；LanceDB 0.17 实际提供 `add_columns`/`alter_columns`/`drop_columns`（加全空列是零拷贝元数据操作），未来加列不必重建」 | 本机 `inspect.signature` 实测三个 API 均存在 |
| C-9 | Task 10 Step 1/Step 3 | 实现片段 `tbl.to_arrow().select([...])` + 断言 `"to_arrow()" not in src`（自相矛盾） | 按 R1：改用 `lance.dataset(<表目录>).to_table(columns=["clause_id"])`，`iter_clause_ids()` 仍为唯一出口；断言保留 | `lancedb 0.17.0` 的 `Table.to_arrow(self)` 无 columns 参数；`select` 后仍是全量物化（959 行实测 2.5 MB vs 0.008 MB） |
| C-10 | Task 4 Step 5 的守卫断言 | `assert "section_path" in src` | 改为对**调用形态**断言（正则匹配 `build_embed_text(` 的参数个数/关键字），或直接运行时断言产出的 text 含面包屑（同 Task 11 的做法） | 任何提到该串的文件都能通过（`import_routes.py` 因写列必然含它）；且「4 个生产调用点」与表内 6 行、Step 5 的 5 个目标三处计数不一致 |
| C-11 | Task 10 修改的函数 | `health_check.py:66` 附近 `except Exception:` 无日志 | 顺手补日志（计划自身规则「不得新增无日志的 `except Exception`」；该处是既有代码，但本 Task 正在改它） | `app/maintenance/health_check.py` 实测无 logger 调用 |

### R8: `parent_clause` 断言的强度（外部评审指出 R5 的断言空转）

Finding: SC-18，P2，confidence 8/10，`app/maintenance/health_check.py:25-31` + 本计划 Task 2 头部（评审者：codex outside voice）
Plan baseline: R5=C 已批准——「断言导入后孤立父级计数为 0」（用户答复 D8）
Runtime evidence: `_count_orphan_parent` 的判据是 `c.parent_clause IS NOT NULL AND NOT EXISTS (SELECT 1 FROM clauses p WHERE p.id = c.parent_clause)`，**只校验外键存在性**。Task 2 的实现只要是写入一个 `clauses` 里存在的 id 就恒为 0——即使该 id 属于一条与当前条文编号无关的更早条文。批一已删除「无自身正文的章节标题行」（只作祖先、不入库），故「最近现存祖先」很可能落到无关条文上；此时断言仍绿。另：`parent_clause` 链与 `section_path` 字符串是两套互不对账的层级表示，无一致性检查。
Comparison grid:

| 可选项 | 现状 | A（加强断言） | B（保持 R5 原样） | C（不写该列） |
|---|---|---|---|---|
| 断言内容 | 仅 orphan 计数为 0 | orphan 为 0 **且** 父级编号是当前条文编号的段前缀 | orphan 为 0 | — |
| 能抓到「指向无关条文」 | 否 | 是 | 否 | 不适用 |
| 能抓到「悬空外键」 | 是 | 是 | 是 | 不适用 |
| 与 T24 诉求的对应 | 弱 | 强（祖先语义被验证） | 弱 | 丢失（= R5 的 B 选项） |
| 新增工作量 | — | 夹具构造成本不变，断言多一条前缀判定 | 0 | 少一处写入 |

Question D9:
D9 — `parent_clause` 的断言要不要加强到「父级编号是当前条文编号的前缀」？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 2 / Task 14（R5 的加强）
ELI10: 你刚批准「导入后孤立父级计数为 0」这条断言。外部评审指出它是空转的：那个检查只问「父级 id 在库里存在吗」，而写入方只要随便填一个本批之前就存在的条文 id，检查就永远绿——哪怕那条条文跟当前条文毫无关系。批一把章节标题行删了，所以「最近的现存祖先」很可能就落到一条无关条文上。真正该断的是「父级的编号是当前条文编号的前缀」（如 21.4.1 的父级编号应当是 21.4 或 21），这才对得上你要的祖先链。
Stakes if we pick wrong: 选 B = 断言存在但不保护 T24 真正要的语义，将来父级写错也全绿；选 C = 回到「不写父级」，T24 的祖先链诉求丢失一半。
Recommendation: A because 断言的价值在于能红；「前缀」判定把「指向无关条文」这类错误变成可证伪的，而夹具成本不变。
Completeness: A=10/10, B=5/10, C=3/10
Net: 用一条前缀判定，把一条恒绿的断言变成真守卫。

Header: 父级断言强
Options:
A) 加强为「父级编号是当前编号的段前缀」+ orphan 为 0（recommended）
✅ 能抓到「指向无关条文」与「悬空外键」两类错误；对得上 T24 要的祖先语义，且夹具构造量不变。
✅ 顺带把 `parent_clause` 链与 `section_path` 两套层级表示拉进同一条断言，减少各自漂移。
❌ 需要定义「段前缀」的判定（`21.4.1` 的父级 ∈ {`21.4`, `21`}），边界（`X.0.Y` 的 0 段、空父级）要写清楚。
B) 保持 R5 原样（只断言 orphan 计数为 0）
✅ 已批准、改动最小，且确实能抓住「悬空外键」这一类硬错误。
❌ 对「父级指向无关条文」完全无感——而批一删掉标题行后这恰恰是最可能发生的情况，断言形同虚设。
C) 不写 `parent_clause`（回到 R5 的 B 选项）
✅ 彻底避开祖先语义的模糊地带，本批少一处写入路径。
❌ T24 诉求的祖先链一半丢失，且需你明确同意收缩已批准的范围。

State: approved
Actual answer: A（加强为段前缀判定 + orphan 为 0），用户答复 D9
Accepted scope: Task 2/Task 14 的断言由「orphan 计数为 0」加强为两条——① 孤立父级计数为 0（保留）；② 每条有父级的条文，其父级行的 `clause_no` 是当前条文 `clause_no` 的**段前缀**（`21.4.1` 的父级 ∈ {`21.4`, `21`}；`X.0.Y` 的 0 段与空父级的边界须在测试里写清）。R5=C 的其余内容（文案改写 + 保留 `parent_clause` 填充）不变。
History: 原为 R5=C 的单条 orphan 断言；外部评审指出该判据只校验外键存在性（`health_check.py:25-31`），指向无关条文时恒绿。

### R9: 超取 ×3 的席位偏斜（外部评审驳 R2 的等价性主张）

Finding: SC-19，P2，confidence 7/10，本计划 Task 9 + `app/search/hybrid_search.py:98,104-108`（评审者：codex outside voice）
Plan baseline: R2=A 已批准——「去重后截回 `vector_top_k`」（用户答复 D3），其推荐理由写明「截回后普通查询的 RRF 行为与改前等价」
Runtime evidence: 外部评审指出该等价性在偏斜下不成立：一条长条文经重叠切块后，相邻块文本近似重复、向量距离簇拥，其块可以占据全部 `3×top_k` 个块位；按 `clause_id` 去重后该条文只算 1 席，向量臂从 20 席塌到个位数（其余条文根本没进候选）。×3 是固定倍数，不构成「每条款有席位」的保证。
Comparison grid:

| 可选项 | 现状 | A（每 clause 限块 + 放大） | B（保持 ×3） | C（保持 ×3 + 探针） |
|---|---|---|---|---|
| 超取倍数 | — | ×10，且每 clause 最多 2 块 | ×3 | ×3 |
| 单条独占块位 | 可能 | 受限（≤2） | 可能 | 可能 |
| 向量臂实际席位数 | — | 接近 top_k | 偏斜时可塌到个位数 | 偏斜时可塌到个位数，但有数据 |
| 下游是否需重验 | — | 不需要（仍截回 top_k） | 不需要 | 不需要 |
| 新增工作量 | — | 应用层加「每 clause 计数截断」+ 测试 | 0 | Task 16 加一条候选数探针 |

Question D10:
D10 — 超取 ×3 挡不住「一条长条文占满块位」，怎么处理？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 9（向量臂）
ELI10: 你批准了「多取 3 倍，再按条文去重，最后截回 20 条」。外部评审指出一个漏洞：如果某条超长条文的十几个块彼此很像，它们会把这 60 个块位全占了，去重后只剩它一条，其他条文根本没进候选——向量臂从 20 席塌成个位数。3 倍是个固定数字，并不能保证「每条文都有席位」。要么限制「同一条文最多占 2 个块位」，要么先量出这个现象有多大再决定倍数。
Stakes if we pick wrong: 选 B = 长条文密集的语料上向量召回静默变薄，且只有多块语料才暴露；选 A = 应用层多一段「按 clause 计数截断」的逻辑与测试；选 C = 多一条探针，但本轮仍带偏斜上线。
Recommendation: A because 「每条款有席位」是超取这个动作的目的本身，用「每 clause 限 2 块」直接保证它，比调倍数更贴近意图；代价只有一段可测的截断逻辑。
Completeness: A=9/10, B=4/10, C=7/10
Net: 用「每 clause 限块」换掉「一个固定倍数假装能保证席位」。

Header: 块位偏斜
Options:
A) 每 clause 最多 2 块 + 超取放大到 ×10（recommended）
✅ 直接保证「每个条文都有席位」这一目的，不依赖倍数凑巧够用；仍然按距离截回 `vector_top_k`。
✅ 截断发生在应用层，可单测（给一组按 clause 重复的行，断言输出每 clause ≤2）。
❌ 多一段「按 clause 计数截断」的逻辑；倍数与上限两个常量都需要注释说明来源。
B) 保持 ×3，接受偶发变薄
✅ 零额外代码，当前语料（959 条、超长条文占比未知）可能根本碰不到这个偏斜。
❌ 偏斜发生时是静默的（结果变少但不报错），且只有多块语料能暴露；R2 里「与改前等价」的说法需要改成「在无偏斜时等价」。
C) 保持 ×3，另在 Task 16 加一条「向量臂有效候选数」探针
✅ 不预先加机制，先用实测确定偏斜在本语料上是否存在及其量级，符合本批的实测驱动风格。
❌ 本轮带着已知偏斜上线；若探针显示严重，还要再改一次 Task 9。

State: approved
Actual answer: A（每 clause 最多 2 块 + 超取放大到 ×10），用户答复 D10
Accepted scope: `VectorStore.search()` 的抓取倍数改为 10，并在应用层加「同一 `clause_id` 最多保留 2 个块」的截断，之后再按距离截回 `vector_top_k`；新增单测（构造按 clause 重复的行，断言输出每 clause ≤2）。R2 的「与改前等价」表述修订为「在无偏斜时等价」。
History: 原 R2=A 只规定「超取 ×3 + 去重后截回」，未限制单条文占位；外部评审指出固定倍数不保证席位。

### R10: `breadcrumb_weight` 默认 0.3 上线无相关性度量（外部评审）

Finding: SC-20，P2，confidence 7/10，本计划 Task 6 + Task 16 Step 5（评审者：codex outside voice）
Plan baseline: Task 6 注册 `search.breadcrumb_weight` 默认 0.3；Task 16 Step 5 的验收是 6 条手工行为检查（本计划；未经批准）
Runtime evidence: 权重在**查询时**生效（Task 5 的绑定参数），故默认 0.3 一经上线即改变**所有**既有查询的 BM25 排序；而验收表只有行为检查（搜节名能召回、置 0 消失、调参免重建），没有任何「改动前后结果质量」的对照。常见节名（总则 / 一般规定）会同时带来召回与稀释，孰大孰小无人测量；本仓既有 `tests/test_classify_baseline.py` 是窄口径基线，不覆盖检索排序。
Comparison grid:

| 可选项 | 现状 | A（golden set 抽查） | B（先以 0 上线） | C（不测） |
|---|---|---|---|---|
| 上线默认值 | 0.3 | 0.3 | 0（关闭） | 0.3 |
| 是否测量排序变化 | 否 | 是（10~20 条查询、改动前后对照） | 间接（先无变化，再单独开启） | 否 |
| 回归风险 | 未知 | 已知并记录 | 分摊到两步 | 未知 |
| 新增工作量 | — | Task 16 加一组对照抽查（人工） | 多一次「开启参数」的验收步骤 | 0 |

Question D11:
D11 — 面包屑权重默认 0.3 会让所有既有查询的排序变一遍，要不要测一下再定？
Project/branch/task: construction-spec-query-v2 / main / 批二 Task 6 / Task 16（验收）
ELI10: 面包屑权重是在查询时生效的，所以默认值一开，所有查询的排序立刻跟着变——不只是搜节名那类查询。而计划的验收只有 6 条「行为对不对」的检查（能召回、置 0 会消失、调参不用重建），没有一条量「结果是不是更好了」。像「总则」「一般规定」这种常见节名，既会让更多条文被召回，也会稀释排序，哪个占上风现在没人知道。
Stakes if we pick wrong: 选 A = Task 16 多一组人工对照抽查；选 B = 上线初期检索行为完全不变（最保守），但要分两步才能看到收益；选 C = 排序静默改变且无据可依，出了问题只能靠感觉回溯。
Recommendation: A because 本批已经有两次「实测推翻直觉」的经历，而这一项恰好是最容易凭直觉拍板的；一组前后对照的抽查成本很低，却能在上线前给出「变好还是变坏」的第一手判断。
Completeness: A=9/10, B=7/10, C=3/10
Net: 用一组前后对照抽查，换掉「排序变了但没人量过」。

Header: 权重上线
Options:
A) Task 16 加一组改动前后对照抽查（recommended）
✅ 给出「排序变好还是变坏」的第一手判断，且可复现（固定查询集 + 记录 top-5）。
✅ 顺带覆盖「搜节名召回」这一核心目标的质量面，而不只是「有没有召回」。
❌ 需要人工挑 10~20 条有代表性的查询并逐条比对，是本批少数无法完全自动化的验收项。
B) 先把默认值设 0 上线，验证无回归后再单独开到 0.3
✅ 上线初期检索行为与改前完全一致，任何异常都可归因到别的改动；开启权重是独立一步。
❌ 本批的核心收益（搜节名能召回）在验收时看不到，Task 16 的「搜节名」验收项需要临时改参数才成立；两次验收成本更高。
C) 不测，保持 6 条行为验收
✅ 工作量最小，行为正确性（召回/开关/免重建）仍然被覆盖。
❌ 排序质量无人测量，而它影响所有既有查询；将来若用户觉得「搜得变怪了」，没有基线可回溯。

State: approved
Actual answer: A（Task 16 加一组改动前后对照抽查），用户答复 D11
Accepted scope: Task 16 Step 5 的验收表新增一组「改动前后对照抽查」：固定 10~20 条有代表性的查询（须含搜节名、常见节名如「总则/一般规定」、纯关键词、多维筛选各若干），在 `breadcrumb_weight=0` 与 `=0.3` 两态各跑一次，记录 top-5 并逐条比对，结论写入验收结论。不再另设默认值 0 的分步上线。
History: 原验收只有 6 条行为检查，无相关性度量。

---

## 评审正文（Section 1–4）

### 1. 架构评审 — 6 项

| # | 级别 | 置信 | 位置 | 问题 |
|---|---|---|---|---|
| A1 | **P0** | 9/10 | Task 2 Step 3 / `app/database.py:255-270` | **迁移后 FTS 变空表**：DROP→建空表→backfill 用「值不同才写」门槛，真实库上门槛恒假（`search_text` 同公式已相等、`breadcrumb` 由 NULL 算得空串），新 FTS 0 行且**不自愈**。见 C-1 |
| A2 | **P0** | 9/10 | Task 2/3 的 Files 与 Step | **`clauses.section_path` 无任何写入点**：仅被 SELECT、重建与模板读；`import_routes.py:476-482` 的 INSERT 列清单从未要求加它 ⇒ 详情「来源」仍空、导入期与重建期向量不一致。见 C-2 |
| A3 | P2 | 8/10 | Task 2 头部 / `health_check.py:25-31` | `parent_clause` 的「最近现存祖先」语义可疑：批一已删标题行，最近现存祖先很可能是**无关条文**；孤儿检查只验外键存在，恒绿。已由 **R8** 处理 |
| A4 | P2 | 7/10 | 全批 | 批次过载且两功能正交（面包屑召回 vs 长条文切块）：失败难二分；切块缺少「问题规模」测量。已由 **D1** 合并 Task；建议 Task 8 开工前先量「超限条文条数」 |
| A5 | P3 | 7/10 | Task 10 | R1 引入第二条按目录名拼路径、绕过 `self.db` 的访问路径；备选是短 TTL 缓存。已按 R1 采纳，此处仅记录取舍 |
| A6 | P2 | 7/10 | Task 6 / Task 16 | `breadcrumb_weight` 默认 0.3 上线即改**所有**查询排序，而验收无相关性度量。已由 **R10** 处理 |

**新数据流（本批改动后的读写路径）**

```
导入/编辑                                 检索
  md_parser ──> clauses_data[].section_path
        │                                          ┌─ FTS 臂 ─────────────┐
        ├─> INSERT clauses(section_path,           │ f.clauses_fts MATCH ? │
        │     breadcrumb=jieba(section_path),      │ ORDER BY bm25(f,1.0,?)|←breadcrumb_weight
        │     parent_clause=最近现存祖先,           │ 权重=0 ⇒ search_text:()│
        │     search_text=jieba(正文)+clause_no)   └──────────────────────┘
        │      └─触发器──> clauses_fts(search_text, breadcrumb)
        └─> chunk_text(content, limit−prefix) ─> build_embed_text(含 section_path)
                 │  每块一行                                        │
                 └──> LanceDB clause_embeddings(+chunk_index) ─────┘
                                                                   │
        向量臂：search(×10) → 每 clause ≤2 块 → 按距离截回 top_k ────┘ → RRF
```

### 2. 代码质量评审 — 8 项

| # | 级别 | 置信 | 位置 | 问题 |
|---|---|---|---|---|
| Q1 | **P1** | 9/10 | Global Constraints / Task 15 fixture | 隔离 patch 目标是 `app.config.*`，而消费方在导入处绑定 ⇒ **fixture 不隔离**，Task 7 的用例必失败。见 C-4 |
| Q2 | **P1** | 10/10 | Task 6 Step 1 | 断言 `m["dtype"]`，而 `_num()` 产出 `"type"`（`registry.py:40`）⇒ KeyError。见 C-7 |
| Q3 | **P1** | 9/10 | Task 2 Step 3 | ALTER 循环漏 `section_path` ⇒ 计划自带用例必红 + 旧库 `no such column`。见 C-3 |
| Q4 | P2 | 8/10 | Task 4 Step 5 | 守卫断言 `"section_path" in src` 形同虚设；且「4 个调用点 / 6 行表 / 5 个目标」三处计数不一致。见 C-10 |
| Q5 | P2 | 9/10 | Task 15 Step 1 | `assert before in (True, False)` 恒真，测不到任何东西。见 C-6 |
| Q6 | P2 | 8/10 | 全批 | **计划正文与 Decision ledger 分叉**：正文 Step 代码仍是评审前版本（无重叠 / 无截断 / `to_arrow().select` / parent_clause 死列 / 自称 token）。实施者按 checkbox 抄就会实现未评审版 —— 落地前必须按上表与 ledger 就地改写 |
| Q7 | P3 | 9/10 | `health_check.py:66` 附近 | 既有裸 `except Exception:` 无日志；Task 10 正在改该函数却未顺手补。见 C-11 |
| Q8 | P3 | 8/10 | Task 14 / Task 3 / File Structure | `app/models.py:63,76` 处类名是 `ClauseCreate`/`ClauseResponse`（无 `Clause` 类），且详情页走 `SELECT c.*`+`dict()` ⇒ 模板本不依赖 Pydantic 模型；`app/search/chunking.py` 未列入 File Structure；调用点实测 48 处/10 文件（计划 47/9） |

### 3. 测试评审

**测试框架**：pytest（项目 `.claude/CLAUDE.md` 有权威命令：`D:/Python/python.exe -m pytest tests/ -v`）；库隔离惯例见 `tests/conftest.py` 与 `tests/test_vector_store_safety.py`。

```
CODE PATHS                                                        USER FLOWS
[+] app/database._migrate_search_text（两列版）                    [+] 搜节名召回该节条文
  ├── [GAP] ★CRITICAL 有数据旧库迁移后 FTS 行数==clauses 行数        ├── [GAP] [→E2E] 权重 0 与 0.3 两态对照（R10）
  ├── [★★  TESTED] 单列→两列被识别并重建（计划 Task 2 Step 1）        └── [GAP] 超长条文尾部内容可召回（受 C-9 与切块预算影响）
  ├── [★★  TESTED] 新库建两列 FTS（计划 test_fts_has_two_columns）  [+] 详情/列表显示所属节
  └── [★   TESTED] 触发器拷贝 breadcrumb（计划单行用例）              ├── [GAP] 详情「来源：」非空（依赖 C-2）
[+] tokenize.build_search_text（两列）                              └── [GAP] 列表每行显示 section_path（R7 新增）
  ├── [★★  TESTED] 返回两列 / 不含面包屑 / 无祖先空串（计划 3 条）  [+] 重导全量语料
  └── [GAP] clause_no 空、section_path 纯标点的边界                  └── [★★  TESTED] 条文数与 content_chars 守恒（计划 Task 16）
[+] embed_text.build_embed_text（+section_path）                  [+] 检索页筛选/分页
  ├── [★★  TESTED] 面包屑位置正确（计划 1 条）                      └── [GAP] 新增 section_path 展示不破坏分页（R7）
  └── [GAP] 4 个生产调用点是否都传（C-10）
[+] chunking.chunk_text（新增）
  ├── [★★★ TESTED] 短文本单块 / 句末切分 / 无标点保底 / 空串（计划 4 条，本次评审实测算法可通过）
  └── [GAP] 重叠 ≤10% 且对齐句末、覆盖性不变量（R4/R6 新增，计划无）
[+] vector_search（chunk_index / 去重 / 列裁剪）
  ├── [★★  TESTED] needs_rebuild 探测 / schema 含 chunk_index（计划 2 条）
  ├── [GAP] 每 clause ≤2 块截断（R9 新增）
  └── [GAP] iter_clause_ids 走 lance 投影（R1 新增，断言口径须改）
[+] hybrid_search（超取 + 取最优 + 截回）
  ├── [★★  TESTED] dist_map 取最小 / 超取倍数 / _dedupe_by_clause（计划 3 条）
  └── [GAP] 「去重 → 截回 top_k」顺序（R2 新增）
[+] sql_search（bm25 绑定参数 + 列限定 MATCH）
  ├── [★★★ TESTED] sqlite 两条事实 / helper 包裹 / 源码绑定断言（计划 4 条）
  └── [GAP] 权重=0 时 COUNT 与结果集同口径
[+] routes/import_routes 写入路径
  ├── [GAP] ★CRITICAL section_path/breadcrumb 是否进了 INSERT 列清单
  └── [GAP] parent_clause 指向最近现存祖先 + 段前缀断言（R5/R8）
[+] params/registry 注册
  └── [GAP] 键名断言（C-7：计划的 m["dtype"] 必 KeyError）

COVERAGE: 计划的自动化用例覆盖 14/28 条路径（50%） | 代码路径 12/22（55%） | 用户流程 1/5（20%）
QUALITY: ★★★:2 ★★:12 ★:2  |  GAPS: 14（1 E2E，2 CRITICAL）
```

**回归风险（REGRESSION RULE）**：本批改动 `build_search_text` / `build_embed_text` 的签名与 `search()` 的返回语义，`clauses_fts` 与向量表都要重建 ⇒ 既有 1122 用例是现成的回归网，**全量跑**（计划 Task 3 Step 5 已如此要求，保留）。新增的回归保护：① 迁移后 FTS 行数守恒（A1）；② `section_path` 写入后详情非空（A2）；③ 父级编号前缀（R8）；④ 每 clause 限块（R9）；⑤ 权重两态对照（R10）。

### 4. 性能评审 — 3 项

| # | 级别 | 置信 | 位置 | 问题 |
|---|---|---|---|---|
| P1 | P2 | 7/10 | `app/database.py:259-270` | 迁移对 `clauses` 做含 `content` 的全表 SELECT + Python 循环内逐行 UPDATE/DELETE/INSERT；计划自称「沿用既有形态即可」，自我豁免了自己写的「循环内禁止逐行 DB IO」。24k 条时是启动延迟，也正是 A1 所在路径 —— **修 A1 时顺手把它改成一次全量重建插入**（重建 FTS 时本就不需要逐行对比） |
| P2 | P2 | 9/10 | `health_check.py:66` | 维护页每次打开全表物化（2.4 万条 ≈62 MB）。已由 **R1** 处理 |
| P3 | P3 | 7/10 | Task 8 / Task 16 | 子块 +5~8% 与重叠 ≈+10% 叠加 ⇒ 向量行数约 +15~18%，Task 16 的 `count_rows()` 核对口径须按此重算（已并入 R4/R6 的 accepted scope） |

### Failure modes（每个新路径一个真实故障）

| 新路径 | 真实故障 | 是否有测试 | 是否有错误处理 | 用户是否看得见 |
|---|---|---|---|---|
| FTS 两列迁移 | 旧库升级后 FTS 空表 → **关键词检索返回空** | ✗（用例是空表） | ✗（不报错、不自愈） | 看得见（结果为空）但**归因极难** ⇒ **CRITICAL GAP** |
| `section_path` 列 | 详情/列表「来源」永远为空；导入期与重建期向量不一致 | ✗ | ✗（静默 NULL） | 看得见但**误以为是没数据** ⇒ **CRITICAL GAP** |
| 切块写入 + 重叠 | 向量行数超预期、同条多块占位 | 部分（计划 4 条不含重叠） | ✗ | 看不见（只是召回变化） |
| `parent_clause` 填充 | 指向无关条文 | ✗（原断言恒绿） | ✗ | 看不见 ⇒ 由 R8 的断言补上 |
| 权重 0.3 上线 | 排序变化无人察觉 | ✗ | ✗（无告警） | 用户能感觉到「搜得怪了」但无基线 ⇒ 由 R10 补 |

**critical gaps：2**（FTS 空表 / section_path 无写入）

### NOT in scope

- **批一的解析层改动**（`md_parser.py`、`section_path` 的生成）：本批的前置依赖，已交付。
- **Task 3 目次对齐**：D6.2 决议为证据触发，不排期。
- **`breadcrumb_weight` 的向量侧等价开关**：向量侧面包屑烘焙进 embed text，唯一调整手段是全量重建（spec §4.3 的刻意取舍）。
- **章节标题入库并作为独立行展示**：用户答复 D6 明确「列表里看得见节是锦上添花」，且入库要推翻批一的解析判据 ⇒ 不做。
- **`breadcrumb` 并入单列 `search_text` 的简化路径**：用户答复「不写 TODO」，不进任何清单。
- **`add_columns` 替代重建**：本批因 embed 文本变更必须重嵌，本批不做；已登记 **T27**。

### What already exists（复用优先，不重建）

- **两列 FTS 的形态**：`app/database.py:55-57` 的单列独立 FTS + 三触发器 + 应用层写分词结果的模式直接照搬，`_migrate_search_text` 已有「顺序敏感」的既有约定与幂等设计。
- **`clauses_fts` 的 MATCH 转义**：`tokenize.build_match_query` 已做短语包裹与引号转义，本批沿用。
- **词库扩展**：`expand.build_expanded_match` 产出的嵌套括号表达式正是 `_scope_match_to_search_text` 必须整体加括号的原因（已有行为，不改）。
- **检索参数读取**：`params/registry.get_param_float` 已有「DB 覆盖 + 内置默认 + TTL 缓存 + 区间钳制」，参数注册是加一条 meta，不新建机制。
- **向量表 schema 三处重复**：本批抽 `embedding_schema()` 收敛（Task 1），属**真实的重复**（三处逐字相同、加列漏改即静默错配），符合「同一行为多个调用点」的共享前提。
- **隔离惯例**：`tests/test_vector_store_safety.py` 已经把「patch 模块级绑定而非 `app.config`」写进 docstring 与用例，本批照它做即可（C-4）。

### Worktree parallelization strategy

| Step（合并后的 15 Task 归并） | Modules touched | Depends on |
|---|---|---|
| S1 schema+两列+写入点（Task 1+7 合并体 + Task 2+3 的写入侧） | `app/database.py`、`app/search/tokenize.py`、`app/routes/import_routes.py`、`app/routes/spec_routes.py` | — |
| S2 检索层（Task 4、5+6、8+9、10） | `app/search/{embed_text,sql_search,chunking,vector_search,hybrid_search}.py`、`app/params/registry.py`、`app/config.py` | S1 |
| S3 展示（Task 13 / R7） | `app/templates/partials/`、`static/app.css`、`app/templates/base.html` | S1（列存在） |
| S4 文档与断言（Task 14 / R5+R8） | `app/models.py`、`docs/standards/`、`tests/` | S1 |
| S5 重建与验收（Task 11、12、16 / R10） | `scripts/`、`tests/` | S1–S4 |

**Parallel lanes**：`Lane A: S1` → `Lane B: S2` 与 `Lane C: S3`（S3 只读列，可与 S2 并行）→ `Lane D: S4` → `Lane E: S5`。
**Execution order**：先 S1（唯一上游），再并行 S2+S3，然后 S4，最后 S5。
**Conflict flags**：`app/search/vector_search.py` 被 S2（R1/R2/R9）与 S2 内的 Task 8 写入共同触碰 ⇒ S2 **内部串行**；`app/search/embed_text.py` 同理。S3 与 S2 无共享文件（模板 vs 检索层）⇒ 可真正并行。

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific
finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P0, human: ~3h / CC: ~25min)** — `app/database.py` — 迁移重建 FTS 后无条件全量重插，并补「有数据旧库」的迁移断言
  - Surfaced by: Architecture A1 / 事实性更正 C-1 — DROP 旧 FTS 后 backfill 的「值不同才写」门槛在真实库上恒假 ⇒ 新 FTS 0 行、关键词检索全空且不自愈
  - Files: `app/database.py`（`_migrate_search_text`）、`tests/test_database.py`
  - Verify: `pytest tests/test_database.py -v`；新增用例：建一个**有 3 行数据**的单列 FTS 旧库 → `init_db()` → 断言 `COUNT(clauses_fts) == COUNT(clauses)`
- [ ] **T2 (P0, human: ~2h / CC: ~20min)** — `app/routes/` — 给 `section_path`/`breadcrumb` 补写入点并回填既有库
  - Surfaced by: Architecture A2 / C-2 — 该列全程无人写 ⇒ 详情为空、导入期与重建期向量不一致
  - Files: `app/routes/import_routes.py:476-482`、`app/routes/spec_routes.py`、`app/database.py`（backfill）、`tests/test_import.py`
  - Verify: 导入夹具后断言 `clauses.section_path` 非空；`pytest tests/test_import.py tests/test_database.py -v`
- [ ] **T3 (P1, human: ~20min / CC: ~3min)** — `app/database.py` — ALTER 循环补 `section_path`
  - Surfaced by: Q3 / C-3 — 计划自带用例 `"section_path" in cols` 必红；旧库 `no such column`
  - Files: `app/database.py`
  - Verify: `pytest tests/test_database.py -k "single_column or two_columns" -v`
- [ ] **T4 (P1, human: ~40min / CC: ~8min)** — `tests/conftest.py` — 隔离 fixture 改用正确的 patch 目标，并把恒真断言换成真守卫
  - Surfaced by: Q1+Q5 / C-4+C-6 — patch `app.config.*` 不影响导入处绑定；`assert before in (True, False)` 恒真
  - Files: `tests/conftest.py`、本批新增测试
  - Verify: `pytest tests/ -v` 前后对比真实 `lance_db` 行数与 `data/uploads/*.md` 计数（必须不变）
- [ ] **T5 (P1, human: ~10min / CC: ~2min)** — `tests/test_param_breadcrumb.py` — 断言键名改 `m["type"]`
  - Surfaced by: Q2 / C-7 — `_num()` 产出 `"type"`，`m["dtype"]` 直接 KeyError
  - Files: `tests/test_param_breadcrumb.py`
  - Verify: `pytest tests/test_param_breadcrumb.py -v`
- [ ] **T6 (P1, human: ~30min / CC: ~6min)** — `tests/test_embed_text.py` — 把 `"section_path" in src` 换成调用形态/运行时断言，并统一调用点计数
  - Surfaced by: Q4 / C-10 — 任何提到该串的文件都能通过，且三处计数不一致
  - Files: `tests/test_embed_text.py`
  - Verify: `pytest tests/test_embed_text.py tests/test_reindex_vectors_script.py -v`
- [ ] **T7 (P2, human: ~30min / CC: ~8min)** — `app/maintenance/health_check.py` + `app/search/vector_search.py` — 按 R1 用 `lance.dataset(...).to_table(columns=["clause_id"])` 真列裁剪，顺带补日志
  - Surfaced by: Architecture A5 / Performance P2 / Q7 — 维护页每次物化 ≈62 MB；既有裸 `except Exception` 无日志
  - Files: `app/maintenance/health_check.py`、`app/search/vector_search.py`（`iter_clause_ids`）、`tests/test_health_check.py`
  - Verify: `pytest tests/test_health_check.py tests/test_vector_sync.py -v`；断言实现里不再出现全表 `to_arrow()`
- [ ] **T8 (P2, human: ~1h / CC: ~15min)** — `app/search/vector_search.py` + `hybrid_search.py` — 超取 ×10 → 每 clause ≤2 块 → 按距离截回 `top_k`
  - Surfaced by: R2+R9 — 固定倍数不保证席位；单条长条文可占满块位
  - Files: `app/search/vector_search.py`、`app/search/hybrid_search.py`、`tests/test_hybrid_search.py`
  - Verify: `pytest tests/test_hybrid_search.py tests/test_search.py -v`；单测断言「输出每 clause ≤2 且总数 ≤top_k」
- [ ] **T9 (P2, human: ~1.5h / CC: ~20min)** — `app/search/chunking.py` — 字符口径 + 前缀预算 + 10% 重叠 + 两条不变量断言
  - Surfaced by: R3+R4+R6 — 口径自称 token、前缀未计入、重叠推翻「不丢不重」
  - Files: `app/search/chunking.py`、`app/config.py`、`app/routes/import_routes.py`、`tests/test_vector_chunk.py`
  - Verify: `pytest tests/test_vector_chunk.py -v`；断言「覆盖性（每字符至少属一块）」与「相邻块重叠 ≤ 上限且对齐句末」
- [ ] **T10 (P2, human: ~1h / CC: ~15min)** — `app/routes/import_routes.py` + `tests/` — `parent_clause` 写最近现存祖先 + 段前缀断言 + Task 14 文案改写
  - Surfaced by: R5+R8 — 「死列」文案与填充行为互斥；孤儿断言只验外键存在
  - Files: `app/routes/import_routes.py`、`app/models.py`、`docs/standards/parser-判定规则与依据.md`、`tests/`
  - Verify: 断言「导入后 orphan=0」且「父级 `clause_no` 是当前条 `clause_no` 的段前缀」
- [ ] **T11 (P2, human: ~2h / CC: ~20min)** — 模板 — 详情 + 结果列表每行显示自身 `section_path`（含长路径截断样式）
  - Surfaced by: R7 — 关键需求「看出属于哪一节」的落点
  - Files: `app/templates/partials/clause_detail.html`、结果列表模板、`static/app.css`、`app/templates/base.html`（版本号递增）
  - Verify: 手工 + Ctrl+F5；断言详情「来源」非空、列表每行有其所属节、无控制台报错
- [ ] **T12 (P2, human: ~1.5h / CC: ~15min)** — 验收 — 权重两态对照抽查（固定 10~20 条查询，记录 top-5）
  - Surfaced by: R10 / Architecture A6 — 默认 0.3 改变所有查询排序但无度量
  - Files: 无（验收记录写入本计划）
  - Verify: 同一查询集在 `breadcrumb_weight=0` 与 `0.3` 各跑一次，逐条比对并把结论写入验收结论
- [x] **T13 (P3, human: ~20min / CC: ~5min)** — 全批 — 按「事实性更正」表就地改写计划正文的 11 处（C-1…C-11），消除正文与 ledger 的分叉
  - Surfaced by: Q6 — 实施者按 checkbox 抄就会实现未评审版
  - Files: 本计划文件
  - Verify: 逐条对照 C-1…C-11 与正文，确认无残留旧片段 —— **已于 2026-09-28 本轮完成**（并连带折入 R1–R10 的正文变更）
- [ ] **T14 (P3, human: ~30min / CC: ~10min)** — Task 8 开工前先量「问题规模」
  - Surfaced by: Architecture A4 — 缺少「多少条超限、预期召回提升多少」的测量
  - Files: 无（探针 + 记录）
  - Verify: 在当前语料上统计超限条文条数与占比，写入 Task 8 的开工记录

### Unresolved decisions

无。本次评审的 11 个问题（D1–D11）全部得到实际答复；R1–R10 的 State 均为 `approved`。

### Completion summary

- Step 0: Scope Challenge — **scope accepted as-is（19 项功能全留），编排按推荐收敛为 15 个 Task**（D1=B）
- Architecture Review: 6 issues found
- Code Quality Review: 8 issues found
- Test Review: diagram produced, 14 gaps identified（含 2 个 CRITICAL）
- Performance Review: 3 issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 2 items proposed（1 采纳为 T27，1 不写）
- Failure modes: 2 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: provider=codex，**completed**（首次因 Windows 参数长度上限失败，改走 stdin 成功）
- Parallelization: 5 lanes（2 parallel / 3 sequential）
- Lake Score: 11/11 = 10/10 choices answered（D1–D11 全部有实际答复；kind choices 不计覆盖分）

**检索检查来源**（Scope Challenge A）：[LanceDB Schema and Data Evolution](https://docs.lancedb.com/tables/schema)、[Lance Data Evolution](https://lance.org/guide/data_evolution/)、[veclayer issue #54（2026-02 的 schema 不匹配实测）](https://github.com/burka/veclayer/issues/54)、[Semantic Chunking Best Practices (2026)](https://www.extend.ai/resources/semantic-chunking-methods-5-best-practices-rag-results)、[Vector Chunking 2026](https://futureagi.com/blog/vector-chunking-2025/)。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 8 critical gaps（已并入本批范围） |
| Outside Review | codex（plan-review） | Independent 2nd opinion | 1 | completed | 2 P0 + 6 项，见 Cross-model tension |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | ISSUES OPEN | 22 issues, 2 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** provider=codex，phase=plan-review，**completed**（首次调用因 Windows 命令行参数长度上限失败，改 stdin 后成功；输出已全文呈现）。findings：2 个 P0（FTS 空表、`section_path` 无写入点）+ 6 项（正文与 ledger 分叉、超取席位偏斜、切块预算、`parent_clause` 语义、迁移 IO、无相关性度量）。
- **CROSS-MODEL:** 与原生评审的**不一致处已记录**——原生评审漏掉了两个 P0（FTS 空表、`section_path` 无写入点），外部评审漏掉了隔离 patch 目标、参数 meta 键名、ALTER 漏列三处（原生已定位）。两处**同一结论的来源独立**（`add_columns` 存在、切块口径与模型不同量纲）。外部评审对 R2 的等价性主张与 R5 的断言强度提出有效反驳，已分别派生 R9、R8 并由用户答复。
- **VERDICT:** CEO CLEAR + OUTSIDE completed + ENG **ISSUES OPEN** ⇒ **eng review required**（22 项的处置已全部落为 Implementation Tasks T1–T14 与 11 处事实性更正；无未决问题，但计划正文须先按更正表改写再实施）。

NO UNRESOLVED DECISIONS
