# 检索模块实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现混合检索（FTS5 全文 + LanceDB 向量语义），用户搜索关键词时自动融合精确匹配和语义相似结果。

**Architecture:** 新建 `hybrid_search.py` 作为编排层，并行调用已有的 `search_clauses()`（FTS5）和 `VectorStore.search()`（LanceDB），合并去重后（FTS5 命中排前、向量补充在后）分页返回。新建 `search_routes.py` 暴露 `/search` 和 `/clause/{id}` 两个 HTTP 端点。

**Tech Stack:** FastAPI, SQLite FTS5, LanceDB, BGE-small-zh-v1.5 (sentence-transformers), Jinja2 (Starlette 1.3.1)

## Global Constraints

- Python 3.14.6, FastAPI, SQLite FTS5, LanceDB
- Starlette 1.3.1 TemplateResponse API: `TemplateResponse(request, name, context)`
- TDD 流程：先写测试 → 确认失败 → 实现 → 确认通过 → commit
- 路由遵循现有模式：`APIRouter()` 无 prefix/tags
- 模板路径：`app/templates/partials/`
- DB 连接：`with get_db() as conn:`
- 认证：AuthMiddleware 检查 `access_token` cookie，公开路径 `/login`, `/static`, `/health`
- 测试：`tmp_path + monkeypatch + init_db()` + `auth_client` fixture
- 每个 Task 完成后 commit

---

### Task 1: 混合搜索引擎 `app/search/hybrid_search.py`

**Files:**
- Create: `app/search/hybrid_search.py`
- Create: `tests/test_hybrid_search.py`

**Interfaces:**
- Consumes: `search_clauses(query: SearchQuery) -> tuple[list[dict], int]` (from `app.search.sql_search`), `VectorStore().search(query_text, top_k) -> list[dict]` (from `app.search.vector_search`), `get_db()` (from `app.database`)
- Produces: `hybrid_search(query: SearchQuery) -> tuple[list[dict], int]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hybrid_search.py
"""混合搜索测试"""
import pytest
from app.models import SearchQuery
from app.database import init_db, get_db


def setup_search_data(conn):
    """写入 3 条测试条文"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。",
         "结构专业", "主体结构", "模板工程"),
        ("5.2.1", "钢筋原材料", "钢筋进场时应抽取试件作屈服强度检验。",
         "结构专业", "主体结构", "金属材料,钢筋"),
        ("6.1.1", "屋面防水", "屋面防水层应采用卷材或涂膜防水。",
         "建筑专业", "屋面", "防水材料"),
    ]
    for no, title, content, dim4, dim5, dim6 in clauses_data:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content,
               dim4_specialty, dim5_location, dim6_material)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6),
        )


def test_hybrid_search_keyword(monkeypatch, tmp_path):
    """FTS5 关键词搜索正常工作"""
    db_path = tmp_path / "test_hybrid.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋"))

    assert total >= 1
    assert any("钢筋" in r["content"] for r in results)


def test_hybrid_search_dimension_filter(monkeypatch, tmp_path):
    """维度筛选正常工作"""
    db_path = tmp_path / "test_hybrid_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(dim5_location="屋面"))

    assert total >= 1
    assert results[0]["dim5_location"] == "屋面"


def test_hybrid_search_no_keyword(monkeypatch, tmp_path):
    """无关键词时返回全部结果（维度筛选依然生效）"""
    db_path = tmp_path / "test_hybrid_all.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery())

    assert total == 3


def test_hybrid_search_pagination(monkeypatch, tmp_path):
    """分页参数生效"""
    db_path = tmp_path / "test_hybrid_page.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(page=1, per_page=2))

    assert len(results) <= 2
    assert total == 3


def test_hybrid_search_no_results(monkeypatch, tmp_path):
    """无匹配时返回空列表"""
    db_path = tmp_path / "test_hybrid_none.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="zzz不存在的关键词zzz"))

    assert total == 0
    assert results == []


def test_hybrid_search_fts5_special_chars(monkeypatch, tmp_path):
    """FTS5 特殊字符被安全处理"""
    db_path = tmp_path / "test_hybrid_special.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    # 含 FTS5 特殊字符 * " ( ) 的查询不应报错
    results, total = hybrid_search(SearchQuery(keyword="钢筋* (测试)"))
    # 不应抛出异常，正常返回
    assert isinstance(results, list)
    assert isinstance(total, int)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:/CC Workspace/construction-spec-query-v2"
D:/Python/python.exe -m pytest tests/test_hybrid_search.py -v
```

