# QA 后端：会话持久化 + 多轮上下文 + 流式 + 降级修正 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 问答补齐服务端能力——历史会话持久化与续聊、多轮上下文注入、Markdown 导出、跨会话搜索、SSE 流式输出，并修正无模型环境下的精排降级缺陷。

**Architecture:** 在既有 QA 链路上做加法。会话内容落两张新表（`qa_sessions` / `qa_messages`），与既有埋点表 `qa_request_logs` 职责分离。`/qa/ask` 增加可选 `session_id` 实现惰性建会话与续聊。**流式不新增路由**——`/qa/ask` 加一个 `stream` 开关分派两种响应形态，检索准备逻辑抽为 `_prepare_qa_context` 由两者共用（评审决定 D3：独立路由会复制整条链路，已因此产生埋点缺失与全局变量竞态两个缺陷）。降级链保持三级不变，仅修正第 3 级的分数语义。

**Tech Stack:** FastAPI · SQLite（`sqlite3` + `get_db()` 上下文管理器）· httpx（SSE 流式）· pytest

**Spec:** `docs/superpowers/specs/2026-09-21-qa-history-session-design.md`

## Global Constraints

- 数据库操作**必须**参数化查询，禁止字符串拼接 SQL（开发铁律 1.1）
- 所有外部输入必须做合法性与范围校验（开发铁律 1.1）
- 敏感信息（API key、内网地址）禁止硬编码（开发铁律 1.1）
- 网络请求/IO 必须捕获**指定**异常，禁止裸 `except:`；保留原始错误堆栈（开发铁律 1.2）
- 接口返回格式统一，禁止「成功返回数组、失败返回 null」（开发铁律 1.2）
- 循环内禁止执行数据库查询、文件读写、网络请求（开发铁律 1.3）；批量操作走批量接口
- 业务常量集中管理，禁止魔法数字散落（开发铁律 1.3）
- 统一分级日志，禁止用 `print` 输出业务日志（开发铁律 1.3）
- TDD：先写测试再实现；每个 Task 覆盖正常/边界/异常三类场景
- 每个 Task 完成后跑**增量测试**（本 Task 关联用例）+ `pyright`（不得新增 error），再 commit
- 提交信息格式 `type: 描述`，type ∈ `feat / fix / test / docs / refactor / chore`，单 Task 单提交
- Python 一律用 `D:/Python/python.exe`（禁用 `python3`）；测试命令 `D:/Python/python.exe -m pytest`
- 模型加载**永不联网**（`local_files_only=True`），不得引入自动下载

## 测试基础设施（**每个 Task 动笔前先读这一节**）

本项目的测试惯例与直觉写法有四处不同，照抄下面的写法，否则测试必然失败：

**1. 数据库隔离用 patch 模块变量，不是环境变量**

```python
monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "x.db"))
from app.database import init_db
init_db()
```

`app/database.py:178` 直接读模块级 `DATABASE_PATH` 常量，**没有环境变量入口**。

**2. QA 路由有鉴权，必须用 conftest 的 `auth_client` 夹具**

`app/main.py:129` 注册了 `AuthMiddleware`，所有路由需登录。`tests/conftest.py:40` 的 `auth_client` 已建库、建用户、登录完毕，**直接用它**：

```python
def test_something(auth_client):
    resp = auth_client.post("/qa/ask", json={"question": "x"})
```

> `auth_client` 内部已 patch `DATABASE_PATH` 为 `tmp_path/"test_auth.db"` 并 `init_db()`，所以**不要**再叠加自己的 DB fixture，否则两者互相覆盖。

**3. 函数内局部 import 的目标，要 patch 源模块，不能 patch 路由模块**

`qa_routes.py` 的 import 是**函数内**的（如 `from app.search.hybrid_search import hybrid_search`），
所以 `app.routes.qa_routes.hybrid_search` **不存在**，patch 它会 `AttributeError`。
`from X import Y` 在函数调用时才解析，因此 patch 源模块即可生效：

| 想替换 | 正确 patch 目标 | 错误写法 |
|---|---|---|
| `hybrid_search` | `app.search.hybrid_search.hybrid_search` | ~~`app.routes.qa_routes.hybrid_search`~~ |
| `get_backend` | `app.ai.cli_client.get_backend` | ~~`app.routes.qa_routes.get_backend`~~ |
| `build_history` | `app.qa.context.build_history` | ~~`app.routes.qa_routes.build_history`~~ |
| `_rerank_scored` | `app.routes.qa_routes._rerank_scored` ✅ | （这个是模块级的，可以） |

**4. `conftest.py` 已有 autouse 夹具**（`_mock_search_rerank`）把 `rerank_candidates` mock 成原序，
避免测试真去加载模型。**不要**删除或覆盖它。

**本计划新增的共享夹具 `qa_db`** 在 **Task 3** 中写入 `tests/conftest.py`（该 Task 是第一个需要真库的），
Task 4 起所有不走 HTTP 的单元测试直接使用它。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/qa/degrade.py` | 精排降级级别的枚举与排名归一化分数 | **新建** |
| `app/qa/sessions.py` | 会话/消息的持久化层（CRUD + 搜索 + 导出数据） | **新建** |
| `app/qa/context.py` | 候选过滤 / 分层 / 上下文组装；**新增多轮历史段 `build_history`** | 修改 |
| `app/qa/context.py` | 候选过滤/分层/上下文组装（既有） | 不改（见 T1 说明） |
| `app/routes/qa_routes.py` | QA 路由与链路编排 | 修改 |
| `app/ai/api_client.py` | API 后端（含新增流式） | 修改 |
| `app/ai/prompts.py` | system prompt 常量 | 修改 |
| `app/config.py` | `QA_CONFIG_DEFAULTS` | 修改 |
| `app/params/registry.py` | 参数注册表 | 修改 |
| `app/database.py` | 建表与迁移 | 修改 |
| `app/maintenance/health_check.py` | 健康检查项 | 修改 |
| `app/models.py` | `QaRequest` / `QAResponse` | 修改 |
| `tests/test_qa_degrade.py` | T1/T2 用例 | **新建** |
| `tests/test_qa_sessions.py` | T4/T5/T6 用例 | **新建** |
| `tests/test_qa_history.py` | T7/T8 用例 | **新建** |
| `tests/test_qa_session_routes.py` | T9~T12 用例 | **新建** |
| `tests/test_qa_relax.py` | T13 用例 | **新建** |
| `tests/test_qa_stream.py` | T14/T15 用例 | **新建** |

> **T1 为何不改 `context.py`**：第 3 级降级的问题不是分层函数写错了，而是喂给它的分数没有绝对意义。改用「排名归一化分数 + `min_score=0` + `high_threshold=2/3`」后，既有 `filter_by_score` / `tier_items` **原样可用**，改动面最小、回归风险最低。

---

## Task 1: 精排降级级别建模 + 第 3 级按排名切分

**Files:**
- Create: `app/qa/degrade.py`
- Modify: `app/routes/qa_routes.py:30-67`（`_rerank_scored`）、`app/routes/qa_routes.py:227-237`（阈值选择）
- Test: `tests/test_qa_degrade.py`

**Interfaces:**
- Consumes: 无（本 Task 是起点）
- Produces:
  - `app.qa.degrade.RERANK_CE: str = "crossencoder"`、`RERANK_VECTOR: str = "vector"`、`RERANK_NONE: str = "none"`
  - `app.qa.degrade.rank_scores(n: int) -> list[float]` — 1-based 排名归一化到 `[0, 1)`，降序
  - `app.qa.degrade.resolve_thresholds(rerank_used: str, get_float) -> tuple[float, float]` — 返回 `(min_score, high_threshold)`
  - `_rerank_scored` 签名不变，第 3 级返回排名归一化分数而非全 `1.0`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_qa_degrade.py
"""精排降级：级别建模、排名归一化分数、阈值解析。"""
import pytest

from app.qa.degrade import (
    RERANK_CE, RERANK_VECTOR, RERANK_NONE,
    rank_scores, resolve_thresholds,
)


def test_rank_scores_is_descending_and_within_unit_interval():
    """正常场景：分数降序、落在 [0, 1)，首项最大。"""
    scores = rank_scores(10)
    assert len(scores) == 10
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s < 1.0 for s in scores)


def test_rank_scores_boundaries():
    """边界场景：n=0 返回空；n=1 返回单元素。"""
    assert rank_scores(0) == []
    assert len(rank_scores(1)) == 1


def test_rank_scores_never_all_one():
    """异常场景（回归守卫）：第 3 级降级不得再把分数拍平成全 1.0。

    全 1.0 会让 tier_items 把全部候选判为 high（强相关），
    导致全部全文进上下文、摘要压缩失效、token 预算被吃光。
    """
    scores = rank_scores(30)
    assert len(set(scores)) > 1, "分数必须能区分次序，不能全相同"
    assert max(scores) < 1.0


def test_rank_scores_splits_top_third_as_high():
    """正常场景：配合 high_threshold=2/3 时，恰好前 1/3 落入 high 区。"""
    n = 30
    scores = rank_scores(n)
    high_thr = 2.0 / 3.0
    high_count = sum(1 for s in scores if s >= high_thr)
    assert high_count == n // 3


def test_resolve_thresholds_ce_uses_rerank_set():
    """正常场景：CE 可用时用 qa.rerank.* 阈值集。"""
    fake = {"rerank.min_score": 0.50, "rerank.high_threshold": 0.80}
    min_score, high_thr = resolve_thresholds(RERANK_CE, fake.__getitem__)
    assert (min_score, high_thr) == (0.50, 0.80)


def test_resolve_thresholds_vector_uses_vector_set():
    """正常场景：向量降级时用 qa.vector.* 阈值集。"""
    fake = {"vector.min_score": 0.30, "vector.high_threshold": 0.55}
    min_score, high_thr = resolve_thresholds(RERANK_VECTOR, fake.__getitem__)
    assert (min_score, high_thr) == (0.30, 0.55)


def test_resolve_thresholds_none_disables_absolute_threshold():
    """异常场景：第 3 级降级不得沿用向量阈值。

    RRF 排名无绝对相关度语义，沿用 0.30/0.55 会误杀或误判，
    必须改为 min_score=0（不丢条）+ high_threshold=2/3（按排名切强弱）。
    """
    min_score, high_thr = resolve_thresholds(RERANK_NONE, lambda k: 0.30)
    assert min_score == 0.0
    assert high_thr == pytest.approx(2.0 / 3.0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_degrade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.qa.degrade'`

- [ ] **Step 3: 实现 `app/qa/degrade.py`**

```python
"""精排降级级别建模与分数语义。

三级降级链（见设计文档 §4.13）：
  1. crossencoder —— CrossEncoder 可用，分数 = 模型输出（0~1），有绝对相关度语义
  2. vector       —— 降级 bi-encoder 余弦相似度（-1~1），有绝对语义，用独立阈值集
  3. none         —— 两者皆不可用，**无任何绝对相关度语义**

第 3 级的关键：候选只有 RRF 排名，第 30 名的条文未必不相关。因此
- 不设绝对丢弃线（min_score = 0，不丢任何条）
- 强弱按**排名**切分：前 1/3 为高相关（全文），后 2/3 为次相关（摘要）

历史上第 3 级把分数拍平成全 1.0，导致 tier_items 把全部候选判为 high、
摘要压缩完全失效、token 预算迅速耗尽。本模块的 rank_scores 即为修正。
"""

# 降级级别常量（禁止在业务代码中散落字符串字面量）
RERANK_CE = "crossencoder"
RERANK_VECTOR = "vector"
RERANK_NONE = "none"

# 第 3 级降级：按排名切分强弱，前 1/3 为高相关
_RANK_HIGH_RATIO = 2.0 / 3.0


def rank_scores(n: int) -> list[float]:
    """把 1-based 排名归一化为 [0, 1) 的分数，降序（首位最大）。

    仅表达**相对次序**，不表达绝对相关度。n=0 时返回空列表。
    用途：第 3 级降级时替代「全 1.0」，使 tier_items 能按排名切分强弱。
    """
    if n <= 0:
        return []
    return [(n - i) / n for i in range(1, n + 1)]


def resolve_thresholds(rerank_used: str, get_float) -> tuple[float, float]:
    """按实际生效的精排级别解析 (min_score, high_threshold)。

    get_float(key) 由调用方注入（通常为 app.qa.config.get_qa_float），
    以便本模块可独立测试。
    """
    if rerank_used == RERANK_CE:
        return get_float("rerank.min_score"), get_float("rerank.high_threshold")
    if rerank_used == RERANK_VECTOR:
        return get_float("vector.min_score"), get_float("vector.high_threshold")
    # 第 3 级：无绝对语义 → 不丢条 + 按排名切强弱
    return 0.0, _RANK_HIGH_RATIO
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_degrade.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 把 `_rerank_scored` 与阈值选择接上 degrade 模块**

修改 `app/routes/qa_routes.py` 顶部 import 区，新增：

```python
from app.qa.degrade import (
    RERANK_CE, RERANK_VECTOR, RERANK_NONE, rank_scores, resolve_thresholds,
)
```

把 `_rerank_scored` 中三处赋值 `_last_rerank_used = "..."` 改为使用常量，并把第 3 级的返回值改为排名分数。函数体改为：

```python
def _rerank_scored(question: str, candidates: list[dict]) -> list[tuple[dict, float]]:
    """精排打分，返回 (候选, 分数) 按分数降序全部候选（不在此截断条数）。

    - 候选 ≤ 1：直接返回 [(c, 1.0)]，不打分、不分层。
    - CrossEncoder 可用：分数 = 模型输出（量纲约 0~1）。
    - 降级 bi-encoder 向量：分数 = 余弦相似度（量纲 -1~1），阈值用 qa.vector.* 独立集。
    - 两者皆不可用：分数 = **排名归一化值**（无绝对相关度语义），
      阈值改为 min_score=0 + high_threshold=2/3，按排名切分强弱。
    """
    global _last_rerank_used
    if len(candidates) <= 1:
        _last_rerank_used = RERANK_NONE
        return [(c, 1.0) for c in candidates]

    texts = [(c.get("content") or "")[:300] for c in candidates]

    try:
        from app.ai.reranker import rerank
        scores = rerank(question, texts)
        if scores is not None:
            _last_rerank_used = RERANK_CE
            ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            return [(c, float(s)) for c, s in ranked]
    except Exception as e:
        logger.warning("CrossEncoder 精排异常: %s", e)

    try:
        from app.ai.embedding import embed_texts
        import numpy as np
        q_vec = np.array(embed_texts([question])[0], dtype=np.float32)
        emb = np.array(embed_texts(texts), dtype=np.float32)
        scores = np.dot(emb, q_vec)
        _last_rerank_used = RERANK_VECTOR
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [(c, float(s)) for c, s in ranked]
    except Exception as e:
        logger.warning("向量重排序失败，降级为原始顺序: %s", e)

    _last_rerank_used = RERANK_NONE
    # 修正（设计文档 §4.13 缺口 1）：不再返回全 1.0，改按排名归一化。
    # hybrid_search 的候选本身已按 RRF 分数降序，此处名次即相对相关度。
    return list(zip(candidates, rank_scores(len(candidates))))
```

把阈值选择段（`qa_routes.py:227-237` 附近）改为：

```python
    ranked = _rerank_scored(question, candidates)
    trace.rerank_used = _last_rerank_used
    min_score, high_thr = resolve_thresholds(_last_rerank_used, get_qa_float)
    ranked = filter_by_score(ranked, min_score)
    trace.after_threshold = len(ranked)
