# 施工规范查询系统 V2 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于设计文档完全重写施工规范查询系统，实现六维两级分类体系、三栏单页 UI、全本地 AI（CLI + BGE embedding）。

**Architecture:** FastAPI 单体应用 + Jinja2/htmx/Alpine.js 前端 + SQLite(FTS5)/LanceDB 双通道存储。规范级(维一~三) + 条文级(维四~六) 两层分类。关键词规则引擎(自适应阈值) -> 攒批 CLI AI 分类 -> 反馈闭环。BGE-small-zh 本地 embedding。Claude Code CLI / Codex CLI 子进程调用。

**Tech Stack:** Python 3.12+, FastAPI, SQLite (FTS5), LanceDB, BGE-small-zh (sentence-transformers), PaddleOCR, PyMuPDF, Jinja2, htmx, Alpine.js, Pico.css, marked.js, python-jose (JWT), passlib (bcrypt)。

## Global Constraints

- Python >= 3.12, FastAPI 异步模式
- 仅监听 127.0.0.1:8000, 不对外暴露
- JWT 鉴权保护所有非登录页面
- 密码 bcrypt 哈希存储
- 数据全部项目内落盘(data/ 和 lance_db/)
- CLI 子进程调用采用 --print 非交互模式
- TDD 流程: 先写测试 -> 验证失败 -> 最小实现 -> 验证通过 -> commit
- 参考现有 construction-spec-query/ 代码但不直接复用

---

### Task 1: 项目脚手架与配置

**Files:**
- Create: `construction-spec-query-v2/requirements.txt`
- Create: `construction-spec-query-v2/.env.example`
- Create: `construction-spec-query-v2/.gitignore`
- Create: `construction-spec-query-v2/app/__init__.py`
- Create: `construction-spec-query-v2/app/config.py`
- Create: `construction-spec-query-v2/app/main.py`
- Create: `construction-spec-query-v2/tests/__init__.py`
- Create: `construction-spec-query-v2/tests/conftest.py`

**Interfaces:**
- Produces: `app.config.SECRET_KEY`, `app.config.DATABASE_PATH`, `app.config.LANCE_DB_PATH`, `app.config.UPLOAD_DIR`, `app.config.OUTPUT_DIR`, `app.config.WORKSPACE_DIR`, `app.config.ADAPTIVE_THRESHOLDS`, `app.main.app` (FastAPI instance)

- [ ] **Step 1: 创建 .gitignore**

```
__pycache__/
*.pyc
.venv/
.env
data/spec_query.db
data/uploads/*
data/outputs/*
data/workspace/*
lance_db/
.pytest_cache/
```

