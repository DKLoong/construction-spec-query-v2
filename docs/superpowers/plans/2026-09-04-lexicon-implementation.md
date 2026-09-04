# 词库子系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单向 `synonym_map` 升级为词库子系统——synonym/alias/confusable 三类词条，接入 FTS 检索扩展、规则引擎归一化、检索页与 QA 的易混淆提示，并以 /lexicon 三 Tab WebUI 管理；替换并删除旧同义词链路。

**Architecture:** 新增 `app/lexicon/` 纯逻辑包（store 缓存加载 / expand 查询端扩展 / normalize 条文归一化 / confusable 命中检测）。检索扩展只改 FTS 查询端（sql_search 薄包装），向量与 embedding 输入不改一字。旧 `synonym_map` 在数据层先做非破坏迁移（拷贝进 lexicon），待全部消费方切源后再在同一收尾单元删除。

**Tech Stack:** Python 3.14 · FastAPI · SQLite（FTS5）· jieba · HTMX + Alpine（Pico）。

**Spec:** `docs/superpowers/specs/2026-09-04-lexicon-module-design.md`（本计划由 spec 论证，executor 需与 spec 同读；任务隐含 spec 全部约束）。

## Global Constraints

- **TDD**：每步先写失败测试再实现；每个 Task 结束时全量测试绿后再 commit（`git add` 仅本 Task 文件，禁止 `git add -A`）。
- 测试/运行命令：先 `cd /d/CC-Workspace/construction-spec-query-v2`，测试用 `D:/Python/python.exe -m pytest tests/ -v`，单测定位 `D:/Python/python.exe -m pytest tests/<file>::<name> -v`。
- 代码注释用中文。禁止使用 `python3`。
- 安全约束：任何用户输入写入/前端渲染均做合法性与转义；SQL 一律参数化。
- **检索边界（写入代码注释）**：同义词/别名只做 BM25/FTS5 检索扩展，向量 embedding 输入不替换原词；confusable 绝不做检索改写、只做命中告警。
- 词库 schema 幂等键 = `(kind, canonical, variants)`；synonym/alias 组唯一（同 kind 同 canonical 不允许并立）是**应用层**约束（`lexicon/validation.py`），不落库。
- `search.lexicon_expand` 参数默认 `1`，读取点仅 `sql_search`；规则归一化与易混淆检测不受其影响。
- 词库任何写操作后必须走 `store.invalidate_lexicon_caches()`（唯一缓存失效出口）。

## 模块接口（全计划统一基准）

- `app/lexicon/store.py`：常量 `KIND_SYNONYM/KIND_ALIAS/KIND_CONFUSABLE`、`EQUIV_KINDS`；dataclass `LexiconRow(id:int, kind:str, canonical:str, variants:list[str], distinguish:str, is_active:int)`；`load_equivalent_groups()->list[LexiconRow]`；`load_confusable_pairs()->list[LexiconRow]`；`invalidate_lexicon_caches()->None`；模块属性 `equiv_conflict_word: str|None`。
- `app/lexicon/expand.py`：`build_expanded_match(keyword:str, groups:list[LexiconRow], join_with:str="AND")->str`。
- `app/lexicon/normalize.py`：`normalize_text(text:str, groups:list[LexiconRow])->str`。
- `app/lexicon/confusable.py`：`detect_confusable(text:str, pairs:list[LexiconRow])->list[dict]`（dict 键 `a/b/distinguish`）。
- `app/lexicon/validation.py`（T8 引入）：`validate_row(kind, canonical, variants:str, distinguish:str)->tuple[dict|None, str|None]`。
- 测试夹具：复用 `tests/conftest.py` 的 `auth_client`、`client`、`monkeypatch DATABASE_PATH` 模式。

---

### Task 1: 数据层——lexicon 建表 + 非破坏迁移 + 种子

**Files:**
- Modify: `app/database.py`（`SCHEMA_SQL`、`init_db` 新增 `_migrate_legacy_synonyms`）
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: 无。
- Produces: DB 中 `lexicon_entries` 表与 `idx_lexicon_kind_canonical_variants`；`init_db()` 幂等迁移旧 `synonym_map` 4 行至 lexicon（copy，**不 DROP**）；预置种子。Task 5/8 依赖此表存在。

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_database.py` 追加：

```python
def test_lexicon_table_and_seed(monkeypatch, tmp_path):
    """init_db 建 lexicon 表 + 预置 alias 混凝土/砼、confusable 箍筋×钢筋"""
    from app.database import init_db, get_db, DATABASE_PATH
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_db() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "lexicon_entries" in names
        rows = conn.execute(
            "SELECT kind, canonical, variants, distinguish FROM lexicon_entries ORDER BY id"
        ).fetchall()
        d = {(r["kind"], r["canonical"]): dict(r) for r in rows}
        assert d[("alias", "混凝土")]["variants"] == "砼"
        assert d[("confusable", "箍筋")]["variants"] == "钢筋"
        assert "子类" in d[("confusable", "箍筋")]["distinguish"]


def test_migrate_legacy_synonyms_copies_not_drops(monkeypatch, tmp_path):
    """旧库升级：synonym_map 4 行拷贝为 3 条 alias（混凝土合并砼/混泥土、I 收 1）+ 1 confusable；synonym_map 仍存在"""
    from app.database import init_db, get_db, DATABASE_PATH
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "old.db"))
    init_db()
    with get_db() as conn:  # 模拟老库 4 行
        conn.execute("INSERT INTO synonym_map(source,target) VALUES ('混泥土','混凝土'),('1','I')")
    init_db()  # 二次 init 触发迁移（synonym_map 存在 → 拷贝）
    with get_db() as conn:
        lex = {(r["kind"], r["canonical"]): r["variants"]
               for r in conn.execute(
                   "SELECT kind, canonical, variants FROM lexicon_entries")}
        assert lex[("alias", "混凝土")] == "砼,混泥土" or "砼" in lex[("alias", "混凝土")] or "混泥土" in lex[("alias", "混凝土")]
        assert "I" in [k[1] for k in lex if k[0] == "alias"]
        assert ("confusable", "箍筋") in lex
        n = conn.execute("SELECT COUNT(*) c FROM synonym_map").fetchone()["c"]
        assert n == 4  # 未 DROP
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_database.py::test_lexicon_table_and_seed tests/test_database.py::test_migrate_legacy_synonyms_copies_not_drops -v`
Expected: FAIL（无 lexicon_entries 表）

- [ ] **Step 3: 实现** —— 在 `app/database.py`：

`SCHEMA_SQL` 中整段删除 `synonym_map` 的 CREATE 与其 UNIQUE INDEX（**留到 Task 8 才删**——不，这里 schema 必须仍在，否则老表建不出来）不动 synonym_map；在 `qa_request_logs` 建表之后追加：

```sql
CREATE TABLE IF NOT EXISTS lexicon_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    canonical    TEXT NOT NULL,
    variants     TEXT NOT NULL DEFAULT '',
    distinguish  TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lexicon_kind_canonical_variants
    ON lexicon_entries(kind, canonical, variants);
```

在 `init_db()` 中，把文件末尾两行 `INSERT OR IGNORE INTO synonym_map ... (砼,混凝土)/(箍筋,钢筋)` **删除**，替换为以下两段（放在 `conn.executescript(param_profiles)` 之后）：

```python
        # ── 词库子系统 ─────────────────────────────────────────────
        # 预置种子（幂等：全行唯一键 + INSERT OR IGNORE；本库无 synonym_map 时也建演示对）
        conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,note,is_active) "
            "VALUES ('alias','混凝土','砼','预置种子',1)")
        conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,is_active) "
            "VALUES ('confusable','箍筋','钢筋',"
            "'箍筋是钢筋加工成型的构造钢筋（子类），用于约束核心混凝土，不等同于全部钢筋',"
            "'预置种子',1)")
        _migrate_legacy_synonyms(conn)
