# 便携分发包实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出一个完全自包含的便携 zip（内置 Python 3.12 运行时、依赖、模型、脱敏数据），接收方解压双击即可使用。

**Architecture:** 分两阶段。阶段一改应用代码（四项修复），每项独立 TDD、独立提交；阶段二建分发工具链（脱敏脚本、启动器、打包脚本）。启动器用 PyInstaller `--windowed` 打包且**不 import `app`**（避免冻结后 `BASE_DIR` 指向 `_MEIPASS`），只负责起 uvicorn 子进程。

**Tech Stack:** Python 3.12、FastAPI/uvicorn、pytest、PyInstaller ≥6.15、pystray + Pillow、便携完整 CPython。

**Spec:** `docs/superpowers/specs/2026-09-30-distribution-package-design.md`

## Global Constraints

- **分支**：全部工作在 `color` 分支上进行。
- **TDD 铁律**：每个 Task 先写失败测试 → 跑测试确认失败 → 写最小实现 → 跑测试确认通过 → commit。测试必须覆盖**正常 / 边界 / 异常**三类场景。
- **pyright**：每个 Task 完成后跑 pyright，**不得新增 error**。
- **提交规范**：`type: 描述`，type ∈ `feat / fix / test / docs / refactor / chore`，单次提交对应单个 Task。
- **测试的 C-4 坑**：monkeypatch 必须 patch **消费方模块的名字**（如 `app.routes.spec_routes.OUTPUT_DIR`），patch `app.config.*` 无效（import 时值已绑定）。
- **真实存储零增长**：`tests/conftest.py` 的 `_guard_real_stores` 会断言全量套件跑完真实 `lance_db`/`uploads`/`outputs` 零增长。新测试若写这些路径，必须用 `isolated_paths` fixture 或自行 patch。
- **依赖版本**：Python **3.12**；`torch` 用 **CPU 版**。
- **不改动**：`app/ai/embedding.py`、`app/ai/reranker.py` 的模型加载逻辑（本次不涉及）。
- **运行命令前缀**：项目根目录执行，Python 用 `D:/Python/python.exe`。

---

## 文件结构

**新建：**

| 路径 | 职责 |
| --- | --- |
| `tests/test_auth_switch.py` | 鉴权开关行为测试 |
| `tests/test_cleanup_orphan_files.py` | 孤儿判定纯函数测试 |
| `tests/test_prepare_distribution.py` | 脱敏脚本断言 |
| `scripts/cleanup_orphan_files.py` | 一次性孤儿清理（dry-run 默认） |
| `scripts/prepare_distribution.py` | 生成脱敏库副本 |
| `scripts/build_distribution.py` | 组装分发包目录并打 zip |
| `launcher/launcher.py` | 启动器主体（自检 → 起服务 → 开浏览器 → 托盘） |
| `launcher/build_launcher.sh` | PyInstaller 构建启动器命令 |
| `tests/test_launcher.py` | 启动器核心逻辑测试（端口探测 / 就绪等待 / 自检 / import 无副作用） |

**修改：**

| 路径 | 改动 |
| --- | --- |
| `app/config.py` | 新增 `AUTH_ENABLED` |
| `app/main.py:132-151` | 中间件加开关短路 |
| `app/routes/spec_routes.py:150-172` | 图片路由加路径回退 |
| `app/routes/import_routes.py:583-599` | `_copy_ocr_images` 复制后删源 imgs；`import shutil` 提到模块级 |
| `requirements.txt` | 补 `jieba`、删 `pytest` |
| `tests/test_spec_routes.py` | 追加回退测试 + 两个既有测试补 patch `OUTPUT_DIR` |
| `tests/test_import.py` | 追加 `_copy_ocr_images` 测试 |
| `.env.example` | 新增 `AUTH_ENABLED=1` |

---

## 数据流

**打包数据流**（Task 8）：

```text
本机                                                   分发包（zip）
──────────────────────────────────────────────────     ──────────────────────────
data/spec_query.db ──sanitize()──► 脱敏副本 ──────────┐
lance_db/ ───────────────────────────────────────────┤
data/outputs/{被引用的 5 个目录} ─────────────────────┤
models/BAAI/ ──删与 safetensors 重复的 .bin──►        ├──► dist/规范智能检索与问答系统/
app/ static/ ──排除 __pycache__，**不含 scripts/**──► │      ├── 启动.exe
便携 CPython(DIST_RUNTIME) ──+ 5 个 VC++ DLL──►        │      ├── runtime/
launcher/dist/启动.exe ──────────────────────────────┘      ├── app/  static/
                                                             ├── models/  data/  lance_db/
                                                             ├── .env  (AUTH_ENABLED=0)
                                                             └── zip(level=1) ──► .zip
```

**启动器启动链**（Task 7）：

```text
双击 启动.exe
  │
  ├─ runtime/python.exe 存在？──否──► MessageBox ──► 退出 1
  ├─ 依赖自检 import torch,lancedb,jieba,fitz ──失败──► MessageBox + 日志 ──► 退出 1
  ├─ _free_port(8000)：被占则 +1 递增（最多 50 次）
  │     └─ 50 次全占 ──► RuntimeError ──► [main 兜底捕获] MessageBox ──► 退出 1
  ├─ Popen(uvicorn …) ──► AssignProcessToJobObject（父死子必死，覆盖强杀）
  ├─ _wait_ready(120s) ──超时──► MessageBox ──► terminate 子进程 ──► 退出 1
  ├─ webbrowser.open(127.0.0.1:N)
  └─ pystray 托盘常驻 ──右键「退出」──► terminate ──► 退出 0
       └─ 托盘不可用 ──► proc.wait() 阻塞等待
```

---

# 阶段一：应用代码修复

## Task 1: 鉴权开关（AUTH_ENABLED）

**Files:**
- Modify: `app/config.py`（在 `SECRET_KEY` 定义之后，约第 14 行后）
- Modify: `app/main.py:132-136`（`AuthMiddleware.dispatch` 首部）
- Create: `tests/test_auth_switch.py`

**Interfaces:**
- Produces: `app.config.AUTH_ENABLED: bool` —— 由 `app/main.py` 的中间件读取；`False` 时匿名请求自动获得 `request.state.username = "admin"`，全部 73 处 `log_action` 的用户名随之变为 `admin`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_auth_switch.py`：

```python
"""鉴权开关（AUTH_ENABLED）行为测试"""
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


_seen: dict[str, object] = {}


async def _probe(request):
    """探针端点：把中间件注入的 username 捕获到模块级 dict"""
    _seen["username"] = getattr(request.state, "username", None)
    return PlainTextResponse("ok")


def _wrapped():
    """把 AuthMiddleware 套在一个最小 ASGI 应用外，隔离业务路由"""
    from app.main import AuthMiddleware
    return AuthMiddleware(Starlette(routes=[Route("/probe", _probe)]))


def test_auth_enabled_defaults_on():
    """未设环境变量时默认**开启**（安全默认值）

    分发包靠**包内 .env** 的 `AUTH_ENABLED=0` 关闭，而不是靠改默认值——
    否则现有 11 处「匿名请求应 302 到 /login」的断言会全部失败。
    """
    from app.config import AUTH_ENABLED
    assert AUTH_ENABLED is True


def test_disabled_injects_admin_username(monkeypatch):
    """开关关闭：匿名请求直达，且 username 被注入为 admin"""
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "AUTH_ENABLED", False)
    _seen.clear()

    with TestClient(_wrapped()) as c:
        resp = c.get("/probe")

    assert resp.status_code == 200
    assert _seen["username"] == "admin"


def test_enabled_redirects_anonymous_to_login(monkeypatch):
    """开关打开：无 cookie 仍 302 到 /login（原行为不变）"""
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "AUTH_ENABLED", True)

    with TestClient(_wrapped()) as c:
        resp = c.get("/probe", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_auth_switch.py -v`
Expected: FAIL —— `ImportError: cannot import name 'AUTH_ENABLED' from 'app.config'`

- [ ] **Step 3: 加 config 开关**

`app/config.py`，在 `ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours` 之后插入：

```python
# 鉴权总开关：**默认开启**（安全默认值）。
# 分发包靠**包内 .env** 写 `AUTH_ENABLED=0` 来关闭 —— `app/config.py:7` 已
# `load_dotenv(BASE_DIR / ".env")`，故无需改动默认值即可让分发包免登录，
# 同时不动摇开发机与未来服务器的安全性。
# ⚠ 若把默认值改为 0：现有 **11 处**「匿名请求应被 302 到 /login」的断言
#   会全部失败（tests/test_auth_routes.py、test_import.py、test_lexicon_routes.py、
#   test_qa_routes.py、test_rules_routes.py、test_search_routes.py、
#   test_settings_routes.py）。
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "1") == "1"
```

- [ ] **Step 4: 中间件加短路**

`app/main.py`，把 `AuthMiddleware.dispatch` 的首两行：

```python
    async def dispatch(self, request: Request, call_next):
        public_paths = ["/login", "/static", "/health"]
```

改为：

```python
    async def dispatch(self, request: Request, call_next):
        if not AUTH_ENABLED:
            # 唯一注入点：全项目 73 处 log_action 的用户名都取自 request.state.username
            request.state.username = "admin"
            return await call_next(request)

        public_paths = ["/login", "/static", "/health"]
```

并在 `app/main.py` 顶部 import 区补上 `AUTH_ENABLED`（找到现有的 `from app.config import ...` 行并入；若不存在该行，新增）：

```python
from app.config import AUTH_ENABLED
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_auth_switch.py -v`
Expected: 3 passed

- [ ] **Step 6: 跑鉴权相关的既有测试（确认未破坏原行为）**

Run: `D:/Python/python.exe -m pytest tests/test_auth.py tests/test_auth_routes.py -v`
Expected: 全部 passed

- [ ] **Step 7: pyright**

Run: `pyright app/config.py app/main.py tests/test_auth_switch.py`
Expected: 无新增 error

- [ ] **Step 8: 同步 `.env.example`**

在 `.env.example` 末尾新增一行，让新部署者知道有这个开关：

```
AUTH_ENABLED=1
```

- [ ] **Step 9: Commit**

```bash
git add app/config.py app/main.py tests/test_auth_switch.py .env.example
git commit -m "feat: 鉴权总开关 AUTH_ENABLED（默认开启，分发包用 .env 关闭）"
```

---

## Task 2: 图片路由路径回退

**Files:**
- Modify: `app/routes/spec_routes.py:150-172`
- Test: `tests/test_spec_routes.py`（文件末尾追加）

**Interfaces:**
- Consumes: `app.config.OUTPUT_DIR`（本 Task 新增顶层 import）
- Produces: `/specs/{spec_id}/imgs/{filename}` 在 `spec.output_dir` 失效时回退用 `OUTPUT_DIR / <code>` 查找。响应契约不变（200 返回文件 / 404 JSON）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_spec_routes.py` 末尾追加：