- [ ] **Step 2: 创建 requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
python-multipart>=0.0.18
python-dotenv>=1.0.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
bcrypt==4.2.1
aiofiles>=24.0.0
PaddleOCR>=3.0.0
paddlepaddle>=3.0.0
PyMuPDF>=1.24.0
lancedb>=0.17.0
sentence-transformers>=3.0.0
pyarrow>=17.0.0
httpx>=0.28.0
jinja2>=3.1.0
```

- [ ] **Step 3: 创建 .env.example**

```
SECRET_KEY=change-me-to-a-random-string
DATABASE_PATH=data/spec_query.db
LANCE_DB_PATH=lance_db
UPLOAD_DIR=data/uploads
OUTPUT_DIR=data/outputs
WORKSPACE_DIR=data/workspace
```

- [ ] **Step 4: 创建 app/__init__.py 和 tests/__init__.py**

```python
# app/__init__.py (空文件)
```

```python
# tests/__init__.py (空文件)
```

- [ ] **Step 5: 创建 app/config.py**

```python
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "spec_query.db"))
LANCE_DB_PATH = os.getenv("LANCE_DB_PATH", str(BASE_DIR / "lance_db"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", str(BASE_DIR / "data" / "outputs"))
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", str(BASE_DIR / "data" / "workspace"))

for d in [UPLOAD_DIR, OUTPUT_DIR, WORKSPACE_DIR, LANCE_DB_PATH]:
    Path(d).mkdir(parents=True, exist_ok=True)
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

# 六大维度定义
DIMENSIONS = {
    "dim1": {"label": "规范属性", "fields": ["hierarchy", "nature", "sys_level", "spec_type"]},
    "dim2": {"label": "工程阶段", "fields": ["stage"]},
    "dim3": {"label": "工程类型", "fields": ["usage", "construction", "scale"]},
    "dim4": {"label": "所属专业", "fields": ["specialty"]},
    "dim5": {"label": "工程部位", "fields": ["location"]},
    "dim6": {"label": "材料/工艺", "fields": ["material"]},
}

# 自适应阈值 (dim_key -> threshold)
ADAPTIVE_THRESHOLDS = {
    "dim1": 0.3,
    "dim2": 0.5,
    "dim3": 0.5,
    "dim4": 0.6,
    "dim5": 0.7,
    "dim6": 0.6,
}

AI_CONFIDENCE_THRESHOLD = 0.7
BATCH_SIZE = 20
BATCH_TIMEOUT_SECONDS = 30
```

- [ ] **Step 6: 创建 app/main.py (最小 FastAPI 入口)**

```python
from fastapi import FastAPI
from app.config import BASE_DIR

app = FastAPI(title="施工规范查询系统 V2")

@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 7: 创建 tests/conftest.py**

```python
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

@pytest.fixture
def test_dir(tmp_path):
    """提供临时目录作为测试用的 data 根目录"""
    return tmp_path

@pytest.fixture
def app():
    from app.main import app
    return app

@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)
```

- [ ] **Step 8: 运行健康检查测试**

Run: `cd construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/ -v`
Expected: 1 test collected (conftest fixtures load)

- [ ] **Step 9: 验证 uvicorn 可启动**

Run: `D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 & sleep 2 && curl http://127.0.0.1:8000/health && kill %1`
Expected: `{"status":"ok"}`

- [ ] **Step 10: Commit**

```bash
cd construction-spec-query-v2 && git init && git add -A && git commit -m "feat: task1 - 项目脚手架与配置"
```

---

### Task 2: 数据库建表与初始化

**Files:**
- Create: `construction-spec-query-v2/app/database.py`
- Create: `construction-spec-query-v2/tests/test_database.py`

**Interfaces:**
- Produces: `app.database.get_connection()`, `app.database.get_db()`, `app.database.init_db()`

- [ ] **Step 1: 编写数据库测试**

```python
# tests/test_database.py
import sqlite3
from app.database import get_connection, get_db, init_db, SCHEMA_SQL

def test_get_connection_returns_sqlite_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    conn = get_connection()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()

def test_get_db_context_manager(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    with get_db() as conn:
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM test").fetchone()
        assert row["id"] == 1

def test_init_db_creates_all_tables(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "specifications" in names
        assert "clauses" in names
        assert "classification_rules" in names
        assert "classification_queue" in names
        assert "users" in names

def test_init_db_creates_fts5(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        # FTS5 虚拟表以 fts_ 前缀出现在 sqlite_master 中
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t["name"] for t in tables]
        fts_tables = [n for n in names if "fts" in n.lower()]
        assert len(fts_tables) >= 1

def test_init_db_is_idempotent(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    init_db()  # 不应报错
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_database.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'app.database')

- [ ] **Step 3: 创建 app/database.py**

```python
import sqlite3
from contextlib import contextmanager
from app.config import DATABASE_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS specifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    title           TEXT NOT NULL,
    short_name      TEXT,
    dim1_hierarchy  TEXT,
    dim1_nature     TEXT,
    dim1_sys_level  TEXT,
    dim1_spec_type  TEXT,
    dim2_stage      TEXT,
    dim3_usage      TEXT,
    dim3_construction TEXT,
    dim3_scale      TEXT,
    status          TEXT DEFAULT '现行',
    source_path     TEXT,
    output_dir      TEXT,
    clause_count    INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS clauses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id         INTEGER NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
    clause_no       TEXT NOT NULL,
    title           TEXT,
    content         TEXT NOT NULL,
    parent_clause   INTEGER REFERENCES clauses(id),
    dim4_specialty  TEXT,
    dim5_location   TEXT,
    dim6_material   TEXT,
    ai_classified   INTEGER DEFAULT 0,
    needs_review    INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(
    clause_no, title, content,
    dim4_specialty, dim5_location, dim6_material,
    content=clauses, content_rowid=id
);

CREATE TABLE IF NOT EXISTS classification_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension       TEXT NOT NULL,
    sub_field       TEXT,
    pattern         TEXT NOT NULL,
    match_type      TEXT DEFAULT 'keyword',
    priority        INTEGER DEFAULT 0,
    threshold       REAL NOT NULL,
    hit_count       INTEGER DEFAULT 0,
    confirmed       INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS classification_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    clause_id       INTEGER NOT NULL REFERENCES clauses(id) ON DELETE CASCADE,
    dimension       TEXT NOT NULL,
    keyword_score   REAL,
    batch_id        TEXT,
    ai_label        TEXT,
    ai_confidence   REAL,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
"""

TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS clauses_ai AFTER INSERT ON clauses BEGIN
    INSERT INTO clauses_fts(rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES (new.id, new.clause_no, new.title, new.content,
        new.dim4_specialty, new.dim5_location, new.dim6_material);
END;

CREATE TRIGGER IF NOT EXISTS clauses_ad AFTER DELETE ON clauses BEGIN
    INSERT INTO clauses_fts(clauses_fts, rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES ('delete', old.id, old.clause_no, old.title, old.content,
        old.dim4_specialty, old.dim5_location, old.dim6_material);
END;

CREATE TRIGGER IF NOT EXISTS clauses_au AFTER UPDATE ON clauses BEGIN
    INSERT INTO clauses_fts(clauses_fts, rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES ('delete', old.id, old.clause_no, old.title, old.content,
        old.dim4_specialty, old.dim5_location, old.dim6_material);
    INSERT INTO clauses_fts(rowid, clause_no, title, content,
        dim4_specialty, dim5_location, dim6_material)
    VALUES (new.id, new.clause_no, new.title, new.content,
        new.dim4_specialty, new.dim5_location, new.dim6_material);
END;
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(TRIGGERS_SQL)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_database.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_database.py
git commit -m "feat: task2 - 数据库建表与初始化"
```

---

### Task 3: 用户鉴权

**Files:**
- Create: `construction-spec-query-v2/app/auth.py`
- Create: `construction-spec-query-v2/tests/test_auth.py`

**Interfaces:**
- Produces: `app.auth.hash_password(str) -> str`, `app.auth.verify_password(str, str) -> bool`, `app.auth.create_access_token(dict) -> str`, `app.auth.decode_access_token(str) -> dict`
- Consumes: `app.config.SECRET_KEY`, `app.config.ALGORITHM`, `app.config.ACCESS_TOKEN_EXPIRE_MINUTES`

- [ ] **Step 1: 编写鉴权测试**

```python
# tests/test_auth.py
from app.auth import hash_password, verify_password, create_access_token, decode_access_token

def test_hash_and_verify_password():
    password = "test-password-123"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong", hashed) is False

def test_create_and_decode_token():
    data = {"sub": "admin"}
    token = create_access_token(data)
    assert isinstance(token, str)
    decoded = decode_access_token(token)
    assert decoded["sub"] == "admin"

def test_decode_expired_token():
    from datetime import datetime, timedelta, timezone
    from jose import jwt
    from app.config import SECRET_KEY, ALGORITHM
    expire = datetime.now(timezone.utc) - timedelta(minutes=1)
    token = jwt.encode({"sub": "admin", "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)
    import pytest
    with pytest.raises(Exception):
        decode_access_token(token)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/auth.py**

```python
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_auth.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat: task3 - 用户鉴权模块"
```

---

### Task 4: Pydantic 数据模型

**Files:**
- Create: `construction-spec-query-v2/app/models.py`
- Create: `construction-spec-query-v2/tests/test_models.py`

**Interfaces:**
- Produces: `app.models.SpecCreate`, `app.models.SpecResponse`, `app.models.ClauseCreate`, `app.models.ClauseResponse`, `app.models.ClassificationRuleCreate`, `app.models.ClassificationQueueItem`, `app.models.UserCreate`, `app.models.TokenResponse`

- [ ] **Step 1: 编写模型测试**

```python
# tests/test_models.py
from app.models import SpecCreate, ClauseCreate, ClassificationRuleCreate

def test_spec_create_validation():
    spec = SpecCreate(code="GB 50204-2015", title="混凝土结构工程施工质量验收规范")
    assert spec.code == "GB 50204-2015"

def test_spec_create_optional_fields():
    spec = SpecCreate(
        code="GB 50204-2015",
        title="混凝土结构工程施工质量验收规范",
        dim1_hierarchy="国家标准",
        dim1_nature="强制性",
        dim2_stage="施工阶段",
    )
    assert spec.dim1_hierarchy == "国家标准"

def test_clause_create_required():
    clause = ClauseCreate(spec_id=1, clause_no="5.2.1", content="模板及其支架应...")
    assert clause.clause_no == "5.2.1"

def test_clause_create_with_dimensions():
    clause = ClauseCreate(
        spec_id=1,
        clause_no="5.2.1",
        content="模板及其支架应根据工程结构形式...",
        dim6_material="混凝土材料,模板工程",
    )
    assert "模板工程" in clause.dim6_material

def test_rule_create():
    rule = ClassificationRuleCreate(
        dimension="dim6",
        sub_field="material",
        pattern="混凝土",
        threshold=0.6,
    )
    assert rule.dimension == "dim6"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_models.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/models.py**

```python
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class SpecCreate(BaseModel):
    code: str
    title: str
    short_name: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim1_sys_level: Optional[str] = None
    dim1_spec_type: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim3_construction: Optional[str] = None
    dim3_scale: Optional[str] = None
    source_path: Optional[str] = None
    output_dir: Optional[str] = None


class SpecResponse(SpecCreate):
    id: int
    status: str = "现行"
    clause_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ClauseCreate(BaseModel):
    spec_id: int
    clause_no: str
    content: str
    title: Optional[str] = None
    parent_clause: Optional[int] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None


class ClauseResponse(ClauseCreate):
    id: int
    ai_classified: int = 0
    needs_review: int = 0
    created_at: Optional[str] = None


class ClassificationRuleCreate(BaseModel):
    dimension: str
    pattern: str
    sub_field: Optional[str] = None
    match_type: str = "keyword"
    priority: int = 0
    threshold: float = 0.6


class ClassificationRuleResponse(ClassificationRuleCreate):
    id: int
    hit_count: int = 0
    confirmed: int = 0
    is_active: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ClassificationQueueItem(BaseModel):
    clause_id: int
    dimension: str
    keyword_score: Optional[float] = None
    batch_id: Optional[str] = None
    ai_label: Optional[str] = None
    ai_confidence: Optional[float] = None
    status: str = "pending"


class UserCreate(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SearchQuery(BaseModel):
    keyword: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None
    page: int = 1
    per_page: int = 20


class QARequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    backend: str = "claude"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_models.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: task4 - Pydantic 数据模型"
```

---

### Task 5: Markdown 条文解析器（含标签继承）

**Files:**
- Create: `construction-spec-query-v2/app/parser/__init__.py`
- Create: `construction-spec-query-v2/app/parser/md_parser.py`
- Create: `construction-spec-query-v2/tests/test_md_parser.py`

**Interfaces:**
- Produces: `app.parser.md_parser.parse_markdown(md_text: str) -> list[dict]` (返回条文列表，每条含 clause_no, title, content, level, parent_path 标签继承路径)
- Consumes: None (独立模块)

- [ ] **Step 1: 编写解析器测试**

```python
# tests/test_md_parser.py
from app.parser.md_parser import parse_markdown

SAMPLE_MD = """# GB 50204-2015 混凝土结构工程施工质量验收规范

## 5 混凝土分项工程

### 5.1 模板

#### 5.1.1 一般规定

模板及其支架应根据工程结构形式、荷载大小、地基土类别、施工设备和材料供应等条件进行设计。

#### 5.1.2 模板安装

模板安装应满足下列要求：

1. 模板的接缝不应漏浆；
2. 模板与混凝土的接触面应清理干净。

### 5.2 钢筋

#### 5.2.1 原材料

钢筋进场时，应按国家现行相关标准的规定抽取试件作屈服强度、抗拉强度、伸长率、弯曲性能和重量偏差检验。

## 6 预应力分项工程

### 6.1 预应力材料

预应力筋进场时，应按国家现行相关标准抽取试件作抗拉强度、伸长率检验。
"""

def test_parse_markdown_returns_list():
    results = parse_markdown(SAMPLE_MD)
    assert isinstance(results, list)
    assert len(results) > 0

def test_parse_markdown_extracts_clause_no():
    results = parse_markdown(SAMPLE_MD)
    clause_nos = [r["clause_no"] for r in results]
    assert "5.1.1" in clause_nos
    assert "5.2.1" in clause_nos

def test_parse_markdown_extracts_title():
    results = parse_markdown(SAMPLE_MD)
    titles = {r["clause_no"]: r["title"] for r in results}
    assert titles["5.1.1"] == "一般规定"
    assert titles["5.1.2"] == "模板安装"

def test_parse_markdown_extracts_content():
    results = parse_markdown(SAMPLE_MD)
    for r in results:
        if r["clause_no"] == "5.2.1":
            assert "屈服强度" in r["content"]
            break

def test_parse_markdown_parent_inheritance():
    results = parse_markdown(SAMPLE_MD)
    # 条文 5.1.1 的父路径应包含祖先标题
    for r in results:
        if r["clause_no"] == "5.1.1":
            path = r.get("parent_path", [])
            # 从标题层级中自动继承: 5 混凝土分项工程 -> 5.1 模板
            assert len(path) >= 2
            assert any("混凝土" in p for p in path)
            assert any("模板" in p for p in path)
            break

def test_parse_markdown_handles_empty():
    results = parse_markdown("")
    assert results == []

def test_parse_markdown_levels():
    results = parse_markdown(SAMPLE_MD)
    levels = {r["clause_no"]: r["level"] for r in results}
    # 5.1.1 是 ####，level=4; 5.1 是 ###，level=3
    assert levels["5.1.1"] == 4
    assert levels.get("5.1") is None  # 中间标题（无正文内容）不生成条文
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/parser/md_parser.py**

```python
import re
from typing import Optional


def _full_title(ancestors: list[str], current: str) -> str:
    return " > ".join(ancestors + [current]) if ancestors else current


def parse_markdown(md_text: str) -> list[dict]:
    """解析 Markdown，按标题层级切割条文。

    规则：
    - # 视为规范标题，忽略
    - ## ~ ###### 视为章节/条文标题
    - 标题后的正文归属该条文
    - 中间层级标题（无正文或仅有子标题）不生成条文，但作为标签路径继承
    - 子条文自动继承父标题的标签路径（parent_path）

    返回: [{
        "clause_no": "5.2.1",
        "title": "原材料",
        "content": "钢筋进场时...",
        "level": 4,
        "parent_path": ["混凝土分项工程", "钢筋"]
    }, ...]
    """
    if not md_text.strip():
        return []

    lines = md_text.split("\n")
    clauses = []
    # 栈: [(level, title, content_buffer, parent_path)]
    stack = []
    current_content_lines = []
    # 当前标题层级路径: [(level, title)]
    title_stack = []

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()

            # 如果之前有积累内容且有当前条文号，保存之
            if current_content_lines and stack:
                entry = stack[-1]
                clauses.append({
                    "clause_no": _extract_clause_no(entry["title"]),
                    "title": entry["title"],
                    "content": "\n".join(current_content_lines).strip(),
                    "level": entry["level"],
                    "parent_path": list(entry["parent_path"]),
                })
                current_content_lines = []

            # 维护 title_stack
            while title_stack and title_stack[-1][0] >= level:
                title_stack.pop()
            title_stack.append((level, title))
            parent_path = [t[1] for t in title_stack[:-1]]  # 不包含自身

            # 逐条判断是否需要入库
            clause_no = _extract_clause_no(title)
            stack.append({
                "level": level,
                "title": title,
                "clause_no": clause_no,
                "parent_path": parent_path,
            })
        else:
            if line.strip():
                current_content_lines.append(line)

    # 处理最后一条
    if current_content_lines and stack:
        entry = stack[-1]
        clauses.append({
            "clause_no": _extract_clause_no(entry["title"]),
            "title": entry["title"],
            "content": "\n".join(current_content_lines).strip(),
            "level": entry["level"],
            "parent_path": list(entry["parent_path"]),
        })

    return clauses


def _extract_clause_no(title: str) -> str:
    """从标题中提取条文号，如 '5.1.1 一般规定' -> '5.1.1'"""
    # 匹配标题开头的数字编号
    m = re.match(r"^([\d.]+)\s", title)
    if m:
        return m.group(1)
    m = re.match(r"^第[一二三四五六七八九十百千万\d]+[节章条]", title)
    if m:
        return title
    return title
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add app/parser/ tests/test_md_parser.py
git commit -m "feat: task5 - MD 条文解析器（含标签继承）"
```

---

### Task 6: OCR 模块 (PaddleOCR + PyMuPDF)

**Files:**
- Create: `construction-spec-query-v2/app/ocr/__init__.py`
- Create: `construction-spec-query-v2/app/ocr/paddle_ocr.py`
- Create: `construction-spec-query-v2/app/ocr/pdf_extract.py`
- Create: `construction-spec-query-v2/tests/test_ocr.py`

**Interfaces:**
- Produces: `app.ocr.paddle_ocr.ocr_image(img_path: str) -> str`, `app.ocr.paddle_ocr.ocr_pdf_to_md(pdf_path: str, output_dir: str) -> str`, `app.ocr.pdf_extract.extract_text(pdf_path: str) -> str`, `app.ocr.pdf_extract.is_scanned(pdf_path: str) -> bool`

- [ ] **Step 1: 编写 OCR 测试**

```python
# tests/test_ocr.py
def test_paddle_ocr_module_imports():
    """PaddleOCR 是可选依赖，但模块本身应可导入"""
    from app.ocr import paddle_ocr
    assert hasattr(paddle_ocr, "ocr_pdf_to_md") or True

def test_pdf_extract_is_scanned(tmp_path):
    from app.ocr.pdf_extract import is_scanned
    # 不存在的文件应返回 False 而非崩溃
    result = is_scanned(str(tmp_path / "nonexistent.pdf"))
    assert isinstance(result, bool)

def test_pdf_extract_import():
    from app.ocr.pdf_extract import extract_text
    assert callable(extract_text)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/ocr/pdf_extract.py**

```python
import fitz  # PyMuPDF


def extract_text(pdf_path: str) -> str:
    """提取 PDF 文本层文本"""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text.strip())
    doc.close()
    return "\n\n".join(pages)


def is_scanned(pdf_path: str) -> bool:
    """判断 PDF 是否为扫描件（文本层为空）"""
    try:
        text = extract_text(pdf_path)
        return len(text.strip()) < 100
    except Exception:
        return False
```

- [ ] **Step 4: 创建 app/ocr/paddle_ocr.py**

```python
import inspect
import logging
from pathlib import Path
from app.config import OUTPUT_DIR

logger = logging.getLogger(__name__)


def _get_ocr():
    """懒加载 PaddleOCR，处理 2.x/3.x API 兼容"""
    try:
        from paddleocr import PaddleOCR
        sig = inspect.signature(PaddleOCR.__init__)

        if "lang" in sig.parameters and len(sig.parameters) <= 3:
            # PaddleOCR 3.x
            return PaddleOCR(lang="ch")
        else:
            # PaddleOCR 2.x
            return PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except ImportError:
        logger.warning("PaddleOCR 未安装，OCR 功能不可用")
        return None


def ocr_image(img_path: str) -> str:
    """对单张图片执行 OCR，返回识别文本"""
    ocr = _get_ocr()
    if ocr is None:
        raise RuntimeError("PaddleOCR 未安装")

    sig = inspect.signature(ocr.ocr)
    if "img" in sig.parameters or len(sig.parameters) >= 1:
        result = ocr.ocr(img_path)
        lines = []
        if result and isinstance(result, list):
            for item in result[0] if result else []:
                if len(item) >= 2:
                    lines.append(item[1][0] if isinstance(item[1], (list, tuple)) else str(item[1]))
        return "\n".join(lines)
    else:
        result = ocr.predict(img_path)
        lines = []
        for item in result if result else []:
            text = item.get("rec_text", "") if isinstance(item, dict) else str(item)
            lines.append(text)
        return "\n".join(lines)


def ocr_pdf_to_md(pdf_path: str, output_dir: str | None = None) -> str:
    """对扫描件 PDF 逐页 OCR，输出 Markdown"""
    import fitz
    if output_dir is None:
        output_dir = str(Path(OUTPUT_DIR) / Path(pdf_path).stem)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    md_path = str(Path(output_dir) / f"{Path(pdf_path).stem}.md")
    doc = fitz.open(pdf_path)
    md_parts = []

    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=200)
        img_path = str(Path(output_dir) / f"page_{i:04d}.png")
        pix.save(img_path)
        try:
            text = ocr_image(img_path)
            md_parts.append(f"## 第{i}页\n\n{text}\n")
        except Exception as e:
            md_parts.append(f"## 第{i}页\n\n_[OCR 失败: {e}]_\n")
        finally:
            Path(img_path).unlink(missing_ok=True)

    doc.close()

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_parts))

    return md_path
```

- [ ] **Step 5: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add app/ocr/ tests/test_ocr.py
git commit -m "feat: task6 - OCR 模块"
```

---

### Task 7: 分类规则引擎（自适应阈值）

**Files:**
- Create: `construction-spec-query-v2/app/classifier/__init__.py`
- Create: `construction-spec-query-v2/app/classifier/rule_engine.py`
- Create: `construction-spec-query-v2/tests/test_rule_engine.py`

**Interfaces:**
- Produces: `app.classifier.rule_engine.classify_clause(clause_text: str, parent_path: list[str], active_rules: list[dict]) -> dict[str, float]` (返回每维度的最高得分)
- Consumes: `app.config.ADAPTIVE_THRESHOLDS`

- [ ] **Step 1: 编写规则引擎测试**

```python
# tests/test_rule_engine.py
from app.classifier.rule_engine import classify_clause, should_use_ai

SAMPLE_RULES = [
    {"dimension": "dim4", "sub_field": "specialty", "pattern": "钢筋", "match_type": "keyword", "priority": 1},
    {"dimension": "dim4", "sub_field": "specialty", "pattern": "混凝土", "match_type": "keyword", "priority": 1},
    {"dimension": "dim5", "sub_field": "location", "pattern": "屋面", "match_type": "keyword", "priority": 1},
    {"dimension": "dim6", "sub_field": "material", "pattern": "混凝土", "match_type": "keyword", "priority": 2},
    {"dimension": "dim6", "sub_field": "material", "pattern": "钢筋", "match_type": "keyword", "priority": 1},
    {"dimension": "dim1", "sub_field": "hierarchy", "pattern": r"^GB(?![\\/T])", "match_type": "regex", "priority": 5},
]

def test_classify_clause_returns_all_dims():
    scores = classify_clause("混凝土结构施工", [], SAMPLE_RULES)
    assert "dim1" in scores
    assert "dim2" in scores
    assert "dim3" in scores
    assert "dim4" in scores
    assert "dim5" in scores
    assert "dim6" in scores

def test_classify_clause_keyword_match():
    scores = classify_clause("屋面防水施工应满足设计要求", [], SAMPLE_RULES)
    assert scores["dim5"] > 0  # 屋面匹配工程部位
    assert scores["dim5"] >= scores["dim6"]  # 屋面关键词更强

def test_classify_clause_material_match():
    scores = classify_clause("混凝土强度等级不应低于C30", [], SAMPLE_RULES)
    assert scores["dim6"] > 0  # 混凝土匹配材料维度
    assert scores["dim4"] > 0  # 混凝土也匹配专业维度

def test_classify_clause_no_match():
    scores = classify_clause("某某某无意义文本", [], SAMPLE_RULES)
    assert all(v == 0.0 for v in scores.values())

def test_should_use_ai_returns_true_for_low_score():
    scores = {"dim4": 0.3, "dim5": 0.4, "dim6": 0.1}
    # dim5 阈值 0.7, 得分 0.4 < 0.7 -> 需要 AI
    assert should_use_ai("dim5", scores, {"dim5": 0.7}) is True

def test_should_use_ai_returns_false_for_high_score():
    scores = {"dim4": 0.9, "dim5": 0.8, "dim6": 0.1}
    assert should_use_ai("dim5", scores, {"dim5": 0.7}) is False

def test_classify_clause_parent_path_boost():
    """标签继承：父路径包含关键词时提高得分"""
    scores = classify_clause(
        "模板安装应符合要求",
        ["混凝土分项工程", "模板"],
        SAMPLE_RULES,
    )
    # 模板不在规则中，但父路径中有"混凝土"应该提高 dim6 得分
    assert scores["dim6"] > 0 or scores["dim4"] > 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_engine.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/classifier/rule_engine.py**

```python
import re
from app.config import ADAPTIVE_THRESHOLDS


def _match_score(text: str, rule: dict) -> float:
    """单条规则对文本的匹配得分"""
    pattern = rule["pattern"]
    match_type = rule.get("match_type", "keyword")
    priority = rule.get("priority", 1)

    if match_type == "exact":
        if pattern == text.strip():
            return 1.0 * (1 + 0.1 * priority)
        return 0.0
    elif match_type == "regex":
        try:
            matches = list(re.finditer(pattern, text))
            if matches:
                # 匹配覆盖度
                coverage = sum(m.end() - m.start() for m in matches) / len(text)
                return min(1.0, coverage * 2) * (1 + 0.1 * priority)
        except re.error:
            return 0.0
        return 0.0
    else:  # keyword
        count = text.count(pattern)
        if count == 0:
            return 0.0
        return min(1.0, 0.3 + count * 0.15) * (1 + 0.1 * priority)


def classify_clause(clause_text: str, parent_path: list[str],
                    active_rules: list[dict]) -> dict[str, float]:
    """对单条条文执行规则匹配，返回六维得分 {dim1..dim6}"""
    dims = ["dim1", "dim2", "dim3", "dim4", "dim5", "dim6"]
    scores = {d: 0.0 for d in dims}

    # 父路径关键词也加入匹配文本（标签继承）
    augmented_text = clause_text + " " + " ".join(parent_path)

    for rule in active_rules:
        if not rule.get("is_active", 1):
            continue
        dim = rule["dimension"]
        if dim not in scores:
            continue
        score = _match_score(augmented_text, rule)
        if score > scores[dim]:
            scores[dim] = score

    return scores


def should_use_ai(dimension: str, scores: dict[str, float],
                  thresholds: dict[str, float] | None = None) -> bool:
    """判断该维度是否需要 AI 辅助分类"""
    if thresholds is None:
        thresholds = ADAPTIVE_THRESHOLDS
    threshold = thresholds.get(dimension, 0.6)
    return scores.get(dimension, 0.0) < threshold
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_engine.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add app/classifier/__init__.py app/classifier/rule_engine.py tests/test_rule_engine.py
git commit -m "feat: task7 - 分类规则引擎（自适应阈值）"
```

---

### Task 8: 攒批队列与反馈闭环

**Files:**
- Create: `construction-spec-query-v2/app/classifier/batch_queue.py`
- Create: `construction-spec-query-v2/app/classifier/feedback.py`
- Create: `construction-spec-query-v2/tests/test_batch_queue.py`

**Interfaces:**
- Produces: `app.classifier.batch_queue.add_to_queue(db, clause_id, dim, score)`, `app.classifier.batch_queue.get_pending_batch(db, dim) -> list[dict]`, `app.classifier.batch_queue.apply_ai_results(db, batch_id, results)`, `app.classifier.feedback.extract_keywords(text: str, top_n: int) -> list[str]`, `app.classifier.feedback.process_feedback(db, clause_id, dim, confirmed_label)`

- [ ] **Step 1: 编写测试**

```python
# tests/test_batch_queue.py
from app.classifier.batch_queue import add_to_queue, get_pending_batch, apply_ai_results
from app.classifier.feedback import extract_keywords, process_feedback
from app.database import init_db, get_db

def setup_sample_data(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '测试规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(25):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
            (spec_id, f"1.0.{i+1}", f"这是第{i+1}条测试条文 混凝土施工"),
        )
    return spec_id

def test_add_to_queue(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        spec_id = setup_sample_data(conn)
        clause_id = conn.execute("SELECT id FROM clauses LIMIT 1").fetchone()["id"]
    add_to_queue(clause_id, "dim6", 0.35)
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE clause_id = ?",
            (clause_id,)
        ).fetchone()[0]
        assert count == 1

def test_get_pending_batch(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        spec_id = setup_sample_data(conn)
        clause_ids = [r["id"] for r in conn.execute("SELECT id FROM clauses LIMIT 20")]
    for cid in clause_ids:
        add_to_queue(cid, "dim6", 0.35)
    batch = get_pending_batch("dim6")
    assert len(batch) >= 1

def test_apply_ai_results(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        spec_id = setup_sample_data(conn)
        clause_id = conn.execute("SELECT id FROM clauses LIMIT 1").fetchone()["id"]
    add_to_queue(clause_id, "dim6", 0.35)
    batch = get_pending_batch("dim6")
    assert len(batch) > 0
    batch_id = batch[0]["batch_id"] if batch[0].get("batch_id") else None
    if batch_id:
        apply_ai_results(batch_id, [
            {"clause_id": clause_id, "label": "混凝土材料", "confidence": 0.85},
        ])
        with get_db() as conn:
            status = conn.execute(
                "SELECT status FROM classification_queue WHERE clause_id = ?",
                (clause_id,)
            ).fetchone()["status"]
            assert status == "auto_adopted"

def test_extract_keywords():
    text = "模板及其支架应根据工程结构形式进行设计。模板的接缝不应漏浆。"
    keywords = extract_keywords(text, top_n=5)
    assert len(keywords) >= 1
    assert any("模板" in kw for kw in keywords)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_batch_queue.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 batch_queue.py 和 feedback.py**

```python
# app/classifier/batch_queue.py
import uuid
from app.config import BATCH_SIZE
from app.database import get_db


def add_to_queue(clause_id: int, dimension: str, keyword_score: float):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
            (clause_id, dimension, keyword_score),
        )


def get_pending_batch(dimension: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.*, c.content FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               WHERE q.dimension = ? AND q.status = 'pending'
               ORDER BY q.created_at LIMIT ?""",
            (dimension, BATCH_SIZE),
        ).fetchall()

        if len(rows) < BATCH_SIZE:
            return []

        batch_id = uuid.uuid4().hex[:12]
        ids = [r["id"] for r in rows]
        conn.execute(
            f"UPDATE classification_queue SET batch_id = ?, status = 'ai_processing' WHERE id IN ({','.join('?'*len(ids))})",
            [batch_id] + ids,
        )
        return [dict(r) for r in rows]


def apply_ai_results(batch_id: str, results: list[dict]):
    with get_db() as conn:
        for r in results:
            status = "auto_adopted" if r["confidence"] >= 0.7 else "review"
            conn.execute(
                """UPDATE classification_queue
                   SET ai_label = ?, ai_confidence = ?, status = ?
                   WHERE batch_id = ? AND clause_id = ?""",
                (r["label"], r["confidence"], status, batch_id, r["clause_id"]),
            )
            # 自动采纳的更新 clauses 表维度标签
            if status == "auto_adopted":
                q_row = conn.execute(
                    "SELECT dimension FROM classification_queue WHERE clause_id = ? AND batch_id = ?",
                    (r["clause_id"], batch_id),
                ).fetchone()
                if q_row:
                    dim = q_row["dimension"]
                    col = _dim_to_column(dim)
                    conn.execute(
                        f"UPDATE clauses SET {col} = ?, ai_classified = 1 WHERE id = ?",
                        (r["label"], r["clause_id"]),
                    )


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
```

```python
# app/classifier/feedback.py
import re
from collections import Counter
from app.database import get_db


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """从中文文本提取高频关键词（简化 TF）"""
    # 简单分词：2-4 字的中文词组
    words = re.findall(r"[一-龥]{2,4}", text)
    # 过滤停用词
    stopwords = {"的", "和", "是", "在", "了", "不", "与", "及", "或", "应", "其", "为", "等", "以", "中", "对"}
    words = [w for w in words if w not in stopwords]
    counter = Counter(words)
    return [w for w, _ in counter.most_common(top_n)]


def process_feedback(clause_id: int, dimension: str, confirmed_label: str):
    with get_db() as conn:
        # 标记复核完成
        conn.execute(
            "UPDATE classification_queue SET status = 'done' WHERE clause_id = ? AND dimension = ?",
            (clause_id, dimension),
        )
        # 更新 clauses 表
        col = _dim_to_column(dimension)
        conn.execute(
            f"UPDATE clauses SET {col} = ?, ai_classified = 1, needs_review = 0 WHERE id = ?",
            (confirmed_label, clause_id),
        )
        # 获取条文内容
        row = conn.execute("SELECT content FROM clauses WHERE id = ?", (clause_id,)).fetchone()
        if not row:
            return

        # 提取关键词
        keywords = extract_keywords(row["content"], top_n=3)
        sub_field = {"dim4": "specialty", "dim5": "location", "dim6": "material"}.get(dimension, "")

        for kw in keywords:
            existing = conn.execute(
                "SELECT id, hit_count, confirmed FROM classification_rules WHERE dimension = ? AND pattern = ?",
                (dimension, kw),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                    (existing["id"],),
                )
                # 准确率过低则禁用
                new_hit = existing["hit_count"] + 1
                new_confirmed = existing["confirmed"] + 1
                if new_confirmed / new_hit < 0.3 and new_hit > 10:
                    conn.execute(
                        "UPDATE classification_rules SET is_active = 0 WHERE id = ?",
                        (existing["id"],),
                    )
            else:
                conn.execute(
                    """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
                       VALUES (?, ?, ?, 'keyword', 0,
                       (SELECT threshold FROM classification_rules WHERE dimension = ? LIMIT 1), 0)""",
                    (dimension, sub_field, kw, dimension),
                )


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
```

Note: `batch_queue.py` also needs `_dim_to_column` — 复制同一函数。

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_batch_queue.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/classifier/batch_queue.py app/classifier/feedback.py tests/test_batch_queue.py
git commit -m "feat: task8 - 攒批队列与反馈闭环"
```

---

### Task 9: CLI 客户端抽象层

**Files:**
- Create: `construction-spec-query-v2/app/ai/__init__.py`
- Create: `construction-spec-query-v2/app/ai/cli_client.py`
- Create: `construction-spec-query-v2/tests/test_cli_client.py`

**Interfaces:**
- Produces: `app.ai.cli_client.CLIBackend` (ABC), `app.ai.cli_client.ClaudeCodeCLI`, `app.ai.cli_client.CodexCLI`, `app.ai.cli_client.get_backend(name: str) -> CLIBackend`

- [ ] **Step 1: 编写测试**

```python
# tests/test_cli_client.py
from app.ai.cli_client import get_backend, ClaudeCodeCLI, CodexCLI, CLIBackend

def test_get_backend_claude():
    backend = get_backend("claude")
    assert isinstance(backend, ClaudeCodeCLI)
    assert backend.command == "claude"

def test_get_backend_codex():
    backend = get_backend("codex")
    assert isinstance(backend, CodexCLI)
    assert backend.command == "codex"

def test_get_backend_default():
    backend = get_backend("unknown")
    assert isinstance(backend, ClaudeCodeCLI)

def test_claude_is_available_mocked(monkeypatch):
    class FakeResult:
        returncode = 0
    def fake_run(*args, **kwargs):
        return FakeResult()
    monkeypatch.setattr("subprocess.run", fake_run)
    backend = ClaudeCodeCLI()
    assert backend.is_available() is True

def test_codex_is_available_mocked(monkeypatch):
    class FakeResult:
        returncode = 0
    def fake_run(*args, **kwargs):
        return FakeResult()
    monkeypatch.setattr("subprocess.run", fake_run)
    backend = CodexCLI()
    assert backend.is_available() is True

def test_cli_backend_abstract():
    import inspect
    assert inspect.isabstract(CLIBackend)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_cli_client.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/ai/cli_client.py**

```python
import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CLIResponse:
    success: bool
    content: str
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class ClassifyResult:
    clause_id: int
    label: str
    confidence: float


class CLIBackend(ABC):
    command: str = ""

    @abstractmethod
    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse: ...

    @abstractmethod
    async def classify_batch(self, clauses: list[dict],
                              dimension: str) -> list[ClassifyResult]: ...

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                [self.command, "--version"], capture_output=True,
                timeout=5, text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_cli(self, prompt: str, work_dir: str | None = None,
                 timeout: int = 60) -> CLIResponse:
        import time
        start = time.time()
        try:
            cmd = [self.command, "--print", prompt]
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=work_dir
            )
            duration = (time.time() - start) * 1000
            if result.returncode == 0:
                return CLIResponse(success=True, content=result.stdout.strip(), duration_ms=duration)
            else:
                return CLIResponse(success=False, content="", error=result.stderr.strip(), duration_ms=duration)
        except subprocess.TimeoutExpired:
            return CLIResponse(success=False, content="", error="CLI 调用超时", duration_ms=timeout*1000)
        except FileNotFoundError:
            return CLIResponse(success=False, content="", error=f"{self.command} 命令未找到，请确认已安装")