```

并在 `init_db()` 上方新增模块函数：

```python
def _migrate_legacy_synonyms(conn):
    """幂等迁移：把旧 synonym_map 行拷贝进 lexicon（不 DROP，DLL 删除见收尾单元）。

    分类规则：
      - source='箍筋' and target='钢筋' → confusable(箍筋 × 钢筋)
      - 其余行 → alias(canonical=target, variants=该 target 全部 source 逗号合并)
    仅当 synonym_map 表存在时执行（新库无此表 → 直接跳过）。
    """
    tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='synonym_map'"
    ).fetchone()
    if not tbl:
        return
    rows = conn.execute(
        "SELECT source, target, is_active FROM synonym_map ORDER BY id"
    ).fetchall()
    # 聚合：按 target 分组 source（保留顺序、去重）
    by_target: dict[str, list[str]] = {}
    for r in rows:
        if r["source"] == "箍筋" and r["target"] == "钢筋":
            conn.execute(
                "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,is_active) "
                "VALUES ('confusable','箍筋','钢筋',"
                "'箍筋是钢筋加工成型的构造钢筋（子类），用于约束核心混凝土，不等同于全部钢筋',"
                "'由旧 synonym_map 迁移',?)",
                (r["is_active"],))
            continue
        by_target.setdefault(r["target"], [])
        if r["source"] not in by_target[r["target"]]:
            by_target[r["target"]].append(r["source"])
    for target, sources in by_target.items():
        if not sources:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,note,is_active) "
            "VALUES ('alias',?,?,?,?)",
            (target, ",".join(sources), "由旧 synonym_map 迁移",
             1 if sources else 1))
```

> 迁移仅当旧表存在跑一次（Task 8 DROP 后不再触发）；`INSERT OR IGNORE` 与预置种子同键去重。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_database.py -v`
Expected: PASS（含既有 synonym_map 用例——此 Task 未删表，必须仍绿）

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_database.py
git commit -m "feat: lexicon_entries 建表 + synonym_map 非破坏迁移 + 种子（混凝土/砼、箍筋×钢筋）"
```

---

### Task 2: `app/lexicon/store.py` — 词库加载 + TTL 缓存 + 词表一致性

**Files:**
- Create: `app/lexicon/__init__.py`, `app/lexicon/store.py`
- Test: `tests/test_lexicon_store.py`

**Interfaces:**
- Consumes: DB `lexicon_entries`（Task 1）。
- Produces: `LexiconRow`、`load_equivalent_groups/load_confusable_pairs/invalidate_lexicon_caches`、模块属性 `equiv_conflict_word`。Task 3/4/5/7 与 UI 失效链依赖。

- [ ] **Step 1: 写失败测试**

```python
import pytest
from app.lexicon import store
from app.lexicon.store import LexiconRow, invalidate_lexicon_caches, load_equivalent_groups, load_confusable_pairs

def _mkrows():
    store._cache = [
        LexiconRow(1, "alias", "混凝土", ["砼", "混泥土"], "", 1),
        LexiconRow(2, "synonym", "坍落度", ["塌落度"], "", 1),
        LexiconRow(3, "confusable", "箍筋", ["钢筋"], "子类区分", 1),
        LexiconRow(4, "alias", "I", ["1"], "", 0),   # 停用
    ]
    store._cache_ts = __import__("time").monotonic() + 1000  # 强制命中

def test_equiv_filter_and_active(monkeypatch, tmp_path, mocker=None):
    _mkrows()
    eq = load_equivalent_groups()
    assert {g.canonical for g in eq} == {"混凝土", "坍落度"}
    assert {g.kind for g in eq} <= set(store.EQUIV_KINDS)
    cf = load_confusable_pairs()
    assert [c.canonical for c in cf] == ["箍筋"]


def test_store_loads_from_db(monkeypatch, tmp_path):
    """无缓存时从真实临时库读 active，变体拆分"""
    from app.database import init_db, get_db, DATABASE_PATH
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "s.db"))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','水灰比','W/C')")
    invalidate_lexicon_caches()
    eq = load_equivalent_groups()
    names = {g.canonical for g in eq}
    assert {"混凝土", "水灰比"} <= names
    row = next(g for g in eq if g.canonical == "水灰比")
    assert row.variants == ["W/C"]


def test_equiv_conflict_word_blocks_load(monkeypatch, tmp_path):
    """同一词分属两个 equiv 组 → load_equivalent_groups 返回空且记录冲突词"""
    from app.database import init_db, get_db, DATABASE_PATH
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "c.db"))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','混凝土','砼')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('synonym','钢筋','混凝土')")
    invalidate_lexicon_caches()
    assert load_equivalent_groups() == []
    assert store.equiv_conflict_word == "混凝土"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_store.py -v`
Expected: FAIL（ModuleNotFoundError: app.lexicon）

- [ ] **Step 3: 实现**

`app/lexicon/__init__.py`：

```python
"""词库子系统：三类词条（synonym/alias/confusable）的数据加载与纯逻辑消费。

边界（写注释）：同义词/别名只做 FTS 检索扩展与条文归一化，向量/embedding 输入
不替换原词；confusable 只做命中告警，绝不用于检索改写。
"""
from app.lexicon.store import (  # noqa: F401
    KIND_ALIAS, KIND_CONFUSABLE, KIND_SYNONYM, EQUIV_KINDS,
    LexiconRow, invalidate_lexicon_caches, load_confusable_pairs,
    load_equivalent_groups,
)
```

`app/lexicon/store.py`：

```python
"""词库数据访问：DB 读取 + 模块级 TTL 缓存 + 词表一致性校验。

缓存仿 rule_engine 旧同义词缓存（TTL 60s + DATABASE_PATH 守卫，测试换库不串）。
词库写路径（routes/CSV）变更后必须调 invalidate_lexicon_caches()（唯一失效出口，
同时延迟调用 hybrid_search.clear_search_cache()）。
"""
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

KIND_SYNONYM = "synonym"
KIND_ALIAS = "alias"
KIND_CONFUSABLE = "confusable"
EQUIV_KINDS = (KIND_SYNONYM, KIND_ALIAS)

_TTL = 60.0


@dataclass
class LexiconRow:
    """一行 lexicon_entries 的词条行（variants 已拆为列表）。"""
    id: int
    kind: str
    canonical: str
    variants: list[str]
    distinguish: str = ""
    is_active: int = 1


_cache: list[LexiconRow] | None = None
_cache_ts = 0.0
_cache_path: str | None = None
# 词表一致性冲突词（同一词属多个 equiv 组）；空串表示无冲突
equiv_conflict_word: str | None = None


def _row_from(rec: dict) -> LexiconRow:
    variants = [v.strip() for v in (rec.get("variants") or "").split(",") if v.strip()]
    return LexiconRow(
        id=rec["id"], kind=rec["kind"], canonical=rec["canonical"],
        variants=variants, distinguish=rec.get("distinguish") or "",
        is_active=rec.get("is_active", 1),
    )


def _fresh() -> list[LexiconRow]:
    """从 DB 加载全部 active 词条（无缓存污染）；失败/无冲突校验用逻辑见 _load_all。"""
    from app.database import get_db
    rows = []
    with get_db() as conn:
        for rec in conn.execute(
            "SELECT id, kind, canonical, variants, distinguish, is_active "
            "FROM lexicon_entries WHERE is_active = 1 ORDER BY id"
        ).fetchall():
            rows.append(_row_from(dict(rec)))
    return rows


def _check_equiv_unique(rows: list[LexiconRow]) -> bool:
    """同一词不得同时属于多个 equiv 组（canonical/variants 合计）。confusable 独立。

    通过时 equiv_conflict_word 置 None；冲突时记录冲突词并返回 False。
    """
    global equiv_conflict_word
    equiv_conflict_word = None
    owner: dict[str, int] = {}
    for r in rows:
        if r.kind not in EQUIV_KINDS:
            continue
        for w in [r.canonical, *r.variants]:
            prev = owner.get(w)
            if prev is not None and prev != r.id:
                equiv_conflict_word = w
                return False
            owner[w] = r.id
    return True


def _load_all() -> list[LexiconRow]:
    global _cache, _cache_ts, _cache_path
    from app.database import DATABASE_PATH
    now = time.monotonic()
    if _cache is not None and _cache_path == DATABASE_PATH and (now - _cache_ts) < _TTL:
        return _cache
    try:
        if not os.path.exists(DATABASE_PATH):
            rows: list[LexiconRow] = []
        else:
            rows = _fresh()
        if _check_equiv_unique(rows):
            _cache = rows
        else:
            logger.warning("词库 equiv 词条冲突（同一词属多组），本次加载作废: %s",
                           equiv_conflict_word)
            _cache = []
    except Exception as e:
        logger.warning("词库加载失败，回退空列表: %s", e)
        _cache = []
    _cache_ts = now
    _cache_path = DATABASE_PATH
    return _cache


def load_equivalent_groups() -> list[LexiconRow]:
    """active 的 synonym/alias 组（供检索扩展与规则归一化）。冲突时返回空列表。"""
    return [r for r in _load_all() if r.kind in EQUIV_KINDS]


def load_confusable_pairs() -> list[LexiconRow]:
    """active 的 confusable 组（供命中检测）。"""
    return [r for r in _load_all() if r.kind == KIND_CONFUSABLE]