```

- [ ] **Step 6: 跑增量测试 + 类型检查**

Run: `D:/Python/python.exe -m pytest tests/test_qa_degrade.py tests/test_qa_routes.py tests/test_qa_context.py -v`
Expected: 全部 PASS（既有 QA 用例不得回归）

Run: `D:/Python/python.exe -m pyright app/qa/degrade.py app/routes/qa_routes.py`
Expected: 无新增 error

- [ ] **Step 7: 提交**

```bash
git add app/qa/degrade.py app/routes/qa_routes.py tests/test_qa_degrade.py
git commit -m "fix: 精排第 3 级降级改按排名切分强弱（修正分层失效）"
```

---

## Task 2: 降级状态透出到响应

**Files:**
- Modify: `app/models.py`（`QAResponse`）
- Modify: `app/routes/qa_routes.py`（`/qa/ask` 返回体）
- Test: `tests/test_qa_degrade.py`（追加）

**Interfaces:**
- Consumes: `app.qa.degrade.RERANK_CE / RERANK_VECTOR / RERANK_NONE`（T1）
- Produces: `QAResponse.rerank_used: str` — 值为 T1 的三个常量之一，供前端展示状态标记

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_degrade.py`：

```python
def test_qa_response_exposes_rerank_used():
    """正常场景：响应必须透出实际生效的精排级别，供前端提示降级。"""
    from app.models import QAResponse
    resp = QAResponse(answer="x", rerank_used=RERANK_NONE)
    assert resp.rerank_used == RERANK_NONE


def test_qa_response_rerank_used_defaults_to_empty():
    """边界场景：缺省为空串（兼容既有调用方，不破坏构造签名）。"""
    from app.models import QAResponse
    assert QAResponse(answer="x").rerank_used == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_degrade.py::test_qa_response_exposes_rerank_used -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'rerank_used'`

- [ ] **Step 3: 实现**

在 `app/models.py` 的 `QAResponse` 中，于 `cli_used` 之后新增：

```python
    # 实际生效的精排级别（crossencoder / vector / none）；前端据此提示降级
    rerank_used: str = ""
```

在 `app/routes/qa_routes.py` 的 `/qa/ask` 返回处，改为：

```python
    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits,
                      rerank_used=trace.rerank_used)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_degrade.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add app/models.py app/routes/qa_routes.py tests/test_qa_degrade.py
git commit -m "feat: QA 响应透出实际生效的精排级别"
```

---

## Task 3: 健康检查增加模型就绪项

**Files:**
- Modify: `app/maintenance/health_check.py`（`LABELS`、`run_health_check`）
- Modify: `app/ai/reranker.py`、`app/ai/embedding.py`（各新增 `is_ready()`）
- Modify: `tests/conftest.py`（新增共享夹具 `qa_db`）
- Test: `tests/test_health_check_models.py`

**Interfaces:**
- Consumes: `app.ai.reranker.is_ready()`、`app.ai.embedding.is_ready()`（本 Task 新增，**不触发模型加载**）
- Produces: 检查项 key `"model_ready"`，`severity ∈ {"ok", "warn", "error"}`，含 `hint` 字段说明缺失后果
- Produces: `app.ai.reranker.is_ready()` / `app.ai.embedding.is_ready()`（供健康检查等只读场景使用）
- Produces: 夹具 `qa_db`（隔离库、无用户），Task 4 起被多个测试文件复用

> 说明：两个模型模块内部用三态哨兵缓存失败态（`False`）。**健康检查不得调用
> `get_reranker()` / `get_model()`**——未加载时它们会真实例化模型（数秒），
> 而本检查在维护页每次打开都跑。改走新增的 `is_ready()`（只探测，不加载）。
> 另：两者均已是 `local_files_only=True`，任何路径都不会触发网络下载。

- [ ] **Step 1: 写失败测试**

先给 `tests/conftest.py` 追加共享夹具（放在既有 `auth_client` 之后）。**必须放 conftest**——
放在测试文件里是文件局部的，Task 4 起其它测试文件用不到：

```python
@pytest.fixture()
def qa_db(tmp_path, monkeypatch):
    """隔离数据库（仅建表、无用户），供不走 HTTP 的单元测试使用。

    需要它是因为 run_health_check 会写 system_logs / 快照表，
    会话持久化层测试也要真库——不隔离会污染 dev 库。
    走 patch 模块常量的方式（见测试基础设施 §1），不要用环境变量。
    """
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "qa_unit.db"))
    init_db()
```

再写测试文件：

```python
# tests/test_health_check_models.py
"""健康检查：AI 模型就绪项（分享场景下用于告知缺什么）。"""
from unittest.mock import patch

from app.maintenance.health_check import run_health_check


def test_model_ready_ok_when_both_models_available(qa_db):
    """正常场景：两个模型都在 → ok，且无 hint。"""
    with patch("app.ai.reranker.get_reranker", return_value=object()), \
         patch("app.ai.embedding.get_model", return_value=object()):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "ok"
    assert item["count"] == 0


def test_model_ready_warns_when_reranker_missing(qa_db):
    """边界场景：仅缺 CrossEncoder → warn，提示精排降级。"""
    with patch("app.ai.reranker.get_reranker", return_value=None), \
         patch("app.ai.embedding.get_model", return_value=object()):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "warn"
    assert "精排" in item["hint"]


def test_health_check_does_not_instantiate_models(qa_db):
    """异常场景（副作用守卫）：健康检查不得触发模型加载。

    get_reranker()/get_model() 在未加载时会真实例化模型（数秒），而本检查
    在维护页每次打开都跑。用「调用即抛」的桩钉住这一点。
    """
    def _boom(*a, **k):
        raise AssertionError("健康检查触发了模型实例化")

    with patch("app.ai.reranker.get_reranker", _boom),          patch("app.ai.embedding.get_model", _boom),          patch("app.ai.reranker.is_ready", return_value=True),          patch("app.ai.embedding.is_ready", return_value=True):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "ok"


def test_model_ready_errors_when_embedding_missing(qa_db):
    """异常场景：缺 embedding → 向量召回一并失效，严重度高于仅缺精排。"""
    with patch("app.ai.reranker.get_reranker", return_value=None), \
         patch("app.ai.embedding.get_model", return_value=None):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "error"
    assert "向量召回" in item["hint"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_health_check_models.py -v`
Expected: FAIL — `StopIteration`（`checks` 中无 `model_ready` 项）

- [ ] **Step 3: 实现**

在 `app/maintenance/health_check.py` 的 `LABELS` 中追加一项：

```python
    "model_ready": "AI 模型就绪",
```

新增检查函数（放在 `_count_fts_mismatch` 之后）：

```python
def _check_models() -> tuple[str, str]:
    """返回 (severity, hint)，探测两个本地模型是否就绪。

    分享场景下用户常常没放模型文件，而系统只会静默降级——此处显式暴露。
    - 缺 CrossEncoder：精排降级为向量/排名，质量下降但可用 → warn
    - 缺 embedding  ：向量召回一并失效，hybrid_search 退化为纯关键词 → error

    **用 `is_ready()` 探测，不调用 `get_reranker()` / `get_model()`**——
    后者在未加载时会真的实例化模型（数秒），而本检查在维护页每次打开都跑，
    不能带这种副作用。
    """
    from app.ai.reranker import is_ready as reranker_ready
    from app.ai.embedding import is_ready as embedding_ready

    has_reranker = reranker_ready()
    has_embedding = embedding_ready()

    if has_reranker and has_embedding:
        return "ok", ""
    if not has_embedding:
        return "error", (
            "embedding 模型（bge-small-zh-v1.5）缺失：向量召回已失效，"
            "检索退化为纯关键词匹配。请将模型放入 models/BAAI/ 下。"
        )
    return "warn", (
        "CrossEncoder 精排模型（bge-reranker-base）缺失："
        "精排已降级为向量/排名排序，问答与检索质量下降。"
        "请将模型放入 models/BAAI/ 下。"
    )
```

在 `run_health_check` 的 `counts` 计算之后、`checks` 循环之前插入：

```python
    model_severity, model_hint = _check_models()
```

在 `checks` 循环内，`if key == "vector_missing":` 之前插入：

```python
        if key == "model_ready":
            item.update(severity=model_severity, count=0,
                        count_text="✅ 就绪" if model_severity == "ok" else "⚠️ 缺失",
                        status_text="", hint=model_hint)
            checks.append(item)
            continue
```

同时把 `counts` 字典补上占位键，避免 `counts[key]` KeyError：

```python
        "model_ready": 0,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_health_check_models.py -v`
Expected: PASS（3 passed）

Run: `D:/Python/python.exe -m pytest tests/ -k "health" -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/maintenance/health_check.py tests/test_health_check_models.py
git commit -m "feat: 健康检查增加 AI 模型就绪项（分享场景告知缺什么）"
```

---

## Task 4: 会话与消息建表

**Files:**
- Modify: `app/database.py`（`SCHEMA_SQL`）
- Test: `tests/test_qa_sessions.py`

**Interfaces:**
- Consumes: 夹具 `qa_db`（T3 已写入 `tests/conftest.py`）
- Produces: 表 `qa_sessions(id, title, created_at, updated_at)`、`qa_messages(id, session_id, role, content, sources_json, confusable_json, filters_json, mode, created_at)`、索引 `idx_qa_messages_session`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_qa_sessions.py
"""会话与消息表结构。"""
from app.database import get_db


def test_qa_sessions_table_exists(qa_db):
    """正常场景：会话表建好且含预期列。"""
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(qa_sessions)")}
    assert {"id", "title", "created_at", "updated_at"} <= cols


def test_qa_messages_table_exists(qa_db):
    """正常场景：消息表建好且含预期列。"""
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(qa_messages)")}
    assert {"id", "session_id", "role", "content", "sources_json",
            "confusable_json", "filters_json", "mode", "created_at"} <= cols


def test_qa_messages_index_exists(qa_db):
    """边界场景：按会话取消息的索引存在（历史会话列表/详情依赖）。"""
    with get_db() as conn:
        idx = {r[1] for r in conn.execute("PRAGMA index_list(qa_messages)")}
    assert "idx_qa_messages_session" in idx


def test_qa_foreign_keys_state_is_documented(qa_db):
    """异常场景：记录 SQLite 外键实际状态。

    这是事实断言而非期望断言——SQLite 默认 PRAGMA foreign_keys=OFF，
    因此级联删除**不可依赖**，delete_session 必须应用层显式删（T5 覆盖）。
    本用例存在的意义：若将来有人开启外键，这里会失败并提醒复核 T5。
    """
    with get_db() as conn:
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_on == 0, "外键状态变了：请复核 sessions.delete_session 的显式删除是否仍必要"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_sessions.py -v`
Expected: FAIL — `no such table: qa_sessions`

- [ ] **Step 3: 实现**

在 `app/database.py` 的 `SCHEMA_SQL` 末尾（`TRIGGERS_SQL` 之前）追加：

```sql
-- AI 问答会话与消息（用户可见的会话内容）
-- 与 qa_request_logs 职责分离：后者是请求级埋点（调参用），本表是会话内容
CREATE TABLE IF NOT EXISTS qa_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS qa_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES qa_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    sources_json    TEXT DEFAULT '[]',
    confusable_json TEXT DEFAULT '[]',
    filters_json    TEXT DEFAULT '{}',
    mode            TEXT DEFAULT 'rag',
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_qa_messages_session ON qa_messages(session_id, id);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_sessions.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/database.py tests/test_qa_sessions.py
git commit -m "feat: 新增 qa_sessions / qa_messages 表"
```

---

## Task 5: 会话持久化层

**Files:**
- Create: `app/qa/sessions.py`
- Test: `tests/test_qa_sessions.py`（追加）

**Interfaces:**
- Consumes: 表结构（T4）；`app.database.get_db`
- Produces（签名逐字固定，后续 Task 依赖）：
  - `TITLE_MAX_CHARS: int = 20`
  - `derive_title(question: str) -> str`
  - `create_session(title: str) -> int`
  - `list_sessions() -> list[dict]` — 键：`id, title, updated_at, msg_count`，按 `updated_at DESC, id DESC`
  - `get_session(session_id: int) -> dict | None` — 键：`id, title, created_at, updated_at`
  - `get_messages(session_id: int) -> list[dict]` — 键：`id, role, content, sources, confusable, mode, created_at`（`sources`/`confusable` 已反序列化为 list）
  - `append_message(session_id: int, role: str, content: str, sources=None, confusable=None, mode="rag") -> int`
  - `rename_session(session_id: int, title: str) -> bool`
  - `delete_session(session_id: int) -> bool`
  - `touch_session(session_id: int) -> None`
  - `search_messages(keyword: str, limit: int = 100) -> list[dict]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_sessions.py`（`from app.qa import sessions as S` 请并入文件**顶部**的 import 区，不要留在追加块里）：

```python
from app.qa import sessions as S


def test_derive_title_truncates_long_question(qa_db):
    """正常场景：超长问题截断到 TITLE_MAX_CHARS。"""
    q = "混凝土强度等级应如何评定" * 5
    assert len(S.derive_title(q)) == S.TITLE_MAX_CHARS


def test_derive_title_keeps_short_question(qa_db):
    """边界场景：短问题取全文。"""
    assert S.derive_title("那检验批怎么划分") == "那检验批怎么划分"


def test_derive_title_strips_whitespace(qa_db):
    """边界场景：首尾空白被清理，全空白退化为兜底名。"""
    assert S.derive_title("  混凝土强度  ") == "混凝土强度"
    assert S.derive_title("   ") == "新会话"


def test_create_and_get_session(qa_db):
    """正常场景：建会话后可按 id 取回。"""
    sid = S.create_session("混凝土强度")
    got = S.get_session(sid)
    assert got is not None and got["title"] == "混凝土强度"


def test_get_session_missing_returns_none(qa_db):
    """异常场景：不存在的 id 返回 None，不抛异常。"""
    assert S.get_session(999999) is None


def test_append_message_and_read_back(qa_db):
    """正常场景：消息落库并可读回，sources 反序列化为 list。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "问题")
    S.append_message(sid, "assistant", "答案",
                     sources=[{"code": "GB 50204", "clause_no": "8.2.1"}])
    msgs = S.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["sources"][0]["code"] == "GB 50204"


def test_append_message_records_filters(qa_db):
    """正常场景：当轮生效筛选随助手消息落库，回看时可还原（D5）。

    筛选不入库则该信息不可逆丢失——同一个问题按「混凝土」专业筛与不筛，
    答案来源完全不同，事后无法推断。
    """
    sid = S.create_session("s")
    S.append_message(sid, "assistant", "答案",
                     filters={"dim4_specialty": ["混凝土"], "status_filter": "现行"})
    msgs = S.get_messages(sid)
    assert msgs[0]["filters"]["dim4_specialty"] == ["混凝土"]


def test_get_messages_filters_default_empty_dict(qa_db):
    """边界场景：未记录筛选时返回空 dict（而非 None 或缺失键），前端免判空。"""
    sid = S.create_session("s")
    S.append_message(sid, "assistant", "答案")
    assert S.get_messages(sid)[0]["filters"] == {}


def test_get_messages_tolerates_bad_filters_json(qa_db):
    """异常场景：filters_json 脏数据退化为 {}，不抛异常中断整条会话。"""
    sid = S.create_session("s")
    S.append_message(sid, "assistant", "答案")
    with get_db() as conn:
        conn.execute("UPDATE qa_messages SET filters_json = ? WHERE session_id = ?",
                     ("not-json", sid))
    assert S.get_messages(sid)[0]["filters"] == {}


def test_concurrent_appends_same_session_do_not_lose_or_mix(qa_db):
    """异常场景（并发）：同一会话并发追加消息不重不漏、不错位。

    开启多轮与流式后，一次问答可持续数秒到数十秒；同一会话可能被两个
    标签页（或手快连点两次）同时写入。本用例验证上层已依赖的不变量：
    **消息按 id 顺序落库、互不覆盖**。

    注：`get_db()` 的 `sqlite3.connect(..., timeout=30)` 负责把并发写
    串行化，本用例同时是那个 timeout 的守卫（取消它会让此测试报
    "database is locked"）。
    """
    from concurrent.futures import ThreadPoolExecutor

    sid = S.create_session("并发")

    def write(i: int) -> None:
        S.append_message(sid, "user", f"q{i}")
        S.append_message(sid, "assistant", f"a{i}")

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(write, range(8)))

    msgs = S.get_messages(sid)
    assert len(msgs) == 16, f"并发写入丢消息或多消息（实际 {len(msgs)} 条）"
    assert {m["content"] for m in msgs} == (
        {f"q{i}" for i in range(8)} | {f"a{i}" for i in range(8)}
    ), "并发写入内容错乱或被覆盖"
    assert [m["id"] for m in msgs] == sorted(m["id"] for m in msgs), \
        "消息未按 id 升序返回"


def test_append_message_bumps_updated_at(qa_db):
    """边界场景：追加消息必须刷新 updated_at，否则列表排序不反映活跃度。"""
    sid = S.create_session("s")
    before = S.get_session(sid)["updated_at"]
    S.append_message(sid, "user", "q")
    after = S.get_session(sid)["updated_at"]
    assert after >= before  # 同秒内可能相等，但不得回退


def test_list_sessions_orders_by_recent_activity(qa_db):
    """正常场景：列表按最近活跃倒序。"""
    a = S.create_session("A")
    b = S.create_session("B")
    S.append_message(a, "user", "让 A 变活跃")
    assert S.list_sessions()[0]["id"] == a


def test_list_sessions_reports_message_count(qa_db):
    """正常场景：列表带消息数（前端可判断空会话）。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    S.append_message(sid, "assistant", "a")
    assert S.list_sessions()[0]["msg_count"] == 2