预期：`ModuleNotFoundError: No module named 'app.search.hybrid_search'`

- [ ] **Step 3: 实现 hybrid_search**

```python
# app/search/hybrid_search.py
"""混合搜索编排：FTS5 全文 + LanceDB 向量语义"""
import re
from app.models import SearchQuery
from app.database import get_db


# FTS5 语法特殊字符（中文工程术语中不会出现，直接移除）
_FTS5_SPECIAL = re.compile(r'[*"(){}\[\]^~:+\-=/,]')


def _sanitize_fts5(keyword: str) -> str:
    """移除 FTS5 特殊字符，避免语法错误"""
    return _FTS5_SPECIAL.sub(' ', keyword).strip()


def hybrid_search(query: SearchQuery) -> tuple[list[dict], int]:
    """混合搜索：FTS5 精确命中 + 向量语义补充，合并去重后分页"""
    from app.search.sql_search import search_clauses  # 延迟导入避免循环依赖

    keyword = (query.keyword or "").strip()

    # ── 1. FTS5 搜索（获取全部结果，不做分页） ──
    # 清理 FTS5 特殊字符
    clean_keyword = _sanitize_fts5(keyword) if keyword else ""
    fts5_query = query.model_copy()
    fts5_query.keyword = clean_keyword
    fts5_query.page = 1
    fts5_query.per_page = 10000  # 先取全部，在合并后统一分页

    fts5_results, fts5_total = search_clauses(fts5_query)

    # ── 2. 向量搜索（语义匹配） ──
    vector_raw = []
    if clean_keyword:
        try:
            from app.search.vector_search import VectorStore
            vs = VectorStore()
            vector_raw = vs.search(clean_keyword, top_k=50)
        except Exception:
            pass  # 向量搜索不可用时静默降级

    # ── 3. 合并去重 ──
    fts5_ids = {r["id"] for r in fts5_results}
    new_ids = [v["clause_id"] for v in vector_raw if v["clause_id"] not in fts5_ids]

    vector_results = []
    if new_ids:
        with get_db() as conn:
            placeholders = ",".join("?" * len(new_ids))
            rows = conn.execute(
                f"""SELECT c.*, s.code as spec_code, s.title as spec_title
                    FROM clauses c
                    JOIN specifications s ON c.spec_id = s.id
                    WHERE c.id IN ({placeholders})""",
                new_ids,
            ).fetchall()
            seen = set()
            for r in rows:
                d = dict(r)
                if d["id"] not in seen:
                    seen.add(d["id"])
                    d["_source"] = "semantic"
                    vector_results.append(d)

    merged = list(fts5_results) + vector_results
    total = len(merged)

    # ── 4. 分页 ──
    per_page = min(query.per_page or 20, 100)
    page = max(query.page or 1, 1)
    start = (page - 1) * per_page
    end = start + per_page

    return merged[start:end], total
```

- [ ] **Step 4: 运行测试确认通过**

```bash
D:/Python/python.exe -m pytest tests/test_hybrid_search.py -v
```

预期：6 passed

- [ ] **Step 5: 运行全量测试确保无回归**

```bash
D:/Python/python.exe -m pytest tests/ -v
```

预期：86 passed（原有 80 + 新增 6）

- [ ] **Step 6: Commit**

```bash
git add app/search/hybrid_search.py tests/test_hybrid_search.py
git commit -m "feat: 混合搜索引擎 — FTS5 + LanceDB 向量并行搜索合并去重"
```

---

### Task 2: 条文详情模板 `app/templates/partials/clause_detail.html`

**Files:**
- Create: `app/templates/partials/clause_detail.html`

**Interfaces:**
- Consumes: 模板变量 `clause` (dict with id, clause_no, title, content, spec_code, spec_title, dim4_specialty, dim5_location, dim6_material)
- Produces: HTML 片段，在 `#clause-detail` 容器中展示

- [ ] **Step 1: 创建模板**