def invalidate_lexicon_caches() -> None:
    """清词库缓存 + 检索结果缓存。词库任何写操作后必须调用。"""
    global _cache, _cache_ts, _cache_path, equiv_conflict_word
    _cache = None
    _cache_ts = 0.0
    _cache_path = None
    equiv_conflict_word = None
    try:
        from app.search.hybrid_search import clear_search_cache  # 延迟：避免包级环依赖
        clear_search_cache()
    except Exception as e:
        logger.debug("clear_search_cache 调用失败: %s", e)
```

> 注意：上面 `_check_equiv_unique`/`_load_all` 的全局处理使用了一个冗余 `equiv_conflict_word_holder` 局部，属实现噪音——请在实现时收敛为只在冲突分支 `global equiv_conflict_word; equiv_conflict_word = w; return False`，函数开头重置即可。测试只断言 `store.equiv_conflict_word` 与返回空列表。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lexicon/ tests/test_lexicon_store.py
git commit -m "feat: 词库 store 加载 + TTL 缓存 + equiv 词表一致性校验"
```

---

### Task 3: `expand.py` — 检索查询扩展（词条感知切词 + OR 组）

**Files:**
- Create: `app/lexicon/expand.py`
- Test: `tests/test_lexicon_expand.py`

**Interfaces:**
- Consumes: `LexiconRow`（Task 2）；`app/search/tokenize.build_match_query`（退化委托）与 `tokenize` 切词。
- Produces: `build_expanded_match(keyword, groups, join_with)`。Task 6（sql_search 接线）依赖。

- [ ] **Step 1: 写失败测试**

```python
from app.lexicon.store import LexiconRow
from app.lexicon.expand import build_expanded_match

G = [
    LexiconRow(1, "alias", "混凝土", ["砼", "混泥土"]),
    LexiconRow(2, "synonym", "坍落度", ["塌落度"]),
    LexiconRow(3, "alias", "混凝土结构", ["砼结构"]),  # 互为前缀组（长词优先）
]

def test_oov_variant_expands():
    # 俗词即使 jieba 切不出（此处整词命中词表）也展开
    out = build_expanded_match("塌落度试验", [LexiconRow(9, "alias", "坍落度", ["塌落度"])])
    assert '("坍落度" OR "塌落度")' in out and '"试验"' in out

def test_canonical_expansion_and_normal_token():
    out = build_expanded_match("砼强度等级", G)
    # 命中 alias 混凝土组 → OR；强度/等级为普通词 token AND 连接
    assert '("混凝土" OR "砼" OR "混泥土")' in out
    assert out.endswith('AND "强度" AND "等级"') or '"强度"' in out and '"等级"' in out
    assert out.count("OR") == 2

def test_longest_prefix_group_wins():
    # 词库同时含「混凝土」与「混凝土结构」：长词优先命中后者，不重复展开前者
    g = [LexiconRow(1, "alias", "混凝土", ["砼"]),
         LexiconRow(2, "alias", "混凝土结构", ["砼结构"])]
    out = build_expanded_match("混凝土结构施工", g)
    assert '("混凝土结构" OR "砼结构")' in out
    assert '("混凝土" OR "砼")' not in out

def test_no_group_delegates_to_original():
    import jieba
    from app.search.tokenize import build_match_query as orig
    kw = "钢筋混凝土强度"
    assert build_expanded_match(kw, []) == orig(kw)

def test_join_or_and_quote_escape():
    out = build_expanded_match('抗"震"砼', [LexiconRow(1, "alias", "混凝土", ["砼"])], "OR")
    assert '"砼"' in out  # 词条词参与；引号转义沿用原版 quote
    out2 = build_expanded_match("砼", G, "AND")
    assert out2.startswith("(") and out2.endswith(")")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_expand.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
"""检索查询扩展：词条感知切词 + OR 组，生成 FTS5 MATCH 串。

只改查询端——索引 search_text 与向量输入均不改一字（spec 边界 1）。
groups 为空/开关关时直接委托 app.search.tokenize.build_match_query，保证旧行为
与既有检索测试逐字节一致（退化护栏）。
"""
from app.lexicon.store import LexiconRow


def _quote(word: str) -> str:
    return '"' + word.replace('"', '""') + '"'


def _terms_by_len(groups: list[LexiconRow]) -> list[tuple[int, str]]:
    """返回 (组下标, 词) 按词长降序；同一词跨组已被 store 校验拒绝，term→组唯一。"""
    items: dict[str, int] = {}
    for gi, g in enumerate(groups):
        for w in [g.canonical, *g.variants]:
            items.setdefault(w, gi)
    return [(gi, w) for w, gi in sorted(items.items(), key=lambda kv: len(kv[0]), reverse=True)]


def _items(keyword: str, groups: list[LexiconRow]) -> list[tuple[str, object]]:
    """把 keyword 切成有序项列表。

    返回 list of ('group', (gi, hit_word)) | ('seg', 子串)；seg 交给 jieba 再切。
    """
    terms = _terms_by_len(groups)
    pieces: list[tuple[str, object]] = []
    i, n = 0, len(keyword)
    while i < n:
        matched = False
        for gi, w in terms:
            if keyword.startswith(w, i):
                pieces.append(("group", (gi, w)))
                i += len(w)
                matched = True
                break
        if matched:
            continue
        j = i
        while j < n:
            if any(keyword.startswith(w, j) for _, w in terms):
                break
            j += 1
        if j > i:
            pieces.append(("seg", keyword[i:j]))
        i = j
    return pieces


def _group_words(groups: list[LexiconRow], gi: int) -> list[str]:
    seen: list[str] = []
    for w in [groups[gi].canonical, *groups[gi].variants]:
        if w and w not in seen:
            seen.append(w)
    return seen


def build_expanded_match(keyword: str, groups: list[LexiconRow],
                         join_with: str = "AND") -> str:
    """生成 FTS5 MATCH 串；组内候选词 OR、项间按 join_with 连接；空词返回空串。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return ""
    if not groups:  # 退化委托：开关关或无词条
        from app.search.tokenize import build_match_query
        return build_match_query(keyword, join_with)

    tokens: list[str] = []
    for kind, payload in _items(keyword, groups):
        if kind == "group":
            gi, _ = payload
            words = [_quote(w) for w in _group_words(groups, gi)]
            if len(words) == 1:
                tokens.append(words[0])
            else:
                tokens.append("(" + " OR ".join(words) + ")")
        else:
            from app.search.tokenize import tokenize
            for tok in tokenize(payload):
                tokens.append(_quote(tok))
    if not tokens:
        return ""
    return f" {join_with} ".join(tokens)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_expand.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lexicon/expand.py tests/test_lexicon_expand.py
git commit -m "feat: 检索查询扩展 expand（词条感知切词 + OR 组 + 退化委托）"
```

---

### Task 4: `normalize.py` + `confusable.py` — 条文归一化与易混淆命中

**Files:**
- Create: `app/lexicon/normalize.py`, `app/lexicon/confusable.py`
- Test: `tests/test_lexicon_normalize.py`, `tests/test_lexicon_confusable.py`

**Interfaces:**
- Consumes: `LexiconRow`（Task 2）。
- Produces: `normalize_text`（Task 5 rule_engine 依赖）；`detect_confusable`（Task 7 依赖）。

- [ ] **Step 1: 写失败测试**

`tests/test_lexicon_normalize.py`：

```python
from app.lexicon.store import LexiconRow
from app.lexicon.normalize import normalize_text

G = [LexiconRow(1, "alias", "混凝土", ["砼", "混泥土"]),
     LexiconRow(2, "confusable", "箍筋", ["钢筋"], "子类区分"),
     LexiconRow(3, "alias", "高强螺栓", ["高强度螺栓"])]

def test_variant_to_canonical():
    assert normalize_text("采用砼浇筑，混泥土强度合格", G) == "采用混凝土浇筑，混凝土强度合格"

def test_canonical_untouched_confusable_ignored():
    assert normalize_text("混凝土", G) == "混凝土"
    assert "箍筋" not in "钢筋强度" and normalize_text("钢筋强度 箍筋", G).count("箍筋") == 1

def test_longer_variant_replaced_first():
    assert normalize_text("高强度螺栓连接 混泥土", G) == "高强螺栓连接 混凝土"
```

`tests/test_lexicon_confusable.py`：