def test_list_sessions_includes_empty_sessions(qa_db):
    """边界场景：零消息的会话仍出现在列表里，计数为 0。

    守卫 LEFT JOIN 的语义——写成 INNER JOIN 会静默丢掉所有空会话，
    而「草稿态会话」正是靠列表可见性管理的。
    """
    S.create_session("空会话")
    rows = S.list_sessions()
    assert len(rows) == 1
    assert rows[0]["msg_count"] == 0


def test_rename_session(qa_db):
    """正常场景：重命名生效。"""
    sid = S.create_session("旧名")
    assert S.rename_session(sid, "新名") is True
    assert S.get_session(sid)["title"] == "新名"


def test_rename_missing_session_returns_false(qa_db):
    """异常场景：重命名不存在的会话返回 False。"""
    assert S.rename_session(999999, "x") is False


def test_delete_session_removes_messages(qa_db):
    """正常场景：删除会话时消息一并清除（应用层显式删，不依赖外键）。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    assert S.delete_session(sid) is True
    assert S.get_session(sid) is None
    assert S.get_messages(sid) == []


def test_delete_missing_session_returns_false(qa_db):
    """异常场景：删除不存在的会话返回 False。"""
    assert S.delete_session(999999) is False


def test_search_messages_across_sessions(qa_db):
    """正常场景：跨会话搜消息，返回所属会话名与消息 id。"""
    a = S.create_session("混凝土")
    b = S.create_session("钢筋")
    S.append_message(a, "user", "混凝土强度等级如何评定")
    S.append_message(b, "user", "钢筋保护层厚度")
    hits = S.search_messages("混凝土")
    assert len(hits) == 1
    assert hits[0]["session_id"] == a
    assert hits[0]["session_title"] == "混凝土"


def test_search_messages_escapes_like_wildcards(qa_db):
    """异常场景：% 和 _ 必须转义，否则退化为全表命中。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "普通内容")
    S.append_message(sid, "user", "含 100% 的内容")
    assert len(S.search_messages("普通内容")) == 1
    # 裸 % 若未转义会命中全部；转义后应命中 0 条
    assert S.search_messages("不存在的%串") == []


def test_search_messages_empty_keyword_returns_empty(qa_db):
    """边界场景：空关键词返回空列表，不退化为全量。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "x")
    assert S.search_messages("") == []
    assert S.search_messages("   ") == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.qa.sessions'`

- [ ] **Step 3: 实现 `app/qa/sessions.py`**

```python
"""AI 问答会话持久化层。

职责边界：
- 本模块只管**会话内容的读写**（表 qa_sessions / qa_messages）
- 请求级埋点（表 qa_request_logs）由 qa_routes._emit_trace 负责，两者互不干涉

约定：
- 所有 SQL 一律参数化（开发铁律 1.1）
- 并发安全依赖 `get_db()` 的 `sqlite3.connect(..., timeout=30)` 把写入串行化；
  有测试守护该不变量（`test_concurrent_appends_same_session_do_not_lose_or_mix`）
- 删除会话时**应用层显式删消息**，不依赖 ON DELETE CASCADE
  （SQLite 默认 PRAGMA foreign_keys=OFF，见 tests/test_qa_sessions.py 的
   test_qa_tables_cascade_delete 对实际状态的断言）
- 单会话消息量小（几十条），遍历取用可接受；禁止在循环内发起查询
"""
import json
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

# 会话标题取自首轮问题，截断长度（业务常量集中管理，禁止散落魔法数字）
TITLE_MAX_CHARS = 20

# 标题兜底名（问题为全空白时）
_FALLBACK_TITLE = "新会话"

# 跨会话搜索返回上限
_SEARCH_LIMIT = 100


def derive_title(question: str) -> str:
    """会话默认标题 = 首轮问题截断。

    设计取舍：不做 AI 摘要（多余一次 API 调用、多一个失败点），
    用户自己的话反而是最有效的记忆锚点。
    """
    text = (question or "").strip()
    if not text:
        return _FALLBACK_TITLE
    return text[:TITLE_MAX_CHARS]


def create_session(title: str) -> int:
    """新建会话，返回其 id。"""
    with get_db() as conn:
        cur = conn.execute("INSERT INTO qa_sessions (title) VALUES (?)", (title,))
        return int(cur.lastrowid)


def get_session(session_id: int) -> dict | None:
    """按 id 取会话元信息；不存在返回 None。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM qa_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "title": row[1],
            "created_at": row[2], "updated_at": row[3]}


def list_sessions() -> list[dict]:
    """会话列表，按最近活跃倒序；带消息数。

    用 LEFT JOIN + GROUP BY 一次算出全部计数，避免逐行的关联子查询（N+1）——
    开发铁律 1.3「循环内部禁止执行数据库查询」在 SQL 层的等价约束。
    LEFT JOIN 保证零消息的会话仍出现在列表里（COUNT(m.id) 为 0）。
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.id, s.title, s.updated_at, COUNT(m.id)
               FROM qa_sessions s
               LEFT JOIN qa_messages m ON m.session_id = s.id
               GROUP BY s.id, s.title, s.updated_at
               ORDER BY s.updated_at DESC, s.id DESC"""
        ).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "msg_count": r[3]}
            for r in rows]


def get_messages(session_id: int) -> list[dict]:
    """取会话全部消息（时间升序），sources/confusable 反序列化为 list。

    前端 messages 数组的字段结构与本函数返回一一对应，可直接映射。
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, role, content, sources_json, confusable_json,
                      filters_json, mode, created_at
               FROM qa_messages WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0], "role": r[1], "content": r[2],
            "sources": _loads_list(r[3]),
            "confusable": _loads_list(r[4]),
            "filters": _loads_dict(r[5]),
            "mode": r[6], "created_at": r[7],
        })
    return out


def _loads_list(raw) -> list:
    """容错反序列化 JSON 数组；脏数据退化为空列表，不抛异常中断整条会话。"""
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning("会话消息 JSON 解析失败，按空处理: %s", e)
        return []
    return val if isinstance(val, list) else []


def _loads_dict(raw) -> dict:
    """容错反序列化 JSON 对象（当轮筛选）；脏数据退化为空 dict。"""
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning("会话消息筛选 JSON 解析失败，按空处理: %s", e)
        return {}
    return val if isinstance(val, dict) else {}


def append_message(session_id: int, role: str, content: str,
                   sources: list | None = None, confusable: list | None = None,
                   filters: dict | None = None,
                   mode: str = "rag") -> int:
    """追加一条消息，并刷新所属会话的 updated_at。

    filters 记录**当轮实际生效的筛选**（D5）：回看历史时据此还原
    「这条答案是在什么筛选下产生的」——筛选不入库则该信息不可逆丢失。

    失败消息不入库由调用方保证（见 qa_routes），本层不做判断。
    """
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    confusable_json = json.dumps(confusable or [], ensure_ascii=False)
    filters_json = json.dumps(filters or {}, ensure_ascii=False)
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO qa_messages
               (session_id, role, content, sources_json, confusable_json,
                filters_json, mode)
               VALUES (?,?,?,?,?,?,?)""",
            (session_id, role, content, sources_json, confusable_json,
             filters_json, mode),
        )
        conn.execute(
            "UPDATE qa_sessions SET updated_at = datetime('now','localtime') WHERE id = ?",
            (session_id,),
        )
        return int(cur.lastrowid)


def touch_session(session_id: int) -> None:
    """仅刷新 updated_at（用于无新消息但要提升排序的场景）。"""
    with get_db() as conn:
        conn.execute(
            "UPDATE qa_sessions SET updated_at = datetime('now','localtime') WHERE id = ?",
            (session_id,),
        )


def rename_session(session_id: int, title: str) -> bool:
    """重命名；会话不存在返回 False。"""
    clean = (title or "").strip()
    if not clean:
        return False
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE qa_sessions SET title = ? WHERE id = ?", (clean, session_id)
        )
        return cur.rowcount > 0


def delete_session(session_id: int) -> bool:
    """删除会话及其全部消息（应用层显式删，不依赖外键级联）。"""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM qa_sessions WHERE id = ?", (session_id,))
        if cur.rowcount == 0:
            return False
        conn.execute("DELETE FROM qa_messages WHERE session_id = ?", (session_id,))
        return True


def search_messages(keyword: str, limit: int = _SEARCH_LIMIT) -> list[dict]:
    """跨会话搜索消息内容（LIKE，参数化 + 通配符转义）。

    为什么不上 FTS：qa_messages 是小表（个人/团队量级），全表扫足够；
    且中文子串匹配对「找出我说过的那句话」比分词更精确。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    pattern = f"%{_escape_like(kw)}%"
    with get_db() as conn:
        rows = conn.execute(
            """SELECT m.id, m.session_id, s.title, m.role, m.content
               FROM qa_messages m JOIN qa_sessions s ON s.id = m.session_id
               WHERE m.content LIKE ? ESCAPE '\\'
               ORDER BY m.session_id DESC, m.id DESC
               LIMIT ?""",
            (pattern, max(1, min(limit, _SEARCH_LIMIT))),
        ).fetchall()
    return [{"id": r[0], "session_id": r[1], "session_title": r[2],
             "role": r[3], "content": r[4]} for r in rows]


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，防止用户输入的 % / _ 退化为全表命中。"""
    return (text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_"))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_sessions.py -v`
Expected: PASS（18 passed）

- [ ] **Step 5: 提交**

```bash
git add app/qa/sessions.py tests/test_qa_sessions.py
git commit -m "feat: QA 会话持久化层（CRUD + 跨会话搜索）"
```

---

## Task 6: 多轮历史段组装

**Files:**
- Modify: `app/qa/context.py`（新增 `build_history` 与 `HISTORY_HEADER`）
- Modify: `app/config.py`（`QA_CONFIG_DEFAULTS`）
- Modify: `app/params/registry.py`
- Test: `tests/test_qa_history.py`

**Interfaces:**
- Consumes: `app.qa.sessions.get_messages` 的返回结构（T5）
- Produces:
  - `app.qa.context.build_history(messages: list[dict], max_turns: int, token_budget: int) -> str`
  - `app.qa.context.build_history(messages: list[dict], max_turns: int, token_budget: int) -> str`
  - `app.qa.context.HISTORY_HEADER: str`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_qa_history.py
"""多轮历史段组装（精简多轮：只带问答文本，绝不复用历史条文）。"""
from app.qa.context import build_history, HISTORY_HEADER


def _msgs(*pairs):
    out = []
    for q, a in pairs:
        out.append({"role": "user", "content": q})
        out.append({"role": "assistant", "content": a})
    return out


def test_build_history_empty_when_no_messages():
    """边界场景：无消息 → 空串（纯单轮语义）。"""
    assert build_history([], 6, 6000) == ""


def test_build_history_zero_turns_disables_history():
    """边界场景：max_turns=0 → 关闭历史，等同单轮。"""
    assert build_history(_msgs(("q1", "a1")), 0, 6000) == ""


def test_build_history_keeps_only_recent_turns():
    """正常场景：只保留最近 N 轮，更早的被丢弃。"""
    msgs = _msgs(("老问题", "老答案"), ("新问题", "新答案"))
    out = build_history(msgs, 1, 6000)
    assert "新问题" in out and "新答案" in out
    assert "老问题" not in out


def test_build_history_includes_q_and_a():
    """正常场景：历史段含问与答。"""
    out = build_history(_msgs(("混凝土强度", "按 GB 50204 评定")), 6, 6000)
    assert "混凝土强度" in out and "按 GB 50204 评定" in out
    assert out.startswith(HISTORY_HEADER)


def test_build_history_never_contains_sources():
    """异常场景（核心不变量）：历史段绝不能带历史条文上下文。

    条文每轮由 hybrid_search 重新召回；把历史条文带进上下文会导致
    token 平方级增长，且旧条文可能与新问题矛盾。
    """
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "a",
             "sources": [{"code": "GB 50204", "clause_no": "8.2.1"}]}]
    out = build_history(msgs, 6, 6000)
    assert "8.2.1" not in out


def test_build_history_drops_oldest_when_over_budget():
    """边界场景：历史段超预算时逐轮丢弃最旧，保留最近的。"""
    long_a = "很长" * 400          # 约 800 字符 ≈ 400 token
    msgs = _msgs(("旧问题", long_a), ("旧问题2", long_a), ("新问题", "短答案"))
    out = build_history(msgs, 6, token_budget=300)   # 预算不足以装下多轮
    assert "新问题" in out
    assert out.count("很长") < 2 * 400 / 2   # 未把全部长答案都塞进去


def test_build_history_does_not_truncate_inside_an_answer():
    """边界场景：不得截断单条答案内部（与 build_context 的既有原则一致）。"""
    answer = "起" + "中" * 100 + "止"
    out = build_history([{"role": "user", "content": "q"},
                         {"role": "assistant", "content": answer}], 6, 6000)
    assert "止" in out, "单条答案必须完整，不得从中间截断"


def test_build_history_skips_malformed_entries():
    """异常场景：缺 role/content 的脏数据被跳过，不抛异常。"""
    out = build_history([{"role": "user"}, {"content": "x"}, None,
                         {"role": "user", "content": "有效"}], 6, 6000)
    assert "有效" in out
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_history.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_history' from 'app.qa.context'`

- [ ] **Step 3: 实现**

在 `app/qa/context.py` 末尾新增（该文件职责即「上下文组装」，`build_history` 天然属于它——不另立文件）：

