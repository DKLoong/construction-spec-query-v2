# 导入任务台账持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把导入任务台账从进程内存字典搬进主库，使进度、状态、待审查入口在服务重启后依然可用，并让被中断的任务给出明确结论。

**Architecture:** 新增主库表 `import_tasks`（加法式迁移）；`_get_task` / `_update_task` 保持名称与签名、实现换为参数化 SQL；新增 `create_task` / `delete_task` / `iter_tasks` 替代直接操作字典之处；启动时同步把 `uploading`/`processing` 自愈为 `error`；A 组的超期清理与两档时限参数原样保留，只把遍历对象从字典换成表行。**不加进程内缓存**——有缓存就等于把「重启丢失」换个形式再引入一次。

**Tech Stack:** Python 3.14 / FastAPI / sqlite3（`app.database.get_db`）/ pytest / Playwright（`scripts/probe_spec_ui.py`）

**Spec:** `docs/superpowers/specs/2026-10-02-import-task-persistence-design.md`

## Global Constraints

- 全程中文注释与中文提交信息；提交格式 `type: 描述`（`feat`/`fix`/`test`/`docs`/`refactor`/`chore`），**单次提交对应单个 Task**。
- 每 Task 必须：跑本次改动关联的增量测试（全绿）→ 跑 `pyright app tests scripts`（**不得新增 error**，当前基线 0 error）→ 再提交。
- **数据库操作必须参数化查询**。本设计里列名需要拼接进 `SET` 子句，因此**列名必须走白名单常量校验**，禁止把未经校验的键名拼进 SQL。
- 严禁在真实 `data/` 上跑测试或探针（真实库 `data/spec_query.db`、`data/uploads/`、`data/outputs/`、`lance_db/` 一律不得写入）。例外只有 Task 5 的回填 —— 它按用户授权**只**写 `import_tasks` 一行。
- 探针必须按 `scripts/probe_spec_ui.py` 顶部配方的**四条**环境变量隔离（`DATABASE_PATH` / `UPLOAD_DIR` / `OUTPUT_DIR` / `LANCE_DB_PATH`），跑完杀进程并删四个隔离目录。
- 台账的 `updated_at` 一律由 `_update_task` 统一刷新，调用方不得自行传 `updated_at`。
- spec §7（`--reload-dir app` 写进项目 `CLAUDE.md`）**已在前序提交 `3056fc4` 完成**，本计划不含该改动。

---

### Task 1: 台账落库（表 + 存储层 + 全部调用点）

> **为什么这一条不能拆**：把 `progress_store` 删掉而调用点仍引用它，中间态的应用是**坏的**
> （上传即 `NameError`）。「存储层换 SQL」与「调用点接线」是同一次不可分割的改动。

**Files:**
- Modify: `app/database.py`（`SCHEMA_SQL` 末尾追加建表）
- Modify: `app/routes/import_routes.py`（删 `progress_store = {}`；重写 `_get_task` / `_update_task`；新增 `create_task` / `delete_task` / `iter_tasks`；替换创建处、2 处 `pop`、10 处 `.get` 直读、sweep 的遍历与 `pop`；清理失真注释）
- Modify: `app/config.py:82-87`（台账时限注释）
- Modify: `app/params/registry.py`（两个参数的 help 文案）
- Modify: `tests/test_import_task_lifecycle.py`（夹具：字典 → 落库接口）
- Create: `tests/test_import_task_store.py`
- Create: `tests/test_import_no_memory_store.py`

**Interfaces:**
- Consumes: `app.database.get_db`（上下文管理器；连接 `row_factory = sqlite3.Row`）
- Produces:
  - `create_task(task_id: str, owner: str = "") -> None`
  - `_get_task(task_id: str) -> dict | None`（**普通 dict**，因为调用方大量用 `task.get(...)`，而 `sqlite3.Row` 没有 `.get`）
  - `_update_task(task_id: str, **fields) -> bool`（任务不存在返回 `False`）
  - `delete_task(task_id: str) -> None`（幂等）
  - `iter_tasks() -> list[dict]`
  - `_TASK_COLUMNS: frozenset[str]`（可写列白名单）
  - `progress_store` 符号**从此不存在**

- [ ] **Step 1: 写存储层测试（会失败）**

创建 `tests/test_import_task_store.py`：