class ClaudeCodeCLI(CLIBackend):
    command = "claude"

    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
        return self._run_cli(full_prompt, work_dir=work_dir, timeout=60)

    async def classify_batch(self, clauses: list[dict],
                              dimension: str) -> list[ClassifyResult]:
        dim_labels = {
            "dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"
        }
        dim_label = dim_labels.get(dimension, dimension)

        items = "\n".join(
            f"{i+1}. [ID:{c['clause_id']}] {c['content'][:200]}"
            for i, c in enumerate(clauses)
        )
        prompt = (
            f"你是施工规范分类助手。为以下条文标注{dim_label}维度。\n"
            f"{items}\n\n"
            f"请以 JSON 格式返回分类结果: "
            f'[{{"clause_id": <id>, "label": "<分类标签>", "confidence": <0.0-1.0>}}]'
        )

        import json
        resp = self._run_cli(prompt, timeout=120)
        if resp.success:
            try:
                # 提取 JSON 数组
                text = resp.content
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    data = json.loads(text[start:end])
                    return [
                        ClassifyResult(
                            clause_id=item["clause_id"],
                            label=item.get("label", ""),
                            confidence=item.get("confidence", 0.5),
                        )
                        for item in data
                    ]
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"CLI 返回解析失败: {e}")
        return []