```python
# ── 多轮历史段组装（精简多轮策略） ──
#
# 核心不变量（设计文档 D1/D3）：
# 1. 历史段只含问答文本，绝不复用历史条文上下文——条文每轮由
#    hybrid_search 重新召回。带历史条文会导致 token 平方级增长，
#    且旧条文可能与新问题矛盾。
# 2. 上下文严格限于当前会话——调用方只传当前会话的消息。
# 3. 超预算时逐轮丢弃最旧，不截断单条答案内部
#    （与同文件 build_context 的既有原则一致）。
#
# 复用本文件既有的 estimate_tokens()，不另造 token 估算。

HISTORY_HEADER = "【历史对话】"


def _turns(messages: list[dict]) -> list[tuple[str, str]]:
    """把扁平消息列表按 (user, assistant) 配对成轮次，顺序保持。

    - 连续的 user（如失败重试）取最后一条，避免出现无答案的轮次
    - 末尾孤立的 user（正在提问但无答案）不构成轮次
    - 脏数据（None / 缺字段）跳过
    """
    turns: list[tuple[str, str]] = []
    pending_q: str | None = None
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            pending_q = content
        elif role == "assistant" and pending_q is not None:
            turns.append((pending_q, content))
            pending_q = None
    return turns


def build_history(messages: list[dict], max_turns: int,
                  token_budget: int) -> str:
    """取最近 max_turns 轮「问+答」拼为历史段；超预算逐轮丢弃最旧。

    max_turns <= 0 → 返回空串（关闭多轮，行为等同单轮）。
    """
    if max_turns <= 0 or not messages or token_budget <= 0:
        return ""

    turns = _turns(messages)
    if not turns:
        return ""

    recent = turns[-max_turns:]
    # 从最近往前累积，超预算即停——保证丢的是最旧的轮次
    kept: list[tuple[str, str]] = []
    used = estimate_tokens(HISTORY_HEADER)
    for q, a in reversed(recent):
        cost = estimate_tokens(q) + estimate_tokens(a)
        if kept and used + cost > token_budget:
            break
        used += cost
        kept.append((q, a))
    kept.reverse()

    if not kept:
        return ""

    blocks = [f"用户：{q}\n助手：{a}" for q, a in kept]
    return HISTORY_HEADER + "\n" + "\n\n".join(blocks)
```

在 `app/config.py` 的 `QA_CONFIG_DEFAULTS` 中，`token.summary_chars` 之后追加：

```python
    "history.max_turns": 6,     # 多轮历史窗口（轮）；0 = 关闭多轮
    # 历史段的**独立** token 预算。不复用 token.max_context_tokens——
    # 那是「条文上下文」的预算，两段各自按它截断会让总上下文达配置值的两倍
    "token.max_history_tokens": 800,
```

在 `app/params/registry.py` 的 qa 段末尾（`qa.token.summary_chars` 之后）追加：

```python
    meta.append(_num(
        "qa.history.max_turns", "qa", "多轮历史窗口",
        float(QA_CONFIG_DEFAULTS["history.max_turns"]), 0, 50, "0~50",
        "注入模型的历史对话轮数上限；0 = 不带历史（纯单轮）。"
        "历史只含问答文本，不含条文上下文。", dtype="int"))
    meta.append(_num(
        "qa.token.max_history_tokens", "qa", "历史段 token 预算",
        float(QA_CONFIG_DEFAULTS["token.max_history_tokens"]), 0, 4000, "0~4000",
        "历史对话段的独立 token 预算，与「上下文 token 预算」（条文段）分开计。"
        "两段合计上界 = 本值 + 上下文 token 预算 + system prompt。"
        "0 = 不注入历史。", dtype="int"))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_history.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 验证参数注册表渲染**

Run: `D:/Python/python.exe -c "from app.params.registry import PARAM_META; m=[x for x in PARAM_META if x['key']=='qa.history.max_turns']; print(m[0]['group'], m[0]['default'], m[0]['min'], m[0]['max'])"`
Expected: 输出 `qa 6.0 0 50`（分组 `qa` 会自动渲染进「维护 → 参数设置 → 问答」）

- [ ] **Step 6: 提交**

```bash
git add app/qa/context.py app/config.py app/params/registry.py tests/test_qa_history.py
git commit -m "feat: 多轮历史段组装 + qa.history.max_turns 参数"
```

---

## Task 7: 多轮引用护栏（prompt）

**Files:**
- Modify: `app/ai/prompts.py`
- Test: `tests/test_qa_history.py`（追加）

**Interfaces:**
- Consumes: `app.ai.prompts.build_system_prompt`
- Produces: `build_system_prompt(mode, multi_turn: bool = False) -> str` — 新增第二参数，默认 `False` 保持既有调用方行为不变

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_history.py`：

```python
from app.ai.prompts import build_system_prompt


def test_single_turn_prompt_unchanged():
    """回归：默认（单轮）prompt 不含多轮护栏，既有行为不受影响。"""
    assert "历史对话" not in build_system_prompt("rag")


def test_multi_turn_prompt_forbids_citing_history_clauses():
    """正常场景：多轮护栏必须禁止引用历史条文（护栏是 D1 的配套）。"""
    p = build_system_prompt("rag", multi_turn=True)
    assert "本轮" in p
    assert "历史" in p


def test_multi_turn_prompt_applies_to_verbatim_mode():
    """边界场景：原文摘抄模式同样需要护栏。"""
    assert "本轮" in build_system_prompt("verbatim", multi_turn=True)


def test_unknown_mode_multi_turn_falls_back_to_rag():
    """异常场景：未知模式回退 RAG，且仍带护栏。"""
    p = build_system_prompt("不存在", multi_turn=True)
    assert "本轮" in p
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_history.py -k multi_turn -v`
Expected: FAIL — `TypeError: build_system_prompt() got an unexpected keyword argument 'multi_turn'`

- [ ] **Step 3: 实现**

在 `app/ai/prompts.py` 中，`PROMPT_MODES` 之后新增护栏常量：

```python
# 多轮护栏：历史段只含问答文本、不含条文上下文，
# 因此必须禁止 AI 凭「历史里见过」去引用本轮未提供的条文。
MULTI_TURN_GUARD = """

【多轮对话附加约束】
本轮为连续对话，上方可能附有【历史对话】段。请注意：
1. 你**只能引用本轮【参考上下文】中实际提供的条文**。
2. 历史对话中出现过的规范编号或条文号，若本轮【参考上下文】中未提供，
   不得作为引用来源，也不得凭记忆复述其内容。
3. 若本轮上下文不足以回答，请如实说明「当前未检索到相关条文」，禁止编造。"""
```

把 `build_system_prompt` 改为：

```python
def build_system_prompt(mode: str, multi_turn: bool = False) -> str:
    """按模式返回 system prompt；未知模式回退 RAG。

    multi_turn=True 时追加多轮引用护栏（见 MULTI_TURN_GUARD）。
    """
    base = PROMPT_MODES.get(mode, SYSTEM_PROMPT_RAG)
    return base + MULTI_TURN_GUARD if multi_turn else base
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_history.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: 提交**

```bash
git add app/ai/prompts.py tests/test_qa_history.py
git commit -m "feat: 多轮对话引用护栏（禁止引用本轮未提供的条文）"
```

---

## Task 8: `/qa/ask` 接入会话与多轮

**Files:**
- Modify: `app/models.py`（`QaRequest`）
- Modify: `app/routes/qa_routes.py`
- Test: `tests/test_qa_session_routes.py`

**Interfaces:**
- Consumes: `app.qa.sessions`（T5）、`app.qa.context.build_history`（T6）、`build_system_prompt(mode, multi_turn)`（T7）
- Produces: `QaRequest.session_id: int | None`、`QaRequest.relaxed: bool = False`、`QAResponse.session_id: int`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_qa_session_routes.py
"""QA 路由的会话行为（惰性创建、续聊、失败不入库）。

测试基础设施见本计划「测试基础设施」节，两条硬性要求：
  1. 用 conftest 的 auth_client（已建库/建用户/登录），不要裸 TestClient；
  2. patch **函数内局部 import 的源模块**，不要 patch 路由模块。
"""
from unittest.mock import AsyncMock

import pytest

from app.ai.cli_client import CLIResponse
from app.qa import sessions as S

# 固定候选（stub 用），字段与真实 hybrid_search 返回对齐
_CAND = {"id": 1, "spec_code": "GB 50204", "clause_no": "8.2.1",
         "content": "混凝土强度等级应…", "title": "", "spec_status": "现行"}


def _fake_backend(answer="答案"):
    b = AsyncMock()
    b.is_available = lambda: True
    b.ask = AsyncMock(return_value=CLIResponse(success=True, content=answer))
    return b


@pytest.fixture()
def qa_env(monkeypatch):
    """打桩检索与后端：QA 链路完全确定，且不加载任何模型。

    注：get_backend 被调用时只传一个位置参数（body.backend），
    故此处签名为 (name=None)。
    """
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend",
                        lambda name=None: _fake_backend())


def test_first_ask_creates_session(auth_client, qa_env):
    """正常场景：首次问答（无 session_id）惰性建会话，标题取问题前 20 字。"""
    r = auth_client.post("/qa/ask", json={"question": "混凝土强度等级如何评定"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert S.get_session(sid)["title"] == "混凝土强度等级如何评定"


def test_ask_persists_both_messages(auth_client, qa_env):
    """正常场景：一轮问答落两条消息（user + assistant）。"""
    sid = auth_client.post("/qa/ask", json={"question": "q"}).json()["session_id"]
    msgs = S.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "答案"


def test_second_ask_same_session_appends(auth_client, qa_env):
    """正常场景：带 session_id 续聊，消息追加而非新建会话。"""
    sid = auth_client.post("/qa/ask", json={"question": "q1"}).json()["session_id"]
    r2 = auth_client.post("/qa/ask", json={"question": "q2", "session_id": sid})
    assert r2.json()["session_id"] == sid
    assert len(S.get_messages(sid)) == 4


def test_unknown_session_id_creates_new_session(auth_client, qa_env):
    """异常场景：session_id 指向不存在的会话 → 视为新会话，不报错、不跨会话取历史。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "session_id": 999999})
    assert r.status_code == 200
    assert r.json()["session_id"] != 999999


def test_history_is_injected_on_second_turn(auth_client, monkeypatch):
    """正常场景：第二轮的 context 中含第一轮问答，且不含本轮问题。

    直接断言喂给后端的上下文，比断言某个函数被调用更强——它验证的是
    可观测的结果（历史真的进了 prompt），而非实现细节。
    """
    captured = {}

    async def _ask(prompt, context="", system_prompt="", work_dir=None):
        captured["context"] = context
        return CLIResponse(success=True, content="答案")

    b = AsyncMock()
    b.is_available = lambda: True
    b.ask = _ask
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    first_q = "第一轮问题"
    sid = auth_client.post("/qa/ask", json={"question": first_q}).json()["session_id"]
    auth_client.post("/qa/ask", json={"question": "第二轮问题", "session_id": sid})

    assert first_q in captured["context"], "第二轮必须注入第一轮问答作为历史"
    assert "第二轮问题" not in captured["context"], "本轮问题不得混进历史段"


def test_failed_llm_call_not_persisted(auth_client, qa_env, monkeypatch):
    """异常场景：LLM 调用失败时整轮不入库，避免半截会话污染历史。"""
    failing = AsyncMock()
    failing.is_available = lambda: True
    failing.ask = AsyncMock(return_value=CLIResponse(
        success=False, content="", error="API 调用超时"))
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: failing)

    r = auth_client.post("/qa/ask", json={"question": "q"})
    assert r.status_code == 200
    # 失败时助手消息不入库；用户消息也不入库（整轮作废）
    assert S.list_sessions() == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -v`
Expected: FAIL — `KeyError: 'session_id'`

- [ ] **Step 3: 实现**

在 `app/models.py` 的 `QaRequest` 中，于 `question` 之后新增：

```python
    # 会话 id：None → 惰性新建会话；指向不存在的会话 → 同样视为新建（不跨会话取历史）
    session_id: Optional[int] = None
    # 「放宽分类筛选」重发标记：True 时**只**忽略分类维度重新检索，
    # 状态过滤与前言设置仍生效（见设计文档 §4.7）
    relaxed: bool = False
```

在 `app/models.py` 的 `QAResponse` 中，于 `rerank_used` 之后新增：

```python
    # 本次问答所属会话 id（惰性创建时为新 id）
    session_id: int = 0
```

> **实现形态（重要，评审 D12③）**：本步**不要**把逻辑直接写进 `/qa/ask` 的函数体——
> 请一开始就落在模块级 `_prepare_qa_context(question, body) -> QaContext` 中
> （完整形状见 Task 15 的 Step 3c），`/qa/ask` 只负责调用它。
>
> 理由：Task 13 要在同一段代码里加 `filtered_out` / `effective_filters`，
> Task 15 要加流式分支。若 T8/T13 先写内联版本、到 T15 才抽取，就是**事后重构**——
> 同一段代码改三遍，回归风险全压在最后一步，而那一步还要同时引入 SSE。
> **先建形状，后续任务只做加法。**
>
> 本步实现到「检索 → 过滤 → 精排 → 选条 → 分层 → 组装上下文 + 会话/历史解析」为止；
> `QaContext.filtered_out` / `rerank_used` 可先留默认值（T13 补齐），流式分支完全不管（T15 补）。
> 下面列出的改动点，一律落在 `_prepare_qa_context` 内或 `/qa/ask` 的调用处，不要复制成两份。

在 `app/routes/qa_routes.py` 的 `/qa/ask` 中，import 段追加：

```python
    from app.qa.context import build_history
    from app.qa import sessions as qa_sessions
```

在 `question` 校验之后、`trace` 构造之前插入会话解析：

```python
    # 会话解析：不存在的 id 一律视为新会话——严格不跨会话取历史（D3）
    session_id = body.session_id
    if session_id is not None and qa_sessions.get_session(session_id) is None:
        logger.info("QA session_id=%s 不存在，按新会话处理", session_id)
        session_id = None
    # 历史段必须在落库本轮消息之前读取，否则本轮问答会被算进历史
    history_messages = qa_sessions.get_messages(session_id) if session_id else []
    max_turns = get_qa_int("history.max_turns")
    history_str = build_history(
        history_messages, max_turns, get_qa_int("token.max_history_tokens"),
    )
```

把 `build_context` 之后、`backend = get_backend(...)` 之前的上下文拼接改为（在既有 `context_str` 后追历史段）：

```python
    if history_str:
        context_str = f"{history_str}\n\n{context_str}"
```

把 `build_system_prompt(body.mode)` 改为：

```python
        system_prompt=build_system_prompt(body.mode, multi_turn=bool(history_str)),
```

把答案成功分支改为（**仅在成功且有内容时落库**），替换原 `if resp.success and resp.content.strip():` 三支：

```python
    persist_ok = False
    if resp.success and resp.content.strip():
        answer = resp.content
        persist_ok = True
    elif resp.success:
        logger.warning("QA CLI returned empty content (command=%s)", cli_used)
        answer = "抱歉，AI 服务返回了空内容，请确认 CLI 已登录并可用。"
    else:
        logger.warning("QA CLI error (command=%s): %s", cli_used, resp.error)
        answer = "抱歉，AI 服务返回错误。" + ("（超时）" if "超时" in (resp.error or "") else "")

    # 仅成功轮次落库（失败不入库，避免半截会话污染历史）
    if persist_ok:
        if session_id is None:
            session_id = qa_sessions.create_session(
                qa_sessions.derive_title(question))
        qa_sessions.append_message(session_id, "user", question)
        qa_sessions.append_message(
            session_id, "assistant", answer, sources=sources,
            confusable=confusable_hits, mode=body.mode,
        )
```

> 注意：`sources` 与 `confusable_hits` 的计算必须**上移到落库之前**（原代码它们在答案分支之后）。把 `sources = _extract_sources(...)` 与 confusable 检测段整体移到 `persist_ok` 判定之前即可。

最后把返回改为：

```python
    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits,
                      rerank_used=trace.rerank_used, session_id=session_id or 0)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑既有 QA 用例确认无回归**

Run: `D:/Python/python.exe -m pytest tests/test_qa_routes.py tests/test_qa_status_filter.py tests/test_qa_context.py -v`
Expected: 全部 PASS

Run: `D:/Python/python.exe -m pyright app/routes/qa_routes.py app/models.py`
Expected: 无新增 error

- [ ] **Step 6: 提交**

```bash
git add app/models.py app/routes/qa_routes.py tests/test_qa_session_routes.py
git commit -m "feat: /qa/ask 接入会话惰性创建与多轮上下文"
```

---

## Task 9: 会话列表与详情接口

**Files:**
- Modify: `app/routes/qa_routes.py`
- Test: `tests/test_qa_session_routes.py`（追加）

**Interfaces:**
- Consumes: `app.qa.sessions.list_sessions` / `get_session` / `get_messages`（T5）
- Produces: `GET /qa/sessions` → `{"sessions": [...]}`；`GET /qa/sessions/{id}` → `{"session": {...}, "messages": [...]}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_session_routes.py`：

```python
def test_list_sessions_endpoint(auth_client):
    """正常场景：列表接口返回会话数组，按最近活跃倒序。"""
    a = S.create_session("A")
    S.append_message(a, "user", "q")
    r = auth_client.get("/qa/sessions")
    assert r.status_code == 200
    assert r.json()["sessions"][0]["id"] == a