```python
"""导入任务台账落库：CRUD 往返、只写传入列、无进程内状态

「无进程内状态」是本文件的核心断言：每次校验都**另开一个连接**，因此"写完能读到"
在语义上等价于"重启后仍在"——这是持久化唯一的真实判据（探针证明不了它：探针跑在
一个已运行的服务上，刷新页面并不会重启服务）。
"""
import sqlite3
import time

import pytest

from app.routes import import_routes as ir


@pytest.fixture
def _db(tmp_path, monkeypatch):
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "store.db"))
    init_db()
    return tmp_path / "store.db"


def test_create_and_get_roundtrip(_db):
    ir.create_task("aaaabbbb", owner="admin")

    task = ir._get_task("aaaabbbb")

    assert task["task_id"] == "aaaabbbb"
    assert task["status"] == "uploading"
    assert task["progress"] == 0
    assert task["message"] == "正在上传..."
    assert task["owner"] == "admin"
    assert isinstance(task["updated_at"], float)


def test_get_missing_returns_none(_db):
    assert ir._get_task("nope0000") is None


def test_update_returns_false_for_missing_task(_db):
    """A 组确立的语义：任务不存在返回 False，调用方据此收工（不得抛异常）"""
    assert ir._update_task("nope0000", progress=50) is False


def test_update_refreshes_updated_at(_db):
    ir.create_task("aaaabbbb")
    before = ir._get_task("aaaabbbb")["updated_at"]

    time.sleep(0.01)
    assert ir._update_task("aaaabbbb", progress=50) is True

    assert ir._get_task("aaaabbbb")["updated_at"] > before


def test_update_rejects_unknown_column(_db):
    """列名要拼进 SET 子句，必须有白名单兜底（防注入，也防字段名打错后静默写不进去）"""
    ir.create_task("aaaabbbb")

    with pytest.raises(ValueError):
        ir._update_task("aaaabbbb", status_typo="processing")


def test_update_writes_only_passed_columns(_db, monkeypatch):
    """UPDATE 只写传入字段——否则 OCR 期间每次只改 message 都会重写整行 md_text

    用 sqlite3 的 trace 回调抓**实际执行的 SQL**（不是读源码字符串）：它才是真正
    决定"写多少"的东西。
    """
    traces = []
    real_connect = sqlite3.connect

    ir.create_task("aaaabbbb")
    ir._update_task("aaaabbbb", md_text="正文" * 100)

    def traced(path, *a, **kw):
        conn = real_connect(path, *a, **kw)
        conn.set_trace_callback(traces.append)
        return conn

    monkeypatch.setattr("app.database.sqlite3.connect", traced)
    traces.clear()
    ir._update_task("aaaabbbb", message="正在 OCR 识别...")

    updates = [s for s in traces if s.strip().upper().startswith("UPDATE IMPORT_TASKS")]
    assert updates, f"没抓到 UPDATE 语句：{traces!r}"
    assert all("md_text" not in s for s in updates), \
        f"UPDATE 覆盖了未传入的 md_text 列：{updates!r}"


def test_md_text_survives_message_update(_db):
    ir.create_task("aaaabbbb")
    big = "正" * 200000
    ir._update_task("aaaabbbb", md_text=big)

    ir._update_task("aaaabbbb", message="正在写入向量索引（500/3000）…")

    assert ir._get_task("aaaabbbb")["md_text"] == big


def test_delete_and_iter(_db):
    ir.create_task("aaaabbbb")
    ir.create_task("ccccdddd")
    assert {t["task_id"] for t in ir.iter_tasks()} == {"aaaabbbb", "ccccdddd"}

    ir.delete_task("aaaabbbb")

    assert ir._get_task("aaaabbbb") is None
    assert {t["task_id"] for t in ir.iter_tasks()} == {"ccccdddd"}


def test_delete_missing_is_noop(_db):
    ir.delete_task("nope0000")      # 幂等：不得抛异常


def test_row_visible_from_new_connection(_db):
    """核心判据：写入后**另开连接**能读到 = 没有进程内状态 = 重启后仍在"""
    ir.create_task("eeeeffff")
    ir._update_task("eeeeffff", status="review_needed", progress=50, md_text="1.0.1 条文")

    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, md_text FROM import_tasks WHERE task_id = ?", ("eeeeffff",)
    ).fetchone()
    conn.close()

    assert row is not None, "另开连接读不到 → 说明还依赖进程内状态"
    assert row["status"] == "review_needed"
    assert row["md_text"] == "1.0.1 条文"
```

创建 `tests/test_import_no_memory_store.py`：