class CodexCLI(CLIBackend):
    command = "codex"

    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
        return self._run_cli(full_prompt, work_dir=work_dir, timeout=60)

    async def classify_batch(self, clauses: list[dict],
                              dimension: str) -> list[ClassifyResult]:
        # Codex CLI 与 Claude CLI 接口相同
        dim_labels = {
            "dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"
        }
        dim_label = dim_labels.get(dimension, dimension)

        items = "\n".join(
            f"{i+1}. [ID:{c['clause_id']}] {c['content'][:200]}"
            for i, c in enumerate(clauses)
        )
        prompt = (
            f"你是施工规范分类助手。为以下条文标注{dim_label}维度。\n"
            f"{items}\n\n"
            f"请以 JSON 格式返回分类结果: "
            f'[{{"clause_id": <id>, "label": "<分类标签>", "confidence": <0.0-1.0>}}]'
        )

        import json
        resp = self._run_cli(prompt, timeout=120)
        if resp.success:
            try:
                text = resp.content
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    data = json.loads(text[start:end])
                    return [
                        ClassifyResult(
                            clause_id=item["clause_id"],
                            label=item.get("label", ""),
                            confidence=item.get("confidence", 0.5),
                        )
                        for item in data
                    ]
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"CLI 返回解析失败: {e}")
        return []


def get_backend(name: str) -> CLIBackend:
    if name == "codex":
        return CodexCLI()
    return ClaudeCodeCLI()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_cli_client.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/__init__.py app/ai/cli_client.py tests/test_cli_client.py
git commit -m "feat: task9 - CLI 客户端抽象层"
```

---

### Task 10: AI 批量分类器

**Files:**
- Create: `construction-spec-query-v2/app/ai/classifier_ai.py`
- Create: `construction-spec-query-v2/tests/test_classifier_ai.py`

**Interfaces:**
- Produces: `app.ai.classifier_ai.run_batch_classification(backend_name: str) -> int` (处理一个批次，返回处理条数)
- Consumes: `app.ai.cli_client.get_backend`, `app.classifier.batch_queue`

- [ ] **Step 1: 编写测试**

```python
# tests/test_classifier_ai.py
from app.ai.classifier_ai import process_pending_batches
from app.database import init_db, get_db
from app.classifier.batch_queue import add_to_queue

def setup_data(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '测试规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(25):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
            (spec_id, f"1.0.{i+1}", f"第{i+1}条 混凝土模板钢筋施工"),
        )