```python
def test_spec_image_falls_back_to_output_dir_by_code(auth_client, monkeypatch, tmp_path):
    """output_dir 路径失效时，回退用 OUTPUT_DIR/<code>/imgs/ 返回图片

    分发给他人后，库中存的 output_dir 绝对路径在本机不存在；
    回退保证 78 张正文图片不裂。
    """
    import app.routes.spec_routes as sr

    db_path = tmp_path / "test_spec_img_fb.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))

    outputs_root = tmp_path / "outputs"
    imgs = outputs_root / "GB-TEST-IMG" / "imgs"
    imgs.mkdir(parents=True)
    (imgs / "a.jpg").write_bytes(b"fb-data")
    monkeypatch.setattr(sr, "OUTPUT_DIR", str(outputs_root))

    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, output_dir) VALUES (?, ?, ?)",
            ("GB-TEST-IMG", "图片规范", str(tmp_path / "nowhere-does-not-exist")),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.get(f"/specs/{spec_id}/imgs/a.jpg")
    assert resp.status_code == 200
    assert resp.content == b"fb-data"


def test_spec_image_prefers_stored_dir_over_fallback(auth_client, monkeypatch, tmp_path):
    """output_dir 有效时优先用它，不回退（原行为不变）"""
    import app.routes.spec_routes as sr

    db_path = tmp_path / "test_spec_img_prio.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))

    outputs_root = tmp_path / "outputs"
    (outputs_root / "GB-TEST-IMG" / "imgs").mkdir(parents=True)
    (outputs_root / "GB-TEST-IMG" / "imgs" / "a.jpg").write_bytes(b"FALLBACK")
    monkeypatch.setattr(sr, "OUTPUT_DIR", str(outputs_root))

    stored_root = tmp_path / "stored"
    (stored_root / "imgs").mkdir(parents=True)
    (stored_root / "imgs" / "a.jpg").write_bytes(b"STORED")

    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, output_dir) VALUES (?, ?, ?)",
            ("GB-TEST-IMG", "图片规范", str(stored_root)),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.get(f"/specs/{spec_id}/imgs/a.jpg")
    assert resp.status_code == 200
    assert resp.content == b"STORED"


def test_spec_image_404_when_both_paths_miss(auth_client, monkeypatch, tmp_path):
    """两处路径都找不到 → 仍 404"""
    import app.routes.spec_routes as sr

    db_path = tmp_path / "test_spec_img_both.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))

    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir()
    monkeypatch.setattr(sr, "OUTPUT_DIR", str(outputs_root))

    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, output_dir) VALUES (?, ?, ?)",
            ("GB-TEST-IMG", "图片规范", str(tmp_path / "nowhere")),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.get(f"/specs/{spec_id}/imgs/nope.jpg")
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_spec_routes.py -k "falls_back or prefers_stored" -v`
Expected: `test_spec_image_falls_back_to_output_dir_by_code` FAIL（404，因当前实现只用 output_dir）；`prefers_stored` PASS（原行为）

- [ ] **Step 3: 加 OUTPUT_DIR import**

`app/routes/spec_routes.py` 的 import 区（`from app.database import get_db` 之后）追加：

```python
from app.config import OUTPUT_DIR
```

- [ ] **Step 4: 改路由实现**

把 `app/routes/spec_routes.py` 中 `spec_image` 的函数体：

```python
    with get_db() as conn:
        row = conn.execute(
            "SELECT output_dir FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()

    if not row or not row["output_dir"]:
        return JSONResponse({"detail": "规范不存在或无图片目录"}, status_code=404)

    # 仅取文件名，防路径穿越
    safe_name = Path(filename).name
    img_path = Path(row["output_dir"]) / "imgs" / safe_name
    if not img_path.is_file():
        return JSONResponse({"detail": "图片不存在"}, status_code=404)
    return FileResponse(str(img_path))
```

替换为：

```python
    with get_db() as conn:
        row = conn.execute(
            "SELECT output_dir, code FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()

    if not row:
        return JSONResponse({"detail": "规范不存在或无图片目录"}, status_code=404)

    # 仅取文件名，防路径穿越
    safe_name = Path(filename).name

    # 候选路径按优先级排列：库中存的 output_dir（本机）→ OUTPUT_DIR/<code>（分发后）。
    # 分享场景下 output_dir 是本机绝对路径，在对方机器上不存在，必须回退，
    # 否则 78 张正文图片全部 404。code 可能含 '/'（如 JTG/T 3610-2019），
    # 拼出的嵌套路径与导入时 _copy_ocr_images 的落盘位置一致。
    candidates: list[Path] = []
    if row["output_dir"]:
        candidates.append(Path(row["output_dir"]) / "imgs" / safe_name)
    if row["code"]:
        candidates.append(Path(OUTPUT_DIR) / row["code"] / "imgs" / safe_name)

    for img_path in candidates:
        if img_path.is_file():
            return FileResponse(str(img_path))

    return JSONResponse({"detail": "图片不存在"}, status_code=404)
```

- [ ] **Step 5: 给两个既有图片测试补 `OUTPUT_DIR` patch**

`tests/test_spec_routes.py` 里 `test_spec_image_404_when_no_output_dir` 与
`test_spec_image_rejects_path_traversal`（若存在）**没有 patch `sr.OUTPUT_DIR`**，
改动后它们会 fall through 到**真实** `data/outputs/` —— 只因 `data/outputs/GB-TEST-IMG/`
恰好不存在才"通过"，属于侥幸，且违反 `conftest.py` 的真实存储零增长守卫。

给这两个测试各补一行（放在 `monkeypatch.setattr("app.database.DATABASE_PATH", ...)` 之后）：

```python
    monkeypatch.setattr("app.routes.spec_routes.OUTPUT_DIR", str(tmp_path / "outputs"))
```

- [ ] **Step 6: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_spec_routes.py -v`
Expected: 全部 passed（含既有的 `test_spec_image_serves_file`、`test_spec_image_404_when_missing`、`test_spec_image_404_when_no_output_dir`）

- [ ] **Step 7: pyright**

Run: `pyright app/routes/spec_routes.py`
Expected: 无新增 error

- [ ] **Step 8: Commit**

```bash
git add app/routes/spec_routes.py tests/test_spec_routes.py
git commit -m "fix: 图片路由增加 OUTPUT_DIR/<code> 回退，修复分发后图片全裂"
```

---

## Task 3: 导入后删除冗余图片源

**Files:**
- Modify: `app/routes/import_routes.py:583-599`
- Test: `tests/test_import.py`（文件末尾追加）

**Interfaces:**
- Consumes: 无
- Produces: `_copy_ocr_images(ocr_dir, out_dir) -> None` 契约不变（仍无返回值）；新增副作用——**复制成功后**删除 `ocr_dir/imgs/`，**保留 `ocr_dir` 下其他文件（尤其 `{task_id}.md`）**。

> ⚠ 保留 `{task_id}.md` 是硬约束：`scripts/reimport_specs.py`（解析器改动后的库级验收工具）依赖它，见 `reimport_specs.py:50-59`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_import.py` 末尾追加：

```python
# ═══════════════════════════════════════════
# _copy_ocr_images 的源清理
# ═══════════════════════════════════════════

def test_copy_ocr_images_removes_source_imgs_keeps_md(tmp_path):
    """复制成功后源 imgs/ 被删，同目录的 {task_id}.md 保留"""
    from app.routes.import_routes import _copy_ocr_images

    ocr_dir = tmp_path / "abc12345"
    (ocr_dir / "imgs").mkdir(parents=True)
    (ocr_dir / "imgs" / "a.jpg").write_bytes(b"img")
    (ocr_dir / "abc12345.md").write_text("正文", encoding="utf-8")
    out_dir = tmp_path / "GB-TEST"

    _copy_ocr_images(ocr_dir, out_dir)

    assert (out_dir / "imgs" / "a.jpg").is_file(), "副本必须存在"
    assert not (ocr_dir / "imgs").exists(), "源 imgs 必须被删"
    assert (ocr_dir / "abc12345.md").is_file(), "md 必须保留（reimport_specs 依赖）"


def test_copy_ocr_images_noop_when_same_dir(tmp_path):
    """未填 code 时 ocr_dir == out_dir：不复制、不删（删了就丢图）"""
    from app.routes.import_routes import _copy_ocr_images

    d = tmp_path / "abc12345"
    (d / "imgs").mkdir(parents=True)
    (d / "imgs" / "a.jpg").write_bytes(b"img")

    _copy_ocr_images(d, d)

    assert (d / "imgs" / "a.jpg").is_file(), "同目录时必须原地保留"


def test_copy_ocr_images_noop_when_source_missing(tmp_path):
    """源 imgs/ 不存在：静默返回，不创建目标目录"""
    from app.routes.import_routes import _copy_ocr_images

    ocr_dir = tmp_path / "abc12345"
    ocr_dir.mkdir()
    out_dir = tmp_path / "GB-TEST"

    _copy_ocr_images(ocr_dir, out_dir)

    assert not (out_dir / "imgs").exists()


def test_copy_ocr_images_keeps_source_when_copy_fails(tmp_path, monkeypatch):
    """copytree 抛异常时源 imgs 必须保留（绝不能删了源却没复制成）"""
    from app.routes import import_routes

    ocr_dir = tmp_path / "abc12345"
    (ocr_dir / "imgs").mkdir(parents=True)
    (ocr_dir / "imgs" / "a.jpg").write_bytes(b"img")
    out_dir = tmp_path / "GB-TEST"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(import_routes.shutil, "copytree", boom)
    with pytest.raises(OSError):
        import_routes._copy_ocr_images(ocr_dir, out_dir)

    assert (ocr_dir / "imgs" / "a.jpg").is_file(), "复制失败时不能删源"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_import.py -k "copy_ocr_images" -v`
Expected: `test_copy_ocr_images_removes_source_imgs_keeps_md` FAIL（`assert not (ocr_dir / "imgs").exists()` —— 当前实现不删源）；另两条 PASS

- [ ] **Step 3: 把 `import shutil` 提到模块级（否则本 Task 的测试跑不起来）**

`app/routes/import_routes.py` 目前 `shutil` 只在**两个函数内部**导入（第 590、1082 行），
模块级 `import shutil` 计数为 0。因此 `monkeypatch.setattr(import_routes.shutil, ...)`
在参数求值时就抛 `AttributeError`，测试根本到不了断言。

在文件顶部的 import 区（`import re` / `import uuid` 附近）新增：

```python
import shutil
```

并删除 `_copy_ocr_images` 内第 590 行的 `import shutil`（`_cleanup_task_artifacts`
内第 1082 行的那处可保留，也可一并删除——同一模块内冗余）。

- [ ] **Step 4: 改实现**

把 `app/routes/import_routes.py` 的 `_copy_ocr_images` 尾部：

```python
    src = ocr_dir / "imgs"
    if not src.is_dir():
        return
    shutil.copytree(src, out_dir / "imgs", dirs_exist_ok=True)
```

替换为：

```python
    src = ocr_dir / "imgs"
    if not src.is_dir():
        return
    shutil.copytree(src, out_dir / "imgs", dirs_exist_ok=True)

    # 复制成功后删除源 imgs/：图片已在 out_dir/imgs/ 有权威副本，留在 {task_id}/imgs/
    # 只会让 outputs 每次导入多一份冗余（实测 33 个目录 / 5.8MB）。
    # ⚠ 只删 imgs/ 子目录，**不删 {task_id}.md** —— scripts/reimport_specs.py 依赖它。
    # ⚠ copytree 抛异常时不会执行到此处，源因此得以保留（不会「删了源却没复制成」）。
    shutil.rmtree(src, ignore_errors=True)
```