def test_list_sessions_empty_returns_empty_array(auth_client):
    """边界场景：无会话时返回空数组（统一结构，禁止返回 null）。"""
    r = auth_client.get("/qa/sessions")
    assert r.json() == {"sessions": []}


def test_get_session_detail_returns_messages_with_sources(auth_client):
    """正常场景：详情返回会话元信息 + 全部消息，含 sources（前端据此重建条文链接）。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    S.append_message(sid, "assistant", "a", sources=[{"code": "GB 50204",
                                                      "clause_no": "8.2.1"}])
    r = auth_client.get(f"/qa/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["title"] == "s"
    assert body["messages"][1]["sources"][0]["clause_no"] == "8.2.1"


def test_get_missing_session_returns_404(auth_client):
    """异常场景：不存在的会话返回 404，而非空对象。"""
    assert auth_client.get("/qa/sessions/999999").status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -k "list_sessions or detail" -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: 实现**

在 `app/routes/qa_routes.py` 末尾追加：

```python
# ── 会话管理接口 ──

@router.get("/qa/sessions")
async def qa_list_sessions():
    """会话列表（按最近活跃倒序）。"""
    from app.qa import sessions as qa_sessions
    return JSONResponse({"sessions": qa_sessions.list_sessions()})


@router.get("/qa/sessions/{session_id}")
async def qa_get_session(session_id: int):
    """会话详情：元信息 + 全部消息（含 sources，供前端重建条文链接）。"""
    from app.qa import sessions as qa_sessions
    sess = qa_sessions.get_session(session_id)
    if sess is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return JSONResponse({
        "session": sess,
        "messages": qa_sessions.get_messages(session_id),
    })
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: 提交**

```bash
git add app/routes/qa_routes.py tests/test_qa_session_routes.py
git commit -m "feat: 会话列表与详情接口"
```

---

## Task 10: 重命名与删除接口

**Files:**
- Modify: `app/routes/qa_routes.py`
- Test: `tests/test_qa_session_routes.py`（追加）

**Interfaces:**
- Consumes: `app.qa.sessions.rename_session` / `delete_session`（T5）
- Produces: `PATCH /qa/sessions/{id}` body `{"title": str}` → `{"ok": true}`；`DELETE /qa/sessions/{id}` → `{"ok": true}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_session_routes.py`：

```python
def test_rename_session_endpoint(auth_client):
    """正常场景：重命名生效。"""
    sid = S.create_session("旧名")
    r = auth_client.patch(f"/qa/sessions/{sid}", json={"title": "新名"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert S.get_session(sid)["title"] == "新名"


def test_rename_rejects_blank_title(auth_client):
    """异常场景：空白标题被拒（400），不得写库。"""
    sid = S.create_session("原名")
    r = auth_client.patch(f"/qa/sessions/{sid}", json={"title": "   "})
    assert r.status_code == 400
    assert S.get_session(sid)["title"] == "原名"


def test_rename_missing_session_returns_404(auth_client):
    """异常场景：不存在的会话返回 404。"""
    r = auth_client.patch("/qa/sessions/999999", json={"title": "x"})
    assert r.status_code == 404


def test_rename_title_length_enforced(auth_client):
    """边界场景：超长标题被截断到上限，不拒绝（用户体验优先）。"""
    sid = S.create_session("s")
    r = auth_client.patch(f"/qa/sessions/{sid}", json={"title": "长" * 200})
    assert r.status_code == 200
    assert len(S.get_session(sid)["title"]) <= 100


def test_delete_session_endpoint_removes_messages(auth_client):
    """正常场景：删除会话后消息一并清除。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    r = auth_client.delete(f"/qa/sessions/{sid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert S.get_session(sid) is None and S.get_messages(sid) == []


def test_delete_missing_session_returns_404(auth_client):
    """异常场景：删除不存在的会话返回 404。"""
    assert auth_client.delete("/qa/sessions/999999").status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -k "rename or delete" -v`
Expected: FAIL — `405 Method Not Allowed`

- [ ] **Step 3: 实现**

在 `app/models.py` 中新增请求体模型：

```python
class QaSessionRenameRequest(BaseModel):
    """会话重命名请求。"""
    title: str
```

在 `app/routes/qa_routes.py` 末尾追加（并把 `QaSessionRenameRequest` 加入顶部 `from app.models import ...`）：

```python
# 会话标题长度上限（业务常量集中管理）
_SESSION_TITLE_MAX = 100


@router.patch("/qa/sessions/{session_id}")
async def qa_rename_session(session_id: int, body: QaSessionRenameRequest):
    """重命名会话；空白标题拒绝，超长截断。"""
    from app.qa import sessions as qa_sessions
    title = (body.title or "").strip()
    if not title:
        return JSONResponse({"detail": "会话名不能为空"}, status_code=400)
    if qa_sessions.get_session(session_id) is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    qa_sessions.rename_session(session_id, title[:_SESSION_TITLE_MAX])
    return JSONResponse({"ok": True})


@router.delete("/qa/sessions/{session_id}")
async def qa_delete_session(session_id: int):
    """删除会话及其全部消息（前端需二次确认）。"""
    from app.qa import sessions as qa_sessions
    if not qa_sessions.delete_session(session_id):
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -v`
Expected: PASS（16 passed）

- [ ] **Step 5: 提交**

```bash
git add app/models.py app/routes/qa_routes.py tests/test_qa_session_routes.py
git commit -m "feat: 会话重命名与删除接口"
```

---

## Task 11: 导出 Markdown

**Files:**
- Modify: `app/qa/sessions.py`（新增 `build_markdown`）
- Modify: `app/routes/qa_routes.py`
- Test: `tests/test_qa_session_routes.py`（追加）

**Interfaces:**
- Consumes: `app.qa.sessions.get_session` / `get_messages`（T5）
- Produces: `GET /qa/sessions/{id}/export` → `text/markdown` 附件响应
- Produces: `app.qa.sessions.build_markdown(sess: dict, messages: list[dict]) -> str`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_session_routes.py`：

```python
def test_build_markdown_contains_title_and_turns():
    """正常场景：导出内容含会话名、问答正文与参考条文。"""
    sess = {"id": 1, "title": "混凝土强度", "created_at": "2026-09-21 10:00:00",
            "updated_at": "2026-09-21 10:05:00"}
    msgs = [{"role": "user", "content": "如何评定", "sources": [], "created_at": "x"},
            {"role": "assistant", "content": "按 GB 50204 评定",
             "sources": [{"code": "GB 50204", "clause_no": "8.2.1"}],
             "created_at": "x"}]
    md = S.build_markdown(sess, msgs)
    assert "# 混凝土强度" in md
    assert "如何评定" in md
    assert "GB 50204 8.2.1" in md


def test_build_markdown_handles_session_without_messages():
    """边界场景：空会话导出不报错，仍含标题。"""
    md = S.build_markdown({"title": "空会话", "created_at": "", "updated_at": ""}, [])
    assert "# 空会话" in md


def test_export_endpoint_returns_markdown_attachment(auth_client):
    """正常场景：导出接口返回 markdown 附件，带 Content-Disposition。"""
    sid = S.create_session("混凝土强度")
    S.append_message(sid, "user", "如何评定")
    r = auth_client.get(f"/qa/sessions/{sid}/export")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "如何评定" in r.text


def test_export_missing_session_returns_404(auth_client):
    """异常场景：导出不存在的会话返回 404。"""
    assert auth_client.get("/qa/sessions/999999/export").status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -k markdown -v`
Expected: FAIL — `AttributeError: module 'app.qa.sessions' has no attribute 'build_markdown'`

- [ ] **Step 3: 实现**

在 `app/qa/sessions.py` 末尾追加：

```python
def build_markdown(sess: dict, messages: list[dict]) -> str:
    """把会话渲染为 Markdown（导出用）。

    只做 Markdown 不做 HTML：项目已有完整 md 渲染管线
    （marked + DOMPurify + KaTeX），导出的 md 可直接丢回系统渲染。
    """
    lines = [f"# {sess.get('title') or '未命名会话'}", ""]
    if sess.get("created_at"):
        lines += [f"- 创建时间：{sess['created_at']}"]
    if sess.get("updated_at"):
        lines += [f"- 最后活跃：{sess['updated_at']}"]
    lines.append("")

    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "user":
            lines += ["## 问", "", content, ""]
        else:
            lines += ["## 答", "", content, ""]
            sources = m.get("sources") or []
            if sources:
                refs = "、".join(
                    f"《{s.get('code', '')}》{s.get('clause_no', '')}"
                    for s in sources if isinstance(s, dict)
                )
                if refs:
                    lines += [f"> 参考条文：{refs}", ""]
    return "\n".join(lines)
```

在 `app/routes/qa_routes.py` 末尾追加：

```python
@router.get("/qa/sessions/{session_id}/export")
async def qa_export_session(session_id: int):
    """导出会话为 Markdown 附件。"""
    from urllib.parse import quote

    from fastapi.responses import Response
    from app.qa import sessions as qa_sessions

    sess = qa_sessions.get_session(session_id)
    if sess is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    md = qa_sessions.build_markdown(sess, qa_sessions.get_messages(session_id))
    # 文件名做 RFC 5987 编码，避免中文标题导致下载名乱码
    fname = quote(f"{sess['title']}.md")
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -v`
Expected: PASS（20 passed）

- [ ] **Step 5: 提交**

```bash
git add app/qa/sessions.py app/routes/qa_routes.py tests/test_qa_session_routes.py
git commit -m "feat: 会话导出 Markdown"
```

---

## Task 12: 跨会话搜索接口

**Files:**
- Modify: `app/routes/qa_routes.py`
- Test: `tests/test_qa_session_routes.py`（追加）

**Interfaces:**
- Consumes: `app.qa.sessions.search_messages`（T5）
- Produces: `GET /qa/search?q=<kw>` → `{"hits": [...]}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_qa_session_routes.py`：

```python
def test_search_endpoint_returns_hits(auth_client):
    """正常场景：命中返回消息 + 所属会话名 + 消息 id（前端用于定位跳转）。"""
    sid = S.create_session("混凝土")
    S.append_message(sid, "user", "混凝土强度等级如何评定")
    r = auth_client.get("/qa/search", params={"q": "混凝土"})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["session_id"] == sid
    assert hits[0]["session_title"] == "混凝土"
    assert "id" in hits[0]


def test_search_endpoint_blank_query_returns_empty(auth_client):
    """边界场景：空查询返回空数组，不退化为全量。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "x")
    assert auth_client.get("/qa/search", params={"q": "  "}).json() == {"hits": []}


def test_search_endpoint_escapes_wildcards(auth_client):
    """异常场景：% 被转义，不得命中全部消息。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "普通内容")
    assert auth_client.get("/qa/search", params={"q": "%"}).json() == {"hits": []}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -k search_endpoint -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: 实现**

在 `app/routes/qa_routes.py` 末尾追加：

```python
@router.get("/qa/search")
async def qa_search_messages(q: str = ""):
    """跨会话搜索消息内容（LIKE，参数化 + 通配符转义）。"""
    from app.qa import sessions as qa_sessions
    return JSONResponse({"hits": qa_sessions.search_messages(q)})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_session_routes.py -v`
Expected: PASS（23 passed）

- [ ] **Step 5: 提交**

```bash
git add app/routes/qa_routes.py tests/test_qa_session_routes.py
git commit -m "feat: 跨会话搜索消息接口"
```

---

## Task 13: 取消静默放宽，改显式提示

**Files:**
- Modify: `app/routes/qa_routes.py:190-200`（放宽段）、返回体
- Modify: `app/models.py`（`QAResponse`）
- Test: `tests/test_qa_relax.py`

**Interfaces:**
- Consumes: `app.qa.config.get_qa_int`（既有）
- Produces: `QAResponse.filtered_out: int`、`QAResponse.effective_filters: dict`；`QaRequest.relaxed`（T8 已加字段）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_qa_relax.py
"""分类筛选候选不足：取消静默放宽，改显式提示 + 一键放宽。"""
from unittest.mock import AsyncMock

import pytest

from app.ai.cli_client import CLIResponse

_CAND = {"id": 1, "spec_code": "GB 50204", "clause_no": "8.2.1",
         "content": "混凝土", "title": "", "spec_status": "现行"}


def _backend(answer="答案"):
    b = AsyncMock()
    b.is_available = lambda: True
    b.ask = AsyncMock(return_value=CLIResponse(success=True, content=answer))
    return b


@pytest.fixture()
def qa_env(monkeypatch):
    """默认打桩：检索稳定返回 1 条候选、后端稳定成功。

    个别用例会用 monkeypatch 覆盖 hybrid_search 来模拟「筛选后候选不足」。
    """
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: _backend())


def test_narrow_filter_reports_filtered_out(auth_client, qa_env, monkeypatch):
    """正常场景：分类筛选候选不足时返回全局命中数，供前端提示。

    第 1 次 hybrid_search（带分类）返回 1 条 < qa_min_candidates(3)，
    触发诊断性的第 2 次（不带分类）→ 20 条，即 filtered_out。
    """
    calls = {"n": 0}

    def fake_search(sq):
        calls["n"] += 1
        if calls["n"] == 1:
            return [dict(_CAND)], 1
        return [dict(_CAND) for _ in range(20)], 20

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)

    r = auth_client.post("/qa/ask",
                         json={"question": "q", "dim4_specialty": ["混凝土"]})
    body = r.json()
    assert body["filtered_out"] == 20, "必须报告全局候选量，供前端提示可放宽"
    assert body["effective_filters"]["dim4_specialty"] == ["混凝土"]


def test_relaxed_request_skips_dimension_filters(auth_client, qa_env, monkeypatch):
    """正常场景：relaxed=True 时不携带分类维度检索，且不再报 filtered_out。"""
    seen = {}

    def fake_search(sq):
        seen["dims"] = sq.dim4_specialty
        return [dict(_CAND)], 1

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)

    body = auth_client.post("/qa/ask",
                            json={"question": "q", "dim4_specialty": ["混凝土"],
                                  "relaxed": True}).json()
    assert not seen["dims"], "relaxed 时必须清空分类维度"
    assert body["filtered_out"] == 0
    assert body["effective_filters"] == {}, "放宽后无生效筛选"


def test_no_filter_no_relax_hint(auth_client, qa_env):
    """边界场景：未使用分类筛选时 filtered_out 为 0，不产生放宽提示。"""
    body = auth_client.post("/qa/ask", json={"question": "q"}).json()
    assert body["filtered_out"] == 0
    assert body["effective_filters"] == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_relax.py -v`
Expected: FAIL — `KeyError: 'filtered_out'`

- [ ] **Step 3: 实现**

在 `app/models.py` 的 `QAResponse` 中，于 `session_id` 之后新增：

```python
    # 分类筛选候选不足时的全局命中数（>0 表示被筛选挡住，前端提示可放宽）
    filtered_out: int = 0
    # 本轮实际生效的分类筛选（前端展示"当前生效筛选"）
    effective_filters: dict = {}
```

在 `app/routes/qa_routes.py` 中，把「兜底：分类筛选使候选过少时放宽为全局检索」整段替换为：

```python
    # 分类筛选候选不足：**不再静默放宽**（设计文档 D9）。
    # 原因：QA 与检索结果同屏后，静默放宽会造成"左边筛了分类、
    # 右边答案却来自别的分类"的可见不一致。改为如实报告候选量，
    # 由前端提示并提供一键放宽（body.relaxed=True 重发）。
    filtered_out = 0
    if not body.relaxed and has_dim and len(candidates) < get_qa_int("retrieve.qa_min_candidates"):
        wide_sq = SearchQuery(
            keyword=question, per_page=pool,
            include_non_clause=keyword_mentions_non_clause,
        )
        try:
            wide_candidates, wide_total = hybrid_search(wide_sq)
            filtered_out = wide_total
            logger.info("QA 分类筛选候选不足（%d 条），已如实报告全局命中 %d 条",
                        len(candidates), wide_total)
        except Exception as e:
            logger.error("QA hybrid_search (diagnostic) failed: %s", e)
```

在返回处补齐字段：

```python
    effective_filters = {
        k: v for k, v in {
            "dim1_hierarchy": body.dim1_hierarchy,
            "dim1_industry": body.dim1_industry,
            "dim1_nature": body.dim1_nature,
            "dim2_stage": body.dim2_stage,
            "dim3_usage": body.dim3_usage,
            "dim4_specialty": body.dim4_specialty,
            "dim5_location": body.dim5_location,
            "dim6_material": body.dim6_material,
        }.items() if v
    }
    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits,
                      rerank_used=trace.rerank_used, session_id=session_id or 0,
                      filtered_out=filtered_out,
                      effective_filters={} if body.relaxed else effective_filters)
```

把 `has_dim` 与 `SearchQuery` 的构造改为在 `relaxed` 时清空维度：

```python
    dims = {} if body.relaxed else {
        "dim1_hierarchy": body.dim1_hierarchy,
        "dim1_industry": body.dim1_industry,
        "dim1_nature": body.dim1_nature,
        "dim2_stage": body.dim2_stage,
        "dim3_usage": body.dim3_usage,
        "dim4_specialty": body.dim4_specialty,
        "dim5_location": body.dim5_location,
        "dim6_material": body.dim6_material,
    }
    has_dim = any(dims.values())
    sq = SearchQuery(
        keyword=question, per_page=pool,
        include_non_clause=keyword_mentions_non_clause, **dims,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_relax.py tests/test_qa_session_routes.py tests/test_qa_routes.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/models.py app/routes/qa_routes.py tests/test_qa_relax.py
git commit -m "feat: 分类筛选候选不足改为显式提示 + 一键放宽（取消静默兜底）"
```

---

## Task 14: API 后端流式调用

**Files:**
- Modify: `app/ai/api_client.py`
- Test: `tests/test_qa_stream.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`
- Produces: `APIBackend.ask_stream(prompt, context="", system_prompt="", work_dir=None) -> AsyncIterator[dict]`
  - 逐条 yield `{"type": "delta", "text": str}`；结束 yield `{"type": "done"}`
  - 出错 yield `{"type": "error", "message": str}` 后 return
- Produces: `CLIBackend.ask_stream` 默认实现 —— 调 `ask()` 后一次性 yield 一个 delta + done（CLI 后端不支持真流式，SSE 里就一次性吐出；`stage` 事件仍给进度反馈，无需前端另走一条路径）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_qa_stream.py
"""API 后端 SSE 流式解析。"""
import json

import httpx
import pytest

from app.ai.api_client import APIBackend


def _sse(*chunks: str) -> bytes:
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps(
            {"choices": [{"delta": {"content": c}}]}, ensure_ascii=False))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