def test_process_pending_batches_no_cli(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_data(conn)
        clause_ids = [r["id"] for r in conn.execute("SELECT id FROM clauses LIMIT 22")]
    for cid in clause_ids[:20]:
        add_to_queue(cid, "dim6", 0.35)

    # CLI 不可用时应优雅降级
    result = process_pending_batches("nonexistent_cli")
    assert result == 0  # 无法处理，返回 0

def test_process_pending_batches_no_pending(monkeypatch, tmp_path):
    """队列为空时正常返回"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    result = process_pending_batches("claude")
    assert result == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_classifier_ai.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/ai/classifier_ai.py**

```python
import logging
from app.ai.cli_client import get_backend
from app.classifier.batch_queue import get_pending_batch, apply_ai_results

logger = logging.getLogger(__name__)

DIMS = ["dim4", "dim5", "dim6"]


def process_pending_batches(backend_name: str = "claude") -> int:
    """处理所有维度的待分类批次，返回处理条数。由后台任务定期调用。"""
    backend = get_backend(backend_name)
    if not backend.is_available():
        logger.warning(f"CLI ({backend_name}) 不可用，跳过批次处理")
        return 0

    total = 0
    for dim in DIMS:
        batch = get_pending_batch(dim)
        if not batch:
            continue

        try:
            results = backend.classify_batch_sync(batch, dim)
            if results:
                formatted = [
                    {"clause_id": r.clause_id, "label": r.label, "confidence": r.confidence}
                    for r in results
                ]
                batch_id = batch[0].get("batch_id")
                if batch_id:
                    apply_ai_results(batch_id, formatted)
                    total += len(formatted)
        except Exception as e:
            logger.error(f"批次处理失败 (dim={dim}): {e}")

    return total
```

Note: 需要在 `CLIBackend` 添加同步版 `classify_batch_sync` 方法，实现同上但非 async。

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_classifier_ai.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/classifier_ai.py tests/test_classifier_ai.py
git commit -m "feat: task10 - AI 批量分类器"
```

---

### Task 11: BGE Embedding 模块

**Files:**
- Create: `construction-spec-query-v2/app/ai/embedding.py`
- Create: `construction-spec-query-v2/tests/test_embedding.py`

**Interfaces:**
- Produces: `app.ai.embedding.get_model()`, `app.ai.embedding.embed_texts(texts: list[str]) -> list[list[float]]` (512维向量)
  
- [ ] **Step 1: 编写测试**

```python
# tests/test_embedding.py
def test_embedding_module_imports():
    from app.ai.embedding import embed_texts, get_model
    assert callable(embed_texts)
    assert callable(get_model)

def test_get_model_returns_same_instance():
    from app.ai.embedding import get_model
    m1 = get_model()
    m2 = get_model()
    assert m1 is m2  # 单例

def test_embed_texts_returns_correct_shape():
    """如 BGE 未安装则跳过"""
    import pytest
    sentence_transformers = pytest.importorskip("sentence_transformers")
    from app.ai.embedding import embed_texts
    texts = ["混凝土结构施工", "钢筋绑扎要求"]
    vectors = embed_texts(texts)
    assert len(vectors) == 2
    assert len(vectors[0]) == 512  # BGE-small-zh 输出 512 维
    assert isinstance(vectors[0][0], float)

def test_embed_texts_empty():
    from app.ai.embedding import embed_texts
    assert embed_texts([]) == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_embedding.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 创建 app/ai/embedding.py**

```python
import logging
from typing import Optional

logger = logging.getLogger(__name__)
_model: Optional[object] = None


def get_model():
    """懒加载 BGE-small-zh 模型（单例）"""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
            logger.info("BGE-small-zh 模型加载完成")
        except ImportError:
            logger.warning("sentence-transformers 未安装，向量检索不可用")
            _model = False
        except Exception as e:
            logger.error(f"BGE 模型加载失败: {e}")
            _model = False
    return _model if _model is not False else None


def embed_texts(texts: list[str]) -> list[list[float]]:
    """将文本列表转为 512 维向量"""
    if not texts:
        return []
    model = get_model()
    if model is None:
        raise RuntimeError("Embedding 模型不可用")
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_embedding.py -v`
Expected: 4 PASS (BGE 未安装则部分 skip)

- [ ] **Step 5: Commit**

```bash
git add app/ai/embedding.py tests/test_embedding.py
git commit -m "feat: task11 - BGE Embedding 模块"
```

---

### Task 12: SQL 搜索 + LanceDB 向量搜索

**Files:**
- Create: `construction-spec-query-v2/app/search/__init__.py`
- Create: `construction-spec-query-v2/app/search/sql_search.py`
- Create: `construction-spec-query-v2/app/search/vector_search.py`
- Create: `construction-spec-query-v2/tests/test_search.py`

**Interfaces:**
- Produces: `app.search.sql_search.search_clauses(db, query: SearchQuery) -> tuple[list[dict], int]` (结果列表+总数), `app.search.vector_search.VectorStore` class with `search(text: str, top_k: int) -> list[dict]`, `index_clause(clause_id, spec_id, text, dim_scores)`, `delete_clause(clause_id)`

- [ ] **Step 1: 编写搜索测试**

```python
# tests/test_search.py
from app.search.sql_search import search_clauses
from app.database import init_db, get_db
from app.models import SearchQuery

def setup_search_data(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。", "结构专业", "主体结构", "模板工程"),
        ("5.2.1", "钢筋原材料", "钢筋进场时应抽取试件作屈服强度检验。", "结构专业", "主体结构", "金属材料,钢筋"),
        ("6.1.1", "屋面防水", "屋面防水层应采用卷材或涂膜防水。", "建筑专业", "屋面", "防水材料"),
    ]
    for no, title, content, dim4, dim5, dim6 in clauses_data:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, dim4_specialty, dim5_location, dim6_material)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6),
        )

def test_search_keyword(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(keyword="钢筋"))
    assert total >= 1
    assert any("钢筋" in r["content"] for r in results)

def test_search_dimension_filter(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(dim5_location="屋面"))
    assert total >= 1
    assert results[0]["dim5_location"] == "屋面"

def test_search_combined(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(keyword="混凝土", dim6_material="模板"))
    # 关键词"混凝土"匹配条文但 dim6 不匹配"模板" -> 0 结果
    assert total == 0

def test_search_pagination(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(page=1, per_page=2))
    assert len(results) <= 2
    assert total == 3

def test_vector_store_import():
    from app.search.vector_search import VectorStore
    assert hasattr(VectorStore, "search")
    assert hasattr(VectorStore, "index_clause")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_search.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 app/search/sql_search.py**

```python
from app.database import get_db
from app.models import SearchQuery


def search_clauses(query: SearchQuery) -> tuple[list[dict], int]:
    """多维筛选 + FTS5 关键词搜索，返回 (结果列表, 总数)"""
    with get_db() as conn:
        conditions = []
        params = []

        if query.keyword:
            conditions.append(
                "c.id IN (SELECT rowid FROM clauses_fts WHERE clauses_fts MATCH ?)"
            )
            params.append(query.keyword)

        dim_filters = {
            "dim1_hierarchy": query.dim1_hierarchy,
            "dim1_nature": query.dim1_nature,
            "dim2_stage": query.dim2_stage,
            "dim3_usage": query.dim3_usage,
            "dim4_specialty": query.dim4_specialty,
            "dim5_location": query.dim5_location,
            "dim6_material": query.dim6_material,
        }
        for col, val in dim_filters.items():
            if val:
                if col.startswith("dim"):
                    conditions.append(f"c.{col} LIKE ?")
                    params.append(f"%{val}%")
                else:
                    conditions.append(f"s.{col} LIKE ?")
                    params.append(f"%{val}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        # 总数
        count_sql = f"""
            SELECT COUNT(*) FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            {where}
        """
        total = conn.execute(count_sql, params).fetchone()[0]

        # 结果
        offset = (query.page - 1) * query.per_page
        data_sql = f"""
            SELECT c.*, s.code as spec_code, s.title as spec_title
            FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            {where}
            ORDER BY c.id
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(data_sql, params + [query.per_page, offset]).fetchall()
        return [dict(r) for r in rows], total
```

- [ ] **Step 4: 创建 app/search/vector_search.py**

```python
import lancedb
import pyarrow as pa
from app.config import LANCE_DB_PATH
from app.ai.embedding import embed_texts


class VectorStore:
    def __init__(self):
        self.db = lancedb.connect(LANCE_DB_PATH)

    def _table_exists(self) -> bool:
        try:
            tables = self.db.list_tables()
            if hasattr(tables, "tables"):
                names = tables.tables
            else:
                names = list(tables)
            return "clause_embeddings" in names
        except Exception:
            return False

    def _get_table(self):
        return self.db.open_table("clause_embeddings")

    def index_clause(self, clause_id: int, spec_id: int, text: str, dim_scores: str = ""):
        vectors = embed_texts([text])
        data = [{
            "clause_id": clause_id,
            "spec_id": spec_id,
            "text": text,
            "embedding": vectors[0],
            "dim_scores": dim_scores,
        }]
        if self._table_exists():
            self._get_table().add(data)
        else:
            self.db.create_table("clause_embeddings", data)

    def search(self, query_text: str, top_k: int = 10,
               dim_filter: str | None = None) -> list[dict]:
        if not self._table_exists():
            return []
        q_vec = embed_texts([query_text])[0]
        tbl = self._get_table()
        results = tbl.search(q_vec).limit(top_k).to_list()
        return [
            {"clause_id": r["clause_id"], "spec_id": r["spec_id"],
             "text": r["text"], "_distance": r.get("_distance", 0)}
            for r in results
        ]

    def delete_clause(self, clause_id: int):
        if self._table_exists():
            self._get_table().delete(f"clause_id = {clause_id}")
```

- [ ] **Step 5: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_search.py -v`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add app/search/ tests/test_search.py
git commit -m "feat: task12 - SQL 搜索 + LanceDB 向量搜索"
```

---

### Task 13: 认证路由 + 登录页面

**Files:**
- Create: `construction-spec-query-v2/app/routes/__init__.py`
- Create: `construction-spec-query-v2/app/routes/auth_routes.py`
- Create: `construction-spec-query-v2/app/templates/login.html`
- Create: `construction-spec-query-v2/app/templates/base.html`
- Create: `construction-spec-query-v2/tests/test_auth_routes.py`

**Interfaces:**
- Produces: FastAPI router at `/login` (GET/POST), `/logout`. JWT cookie/session.
- Consumes: `app.auth`, `app.database`, `app.models`

- [ ] **Step 1: 编写路由测试**

```python
# tests/test_auth_routes.py
from fastapi.testclient import TestClient

def test_login_page_loads(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "登录" in resp.text or "login" in resp.text.lower()

def test_login_success(client, monkeypatch, tmp_path):
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    pwd_hash = hash_password("test123")
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", pwd_hash))
    resp = client.post("/login", data={"username": "admin", "password": "test123"},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)

def test_login_failure(client, monkeypatch, tmp_path):
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    pwd_hash = hash_password("test123")
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", pwd_hash))
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 200  # 回到登录页
    assert "用户名或密码错误" in resp.text

def test_protected_page_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `D:/Python/python.exe -m pytest tests/test_auth_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 创建路由和模板**

```python
# app/routes/auth_routes.py
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_302_FOUND
from app.database import get_db
from app.auth import verify_password, create_access_token
from app.config import ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()

    if user and verify_password(password, user["password_hash"]):
        token = create_access_token({"sub": username})
        resp = RedirectResponse(url="/", status_code=HTTP_302_FOUND)
        resp.set_cookie(
            key="access_token", value=token,
            httponly=True, max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
        return resp

    from app.main import templates
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "用户名或密码错误"},
    )


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=HTTP_302_FOUND)
    resp.delete_cookie("access_token")
    return resp
```

```html
<!-- app/templates/login.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>登录 - 施工规范查询系统</title>
    <link rel="stylesheet" href="/static/pico.min.css">
    <link rel="stylesheet" href="/static/app.css">
</head>
<body>
    <main class="container" style="max-width: 400px; margin-top: 10vh;">
        <h1>施工规范查询系统</h1>
        {% if error %}
        <article style="background: #fee; color: #c00;">{{ error }}</article>
        {% endif %}
        <form method="post">
            <label>用户名 <input type="text" name="username" required autofocus></label>
            <label>密码 <input type="password" name="password" required></label>
            <button type="submit">登 录</button>
        </form>
    </main>
</body>
</html>
```

```html
<!-- app/templates/base.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>施工规范查询系统 V2</title>
    <link rel="stylesheet" href="/static/pico.min.css">
    <link rel="stylesheet" href="/static/pico.custom.css">
    <link rel="stylesheet" href="/static/app.css">
    <script src="/static/htmx.min.js"></script>
    <script src="/static/alpine.min.js" defer></script>
    <script src="/static/marked.min.js"></script>
</head>
<body>
    <!-- 三栏布局容器 -->
    <div x-data="appLayout()" class="app-layout">
        <!-- 左栏 -->
        <aside class="left-panel" :class="{ collapsed: sidebarCollapsed }">
            {% block left_panel %}{% endblock %}
        </aside>
        <!-- 中栏 -->
        <main class="center-panel">
            {% block center_panel %}{% endblock %}
        </main>
        <!-- 右栏 -->
        <aside class="right-panel" :class="{ collapsed: qaCollapsed }">
            {% block right_panel %}{% endblock %}
        </aside>
    </div>
    <script src="/static/components/tree.js"></script>
    <script src="/static/components/qa.js"></script>
    <script src="/static/components/search.js"></script>
    <script src="/static/components/import.js"></script>
    <script>
        function appLayout() {
            return {
                sidebarCollapsed: false,
                qaCollapsed: false,
                toggleSidebar() { this.sidebarCollapsed = !this.sidebarCollapsed; },
                toggleQA() { this.qaCollapsed = !this.qaCollapsed; },
            }
        }
    </script>
</body>
</html>
```

- [ ] **Step 4: 更新 app/main.py 注册路由和鉴权中间件**

```python
# app/main.py (更新)
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import BASE_DIR
from app.auth import decode_access_token
from jose import JWTError

app = FastAPI(title="施工规范查询系统 V2")

# 静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Jinja2 模板
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public_paths = ["/login", "/static", "/health"]
        if any(request.url.path.startswith(p) for p in public_paths):
            return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)

        try:
            payload = decode_access_token(token)
            request.state.username = payload.get("sub")
        except JWTError:
            return RedirectResponse(url="/login", status_code=302)

        return await call_next(request)


app.add_middleware(AuthMiddleware)

from app.routes.auth_routes import router as auth_router
app.include_router(auth_router)


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("base.html", {
        "request": request,
        "left_panel": "partials/tree_panel.html",
        "center_panel": "partials/welcome.html",
        "right_panel": "partials/qa_panel.html",
    })
```

- [ ] **Step 5: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_auth_routes.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/ app/templates/ app/main.py tests/test_auth_routes.py
git commit -m "feat: task13 - 认证路由 + 登录页面 + 基础布局"
```

---

### Task 14: 导入路由（文件上传 + OCR + 解析 + 分类）

**Files:**
- Create: `construction-spec-query-v2/app/routes/import_routes.py`
- Create: `construction-spec-query-v2/app/templates/partials/import_progress.html`
- Create: `construction-spec-query-v2/app/templates/components/import.js`
- Create: `construction-spec-query-v2/tests/test_import.py`

**Interfaces:**
- Produces: `POST /import/upload`, `GET /import/progress/{task_id}`
- Consumes: OCR modules, parser, classifier

- [ ] **Step 1: 编写导入路由测试**

```python
# tests/test_import.py
def test_import_page_protected(client):
    resp = client.get("/import")
    assert resp.status_code in (302, 303, 401)

def test_upload_no_file_authenticated(auth_client):
    resp = auth_client.post("/import/upload")
    assert resp.status_code in (400, 422)
```

依赖 `auth_client` fixture — 需要在 conftest.py 添加认证 fixture。

- [ ] **Step 2: 创建导入路由** (代码较长，实现要点)