```python
from app.lexicon.store import LexiconRow
from app.lexicon.confusable import detect_confusable

P = [LexiconRow(1, "confusable", "箍筋", ["钢筋"], "箍筋是钢筋加工成型的构造钢筋（子类）"),
     LexiconRow(2, "confusable", "静力触探", ["动力触探"], "两种地基勘探方法，加载方式不同")]

def test_both_hit():
    hits = detect_confusable("静力触探和动力触探有何区别", P)
    assert len(hits) == 1 and hits[0]["a"] == "静力触探" and hits[0]["b"] == "动力触探"
    assert "加载" in hits[0]["distinguish"]

def test_single_no_hit_and_empty():
    assert detect_confusable("静力触探适用什么土层", P) == []
    assert detect_confusable("", P) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_normalize.py tests/test_lexicon_confusable.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`app/lexicon/normalize.py`：

```python
"""条文归一化：把 active 等价组中的变体词替换为组 canonical（仅 synonym/alias）。

替换方向唯一：variants → canonical，绝不反向替换 canonical。变体按词长降序处理，
避免短俗词抢先替换破坏长词（沿用 rule_engine 旧语义）。局限（注释）：单字俗词
（如「砼」）在极端语境可能误伤词内同形字，工程词库语境可接受。
"""
from app.lexicon.store import EQUIV_KINDS, LexiconRow


def normalize_text(text: str, groups: list[LexiconRow]) -> str:
    if not text or not groups:
        return text
    repl = []
    for g in groups:
        if g.kind not in EQUIV_KINDS:
            continue
        for v in g.variants:
            if v and v != g.canonical:
                repl.append((v, g.canonical))
    repl.sort(key=lambda x: len(x[0]), reverse=True)
    out = text
    for v, c in repl:
        out = out.replace(v, c)
    return out
```

`app/lexicon/confusable.py`：

```python
"""易混淆命中检测（纯函数）。

语义校准（spec §5.4）：针对「原问句同现两个易混淆概念、用户需要区分」的场景
（如「钢筋和箍筋有何不同」）。不做「单用歧义」推断——用户只问 A 时不提示可能
意指 B。行级校验已拒绝互含子串词对，避免长词语境恒命中。
"""
from app.lexicon.store import LexiconRow


def detect_confusable(text: str, pairs: list[LexiconRow]) -> list[dict]:
    if not text:
        return []
    hits = []
    for p in pairs:
        if p.canonical not in text:
            continue
        for v in p.variants:
            if v and v in text:
                hits.append({"a": p.canonical, "b": v, "distinguish": p.distinguish})
                break
    return hits
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_normalize.py tests/test_lexicon_confusable.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lexicon/normalize.py app/lexicon/confusable.py \
       tests/test_lexicon_normalize.py tests/test_lexicon_confusable.py
git commit -m "feat: 条文归一化 normalize + 易混淆命中 confusable 纯函数"
```

---

### Task 5: rule_engine 归一化切源 + 保留旧 UI 的过渡兼容

**Files:**
- Modify: `app/classifier/rule_engine.py`（删自带 synonym 缓存/加载/normalize）
- Test: `tests/test_rule_engine.py`, `tests/test_database.py`

**Interfaces:**
- Consumes: `app.lexicon.store.load_equivalent_groups`、`app.lexicon.normalize.normalize_text`。
- Produces: `classify_clause(..., synonyms=None)` 注入语义改为 `LexiconRow` 组行；`clear_synonym_cache` 保留为过渡别名（Task 8 删 synonym_routes 后一并移除）。

- [ ] **Step 1: 改测试（先红）** —— 在 `tests/test_rule_engine.py` 替换注入结构：

```python
from app.lexicon.store import LexiconRow

def test_normalize_uses_lexicon_groups():
    from app.lexicon.normalize import normalize_text
    groups = [LexiconRow(1, "alias", "混凝土", ["砼"])]
    assert normalize_text("砼强度等级不应低于C30", groups) == "混凝土强度等级不应低于C30"

def test_classify_clause_synonym_normalization_matches():
    from app.classifier.rule_engine import classify_clause
    rules = [{"id": 1, "dimension": "dim6", "pattern": "混凝土", "match_type": "keyword",
              "priority": 1, "threshold": 0.0, "is_active": 1, "label": "混凝土"}]
    groups = [LexiconRow(1, "alias", "混凝土", ["砼"])]
    scores, labels, _ = classify_clause("砼强度等级不应低于C30", [], rules, synonyms=groups)
    assert labels.get("dim6") == "混凝土" and scores["dim6"] > 0
```

> 移除旧 `test_normalize_text_replaces_synonyms`/`test_normalize_text_no_synonyms_unchanged`（其断言迁至 normalize 纯函数测试）。

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_engine.py -v`
Expected: 至少含 normalize/注入用例 FAIL

- [ ] **Step 3: 实现** —— 在 `app/classifier/rule_engine.py`：

删除第 12-16 行同义词缓存全局、第 45-102 行 `normalize_text`/`clear_synonym_cache`/`_load_active_synonyms`。保留过渡别名（供仍存在的 synonym_routes 引用，Task 8 移除）：

```python
def clear_synonym_cache() -> None:
    """过渡兼容：旧同义词管理页调用点。Task 8 移除 synonym_routes 后删除本别名。"""
    from app.lexicon.store import invalidate_lexicon_caches
    invalidate_lexicon_caches()
```

修改 `classify_clause` 前段：

```python
    # 先归一化再匹配：词库 equiv 组（synonym/alias）把变体归并为 canonical + 父路径噪声过滤
    if synonyms is None:
        from app.lexicon.store import load_equivalent_groups
        synonyms = load_equivalent_groups()
    from app.lexicon.normalize import normalize_text
    clause_text = normalize_text(clause_text, synonyms)
    filtered_parent = [
        normalize_text(p, synonyms)
        for p in parent_path
        if p not in _PARENT_NOISE_TITLES
    ]
```

更新 `classify_clause` docstring：synonyms 注入语义由「source/target 映射行」改为「`lexicon.LexiconRow` 等价组行；None 时自动从词库加载（带缓存）」。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_engine.py tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/classifier/rule_engine.py tests/test_rule_engine.py
git commit -m "refactor: 规则引擎归一化切源 lexicon（classify_clause 注入语义改 LexiconRow）"
```

---

### Task 6: 检索扩展接线——sql_search 薄包装 + 参数 + 缓存 key

**Files:**
- Modify: `app/search/sql_search.py`, `app/search/hybrid_search.py`, `app/config.py`, `app/params/registry.py`
- Test: `tests/test_hybrid_search.py`, `tests/test_search_param_reading.py`（或新建 `tests/test_search_lexicon_expand.py`）

**Interfaces:**
- Consumes: `expand.build_expanded_match`、`store.load_equivalent_groups`（Task 2/3）；registry 参数体系。
- Produces: `search.lexicon_expand`（0/1，默认 1）；`hybrid_search._cache_key` 含该参数维度。

- [ ] **Step 1: 写失败测试**

`tests/test_search_lexicon_expand.py`：

```python
def test_alias_input_recalls_canonical_document(monkeypatch, tmp_path):
    """输入俗词砼 → 扩展出混凝土 → 命中含混凝土条文（主方向）"""
    from fastapi.testclient import TestClient
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "x.db"))
    # 同步切词 DB 路径（tokenize 无路径依赖；hybrid/store 各自读 DATABASE_PATH，靠换库+清缓存）
    from app.lexicon import store
    store.invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications(code,title) VALUES ('GB 50204-2015','混凝土结构')")
        spec_id = conn.execute("SELECT id FROM specifications").fetchone()["id"]
        conn.execute(
            "INSERT INTO clauses(spec_id, clause_no, title, content, search_text) VALUES (?,?,?,?,?)",
            (spec_id, "4.1", "强度", "混凝土强度不应低于设计值",
             "混凝土 强度 不应 低于 设计 值 4.1"))
    from app.models import SearchQuery
    from app.search.hybrid_search import hybrid_search
    monkeypatch.setattr("app.search.hybrid_search.VectorStore", None)  # 阻断向量
    monkeypatch.setattr("app.search.hybrid_search.rerank_candidates",
                        lambda q, cs: ([(d, 1.0) for d in cs], "none"))
    res, _ = hybrid_search(SearchQuery(keyword="砼"))
    assert any("混凝土" in r["content"] for r in res)