```python
"""台账不得退回进程内存：源码里不应再出现字典式用法

这是**删除型不变量**的守卫：跑测试证明不了"将来不会被加回来"，所以直接钉住符号不存在。
断言用代码形状（`progress_store.get(` 等）而不是裸词——裸词会命中正常叙述里的历史说明。
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "routes" / "import_routes.py"


def test_no_inmemory_progress_store_usage():
    text = SRC.read_text(encoding="utf-8")

    assert "progress_store =" not in text, "台账又变回内存字典了"
    assert "progress_store.get(" not in text, "仍在直接读内存字典"
    assert "progress_store.pop(" not in text, "仍在直接写内存字典"
    assert "progress_store[" not in text, "仍在直接下标内存字典"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -m pytest tests/test_import_task_store.py tests/test_import_no_memory_store.py -v
```
Expected: FAIL —— `AttributeError: module 'app.routes.import_routes' has no attribute 'create_task'`；另一文件断言命中 `progress_store =`

- [ ] **Step 3: 建表**

在 `app/database.py` 的 `SCHEMA_SQL` **末尾**（`CREATE INDEX IF NOT EXISTS idx_qa_messages_session …` 之后、闭合 `"""` 之前）追加：

```sql
CREATE TABLE IF NOT EXISTS import_tasks (
    task_id          TEXT PRIMARY KEY,
    status           TEXT NOT NULL,        -- uploading/processing/review_needed/done/error
    progress         INTEGER NOT NULL DEFAULT 0,
    message          TEXT NOT NULL DEFAULT '',
    owner            TEXT NOT NULL DEFAULT '',
    updated_at       REAL NOT NULL,        -- epoch 秒；超期清理靠它算年龄
    md_text          TEXT,                 -- Phase 1 产物：审查页与"待审查"复活的唯一来源
    title            TEXT,
    code             TEXT,
    file_path        TEXT,
    file_name        TEXT,
    file_hash        TEXT,
    spec_status      TEXT,
    replaced_by_code TEXT
);
```

`init_db()` 无需改动：它已经 `conn.executescript(SCHEMA_SQL)`。

- [ ] **Step 4: 换存储层实现**

> `app/routes/import_routes.py:12` **已经**有 `from app.database import get_db`，本步不需要
> 新增任何导入（先 `grep -n "from app.database import" app/routes/import_routes.py` 确认一次，
> 别加重复导入）。

**删掉** `progress_store = {}` 那一行，并把 `_get_task` / `_update_task` 整体替换为：

```python
# 台账可写列的单一来源：列名要拼进 SET 子句，故必须白名单校验
# （既防注入，也防字段名打错后静默写不进去）
_TASK_COLUMNS = frozenset({
    "status", "progress", "message", "md_text", "title", "code",
    "file_path", "file_name", "file_hash", "spec_status", "replaced_by_code",
})


def create_task(task_id: str, owner: str = "") -> None:
    """登记一条导入任务（初始 uploading）"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO import_tasks (task_id, status, progress, message, owner, updated_at) "
            "VALUES (?, 'uploading', 0, '正在上传...', ?, ?)",
            (task_id, owner, time.time()),
        )


def _get_task(task_id: str) -> dict | None:
    """取任务台账条目；不存在返回 None。

    台账取用一律走本函数与 `_update_task`。`cancel_review` 只在 `status == "done"`
    时拒绝取消，也就是**允许取消 processing 中的任务** —— 该任务随后的状态写入必须
    安全地失败，而不是抛异常。

    返回**普通 dict**：调用方大量使用 `task.get(...)`，而 `sqlite3.Row` 没有 `.get`。
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM import_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _update_task(task_id: str, **fields) -> bool:
    """安全更新台账条目；任务不存在时返回 False（调用方据此提前收工）。

    顺带盖 `updated_at`：超期清理全靠它算年龄。

    ⚠ **只写传入的字段**，绝不"读全行 → 合并 → 整行写回"：OCR 期间 progress_cb 会
    反复只更新 message，整行写回会把 md_text（实测最大 460 KB）每次重写一遍。
    """
    unknown = set(fields) - _TASK_COLUMNS
    if unknown:
        raise ValueError(f"未知台账字段: {sorted(unknown)}")
    values = dict(fields)
    values["updated_at"] = time.time()
    assignments = ", ".join(f"{col} = ?" for col in values)   # 列名已过白名单
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE import_tasks SET {assignments} WHERE task_id = ?",
            (*values.values(), task_id),
        )
        return cur.rowcount > 0


def delete_task(task_id: str) -> None:
    """删除台账条目（幂等：不存在也无妨）"""
    with get_db() as conn:
        conn.execute("DELETE FROM import_tasks WHERE task_id = ?", (task_id,))


def iter_tasks() -> list[dict]:
    """列出全部台账条目（供超期清理遍历）"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM import_tasks").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 5: 替换创建处**

`task_id = uuid.uuid4().hex[:8]` 之后那段字典赋值：

```python
    task_id = uuid.uuid4().hex[:8]
    # 记录属主：取消/确认仅限本人（防御越权删除他人任务）
    progress_store[task_id] = {
        "status": "uploading", "progress": 0, "message": "正在上传...",
        "owner": getattr(request.state, "username", ""),
        # 台账超期清理靠它算年龄（后续每次 _update_task 都会刷新）
        "updated_at": time.time(),
    }
```

改为：

```python
    task_id = uuid.uuid4().hex[:8]
    # 记录属主：取消/确认仅限本人（防御越权删除他人任务）
    create_task(task_id, owner=getattr(request.state, "username", ""))
```

- [ ] **Step 6: 替换 10 处 `.get` 直读**

四处 `log_action` 的属主取用（形态完全一致）：

```python
                   username=progress_store.get(task_id, {}).get("owner", "system"))
```
→
```python
                   username=(_get_task(task_id) or {}).get("owner", "system"))
```

两处进度端点（`get_progress` 与 `get_progress_json`）里的：

```python
    p = progress_store.get(task_id, {"status": "unknown", "progress": 0, "message": "未知任务"})
```
→
```python
    p = _get_task(task_id) or {"status": "unknown", "progress": 0, "message": "未知任务"}
```

四处任务读取（`review_page` / `review_content` / `confirm_review` / `cancel_review`）里的：

```python
    task = progress_store.get(task_id)
```
→
```python
    task = _get_task(task_id)
```

- [ ] **Step 7: 替换 2 处 pop 与 sweep 的遍历/pop**

（a）上传失败回滚（`if not file.filename:` 分支内）：

```python
        progress_store.pop(task_id, None)
```
→
```python
        delete_task(task_id)
```

（b）`cancel_review` 结尾：

```python
    # 移除内存任务（幂等：不存在也无妨）
    progress_store.pop(task_id, None)
```
→
```python
    # 移除台账条目（幂等：不存在也无妨）
    delete_task(task_id)
```

（c）`sweep_progress_store` 的**遍历来源与删除动作**（动作字符串的改名在 Task 3，此处不动）：

```python
    # 先固化键集再删：边遍历边 pop 会 RuntimeError，也避免受并发改动影响
    for task_id, task in list(progress_store.items()):
        action = _task_disposition(task, now, ttl_terminal_s, ttl_review_s)
        if action == "keep":
            continue
        if action == "drop_memory_and_disk" and delete_disk:
            try:
                _cleanup_task_artifacts(task_id)
                cleaned += 1
            except Exception as e:
                # 磁盘清理失败不得拦住内存清理，否则该条目会永远清不掉
                logger.warning("超期待审查任务的磁盘清理失败 task=%s: %s", task_id, e)
        progress_store.pop(task_id, None)