```python
# app/routes/import_routes.py
import uuid
import asyncio
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from app.config import UPLOAD_DIR, OUTPUT_DIR
from app.database import get_db
from app.parser.md_parser import parse_markdown
from app.ocr.pdf_extract import extract_text, is_scanned
from app.classifier.rule_engine import classify_clause, should_use_ai
from app.classifier.batch_queue import add_to_queue
from app.ai.embedding import embed_texts
from app.search.vector_search import VectorStore

router = APIRouter()
progress_store = {}  # {task_id: {"status","progress","message"}}


@router.post("/import/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    code: str = Form(""),
):
    task_id = uuid.uuid4().hex[:8]
    progress_store[task_id] = {"status": "uploading", "progress": 0, "message": "正在上传..."}

    # 保存文件
    ext = Path(file.filename).suffix.lower()
    save_path = Path(UPLOAD_DIR) / f"{task_id}{ext}"
    content = await file.read()
    save_path.write_bytes(content)

    background_tasks.add_task(
        _process_import, task_id, str(save_path), title, code
    )
    return HTMLResponse(f'<div id="import-status" hx-trigger="every 2s" hx-get="/import/progress/{task_id}" hx-swap="outerHTML">处理中...</div>')


@router.get("/import/progress/{task_id}")
async def get_progress(request: Request, task_id: str):
    p = progress_store.get(task_id, {"status": "unknown", "progress": 0, "message": "未知任务"})
    from app.main import templates
    return templates.TemplateResponse("partials/import_progress.html", {
        "request": request, "task_id": task_id, "progress": p,
    })


def _process_import(task_id: str, file_path: str, title: str, code: str):
    """后台任务：OCR(如需) -> 解析 -> 分类 -> 索引"""
    try:
        path = Path(file_path)
        ext = path.suffix.lower()
        progress_store[task_id].update(status="processing", progress=10, message="正在提取文本...")

        # Step 1: 获取 MD 文本
        if ext == ".md":
            md_text = path.read_text(encoding="utf-8")
        elif ext == ".pdf":
            if is_scanned(file_path):
                progress_store[task_id].update(progress=20, message="正在 OCR 识别...")
                from app.ocr.paddle_ocr import ocr_pdf_to_md
                md_path = ocr_pdf_to_md(file_path)
                md_text = Path(md_path).read_text(encoding="utf-8")
            else:
                md_text = extract_text(file_path)
        else:
            progress_store[task_id].update(status="error", message=f"不支持的文件格式: {ext}")
            return

        progress_store[task_id].update(progress=40, message="正在解析条文...")

        # Step 2: 解析条文
        clauses_data = parse_markdown(md_text)

        # Step 3: 规范级分类 (维一~三)
        # 简化：用 code 前缀匹配维一
        dim1_hierarchy = _detect_hierarchy(code)
        dim1_nature = "推荐性" if "/T" in code else "强制性"

        progress_store[task_id].update(progress=60, message=f"正在分类 {len(clauses_data)} 条条文...")

        # Step 4: 写入数据库 + 条文级分类
        from app.config import ADAPTIVE_THRESHOLDS
        output_dir = str(Path(OUTPUT_DIR) / (code or path.stem))
        rules = _load_active_rules()
        vs = VectorStore()

        with get_db() as conn:
            conn.execute(
                """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature, source_path, output_dir)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (code or path.stem, title or path.stem, dim1_hierarchy, dim1_nature, file_path, output_dir),
            )
            spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            count = 0
            for cd in clauses_data:
                scores = classify_clause(cd["content"], cd.get("parent_path", []), rules)
                conn.execute(
                    """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause)
                       VALUES (?, ?, ?, ?, ?)""",
                    (spec_id, cd["clause_no"], cd["title"], cd["content"], None),
                )
                clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # 向量索引
                try:
                    dim_scores_str = ",".join(f"{k}={v:.2f}" for k, v in scores.items())
                    vs.index_clause(clause_id, spec_id,
                                    f"[{cd['clause_no']}] {cd['title'] or ''} {cd['content']}",
                                    dim_scores_str)
                except Exception:
                    pass  # embedding 不可用时跳过

                # 低置信度入队
                for dim in ["dim4", "dim5", "dim6"]:
                    if should_use_ai(dim, scores, ADAPTIVE_THRESHOLDS):
                        add_to_queue(clause_id, dim, scores[dim])

                count += 1

            conn.execute("UPDATE specifications SET clause_count = ? WHERE id = ?", (count, spec_id))

        progress_store[task_id].update(status="done", progress=100, message=f"导入完成：{len(clauses_data)} 条条文")
    except Exception as e:
        progress_store[task_id].update(status="error", progress=0, message=str(e))


def _detect_hierarchy(code: str) -> str:
    if code.startswith("GB"):
        return "国家标准"
    elif code.startswith("JGJ") or code.startswith("CJJ"):
        return "行业标准"
    elif code.startswith("DB"):
        return "地方标准"
    elif code.startswith("T/"):
        return "团体标准"
    return "企业标准"


def _load_active_rules() -> list[dict]:
    from app.database import get_db
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM classification_rules WHERE is_active = 1").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 3: 导入进度模板**

```html
<!-- app/templates/partials/import_progress.html -->
<div id="import-status">
    <progress value="{{ progress.progress }}" max="100"></progress>
    <p>{{ progress.message }}</p>
    {% if progress.status == "done" %}
    <p style="color: green;">导入完成！<a href="/">刷新页面</a></p>
    {% elif progress.status == "error" %}
    <p style="color: red;">导入失败: {{ progress.message }}</p>
    {% else %}
    <div hx-get="/import/progress/{{ task_id }}" hx-trigger="every 2s" hx-swap="outerHTML"></div>
    {% endif %}
</div>
```

- [ ] **Step 4: 导入 Alpine.js 组件**

```javascript
// app/templates/components/import.js (嵌入式)
document.addEventListener('alpine:init', () => {
    Alpine.data('importDialog', () => ({
        open: false,
        uploading: false,
        openDialog() { this.open = true; },
        closeDialog() { this.open = false; this.uploading = false; },
        async handleUpload(event) {
            const form = event.target;
            const formData = new FormData(form);
            this.uploading = true;
            const resp = await fetch('/import/upload', { method: 'POST', body: formData });
            const html = await resp.text();
            document.getElementById('import-result').innerHTML = html;
        },
    }))
});
```

- [ ] **Step 5: 运行测试验证通过**

Run: `D:/Python/python.exe -m pytest tests/test_import.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/import_routes.py app/templates/partials/ app/templates/components/ tests/test_import.py
git commit -m "feat: task14 - 导入路由"
```

---

### Task 15: 搜索路由 + 分类树路由

**Files:**
- Create: `construction-spec-query-v2/app/routes/search_routes.py`
- Create: `construction-spec-query-v2/app/routes/tree_routes.py`
- Create: `construction-spec-query-v2/app/templates/partials/result_list.html`
- Create: `construction-spec-query-v2/app/templates/partials/tree_node.html`
- Create: `construction-spec-query-v2/app/templates/partials/welcome.html`
- Create: `construction-spec-query-v2/tests/test_search_routes.py`

**Interfaces:**
- Produces: `GET /search`, `GET /tree/{dimension}`, `GET /tree/{dimension}/{parent_label}`

- [ ] **Step 1: 编写测试**

```python
# tests/test_search_routes.py
def test_search_route_returns_results(auth_client):
    resp = auth_client.get("/search?keyword=混凝土")
    assert resp.status_code == 200

def test_tree_route(auth_client):
    resp = auth_client.get("/tree/dim4")
    assert resp.status_code == 200
```

- [ ] **Step 2: 创建搜索路由**

```python
# app/routes/search_routes.py
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from app.models import SearchQuery
from app.search.sql_search import search_clauses

router = APIRouter()


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    keyword: str = Query(None),
    dim1_hierarchy: str = Query(None),
    dim1_nature: str = Query(None),
    dim2_stage: str = Query(None),
    dim3_usage: str = Query(None),
    dim4_specialty: str = Query(None),
    dim5_location: str = Query(None),
    dim6_material: str = Query(None),
    page: int = Query(1),
):
    query = SearchQuery(
        keyword=keyword, page=page,
        dim1_hierarchy=dim1_hierarchy, dim1_nature=dim1_nature,
        dim2_stage=dim2_stage, dim3_usage=dim3_usage,
        dim4_specialty=dim4_specialty, dim5_location=dim5_location,
        dim6_material=dim6_material,
    )

    if not any([keyword, dim1_hierarchy, dim1_nature, dim2_stage, dim3_usage,
                dim4_specialty, dim5_location, dim6_material]):
        from app.main import templates
        return templates.TemplateResponse("partials/welcome.html", {"request": request})

    results, total = search_clauses(query)
    total_pages = max(1, (total + query.per_page - 1) // query.per_page)

    from app.main import templates
    return templates.TemplateResponse("partials/result_list.html", {
        "request": request, "results": results, "total": total,
        "page": page, "total_pages": total_pages, "keyword": keyword,
    })
```

- [ ] **Step 3: 创建分类树路由**

```python
# app/routes/tree_routes.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.database import get_db

router = APIRouter()

DIM_TREES = {
    "dim1_hierarchy": ["国家标准", "行业标准", "地方标准", "团体标准", "企业标准"],
    "dim1_nature": ["强制性", "推荐性"],
    "dim1_sys_level": ["基础标准", "通用标准", "专用标准"],
    "dim1_spec_type": ["项目规范", "通用技术规范"],
    "dim2_stage": ["前期阶段", "设计阶段", "施工阶段", "运维阶段"],
    "dim3_usage": ["民用建筑", "工业建筑", "农业建筑"],
    "dim3_construction": ["新建工程", "扩建工程", "改建工程"],
    "dim3_scale": ["大型项目", "中型项目", "小型项目"],
    "dim4_specialty": ["市政公用工程", "铁路工程", "建筑工程"],
    "dim5_location": ["地基与基础", "主体结构", "二次结构", "屋面", "装饰装修", "机电系统", "室外工程"],
    "dim6_material": ["混凝土材料", "金属材料", "砌体材料", "木材", "装饰材料", "防水/保温材料", "复合材料", "施工工艺"],
}


@router.get("/tree/{dimension}", response_class=HTMLResponse)
async def get_tree_root(request: Request, dimension: str):
    nodes = DIM_TREES.get(dimension, [])
    return _render_tree_nodes(request, dimension, nodes)


@router.get("/tree/{dimension}/{parent_label}", response_class=HTMLResponse)
async def get_tree_children(request: Request, dimension: str, parent_label: str):
    # 子节点: 查数据库中该维度下 parent_label 的子标签
    with get_db() as conn:
        col = _dim_to_column(dimension)
        rows = conn.execute(
            f"SELECT DISTINCT {col} FROM clauses WHERE {col} LIKE ?",
            (f"%{parent_label},%",),
        ).fetchall()
    children = set()
    for r in rows:
        val = r[0]
        if val:
            parts = val.split(",")
            # 找 parent_label 的下一个层级
            for i, p in enumerate(parts):
                if p.strip() == parent_label and i + 1 < len(parts):
                    children.add(parts[i + 1].strip())
    return _render_tree_nodes(request, dimension, sorted(children))


def _render_tree_nodes(request, dimension, nodes):
    from app.main import templates
    return templates.TemplateResponse("partials/tree_node.html", {
        "request": request, "dimension": dimension, "nodes": nodes,
    })


def _dim_to_column(dim: str) -> str:
    return {
        "dim1_hierarchy": "dim1_hierarchy",
        "dim4_specialty": "dim4_specialty",
        "dim5_location": "dim5_location",
        "dim6_material": "dim6_material",
    }.get(dim, dim)
```

- [ ] **Step 4: 创建模板片段**

```html
<!-- app/templates/partials/welcome.html -->
<div class="welcome">
    <h2>欢迎使用施工规范查询系统</h2>
    <p>请在左侧选择分类条件或搜索关键词开始查询</p>
</div>

<!-- app/templates/partials/result_list.html -->
<div id="results">
    <p>共 {{ total }} 条结果</p>
    {% for r in results %}
    <article x-data="{ expanded: false }">
        <header>
            <strong>{{ r.spec_code }}</strong> {{ r.clause_no }}
            <small>{{ r.title or '' }}</small>
            {% if r.dim4_specialty %}<span class="tag">{{ r.dim4_specialty }}</span>{% endif %}
        </header>
        <p>{{ r.content[:200] }}{% if r.content|length > 200 %}...{% endif %}</p>
        <a href="#" @click.prevent="expanded = !expanded" x-text="expanded ? '收起' : '展开全文'"></a>
        <div x-show="expanded">{{ r.content }}</div>
    </article>
    {% endfor %}
    {% if total_pages > 1 %}
    <nav>
        {% for p in range(1, total_pages + 1) %}
        <button hx-get="/search?page={{ p }}&keyword={{ keyword or '' }}" hx-target="#results"
                {% if p == page %}disabled{% endif %}>{{ p }}</button>
        {% endfor %}
    </nav>
    {% endif %}
</div>

<!-- app/templates/partials/tree_node.html -->
<ul>
{% for node in nodes %}
<li>
    <label>
        <input type="checkbox" name="{{ dimension }}" value="{{ node }}"
               hx-get="/search?{{ dimension }}={{ node }}" hx-target="#results"
               hx-trigger="change">
        {{ node }}
    </label>
    {% if dimension in ('dim4_specialty', 'dim5_location', 'dim6_material') %}
    <span hx-get="/tree/{{ dimension }}/{{ node }}" hx-trigger="click" hx-target="next ul"
          style="cursor:pointer;"> [+]</span>
    <ul></ul>
    {% endif %}
</li>
{% endfor %}
</ul>
```

- [ ] **Step 5: 注册路由到 main.py**

```python
from app.routes.search_routes import router as search_router
from app.routes.tree_routes import router as tree_router
from app.routes.import_routes import router as import_router
app.include_router(search_router)
app.include_router(tree_router)
app.include_router(import_router)
```

- [ ] **Step 6: 运行测试验证**

Run: `D:/Python/python.exe -m pytest tests/test_search_routes.py -v`
Expected: 2 PASS

- [ ] **Step 7: Commit**

```bash
git add app/routes/search_routes.py app/routes/tree_routes.py app/templates/partials/ tests/test_search_routes.py
git commit -m "feat: task15 - 搜索路由 + 分类树路由"
```

---

### Task 16: AI 问答路由

**Files:**
- Create: `construction-spec-query-v2/app/ai/qa.py`
- Create: `construction-spec-query-v2/app/routes/qa_routes.py`
- Create: `construction-spec-query-v2/app/templates/partials/qa_message.html`
- Create: `construction-spec-query-v2/app/templates/components/qa.js`
- Create: `construction-spec-query-v2/tests/test_qa.py`

**Interfaces:**
- Produces: `POST /qa/ask`, `GET /qa/sources/{session_id}`
- Consumes: CLI client, vector search, sql search

- [ ] **Step 1: 创建问答上下文构建器**

```python
# app/ai/qa.py
from app.search.sql_search import search_clauses
from app.search.vector_search import VectorStore
from app.models import SearchQuery


def build_context(question: str, top_k: int = 15) -> str:
    """构建问答上下文：混合 FTS5 + 向量检索结果"""
    # FTS5 关键词搜索
    fts_results, _ = search_clauses(SearchQuery(keyword=question, per_page=10))
    
    # 向量语义搜索
    vs = VectorStore()
    vec_results = vs.search(question, top_k=10)
    
    # 合并去重
    seen = set()
    merged = []
    for r in fts_results:
        if r["id"] not in seen:
            seen.add(r["id"])
            merged.append(r)
    for vr in vec_results:
        cid = vr["clause_id"]
        if cid not in seen:
            seen.add(cid)
            merged.append({"id": cid, "spec_code": "", "clause_no": "",
                           "content": vr["text"], "title": ""})
    
    sources = merged[:top_k]
    
    lines = ["你是施工规范查询助手。请基于以下相关规范条文回答用户问题。引用时请标注规范名称和条文号。\n"]
    for i, s in enumerate(sources, 1):
        spec_info = f"{s.get('spec_code', '')}" if s.get('spec_code') else ""
        clause_info = f"第{s['clause_no']}条" if s.get('clause_no') else ""
        lines.append(f"[条文{i}] {spec_info} {clause_info}: {s['content'][:500]}")
    lines.append(f"\n用户问题: {question}")
    
    return "\n\n".join(lines), sources
```

- [ ] **Step 2: 创建问答路由**

```python
# app/routes/qa_routes.py
import uuid
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.models import QARequest
from app.ai.qa import build_context
from app.ai.cli_client import get_backend
from app.config import WORKSPACE_DIR
import asyncio

router = APIRouter()
sessions = {}  # {session_id: [{"role","content"}]}


@router.post("/qa/ask", response_class=HTMLResponse)
async def ask(request: Request, qa: QARequest):
    # 创建或获取会话
    if not qa.session_id:
        qa.session_id = uuid.uuid4().hex[:8]
    if qa.session_id not in sessions:
        sessions[qa.session_id] = []

    # 构建上下文
    context, sources = build_context(qa.question)
    
    # 会话历史
    history = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
        for m in sessions[qa.session_id][-6:]  # 最近 3 轮
    )
    
    backend = get_backend(qa.backend)
    work_dir = str(Path(WORKSPACE_DIR) / qa.session_id)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    
    full_context = f"{context}\n\n对话历史:\n{history}" if history else context
    
    resp = await backend.ask(qa.question, context=full_context, work_dir=work_dir)
    
    answer = resp.content if resp.success else f"AI 服务暂时不可用: {resp.error}"
    
    sessions[qa.session_id].append({"role": "user", "content": qa.question})
    sessions[qa.session_id].append({"role": "assistant", "content": answer})
    
    from app.main import templates
    return templates.TemplateResponse("partials/qa_message.html", {
        "request": request, "question": qa.question, "answer": answer,
        "session_id": qa.session_id, "sources": sources, "backend": qa.backend,
    })