def test_expand_switch_off_returns_empty(monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "y.db"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications(code,title) VALUES ('GB 50204-2015','混凝土结构')")
        sid = conn.execute("SELECT id FROM specifications").fetchone()["id"]
        conn.execute("INSERT INTO clauses(spec_id,clause_no,title,content,search_text) VALUES (?,?,?,?,?)",
                     (sid, "4.1", "强度", "混凝土强度不应低于设计值", "混凝土 强度 不应 低于 设计 值 4.1"))
    from app.models import SearchQuery
    from app.search.hybrid_search import hybrid_search
    monkeypatch.setattr("app.search.hybrid_search.VectorStore", None)
    monkeypatch.setattr("app.search.hybrid_search.rerank_candidates",
                        lambda q, cs: ([(d, 1.0) for d in cs], "none"))
    monkeypatch.setattr("app.params.registry.get_param_int",
                        lambda k: 0 if k == "search.lexicon_expand" else 1)
    res, _ = hybrid_search(SearchQuery(keyword="砼"))
    assert res == []  # 开关关：纯原词「砼」无条文命中
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_search_lexicon_expand.py -v`
Expected: FAIL（参数未定义 / sql_search 未扩展）

- [ ] **Step 3: 实现**

`app/config.py`（`SEARCH_RRF_K` 行后）：

```python
SEARCH_LEXICON_EXPAND = 1       # 词库同义/别名检索扩展开关（1 开 / 0 关）
```

`app/params/registry.py`：
- import 加 `SEARCH_LEXICON_EXPAND`。
- search 组 `search.rrf_k` meta 后追加：

```python
    meta.append(_num(
        "search.lexicon_expand", "search", "词库检索扩展", float(SEARCH_LEXICON_EXPAND),
        0, 1, "0~1",
        "1=检索把词库同义/别名等价词纳入 FTS（扩召回）；0=退回纯原词。规则归一化与易混淆提示不受影响。", dtype="int"))
```

`app/search/sql_search.py`：
- 顶部 import 去掉 `from app.search.tokenize import build_match_query`（若无他用）。
- 在 `search_clauses` 内、`with get_db()` 之前加一个嵌套 helper，替换两处 MATCH 构建：

```python
        def _match_for(keyword: str, join: str = "AND") -> str:
            """keyword → FTS MATCH 串。开关关/无词条时委托原 build_match_query（退化护栏）。"""
            if not keyword:
                return ""
            from app.params.registry import get_param_int
            from app.lexicon import store, expand
            groups = store.load_equivalent_groups() if get_param_int("search.lexicon_expand") else []
            return expand.build_expanded_match(keyword, groups, join)
```

替换：
- `match = build_match_query(query.keyword) if query.keyword else ""` → `match = _match_for(query.keyword)`
- `or_match = build_match_query(query.keyword, "OR")` → `or_match = _match_for(query.keyword, "OR")`

（`or_match != match` 比较与降级逻辑不变，两处现在都含扩展产物。）

`app/search/hybrid_search.py::_cache_key`：在 `query.ce_rerank,` 之后追加一维：

```python
        from app.params.registry import get_param_int
        return (
            DATABASE_PATH,
            query.keyword,
            ...
            query.ce_rerank, bool(get_param_int("search.lexicon_expand")),
            tuple(query.status_filter),
        )
```

并在向量调用点（第 90-94 行 `vs.search(keyword, ...)`）上方加注释：

```python
        # 边界：向量输入用 keyword 原文，不做词库改写（spec 边界 1；测试锁定）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_search_lexicon_expand.py tests/test_hybrid_search.py tests/test_search.py tests/test_search_param_reading.py tests/test_search_rerank.py -v`
Expected: PASS（原检索行为不回归，因退化委托）

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/params/registry.py app/search/sql_search.py \
       app/search/hybrid_search.py tests/test_search_lexicon_expand.py
git commit -m "feat: 检索扩展接线 sql_search（词条 OR）+ search.lexicon_expand 参数 + 缓存 key 维度"
```

---

### Task 7: 易混淆命中接线——检索页 + QA + 前端

**Files:**
- Modify: `app/models.py`, `app/routes/qa_routes.py`, `app/routes/search_routes.py`, `app/templates/partials/result_content.html`, `app/templates/partials/qa_panel.html`, `static/components/qa.js`
- Test: `tests/test_search_routes.py`, `tests/test_qa_routes.py`

**Interfaces:**
- Consumes: `confusable.detect_confusable`、`store.load_confusable_pairs`（Task 2/4）。
- Produces: `QAResponse.confusable_hits: list[dict]`；检索结果模板变量 `confusable_hits`；QA 前端 bot 消息 `confusable`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_search_routes.py 追加
def test_search_page_renders_confusable_hint(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "s.db"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants,distinguish)"
                     " VALUES ('confusable','箍筋','钢筋','子类区分')")
        conn.execute("INSERT INTO specifications(code,title) VALUES ('JGJ 107-2016','接头')")
        sid = conn.execute("SELECT id FROM specifications").fetchone()["id"]
        conn.execute("INSERT INTO clauses(spec_id,clause_no,title,content,search_text)"
                     " VALUES (?,?,?,?,?)", (sid, "3", "", "钢筋接头", "钢筋 接头 3"))
    from app.lexicon import store
    store.invalidate_lexicon_caches()
    resp = auth_client.get("/search?keyword=%E7%AD%8B%E9%AA%A8%E9%92%A2%E7%AD%8B")  # 箍筋钢筋
    assert resp.status_code == 200
    assert "子类区分" in resp.text or "箍筋" in resp.text


# tests/test_qa_routes.py 追加
def test_qa_confusable_hits_returned(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "q.db"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants,distinguish)"
                     " VALUES ('confusable','箍筋','钢筋','子类区分')")
    from app.lexicon import store
    store.invalidate_lexicon_caches()
    # monkeypatch 后端 CLI 为可用假后端，避免真实调用
    import app.routes.qa_routes as qr
    class _Fake:
        def is_available(self): return True
        async def ask(self, **kw): return type("R", (), {"success": True, "content": "ok", "error": ""})()
    monkeypatch.setattr(qr, "get_backend", lambda b: _Fake())
    monkeypatch.setattr("app.search.hybrid_search.VectorStore", None)
    resp = auth_client.post("/qa/ask", json={"question": "箍筋与钢筋有什么区别", "mode": "rag"})
    data = resp.json()
    assert data["confusable_hits"] and data["confusable_hits"][0]["distinguish"] == "子类区分"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_search_routes.py::test_search_page_renders_confusable_hint tests/test_qa_routes.py::test_qa_confusable_hits_returned -v`
Expected: FAIL（字段/提示不存在）

- [ ] **Step 3: 实现**

`app/models.py` `QAResponse`：

```python
class QAResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    cli_used: Optional[str] = None
    # 易混淆术语命中（用户问题同现 term_a/term_b）；仅提示，不做任何改写
    confusable_hits: list[dict] = []
```

`app/routes/search_routes.py::search`：在 `results, total = hybrid_search(sq)` 后、`result_list` 渲染前：

```python
    confusable_hits = []
    if keyword:
        from app.lexicon import store, confusable
        confusable_hits = confusable.detect_confusable(
            keyword, store.load_confusable_pairs())
```

并在 `TemplateResponse` context 里加 `"confusable_hits": confusable_hits`。（空查询早退分支 keyword 必为空，无需传。）

`app/templates/partials/result_content.html`：在 `<div id="search-results">` include 之后的**内容顶部**（首条结果渲染前）插：

```html
{% if confusable_hits %}
<div style="margin-bottom:0.6rem;padding:0.5rem 0.7rem;border:1px solid #e0a800;border-radius:6px;background:#fff8e1;color:#5a3d00;font-size:0.85rem">
    <strong>⚠️ 术语区分提示：</strong>
    {% for h in confusable_hits %}
    <div style="margin-top:0.2rem">{{ h.a }} 与 {{ h.b }}：{{ h.distinguish }}</div>
    {% endfor %}
</div>
{% endif %}
```

`app/routes/qa_routes.py::qa_ask` 返回前：

```python
    confusable_hits = []
    if question:
        from app.lexicon import store, confusable
        confusable_hits = confusable.detect_confusable(
            question, store.load_confusable_pairs())
    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits)
```

`static/components/qa.js::send()` bot push 对象加字段：

```javascript
                    content: data.answer || '(AI 未返回回答)',
                    sources: data.sources || [],
                    confusable: data.confusable_hits || [],
```

`app/templates/partials/qa_panel.html` bot 消息内、`renderMarkdown` 那行 `<div x-html=...>` **之前**插（用 `x-text` 杜绝注入）：

```html
                        <template x-if="msg.confusable && msg.confusable.length">
                            <div style="margin-bottom:0.4rem;padding:0.4rem 0.6rem;border:1px solid #e0a800;border-radius:6px;background:#fff8e1;font-size:0.78rem">
                                <strong>⚠️ 术语区分提示</strong>
                                <template x-for="h in msg.confusable" :key="h.a + '|' + h.b">
                                    <div style="margin-top:0.15rem">
                                        <strong x-text="h.a"></strong> 与 <strong x-text="h.b"></strong>：
                                        <span x-text="h.distinguish"></span>
                                    </div>
                                </template>
                            </div>
                        </template>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_search_routes.py tests/test_qa_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/routes/qa_routes.py app/routes/search_routes.py \
       app/templates/partials/result_content.html app/templates/partials/qa_panel.html \
       static/components/qa.js tests/test_search_routes.py tests/test_qa_routes.py
git commit -m "feat: 易混淆术语命中接线（检索页提示条 + QA confusable_hits + 前端警告块）"
```

---

### Task 8: 词库管理 WebUI /lexicon + 行内编辑 + CSV 导入 + 收尾删除旧链

**Files:**
- Create: `app/lexicon/validation.py`, `app/routes/lexicon_routes.py`, `app/templates/lexicon.html`, `app/templates/partials/lexicon_list.html`, `app/templates/partials/lexicon_row.html`, `app/templates/partials/lexicon_edit_row.html`, `app/templates/partials/lexicon_import_result.html`
- Modify: `app/main.py`（挂载 lexicon_router、移除 synonym_router）、`app/templates/partials/tree_panel.html`（按钮改 /lexicon）、`app/database.py`（收尾：SCHEMA_SQL 删 synonym_map DDL、init_db 末尾 DROP）
- Delete: `app/routes/synonym_routes.py`, `app/templates/synonyms.html`, `app/templates/partials/synonyms_list.html`, `app/templates/partials/synonyms_row.html`, `tests/test_synonym_routes.py`（由 `tests/test_lexicon_routes.py` 承接）
- Create Test: `tests/test_lexicon_routes.py`

**Interfaces:**
- Consumes: `store.invalidate_lexicon_caches`（Task 2）；`validation.validate_row`（本任务定义）；`clear_synonym_cache` 过渡别名到本任务后移除。
- Produces: `/lexicon` 三 Tab 页面；词库 CRUD/toggle/edit/CSV；`synonym_map` 全链删除；全库 `grep synonym_map` 零命中。

> **拆分提示**：本任务较大，可按 8a（validation + 路由 CRUD/toggle/edit + 页面/列表）、8b（CSV 导入）、8c（tree_panel/删除旧链/schema drop + 收尾验收）三段各 commit，仍是同一逻辑单元。

- [ ] **Step 1a: 写失败测试**（`tests/test_lexicon_routes.py`）

```python
def test_lexicon_page_and_create(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l.db"))
    init_db()
    resp = auth_client.get("/lexicon")
    assert resp.status_code == 200 and "词库" in resp.text
    r2 = auth_client.post("/lexicon/create", data={"kind": "alias", "canonical": "水灰比", "variants": "W/C,水胶比", "distinguish": ""})
    assert r2.status_code == 200 and "已添加" in r2.text
    r3 = auth_client.post("/lexicon/create", data={"kind": "alias", "canonical": "水灰比", "variants": "W/C"})
    assert r3.status_code == 400  # 同 kind canonical 并立 → 拒绝
    # confusable 互含子串拒绝
    r4 = auth_client.post("/lexicon/create", data={"kind": "confusable", "canonical": "沉降", "variants": "差异沉降", "distinguish": "范围不同"})
    assert r4.status_code == 400
    assert "子串" in r4.text


def test_lexicon_toggle_edit_delete_and_filter(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l2.db"))
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','坍落度','塌落度')")
        lid = cur.lastrowid
    assert auth_client.post(f"/lexicon/{lid}/toggle").status_code == 200
    auth_client.post(f"/lexicon/{lid}/edit", data={"canonical": "坍落度", "variants": "塌落度,落度"})
    with get_db() as conn:
        row = conn.execute("SELECT is_active, variants FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
        assert row["is_active"] == 0 and "落度" in row["variants"]
    assert auth_client.delete(f"/lexicon/{lid}").status_code == 200


def test_lexicon_csv_import(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l3.db"))
    init_db()
    csv_text = ("kind,canonical,variants,distinguish,note\n"
                "alias,防水卷材,卷材,,seed\n"
                "confusable,圈梁,构造柱,竖向构件不同,\n"
                "alias,坍落度,塌落度,,dup\n"
                "alias,坍落度,塌落度,,dup\n"
                ",,bad,,extra\n")
    resp = auth_client.post("/lexicon/import",
                            files={"file": ("seed.csv", csv_text.encode("utf-8"), "text/csv")})
    assert resp.status_code == 200
    body = resp.text
    assert "成功" in body and "跳过" in body and "失败" in body
```

- [ ] **Step 2a: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_routes.py -v`
Expected: FAIL（404 / 模块缺失）

- [ ] **Step 3a: 实现（validation + 路由 + 页面骨架）**

`app/lexicon/validation.py`：

```python
"""词库行级校验（路由表单与 CSV 导入共用，单一来源）。"""
from app.lexicon.store import KIND_ALIAS, KIND_CONFUSABLE, KIND_SYNONYM

_KINDS = (KIND_SYNONYM, KIND_ALIAS, KIND_CONFUSABLE)
_EQUIV = (KIND_SYNONYM, KIND_ALIAS)


def _split(variants: str) -> list[str]:
    return [v.strip() for v in (variants or "").split(",") if v.strip()]


def validate_row(kind: str, canonical: str, variants: str, distinguish: str = "") -> tuple[dict | None, str | None]:
    """校验并返回 (净数据 dict | None, 错误信息 | None)。"""
    kind = (kind or "").strip()
    canonical = (canonical or "").strip()
    if kind not in _KINDS:
        return None, "kind 不合法"
    if not canonical:
        return None, "代表词/词 A 不能为空"
    vs = _split(variants)
    if kind == KIND_CONFUSABLE:
        if len(vs) != 1:
            return None, "易混淆须为 词A + 词B（variants 恰一个词）"
        if not distinguish or not distinguish.strip():
            return None, "易混淆必须填写区分说明"
        if vs[0] == canonical:
            return None, "词A 与 词B 不能相同"
        if vs[0] in canonical or canonical in vs[0]:
            return None, "词A 与 词B 不得互为子串（防命中自误报）"
    else:
        if not vs:
            return None, "变体/俗称词不能为空"
        for v in vs:
            if v == canonical:
                return None, f"变体词「{v}」不能与代表词相同"
            if v in canonical or canonical in v:
                return None, f"变体词「{v}」不得与代表词互为子串"
    return {"kind": kind, "canonical": canonical, "variants": ",".join(vs),
            "distinguish": distinguish.strip()}, None
```

`app/routes/lexicon_routes.py`（核心端点；HTML partial 渲染模式仿 `synonym_routes`/`maintenance`）：

```python
"""词库管理路由：/lexicon 三 Tab（synonym/alias/confusable）统一 CRUD + CSV 导入。"""
import csv
import io

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.lexicon.store import invalidate_lexicon_caches
from app.lexicon.validation import validate_row
from app.lexicon.store import EQUIV_KINDS, KIND_CONFUSABLE

router = APIRouter()

# kind → 文案列头（模板按此渲染，避免三套列表）
KIND_COLUMNS = {
    "synonym": ("代表词", "等价词", "等价词说明"),
    "alias": ("规范词", "俗称/变体", "别名说明"),
    "confusable": ("词 A", "词 B", "区分说明"),
}


@router.get("/lexicon")
async def lexicon_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "lexicon.html",
    })