@pytest.mark.asyncio
async def test_ask_stream_yields_deltas(monkeypatch):
    """正常场景：逐块解析 delta，最后一条为 done。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("混凝土", "强度", "等级"))

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: httpx.AsyncClient(transport=transport, **kw))

    out = [e async for e in backend.ask_stream("q")]
    assert [e["text"] for e in out if e["type"] == "delta"] == ["混凝土", "强度", "等级"]
    assert out[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_ask_stream_reports_http_error(monkeypatch):
    """异常场景：HTTP 错误转成 error 事件，不抛异常中断 SSE 流。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}")

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: httpx.AsyncClient(transport=transport, **kw))

    out = [e async for e in backend.ask_stream("q")]
    assert out[-1]["type"] == "error"
    assert "429" in out[-1]["message"] or "超限" in out[-1]["message"]


@pytest.mark.asyncio
async def test_ask_stream_handles_empty_stream(monkeypatch):
    """边界场景：只有 [DONE] 无内容 → 仅 done，不产生空 delta。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: httpx.AsyncClient(transport=transport, **kw))

    out = [e async for e in backend.ask_stream("q")]
    assert out == [{"type": "done"}]


@pytest.mark.asyncio
async def test_ask_stream_skips_malformed_lines(monkeypatch):
    """异常场景：脏 SSE 行被跳过，不中断整个流。"""
    body = b'data: not-json\n\ndata: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: httpx.AsyncClient(transport=transport, **kw))

    out = [e async for e in backend.ask_stream("q")]
    assert [e["text"] for e in out if e["type"] == "delta"] == ["ok"]
```

同时在 `requirements.txt` 确认已有 `pytest-asyncio`；若无，追加 `pytest-asyncio>=0.23.0` 并在 `pyproject.toml`/`pytest.ini` 设 `asyncio_mode = auto`。

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_stream.py -v`
Expected: FAIL — `AttributeError: 'APIBackend' object has no attribute 'ask_stream'`

- [ ] **Step 3: 实现**

在 `app/ai/cli_client.py` 的 `CLIBackend` 中新增默认实现（CLI 后端不支持真流式，整段返回即可；**不需要 `supports_stream` 之类的判定标志**——单一入口下路由层无需据此分流）：

```python
    async def ask_stream(self, prompt: str, context: str = "",
                         system_prompt: str = "",
                         work_dir: str | None = None):
        """流式接口默认实现：不支持真流式的后端整段吐出。

        API 后端覆盖此方法做真流式；CLI 后端沿用本实现——
        SSE 里一次性发一个 delta 再 done，前端无需第二条渲染路径，
        stage 事件仍会在等待期间给出「生成中…」反馈。
        """
        resp = await self.ask(prompt, context=context,
                              system_prompt=system_prompt, work_dir=work_dir)
        if resp.success:
            if resp.content:
                yield {"type": "delta", "text": resp.content}
            yield {"type": "done"}
        else:
            yield {"type": "error", "message": resp.error or "调用失败"}
```

在 `app/ai/api_client.py` 的 `APIBackend` 中新增：

```python
    async def ask_stream(self, prompt: str, context: str = "",
                         system_prompt: str = "",
                         work_dir: str | None = None):
        """SSE 流式调用，逐块 yield {type: delta|done|error}。

        与 ask() 共用同一套 messages 构造，保证两种路径行为一致。
        """
        import httpx

        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
        messages = [
            {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM},
            {"role": "user", "content": full_prompt},
        ]
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with auth_client.stream(
                    "POST", f"{self.base_url}/chat/completions",
                    json={"model": self.model, "messages": messages,
                          "temperature": 0.3, "max_tokens": 2048, "stream": True},
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                ) as resp:
                    if resp.status_code != 200:
                        msg = ("API 调用超限 (429)，请检查当日配额或稍后重试"
                               if resp.status_code == 429
                               else f"API 返回错误 (HTTP {resp.status_code})")
                        yield {"type": "error", "message": msg}
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        text = _extract_delta(payload)
                        if text:
                            yield {"type": "delta", "text": text}
            yield {"type": "done"}
        except httpx.TimeoutException:
            yield {"type": "error", "message": "API 调用超时"}
        except httpx.HTTPError as e:
            logger.error("API 流式调用异常: %s", e)
            yield {"type": "error", "message": "API 调用失败，请检查网络连接或稍后重试"}
```

在 `app/ai/api_client.py` 顶部（`APIBackend` 之外）新增两个模块级常量/函数：

```python
# 默认 system prompt（与非流式 ask() 保持一致）
_DEFAULT_SYSTEM = (
    "你是建筑施工规范查询助手。只根据提供的上下文回答，不要编造规范条文。"
    "如果上下文中没有相关信息，请如实告知。回答请使用中文。"
)


def _extract_delta(payload: str) -> str:
    """从 SSE 单行 JSON 中取出增量文本；脏数据返回空串（跳过该行）。"""
    import json as _json
    try:
        data = _json.loads(payload)
    except ValueError:
        return ""
    try:
        return data["choices"][0]["delta"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
```

> 同时把 `APIBackend.ask()` 里硬编码的那段默认 system prompt 替换为引用 `_DEFAULT_SYSTEM`，避免两处漂移。

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_stream.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/ai/api_client.py app/ai/cli_client.py requirements.txt tests/test_qa_stream.py
git commit -m "feat: API 后端 SSE 流式调用（ask_stream）"
```

---

## Task 15: 流式输出（并入 `/qa/ask` 单一入口）

**Files:**
- Modify: `app/models.py`（`QaRequest.stream`）
- Modify: `app/routes/qa_routes.py`（抽出 `_prepare_qa_context` + `/qa/ask` 增加流式分支）
- Test: `tests/test_qa_stream.py`（追加）

**Interfaces:**
- Consumes: `APIBackend.ask_stream` / `CLIBackend.ask_stream`（T14）、`app.qa.sessions`（T5）、`app.qa.context.build_history`（T6）
- Produces: `QaContext`（dataclass）与 `_prepare_qa_context(question: str, body: QaRequest) -> QaContext`（均模块级）
- Produces: `QaRequest.stream: bool = False`
- Produces: `POST /qa/ask` 在 `stream=True` 时返回 `text/event-stream`；事件序列 `stage`×N → `delta`×N → `done` | `error`

> **为什么不新增 `/qa/ask/stream` 路由**（评审决定 D3）：
> 独立路由会复制整条检索链路。首版设计已因此产生两个真实缺陷——
> 流式版漏了 `_emit_trace`（流式问答在日志 Tab 中完全不可观测，而流式正是主路径），
> 以及在 `done` 事件处**跨 `await` 读模块级全局** `_last_rerank_used`
> （并发请求互相覆盖，前端显示的精排状态标记会标错）。
> 合并为一个入口后逻辑只有一份，两处缺陷自然消失，也无需「按后端类型选路由」这条隐含逻辑。
>
> 既有 `tests/test_qa_routes.py` 全部不带 `stream` → 默认 `False` → 行为不变，**零改动**。

- [ ] **Step 1: 写失败测试**

改写 `tests/test_qa_stream.py` 的 T15 部分（T14 的 API 后端用例保持不动）。把 T14 用到的 `_stream_backend`
改为同时提供 `ask` 与 `ask_stream`，使同一 fixture 能覆盖两种输出形态：

```python
import pytest
from unittest.mock import AsyncMock

from app.ai.cli_client import CLIResponse
from app.database import get_db
from app.qa import sessions as S

_CAND = {"id": 1, "spec_code": "GB 50204", "clause_no": "8.2.1",
         "content": "混凝土", "title": "", "spec_status": "现行"}


def _stream_backend(chunks=("混凝土", "强度")):
    """同时支持流式与非流式的桩后端。"""
    b = AsyncMock()
    b.is_available = lambda: True

    async def _gen(prompt, context="", system_prompt="", work_dir=None):
        for c in chunks:
            yield {"type": "delta", "text": c}
        yield {"type": "done"}

    b.ask_stream = _gen
    b.ask = AsyncMock(return_value=CLIResponse(
        success=True, content="".join(chunks)))
    return b


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析为 (event, data) 列表。"""
    import json as _json
    out, ev = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            out.append((ev, _json.loads(line[5:].strip())))
    return out


@pytest.fixture()
def stream_env(monkeypatch):
    """打桩检索与后端：两种输出形态都走同一套桩，且不加载模型。"""
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend",
                        lambda name=None: _stream_backend())


def test_default_is_non_streaming_json(auth_client, stream_env):
    """回归（入口合并的核心契约）：不带 stream 时仍是原 JSON 响应。

    既有 tests/test_qa_routes.py 全部依赖该行为，合并入口不得改变它。
    """
    r = auth_client.post("/qa/ask", json={"question": "q"})
    assert "text/event-stream" not in r.headers["content-type"]
    assert "answer" in r.json()


def test_stream_emits_stage_then_deltas_then_done(auth_client, stream_env):
    """正常场景：stream=true 时事件顺序为 stage → delta×N → done。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    kinds = [e for e, _ in _parse_sse(r.text)]
    assert kinds[0] == "stage", "首个事件必须是阶段进度（覆盖流式前的死时间）"
    assert kinds[-1] == "done"
    assert "delta" in kinds


def test_stream_done_carries_session_id_and_sources(auth_client, stream_env):
    """正常场景：done 事件携带会话 id 与参考条文，供前端收尾渲染。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["session_id"] > 0
    assert done["sources"][0]["clause_no"] == "8.2.1"