```
→
```python
    for task in iter_tasks():
        task_id = task["task_id"]
        action = _task_disposition(task, now, ttl_terminal_s, ttl_review_s)
        if action == "keep":
            continue
        if action == "drop_memory_and_disk" and delete_disk:
            try:
                _cleanup_task_artifacts(task_id)
                cleaned += 1
            except Exception as e:
                # 磁盘清理失败不得拦住删行，否则该条目会永远清不掉
                logger.warning("超期待审查任务的磁盘清理失败 task=%s: %s", task_id, e)
        delete_task(task_id)
```

- [ ] **Step 8: 清掉已经失真的注释与文案**

落库之后"服务重启会清空台账"不再成立，留着就是**代码在撒谎**。逐处改：

| 位置 | 现文案要点 | 改为 |
| --- | --- | --- |
| 上传处注释 | 「file_hash 检查之后、progress_store 注册之前」 | 「file_hash 检查之后、台账登记之前」 |
| `get_progress_json` docstring | 「未知任务（服务重启后 progress_store 清空）同样返回 200」 | 「未知任务（不存在或已被超期清理）同样返回 200」 |
| `cancel_review` docstring | 「任务不存在时仍执行磁盘清理——服务重启后 progress_store 被清空但磁盘残留仍在」 | 「任务不存在时仍执行磁盘清理（幂等兜底：台账条目可能已被超期清理，而磁盘残留仍在）」 |
| `app/config.py` 台账时限注释 | 「台账是纯内存结构，无 TTL 会只增不减」 | 「台账原先无 TTL，只增不减；现已落库（import_tasks），超期条目仍须回收，否则表只增不减」 |
| `app/params/registry.py` 两处 help | 「在**内存台账**里的保留时长」 | 「在台账里的保留时长」 |

- [ ] **Step 9: 改 A 组用例的夹具（字典 → 落库接口）**

`tests/test_import_task_lifecycle.py` 里所有 `ir.progress_store["x"] = {...}` 形态的夹具改为走
落库接口；`_update_task` 会盖 `updated_at`，所以"拨老年龄"必须发生在写入**之后**，为此在
文件顶部加一个辅助：

```python
def _age_task(ir, task_id, age_s):
    """把 updated_at 往回拨 age_s 秒（_update_task 会盖时间戳，只能在写入后改）"""
    from app.database import get_db
    with get_db() as conn:
        conn.execute("UPDATE import_tasks SET updated_at = ? WHERE task_id = ?",
                     (time.time() - age_s, task_id))
```

例如：

```python
    ir.progress_store["aaaabbbb"] = _task("done", age_s=TERM + 10)
```
改为：

```python
    ir.create_task("aaaabbbb")
    ir._update_task("aaaabbbb", status="done")
    _age_task(ir, "aaaabbbb", TERM + 10)
```

> 本步**只改夹具**：`_task_disposition` 的纯函数用例（不碰存储）与 sweep 的返回键
> （`{"memory": …}`）此步仍是旧形态，改名在 Task 3 完成。

并在同文件补一条持久化断言：

```python
def test_sweep_drops_row_from_store_not_just_cache(monkeypatch, _paths, _db):
    """终态超时：删的是**库里的行**——另开连接读不到（不是只清了某层缓存）"""
    import sqlite3
    up, _ = _paths
    src = up / "iiiijjjj.pdf"
    src.write_bytes(b"x")
    ir.create_task("iiiijjjj")
    ir._update_task("iiiijjjj", status="done")
    _age_task(ir, "iiiijjjj", TERM + 10)

    _ttls(monkeypatch)
    assert ir.sweep_progress_store(now=1000.0) == {"memory": 1, "disk": 0}

    conn = sqlite3.connect(str(_db))
    row = conn.execute("SELECT 1 FROM import_tasks WHERE task_id = ?",
                       ("iiiijjjj",)).fetchone()
    conn.close()
    assert row is None, "行还在库里 → 说明只清了缓存层"
    assert src.exists(), "终态清理不得删磁盘"
```

- [ ] **Step 10: 跑测试与类型检查**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -m pytest tests/test_import_task_store.py tests/test_import_no_memory_store.py tests/test_import_task_lifecycle.py tests/test_import.py tests/test_import_status.py tests/test_import_progress.py tests/test_import_progress_json.py tests/test_import_ui.py tests/test_import_duplicate.py tests/test_import_vector_batch.py tests/test_import_vector_failure.py tests/test_import_failure_wording.py tests/test_confirm_transport.py tests/test_ocr_review.py tests/test_degraded_heading.py -v
pyright app tests scripts
```
Expected: 全 PASS；pyright `0 errors, 0 warnings, 0 informations`

- [ ] **Step 11: 探针回归（服务端行为变更后的端到端）**

按 `scripts/probe_spec_ui.py` 顶部配方起隔离实例（**四条**环境变量），然后跑 spec §6.2 列明的
四组（f8 覆盖进度端点/浮标/弹窗恢复；f10 与 f11 都走审查页，而审查页的正文正是从台账读的）：

```bash
export PYTHONUTF8=1
for g in f7 f8 f10 f11; do
  D:/Python/python.exe scripts/probe_spec_ui.py $g
done
```
Expected: `== f7: 4/4 passed ==`、`== f8: 9/9 passed ==`、`== f10: 1/1 passed ==`、
`== f11: 4/4 passed ==`

跑完杀 8123 进程并删四个隔离目录。

- [ ] **Step 12: 提交**

```bash
git add app/database.py app/routes/import_routes.py app/config.py app/params/registry.py tests/test_import_task_store.py tests/test_import_no_memory_store.py tests/test_import_task_lifecycle.py
git commit -m "feat: 导入任务台账落库（import_tasks 表 + 存储层换 SQL + 调用点接线）"
```