@router.get("/lexicon/list")
async def lexicon_list(request: Request, kind: str = "alias", active: str = ""):
    from app.main import templates
    with get_db() as conn:
        if active in ("0", "1"):
            rows = conn.execute(
                "SELECT * FROM lexicon_entries WHERE kind=? AND is_active=? ORDER BY id DESC",
                (kind, int(active))).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lexicon_entries WHERE kind=? ORDER BY id DESC", (kind,)).fetchall()
    return templates.TemplateResponse(request, "partials/lexicon_list.html", {
        "rows": [dict(r) for r in rows],
        "kind": kind, "columns": KIND_COLUMNS.get(kind, KIND_COLUMNS["alias"]),
        "filter_active": active,
    })


@router.post("/lexicon/create")
async def create_lexicon(request: Request, kind: str = Form(...),
                         canonical: str = Form(""), variants: str = Form(""),
                         distinguish: str = Form("")):
    data, err = validate_row(kind, canonical, variants, distinguish)
    if err:
        return HTMLResponse(f'<p style="color:red">❌ {err}</p>', status_code=400)
    with get_db() as conn:
        # synonym/alias 组唯一（应用层）：同 kind canonical 已存在 → 拒绝并立
        if kind in EQUIV_KINDS:
            dup = conn.execute(
                "SELECT 1 FROM lexicon_entries WHERE kind=? AND canonical=?",
                (kind, data["canonical"])).fetchone()
            if dup:
                return HTMLResponse(
                    '<p style="color:orange">⚠️ 该代表词已有词条，请在其变体列补充</p>', status_code=400)
        cur = conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note)"
            " VALUES (?,?,?,?,'WebUI 新增')",
            (data["kind"], data["canonical"], data["variants"], data["distinguish"]))
    invalidate_lexicon_caches()
    if cur.rowcount == 0:
        return HTMLResponse('<p style="color:orange">⚠️ 该词条已存在（全行幂等）</p>',
                            headers={"HX-Trigger": "lexiconUpdated"})
    return HTMLResponse('<p style="color:green;margin-top:0.5rem">✅ 词条已添加</p>',
                        headers={"HX-Trigger": "lexiconUpdated"})