> 注意：`if ocr_dir == out_dir: return`（第 594-595 行）在 `src` 赋值**之前**，所以"同目录不删"由它保证，本次改动不影响该分支。

同时在 `_copy_ocr_images` 的 docstring 末尾补一句：

```python
    复制成功后源 imgs/ 会被删除（图片的权威副本在 out_dir/imgs/）；
    {task_id}.md 保留，供 scripts/reimport_specs.py 使用。
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_import.py -v`
Expected: 全部 passed

- [ ] **Step 6: pyright**

Run: `pyright app/routes/import_routes.py`
Expected: 无新增 error

- [ ] **Step 7: Commit**

```bash
git add app/routes/import_routes.py tests/test_import.py
git commit -m "fix: 图片复制到 code 目录后删除 task_id/imgs 冗余源（保留 md）"
```

---

## Task 4: 孤儿文件清理脚本

**Files:**
- Create: `scripts/cleanup_orphan_files.py`
- Create: `tests/test_cleanup_orphan_files.py`

**Interfaces:**
- Produces:
  - `orphan_uploads(upload_dir: Path, referenced: set[str]) -> list[Path]` —— 返回未被引用的上传文件（按**文件名**比对）
  - `orphan_output_dirs(output_root: Path, referenced: set[str]) -> list[Path]` —— 返回未被引用的 outputs **顶层目录**（前缀匹配，防误删 `JTG/` 这类父目录）
- CLI：默认 dry-run 只打印，`--apply` 才真删。

> ⚠ 前缀匹配是硬要求：规范 code 含 `/`（如 `JTG/T 3610-2019`）使 `output_dir` 形成嵌套，若按顶层目录名比对，`JTG/` 会被误判为孤儿而删掉其下的合法目录。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_cleanup_orphan_files.py`：

```python
"""孤儿文件判定纯函数测试（不触碰真实存储）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cleanup_orphan_files import orphan_output_dirs, orphan_uploads


def test_orphan_uploads_keeps_referenced(tmp_path):
    """被引用的上传文件保留，未引用的列入孤儿"""
    (tmp_path / "keep.pdf").write_bytes(b"a")
    (tmp_path / "drop1.pdf").write_bytes(b"b")
    (tmp_path / "drop2.md").write_bytes(b"c")

    result = orphan_uploads(tmp_path, {"keep.pdf"})

    assert sorted(p.name for p in result) == ["drop1.pdf", "drop2.md"]


def test_orphan_uploads_empty_dir(tmp_path):
    """空目录返回空列表（边界）"""
    assert orphan_uploads(tmp_path, set()) == []


def test_orphan_output_dirs_prefix_match_protects_nested(tmp_path):
    """前缀匹配：JTG/ 是 JTG/T 3610-2019 的父目录，必须保留"""
    (tmp_path / "JTG" / "T 3610-2019").mkdir(parents=True)
    (tmp_path / "JTG" / "T 3610-2019" / "x.png").write_bytes(b"i")
    (tmp_path / "CJJ 2-2008").mkdir()
    (tmp_path / "stale123").mkdir()
    (tmp_path / "stale123" / "y.png").write_bytes(b"j")

    referenced = {str(tmp_path / "JTG" / "T 3610-2019"), str(tmp_path / "CJJ 2-2008")}
    result = orphan_output_dirs(tmp_path, referenced)

    assert [p.name for p in result] == ["stale123"]


def test_orphan_output_dirs_never_deletes_dirs_with_md(tmp_path):
    """含 .md 的目录必须保留 —— reimport_specs.py 依赖它（与 Task 3 同一条约束）"""
    d = tmp_path / "abc12345"
    d.mkdir()
    (d / "abc12345.md").write_text("正文", encoding="utf-8")
    (d / "imgs").mkdir()
    (d / "imgs" / "a.jpg").write_bytes(b"i")

    assert orphan_output_dirs(tmp_path, set()) == []


def test_orphan_output_dirs_handles_backslash_refs(tmp_path):
    """库中 output_dir 存的是反斜杠绝对路径时仍能正确匹配"""
    nested = tmp_path / "JTG" / "T 3610-2019"
    nested.mkdir(parents=True)
    victim = tmp_path / "orphan9"
    victim.mkdir()

    # Windows 风格的反斜杠路径 + 大小写差异
    referenced = {str(nested).replace("/", "\\").upper()}
    result = orphan_output_dirs(tmp_path, referenced)

    assert [p.name for p in result] == ["orphan9"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_cleanup_orphan_files.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.cleanup_orphan_files'`

- [ ] **Step 3: 实现脚本**

创建 `scripts/cleanup_orphan_files.py`：

```python
"""清理 uploads/ 与 outputs/ 中未被 specifications 引用的孤儿文件

孤儿来源：
  1. 判重功能上线前的重复上传（历史遗留）
  2. 上传后 OCR 失败 / 中途关页面 → 任务中断，{task_id}.pdf 残留
  3. 导入确认时图片被复制到 outputs/{code}/ 后，旧的 outputs/{task_id}/ 残留
     （修复 2 已堵住新增，本脚本清存量）

用法：
    D:/Python/python.exe scripts/cleanup_orphan_files.py            # dry-run，只打印
    D:/Python/python.exe scripts/cleanup_orphan_files.py --apply    # 真正删除

⚠ 必须用 --apply 才落盘；默认 dry-run。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.config import OUTPUT_DIR, UPLOAD_DIR  # noqa: E402
from app.database import get_db  # noqa: E402


def _norm(p: str) -> str:
    """统一成小写正斜杠形式，消除 Windows 反斜杠与大小写差异"""
    return str(p).replace("\\", "/").lower()


def orphan_uploads(upload_dir: Path, referenced: set[str]) -> list[Path]:
    """返回 uploads 中未被引用的文件（按文件名比对）"""
    if not upload_dir.is_dir():
        return []
    return [
        p for p in upload_dir.iterdir()
        if p.is_file() and p.name not in referenced
    ]


def orphan_output_dirs(output_root: Path, referenced: set[str]) -> list[Path]:
    """返回 outputs 中未被引用的顶层目录

    前缀匹配：某目录本身被引用，或其下任一子路径被引用，都视为保留
    （规范 code 含 '/' 时 output_dir 会形成 JTG/T 3610-2019 这样的嵌套）。

    ⚠ **含 `.md` 的目录一律不删** —— `scripts/reimport_specs.py:50-54` 从
    `outputs/{task_id}/{task_id}.md` 取重导用的 md。实测 `data/outputs/` 下有
    **10 个未被引用的 task_id 目录含 md**，其中 4 个正是已入库规范的 task_id。
    删掉它们 = 断掉重导输入，与 Task 3 的约束直接矛盾。
    """
    if not output_root.is_dir():
        return []
    refs = {_norm(r) for r in referenced}
    orphan = []
    for d in output_root.iterdir():
        if not d.is_dir():
            continue
        if any(d.glob("*.md")):
            continue                      # 重导命脉，永不删
        key = _norm(d)
        if any(r == key or r.startswith(key + "/") for r in refs):
            continue
        orphan.append(d)
    return orphan