---

### Task 2: 启动自愈

**Files:**
- Modify: `app/routes/import_routes.py`（新增 `heal_interrupted_tasks`）
- Modify: `app/main.py`（`startup()` 中调用）
- Create: `tests/test_import_task_heal.py`

**Interfaces:**
- Consumes: Task 1 的 `create_task` / `_get_task` / `_update_task`
- Produces: `heal_interrupted_tasks() -> int`（返回被标记的行数）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_import_task_heal.py`：

```python
"""启动自愈：被进程死亡打断的任务必须给出明确结论，而不是永远转圈

uploading/processing 的后台线程随进程消失，任务**不可能**再推进（用户裁定不自动
重跑：Phase 1 重跑会真调外部 OCR 且计费，Phase 2 重跑会重复入库）。
"""
import time

import pytest

from app.routes import import_routes as ir


@pytest.fixture
def _db(tmp_path, monkeypatch):
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "heal.db"))
    init_db()
    return tmp_path / "heal.db"


def test_heal_marks_running_states_as_error(_db):
    ir.create_task("aaaa0001")                                      # uploading
    ir.create_task("aaaa0002")
    ir._update_task("aaaa0002", status="processing", progress=20)    # processing

    assert ir.heal_interrupted_tasks() == 2

    for tid in ("aaaa0001", "aaaa0002"):
        task = ir._get_task(tid)
        assert task["status"] == "error"
        assert task["progress"] == 0
        assert "服务重启导致本次导入中断" in task["message"]


def test_heal_leaves_review_needed_alone(_db):
    """这是本设计的主要收益：OCR 已完成、待审查的任务**不能**被自愈碰掉"""
    ir.create_task("aaaa0003")
    ir._update_task("aaaa0003", status="review_needed", progress=50, md_text="1.0.1 正文")

    assert ir.heal_interrupted_tasks() == 0

    task = ir._get_task("aaaa0003")
    assert task["status"] == "review_needed"
    assert task["md_text"] == "1.0.1 正文"


def test_heal_leaves_terminal_states_alone(_db):
    ir.create_task("aaaa0004")
    ir._update_task("aaaa0004", status="done", progress=100, message="导入完成")
    ir.create_task("aaaa0005")
    ir._update_task("aaaa0005", status="error", progress=0, message="本次未完成，请重跑：X")

    assert ir.heal_interrupted_tasks() == 0
    assert ir._get_task("aaaa0004")["message"] == "导入完成"
    assert ir._get_task("aaaa0005")["message"] == "本次未完成，请重跑：X"


def test_heal_refreshes_updated_at(_db):
    """必须刷新 updated_at：否则中断很久的任务在重启瞬间就满足终态 TTL 被清理器删掉，
    用户刚看到提示、转身条目已消失——等于把"静默丢失"推迟几秒。"""
    from app.database import get_db
    ir.create_task("aaaa0006")
    ir._update_task("aaaa0006", status="processing")
    old = time.time() - 10 * 86400
    with get_db() as conn:
        conn.execute("UPDATE import_tasks SET updated_at = ? WHERE task_id = ?",
                     (old, "aaaa0006"))

    ir.heal_interrupted_tasks()

    assert ir._get_task("aaaa0006")["updated_at"] > old