```html
<!-- app/templates/partials/clause_detail.html -->
<div id="clause-detail">
    {% if clause %}
    <article>
        <header>
            <h4>{{ clause.spec_code }} — {{ clause.clause_no }}</h4>
            {% if clause.title %}<p><strong>{{ clause.title }}</strong></p>{% endif %}
        </header>
        <div class="clause-body" style="white-space:pre-wrap;line-height:1.8">
            {{ clause.content }}
        </div>
        <footer style="margin-top:1rem">
            <div class="dim-tags">
                {% if clause.dim4_specialty %}
                <span class="dim-tag" style="background:var(--pico-primary-background);color:white;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.8rem">
                    专业：{{ clause.dim4_specialty }}
                </span>
                {% endif %}
                {% if clause.dim5_location %}
                <span class="dim-tag" style="background:var(--pico-primary-background);color:white;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.8rem">
                    部位：{{ clause.dim5_location }}
                </span>
                {% endif %}
                {% if clause.dim6_material %}
                <span class="dim-tag" style="background:var(--pico-primary-background);color:white;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.8rem">
                    材料/工艺：{{ clause.dim6_material }}
                </span>
                {% endif %}
            </div>
            <small style="color:var(--pico-muted-color)">
                来源：{{ clause.spec_title or clause.spec_code }}
            </small>
        </footer>
    </article>
    {% else %}
    <p style="color:var(--pico-muted-color)">条文不存在</p>
    {% endif %}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add app/templates/partials/clause_detail.html
git commit -m "feat: 条文详情模板"
```

---

### Task 3: 搜索路由 + 注册 `app/routes/search_routes.py`

**Files:**
- Create: `app/routes/search_routes.py`
- Create: `tests/test_search_routes.py`
- Modify: `app/main.py` — 注册 search_router

**Interfaces:**
- Consumes: `hybrid_search(query) -> tuple[list[dict], int]`, `templates.TemplateResponse()`, `get_db()`
- Produces: `GET /search` (HTML partial), `GET /clause/{clause_id}` (HTML partial)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_search_routes.py
"""搜索路由 HTTP 集成测试"""
import pytest