def test_stream_persists_messages_on_success(auth_client, stream_env):
    """正常场景：流式成功后消息落库（与非流式行为一致）。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    sid = [d for e, d in _parse_sse(r.text) if e == "done"][0]["session_id"]
    assert [m["role"] for m in S.get_messages(sid)] == ["user", "assistant"]


def test_stream_error_event_when_backend_fails(auth_client, stream_env, monkeypatch):
    """异常场景：后端报错时发 error 事件，且整轮不落库。"""
    b = AsyncMock()
    b.is_available = lambda: True

    async def _gen(prompt, context="", system_prompt="", work_dir=None):
        yield {"type": "error", "message": "API 调用超时"}

    b.ask_stream = _gen
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    assert _parse_sse(r.text)[-1][0] == "error"
    assert S.list_sessions() == [], "失败轮次不得建库"


def test_stream_continues_into_existing_session(auth_client, stream_env):
    """正常场景：带 session_id 的流式请求续聊同一会话。"""
    sid = S.create_session("s")
    r = auth_client.post("/qa/ask",
                         json={"question": "q", "session_id": sid, "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["session_id"] == sid
    assert len(S.get_messages(sid)) == 2


def test_stream_empty_question_returns_400(auth_client, stream_env):
    """异常场景：空问题返回 400，不建立 SSE 流。"""
    r = auth_client.post("/qa/ask", json={"question": "   ", "stream": True})
    assert r.status_code == 400


def test_stream_request_is_traced(auth_client, stream_env):
    """回归（评审修复项）：流式请求必须落 qa_request_logs。

    首版设计的新增路由漏了 _emit_trace，导致走流式的问答（主路径）
    在日志 Tab 中完全不可见。合并入口后，埋点在两条路径上都生效。
    """
    auth_client.post("/qa/ask", json={"question": "流式埋点检查", "stream": True})
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM qa_request_logs WHERE question LIKE ?",
            ("%流式埋点检查%",),
        ).fetchone()[0]
    assert n == 1, "流式请求未落埋点表"


def test_non_stream_request_is_traced(auth_client, stream_env):
    """回归：非流式请求同样落埋点（合并入口不得丢失既有行为）。"""
    auth_client.post("/qa/ask", json={"question": "非流式埋点检查"})
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM qa_request_logs WHERE question LIKE ?",
            ("%非流式埋点检查%",),
        ).fetchone()[0]
    assert n == 1


def test_qa_dim_fields_matches_request_model():
    """一致性守卫：QA_DIM_FIELDS 必须与 QaRequest 的维度字段完全对应。

    该常量是「检索条件构造」与「当轮筛选记录」的唯一来源。若将来新增维度
    只改了 QaRequest 而漏改常量，检索会**静默忽略新维度**（筛选界面能选、
    但不生效），极难排查。本用例把两者钉死，让漏改立刻失败。
    """
    from app.models import QA_DIM_FIELDS, QaRequest
    model_dims = {k for k in QaRequest.model_fields if k.startswith("dim")}
    assert set(QA_DIM_FIELDS) == model_dims, (
        f"QA_DIM_FIELDS 与 QaRequest 维度字段不一致："
        f"仅常量有 {set(QA_DIM_FIELDS) - model_dims}，仅模型有 {model_dims - set(QA_DIM_FIELDS)}"
    )


def test_include_non_clause_flag_is_honored(auth_client, stream_env, monkeypatch):
    """回归（静默 no-op）：左栏「包含前言·条文说明」必须真的到达检索层。

    QaRequest 未声明该字段时，Pydantic 默认 extra='ignore' 会静默丢弃它——
    前端传了也不生效，且没有任何报错。本用例捕获 SearchQuery 断言开关生效。
    """
    seen = {}

    def fake_search(sq):
        seen["inc"] = sq.include_non_clause
        return [dict(_CAND)], 1

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)
    auth_client.post("/qa/ask", json={"question": "q", "include_non_clause": True})
    assert seen.get("inc") is True, "include_non_clause 未到达检索层（字段未声明？）"


def test_question_text_fallback_still_releases_non_clause(auth_client, stream_env,
                                                          monkeypatch):
    """边界场景：问题文本含「条文说明」时隐式放行。

    这是设计文档 §4.3 的兜底条款——新增显式开关后，文本兜底不得失效。
    """
    seen = {}

    def fake_search(sq):
        seen["inc"] = sq.include_non_clause
        return [dict(_CAND)], 1

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)
    auth_client.post("/qa/ask", json={"question": "条文说明里怎么写的"})
    assert seen.get("inc") is True


def test_effective_filters_recorded_with_assistant_message(auth_client, stream_env):
    """正常场景：当轮生效筛选随助手消息落库（D5），回看可追溯。"""
    auth_client.post("/qa/ask", json={"question": "q", "dim4_specialty": ["混凝土"]})
    sid = S.list_sessions()[0]["id"]
    msgs = S.get_messages(sid)
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["filters"]["dim4_specialty"] == ["混凝土"]


def test_done_rerank_used_is_not_read_from_global(auth_client, stream_env, monkeypatch):
    """回归（竞态）：done 里的 rerank_used 必须是本请求的值。

    首版设计在流式循环**之后**读模块级全局 _last_rerank_used——
    期间任何并发请求都会覆盖它。本用例在流式进行中篡改该全局，
    模拟并发干扰，断言 done 事件不受影响。
    """
    import app.routes.qa_routes as qr

    async def _gen(prompt, context="", system_prompt="", work_dir=None):
        yield {"type": "delta", "text": "第一段"}
        # 模拟另一并发请求在本请求流式期间改写了全局
        qr._last_rerank_used = "vector"
        yield {"type": "delta", "text": "第二段"}
        yield {"type": "done"}

    b = AsyncMock()
    b.is_available = lambda: True
    b.ask_stream = _gen
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)
    monkeypatch.setattr(qr, "_last_rerank_used", "crossencoder")

    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["rerank_used"] == "crossencoder", \
        "done 必须回报本请求的精排级别，不能被并发请求改写"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_qa_stream.py -v`
Expected: FAIL — `test_stream_emits_stage_then_deltas_then_done` 返回 JSON 而非 SSE（`stream` 字段尚不存在，被 Pydantic 忽略）

- [ ] **Step 3: 实现**

**3a. `app/models.py`** —— `QaRequest` 增两个字段（放在 `relaxed` 之后），并把维度字段名收敛为单一常量：

```python
    # 输出形态：False → 一次性 JSON（默认，保持既有契约）；
    # True → SSE 流式（stage/delta/done/error）。两种形态共用同一套检索准备。
    stream: bool = False
    # 放行前言/条文说明等打标非条文（来自左栏「包含前言·条文说明」复选框）。
    # ⚠️ 必须显式声明：Pydantic 默认 extra='ignore'，未声明的字段会被静默丢弃，
    #    前端传了也不生效——这类"静默 no-op"极难排查。
    include_non_clause: bool = False
```

**3b.（接上）维度字段名收敛为单一常量——**新增维度时只改这一处**：

```python
# 维度筛选的请求字段名（dim1 含三个子维度，共 8 个字段）。
# 检索条件构造与「当轮生效筛选」记录都从这里派生，避免同一个列表
# 在 _prepare_qa_context / _effective_filters 里各写一遍而漏改。
QA_DIM_FIELDS: tuple[str, ...] = (
    "dim1_hierarchy", "dim1_industry", "dim1_nature",
    "dim2_stage", "dim3_usage", "dim4_specialty",
    "dim5_location", "dim6_material",
)
```

**3c. `app/ai/reranker.py` / `app/ai/embedding.py`** —— 各新增一个**不触发加载**的就绪探测：

```python
def is_ready() -> bool:
    """模型是否就绪：已加载成功，或本地模型文件存在。**不触发加载**。

    供健康检查使用。get_reranker()/get_model() 在未加载时会真的实例化模型
    （数秒），而健康检查在维护页每次打开都跑，不能带这种副作用。
    """
    if _model is False:
        return False          # 曾尝试加载且失败，不会重试
    if _model is not None:
        return True           # 已成功加载
    return any((p / "config.json").exists() for p in _LOCAL_MODEL_PATHS)
```

> 两个模块的三态哨兵与 `_LOCAL_MODEL_PATHS` 结构一致（见 `app/ai/reranker.py:11-16`、
> `app/ai/embedding.py:10-17`），因此函数体逐字相同——**但各自定义一份**，
> 不抽公共工具：它们读的是各自的模块级私有状态，抽出去反而要传参。

**3d. `app/maintenance/health_check.py`** —— 挂上检查项：

```python
@dataclass
class QaContext:
    """一次问答的检索准备结果（非流式与流式共用）。

    抽出它是为了让两种输出形态共用同一份检索逻辑——重复一份必然漂移
    （首版设计已因此漏掉埋点、并在错误位置读全局 rerank_used 造成竞态）。
    """
    question: str
    context_str: str
    picked: list
    trace: QATrace
    filtered_out: int
    rerank_used: str          # 准备阶段立即拷贝，不随后续并发请求变化
    history_str: str
    session_id: int | None


def _prepare_qa_context(question: str, body: QaRequest) -> QaContext:
    """检索 → 元数据过滤 → 精排 → 阈值过滤 → 动态条数 → 分层 → 上下文组装。

    纯准备阶段：不调用模型、不落库、不写埋点。
    """
    from app.qa.context import (
        filter_by_metadata, filter_by_score, dynamic_select,
        tier_items, build_context, build_history,
    )
    from app.qa.config import get_qa_float, get_qa_int, get_qa_str
    from app.qa import sessions as qa_sessions
    from app.search.hybrid_search import hybrid_search

    trace = QATrace(question=question[:100], mode=body.mode,
                    backend=body.backend or "", include_invalid=body.include_invalid)

    # 会话解析：不存在的 id 一律视为新会话——严格不跨会话取历史（D3）
    session_id = body.session_id
    if session_id is not None and qa_sessions.get_session(session_id) is None:
        logger.info("QA session_id=%s 不存在，按新会话处理", session_id)
        session_id = None
    history_messages = qa_sessions.get_messages(session_id) if session_id else []
    history_str = build_history(
        history_messages, get_qa_int("history.max_turns"),
        get_qa_int("token.max_history_tokens"),
    )

    pool = get_qa_int("retrieve.candidate_pool")
    # 放行非条文：左栏复选框显式开关 **或** 问题文本兜底
    # （「问题文本含前言/条文说明字样时隐式放行」是设计文档 §4.3 的兜底条款）
    include_non_clause = body.include_non_clause or \
        ("前言" in question) or ("条文说明" in question)
    # 分类维度：只取非空项（SearchQuery 的对应字段默认 []，语义等价）；
    # relaxed=True 时清空（D9 的「放宽分类筛选」）。字段名来自单一常量。
    dims = {} if body.relaxed else {
        k: getattr(body, k) for k in QA_DIM_FIELDS if getattr(body, k)
    }
    has_dim = bool(dims)
    try:
        candidates, total = hybrid_search(SearchQuery(
            keyword=question, per_page=pool,
            include_non_clause=include_non_clause, **dims))
    except Exception as e:
        logger.error("QA hybrid_search failed: %s", e)
        candidates, total = [], 0
    trace.rrf_total = total
    trace.pool_size = len(candidates)

    # 分类筛选候选不足：不再静默放宽（D9）——如实报告候选量供前端提示
    filtered_out = 0
    if not body.relaxed and has_dim and len(candidates) < get_qa_int("retrieve.qa_min_candidates"):
        try:
            _, wide_total = hybrid_search(SearchQuery(
                keyword=question, per_page=pool,
                include_non_clause=include_non_clause))
            filtered_out = wide_total
            logger.info("QA 分类筛选候选不足（%d 条），全局命中 %d 条",
                        len(candidates), wide_total)
        except Exception as e:
            logger.error("QA 诊断性放宽检索失败: %s", e)

    status_allow = tuple(
        s.strip() for s in get_qa_str("meta.status_allow").split(",") if s.strip())
    candidates = filter_by_metadata(candidates, body.include_invalid, status_allow=status_allow)
    trace.after_meta = len(candidates)

    ranked = _rerank_scored(question, candidates)
    rerank_used = _last_rerank_used      # 立即拷贝：不随后续 await 期间的其他请求变化
    trace.rerank_used = rerank_used
    min_score, high_thr = resolve_thresholds(rerank_used, get_qa_float)
    ranked = filter_by_score(ranked, min_score)
    trace.after_threshold = len(ranked)

    k = dynamic_select(
        len(ranked),
        top_ratio=get_qa_float("retrieve.top_ratio"),
        min_results=get_qa_int("retrieve.min_results"),
        max_results=get_qa_int("retrieve.max_results"),
    )
    trace.select_target = k
    ranked = ranked[:k]

    summary_limit = get_qa_int("token.summary_chars")
    high, low = tier_items(ranked, high_thr, min_score, summary_limit)
    trace.high_count, trace.low_count = len(high), len(low)

    budget = get_qa_int("token.max_context_tokens")
    context_str, used_tok, dropped, picked = build_context(
        high, low, body.mode == "verbatim", budget)
    trace.context_tokens, trace.budget = used_tok, budget
    trace.dropped_overflow = dropped
    trace.context_empty = not context_str.strip()

    if history_str:
        context_str = f"{history_str}\n\n{context_str}"

    return QaContext(question=question, context_str=context_str, picked=picked,
                     trace=trace, filtered_out=filtered_out, rerank_used=rerank_used,
                     history_str=history_str, session_id=session_id)


def _finish_turn(ctx: QaContext, answer: str, persist_ok: bool,
                 body: QaRequest) -> tuple[list[dict], list[dict], int | None]:
    """收尾（两条路径共用）：提取来源、检测易混淆、成功则落库、写埋点。

    返回 (sources, confusable_hits, session_id)。失败轮次不入库（避免半截会话）。
    """
    from app.qa import sessions as qa_sessions

    sources = _extract_sources([it.clause for it, _ in ctx.picked])
    confusable_hits = _confusable_hits(ctx.question)
    session_id = ctx.session_id
    if persist_ok:
        if session_id is None:
            session_id = qa_sessions.create_session(
                qa_sessions.derive_title(ctx.question))
        qa_sessions.append_message(session_id, "user", ctx.question)
        # 助手消息记录**当轮实际生效的筛选**（D5）——回看历史时据此还原
        # 「这条答案是在什么筛选下产生的」。筛选不入库则该信息不可逆丢失。
        qa_sessions.append_message(
            session_id, "assistant", answer,
            sources=sources, confusable=confusable_hits,
            filters=_effective_filters(body), mode=body.mode)
    _emit_trace(ctx.trace)
    return sources, confusable_hits, session_id


def _confusable_hits(question: str) -> list[dict]:
    """易混淆术语命中：检测对象恒为用户问题原文（仅提示，不做任何改写）。"""
    if not question:
        return []
    from app.lexicon import store, confusable
    return confusable.detect_confusable(question, store.load_confusable_pairs())


def _effective_filters(body: QaRequest) -> dict:
    """本轮实际生效的筛选（分类维度 + 状态 + 前言放行）。

    两个用途：① 随助手消息落库，供历史回看追溯（D5）；
    ② 随响应返回，供前端显示「本轮生效筛选」（让"回复中切换只影响下一轮"可见）。
    放宽（relaxed）时分类维度为空——那正是放宽的语义。
    维度字段名取自 QA_DIM_FIELDS（单一来源），新增维度时不会漏记。
    """
    out: dict = {}
    if not body.relaxed:
        out = {k: getattr(body, k) for k in QA_DIM_FIELDS if getattr(body, k)}
    # 状态过滤：None（缺参）时不记录，避免把默认白名单误当成用户显式选择
    if body.status_filter is not None:
        out["status_filter"] = body.status_filter
    if body.include_non_clause:
        out["include_non_clause"] = True
    return out
```

**3d.** 把 `/qa/ask` 整体替换为「一个入口、两种形态」：

```python
@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    """AI 问答。

    默认返回 JSON；body.stream=True 时返回 SSE（text/event-stream）。
    两种输出形态共用 _prepare_qa_context，检索逻辑只有一份。
    """
    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    ctx = _prepare_qa_context(question, body)

    if body.stream:
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            _sse_stream(ctx, body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return await _qa_json(ctx, body)


async def _qa_json(ctx: QaContext, body: QaRequest):
    """非流式输出：一次推理 → 收尾落库/埋点 → 完整 JSON。"""
    from app.ai.prompts import build_system_prompt
    from app.config import WORKSPACE_DIR

    backend, cli_used = _resolve_backend(body.backend, ctx.trace)
    if not backend.is_available():
        return JSONResponse({"detail": f"{cli_used} 不可用，请确认已配置"},
                            status_code=503)

    start = time.time()
    resp = await backend.ask(
        prompt=ctx.question, context=ctx.context_str,
        system_prompt=build_system_prompt(body.mode, multi_turn=bool(ctx.history_str)),
        work_dir=WORKSPACE_DIR,
    )
    ctx.trace.duration_ms = int((time.time() - start) * 1000)

    persist_ok = bool(resp.success and resp.content.strip())
    answer = _answer_text(resp, cli_used)
    sources, confusable_hits, session_id = _finish_turn(ctx, answer, persist_ok, body)

    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits,
                      rerank_used=ctx.rerank_used, session_id=session_id or 0,
                      filtered_out=ctx.filtered_out,
                      effective_filters=_effective_filters(body))


async def _sse_stream(ctx: QaContext, body: QaRequest):
    """流式输出：stage×N → delta×N → done | error。

    注意：rerank_used 取自 ctx（准备阶段已拷贝），**不得**在此处再读
    模块级 _last_rerank_used——本函数跨多次 await，期间并发请求会改写它。
    """
    from app.ai.prompts import build_system_prompt
    from app.config import WORKSPACE_DIR

    yield _sse("stage", {"stage": "retrieving"})
    yield _sse("stage", {"stage": "reranking"})

    backend, cli_used = _resolve_backend(body.backend, ctx.trace)
    if not backend.is_available():
        yield _sse("error", {"message": f"{cli_used} 不可用，请确认已配置"})
        return

    yield _sse("stage", {"stage": "generating"})

    parts: list[str] = []
    start = time.time()
    async for ev in backend.ask_stream(
        prompt=ctx.question, context=ctx.context_str,
        system_prompt=build_system_prompt(body.mode, multi_turn=bool(ctx.history_str)),
        work_dir=WORKSPACE_DIR,
    ):
        if ev["type"] == "delta":
            parts.append(ev["text"])
            yield _sse("delta", {"text": ev["text"]})
        elif ev["type"] == "error":
            ctx.trace.duration_ms = int((time.time() - start) * 1000)
            yield _sse("error", {"message": ev.get("message") or "AI 服务返回错误"})
            _emit_trace(ctx.trace)      # 失败也埋点，供排查
            return
    ctx.trace.duration_ms = int((time.time() - start) * 1000)

    answer = "".join(parts)
    sources, confusable_hits, session_id = _finish_turn(
        ctx, answer, bool(answer.strip()), body)

    yield _sse("done", {
        "session_id": session_id or 0,
        "sources": sources,
        "confusable_hits": confusable_hits,
        "rerank_used": ctx.rerank_used,      # 拷贝值，非全局
        "filtered_out": ctx.filtered_out,
        "effective_filters": _effective_filters(body),   # 前端显示「本轮生效筛选」（D5）
    })
```

**3e.** 新增三个小工具（放在 `_extract_sources` 之后）：

```python
def _sse(event: str, data: dict) -> str:
    """构造一条 SSE 消息（event + data 各一行，以空行结束）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_backend(backend_name: str | None, trace: QATrace):
    """解析后端并回填可读名到埋点，返回 (backend, cli_used)。"""
    from app.ai.cli_client import get_backend
    backend = get_backend(backend_name)
    if isinstance(backend, APIBackend):
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(backend_name or "")
        cli_used = preset["name"] if preset else "自定义"
    else:
        cli_used = (backend.command or "cli").replace("\\", "/").rsplit("/", 1)[-1]
    if not trace.backend:
        trace.backend = cli_used
    return backend, cli_used