def test_heal_is_idempotent(_db):
    ir.create_task("aaaa0007")
    ir._update_task("aaaa0007", status="processing")

    assert ir.heal_interrupted_tasks() == 1
    assert ir.heal_interrupted_tasks() == 0      # 第二遍无可改状态
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -m pytest tests/test_import_task_heal.py -v
```
Expected: FAIL —— `module 'app.routes.import_routes' has no attribute 'heal_interrupted_tasks'`

- [ ] **Step 3: 实现**

在 `app/routes/import_routes.py` 的 `iter_tasks` 之后追加：

```python
def heal_interrupted_tasks() -> int:
    """启动自愈：把已随进程消失的 running 态任务标为 error，返回影响行数。

    `uploading` / `processing` 的后台线程随进程死亡，任务不可能再推进；标成 error
    才能让用户看到明确结论，而不是一个永远转圈的"进行中"。

    **只动这两态**：`review_needed` / `done` / `error` 一律不动。待审查任务因此天然
    复活——它的正文与所需字段都在行里，审查页照旧可用。

    **顺带刷新 `updated_at`**：不刷的话，一个中断很久的任务在重启瞬间就满足终态 TTL
    被清理器立刻删掉，用户刚看到提示、转身条目已消失。
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE import_tasks SET status = 'error', progress = 0, "
            "message = '服务重启导致本次导入中断，请重新导入', updated_at = ? "
            "WHERE status IN ('uploading', 'processing')",
            (time.time(),),
        )
        return cur.rowcount
```

- [ ] **Step 4: 接进启动流程**

`app/main.py` 的 `startup()`：

```python
def startup():
    setup_logging()  # 先接日志桥，后续启动步骤的告警才能进 system_logs
    init_db()
    # 自愈必须**同步**执行、且排在 init_db 之后、开始接受请求之前：
    # 异步会让请求读到仍标 processing 的僵尸任务
    from app.routes.import_routes import heal_interrupted_tasks
    healed = heal_interrupted_tasks()
    if healed:
        logger.warning("启动自愈：%d 个导入任务因服务重启被中断，已标记为需重跑", healed)
    _startup_log_cleanup()
    _startup_import_sweeper()
    _startup_vector_sync()
    _startup_warmup_embedding()
    _startup_fts_optimize()
```

- [ ] **Step 5: 跑测试与类型检查**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -m pytest tests/test_import_task_heal.py tests/test_import_task_store.py -v
pyright app tests scripts
```
Expected: 全 PASS；pyright `0 errors`

- [ ] **Step 6: 提交**

```bash
git add app/routes/import_routes.py app/main.py tests/test_import_task_heal.py
git commit -m "feat: 导入台账启动自愈（中断的 running 任务标为需重跑）"
```

---

### Task 3: 超期清理改走表行 + 动作与返回键重命名

**Files:**
- Modify: `app/routes/import_routes.py`（`_task_disposition` 返回值、`sweep_progress_store` 返回键）
- Modify: `app/main.py`（`_startup_import_sweeper` 读的返回键与该 docstring）
- Modify: `tests/test_import_task_lifecycle.py`（动作串与返回键断言）

**Interfaces:**
- Consumes: Task 1 的 `iter_tasks` / `delete_task`
- Produces:
  - `_task_disposition(...) -> "keep" | "drop_row" | "drop_row_and_disk"`
  - `sweep_progress_store(now=None, *, delete_disk=True) -> {"rows": int, "disk": int}`
  - 两档时限参数与「被 specifications 引用则不删」的保护**均不变**

- [ ] **Step 1: 改断言（先让它失败）**

`tests/test_import_task_lifecycle.py`：

- 动作串 `drop_memory` → `drop_row`、`drop_memory_and_disk` → `drop_row_and_disk`（含
  `test_disposition_*` 与 `test_sweep_*`）；
- 返回键 `{"memory": …, "disk": …}` → `{"rows": …, "disk": …}`（含
  `test_sweep_drops_row_from_store_not_just_cache`、`test_sweep_skips_running_and_fresh`、
  `test_sweep_keeps_file_referenced_by_specifications`、`test_sweep_honors_param_ttls`）。

- [ ] **Step 2: 跑测试确认失败**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -m pytest tests/test_import_task_lifecycle.py -v
```
Expected: FAIL —— 实现仍返回 `drop_memory*` / `{"memory": …}`

- [ ] **Step 3: 改实现**

`_task_disposition` 的两处 return：

```python
    if status == "review_needed":
        return "drop_row_and_disk" if age > ttl_review_s else "keep"
    return "drop_row" if age > ttl_terminal_s else "keep"
```

`sweep_progress_store` 的循环体（`drop_*` 改名 + 返回键 `rows`）：

```python
        if action == "drop_row_and_disk" and delete_disk:
            ...
    return {"rows": dropped, "disk": cleaned}
```

`app/main.py` 的 `_startup_import_sweeper._run()`：

```python
                res = sweep_progress_store()
                if res["rows"]:
                    logger.info("导入任务台账回收：%d 行、磁盘 %d 份",
                                res["rows"], res["disk"])
```

并把该 docstring 里「台账是纯内存字典，原先无 TTL…」一句改为「台账原先无 TTL，只增不减；
现已落库（`import_tasks`），超期条目仍须回收，否则表只增不减」。

- [ ] **Step 4: 跑测试与类型检查**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -m pytest tests/test_import_task_lifecycle.py tests/test_import_task_store.py tests/test_import_task_heal.py tests/test_import.py tests/test_import_progress_json.py -v
pyright app tests scripts
```
Expected: 全 PASS；pyright `0 errors`

- [ ] **Step 5: 提交**

```bash
git add app/routes/import_routes.py app/main.py tests/test_import_task_lifecycle.py
git commit -m "refactor: 台账清理改走表行，动作与返回键更名（memory→row）"
```

---

### Task 4: 真实重启验收 + 读点延迟实测

**Files:** 无代码改动（产出验收记录，追加进 spec §6.2）

**Interfaces:**
- Consumes: Task 1–3 的全部实现

> 这是本设计**唯一的真凭据**：探针无法重启自己依赖的服务，因此"持久"只能这样验。
> 同时补上 spec §6.1 提出的读点延迟实测（落库后读点是否变慢）。

- [ ] **Step 1: 起隔离实例并造两个任务**

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_spec.db
export DATABASE_PATH="$PWD/data/_probe_spec.db"
export UPLOAD_DIR="$PWD/data/_probe_uploads"
export OUTPUT_DIR="$PWD/data/_probe_outputs"
export LANCE_DB_PATH="$PWD/data/_probe_lance"
mkdir -p "$UPLOAD_DIR" "$OUTPUT_DIR" "$LANCE_DB_PATH"
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 > .verify/_probe_uvicorn.log 2>&1 &
```

跑 `f8` 会留下一个真实 `review_needed` 任务：

```bash
export PYTHONUTF8=1
D:/Python/python.exe scripts/probe_spec_ui.py f8
```

从隔离输出目录反查它的任务号：

```bash
ls -t data/_probe_outputs/ | head -1
```

再手工塞一个"正在处理中"的僵尸（**在隔离库上**）：

```bash
D:/Python/python.exe -c "
import os
os.environ['DATABASE_PATH'] = os.path.abspath('data/_probe_spec.db')
from app.routes.import_routes import create_task, _update_task
create_task('dead0001', owner='admin')
_update_task('dead0001', status='processing', progress=20, message='正在 OCR 识别...')
print('僵尸任务已就位')
"
```

- [ ] **Step 2: 记录重启前基线，并实测读点延迟**

```bash
curl -s -c .verify/_cookie.txt -o /dev/null -X POST \
     -d "username=admin&password=admin123" http://127.0.0.1:8123/login
curl -s -b .verify/_cookie.txt http://127.0.0.1:8123/import/progress/<待审查task_id>/json
curl -s -b .verify/_cookie.txt http://127.0.0.1:8123/import/progress/dead0001/json

# 读点延迟：连打 20 次取平均值（spec §6.1 的风险项：落库后读点是否变慢）
for i in $(seq 20); do
  curl -s -o /dev/null -w "%{time_total}\n" -b .verify/_cookie.txt \
       http://127.0.0.1:8123/import/progress/<待审查task_id>/json
done | awk '{s+=$1; n++} END {printf "平均 %.1f ms（n=%d）\n", s/n*1000, n}'
```
Expected: 前者 `status=review_needed`、后者 `status=processing`；延迟平均 **< 50 ms**
（轮询间隔 2000 ms，只要远小于它就是安全的）。把实测值记下来，别写推算数字。

- [ ] **Step 3: 真停服务再启**

按项目 `CLAUDE.md` §三 的顺序杀进程（`netstat -ano | grep :8123 | grep LISTENING` →
`wmic … CommandLine` 找 `spawn_main` → 逐个 `taskkill //F //PID <pid>`），确认没有
`uvicorn`/`spawn_main` 活进程后，用与 Step 1 相同的四条环境变量重新启动（日志仍写
`.verify/_probe_uvicorn.log`）。

- [ ] **Step 4: 逐条核对（这就是验收证据）**

```bash
# ① 待审查任务必须**仍然存在**（重启前是 review_needed）
curl -s -b .verify/_cookie.txt http://127.0.0.1:8123/import/progress/<待审查task_id>/json
#    期望：status=review_needed、needs_review=true（**不是** unknown）

# ② 它的审查页仍可进、正文仍能取到
curl -s -b .verify/_cookie.txt -o /dev/null -w "review_page:%{http_code}\n" \
     http://127.0.0.1:8123/import/review/<待审查task_id>
curl -s -b .verify/_cookie.txt -w "\ncontent:%{http_code}\n" \
     http://127.0.0.1:8123/import/review/<待审查task_id>/content | head -c 200
#    期望：200 且返回正文（非 404）

# ③ 僵尸任务被自愈为 error，文案正确
curl -s -b .verify/_cookie.txt http://127.0.0.1:8123/import/progress/dead0001/json
#    期望：status=error、message 含「服务重启导致本次导入中断」

# ④ 启动日志里有自愈 WARN（可追溯）
grep -n "启动自愈" .verify/_probe_uvicorn.log
#    期望：命中一行，含被标记数量
```

浏览器侧补一条：登录 → 首页右下角浮标显示「📝 待审查」，点击进弹窗后能看到该任务并有
「去审查」入口。

- [ ] **Step 5: 清理并提交验收记录**

```bash
# 先杀 8123 进程，然后：
rm -rf data/_probe_spec.db data/_probe_uploads data/_probe_outputs data/_probe_lance
rm -f .verify/_cookie.txt .verify/_probe_uvicorn.log
```

把四条核对结果与延迟实测值（**原始输出，不要转述**）追加到
`docs/superpowers/specs/2026-10-02-import-task-persistence-design.md` 的 §6.2 末尾，
作为「实测记录」，然后：

```bash
git add docs/superpowers/specs/2026-10-02-import-task-persistence-design.md
git commit -m "docs: 回填台账持久化的真实重启验收与读点延迟实测记录"
```

---

### Task 5: 手工回填 `411d4975`（已授权）

**Files:**
- Create: `scripts/backfill_import_task.py`
- Modify: `docs/superpowers/specs/2026-10-02-import-task-persistence-design.md`（§8 标注已执行）

**Interfaces:**
- Consumes: Task 1 的 `create_task` / `_get_task` / `_update_task`
- Produces: `scripts/backfill_import_task.py`（默认干跑，`--apply` 才写库）

> **前置条件（缺一不可）**：Task 1–4 已验收（`import_tasks` 表已存在）；用户已提供该 PDF
> 对应的**规范编号与名称**。编号是规范的身份，**填错会污染库**——若无法准确给出，就不要
> 回填，改为重新导入（`data/uploads/411d4975.pdf` 仍在，重导会重跑 OCR）。

- [ ] **Step 1: 向用户索取编号与名称**

`411d4975` 的编号/名称随台账一起丢了（它们只存在那份被清空的内存里）。**先问用户**，
拿到后再往下走；不要猜、不要从 PDF 文件名推断（该文件名为任务号，无信息）。

- [ ] **Step 2: 写下脚本**

创建 `scripts/backfill_import_task.py`：

```python
"""手工回填一条 import_tasks 台账（用于持久化之前丢失、但 OCR 产物仍在磁盘的任务）。