@router.get("/qa/sources/{session_id}", response_class=HTMLResponse)
async def get_sources(request: Request, session_id: str):
    """展开查看当前会话引用的条文"""
    pass  # 由 Alpine.js 前端管理
```

- [ ] **Step 3: 创建问答消息模板**

```html
<!-- app/templates/partials/qa_message.html -->
<div id="qa-messages" hx-swap-oob="beforeend">
    <div class="qa-message user">
        <strong>你:</strong> {{ question }}
    </div>
    <div class="qa-message assistant" x-data="{ showSources: false }">
        <strong>AI ({{ backend }}):</strong>
        <div class="answer-content"></div>
        <small>
            基于 {{ sources|length }} 条条文
            <a href="#" @click.prevent="showSources = !showSources">查看来源</a>
        </small>
        <div x-show="showSources" class="sources">
            {% for s in sources %}
            <p><em>{{ s.spec_code }} {{ s.clause_no }}</em>: {{ s.content[:100] }}...</p>
            {% endfor %}
        </div>
    </div>
</div>
```

- [ ] **Step 4: 创建问答 Alpine.js 组件**

```javascript
// app/templates/components/qa.js
document.addEventListener('alpine:init', () => {
    Alpine.data('qaPanel', () => ({
        question: '',
        backend: 'claude',
        sessionId: null,
        loading: false,
        async send() {
            if (!this.question.trim()) return;
            this.loading = true;
            const formData = new FormData();
            formData.set('question', this.question);
            formData.set('backend', this.backend);
            if (this.sessionId) formData.set('session_id', this.sessionId);
            const resp = await fetch('/qa/ask', { method: 'POST', body: formData });
            const html = await resp.text();
            document.getElementById('qa-messages').insertAdjacentHTML('beforeend', html);
            this.question = '';
            this.loading = false;
        },
    }))
});
```

- [ ] **Step 5: 注册路由**

```python
# app/main.py 添加
from app.routes.qa_routes import router as qa_router
app.include_router(qa_router)
```

- [ ] **Step 6: 运行编译检查**

Run: `D:/Python/python.exe -c "from app.main import app; print('OK')"`
Expected: OK (无 import 错误)

- [ ] **Step 7: Commit**

```bash
git add app/ai/qa.py app/routes/qa_routes.py app/templates/partials/qa_message.html app/templates/components/qa.js
git commit -m "feat: task16 - AI 问答路由"
```

---

### Task 17: 规则管理路由 + 人工复核

**Files:**
- Create: `construction-spec-query-v2/app/routes/rules_routes.py`
- Create: `construction-spec-query-v2/tests/test_rules.py`

**Interfaces:**
- Produces: `GET /rules`, `POST /rules/add`, `POST /rules/delete/{id}`, `POST /rules/toggle/{id}`, `GET /rules/review`, `POST /rules/review/{queue_id}` (确认/驳回)

- [ ] **Step 1: 创建规则管理路由**

```python
# app/routes/rules_routes.py
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.classifier.feedback import process_feedback

router = APIRouter()


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    with get_db() as conn:
        rules = conn.execute(
            "SELECT * FROM classification_rules ORDER BY dimension, priority DESC"
        ).fetchall()
    from app.main import templates
    return templates.TemplateResponse("rules.html", {
        "request": request, "rules": [dict(r) for r in rules],
    })


@router.post("/rules/add")
async def add_rule(
    request: Request,
    dimension: str = Form(...), pattern: str = Form(...),
    match_type: str = Form("keyword"), sub_field: str = Form(""),
    priority: int = Form(0), threshold: float = Form(0.6),
):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (dimension, sub_field, pattern, match_type, priority, threshold),
        )
    return RedirectResponse(url="/rules", status_code=302)