@router.post("/lexicon/{lid}/toggle")
async def toggle_lexicon(request: Request, lid: int):
    with get_db() as conn:
        conn.execute("UPDATE lexicon_entries SET is_active = 1 - is_active WHERE id=?", (lid,))
        row = conn.execute("SELECT * FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    invalidate_lexicon_caches()
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_row.html", {
        "row": dict(row), "columns": KIND_COLUMNS.get(row["kind"], KIND_COLUMNS["alias"])})


@router.get("/lexicon/{lid}/edit")
async def edit_lexicon_form(request: Request, lid: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_edit_row.html",
                                      {"row": dict(row), "kind": row["kind"]})


@router.post("/lexicon/{lid}/edit")
async def edit_lexicon(request: Request, lid: int, canonical: str = Form(""),
                       variants: str = Form(""), distinguish: str = Form("")):
    with get_db() as conn:
        row = conn.execute("SELECT kind FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    data, err = validate_row(row["kind"], canonical, variants, distinguish)
    if err:
        return HTMLResponse(f'<p style="color:red">❌ {err}</p>', status_code=400)
    if row["kind"] in EQUIV_KINDS:
        with get_db() as conn:
            dup = conn.execute(
                "SELECT 1 FROM lexicon_entries WHERE kind=? AND canonical=? AND id<>?",
                (row["kind"], data["canonical"], lid)).fetchone()
            if dup:
                return HTMLResponse('<p style="color:orange">⚠️ 已有同代表词词条</p>', status_code=400)
    with get_db() as conn:
        conn.execute("UPDATE lexicon_entries SET canonical=?, variants=?, distinguish=? WHERE id=?",
                     (data["canonical"], data["variants"], data["distinguish"], lid))
    invalidate_lexicon_caches()
    return await lexicon_list(request, kind=row["kind"])


@router.delete("/lexicon/{lid}")
async def delete_lexicon(request: Request, lid: int):
    with get_db() as conn:
        conn.execute("DELETE FROM lexicon_entries WHERE id=?", (lid,))
    invalidate_lexicon_caches()
    return HTMLResponse("", headers={"HX-Trigger": "lexiconUpdated"})


@router.post("/lexicon/import")
async def import_lexicon(request: Request, file: UploadFile = File(...),
                         kind: str = Form("")):
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    ok = skip = fail = 0
    fails: list[str] = []
    seen = set()
    with get_db() as conn:
        for line_no, rec in enumerate(reader, start=2):
            k = (rec.get("kind") or "").strip() or kind
            data, err = validate_row(k, rec.get("canonical", ""), rec.get("variants", ""),
                                     rec.get("distinguish", ""))
            if err:
                fail += 1
                fails.append(f"第{line_no}行: {err}")
                continue
            key = (data["kind"], data["canonical"], data["variants"])
            if key in seen:
                skip += 1
                continue
            seen.add(key)
            cur = conn.execute(
                "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note)"
                " VALUES (?,?,?,?,?)",
                (data["kind"], data["canonical"], data["variants"], data["distinguish"],
                 (rec.get("note") or "").strip() or "CSV 导入"))
            if cur.rowcount == 0:
                skip += 1
            else:
                ok += 1
    invalidate_lexicon_caches()
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_import_result.html",
                                      {"ok": ok, "skip": skip, "fail": fail, "fails": fails})
```

`app/templates/lexicon.html`（center；仿 maintenance tab 结构 + kind 驱动表单）：

```html
<!-- 中栏：词库管理（三 Tab：同义词 / 别名 / 易混淆） -->
<div x-data="{ kind: 'alias' }" style="padding:1rem">
    <!-- 当前 kind 单一来源：供添加/导入表单 hx-include 与列表自动刷新带参 -->
    <input type="hidden" id="lexicon-kind-input" name="kind" :value="kind">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem">
        <h2 style="margin:0">📖 词库</h2>
        <small style="color:var(--pico-muted-color)">同义/别名只扩 FTS 检索；易混淆仅提示不改写（向量不替换原词）</small>
    </div>
    <!-- Tab 切换：设 Alpine kind（驱动表单）+ 立即 hx-get 刷新列表 -->
    <div style="display:flex;gap:0.5rem;margin:0.8rem 0;flex-wrap:wrap">
        <button class="outline" style="font-size:0.8rem" :class="kind==='synonym'?'contrast':''"
                @click="kind='synonym'" hx-get="/lexicon/list?kind=synonym" hx-target="#lexicon-table" hx-swap="innerHTML">🔄 同义词</button>
        <button class="outline" style="font-size:0.8rem" :class="kind==='alias'?'contrast':''"
                @click="kind='alias'" hx-get="/lexicon/list?kind=alias" hx-target="#lexicon-table" hx-swap="innerHTML">🏷 别名</button>
        <button class="outline" style="font-size:0.8rem" :class="kind==='confusable'?'contrast':''"
                @click="kind='confusable'" hx-get="/lexicon/list?kind=confusable" hx-target="#lexicon-table" hx-swap="innerHTML">⚠️ 易混淆术语</button>
    </div>

    <!-- 新增表单（synonym/alias：代表词+变体；confusable：词A+词B+区分） -->
    <form x-show="kind!=='confusable'" hx-post="/lexicon/create" hx-target="#create-result"
          hx-swap="innerHTML" hx-include="#lexicon-kind-input"
          hx-on::after-request="if(event.detail.successful) this.reset()">
        <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
            <label style="margin:0;white-space:nowrap"><span x-text="kind==='alias'?'规范词':'代表词'"></span></label>
            <input type="text" name="canonical" required style="width:150px;margin:0;height:40px">
            <label style="margin:0;white-space:nowrap">变体/俗称(逗号分隔)</label>
            <input type="text" name="variants" placeholder="如 砼,混泥土" required style="width:220px;margin:0;height:40px">
            <button type="submit" class="secondary" style="height:40px;margin:0">➕ 添加</button>
        </div>
    </form>
    <form x-show="kind==='confusable'" hx-post="/lexicon/create" hx-target="#create-result"
          hx-swap="innerHTML" hx-include="#lexicon-kind-input"
          hx-on::after-request="if(event.detail.successful) this.reset()">
        <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
            <label style="margin:0">词A</label><input type="text" name="canonical" required style="width:120px;margin:0;height:40px">
            <label style="margin:0">词B</label><input type="text" name="variants" required style="width:120px;margin:0;height:40px">
            <input type="text" name="distinguish" placeholder="区分说明(必填)" required style="width:280px;margin:0;height:40px">
            <button type="submit" class="secondary" style="height:40px;margin:0">➕ 添加</button>
        </div>
    </form>
    <div id="create-result"></div>

    <!-- CSV 导入（kind 空时按当前 Tab 兜底） -->
    <div style="margin:0.8rem 0;display:flex;gap:0.5rem;align-items:center">
        <label style="margin:0;white-space:nowrap">批量导入 CSV：</label>
        <form hx-post="/lexicon/import" hx-target="#import-result" hx-swap="innerHTML"
              hx-encoding="multipart/form-data" hx-include="#lexicon-kind-input" style="display:inline">
            <input type="file" name="file" accept=".csv" required style="font-size:0.8rem">
            <button type="submit" class="outline" style="font-size:0.8rem;height:32px">上传导入</button>
        </form>
    </div>
    <div id="import-result"></div>

    <!-- 列表：load/增删启禁(lexiconUpdated)后按当前 kind(hidden)自动重拉 -->
    <div id="lexicon-table" hx-get="/lexicon/list" hx-include="#lexicon-kind-input"
         hx-trigger="load, lexiconUpdated from:body" style="margin-top:0.6rem"></div>