def _size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        else:
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="真正删除（默认 dry-run，只打印）")
    ap.add_argument("--uploads-dir", default=str(UPLOAD_DIR),
                    help="uploads 路径（默认取 app.config）")
    ap.add_argument("--outputs-dir", default=str(OUTPUT_DIR),
                    help="outputs 路径（默认取 app.config）")
    args = ap.parse_args()

    # 路径可传入：既能指向副本做演练，也能避免误伤真实存储
    apply = args.apply
    up_dir = Path(args.uploads_dir)
    out_root = Path(args.outputs_dir)

    with get_db() as conn:
        upload_names = {
            Path(r["source_path"]).name
            for r in conn.execute(
                "SELECT source_path FROM specifications WHERE source_path IS NOT NULL")
        }
        output_refs = {
            r["output_dir"] for r in conn.execute(
                "SELECT output_dir FROM specifications WHERE output_dir IS NOT NULL")
        }

    orphans_u = orphan_uploads(up_dir, upload_names)
    orphans_o = orphan_output_dirs(out_root, output_refs)

    print(f"uploads 孤儿：{len(orphans_u)} 个，{_size(orphans_u)/1048576:.1f} MB")
    for p in sorted(orphans_u)[:20]:
        print(f"    {p.name}")
    if len(orphans_u) > 20:
        print(f"    ... 另有 {len(orphans_u)-20} 个")

    print(f"outputs 孤儿：{len(orphans_o)} 个，{_size(orphans_o)/1048576:.1f} MB")
    for p in sorted(orphans_o)[:20]:
        print(f"    {p.name}")
    if len(orphans_o) > 20:
        print(f"    ... 另有 {len(orphans_o)-20} 个")

    total_mb = (_size(orphans_u) + _size(orphans_o)) / 1048576
    print(f"\n合计可回收：{total_mb:.1f} MB")

    if not apply:
        print("（dry-run，未删除。加 --apply 才真正删除）")
        return 0

    import shutil
    for p in orphans_u:
        p.unlink(missing_ok=True)
    for p in orphans_o:
        shutil.rmtree(p, ignore_errors=True)
    print(f"已删除：{len(orphans_u)} 个文件 + {len(orphans_o)} 个目录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_cleanup_orphan_files.py -v`
Expected: 4 passed

- [ ] **Step 5: pyright**

Run: `pyright scripts/cleanup_orphan_files.py`
Expected: 无新增 error

- [ ] **Step 6: Commit**

```bash
git add scripts/cleanup_orphan_files.py tests/test_cleanup_orphan_files.py
git commit -m "feat: 孤儿文件清理脚本（dry-run 默认，前缀匹配防误删嵌套目录）"
```

- [ ] **Step 7: 在真实库上跑 dry-run（只读，不改动）**

Run: `D:/Python/python.exe scripts/cleanup_orphan_files.py`
Expected: 打印约 `uploads 孤儿：36 个，42.3 MB` / `outputs 孤儿：33 个，5.8 MB` / `合计约 48 MB`。
**注意：此步只跑 dry-run。真正 `--apply` 等阶段二脱敏前再做（届时操作副本）。**

---

# 阶段二：分发工具链

## Task 5: 依赖修正与便携运行时构建

**Files:**
- Modify: `requirements.txt`
- 构建产物（不入 git）：`D:/pyruntime312/` —— 完整便携 CPython 3.12 + 依赖

**Interfaces:**
- Produces: `D:/pyruntime312/` —— Task 7 的启动器自检与 Task 8 的 `_copy_runtime()` 都以此为 runtime 来源。

- [ ] **Step 1: 改 requirements.txt**

把 `requirements.txt` 中：

```
pytest>=8.0.0
```

删除，并在 `jinja2>=3.1.0` 之后新增一行：

```
jieba>=0.42.1
```

改完后文件应为：

```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
python-multipart>=0.0.18
python-dotenv>=1.0.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
bcrypt==4.2.1
aiofiles>=24.0.0
PyMuPDF>=1.24.0
lancedb>=0.17.0
sentence-transformers>=3.0.0
pyarrow>=17.0.0
httpx>=0.28.0
jinja2>=3.1.0
jieba>=0.42.1
```

- [ ] **Step 2: 安装一份干净的便携 CPython 3.12**

> ⚠ **不能用 `venv`**：venv 只含 `site-packages` 与一个 `python.exe`，**标准库仍在 base Python 目录**（靠 `pyvenv.cfg` 指过去）。直接用 venv 当运行时打包，对方机器上会因找不到标准库而启动失败。
> ⚠ 也不能用本机现成的 `D:\Python312`：它已装了 paddleocr 全家桶（102 个包），不干净。

从 python.org 静默安装一份独立副本到构建目录：

```bash
curl -L -o /tmp/python-3.12.10-amd64.exe \
  https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
/tmp/python-3.12.10-amd64.exe /quiet InstallAllUsers=0 \
  TargetDir=D:/pyruntime312 Include_launcher=0 Include_test=0 Include_doc=0
```

Expected: `D:/pyruntime312/python.exe` 存在，且 `D:/pyruntime312/Lib/` 下有标准库。

- [ ] **Step 3: 装依赖**

```bash
D:/pyruntime312/python.exe -m pip install --upgrade pip
D:/pyruntime312/python.exe -m pip install -r requirements.txt
```

若 `torch` 拉到了 CUDA 版（体积 2GB+），改装 CPU 版：

```bash
D:/pyruntime312/python.exe -m pip install --force-reinstall torch \
  --index-url https://download.pytorch.org/whl/cpu
```

- [ ] **Step 4: 验证依赖齐备（本 Task 的核心断言）**

Run: `D:/pyruntime312/python.exe -c "import torch, lancedb, jieba, fitz, fastapi, sentence_transformers; print('deps ok')"`
Expected: 打印 `deps ok`（无 ImportError）

- [ ] **Step 5: 验证应用能起**

Run: `D:/pyruntime312/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011`（后台）
然后 `curl -s http://127.0.0.1:8011/health`
Expected: `{"status":"ok"}`

- [ ] **Step 6: 记录体积基线**

Run: `du -sh /d/pyruntime312`
把结果记入本计划末尾「实测记录」（供 Task 8 估算包体积）。

- [ ] **Step 7: Commit**

```bash
git add requirements.txt
git commit -m "fix: requirements 补 jieba（缺失会致检索崩溃）、删 pytest（生产不需要）"
```

> 注：`D:/pyruntime312` **不入 git**（在 D 盘根、且是构建产物）。Task 8 直接把它作为 runtime 来源。

---

## Task 6: 脱敏脚本

**Files:**
- Create: `scripts/prepare_distribution.py`
- Create: `tests/test_prepare_distribution.py`

**Interfaces:**
- 输入：源库路径、输出库路径、`--admin-password`（默认 `admin`）
- Produces: 一份可直接入包的脱敏库 + 随机 `SECRET_KEY` 文本（写入 stdout 供打包脚本捕获，或由打包脚本另建 `.env`）。

> ⚠ **必须在副本上操作**：脚本的 `src` 与 `dst` 必须不同，且拒绝 `src == dst`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_prepare_distribution.py`：

```python
"""脱敏脚本断言（在临时副本上操作，不触碰真实库）"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.prepare_distribution import sanitize


def _make_src(path: Path) -> None:
    """造一个含敏感数据的最小库"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE system_logs (id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE qa_request_logs (id INTEGER PRIMARY KEY, question TEXT);
        CREATE TABLE health_check_snapshots (id INTEGER PRIMARY KEY);
        CREATE TABLE rule_pending (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE classification_queue (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE specifications (id INTEGER PRIMARY KEY, code TEXT, source_path TEXT, output_dir TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT);
        CREATE TABLE clauses (id INTEGER PRIMARY KEY, content TEXT);
    """)
    conn.execute("INSERT INTO settings VALUES ('ai.deepseek.api_key','sk-SECRET')")
    conn.execute("INSERT INTO settings VALUES ('ai.custom.base_url','https://internal.example')")
    conn.execute("INSERT INTO settings VALUES ('ocr.paddle-vl.access_token','tok-SECRET')")
    conn.execute("INSERT INTO settings VALUES ('ai.backend','deepseek')")
    conn.execute("INSERT INTO system_logs VALUES (1,'admin')")
    conn.execute("INSERT INTO qa_request_logs VALUES (1,'我提的问题')")
    conn.execute("INSERT INTO health_check_snapshots VALUES (1)")
    conn.execute("INSERT INTO rule_pending VALUES (1,'approved')")
    conn.execute("INSERT INTO rule_pending VALUES (2,'rejected')")
    conn.execute("INSERT INTO rule_pending VALUES (3,'pending')")
    conn.execute("INSERT INTO classification_queue VALUES (1,'review')")
    conn.execute("INSERT INTO classification_queue VALUES (2,'ai_processing')")
    conn.execute("INSERT INTO specifications VALUES (1,'GB 1','D:\\\\src\\\\a.pdf','D:\\\\out\\\\GB 1')")
    conn.execute("INSERT INTO users VALUES (1,'admin','$2b$OLDHASH')")
    conn.execute("INSERT INTO clauses VALUES (1,'正文')")
    conn.commit()
    conn.close()


def test_sanitize_clears_secrets_and_logs(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _make_src(src)

    sanitize(src, dst, admin_password="newpass")

    conn = sqlite3.connect(dst)
    # 密钥与内网地址被清空
    for key in ("ai.deepseek.api_key", "ai.custom.base_url", "ocr.paddle-vl.access_token"):
        v = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]
        assert v == "", f"{key} 未清空：{v!r}"
    # 后端选择保留
    assert conn.execute("SELECT value FROM settings WHERE key='ai.backend'").fetchone()[0] == "deepseek"
    # 日志类表清空
    for t in ("system_logs", "qa_request_logs", "health_check_snapshots"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n == 0, f"{t} 未清空，剩 {n} 行"
    conn.close()


def test_sanitize_filters_rule_pending_by_status(tmp_path):
    src = tmp_path / "src2.db"
    dst = tmp_path / "dst2.db"
    _make_src(src)

    sanitize(src, dst, admin_password="newpass")

    conn = sqlite3.connect(dst)
    statuses = {r[0] for r in conn.execute("SELECT status FROM rule_pending")}
    assert statuses == {"approved", "rejected"}, "pending 应被删，approved/rejected 应保留"
    conn.close()


def test_sanitize_filters_classification_queue(tmp_path):
    src = tmp_path / "src3.db"
    dst = tmp_path / "dst3.db"
    _make_src(src)

    sanitize(src, dst, admin_password="newpass")

    conn = sqlite3.connect(dst)
    statuses = {r[0] for r in conn.execute("SELECT status FROM classification_queue")}
    assert statuses == {"review"}, "ai_processing 应被删，review 应保留"
    conn.close()


def test_sanitize_resets_paths_and_password(tmp_path):
    src = tmp_path / "src4.db"
    dst = tmp_path / "dst4.db"
    _make_src(src)

    sanitize(src, dst, admin_password="newpass")

    conn = sqlite3.connect(dst)
    src_path = conn.execute("SELECT source_path FROM specifications").fetchone()[0]
    assert src_path == "a.pdf", "source_path 应只留文件名（保住 reimport_specs 的 stem 反推）"
    assert "/" not in src_path and "\\" not in src_path, "不得残留路径分隔符"
    out_dir = conn.execute("SELECT output_dir FROM specifications").fetchone()[0]
    assert out_dir == "D:\\out\\GB 1", "output_dir 必须保留（图片路由依赖）"
    pw = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
    assert pw != "$2b$OLDHASH", "旧密码哈希必须被替换"
    conn.close()


def test_sanitize_refuses_same_path(tmp_path):
    """异常场景：src == dst 必须拒绝（防误伤生产库）"""
    src = tmp_path / "same.db"
    _make_src(src)
    try:
        sanitize(src, src, admin_password="x")
    except ValueError as e:
        assert "不能相同" in str(e)
    else:
        raise AssertionError("src == dst 必须抛 ValueError")


def test_sanitize_new_password_can_log_in(tmp_path):
    """重置后的 admin 密码必须能通过 verify_password（设计文档 §8 明确要求）

    只断言「哈希变了」不够——哈希写成不可验证的值同样能通过。必须验证真能登录。
    """
    src = tmp_path / "src5.db"
    dst = tmp_path / "dst5.db"
    _make_src(src)

    sanitize(src, dst, admin_password="newpass")

    from app.auth import verify_password
    conn = sqlite3.connect(dst)
    h = conn.execute(
        "SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
    conn.close()
    assert verify_password("newpass", h), "重置后的密码无法登录"
    assert not verify_password("wrong", h), "错误密码不应通过"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_prepare_distribution.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.prepare_distribution'`

- [ ] **Step 3: 实现脚本**

创建 `scripts/prepare_distribution.py`：

```python
"""生成脱敏后的分发包数据库副本

用法：
    D:/Python/python.exe scripts/prepare_distribution.py \
        --src data/spec_query.db \
        --dst dist/data/spec_query.db \
        --admin-password admin

⚠ 全程在**副本**上操作：src 与 dst 必须不同，脚本会拒绝相同路径。
⚠ output_dir **不清空** —— 图片路由依赖它（见设计文档 §7.3）；
   source_path 清空（本机绝对路径，无人消费，仅消除信息泄露）。
"""
import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.auth import hash_password  # noqa: E402

# 需要清空的 settings 键（5 个密钥 + 1 个私有网关地址）
SECRET_KEYS = (
    # AI 后端密钥与私有网关地址
    "ai.custom.api_key",
    "ai.deepseek.api_key",
    "ai.doubao.api_key",
    "ai.glm.api_key",
    "ai.kimi.api_key",
    "ai.custom.base_url",
    # OCR token、网关地址与**自由 JSON 参数**
    # （params 是自由格式，可能被填入自定义请求头，保守起见一并清空）
    "ocr.access_token",
    "ocr.accurate-basic.access_token",
    "ocr.paddle-vl.access_token",
    "ocr.paddle-vl.params",
    "ocr.custom.access_token",
    "ocr.custom.api_key",
    "ocr.custom.base_url",
    "ocr.custom.params",
)

# 整体清空的数据表
PURGE_TABLES = ("system_logs", "qa_request_logs", "health_check_snapshots")

# 按 status 白名单过滤的表
STATUS_KEEP = {
    "rule_pending": {"approved", "rejected"},
    "classification_queue": {"review", "auto_adopted"},
}


def sanitize(src: Path, dst: Path, admin_password: str) -> None:
    """把 src 脱敏后写入 dst"""
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("src 与 dst 不能相同（必须在副本上脱敏）")
    if not src.is_file():
        raise FileNotFoundError(f"源库不存在：{src}")

    # ⚠ 用 sqlite3 的 backup API，而不是 shutil.copy2：
    #   直接复制文件会**漏掉 WAL 中已提交但尚未 checkpoint 的事务**，
    #   且服务运行中复制可能得到撕裂的副本（-wal/-shm 与主库不一致）。
    #   **前置条件：打包前先停止服务**（停服后 SQLite 会自动 checkpoint）。
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    conn = sqlite3.connect(dst)
    try:
        # 1. 清空密钥与内网地址
        ph = ",".join("?" * len(SECRET_KEYS))
        conn.execute(f"UPDATE settings SET value='' WHERE key IN ({ph})", SECRET_KEYS)

        # 2. 清空日志类表（同样兜住表不存在的情况）
        for t in PURGE_TABLES:
            try:
                conn.execute(f"DELETE FROM {t}")
            except sqlite3.OperationalError:
                pass

        # 3. 按 status 白名单过滤审核类表
        for table, keep in STATUS_KEEP.items():
            try:
                ph = ",".join("?" * len(keep))
                conn.execute(
                    f"DELETE FROM {table} WHERE status NOT IN ({ph})", tuple(keep))
            except sqlite3.OperationalError:
                pass  # 表不存在则跳过（不同版本库结构差异）

        # 4. source_path **只保留文件名主干**：
        #    清掉本机绝对路径（不泄露工作目录结构），但保住 stem ——
        #    `scripts/reimport_specs.py:56` 靠 `Path(source_path).stem` 反推
        #    task_id 去找 `outputs/{task_id}/{task_id}.md`；置 NULL 会让它失去索引。
        #    `output_dir` 必须原样保留（图片路由依赖）。
        for _sid, _sp in conn.execute(
                "SELECT id, source_path FROM specifications WHERE source_path IS NOT NULL"
        ).fetchall():
            conn.execute(
                "UPDATE specifications SET source_path = ? WHERE id = ?",
                (Path(_sp).name, _sid))

        # 5. 重置 admin 密码
        pwd_hash = hash_password(admin_password)
        conn.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
                     (pwd_hash,))

        conn.commit()
        # 收缩文件，去掉删除留下的空洞
        conn.execute("VACUUM")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--admin-password", default="admin")
    args = ap.parse_args()

    sanitize(Path(args.src), Path(args.dst), args.admin_password)
    print(f"脱敏完成：{args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_prepare_distribution.py -v`
Expected: 5 passed

- [ ] **Step 5: pyright**

Run: `pyright scripts/prepare_distribution.py`
Expected: 无新增 error

- [ ] **Step 6: Commit**

```bash
git add scripts/prepare_distribution.py tests/test_prepare_distribution.py
git commit -m "feat: 分发脱敏脚本（清密钥/日志/未审核队列 + 重置 admin 密码）"
```

---

## Task 7: 启动器

**Files:**
- Create: `launcher/launcher.py`
- Create: `launcher/build_launcher.sh`
- Create: `tests/test_launcher.py`

**Interfaces:**
- 消费：分发包内的 `runtime/python.exe`（**绝对路径**，绝不使用裸 `python`）
- Produces: `launcher.exe`（PyInstaller `--windowed` 产物），无命令行参数。

> ⚠ 启动器**不得 import `app`** —— 冻结后 `Path(__file__).resolve().parent.parent` 会指向 `_MEIPASS`，导致应用路径全乱。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_launcher.py`：

```python
"""启动器核心逻辑测试（不启动真实服务、不写日志到项目根）"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))

import launcher  # type: ignore[import-not-found]  # noqa: E402


def test_free_port_skips_occupied():
    """被占用的端口必须被跳过（对方机器可能占着 8000）"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        assert launcher._free_port(busy) != busy


def test_free_port_returns_start_when_free():
    """端口空闲时原样返回（正常场景）"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert launcher._free_port(free) == free


def test_wait_ready_times_out_on_dead_port():
    """无服务监听时必须在超时后返回 False，不得无限等待"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert launcher._wait_ready(free, timeout=1.0) is False


def test_check_deps_fails_on_missing_interpreter(tmp_path, monkeypatch):
    """解释器不存在时自检返回 False，而不是抛异常"""
    monkeypatch.setattr(launcher, "PYTHON", tmp_path / "nope" / "python.exe")
    assert launcher._check_deps() is False


def test_import_has_no_logging_side_effect():
    """import 本模块不得在项目根创建日志文件（日志配置必须延迟到 main）"""
    root_log = Path(launcher.__file__).resolve().parent.parent / "启动日志.txt"
    assert not root_log.exists(), "import 产生了副作用：创建了启动日志.txt"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_launcher.py -v`
Expected: FAIL —— `ModuleNotFoundError`（`launcher/launcher.py` 尚未创建）

- [ ] **Step 3: 写启动器**

创建 `launcher/launcher.py`：

```python
"""分发包启动器：自检 → 起服务 → 开浏览器 → 托盘常驻

用 PyInstaller --windowed 打包（无控制台窗口）。
⚠ 本文件**不得 import app** —— 冻结后 BASE_DIR 会指向 _MEIPASS 临时目录。
"""
import ctypes
import logging
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 分发包根目录 = 启动器 exe 所在目录
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parent.parent
PYTHON = ROOT / "runtime" / "python.exe"
LOG = ROOT / "启动日志.txt"


def _setup_logging() -> None:
    """延迟配置日志。

    ⚠ import 本模块必须**无副作用**——否则 `tests/test_launcher.py` 一 import
    就会在项目根建出「启动日志.txt」。
    """
    logging.basicConfig(
        filename=str(LOG), level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )

# Windows Job Object：父进程终止时由系统回收子进程
_JOB = None


def _bind_child_to_job(proc: subprocess.Popen) -> None:
    """把子进程绑到 Job Object，覆盖「启动器被强杀」的残留进程场景"""
    global _JOB
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong)]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t),      # ULONG_PTR
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ctypes.windll.kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
    handle = int(proc._handle)  # type: ignore[attr-defined]
    ctypes.windll.kernel32.AssignProcessToJobObject(job, handle)
    _JOB = job


def _check_deps() -> bool:
    """依赖自检：把「依赖能否加载」从发布前测试变成每次启动的自动检查

    除导入检查外，还校验**模型文件存在** —— 模型缺失时 `get_model()` 返回
    `None`（`app/ai/embedding.py:47-56`），向量检索会**静默降级**为纯关键词检索，
    用户和日志都不会察觉。宁可启动时大声失败。
    """
    logging.info("自检使用的解释器：%s", PYTHON)
    r = subprocess.run(
        [str(PYTHON), "-c",
         "import torch, lancedb, jieba, fitz, sentence_transformers; print('ok')"],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT),
    )
    if r.returncode != 0:
        logging.error("依赖自检失败：%s", r.stderr)
        return False

    models_dir = ROOT / "models" / "BAAI"
    if not any(models_dir.glob("*/config.json")):
        logging.error("模型目录缺失或为空：%s", models_dir)
        return False

    logging.info("依赖自检通过")
    return True


def _free_port(start: int = 8000) -> int:
    """从 start 起找第一个空闲端口"""
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("找不到空闲端口")


def _wait_ready(port: int, timeout: float = 120.0) -> bool:
    """轮询 `/health` 直到返回 200。

    ⚠ **不能用裸 TCP connect**：uvicorn 在 lifespan / `init_db()` 完成**之前**
    就已 bind + listen，connect 会成功但应用尚不可服务 —— 用户会在一次"成功"
    的启动中看到浏览器错误页。`/health` 已在 `public_paths` 内，且是真实的应用响应。
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def _message_box(text: str, title: str = "规范智能检索与问答系统") -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)


def main() -> int:
    """入口：任何未预期异常都必须转成 MessageBox。

    ⚠ 本程序以 `--windowed` 打包，**没有控制台** —— 未捕获的异常会静默退出，
    用户只看到「双击了没反应」。故在此兜底所有异常（如 `_free_port` 找不到
    空闲端口时抛的 RuntimeError）。
    """
    _setup_logging()
    try:
        return _run()
    except Exception as e:
        logging.exception("启动失败")
        _message_box(f"启动失败：{e}\n\n详情见日志：\n{LOG}")
        return 1


def _run() -> int:
    if not PYTHON.is_file():
        _message_box(f"未找到运行时：\n{PYTHON}\n\n请确认压缩包已完整解压。")
        return 1

    if not _check_deps():
        _message_box(
            "运行时依赖加载失败。\n\n"
            f"详情见日志：{LOG}\n"
            "（常见原因：VC++ 运行库缺失，或压缩包解压不完整）")
        return 1

    port = _free_port()
    logging.info("使用端口 %d", port)

    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    _bind_child_to_job(proc)

    if not _wait_ready(port):
        _message_box(f"服务启动超时。详情见日志：\n{LOG}")
        proc.terminate()
        return 1

    webbrowser.open(f"http://127.0.0.1:{port}")
    logging.info("服务已就绪，浏览器已打开")

    # 托盘常驻（pystray 必须在主线程）
    try:
        import pystray
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (64, 64), "white")
        ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill="#2b6cb0")

        def on_open(icon, item):
            webbrowser.open(f"http://127.0.0.1:{port}")

        def on_quit(icon, item):
            icon.stop()

        pystray.Icon(
            "spec", img, "规范智能检索与问答系统",
            menu=pystray.Menu(
                pystray.MenuItem("打开界面", on_open, default=True),
                pystray.MenuItem("退出", on_quit),
            ),
        ).run()
    except Exception as e:
        # 托盘不可用时**必须给出可见的说明** —— 否则用户面对一个没有窗口、
        # 没有控制台、没有托盘图标的进程，唯一的退出方式是任务管理器。
        logging.warning("托盘不可用：%s", e)
        _message_box(
            f"托盘图标不可用，服务已在后台运行。\n\n"
            f"浏览器访问：http://127.0.0.1:{port}\n"
            f"结束服务：在任务管理器里结束「启动.exe」与 python.exe。")
        proc.wait()

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    logging.info("已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_launcher.py -v`
Expected: 5 passed

- [ ] **Step 5: 写构建脚本**

创建 `launcher/build_launcher.sh`：

```bash
#!/usr/bin/env bash
# 构建启动器 exe（需在装有 pyinstaller 的环境执行）
set -e
cd "$(dirname "$0")/.."

# ⚠ 显式绝对路径，**不用裸 `python`**：裸 python 在 Git Bash 下可能解析到
#   Windows Store 空壳，或本机那个装了 paddleocr 的解释器，
#   导致启动器被冻结在一个与运行时（3.12）不同的版本上。
PY="D:/Python/python.exe"

"$PY" -m pip install "pyinstaller>=6.15" pystray pillow
"$PY" -m PyInstaller \
  --noconfirm --clean --onefile --windowed \
  --name 启动 \
  --distpath launcher/dist \
  --workpath launcher/build \
  --specpath launcher \
  launcher/launcher.py
echo "产物：launcher/dist/启动.exe"
```

- [ ] **Step 6: 构建并冒烟**

Run:
```bash
bash launcher/build_launcher.sh
```

Expected: 生成 `launcher/dist/启动.exe`

- [ ] **Step 7: 冒烟验证（在本机、用当前真实目录）**

把 `启动.exe` 临时放到项目根目录，双击运行。

Expected：
- 无黑色控制台窗口
- 生成的 `启动日志.txt` 首行含 `自检使用的解释器：`，路径指向 `<根>/runtime/python.exe`
- 浏览器打开且页面可访问

> 若 `runtime/` 尚未就绪（Task 8 才组装），可临时把 `PYTHON` 指向 `D:/Python/python.exe` 验证逻辑，**但必须确认日志打印的是该临时路径**，避免误以为已通过。

- [ ] **Step 8: Commit**

```bash
git add launcher/launcher.py launcher/build_launcher.sh tests/test_launcher.py
git commit -m "feat: 分发包启动器（依赖自检 + Job Object + 托盘 + 端口探测）"
```

---

## Task 8: 打包脚本

**Files:**
- Create: `scripts/build_distribution.py`

**Interfaces:**
- Consumes: Task 5 的 `.venv312`、Task 6 的脱敏库、Task 7 的 `启动.exe`
- Produces: `dist/规范智能检索与问答系统.zip`

- [ ] **Step 1: 实现打包脚本**

创建 `scripts/build_distribution.py`：

```python
"""组装便携分发包并打成 zip

前置（须先完成）：
  1. .venv312 已按 requirements.txt 装齐（Task 5）
  2. launcher/dist/启动.exe 已构建（Task 7）

用法：
    D:/Python/python.exe scripts/build_distribution.py
"""
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.')

from scripts.prepare_distribution import sanitize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 分发包版本 —— 发新版时**手动 bump**。日期戳自动生成，保证即使忘了 bump
#: 也不会出现「两个同名但内容不同的包」。
DIST_VERSION = "v1.0"
BUILD_DATE = datetime.now().strftime("%Y%m%d")

STAGE = ROOT / "dist" / "规范智能检索与问答系统"
OUT_ZIP = ROOT / "dist" / f"规范智能检索与问答系统-{DIST_VERSION}-{BUILD_DATE}.zip"

# Task 5 装好的便携 CPython（完整、含标准库）
PYRUNTIME = Path(os.environ.get("DIST_RUNTIME", "D:/pyruntime312"))

# 需要打进包的 VC++ 运行库（便携 CPython 不自带 msvcp140）
VCRUNTIME_DLLS = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140.dll", "vcruntime140_1.dll",
)

# 排除的应用目录
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "tests", "docs", ".git", ".venv", ".venv312"}


def _copy_app() -> None:
    """复制应用代码（排除开发产物与 scripts/）

    `scripts/` 不打包：里面是开发期工具（probe_*.py 探针、勘察脚本、
    脱敏脚本本身），对方是来用系统的，运行期不需要任何脚本。
    """
    for name in ("app", "static"):
        shutil.copytree(
            ROOT / name, STAGE / name,
            ignore=shutil.ignore_patterns(*EXCLUDE_DIRS),
        )
    (STAGE / "requirements.txt").write_text(
        (ROOT / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8")


def _copy_runtime() -> None:
    """复制便携 CPython 运行时（Task 5 装好的 D:/pyruntime312，完整含标准库）

    ⚠ **不能复制 venv** —— venv 只含 site-packages，标准库仍在 base Python
    （靠 pyvenv.cfg 指过去），单独打包会在对方机器上启动失败。
    """
    if not (PYRUNTIME / "python.exe").is_file():
        raise FileNotFoundError(f"未找到便携 Python：{PYRUNTIME}（先完成 Task 5）")

    shutil.copytree(PYRUNTIME, STAGE / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 补 VC++ 运行库（对方机器可能没装 Redistributable）
    for dll in VCRUNTIME_DLLS:
        src = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / dll
        if src.is_file():
            shutil.copy2(src, STAGE / "runtime" / dll)
        else:
            print(f"  [WARN] 未找到 {dll}，跳过")


def _copy_data() -> None:
    """脱敏库 + 向量库 + 图片目录"""
    (STAGE / "data").mkdir(parents=True, exist_ok=True)
    sanitize(ROOT / "data" / "spec_query.db", STAGE / "data" / "spec_query.db",
             admin_password=os.environ.get("DIST_ADMIN_PASSWORD", "admin"))

    shutil.copytree(ROOT / "lance_db", STAGE / "lance_db",
                    ignore=shutil.ignore_patterns("__pycache__"))

    # 只带被 specifications 引用的 outputs 目录
    import sqlite3
    conn = sqlite3.connect(STAGE / "data" / "spec_query.db")
    refs = [r[0] for r in conn.execute(
        "SELECT output_dir FROM specifications WHERE output_dir IS NOT NULL")]
    conn.close()

    base = ROOT / "data" / "outputs"
    out_dst = STAGE / "data" / "outputs"
    for ref in refs:
        try:
            rel = Path(ref).relative_to(base)
        except ValueError:
            print(f"  [WARN] output_dir 不在 data/outputs 下，跳过：{ref}")
            continue
        src = base / rel
        if src.is_dir():
            shutil.copytree(src, out_dst / rel, dirs_exist_ok=True)
        else:
            print(f"  [WARN] output_dir 不存在：{src}")


#: 分发包实际携带的模型目录。实测 `models/BAAI` 共 1265MB，其中
#: `bge-reranker-base` 占 **1061MB（84%）**——而 reranker 只服务 QA 精排，
#: 检索侧 `ce_rerank` 默认 False，属可选增强。带上它包体积直接翻倍。
MODELS_TO_SHIP = ("bge-small-zh-v1___5",)


def _copy_models() -> None:
    """只复制分发包需要的模型，并删掉与 safetensors 重复的 .bin（省 95MB）

    ⚠ **不得整个复制 `models/BAAI/`** —— 那会把 1.06GB 的 bge-reranker-base
    一起打进包，与设计文档 §2「只带 bge-small-zh」冲突。
    """
    src_base = ROOT / "models" / "BAAI"
    dst_base = STAGE / "models" / "BAAI"
    dst_base.mkdir(parents=True, exist_ok=True)

    for name in MODELS_TO_SHIP:
        src = src_base / name
        if not src.is_dir():
            raise FileNotFoundError(f"模型目录不存在：{src}")
        shutil.copytree(src, dst_base / name)

    for bin_file in dst_base.rglob("pytorch_model.bin"):
        if (bin_file.parent / "model.safetensors").is_file():
            bin_file.unlink()
            print(f"  删除冗余权重：{bin_file}")


def _write_env() -> None:
    """生成 .env：关闭鉴权 + 随机 SECRET_KEY"""
    (STAGE / ".env").write_text(
        f"AUTH_ENABLED=0\nSECRET_KEY={secrets.token_urlsafe(48)}\n",
        encoding="utf-8")


def _copy_launcher() -> None:
    exe = ROOT / "launcher" / "dist" / "启动.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"启动器未构建：{exe}（先跑 launcher/build_launcher.sh）")
    shutil.copy2(exe, STAGE / "启动.exe")


def _zip() -> None:
    # compresslevel=1：模型权重实测压缩率仅 91.7%（高熵数据，压不动），
    # level=6 会为几乎为零的收益多烧 5-15 分钟 CPU；level=1 对 DLL/代码
    # 仍有 ~30% 的压缩效果（torch_cpu.dll 实测）。
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in STAGE.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(STAGE.parent))


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    print("1/7 复制应用代码…");  _copy_app()
    print("2/7 复制运行时…");    _copy_runtime()
    print("3/7 复制数据…");      _copy_data()
    print("4/7 复制模型…");      _copy_models()
    print("5/7 生成 .env…");     _write_env()
    print("6/7 复制启动器…");    _copy_launcher()
    print("7/7 打包 zip…");      _zip()

    size = OUT_ZIP.stat().st_size / 1048576
    print(f"\n完成：{OUT_ZIP}（{size:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: pyright**

Run: `pyright scripts/build_distribution.py`
Expected: 无新增 error

- [ ] **Step 3: 跑打包**

Run: `D:/Python/python.exe scripts/build_distribution.py`
Expected: 生成 `dist/规范智能检索与问答系统-v1.0-<日期>.zip`，打印体积（预期 600MB 量级）

- [ ] **Step 4: 从 STAGE 起一次真实服务并验证（关键门禁，不可跳过）**

⚠ 两层风险都要在这里暴露，不能留到对方机器上才发现：
1. **官方安装版 Python 会写注册表**（实测 `HKCU:\SOFTWARE\Python\PythonCore\3.12` → `InstallPath`），复制到没有这些项的机器上能否跑，未经验证；
2. `cd` 到 STAGE 后，路径基准（`BASE_DIR`）、静态资源、数据库连接全是**新组合**，只验 `import` 发现不了这类回归。

Run（在 STAGE 目录内起服务）：

```bash
cd "dist/规范智能检索与问答系统"
runtime/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8012 &
sleep 20
curl -s http://127.0.0.1:8012/health
curl -s "http://127.0.0.1:8012/search?keyword=%E9%92%A2%E7%AD%8B" | head -c 300
```

Expected: `/health` 返回 `{"status":"ok"}`；`/search` 返回含条文的 HTML。

若 `import` 就失败 → 运行时不可用：改用官方 embeddable 包（`python-3.12.x-embed-amd64.zip`）重建，或查明缺失的注册表项。
若服务起得来但搜索为空 → 检查 `lance_db/` 与 `data/` 是否正确落位。

完成后**结束该进程**再继续。

- [ ] **Step 5: 校验包内容（自动化断言，替代肉眼）**

Run:
```bash
D:/Python/python.exe -c "
import glob, zipfile
z = zipfile.ZipFile(glob.glob('dist/规范智能检索与问答系统-*.zip')[0])
names = z.namelist()
must_have = ['规范智能检索与问答系统/启动.exe', '规范智能检索与问答系统/.env']
must_not = ['uploads/', 'backups/', '__pycache__', '.pytest_cache', 'tests/', 'app.db']
for m in must_have:
    assert any(n.startswith(m) or n == m for n in names), f'缺 {m}'
for m in must_not:
    bad = [n for n in names if m in n]
    assert not bad, f'不该含 {m}: {bad[:3]}'
print('包内容校验通过，文件数', len(names))
"
```
Expected: `包内容校验通过`

- [ ] **Step 6: Commit**

```bash
git add scripts/build_distribution.py
git commit -m "feat: 分发包打包脚本（含 VC++ 运行库、模型减重、包内容自校验）"
```

---

## Task 9: 分层验收

**Files:** 无（执行性任务，产出为验收结论）

- [ ] **Step 1: L1 —— 本机异路径 + codex 沙箱**

```bash
# 解压到与项目无关的路径（含空格/中文）
mkdir -p E:/test/ && cd E:/test/ && unzip -q <路径>/规范智能检索与问答系统.zip
# 用沙箱验证写入边界（§10）
codex sandbox --sandbox-state-readable-root "E:/test/规范智能检索与问答系统" \
  -- "E:/test/规范智能检索与问答系统/runtime/python.exe" -c "import torch, lancedb, jieba, fitz; print('ok')"
```
Expected：打印 `ok`；**无越界写入被拒的错误**。若有 `PermissionError`/`Access is denied` 指向 `~/.cache` 等路径，记录之——那是 §10 承认的隐藏写入被暴露。

- [ ] **Step 2: L1 端到端**

双击 `E:/test/规范智能检索与问答系统/启动.exe`。
逐条核对设计文档 §9.4 清单：无黑窗、浏览器自动打开、无登录页、能检索 3044 条、正文图片不裂、托盘可退出且无残留进程。

- [ ] **Step 3: L2 —— 新建本地用户账户**

新建一个 Windows 本地用户，登录后重复 Step 2。
Expected：同样通过（验证不依赖当前用户的 Path 配置）。

- [ ] **Step 4: 记录结论**

把 L1/L2/沙箱的实测结果写入本计划末尾「实测记录」小节。

---

## 实测记录

（执行时填写）

| 项 | 实测值 |
| --- | --- |
| `.venv312` 体积 | |
| zip 体积 | |
| L1 结果 | |
| L2 结果 | |
| 沙箱写入边界结果 | |

---

## 自检清单（写完计划后核对）

- [ ] **Spec 覆盖**：设计文档 §3（依赖）、§4（脱敏）、§5（启动器）、§6（打包）、§7.1–7.4（四项修复）均有对应 Task（分别为 5、6、7、8、4/3/2/1）。
- [ ] **无占位符**：全文无 "TBD" / "实现 X" / "类似 Task N"。
- [ ] **类型一致**：`orphan_uploads` / `orphan_output_dirs` / `sanitize` / `_copy_ocr_images` 的签名在定义处与调用处一致。
- [ ] **交叉引用**：§9.2/§10 的引用与设计文档当前编号一致（设计文档已把小节顺延为 9.2 沙箱 / 9.3 假通过 / 9.4 端到端）。

---

# 工程评审记录（plan-eng-review）

> **审查目标（固定）**：`docs/superpowers/plans/2026-10-01-distribution-package.md`
> 分支：color　日期：2026-10-01　Session：1-1790863209-4a1b8307

## Decision ledger

### R1：文件结构安排（复杂度 gate）

- **Finding**：非 finding —— 复杂度 gate（15 文件 ≥ 8 触发）
- **Plan baseline**：原安排 = 7 改 + 8 建，共 15 文件（本计划「文件结构」节）
- **Runtime evidence**：实测 `python -m pyright` 报 `No module named pyright`；本机 pyright 实为 `/d/nodejs/npm-global/pyright`（npm 全局二进制）。`pyrightconfig.json` 的 `include` 当前已含 `"scripts"`。
- **Comparison grid**：

| 可选项 | 当前 | A | B |
|---|---|---|---|
| 文件安排 | 15 文件（7 改 + 8 建） | 15 文件，不变 | 待调查更小拆法 |
| 功能清单（9 Task） | 全部保留 | 全部保留 | 全部保留 |
| 各 Task 承诺与契约 | 全部保留 | 全部保留 | 全部保留 |

- **Question D1**：分发包计划涉及 15 个文件，触发复杂度 gate。确认原安排，还是暂停调查更小的拆法？
- **Header**：文件结构
- **Options**：
  - A) 确认原安排（7 改 + 8 建）—— 每文件单一职责；Task 7 可独立冒烟，不等 Task 8
  - B) 暂停，先调查更小的拆法

**State**: approved
**Actual answer**: A) 确认原安排（D1，用户答复）
**Accepted scope**: 保持原文件安排（7 改 + 8 建，共 15 文件）
**History**: —

### R2：分发包内容（是否含 scripts/）

- **Finding**：SC6 [P3] (confidence: 8/10) Task 8 `_copy_app()` — 整个 `scripts/`（30 个文件）被复制进分发包
- **Plan baseline**：`for name in ("app", "static", "scripts")`
- **Runtime evidence**：`scripts/` 含 `probe_*.py` 探针、`survey_structure.py`、`prepare_distribution.py` 等开发工具；运行期不需要任何脚本
- **Comparison grid**：

| 可选项 | 当前 | A | B |
|---|---|---|---|
| 分发包内容 | 含 scripts/（30 文件） | 不含 scripts/ | 含全部 scripts/ |
| 运行必需文件 | app/ static/ runtime/ models/ data/ lance_db/ | 不变 | 不变 |

- **Question D2**：分发包是否包含开发脚本？
- **Header**：分发内容
- **Options**：
  - A) 排除整个 scripts/
  - B) 保留全部 scripts/

**State**: approved
**Actual answer**: A) 排除整个 scripts/（D2）
**Accepted scope**: `_copy_app()` 只复制 `app/` 与 `static/`
**History**: —

### R3：分发包目录名

- **Finding**：SC7 [P3] (confidence: 9/10) §2 — 目录名「规范检索系统」与已更名品牌不一致
- **Plan baseline**：`STAGE = ROOT / "dist" / "规范检索系统"`
- **Runtime evidence**：`app/templates/base.html`、`app/main.py` 均用「规范智能检索与问答系统」（提交 ff38e72 更名）
- **Comparison grid**：

| 可选项 | 当前 | A | B |
|---|---|---|---|
| 目录/zip 名 | 规范检索系统 | 规范智能检索与问答系统 | 规范检索系统 |
| 其他标识（托盘标题等） | 规范检索系统 | 同步改为全名 | 不变 |

- **Question D3**：分发包目录名用哪个？
- **Header**：目录命名
- **Options**：
  - A) 规范智能检索与问答系统
  - B) 规范检索系统

**State**: approved
**Actual answer**: A) 用产品全名（D3）
**Accepted scope**: 全计划内「规范检索系统」→「规范智能检索与问答系统」
**History**: —

### 事实性纠正（SC1–SC5，无需决策）

| ID | 严重度 | 置信度 | 位置 | 纠正 | 证据 |
|---|---|---|---|---|---|
| SC1 | P1 | 10/10 | 6 处 pyright 步骤 | `D:/Python/python.exe -m pyright` → `pyright` | 实测报 `No module named pyright`；本机 pyright 为 `/d/nodejs/npm-global/pyright` |
| SC2 | P2 | 9/10 | Task 8 | 补 pyright 步骤 | 另 7 个 Task 均有 |
| SC3 | P2 | 8/10 | Task 9 | `/e/test/…` → `E:/test/…` | Git Bash 路径对 Windows 程序（codex）无效 |
| SC4 | P2 | 7/10 | Task 8 | 新增「复制品自检」步骤 | 实测安装版写注册表：`HKCU:\SOFTWARE\Python\PythonCore\3.12` → `InstallPath=D:\Python312\` |
| SC5 | P2 | 7/10 | Task 8 | `PYRUNTIME` 改为 `os.environ.get("DIST_RUNTIME", ...)` | 硬编码路径换机器即失败 |

### Sections 1–4 的修复（必要证明与事实性纠正）

| ID | 严重度 | 置信度 | 位置 | 修复 |
|---|---|---|---|---|
| T1 | P2 | 9/10 | Task 6 | 补 `test_sanitize_new_password_can_log_in` —— 设计文档 §8 要求验证可登录，原计划只断言「哈希变了」（写成不可验证的值同样能过） |
| T2 | P2 | 9/10 | Task 7 | 新增 `tests/test_launcher.py`（5 例）；并把 launcher 的日志配置延迟到 `main()`（`import` 必须无副作用，否则测试会在项目根建出「启动日志.txt」） |
| T3 | P3 | 8/10 | Task 3 | 补 `test_copy_ocr_images_keeps_source_when_copy_fails` |
| P1 | P2 | 8/10 | Task 8 | `_zip()` 的 `compresslevel` 由 6 降至 1 —— 模型权重实测压缩率仅 91.7%，level=6 为近乎零的收益白烧 5-15 分钟 CPU |
| C1 | P3 | 9/10 | 计划 | 补「打包数据流」「启动器启动链」两张 ASCII 图 |

**Critical gap（failure modes 口径：无测试 + 无错误处理 + 静默）**

`_free_port()` 在 50 个端口全被占时抛 `RuntimeError`，而 `main()` 未捕获 —— 启动器以 `--windowed` 打包、**没有控制台**，未捕获异常会静默退出，用户只看到"双击了没反应"。**已修**：`main()` 兜底 `try/except` → `MessageBox` + 日志；启动链图中标注该分支。

**共享代码评估（不提取）**：候选为 `cleanup_orphan_files.orphan_output_dirs` 与 `import_routes._cleanup_task_artifacts` 的引用判断。契约不兼容——前者全量扫描 + 前缀匹配，后者按 uuid `task_id` 精确匹配（输入受 `[0-9a-f]{8}` 约束，本就不会触及 `JTG/` 这类父目录）。按 rubric「契约不兼容的相似性应拒绝」记录后不提取。

**文件计数的连带变化**：新增 `tests/test_launcher.py` → 结构变为 **7 改 + 9 建 = 16 文件**（属已批准行为的必要证明，非范围扩张）。

### R4：分发包版本标识（修订后）

- **Finding**：A1 [P3] (confidence: 6/10) — 分发包无版本标识
- **Plan baseline**：`OUT_ZIP = ROOT / "dist" / "规范智能检索与问答系统.zip"`（固定名）
- **Runtime evidence**：项目**无任何版本机制** —— 无 `VERSION` 文件、无版本常量、页脚不显示版本、无 git tag（四项均已实测）
- **Comparison grid**：

| 可选项 | 当前 | A | B | C |
|---|---|---|---|---|
| zip 文件名 | 固定名 | `v1.0-YYYYMMDD` | `YYYYMMDD` | `v1.0` |
| 维护成本 | — | 手动 bump + 自动日期 | 零 | 手动 bump |
| 撞名风险 | 高（同名覆盖） | 无（日期兜底） | 无 | **有（忘 bump）** |

- **Question D4（修订）**：分发包用哪种版本标识？
- **Header**：版本标识
- **Options**：
  - A) `v1.0-20261001` 组合
  - B) 纯日期戳
  - C) 纯版本号 `v1.0`

**State**: approved
**Actual answer**: A) `v1.0-20261001` 组合（D4 修订）
**Accepted scope**: `DIST_VERSION = "v1.0"` + `BUILD_DATE`；zip 名为 `规范智能检索与问答系统-v1.0-<日期>.zip`；包内容校验命令改用通配符
**History**: 原 D4 选项为「固定名 / 纯日期戳」。用户追问「加版本号 v1.0 是否更好、实现难度是否更大」，遂补充「项目无任何版本配套」的实测事实并修订为三选项。结论：代码难度几乎相同（均约一行），差异在**维护纪律**——版本号需手动 bump，忘 bump 会产生同名不同内容的包。

### R5：Task 4（孤儿清理）去留

- **Finding**：L3（Outside Voice / codex）— `_copy_data()` 只带 `spec_query.db` + `lance_db/` + 被引用的 `outputs/`，**从不包含 `uploads/`**，故清理那 42MB 孤儿对 zip 体积影响为**零字节**；Task 4 属 off-goal
- **Plan baseline**：Task 4 保留在本计划内（用户先前决定「一并做」）
- **Runtime evidence**：计划 Task 8 `_copy_data()` 的实现；`uploads/` 确实不在复制清单内
- **Comparison grid**：

| 可选项 | 当前 | A | B |
|---|---|---|---|
| Task 4 位置 | 在本计划内 | 保留 + 加路径参数 + md 保护 | 移出本计划，单独排期 |
| 对 zip 体积的影响 | 0 | 0 | 0 |

- **Question D5**：Task 4 留在本计划里吗？
- **Header**：Task 4 去留
- **Options**：
  - A) 保留，但加路径参数 + md 保护
  - B) 从本计划移出，单独排期

**State**: approved
**Actual answer**: A) 保留 + 加保护（D5）
**Accepted scope**: `orphan_output_dirs` 永不删含 `.md` 的目录；`main()` 新增 `--uploads-dir` / `--outputs-dir`
**History**: —

### R6：离线机器上的问答（QA）处置

- **Finding**：L8（Outside Voice / codex）— 清空 key 但保留 `ai.backend`，对方点问答会撞 DNS 失败/超时，看到的是报错而非「未配置」
- **Plan baseline**：保留 `ai.backend`（用户先前决定「只清 key、留后端选择」）
- **Runtime evidence**：`settings` 保留 `ai.backend='custom'`、`ai.backend.qa='deepseek'`；对应 key 被清空
- **Comparison grid**：

| 可选项 | 当前 | A | B |
|---|---|---|---|
| `ai.backend` | 保留 | 保留 | 清空 |
| 说明文档 | 未涉及 | 写明「问答需自行配置 AI key」 | — |
| 代码改动 | — | 0 | 需确认 UI 能优雅显示「未配置」 |

- **Question D6**：对方离线机器上的问答功能怎么处理？
- **Header**：QA 处置
- **Options**：
  - A) 保留后端选择，在使用说明写明
  - B) 清空 `ai.backend`，让问答显式「未配置」

**State**: approved
**Actual answer**: A) 保留后端，说明文档写明（D6）
**Accepted scope**: 不改代码；由用户在使用说明中写明「问答需自行配置 AI key，不配也可用检索」
**History**: —

### Outside Voice 引入的其余修复（经父审查逐条核实后直接应用）

| ID | 严重度 | 位置 | 修复 | 核实结果 |
|---|---|---|---|---|
| B1 | **Blocking** | Task 1 | `AUTH_ENABLED` 默认改回 `"1"`，分发包靠**包内 .env** 关闭 | ✅ 实测 **11 处**测试断言依赖鉴权生效 |
| B3 | **Blocking** | Task 3 | `import shutil` 提到 `import_routes.py` 模块级 | ✅ 实测模块级计数为 0，测试会 `AttributeError` |
| L1 | **Blocking** | Task 4 | `orphan_output_dirs` 永不删含 `.md` 的目录 | ✅ 实测 10 个未引用目录含 md，其中 4 个是已入库规范的 task_id |
| F1 | **Blocking** | Task 8 | `_copy_models()` 只带 `bge-small-zh`，排除 1.06GB reranker | ✅ 原代码 `copytree` 整个 BAAI 目录，与设计文档 §2 冲突 |
| L2 | P2 | Task 4 | `main()` 新增 `--uploads-dir` / `--outputs-dir` | ✅ 原脚本无路径参数，无法「操作副本」 |
| L4 | P2 | Task 6 | `source_path` 只留 basename，保住 reimport_specs 的 stem 反推 | ✅ `reimport_specs.py:56` 实测依赖它 |
| L5 | P2 | Task 6 | `SECRET_KEYS` 补 `ocr.paddle-vl.params` 等 | ✅ 实测该键非空 |
| L6 | P2 | Task 6 | 用 `Connection.backup()` 替代 `shutil.copy2` | ⚠ 当前无 `-wal` 文件，但风险成立（服务运行时会有） |
| L7 | P3 | Task 6 | `PURGE_TABLES` 加存在性保护 | ✅ |
| F3 | P2 | Task 7 | 托盘 fallback 加 MessageBox | ✅ 否则用户只能开任务管理器 |
| F4 | P2 | Task 7 | `_wait_ready` 改用 `/health` HTTP 轮询 | ✅ 裸 TCP 会在应用可服务前就成功 |
| F5 | P2 | Task 8 | pre-zip 从 STAGE 起真实服务 + `/health` + 一次搜索 | ✅ |
| F6 | P2 | Task 7 | `_check_deps` 补 `sentence_transformers` + 模型文件校验 | ✅ 否则向量检索静默降级 |
| F7 | P2 | Task 7 | `build_launcher.sh` 用绝对路径 python | ✅ 裸 python 可能冻结在 3.14 而运行时是 3.12 |
| L9 | P3 | Task 2 | 两个既有图片测试补 patch `OUTPUT_DIR` | ✅ grep 确认无 patch |
| L10 | P3 | Task 1 | `.env.example` 加 `AUTH_ENABLED=1` | ✅ 实测缺失 |

**Outside Voice 确认无误的部分**：无外部 CDN 资源（离线 UI 可用）；`proc._handle` 的 `int()` 转换可用；Task 3 的前提（成功导入会留下 `outputs/{task_id}`）正确；Task 2 的路由改动自洽。

**未采纳的一条**：codex 称「staged ≈ 3.0–3.3 GB、600MB 不可达」——该估算把整个 `models/BAAI`（含 1.06GB reranker）与本机全局 `site-packages`（含 cv2/playwright/modelscope 等无关包）都算了进去。F1 修复后模型仅 88MB，site-packages 只含 requirements 的包，故压缩后 600MB 量级的原估算成立。

**Approval readiness: PASS** —— R1(D1) / R2(D2) / R3(D3) / R4(D4 修订) / R5(D5) / R6(D6) 均有实际答复；SC1–SC5 与 Sections 1–4、Outside Voice 的其余项均为**事实性纠正或已批准行为的必要证明**，按 Decision procedure 无需单独决策。

---

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~1h / CC: ~5min)** — Task 1 — `AUTH_ENABLED` 默认值改为 `"1"`，改测试断言为默认开启
  - Surfaced by: Outside Voice B1 — 「默认关闭会让 11 处既有断言失败；包内 .env 已能关闭鉴权」
  - Files: `docs/superpowers/plans/2026-10-01-distribution-package.md`（Task 1）
  - Verify: `pytest tests/test_auth.py tests/test_auth_routes.py -v` 全绿
- [ ] **T2 (P1, human: ~30min / CC: ~3min)** — Task 3 — `import shutil` 提到 `import_routes.py` 模块级
  - Surfaced by: Outside Voice B3 — 「模块级 `shutil` 计数为 0，测试会 AttributeError」
  - Files: `app/routes/import_routes.py`
  - Verify: `pytest tests/test_import.py -k copy_ocr_images -v`
- [ ] **T3 (P1, human: ~1h / CC: ~5min)** — Task 4 — `orphan_output_dirs` 加 md 保护 + 路径参数
  - Surfaced by: Outside Voice L1 — 「10 个未引用目录含 md，其中 4 个是已入库规范的 task_id」
  - Files: `scripts/cleanup_orphan_files.py`, `tests/test_cleanup_orphan_files.py`
  - Verify: `pytest tests/test_cleanup_orphan_files.py -v`
- [ ] **T4 (P1, human: ~30min / CC: ~3min)** — Task 8 — `_copy_models()` 排除 reranker
  - Surfaced by: Outside Voice F1 — 「原代码复制整个 BAAI 目录，多带 1.06GB」
  - Files: `scripts/build_distribution.py`
  - Verify: 打包后 `ls dist/*/models/BAAI/` 只应有 `bge-small-zh-v1___5`
- [ ] **T5 (P2, human: ~3h / CC: ~20min)** — 其余 P2 修复（F3/F4/F5/F6/F7/L2/L4/L5/L6）
  - Surfaced by: Outside Voice 多条
  - Files: `launcher/launcher.py`, `launcher/build_launcher.sh`, `scripts/prepare_distribution.py`, `scripts/build_distribution.py`
  - Verify: `pytest tests/test_launcher.py tests/test_prepare_distribution.py -v` + Task 8 Step 4 的 STAGE 真实服务验证
- [ ] **T6 (P3, human: ~30min / CC: ~3min)** — Task 1/2 — `.env.example` 同步 + 既有图片测试补 patch
  - Surfaced by: Outside Voice L9/L10
  - Files: `.env.example`, `tests/test_spec_routes.py`
  - Verify: `pytest tests/test_spec_routes.py -v`

### NOT in scope（本计划明确不做）

- 改密码 / 登录页优化 —— 鉴权默认关闭后无必要，且属独立功能
- agent API / MCP 服务化 —— 08-25 稿列出的后续阶段
- 云服务器 / Linux / Docker 部署 —— 接收方是 Windows 离线机
- 上传文件名规范化 + 重导 UI —— 用户实际从自己文件夹选文件重传，UI 是伪需求
- 版本号写进包内（`VERSION` 文件 / 页脚显示）—— 项目无任何版本配套，本次只在 zip 名体现
- SmartScreen/MotW 的正式缓解（代码签名证书）—— 属发布流程，本次仅以使用说明告知绕过方式

### What already exists（复用而非重建）

- `_cleanup_task_artifacts`（`app/routes/import_routes.py:1077`）的引用集判据 —— Task 4 复用其**思路**，但契约不兼容（单 task_id vs 全量扫描），故不提取共享函数
- `scripts/reimport_specs.py` —— 已有的解析器重导工具；本计划**保护它的输入 md**，而非重建
- `app/main.py:200` 的 `/health` —— 启动器就绪探测直接复用，不新增端点
- `app/config.py:7` 的 `load_dotenv(BASE_DIR / ".env")` —— `AUTH_ENABLED` 靠它生效，无需新机制
- 便携 CPython 自带的 `vcruntime140*.dll` —— 只需补 `msvcp140*.dll` 三个

### Worktree parallelization strategy

**Sequential implementation, no parallelization opportunity.**

Task 9 依赖 Task 8 的产物，8 依赖 7 的 exe，7 依赖 5 的 runtime，5/6 依赖 Task 1–4 的代码稳定；全部落在同一个 `color` 分支且共享 `app/` 与 `scripts/`，无不相交的模块车道可并行。

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | codex exec（由 Eng Review 触发） | Independent 2nd opinion | 1 | completed | 20 findings |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN | 7 issues（四节）+ 4 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**OUTSIDE COVERAGE:** codex（`codex exec`，read-only，model=gpt-6-astra），phase=plan-review，**completed**，20 findings —— 4 Blocking、6 logic/correctness、5 feasibility/sizing、5 lower。全部经父审查逐条实测核实后处置：16 项直接修、1 项驳回（体积估算）、2 项转用户决策（D5/D6）。codex 另确认 4 处无误（无外部 CDN、`proc._handle` 转换可用、Task 3 前提正确、Task 2 改动自洽）。

**CROSS-MODEL:** 双方独立命中同批问题（`AUTH_ENABLED` 默认值致 11 处断言红、`import shutil` 位置致测试 `AttributeError`、Task 4 会删 md、`.env.example` 缺项）。codex **独占**发现：`_copy_models()` 会带上 1.06GB reranker（与设计文档 §2 冲突）、`_wait_ready` 用裸 TCP 会在应用可服务前就成功、`_check_deps` 漏 `sentence_transformers` 致向量检索**静默降级**、`build_launcher.sh` 用裸 `python` 可能把启动器冻结在 3.14（而运行时是 3.12）、托盘 fallback 产生无法退出的进程、SmartScreen/MotW 未处理。父审查**独占**发现：`_free_port` 抛异常而 `main()` 未捕获 → `--windowed` 下静默退出。

**VERDICT:** Outside Review + Eng Review 均 **ISSUES OPEN** —— 全部 findings 已映射为 T1–T6 并**已直接写入本计划**，无未决阻塞项，可进入实施。

NO UNRESOLVED DECISIONS