def _answer_text(resp, cli_used: str) -> str:
    """把后端响应归一为给用户看的文本（失败/空内容给可读提示）。"""
    if resp.success and resp.content.strip():
        return resp.content
    if resp.success:
        logger.warning("QA 后端返回空内容 (command=%s)", cli_used)
        return "抱歉，AI 服务返回了空内容，请确认后端已正确配置。"
    logger.warning("QA 后端错误 (command=%s): %s", cli_used, resp.error)
    return "抱歉，AI 服务返回错误。" + ("（超时）" if "超时" in (resp.error or "") else "")
```

> **同步删除** T8 与 T13 引入的旧 `/qa/ask` 函数体——其逻辑已全部并入
> `_prepare_qa_context` / `_qa_json` / `_finish_turn`。这是纯重构，
> `tests/test_qa_routes.py`、`tests/test_qa_session_routes.py`、`tests/test_qa_relax.py`
> 必须全部保持通过，用以证明抽取无行为变化。

- [ ] **Step 4: 运行测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_qa_stream.py -v`
Expected: PASS（T14 的 4 条 + 本 Task 的 10 条）

- [ ] **Step 5: 全量回归 + 类型检查**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: 全部 PASS（既有约 726 条 + 本计划新增）

Run: `D:/Python/python.exe -m pyright app/`
Expected: 无新增 error

- [ ] **Step 6: 提交**

```bash
git add app/models.py app/routes/qa_routes.py tests/test_qa_stream.py
git commit -m "feat: /qa/ask 单一入口支持流式输出（SSE），消除检索链路重复"
```

## 完成标准

- [ ] `tests/` 全量通过，无回归
- [ ] `pyright app/` 无新增 error
- [ ] 15 个 Task 各自单次提交，提交信息符合 `type: 描述` 规范
- [ ] `GET /qa/sessions`、`GET /qa/sessions/{id}`、`PATCH`、`DELETE`、`export`、`/qa/search` 均可访问
- [ ] `POST /qa/ask` 带 `stream=true` 返回 `text/event-stream`，不带则返回 JSON（既有契约不变）
- [ ] `/qa/ask` 非流式路径行为不变（既有用例通过）
- [ ] 第 3 级降级不再产生"全 1.0 → 全 high"，有回归测试守卫
- [ ] 流式与非流式两条路径都落 `qa_request_logs`（埋点不因输出形态而丢失）

## 后续（不在本计划内）

- **计划 2（前端）**：`#main-content` 容器契约、QA 页布局与折叠、筛选统一、会话列表 UI 与续聊交互、SSE 前端读取与降级渲染、分阶段进度提示
- 修复 `qa.js` 对 `/qa/ask` 的调用以携带 `session_id`
---

## 评审记录（2026-09-23 · `/plan-eng-review`）

评审对象：本计划 + `2026-09-21-qa-frontend-layout-stream.md` + `2026-09-21-qa-history-session-design.md`。
外部独立评审：Codex（`codex exec`，只读，模型默认，参考力度 高）。

### What already exists（已有实现，本计划复用而非重建）

| 子问题 | 已有实现 | 计划如何处理 |
|---|---|---|
| 精排三级降级链 | `app/routes/qa_routes.py:30-67` 的 `_rerank_scored` | **复用**，只改第 3 级的分数语义与阈值解析 |
| 候选过滤/分层/上下文组装 | `app/qa/context.py`（`filter_by_score`/`tier_items`/`build_context`/`estimate_tokens`） | **完全复用**——第 3 级修正靠「喂进去的分数换成语义正确的值」实现，该文件一行不改 |
| 筛选状态共享 | `Alpine.store('searchState')`（`tree.js`） | **复用**，QA 改读它而非自建副本 |
| 问答埋点 | `qa_request_logs` 表 + `_emit_trace` | **复用且不动表结构**（与会话关联留作 YAGNI） |
| 健康检查框架 | `app/maintenance/health_check.py` 的 `LABELS`/`checks`/`fix_issue` | **复用**，加一项 `model_ready` |
| 参数设置 UI | `app/params/registry.py` 的 `qa` 分组 | **复用**，加两条注册表项即自动渲染进维护页 |
| markdown 渲染管线 | `static/components/md-render.js`（marked + DOMPurify + KaTeX） | **复用**，收尾阶段直接调用；**计划已不再改动该文件** |
| 会话持久化模式 | `app/database.py` 的 `CREATE TABLE IF NOT EXISTS` + 迁移段 | **复用**同一模式 |
| 全文检索 | `clauses_fts`（FTS5 + jieba 预分词） | ⚠️ **有意不用**——`qa_messages` 是小表，改 LIKE + 通配符转义（理由见 spec §4.12） |

### NOT in scope（本次明确不做）

| 项 | 理由 |
|---|---|
| HTML 导出 | 已有 md 渲染管线，导出的 md 可直接丢回系统渲染；HTML 导出等于把渲染结果静态化，多一份维护面而不增能力 |
| 给 `qa_messages` 建 FTS 索引 | 小表用 LIKE 足够；上 FTS 需额外维护索引同步（含级联删除），复杂度远超收益 |
| 「仅当前会话」搜索勾选框 | 会话内通常只有几轮，价值低；全局搜索是它的自然超集 |
| `qa_request_logs` 增 `session_id` 列 | 按会话分析检索质量确有价值，但本轮不动既有埋点表结构（已记入 TODO T12 的邻域） |
| 会话绑定用户 / 权限 | D6 已定：全局共享。**代价已知**：多用户环境下任何登录用户可读可删他人历史（用户 2026-09-23 明确不采纳该项 TODO） |
| CLI 后端取消 | 三个调用点、`APIBackend` 可覆盖，但属独立改造（TODO T13） |
| 模型安装期可选化 | 属封装方案范畴（TODO T14） |
| CLI 后端的真流式 | `CLIBackend.ask_stream` 为一次性吐出；`_run_cli` 的同步 `subprocess.run` 在 async 路径会阻塞事件循环——**pre-existing，本轮不修**（已记入 TODO T13） |
| 流式中断的服务端语义 | 本轮未定义（TODO T12） |

### 失败模式（新增代码路径）

| 路径 | 现实失败场景 | 有测试？ | 有错误处理？ | 用户可见？ |
|---|---|---|---|---|
| `_prepare_qa_context` 检索段 | `hybrid_search` 抛异常 | ✅ `test_qa_routes` 既有 | ✅ `except` + 空候选降级，`logger.error` | ✅ 得到「未检索到相关条文」而非报错 |
| 第 3 级降级排名切分 | 无模型环境下全部候选被误判强弱 | ✅ **本轮新增回归守卫** | ✅ 透传排名分数替代全 1.0 | ✅ 前端显示「⚠️ 无精排」 |
| 会话落库 | SQLite 并发写锁 | ✅ `test_concurrent_appends_same_session_do_not_lose_or_mix` | ✅ `get_db()` 的 `timeout=30` 串行化 | ⚠️ 超时会 500；概率极低 |
| 流式 SSE | 后端在流中途报错 | ✅ `test_stream_error_event_when_backend_fails` | ✅ 发 `error` 事件 + 补埋点 + 不落库 | ✅ 前端显示错误并回退非流式 |
| 流式 SSE | **客户端中断**（关页/切会话） | ❌ **无** | ❌ **无**（未定义） | ⚠️ **静默**——可能留空会话、可能前端重发导致双倍生成 → **critical gap，记入 TODO T12** |
| 导出 Markdown | 会话标题含换行/特殊字符 | ✅ 用 `quote()` 做 RFC 5987 编码 | ✅ | ✅ 文件名正确，无头注入 |
| 跨会话搜索 | 用户输入含 `%` / `_` | ✅ `test_search_messages_escapes_like_wildcards` | ✅ `_escape_like` | ✅ 不会退化为全表命中 |
| 健康检查 | 模型未装 | ✅ 三条用例 | ✅ 报告 severity + hint | ✅ 维护页显式可见 |
| 前端 SSE 读取 | `fetch` 流中断 | ✅ 探针 t5 | ✅ `_fallbackAsk` 回退 | ✅ |
| QA 页 URL 回填 | 维度名与后端漂移 | ✅ 探针 t1 | — | ✅ 筛选与树状态不一致会被探针抓到 |

**critical gap 计 1 条**：流式中断的服务端语义未定义（无测试、无处理、静默）。

### 并行化

**顺序实施，无实质并行机会。** 两个计划共用 `app/routes/qa_routes.py` 与 `static/components/qa.js`，
且计划 2 依赖计划 1 的全部接口（`/qa/sessions*`、`/qa/search`、`/qa/ask` 的 `stream` 开关、`rerank_used`）。
计划 1 内部任务链亦为线性（T1 的 degrade 模块被 T8/T13/T15 依赖；T4 的表被 T5~T13 依赖）。

| 阶段 | 模块 | 依赖 |
|---|---|---|
| 计划 1 | `app/qa/`、`app/ai/`、`app/routes/qa_routes.py`、`app/database.py` | — |
| 计划 2 | `app/templates/`、`static/components/`、`static/app.css` | 计划 1 全部合入 |

**Lane A**：计划 1（T1→T15，严格顺序）
**Lane B**：计划 2（T1→T5，严格顺序），**必须等 Lane A 完成**

两条 Lane 共用 `app/routes/qa_routes.py`（计划 2 的 T1 加 `GET /qa`），**不可并行**。

### Implementation Tasks

- [ ] **T1 (P1, human: ~2h / CC: ~20min)** — `app/qa/degrade.py` — 第 3 级降级改按排名切分
  - Surfaced by: 架构评审 —— `_rerank_scored` 第 3 级返回全 1.0 会让 `tier_items` 把全部候选判为 high，摘要压缩失效、token 预算被吃光
  - Files: `app/qa/degrade.py`、`app/routes/qa_routes.py`
  - Verify: `pytest tests/test_qa_degrade.py -v`
- [ ] **T2 (P1, human: ~3h / CC: ~30min)** — 流式并入 `/qa/ask` 单一入口，抽 `_prepare_qa_context`
  - Surfaced by: 架构评审 —— 独立路由复制 72 行检索链，已致埋点缺失 + `_last_rerank_used` 跨 await 竞态
  - Files: `app/routes/qa_routes.py`、`app/models.py`
  - Verify: `pytest tests/test_qa_stream.py -v`（含 3 条回归守卫）
- [ ] **T3 (P1, human: ~2h / CC: ~20min)** — 会话持久化 + 当轮筛选落库
  - Surfaced by: 代码质量评审 —— 筛选不入库则历史答案的筛选背景不可逆丢失
  - Files: `app/qa/sessions.py`、`app/database.py`
  - Verify: `pytest tests/test_qa_sessions.py -v`（含并发与空会话用例）
- [ ] **T4 (P1, human: ~1h / CC: ~10min)** — `QaRequest.include_non_clause` 显式声明
  - Surfaced by: 代码质量评审 —— Pydantic `extra='ignore'` 会静默丢弃未声明字段，前端传了不生效且无报错
  - Files: `app/models.py`、`app/routes/qa_routes.py`
  - Verify: `pytest tests/test_qa_stream.py -k include_non_clause -v`
- [ ] **T5 (P2, human: ~2h / CC: ~20min)** — 历史段独立 token 预算
  - Surfaced by: 性能评审 —— 历史段与条文段共用 `token.max_context_tokens`，总上下文达配置值两倍
  - Files: `app/config.py`、`app/params/registry.py`、`app/routes/qa_routes.py`
  - Verify: `pytest tests/test_qa_history.py -v`
- [ ] **T6 (P2, human: ~4h / CC: ~45min)** — QA 改整页导航 + 筛选进 URL
  - Surfaced by: 架构评审 —— htmx 局部替换的唯一收益（筛选携带）本就非需求，代价却是改检索页 swap 目标 + Alpine-in-swap 无先例 + 后退键失效
  - Files: `app/routes/qa_routes.py`、`app/templates/base.html`、`static/components/qa.js`、`tree.js`、`search.js`
  - Verify: `python scripts/probe_qa_ui.py t1`
- [ ] **T7 (P2, human: ~1h / CC: ~10min)** — 流式渲染改两态纯文本
  - Surfaced by: 架构评审（外部） —— 「节流 + marked」真正的问题是半截 Markdown 反复重排的观感，且顺带引入未闭合公式风险
  - Files: `static/components/qa.js`、`static/app.css`
  - Verify: `python scripts/probe_qa_ui.py t5`
- [ ] **T8 (P2, human: ~30min / CC: ~5min)** — 健康检查改探测不实例化
  - Surfaced by: 性能评审（外部） —— `get_reranker()` 会在维护页真加载模型（数秒）
  - Files: `app/ai/reranker.py`、`app/ai/embedding.py`、`app/maintenance/health_check.py`
  - Verify: `pytest tests/test_health_check_models.py -v`
- [ ] **T9 (P3, human: ~1d / CC: ~1h)** — 流式中断的服务端语义（见 TODOS.md T12）
- [ ] **T10 (P3, human: ~1d / CC: ~1h)** — CLI 后端取消（见 TODOS.md T13）

### 评审结论

两个计划在本次评审中经 **10 轮逐项决策**修订。最重的三处结构性问题（流式路由重复导致的两个真实缺陷、
QA 页面形态选错带来的三项代价、上下文预算双计）均已修正。
外部评审（Codex）另有 6 条为假阳性（其 shell executor 故障，只能读提示词骨架，无法核实仓库），已逐条排除。

**残余风险 1 条**：流式中断的服务端语义未定义（TODO T12）。


## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | `codex exec`（由 /plan-eng-review 自动发起） | Independent 2nd opinion | 1 | completed | 18 条：12 条经核实为真、6 条假阳性 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_found | 13 项发现，**全部已处置** |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**OUTSIDE COVERAGE:** provider=codex · phase=plan-review · **completed**（`codex exec` EXIT=0，32,333 tokens，只读沙箱）。
Codex 自述其 shell executor 故障（`CreateProcess helper_unknown_error`），**无法读取仓库**，结论全部由提示词文本推导——
因此其中 6 条（会话标题 XSS、`done` 事件缺 `effective_filters`、T1 需改 `context.py`、`build_history` 无预算来源、
`relax()` 产生 Q,A,A、`filter_by_score` 需跳过）经逐条核实为**假阳性**，已排除。

**CROSS-MODEL:** 两端一致的三条——① 流式路由复制检索链是架构缺陷；② 多轮上下文预算无明确上界；
③ `_prepare_qa_context` 的抽取应前置而非放在最后一个任务。分歧一条：Codex 建议流式渲染改用纯文本
（**已采纳**，见 D15）；它另建议把模型降级修正拆成独立交付单元**先交付**（未采纳，用户选择并入本轮）。

**VERDICT:** ENG REVIEWED — 13 项发现全部处置完成，无未决项。两份计划（后端 15 Task / 前端 5 Task）可进入实施。

NO UNRESOLVED DECISIONS