</div>
```

> 关键联动说明：`#lexicon-table` 不依赖 Alpine，只用「Tab 按钮显式 hx-get + 列表容器监听 lexiconUpdated 时 hx-include `#lexicon-kind-input` 带当前 kind」。后端增删启禁后统一触发 `lexiconUpdated`（见路由 Step 3a 的 HX-Trigger / 行 partial 的 after-request），列表即按当前 Tab 刷新——与既有 synonyms 页「单一数据源刷新」模式一致，勿引入 Alpine 对列表的直接依赖。

`app/templates/partials/lexicon_list.html`（按 kind 用 columns）：

```html
{% if not rows %}
<p style="color:var(--pico-muted-color);font-size:0.85rem">当前类别暂无词条。</p>
{% else %}
<div style="overflow-x:auto">
<table style="font-size:0.85rem">
    <thead><tr>
        <th>{{ columns[0] }}</th><th>{{ columns[1] }}</th>
        {% if kind == 'confusable' %}<th>{{ columns[2] }}</th>{% endif %}
        <th>状态</th><th>操作</th>
    </tr></thead>
    <tbody hx-target="closest tr" hx-swap="outerHTML">
    {% for row in rows %}{% include "partials/lexicon_row.html" %}{% endfor %}
    </tbody>
</table>
</div>
{% endif %}
```

`app/templates/partials/lexicon_row.html`：

```html
<tr>
    <td>{{ row.canonical }}</td>
    <td>{{ row.variants }}</td>
    {% if kind == 'confusable' %}<td>{{ row.distinguish }}</td>{% endif %}
    <td>{{ '启用' if row.is_active else '禁用' }}</td>
    <td style="white-space:nowrap">
        <button class="outline" style="font-size:0.75rem;padding:0.1rem 0.4rem"
                hx-get="/lexicon/{{ row.id }}/edit" hx-target="closest tr" hx-swap="outerHTML">编辑</button>
        <button class="outline" style="font-size:0.75rem;padding:0.1rem 0.4rem"
                hx-post="/lexicon/{{ row.id }}/toggle"
                hx-on::after-request="document.body.dispatchEvent(new Event('lexiconUpdated'))">{{ '禁用' if row.is_active else '启用' }}</button>
        <button class="outline" style="font-size:0.75rem;padding:0.1rem 0.4rem;color:#b00000"
                hx-delete="/lexicon/{{ row.id }}"
                hx-confirm="确定删除该词条？">删除</button>
    </td>
</tr>
```

`app/templates/partials/lexicon_edit_row.html`：

```html
<tr>
    <td colspan="5">
        <form hx-post="/lexicon/{{ row.id }}/edit" hx-target="closest tr" hx-swap="outerHTML"
              hx-on::after-request="if(event.detail.successful) document.body.dispatchEvent(new Event('lexiconUpdated'))">
            {% if kind == 'confusable' %}
            <input type="text" name="canonical" value="{{ row.canonical }}" style="width:110px">
            <input type="text" name="variants" value="{{ row.variants }}" style="width:110px">
            <input type="text" name="distinguish" value="{{ row.distinguish }}" style="width:260px" required>
            {% else %}
            <input type="text" name="canonical" value="{{ row.canonical }}" style="width:150px">
            <input type="text" name="variants" value="{{ row.variants }}" style="width:220px" required>
            {% endif %}
            <button type="submit" class="secondary" style="font-size:0.75rem;padding:0.1rem 0.5rem">保存</button>
            <button type="button" class="outline" style="font-size:0.75rem;padding:0.1rem 0.5rem"
                    hx-get="/lexicon/list?kind={{ kind }}&active={{ row.is_active }}" hx-target="#lexicon-table" hx-swap="innerHTML">取消</button>
        </form>
    </td>
</tr>
```

`app/templates/partials/lexicon_import_result.html`：

```html
<p style="margin-top:0.5rem">✅ 成功 {{ ok }} 行 ｜ 跳过重复 {{ skip }} 行 ｜ 失败 {{ fail }} 行</p>
{% if fails %}<ul style="color:#b00000;font-size:0.8rem;margin:0.3rem 0">{% for f in fails %}<li>{{ f }}</li>{% endfor %}</ul>{% endif %}
```

- [ ] **Step 4a: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_lexicon_routes.py -v`
Expected: PASS（若 Tab kind 切换实现用了按钮 hx-get 直发，则列表随 kind；测试断言仅覆盖路由行为）

- [ ] **Step 1b: 收尾删除（先跑既有测试确认当前全绿基线）**

- [ ] **Step 2b: 实现收尾删除**

`app/main.py`：`from app.routes.lexicon_routes import router as lexicon_router; app.include_router(lexicon_router)`；删除 synonym_router import/include。

`app/templates/partials/tree_panel.html`：把 `🔤 同义词`(/synonyms) 按钮改为：

```html
        <a href="/lexicon" style="text-decoration:none">
            <button class="outline" style="width:100%;font-size:0.8rem">📖 词库</button>
        </a>
```

`app/database.py`：`SCHEMA_SQL` 删除 `synonym_map` CREATE 与 `idx_synonym_map_source` 两段；`init_db` 末尾（`_migrate_legacy_synonyms` 之后）追加幂等 DROP：

```python
        conn.execute("DROP TABLE IF EXISTS synonym_map")
        conn.execute("DROP INDEX IF EXISTS idx_synonym_map_source")
```

删除 `app/routes/synonym_routes.py`、`app/templates/synonyms.html`、`partials/synonyms_list.html`、`partials/synonyms_row.html`、`tests/test_synonym_routes.py`。

`app/classifier/rule_engine.py`：删除 Task 5 加的过渡 `clear_synonym_cache` 别名。

- [ ] **Step 3b: 收尾验收**

Run: `D:/Python/python.exe -m pytest tests/ -v`（全量）
Expected: 全绿。
Run: `grep -ri "synonym_map\|synonym_routes\|synonyms.html\|synonyms_list\|synonyms_row\|clear_synonym_cache" app/ tests/ scripts/`
Expected: 零命中。

- [ ] **Step 4b: 前端人工验证项（T-gap-4）**

本地启动（先按项目 CLAUDE.md 规范杀残留进程 → `D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`）→ 浏览器 Ctrl+F5：
1. 左栏「📖 词库」→ 三 Tab 切换、新增/编辑/启禁/删除、CSV 导入出报告；
2. 检索页输入「箍筋 钢筋」出现术语区分提示条；
3. QA 弹窗提问「箍筋和钢筋有何不同」回答上方出现警告块（x-text 无注入）。
完成后截图留档。

- [ ] **Step 5: Commit（8a）**

```bash
git add app/lexicon/validation.py app/routes/lexicon_routes.py app/templates/lexicon.html \
       app/templates/partials/lexicon_list.html app/templates/partials/lexicon_row.html \
       app/templates/partials/lexicon_edit_row.html app/templates/partials/lexicon_import_result.html \
       app/main.py app/templates/partials/tree_panel.html tests/test_lexicon_routes.py
git commit -m "feat: /lexicon 三 Tab 词库管理（CRUD/行内编辑/CSV 导入）+ 挂载"
```

```bash
git rm app/routes/synonym_routes.py app/templates/synonyms.html \
       app/templates/partials/synonyms_list.html app/templates/partials/synonyms_row.html \
       tests/test_synonym_routes.py
git add app/database.py app/classifier/rule_engine.py
git commit -m "refactor: 删除旧 synonym_map 全链（schema/路由/模板/测试/过渡别名），grep 零残留"
```

---

## Self-Review 记录

- **Spec 覆盖核对**：数据模型/唯一键/互含校验（T1、validation T8a）✓；store 缓存与一致性（T2）✓；expand 词条感知+退化委托+规范形（T3）✓；normalize/confusable 语义（T4）✓；rule_engine 切源（T5）✓；检索扩展接线+参数+缓存 key（T6）✓；confusable 检索页/QA/前端 x-text（T7）✓；/lexicon WebUI+CSV+收尾删除（T8）✓；测试矩阵含探针/重叠边界/CSV BOM/空态（分散于 T3/T6/T7/T8a）✓。
- **待办衔接**：synonym_map DROP 有意推迟到 T8（非 T1），避免 T1 后 synonym_routes 读到空表致 suite 变红——迁移非破坏、删除集中收尾，与 spec §6.1「init_db 幂等删除」的落点不同但行为等价，特此注明供 spec 读者对照。
- **风险**：Task 8 模板较大，kind 驱动表单/列表若遇 HTMX 联动问题（如列表不随 Tab 刷新），采用旧 synonyms 页既有的「按钮 hx-get 直发 + HX-Trigger 重载」模式（spec/记忆 htmx 契约坑），勿引入 Alpine 状态依赖。