默认**干跑**：只打印将要写入的内容，不碰数据库。加 --apply 才真正写入。

为什么需要：台账落库（2026-10-02）之前，任何 worker 重启都会清空台账，OCR 成果虽在
磁盘上却无入口可审查。本脚本为这类任务补一条处于 review_needed 的记录。

用法：
  D:/Python/python.exe scripts/backfill_import_task.py \\
      --task-id 411d4975 \\
      --md-path "data/outputs/411d4975/411d4975.md" \\
      --pdf-path "data/uploads/411d4975.pdf" \\
      --code "CJJ 2-2008" --title "城市桥梁工程施工与质量验收规范" --owner admin
  # 确认无误后追加 --apply
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="回填 import_tasks 台账（默认干跑）")
    ap.add_argument("--task-id", required=True, help="8 位十六进制任务号")
    ap.add_argument("--md-path", required=True, help="OCR 产物 md 的路径")
    ap.add_argument("--pdf-path", required=True, help="原始上传文件路径")
    ap.add_argument("--code", required=True, help="规范编号（**必须准确**，它是规范身份）")
    ap.add_argument("--title", required=True, help="规范名称")
    ap.add_argument("--owner", default="admin", help="任务属主（取消/确认的鉴权用）")
    ap.add_argument("--apply", action="store_true", help="真正写入（缺省只干跑）")
    args = ap.parse_args()

    if not re.fullmatch(r"[0-9a-f]{8}", args.task_id):
        print(f"❌ 任务号必须是 8 位十六进制小写：{args.task_id!r}")
        return 2
    md_path, pdf_path = Path(args.md_path), Path(args.pdf_path)
    for p in (md_path, pdf_path):
        if not p.is_file():
            print(f"❌ 文件不存在：{p}")
            return 2

    md_text = md_path.read_text(encoding="utf-8", errors="replace")
    payload = {
        "status": "review_needed",
        "progress": 50,
        "message": "OCR 完成，请审查识别结果",
        "md_text": md_text,
        "title": args.title,
        "code": args.code,
        "file_path": str(pdf_path),
        "file_name": pdf_path.name,
        "file_hash": _sha256(pdf_path),
        "spec_status": "现行",
        "replaced_by_code": "",
    }
    print(f"任务号 {args.task_id}｜正文 {len(md_text)} 字符｜原文件 {pdf_path.name}")
    for k, v in payload.items():
        print(f"  {k} = {f'{len(v)} 字符' if k == 'md_text' else v}")

    if not args.apply:
        print("\n（干跑）确认无误后加 --apply 写入。")
        return 0

    from app.database import init_db
    from app.routes.import_routes import create_task, _get_task, _update_task
    init_db()
    if _get_task(args.task_id) is not None:
        print(f"❌ 台账里已存在 {args.task_id}，不覆盖")
        return 1
    create_task(args.task_id, owner=args.owner)
    _update_task(args.task_id, **payload)

    from app.logging_util import json_detail, log_action
    log_action("import", "WARN", "手工回填导入台账",
               detail=json_detail({"task_id": args.task_id, "code": args.code,
                                   "md_chars": len(md_text),
                                   "by": "backfill_import_task.py"}),
               username=args.owner)
    task = _get_task(args.task_id)
    print(f"✅ 已写入：status={task['status']} updated_at={task['updated_at']:.0f}")
    print(f"   审查页：/import/review/{args.task_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 干跑（不写库）**

```bash
cd /d/CC-Workspace/construction-spec-query-v2
export PYTHONUTF8=1
D:/Python/python.exe scripts/backfill_import_task.py \
    --task-id 411d4975 \
    --md-path "data/outputs/411d4975/411d4975.md" \
    --pdf-path "data/uploads/411d4975.pdf" \
    --code "<Step 1 拿到的编号>" --title "<Step 1 拿到的名称>" --owner admin
```
Expected: 打印各字段（正文显示为字符数），末行「（干跑）确认无误后加 --apply 写入。」

**把干跑输出给用户过一眼再继续**——编号一旦填错会污染库。

- [ ] **Step 4: 写入（用户确认后）**

```bash
D:/Python/python.exe scripts/backfill_import_task.py \
    --task-id 411d4975 \
    --md-path "data/outputs/411d4975/411d4975.md" \
    --pdf-path "data/uploads/411d4975.pdf" \
    --code "<Step 1 拿到的编号>" --title "<Step 1 拿到的名称>" --owner admin --apply
```
Expected: `✅ 已写入：status=review_needed …`

- [ ] **Step 5: 验证审查页可进**

```bash
curl -s -c .verify/_cookie.txt -o /dev/null -X POST \
     -d "username=admin&password=admin123" http://127.0.0.1:8000/login
curl -s -b .verify/_cookie.txt -o /dev/null -w "page:%{http_code}\n" \
     http://127.0.0.1:8000/import/review/411d4975
curl -s -b .verify/_cookie.txt -o /dev/null -w "content:%{http_code}\n" \
     http://127.0.0.1:8000/import/review/411d4975/content
rm -f .verify/_cookie.txt
```
Expected: `page:200`、`content:200`（404 说明回填未生效）

再在浏览器走一遍：首页 → 浮标「📝 待审查」→ 点进弹窗 → 「去审查」→ 审查页有正文与图片。

- [ ] **Step 6: 更新 spec 并提交**

把 spec §8 第一条标注为「✅ 已执行（日期 + 编号/名称 + 干跑与写入输出摘要）」，然后：

```bash
git add scripts/backfill_import_task.py docs/superpowers/specs/2026-10-02-import-task-persistence-design.md
git commit -m "feat: 导入台账回填脚本（默认干跑）+ 回填 411d4975"
```

---

## 附：本计划明确不做的事

- **不把台账拆到独立模块**（如 `app/import_store.py`）：虽然"路由模块持有持久化"有味道，
  但 A 组的回归网是按 `app.routes.import_routes` 的 monkeypatch 目标建的（`ir.UPLOAD_DIR`
  等），搬模块会削弱刚建好的网。列为后续独立重构。
- **不加进程内缓存**（有缓存就等于把"重启丢失"换个形式再引入一次；这里是主键点查）。
- **不自动重跑被中断的任务**、**不做 Phase 2 续跑**（用户裁定 + 见 spec §4.2）。
- **不动 `specifications` 表结构**；不回填 `411d4975` 之外的任何历史任务。
- **不清空 A 组建立的探针网**（f7/f8/f9/f10/f11 全部保留，本计划只新增断言不删用例）。