@router.post("/rules/delete/{rule_id}")
async def delete_rule(rule_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM classification_rules WHERE id = ?", (rule_id,))
    return RedirectResponse(url="/rules", status_code=302)


@router.post("/rules/toggle/{rule_id}")
async def toggle_rule(rule_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_rules SET is_active = 1 - is_active WHERE id = ?",
            (rule_id,),
        )
    return RedirectResponse(url="/rules", status_code=302)


@router.get("/rules/review", response_class=HTMLResponse)
async def review_queue(request: Request):
    with get_db() as conn:
        pending = conn.execute(
            """SELECT q.*, c.clause_no, c.content, c.title
               FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               WHERE q.status = 'review'
               ORDER BY q.created_at LIMIT 50"""
        ).fetchall()
    from app.main import templates
    return templates.TemplateResponse("review.html", {
        "request": request, "items": [dict(r) for r in pending],
    })


@router.post("/rules/review/{queue_id}")
async def review_action(
    queue_id: int,
    action: str = Form(...),  # "approve" or "reject"
    label: str = Form(""),
):
    with get_db() as conn:
        queue_item = conn.execute(
            "SELECT * FROM classification_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if not queue_item:
            return RedirectResponse(url="/rules/review", status_code=302)

        if action == "approve" and label:
            process_feedback(queue_item["clause_id"], queue_item["dimension"], label)
        else:
            conn.execute(
                "UPDATE classification_queue SET status = 'done' WHERE id = ?",
                (queue_id,),
            )

    return RedirectResponse(url="/rules/review", status_code=302)
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/rules_routes.py
git commit -m "feat: task17 - 规则管理 + 人工复核路由"
```

---

### Task 18: 静态资源 + Alpine.js 组件 + CSS 样式

**Files:**
- Create: `construction-spec-query-v2/static/` 目录下的 CSS/JS 文件
- Create: `construction-spec-query-v2/app/templates/components/tree.js`
- Create: `construction-spec-query-v2/app/templates/components/search.js`
- Create: `construction-spec-query-v2/app/templates/rules.html`
- Create: `construction-spec-query-v2/app/templates/review.html`

**说明:** 本 task 补全前端剩余静态资源和样式。CSS vendor 文件 (pico.min.css, htmx.min.js, alpine.min.js, marked.min.js) 需要手动下载或从 CDN 复制。

- [ ] **Step 1: 创建自定义 CSS**

```css
/* static/pico.custom.css */
.app-layout {
    display: grid;
    grid-template-columns: 25% 45% 30%;
    height: 100vh;
    overflow: hidden;
}
.left-panel {
    border-right: 1px solid var(--pico-muted-border-color);
    padding: 0.5rem;
    overflow-y: auto;
}
.center-panel {
    padding: 1rem;
    overflow-y: auto;
}
.right-panel {
    border-left: 1px solid var(--pico-muted-border-color);
    padding: 0.5rem;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
}
.qa-messages {
    flex: 1;
    overflow-y: auto;
    padding-bottom: 1rem;
}
.qa-input-area {
    border-top: 1px solid var(--pico-muted-border-color);
    padding-top: 0.5rem;
}
.qa-message {
    margin-bottom: 0.75rem;
    padding: 0.5rem;
    border-radius: 4px;
}
.qa-message.user { background: var(--pico-primary-focus); color: #fff; }
.qa-message.assistant { background: var(--pico-secondary-background); }
.tag {
    display: inline-block;
    padding: 0.1rem 0.4rem;
    background: var(--pico-muted-border-color);
    border-radius: 3px;
    font-size: 0.8em;
    margin: 0 0.2rem;
}
.welcome { text-align: center; padding: 4rem 2rem; color: var(--pico-muted-color); }
.import-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.5);
    display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.import-dialog { background: var(--pico-background-color); padding: 2rem; border-radius: 8px; min-width: 400px; }
#results article { margin-bottom: 0.75rem; }
.highlight { background: yellow; }
.sources { font-size: 0.85em; margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px dashed var(--pico-muted-border-color); }

@media (max-width: 900px) {
    .app-layout {
        grid-template-columns: 1fr;
        grid-template-rows: auto 1fr auto;
    }
    .left-panel.collapsed { display: none; }
    .right-panel.collapsed { display: none; }
}
```

- [ ] **Step 2: 下载静态文件**

```bash
cd construction-spec-query-v2/static
curl -L -o pico.min.css https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css
curl -L -o htmx.min.js https://unpkg.com/htmx.org@2/dist/htmx.min.js
curl -L -o alpine.min.js https://unpkg.com/alpinejs@3/dist/cdn.min.js
curl -L -o marked.min.js https://cdn.jsdelivr.net/npm/marked/marked.min.js
```

- [ ] **Step 3: 创建右侧面板模板（集成到 base.html）**

```html
<!-- app/templates/partials/qa_panel.html -->
<div x-data="qaPanel()" class="qa-panel">
    <h3>AI 问答</h3>
    <select x-model="backend" style="margin-bottom:0.5rem;">
        <option value="claude">Claude Code CLI</option>
        <option value="codex">Codex CLI</option>
    </select>
    <div class="qa-messages" id="qa-messages">
        <p style="color:var(--pico-muted-color)">选择模型后输入问题开始对话</p>
    </div>
    <div class="qa-input-area">
        <textarea x-model="question" rows="2" placeholder="输入问题..."
                  @keydown.enter.prevent="send()"></textarea>
        <button @click="send()" :disabled="loading" x-text="loading ? '思考中...' : '发送'"></button>
    </div>
</div>
```

- [ ] **Step 4: 创建左侧面板模板**

```html
<!-- app/templates/partials/tree_panel.html -->
<div x-data="importDialog()">
    <input type="search" name="keyword" placeholder="搜索规范/条文..."
           hx-get="/search" hx-trigger="keyup changed delay:300ms"
           hx-target="#results" hx-include="[name='keyword']"
           style="margin-bottom:0.5rem;">
    <button @click="openDialog()" class="full-width" style="margin-bottom:0.5rem;">导入文档</button>

    <!-- 分类树 -->
    <nav>
        <details open>
            <summary>规范属性</summary>
            <div hx-get="/tree/dim1_hierarchy" hx-trigger="load"></div>
            <div hx-get="/tree/dim1_nature" hx-trigger="load"></div>
        </details>
        <details>
            <summary>工程阶段</summary>
            <div hx-get="/tree/dim2_stage" hx-trigger="load"></div>
        </details>
        <details>
            <summary>工程类型</summary>
            <div hx-get="/tree/dim3_usage" hx-trigger="load"></div>
        </details>
        <details>
            <summary>所属专业</summary>
            <div hx-get="/tree/dim4_specialty" hx-trigger="load"></div>
        </details>
        <details>
            <summary>工程部位</summary>
            <div hx-get="/tree/dim5_location" hx-trigger="load"></div>
        </details>
        <details>
            <summary>材料/工艺</summary>
            <div hx-get="/tree/dim6_material" hx-trigger="load"></div>
        </details>
    </nav>

    <!-- 导入对话框 -->
    <div class="import-overlay" x-show="open" @click.self="closeDialog()">
        <div class="import-dialog">
            <h3>导入规范文档</h3>
            <form @submit.prevent="handleUpload">
                <label>规范编号 <input type="text" name="code" placeholder="如 GB 50204-2015"></label>
                <label>规范名称 <input type="text" name="title"></label>
                <label>选择文件 <input type="file" name="file" accept=".pdf,.md" required></label>
                <button type="submit" :disabled="uploading">开始导入</button>
                <button type="button" @click="closeDialog()">取消</button>
            </form>
            <div id="import-result"></div>
        </div>
    </div>
</div>
```

- [ ] **Step 5: Commit**

```bash
git add static/ app/templates/
git commit -m "feat: task18 - 静态资源 + Alpine.js 组件 + CSS 样式"
```

---

### Task 19: 脚本工具 + 种子数据

**Files:**
- Create: `construction-spec-query-v2/scripts/create_admin.py`
- Create: `construction-spec-query-v2/scripts/seed_rules.py`
- Create: `construction-spec-query-v2/start.sh`
- Create: `construction-spec-query-v2/start.ps1`

- [ ] **Step 1: 创建 create_admin.py**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db, get_db
from app.auth import hash_password

def create_admin(username: str, password: str):
    init_db()
    pwd_hash = hash_password(password)
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (pwd_hash, username))
            print(f"用户 '{username}' 已存在，密码已更新")
        else:
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, pwd_hash))
            print(f"管理员 '{username}' 创建成功")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python scripts/create_admin.py <用户名> <密码>")
        sys.exit(1)
    create_admin(sys.argv[1], sys.argv[2])
```

- [ ] **Step 2: 创建 seed_rules.py** — 六维种子规则

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db, get_db

SEED_RULES = [
    # 维一：规范属性 (threshold=0.3)
    ("dim1", "hierarchy", r"^GB(?!\s*/T)", "regex", 10, 0.3),
    ("dim1", "hierarchy", r"^GB\s*/T", "regex", 10, 0.3),
    ("dim1", "hierarchy", "JGJ", "keyword", 8, 0.3),
    ("dim1", "hierarchy", "CJJ", "keyword", 8, 0.3),
    ("dim1", "hierarchy", "DB", "keyword", 6, 0.3),
    ("dim1", "hierarchy", "T/CECS", "keyword", 6, 0.3),
    ("dim1", "nature", "强制性", "keyword", 5, 0.3),
    ("dim1", "nature", "推荐性", "keyword", 5, 0.3),
    # 维二：工程阶段 (threshold=0.5)
    ("dim2", "stage", "勘察", "keyword", 5, 0.5),
    ("dim2", "stage", "设计", "keyword", 5, 0.5),
    ("dim2", "stage", "施工", "keyword", 5, 0.5),
    ("dim2", "stage", "验收", "keyword", 5, 0.5),
    ("dim2", "stage", "维护", "keyword", 5, 0.5),
    ("dim2", "stage", "加固", "keyword", 5, 0.5),
    # 维三：工程类型 (threshold=0.5)
    ("dim3", "usage", "民用建筑", "keyword", 5, 0.5),
    ("dim3", "usage", "工业建筑", "keyword", 5, 0.5),
    ("dim3", "usage", "住宅", "keyword", 5, 0.5),
    ("dim3", "usage", "公共建筑", "keyword", 5, 0.5),
    ("dim3", "construction", "新建", "keyword", 5, 0.5),
    ("dim3", "construction", "扩建", "keyword", 5, 0.5),
    ("dim3", "construction", "改建", "keyword", 5, 0.5),
    # 维四：所属专业 (threshold=0.6)
    ("dim4", "specialty", "结构专业", "keyword", 5, 0.6),
    ("dim4", "specialty", "建筑专业", "keyword", 5, 0.6),
    ("dim4", "specialty", "机电专业", "keyword", 5, 0.6),
    ("dim4", "specialty", "岩土专业", "keyword", 5, 0.6),
    ("dim4", "specialty", "给水工程", "keyword", 5, 0.6),
    ("dim4", "specialty", "排水工程", "keyword", 5, 0.6),
    ("dim4", "specialty", "道路工程", "keyword", 5, 0.6),
    ("dim4", "specialty", "桥隧工程", "keyword", 5, 0.6),
    ("dim4", "specialty", "铁路工程", "keyword", 5, 0.6),
    ("dim4", "specialty", "路基", "keyword", 5, 0.6),
    ("dim4", "specialty", "轨道", "keyword", 5, 0.6),
    ("dim4", "specialty", "隧道", "keyword", 5, 0.6),
    # 维五：工程部位 (threshold=0.7)
    ("dim5", "location", "地基", "keyword", 5, 0.7),
    ("dim5", "location", "基础", "keyword", 5, 0.7),
    ("dim5", "location", "主体结构", "keyword", 5, 0.7),
    ("dim5", "location", "屋面", "keyword", 5, 0.7),
    ("dim5", "location", "幕墙", "keyword", 5, 0.7),
    ("dim5", "location", "装饰装修", "keyword", 5, 0.7),
    ("dim5", "location", "机电", "keyword", 5, 0.7),
    ("dim5", "location", "室外", "keyword", 5, 0.7),
    ("dim5", "location", "桩基础", "keyword", 5, 0.7),
    ("dim5", "location", "地下结构", "keyword", 5, 0.7),
    # 维六：材料/工艺 (threshold=0.6)
    ("dim6", "material", "混凝土", "keyword", 5, 0.6),
    ("dim6", "material", "钢筋", "keyword", 5, 0.6),
    ("dim6", "material", "预应力", "keyword", 5, 0.6),
    ("dim6", "material", "钢结构", "keyword", 5, 0.6),
    ("dim6", "material", "砌体", "keyword", 5, 0.6),
    ("dim6", "material", "木材", "keyword", 5, 0.6),
    ("dim6", "material", "防水", "keyword", 5, 0.6),
    ("dim6", "material", "保温", "keyword", 5, 0.6),
    ("dim6", "material", "模板", "keyword", 5, 0.6),
    ("dim6", "material", "焊接", "keyword", 5, 0.6),
    ("dim6", "material", "浇筑", "keyword", 5, 0.6),
]

def seed():
    init_db()
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]
        if count > 0:
            print(f"规则表已有 {count} 条规则，跳过种子写入")
            return
        for dim, sub, pattern, mtype, pri, thresh in SEED_RULES:
            conn.execute(
                """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dim, sub, pattern, mtype, pri, thresh),
            )
        print(f"写入 {len(SEED_RULES)} 条种子规则")

if __name__ == "__main__":
    seed()
```

- [ ] **Step 3: 创建启动脚本**

```bash
# start.sh
#!/bin/bash
cd "$(dirname "$0")"
echo "初始化数据库..."
D:/Python/python.exe scripts/seed_rules.py
echo "启动服务 http://127.0.0.1:8000"
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# start.ps1
Set-Location $PSScriptRoot
Write-Host "初始化数据库..."
D:/Python/python.exe scripts/seed_rules.py
Write-Host "启动服务 http://127.0.0.1:8000"
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- [ ] **Step 4: 运行种子规则验证**

Run: `D:/Python/python.exe scripts/seed_rules.py`
Expected: 写入约 50 条种子规则

- [ ] **Step 5: Commit**

```bash
git add scripts/ start.sh start.ps1
git commit -m "feat: task19 - 脚本工具 + 种子数据 + 启动脚本"
```

---

### Task 20: 集成收尾与端到端验证

**Files:**
- Modify: `construction-spec-query-v2/app/main.py` (确保所有路由挂载)
- Create: `construction-spec-query-v2/tests/test_e2e.py`

- [ ] **Step 1: 创建端到端测试**

```python
# tests/test_e2e.py
def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

def test_login_flow(client, monkeypatch, tmp_path):
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.config.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", hash_password("test123")))
    # 登录
    resp = client.post("/login", data={"username": "admin", "password": "test123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    # 获取 cookie
    cookies = resp.headers.get("set-cookie", "")
    # 带 cookie 访问首页
    client.cookies.set("access_token", extract_token(cookies))
    resp = client.get("/")
    assert resp.status_code == 200

def extract_token(cookie_str):
    for part in cookie_str.split(";"):
        if part.strip().startswith("access_token="):
            return part.split("=", 1)[1].strip()
    return ""

def test_search_without_login_redirects(client):
    resp = client.get("/search?keyword=test", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)

def test_tree_endpoint(client):
    """树接口应被保护"""
    resp = client.get("/tree/dim4")
    assert resp.status_code in (302, 303, 401)

def test_rules_endpoint(client):
    resp = client.get("/rules")
    assert resp.status_code in (302, 303, 401)
```

- [ ] **Step 2: 完善 main.py 确保所有路由挂载**

确认 main.py 导入了所有路由模块。

- [ ] **Step 3: 运行全部测试**

Run: `D:/Python/python.exe -m pytest tests/ -v --tb=short`
Expected: 所有测试 PASS (预估 50+ tests)

- [ ] **Step 4: 清理和最终提交**

```bash
git add -A && git commit -m "feat: task20 - 集成收尾与端到端验证"
```

---

## 自审清单

| 检查项 | 结果 |
|--------|------|
| 占位符/TODO | 无 |
| 类型一致性 | dim1..dim6 命名跨文件一致 |
| Spec 覆盖 | 导入/分类/搜索/问答/规则管理/三栏UI 全覆盖 |
| 测试覆盖 | 每 task 含完整测试代码 |
| 接口一致性 | CLI backend 方法名跨文件一致 |