def setup_search_data(conn):
    """写入测试数据"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。",
         "结构专业", "主体结构", "模板工程"),
        ("5.2.1", "钢筋原材料", "钢筋进场时应抽取试件作屈服强度检验。",
         "结构专业", "主体结构", "金属材料,钢筋"),
        ("6.1.1", "屋面防水", "屋面防水层应采用卷材或涂膜防水。",
         "建筑专业", "屋面", "防水材料"),
    ]
    for no, title, content, dim4, dim5, dim6 in clauses_data:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content,
               dim4_specialty, dim5_location, dim6_material)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6),
        )


# ── /search 端点测试 ──

def test_search_keyword_returns_html(auth_client, monkeypatch, tmp_path):
    """关键词搜索返回 HTML"""
    db_path = tmp_path / "test_search_kw.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=钢筋")
    assert resp.status_code == 200
    assert "钢筋" in resp.text


def test_search_dimension_filter(auth_client, monkeypatch, tmp_path):
    """维度筛选返回正确结果"""
    db_path = tmp_path / "test_search_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?dim5_location=屋面")
    assert resp.status_code == 200
    assert "屋面防水" in resp.text


def test_search_combined(auth_client, monkeypatch, tmp_path):
    """关键词 + 维度组合筛选"""
    db_path = tmp_path / "test_search_comb.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=防水&dim4_specialty=建筑专业")
    assert resp.status_code == 200
    assert "防水" in resp.text


def test_search_pagination(auth_client, monkeypatch, tmp_path):
    """分页参数生效"""
    db_path = tmp_path / "test_search_page.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?page=1&page_size=2")
    assert resp.status_code == 200
    html = resp.text
    # 3 条数据，每页 2 条 → 应有分页控件
    assert "下一页" in html or "共找到" in html


def test_search_no_keyword(auth_client, monkeypatch, tmp_path):
    """无参数时返回全部结果"""
    db_path = tmp_path / "test_search_all.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search")
    assert resp.status_code == 200
    assert "共找到" in resp.text


def test_search_no_results(auth_client, monkeypatch, tmp_path):
    """无匹配时显示空结果提示"""
    db_path = tmp_path / "test_search_none.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=abcdefg不存在的")
    assert resp.status_code == 200
    # 应该包含空结果提示
    assert "暂无" in resp.text or "0 条" in resp.text


def test_search_requires_auth(client):
    """未登录不能访问搜索接口"""
    resp = client.get("/search?keyword=test", follow_redirects=False)
    assert resp.status_code == 302


# ── /clause/{id} 端点测试 ──

def test_clause_detail_returns_html(auth_client, monkeypatch, tmp_path):
    """条文详情返回 HTML"""
    db_path = tmp_path / "test_clause.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
        clause_id = conn.execute(
            "SELECT id FROM clauses WHERE clause_no = '5.1.1'"
        ).fetchone()[0]

    resp = auth_client.get(f"/clause/{clause_id}")
    assert resp.status_code == 200
    assert "模板设计" in resp.text
    assert "GB 50204" in resp.text


def test_clause_detail_not_found(auth_client, monkeypatch, tmp_path):
    """不存在的条文返回 404"""
    db_path = tmp_path / "test_clause_nf.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.get("/clause/99999")
    assert resp.status_code == 404


def test_clause_detail_requires_auth(client):
    """未登录不能访问条文详情"""
    resp = client.get("/clause/1", follow_redirects=False)
    assert resp.status_code == 302
```

- [ ] **Step 2: 运行测试确认失败**

```bash
D:/Python/python.exe -m pytest tests/test_search_routes.py -v
```

预期：全部 FAIL（路由不存在，返回 404）

- [ ] **Step 3: 实现搜索路由**

```python
# app/routes/search_routes.py
"""搜索路由"""
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from app.models import SearchQuery
from app.database import get_db

router = APIRouter()


@router.get("/search")
async def search(
    request: Request,
    keyword: str = Query(""),
    dim1_hierarchy: str = Query(""),
    dim1_nature: str = Query(""),
    dim2_stage: str = Query(""),
    dim3_usage: str = Query(""),
    dim4_specialty: str = Query(""),
    dim5_location: str = Query(""),
    dim6_material: str = Query(""),
    page: int = Query(1),
    page_size: int = Query(20),
):
    """混合搜索：关键词 + 六维筛选 + 分页"""
    from app.search.hybrid_search import hybrid_search

    sq = SearchQuery(
        keyword=keyword,
        dim1_hierarchy=dim1_hierarchy,
        dim1_nature=dim1_nature,
        dim2_stage=dim2_stage,
        dim3_usage=dim3_usage,
        dim4_specialty=dim4_specialty,
        dim5_location=dim5_location,
        dim6_material=dim6_material,
        page=page,
        per_page=min(page_size, 100),
    )

    results, total = hybrid_search(sq)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/result_list.html", {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "keyword": keyword,
        "dim4_specialty": dim4_specialty,
        "dim5_location": dim5_location,
        "dim6_material": dim6_material,
    })


@router.get("/clause/{clause_id}")
async def clause_detail(request: Request, clause_id: int):
    """条文详情"""
    with get_db() as conn:
        clause = conn.execute(
            """SELECT c.*, s.code as spec_code, s.title as spec_title
               FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               WHERE c.id = ?""",
            (clause_id,),
        ).fetchone()

    if not clause:
        return JSONResponse({"detail": "条文不存在"}, status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clause_detail.html", {
        "clause": dict(clause),
    })
```

- [ ] **Step 4: 注册路由 — 修改 `app/main.py`**

在 `app/main.py` 第 47 行 `app.include_router(rules_router)` 之后添加：

```python
from app.routes.search_routes import router as search_router
app.include_router(search_router)
```

即完整修改为：

```python
# app/main.py 第 46-47 行后追加
from app.routes.search_routes import router as search_router
app.include_router(search_router)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
D:/Python/python.exe -m pytest tests/test_search_routes.py -v
```

预期：11 passed

- [ ] **Step 6: 运行全量测试**

```bash
D:/Python/python.exe -m pytest tests/ -v
```

预期：97 passed（80 原有 + 6 hybrid + 11 routes）

- [ ] **Step 7: Commit**

```bash
git add app/routes/search_routes.py tests/test_search_routes.py app/main.py
git commit -m "feat: 搜索路由 — GET /search 混合搜索 + GET /clause/{id} 条文详情"
```

---

### 最终验证

```bash
# 1. 重启服务器
taskkill //F //IM python.exe 2>/dev/null
cd "D:/CC Workspace/construction-spec-query-v2"
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &

# 2. 浏览器测试
# - 搜索框输入"混凝土" → 应返回精确匹配 + 语义相关结果
# - 搜索"砼" → 语义搜索应返回混凝土相关内容
# - 点击分类树节点 → 按维度筛选
# - 点击搜索结果项 → 右侧显示条文详情
```
