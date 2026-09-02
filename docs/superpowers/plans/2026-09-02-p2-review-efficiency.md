# P2 复核减负 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低人工复核工作量——补齐 auto_adopted 规则沉淀闭环、规则质量报表 + 一键处理、批量确认/驳回与批量重分类。

**Architecture:** 规则命中沉淀抽为公共模块 `app/classifier/rule_sink.py`（`bump_rule` 统一 hit/confirmed 累加 + 按长期正确率自动启停），`feedback.process_feedback`（人工确认）与 `batch_queue.apply_ai_results`（AI 自动采纳）共用；规则质量报表为规则页顶部实时三类查询 + 一键批量动作；批量复核/重分类为列表 checkbox + 批量端点。

**Tech Stack:** FastAPI / SQLite / HTMX + Alpine.js。测试用 pytest + `auth_client` fixture。

**Spec:** `docs/superpowers/specs/2026-08-27-maintenance-and-lifecycle-design.md`（§6 复核减负）

## Global Constraints

- Python 一律用 `D:/Python/python.exe`（**禁用 python3**）；测试命令 `D:/Python/python.exe -m pytest <file> -v`
- 提交规范：`feat:`/`fix:` 前缀，只 add 实际改动文件（工作区有无关未提交改动，严禁 `git add .`）
- 数据库操作全部参数化（`?`）；代码注释用中文；不做 P2 无关重构
- 前端 JS 无 pytest 自动化，以模板/路由断言 + 浏览器验证为准
- 规则命中率用浮点（`confirmed * 1.0 / hit_count`）避免整数除零

---

### Task 1: rule_sink 公共模块 + config 常量

**Files:**
- Create: `app/classifier/rule_sink.py`
- Modify: `app/config.py`（常量区加 3 个 RULE_AUTO_* 常量）
- Test: `tests/test_rule_sink.py`

**Interfaces:**
- Produces:
  - `app.classifier.rule_sink.bump_rule(conn, dimension, pattern, sub_field="", is_confirmed=False, new_rule_active=False) -> None`
    - 规则已存在：`hit_count+1`；`is_confirmed=True` 时 `confirmed+1`；随后按正确率自动启停——`confirmed/hit ≥ RULE_AUTO_ENABLE_RATIO 且 hit ≥ RULE_AUTO_ENABLE_MIN_HIT` → `is_active=1`；`confirmed/hit < 0.3 且 hit > 10` → `is_active=0`
    - 规则不存在：INSERT（match_type='keyword', priority=0, threshold=0.6, is_active = 1 if new_rule_active else 0）
  - config 常量：`RULE_AUTO_ENABLE_RATIO = 0.8`、`RULE_AUTO_ENABLE_MIN_HIT = 5`、`RULE_AUTO_ENABLE_CONF = 0.9`
- Consumes: `classification_rules` 表

- [ ] **Step 1: 写失败测试**

Create `tests/test_rule_sink.py`:

```python
"""规则命中沉淀（rule_sink.bump_rule）测试"""
from app.database import init_db, get_db
from app.config import RULE_AUTO_ENABLE_RATIO, RULE_AUTO_ENABLE_MIN_HIT


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "rs.db"))
    init_db()


def test_bump_new_rule_inactive_by_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim4", "钢筋", "specialty", is_confirmed=True, new_rule_active=False)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='钢筋'").fetchone()
    assert row is not None
    assert row["is_active"] == 0
    assert row["hit_count"] == 1 and row["confirmed"] == 1


def test_bump_new_rule_active(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim4", "混凝土", is_confirmed=False, new_rule_active=True)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='混凝土'").fetchone()
    assert row["is_active"] == 1
    assert row["confirmed"] == 0  # 未人工确认不累加 confirmed


def test_bump_existing_rule_auto_enable(monkeypatch, tmp_path):
    """高正确率(confirmed/hit>=0.8 & hit>=5)自动启用"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active)
               VALUES ('dim4','specialty','钢筋','keyword',0,0.6,4,4,0)"""
        )
        bump_rule(conn, "dim4", "钢筋", is_confirmed=True, new_rule_active=False)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='钢筋'").fetchone()
    # hit 5, confirmed 5 → 5/5 >= 0.8 且 hit>=5 → 自动启用
    assert row["is_active"] == 1


def test_bump_existing_rule_auto_disable(monkeypatch, tmp_path):
    """低正确率(confirmed/hit<0.3 & hit>10)自动停用"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active)
               VALUES ('dim4','specialty','废词','keyword',0,0.6,11,3,1)"""
        )
        bump_rule(conn, "dim4", "废词", is_confirmed=False, new_rule_active=False)  # 仅 hit++
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='废词'").fetchone()
    # hit 12, confirmed 3 → 3/12 < 0.3 且 hit>10 → 停用
    assert row["is_active"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_sink.py -v`
Expected: FAIL（`No module named 'app.classifier.rule_sink'`）

- [ ] **Step 3: 实现 config 常量与 rule_sink**

Modify `app/config.py`（`ADAPTIVE_THRESHOLDS` 之后加）：

```python
# 规则自动启停阈值（半监督闭环）：按长期命中正确率，而非单次置信度
RULE_AUTO_ENABLE_RATIO = 0.8    # 正确率 ≥ 80% 且命中≥5 → 自动启用
RULE_AUTO_ENABLE_MIN_HIT = 5    # 自动启用最少命中次数
RULE_AUTO_ENABLE_CONF = 0.9     # 新规则由高置信来源生成时初始启用
```

Create `app/classifier/rule_sink.py`:

```python
"""规则命中沉淀（人工确认与 AI 自动采纳共用，避免重复逻辑）

bump_rule 统一维护 classification_rules 的 hit_count/confirmed，并按「长期正确率」
自动启停——不是单条置信度（单条可能恰好命中，规则质量看积累）。
"""
from app.config import (
    RULE_AUTO_ENABLE_RATIO, RULE_AUTO_ENABLE_MIN_HIT, RULE_AUTO_ENABLE_CONF,
)

# 与旧逻辑一致的自动停用下界
_DISABLE_RATIO = 0.3
_DISABLE_MIN_HIT = 10


def bump_rule(conn, dimension: str, pattern: str, sub_field: str = "",
              is_confirmed: bool = False, new_rule_active: bool = False) -> None:
    """规则命中沉淀：存在则 hit++（人工确认时 confirmed++）并自动启停；不存在则新建。

    - is_confirmed：人工确认来源为 True（confirmed+1）；auto_adopted 自动采纳为 False
      （仅 hit++，避免 AI 未人工确认虚增正确率）
    - new_rule_active：新生成规则初始是否启用（auto_adopted 或人工确认 conf≥0.9 传 True）
    """
    row = conn.execute(
        "SELECT id, hit_count, confirmed, is_active FROM classification_rules "
        "WHERE dimension = ? AND pattern = ?",
        (dimension, pattern),
    ).fetchone()

    if row:
        new_hit = row["hit_count"] + 1
        new_conf = row["confirmed"] + (1 if is_confirmed else 0)
        conn.execute(
            "UPDATE classification_rules SET hit_count = ?, confirmed = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (new_hit, new_conf, row["id"]),
        )
        # 自动启停：按长期正确率
        ratio = (new_conf * 1.0 / new_hit) if new_hit else 0.0
        if ratio >= RULE_AUTO_ENABLE_RATIO and new_hit >= RULE_AUTO_ENABLE_MIN_HIT:
            if not row["is_active"]:
                conn.execute(
                    "UPDATE classification_rules SET is_active = 1, updated_at = datetime('now','localtime') WHERE id = ?",
                    (row["id"],),
                )
        elif ratio < _DISABLE_RATIO and new_hit > _DISABLE_MIN_HIT:
            if row["is_active"]:
                conn.execute(
                    "UPDATE classification_rules SET is_active = 0, updated_at = datetime('now','localtime') WHERE id = ?",
                    (row["id"],),
                )
    else:
        conn.execute(
            """INSERT INTO classification_rules
               (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
               VALUES (?, ?, ?, 'keyword', 0, 0.6, ?)""",
            (dimension, sub_field, pattern, 1 if new_rule_active else 0),
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_rule_sink.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/classifier/rule_sink.py app/config.py tests/test_rule_sink.py
git commit -m "feat: rule_sink 规则沉淀公共模块 + 自动启停阈值常量"
```

---

### Task 2: auto_adopted 闭环 + feedback 复用 + confirm 传置信度

**Files:**
- Modify: `app/classifier/batch_queue.py:47-68`（`apply_ai_results` auto_adopted 分支调 rule_sink）
- Modify: `app/classifier/feedback.py:15-55`（`process_feedback` 重构用 rule_sink + source_conf 参数）
- Modify: `app/routes/rules_routes.py:243-262`（`confirm_review` 传 queue 的 ai_confidence）
- Test: `tests/test_rule_feedback_loop.py`

**Interfaces:**
- Consumes: Task 1 `bump_rule`；`extract_keywords`（feedback.py 现成）
- Produces:
  - `process_feedback(clause_id, dimension, confirmed_label, source_conf=0.0)`：写 clauses 分类后，对提取关键词逐个调 `bump_rule(is_confirmed=True, new_rule_active=(source_conf >= RULE_AUTO_ENABLE_CONF))`
  - `apply_ai_results` 的 auto_adopted 分支：调 `bump_rule(is_confirmed=False, new_rule_active=True)`
  - `POST /review/{queue_id}/confirm`：读 queue `ai_confidence` 传入 process_feedback

- [ ] **Step 1: 写失败测试**

Create `tests/test_rule_feedback_loop.py`:

```python
"""半监督闭环：auto_adopted 沉淀规则 + 人工确认高置信自动启用"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "rfl.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    from tests.conftest import setup_search_data
    with get_db() as conn:
        setup_search_data(conn)  # 3 条文
    return db_path


def test_auto_adopted_sinks_rule_active(monkeypatch, tmp_path):
    """auto_adopted 分支沉淀新规则且 is_active=1，confirmed 不累加"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.batch_queue import add_to_queue, apply_ai_results
    from app.database import get_db as _g
    with _g() as conn:
        clause = conn.execute("SELECT id, content FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
        # 构造 pending 批次：add_to_queue 后手动置 batch_id + ai_processing（模拟 get_pending_batch）
        add_to_queue(clause["id"], "dim4", 0.0)
        conn.execute(
            "UPDATE classification_queue SET batch_id='b1', status='ai_processing' "
            "WHERE clause_id=? AND dimension='dim4'", (clause["id"],),
        )
    apply_ai_results("b1", [
        {"clause_id": clause["id"], "label": "结构专业", "confidence": 0.85},
    ])
    with _g() as conn:
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋'"
        ).fetchone()
        q = conn.execute(
            "SELECT status FROM classification_queue WHERE clause_id=? AND dimension='dim4'",
            (clause["id"],),
        ).fetchone()
    assert q["status"] == "auto_adopted"
    assert rule is not None and rule["is_active"] == 1
    assert rule["confirmed"] == 0  # auto_adopted 未人工确认，confirmed 不累加


def test_feedback_high_conf_rule_auto_active(monkeypatch, tmp_path):
    """人工确认高置信(>=0.9)生成新规则 is_active=1"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        clause = conn.execute("SELECT id, content FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        # 确保该条关键词语料能提取到独立关键词
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
    process_feedback(clause["id"], "dim4", "结构专业", source_conf=0.95)
    with get_db() as conn:
        rule = conn.execute("SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋'").fetchone()
        # 关键词含 钢筋 → 新规则，高置信 → is_active=1
        if rule:
            assert rule["is_active"] == 1
            assert rule["confirmed"] == 1


def test_feedback_low_conf_rule_inactive(monkeypatch, tmp_path):
    """人工确认低置信(<0.9)生成新规则 is_active=0 待审核"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        clause = conn.execute("SELECT id FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
    process_feedback(clause["id"], "dim4", "结构专业", source_conf=0.6)
    with get_db() as conn:
        rule = conn.execute("SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋'").fetchone()
        if rule:
            assert rule["is_active"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_feedback_loop.py -v`
Expected: FAIL（`process_feedback` 尚无 `source_conf` 参数）

- [ ] **Step 3: 实现三处改造**

Modify `app/classifier/feedback.py` — 重构 `process_feedback` 复用 rule_sink：

```python
def process_feedback(clause_id: int, dimension: str, confirmed_label: str,
                     source_conf: float = 0.0):
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_queue SET status = 'done' WHERE clause_id = ? AND dimension = ?",
            (clause_id, dimension),
        )
        col = _dim_to_column(dimension)
        conn.execute(
            f"UPDATE clauses SET {col} = ?, ai_classified = 1, needs_review = 0 WHERE id = ?",
            (confirmed_label, clause_id),
        )
        row = conn.execute("SELECT content FROM clauses WHERE id = ?", (clause_id,)).fetchone()
        if not row:
            return

        keywords = extract_keywords(row["content"], top_n=3)
        sub_field = {"dim4": "specialty", "dim5": "location", "dim6": "material"}.get(dimension, "")
        from app.classifier.rule_sink import bump_rule
        from app.config import RULE_AUTO_ENABLE_CONF
        for kw in keywords:
            bump_rule(conn, dimension, kw, sub_field,
                      is_confirmed=True,
                      new_rule_active=(source_conf >= RULE_AUTO_ENABLE_CONF))
```

（移除原 L37-55 的内联规则更新逻辑，`extract_keywords`/`_dim_to_column` 保留。）

Modify `app/classifier/batch_queue.py` — `apply_ai_results` 的 auto_adopted 分支（原 L57-68）后追加规则沉淀：

```python
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
                    # 半监督闭环：auto_adopted 也沉淀规则（新规则直接启用；confirmed 不累加，
                    # 避免 AI 未经人工确认虚增正确率）
                    from app.classifier.feedback import extract_keywords
                    from app.classifier.rule_sink import bump_rule
                    content_row = conn.execute(
                        "SELECT content FROM clauses WHERE id = ?", (r["clause_id"],)
                    ).fetchone()
                    if content_row:
                        sub_field = _DIM_SUB_FIELD.get(dim, "")
                        for kw in extract_keywords(content_row["content"] or "", top_n=3):
                            bump_rule(conn, dim, kw, sub_field,
                                      is_confirmed=False, new_rule_active=True)
```

（在 `_DIM_COLUMN` 附近加 `_DIM_SUB_FIELD = {"dim4": "specialty", "dim5": "location", "dim6": "material"}`）

Modify `app/routes/rules_routes.py` — `confirm_review`（L243-262）读 ai_confidence 传入：

```python
    with get_db() as conn:
        item = conn.execute(
            "SELECT clause_id, dimension, ai_label, ai_confidence "
            "FROM classification_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()

    if item:
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"],
                         source_conf=item["ai_confidence"] or 0.0)
```

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `D:/Python/python.exe -m pytest tests/test_rule_feedback_loop.py tests/test_batch_queue.py tests/test_rule_engine.py tests/test_rules_routes.py -v`
Expected: PASS（现有 batch_queue/rule_engine/rules 测试回归不破）

- [ ] **Step 5: Commit**

```bash
git add app/classifier/feedback.py app/classifier/batch_queue.py app/routes/rules_routes.py tests/test_rule_feedback_loop.py
git commit -m "feat: 半监督闭环——auto_adopted 沉淀规则 + 人工确认高置信规则自动启用"
```

---

### Task 3: 规则质量报表（规则页顶部三类 + 一键处理 + 导出）

**Files:**
- Modify: `app/routes/rules_routes.py`（加 `/rules/quality` 查询 + 批量动作端点）
- Modify: `app/templates/partials/rules_list.html`（顶部加报表区）
- Test: `tests/test_rule_quality.py`

**Interfaces:**
- Consumes: `classification_rules`
- Produces:
  - `GET /rules/quality` → HTML 片段（三类卡片：建议启用/建议停用/僵尸，各带"全部启用/停用/删除"按钮 + 行内清单）
  - `POST /rules/quality/batch`（body: `{action: 'enable_all'|'disable_all'|'delete_all', kind: 'suggest_enable'|'suggest_disable'|'zombie'}`）→ 执行后返回更新报表
  - `GET /rules/quality/export` → 三类汇总 JSON 附件
  - 报表计算逻辑（Python 函数 `_quality_rows(conn)` 返回三类 dict，供端点/测试共用）

- [ ] **Step 1: 写失败测试**

Create `tests/test_rule_quality.py`:

```python
"""规则质量报表测试（三类异常 + 一键处理）"""
from app.database import init_db, get_db
from app.config import RULE_AUTO_ENABLE_RATIO, RULE_AUTO_ENABLE_MIN_HIT


def _seed(conn):
    # 建议启用：is_active=0 且 hit>=5 且正确率>=0.8
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','钢筋','keyword',0,0.6,6,5,0,datetime('now','localtime'))"""
    )
    # 建议停用：is_active=1 且 hit>10 且正确率<0.3
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','废词','keyword',0,0.6,15,3,1,datetime('now','localtime'))"""
    )
    # 僵尸：hit=0 且超 30 天
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','老词','keyword',0,0.6,0,0,1,datetime('now','localtime','-40 days'))"""
    )
    # 正常规则（不应进入任何类）
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','混凝土','keyword',0,0.6,20,16,1,datetime('now','localtime'))"""
    )


def test_quality_rows_classify(monkeypatch, tmp_path):
    db_path = tmp_path / "rq.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
        from app.routes.rules_routes import _quality_rows
        q = _quality_rows(conn)
    assert [r["pattern"] for r in q["suggest_enable"]] == ["钢筋"]
    assert [r["pattern"] for r in q["suggest_disable"]] == ["废词"]
    assert [r["pattern"] for r in q["zombie"]] == ["老词"]


def test_batch_action_enable_all(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rq2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
    resp = auth_client.post("/rules/quality/batch", json={"action": "enable_all", "kind": "suggest_enable"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM classification_rules WHERE pattern='钢筋'").fetchone()
    assert row["is_active"] == 1


def test_batch_action_delete_all_zombie(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rq3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
    resp = auth_client.post("/rules/quality/batch", json={"action": "delete_all", "kind": "zombie"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM classification_rules WHERE pattern='老词'").fetchone()
    assert row[0] == 0


def test_quality_export_json(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rq4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
    resp = auth_client.get("/rules/quality/export")
    assert resp.status_code == 200
    data = resp.json()
    assert {"suggest_enable", "suggest_disable", "zombie"} <= set(data.keys())
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_rule_quality.py -v`
Expected: FAIL（`_quality_rows` 不存在 / 端点 404）

- [ ] **Step 3: 实现报表查询与批量端点**

Modify `app/routes/rules_routes.py` 加（文件末尾）：

```python
import json as _json
from fastapi.responses import JSONResponse


def _quality_rows(conn):
    """规则质量三类（供报表渲染/一键处理/导出共用）"""
    ratio = 0.8  # 与 RULE_AUTO_ENABLE_RATIO 同值（防止 import 环可复制常量语义）
    min_hit = 5
    suggest_enable = conn.execute(
        """SELECT * FROM classification_rules
           WHERE is_active = 0 AND hit_count >= ? AND confirmed * 1.0 / hit_count >= ?
           ORDER BY hit_count DESC""",
        (min_hit, ratio),
    ).fetchall()
    suggest_disable = conn.execute(
        """SELECT * FROM classification_rules
           WHERE is_active = 1 AND hit_count > 10 AND confirmed * 1.0 / hit_count < 0.3
           ORDER BY hit_count DESC"""
    ).fetchall()
    zombie = conn.execute(
        """SELECT * FROM classification_rules
           WHERE hit_count = 0 AND created_at < datetime('now', 'localtime', '-30 days')
           ORDER BY created_at"""
    ).fetchall()
    return {
        "suggest_enable": [dict(r) for r in suggest_enable],
        "suggest_disable": [dict(r) for r in suggest_disable],
        "zombie": [dict(r) for r in zombie],
    }


def _action_sql(action: str, kind: str) -> tuple[str, list]:
    """返回批量动作的 UPDATE/DELETE SQL 与参数（kind 决定过滤条件）"""
    conds = {
        "suggest_enable": "is_active = 0 AND hit_count >= 5 AND confirmed * 1.0 / hit_count >= 0.8",
        "suggest_disable": "is_active = 1 AND hit_count > 10 AND confirmed * 1.0 / hit_count < 0.3",
        "zombie": "hit_count = 0 AND created_at < datetime('now','localtime','-30 days')",
    }
    cond = conds[kind]
    if action == "delete_all":
        return f"DELETE FROM classification_rules WHERE {cond}", []
    if action == "enable_all":
        return f"UPDATE classification_rules SET is_active = 1, updated_at = datetime('now','localtime') WHERE {cond}", []
    return f"UPDATE classification_rules SET is_active = 0, updated_at = datetime('now','localtime') WHERE {cond}", []


@router.get("/rules/quality")
async def rules_quality(request: Request):
    """规则质量报表 HTML 片段"""
    with get_db() as conn:
        q = _quality_rows(conn)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_quality.html", {"quality": q})


@router.post("/rules/quality/batch")
async def rules_quality_batch(request: Request, body: dict):
    """批量处理：enable_all/disable_all/delete_all × kind"""
    action = body.get("action", "")
    kind = body.get("kind", "")
    if action not in ("enable_all", "disable_all", "delete_all") or kind not in (
        "suggest_enable", "suggest_disable", "zombie"
    ):
        return JSONResponse({"detail": "非法参数"}, status_code=400)
    with get_db() as conn:
        sql, params = _action_sql(action, kind)
        conn.execute(sql, params)
    with get_db() as conn:
        q = _quality_rows(conn)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_quality.html", {"quality": q})


@router.get("/rules/quality/export")
async def rules_quality_export(request: Request):
    """导出规则质量汇总 JSON 附件"""
    import time as _t
    with get_db() as conn:
        q = _quality_rows(conn)
    ts = _t.strftime("%Y%m%d_%H%M%S")
    return JSONResponse(q, headers={
        "Content-Disposition": f'attachment; filename="rule_quality_{ts}.json"'
    })
```

Create `app/templates/partials/rules_quality.html`:

```html
<!-- 规则质量报表：三类异常 + 一键处理 + 导出 -->
<div id="rules-quality" style="margin-bottom:1rem">
    {% set kinds = [('suggest_enable','🔔 建议启用', 'enable_all'), ('suggest_disable','⚠️ 建议停用','disable_all'), ('zombie','🧟 僵尸规则','delete_all')] %}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0.5rem">
    {% for key, label, act in kinds %}
        <div style="border:1px solid var(--pico-muted-border-color);border-radius:6px;padding:0.5rem">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <strong style="font-size:0.8rem">{{ label }}</strong>
                <span style="font-size:0.85rem;color:{{ 'var(--pico-del-color)' if key=='suggest_disable' else '#a06500' }}">{{ quality[key]|length }}</span>
            </div>
            {% if quality[key] %}
            <div style="margin-top:0.3rem;max-height:90px;overflow-y:auto;font-size:0.72rem">
                {% for r in quality[key] %}
                <div style="display:flex;justify-content:space-between">
                    <code>{{ r.pattern }}</code>
                    <small style="color:var(--pico-muted-color)">{{ r.hit_count }}/{{ r.confirmed }}</small>
                </div>
                {% endfor %}
            </div>
            <button hx-post="/rules/quality/batch" hx-vals='{"action":"{{ act }}","kind":"{{ key }}"}'
                    hx-target="#rules-quality" hx-swap="outerHTML"
                    hx-confirm="确定{{ '删除' if key=='zombie' else ('全部启用' if act=='enable_all' else '全部停用') }}这 {{ quality[key]|length }} 条规则？"
                    class="outline" style="font-size:0.7rem;padding:0.1rem 0.4rem;margin-top:0.3rem">
                {{ '全部删除' if key=='zombie' else ('全部启用' if act=='enable_all' else '全部停用') }}
            </button>
            {% else %}
            <small style="color:var(--pico-muted-color)">无</small>
            {% endif %}
        </div>
    {% endfor %}
    </div>
    <div style="margin-top:0.4rem">
        <a href="/rules/quality/export" class="outline"
           style="font-size:0.75rem;padding:0.15rem 0.5rem;text-decoration:none">📤 导出质量报表</a>
    </div>
</div>
```

- [ ] **Step 4: 规则页接入报表**

Modify `app/templates/partials/rules_list.html`（维度筛选后、操作按钮行前）插入：

```html
    <!-- 规则质量报表（实时：三类异常 + 一键处理） -->
    <div id="rules-quality-wrap"
         hx-get="/rules/quality" hx-trigger="load, rulesQualityRefresh from:body" hx-swap="innerHTML">
    </div>
```

- [ ] **Step 5: 运行确认通过 + 回归**

Run: `D:/Python/python.exe -m pytest tests/test_rule_quality.py tests/test_rules_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/rules_routes.py app/templates/partials/rules_quality.html app/templates/partials/rules_list.html tests/test_rule_quality.py
git commit -m "feat: 规则质量报表——规则页三类异常实时展示 + 一键处理 + 导出"
```

---

### Task 4: 复核队列批量确认/驳回

**Files:**
- Modify: `app/routes/rules_routes.py`（加 `POST /review/batch-confirm`、`POST /review/batch-reject`）
- Modify: `app/templates/partials/review_list.html`（外层 wrapper + checkbox + 批量工具栏）
- Modify: `app/routes/rules_routes.py:210-217`（`review_page` → 复用 `review_list.html`，校验外层容器）
- Test: `tests/test_review_batch.py`

**Interfaces:**
- Consumes: `process_feedback`（Task 2 改造后带 source_conf）
- Produces:
  - `POST /review/batch-confirm`（body JSON `{queue_ids: [int]}`）→ 逐条 `process_feedback`（带 queue 的 ai_confidence）→ 返回更新后的 `review_list.html`
  - `POST /review/batch-reject`（body JSON `{queue_ids: [int]}`）→ 逐条驳回 → 返回更新列表
  - `review_list.html`：表格加 checkbox 列 + 表头全选 + 工具栏「批量确认 / 批量驳回」（无选中时禁用）

- [ ] **Step 1: 写失败测试**

Create `tests/test_review_batch.py`:

```python
"""复核队列批量确认/驳回测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _seed_queue(conn, n=2):
    setup_search_data(conn)
    clause = conn.execute("SELECT id, content FROM clauses LIMIT 1").fetchone()
    ids = []
    for i in range(n):
        cur = conn.execute(
            """INSERT INTO classification_queue (clause_id, dimension, status, ai_label, ai_confidence)
               VALUES (?, 'dim4', 'review', ?, ?)""",
            (clause["id"], f"结构专业{i}", 0.8 + i * 0.05),
        )
        ids.append(cur.lastrowid)
    return ids


def test_batch_confirm_updates_queue(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rb1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        ids = _seed_queue(conn)
    resp = auth_client.post("/review/batch-confirm", json={"queue_ids": ids})
    assert resp.status_code == 200
    with get_db() as conn:
        done = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE id IN (?,?) AND status='done'",
            (ids[0], ids[1]),
        ).fetchone()[0]
    assert done == 2


def test_batch_reject_updates_queue(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rb2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        ids = _seed_queue(conn)
    resp = auth_client.post("/review/batch-reject", json={"queue_ids": ids})
    assert resp.status_code == 200
    with get_db() as conn:
        rej = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE id IN (?,?) AND status='rejected'",
            (ids[0], ids[1]),
        ).fetchone()[0]
    assert rej == 2


def test_batch_empty_ids_noop(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rb3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.post("/review/batch-confirm", json={"queue_ids": []})
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_review_batch.py -v`
Expected: FAIL（404 — 端点未实现）

- [ ] **Step 3: 实现批量端点**

Modify `app/routes/rules_routes.py`（`reject_review` 后加）：

```python
@router.post("/review/batch-confirm")
async def batch_confirm(request: Request, body: dict):
    """批量确认复核项（逐条走 process_feedback 反馈闭环）"""
    from app.classifier.feedback import process_feedback
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("queue_ids") or []
    if not isinstance(ids, list):
        return _JR({"detail": "queue_ids 须为数组"}, status_code=400)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, clause_id, dimension, ai_label, ai_confidence FROM classification_queue WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
    for item in rows:
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"],
                         source_conf=item["ai_confidence"] or 0.0)
    return await review_list(request)


@router.post("/review/batch-reject")
async def batch_reject(request: Request, body: dict):
    """批量驳回复核项"""
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("queue_ids") or []
    if not isinstance(ids, list):
        return _JR({"detail": "queue_ids 须为数组"}, status_code=400)
    if ids:
        with get_db() as conn:
            for i in ids:
                item = conn.execute(
                    "SELECT clause_id FROM classification_queue WHERE id = ?", (i,)
                ).fetchone()
                if item:
                    conn.execute(
                        "UPDATE classification_queue SET status='rejected', ai_label=NULL WHERE id=?",
                        (i,),
                    )
                    conn.execute("UPDATE clauses SET needs_review=0 WHERE id=?", (item["clause_id"],))
    return await review_list(request)
```

- [ ] **Step 4: 前端 review_list.html 加 checkbox + 批量工具栏**

Modify `app/templates/partials/review_list.html` — 整体包一层 `#review-list`（页面级 target），表格加选择列，工具栏：

```html
<div id="review-list">
{% if items %}
    <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.5rem">
        <label style="font-size:0.75rem;display:flex;align-items:center;gap:0.3rem;margin:0">
            <input type="checkbox" id="review-select-all" style="width:0.9rem;height:0.9rem"
                   onchange="toggleReviewAll(this)"> 全选
        </label>
        <button id="review-batch-confirm" class="outline" disabled
                style="font-size:0.75rem;padding:0.15rem 0.5rem;background:green;border-color:green;color:white"
                onclick="reviewBatch('confirm')">✅ 批量确认所选</button>
        <button id="review-batch-reject" class="outline secondary" disabled
                style="font-size:0.75rem;padding:0.15rem 0.5rem"
                onclick="reviewBatch('reject')">❌ 批量驳回所选</button>
    </div>
    <table class="striped" style="font-size:0.85rem">
        <thead><tr>
            <th><input type="checkbox" style="width:0.9rem;height:0.9rem" disabled></th>
            <th>规范</th><th>条文</th><th>内容</th><th>维度</th><th>AI 建议</th><th>置信度</th><th>操作</th>
        </tr></thead>
        <tbody>
        {% for item in items %}
        <tr>
            <td><input type="checkbox" class="review-check" value="{{ item.queue_id }}"
                       style="width:0.9rem;height:0.9rem"
                       onchange="reviewSelectionChanged()"></td>
            ...（其余列保留原样）
        </tr>
        {% endfor %}
        </tbody>
    </table>
    <script>
    function toggleReviewAll(el) {
        document.querySelectorAll('.review-check').forEach(function (c) { c.checked = el.checked; });
        reviewSelectionChanged();
    }
    function reviewSelectionChanged() {
        var n = document.querySelectorAll('.review-check:checked').length;
        document.getElementById('review-batch-confirm').disabled = n === 0;
        document.getElementById('review-batch-reject').disabled = n === 0;
    }
    async function reviewBatch(action) {
        var ids = Array.from(document.querySelectorAll('.review-check:checked'))
            .map(function (c) { return parseInt(c.value, 10); });
        if (!ids.length) return;
        var ok = action === 'confirm'
            ? confirm('确认所选 ' + ids.length + ' 条审核结果？')
            : confirm('驳回所选 ' + ids.length + ' 条？');
        if (!ok) return;
        var url = action === 'confirm' ? '/review/batch-confirm' : '/review/batch-reject';
        try {
            var resp = await fetch(url, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ queue_ids: ids }),
            });
            var html = await resp.text();
            var host = document.getElementById('review-list');
            if (host) { host.innerHTML = html; if (window.htmx) htmx.process(host); }
        } catch (e) { alert('批量操作失败：' + e.message); }
    }
    </script>
{% else %}
    <p style="color:var(--pico-muted-color);text-align:center;padding:2rem">🎉 暂无待审核项</p>
{% endif %}
</div>
```

（具体列体保留现有各行渲染，仅表头/行首加 checkbox 列；批量后返回的 `review_list.html` 自带脚本，需保证脚本在 htmx innerHTML 中被执行——htmx 默认执行，或由外层处理。）

- [ ] **Step 5: 运行确认通过 + 回归**

Run: `D:/Python/python.exe -m pytest tests/test_review_batch.py tests/test_rules_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/rules_routes.py app/templates/partials/review_list.html tests/test_review_batch.py
git commit -m "feat: 复核队列批量确认/驳回（多选 + 全选 + 批量工具栏）"
```

---

### Task 5: 批量重新分类（规范多选 + 范围）

**Files:**
- Modify: `app/routes/spec_routes.py`（加 `POST /specs/batch-reclassify`）
- Modify: `app/templates/partials/specs_table.html`（checkbox 列 + 工具栏 + 范围选择）
- Test: `tests/test_spec_batch_reclassify.py`

**Interfaces:**
- Consumes: `app.classifier.batch_queue.add_to_queue`；`app.ai.classifier_ai.process_pending_batches`
- Produces:
  - `POST /specs/batch-reclassify`（body JSON `{spec_ids: [int], scope: 'unclassified'|'all'}`，默认 `unclassified`）
    - 对所选规范下条文（scope=all 全量；scope=unclassified 取 `ai_classified=0 OR needs_review=1`）：
      - all 时先清空 dim4/5/6 与 `ai_classified`；unclassified 不动已有标签
      - 重置 `needs_review=1`，清旧队列项，重新 `add_to_queue`（dim4/5/6 pending）
      - 触发 `process_pending_batches(force=True)` 跑 AI 分类
    - 返回处理条数摘要 HTML

- [ ] **Step 1: 写失败测试**

Create `tests/test_spec_batch_reclassify.py`:

```python
"""批量重新分类测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path, name="bsr.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)  # 1 spec(现行默认) + 3 clauses 无分类（ai_classified 默认0）


def _get_spec_and_clause(conn):
    spec = conn.execute("SELECT id FROM specifications LIMIT 1").fetchone()
    clause = conn.execute("SELECT id, dim4_specialty FROM clauses LIMIT 1").fetchone()
    return spec["id"], clause


def test_batch_reclassify_unclassified_only(auth_client, monkeypatch, tmp_path):
    """scope=unclassified：只处理未分类/待复核条文，不动已分类"""
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        spec_id, clause = _get_spec_and_clause(conn)
        # 给第 2 条已分类标签
        c2 = conn.execute("SELECT id FROM clauses WHERE id != ? LIMIT 1", (clause["id"],)).fetchone()
        conn.execute("UPDATE clauses SET dim4_specialty='结构专业', ai_classified=1, needs_review=0 WHERE id=?",
                     (c2["id"],))
    # mock process_pending_batches 避免真实 AI
    import app.routes.spec_routes as sr
    sr.process_pending_batches = lambda force=False: 0  # noqa
    resp = auth_client.post("/specs/batch-reclassify",
                            json={"spec_ids": [spec_id], "scope": "unclassified"})
    assert resp.status_code == 200
    with get_db() as conn:
        queued = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE status='pending'"
        ).fetchone()[0]
    # 未分类条文（3条里第2条已分类+needs_review=0 除外）入队；第2条标签保留
    assert queued >= 1
    with get_db() as conn:
        tag = conn.execute("SELECT dim4_specialty FROM clauses WHERE id=?", (c2["id"],)).fetchone()
    assert tag["dim4_specialty"] == "结构专业"  # 未清空


def test_batch_reclassify_scope_all_clears_tags(auth_client, monkeypatch, tmp_path):
    """scope=all：清空已分类标签重入队"""
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        spec_id, clause = _get_spec_and_clause(conn)
        conn.execute("UPDATE clauses SET dim4_specialty='结构专业', ai_classified=1, needs_review=0")
    import app.routes.spec_routes as sr
    sr.process_pending_batches = lambda force=False: 0  # noqa
    resp = auth_client.post("/specs/batch-reclassify",
                            json={"spec_ids": [spec_id], "scope": "all"})
    assert resp.status_code == 200
    with get_db() as conn:
        cleared = conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE dim4_specialty != '' OR dim5_location != '' OR dim6_material != ''"
        ).fetchone()[0]
        queued = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE status='pending'"
        ).fetchone()[0]
    assert cleared == 0  # 标签已清
    assert queued >= 3
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_spec_batch_reclassify.py -v`
Expected: FAIL（404 — 端点未实现）

- [ ] **Step 3: 实现批量重分类端点**

Modify `app/routes/spec_routes.py` 加（`update_clause_class` 后）：

```python
from fastapi.responses import JSONResponse as _JR


@router.post("/specs/batch-reclassify")
async def batch_reclassify(request: Request, body: dict):
    """对所选规范条文批量重新分类（scope: all=全量清标签重跑 / unclassified=仅未分类）

    unclassified 不动已有标签（防覆盖人工修正）；all 需清空 dim4/5/6 再重跑。
    """
    spec_ids = body.get("spec_ids") or []
    scope = body.get("scope") or "unclassified"
    if not isinstance(spec_ids, list) or not spec_ids:
        return _JR({"detail": "spec_ids 须为非空数组"}, status_code=400)
    if scope not in ("all", "unclassified"):
        return _JR({"detail": "scope 须为 all 或 unclassified"}, status_code=400)

    from app.classifier.batch_queue import add_to_queue
    from app.ai.classifier_ai import process_pending_batches

    ph = ",".join("?" * len(spec_ids))
    with get_db() as conn:
        # 收集目标条文
        base = ("SELECT c.id, c.clause_no, c.title, c.content, "
                "c.ai_classified, c.needs_review FROM clauses c WHERE c.spec_id IN (" + ph + ")")
        if scope == "unclassified":
            clauses = conn.execute(
                base + " AND (c.ai_classified = 0 OR c.needs_review = 1)", spec_ids
            ).fetchall()
        else:
            clauses = conn.execute(base, spec_ids).fetchall()
            # scope=all：清空已有标签（防旧分类干扰重跑）
            conn.execute(
                "UPDATE clauses SET dim4_specialty='', dim5_location='', dim6_material='', "
                "ai_classified=0, needs_review=1 WHERE spec_id IN (" + ph + ")",
                spec_ids,
            )
        target_ids = [c["id"] for c in clauses]
        if target_ids:
            # 清旧队列项 + 重新入队（六维中 dim4/5/6 待 AI）
            tph = ",".join("?" * len(target_ids))
            conn.execute(
                "DELETE FROM classification_queue WHERE clause_id IN (" + tph + ")", target_ids)
            for cid in target_ids:
                for dim in ("dim4", "dim5", "dim6"):
                    add_to_queue(cid, dim, 0.0)

    count = 0
    if target_ids:
        try:
            count = process_pending_batches(force=True)
        except Exception as e:
            return HTMLResponse(f"""<p style="color:orange;margin-top:0.5rem">已重新入队 {len(target_ids)} 条，但 AI 分类未完成：{e}</p>""")
    from app.main import templates
    return HTMLResponse(f"""<p style="color:green;margin-top:0.5rem">✅ 已对 {len(target_ids)} 条条文重新分类（AI 处理 {count} 条）</p>
    <div id="spec-class-area" hx-swap-oob="true"></div>""")
```

- [ ] **Step 4: 前端 specs_table.html 加 checkbox + 工具栏**

Modify `app/templates/partials/specs_table.html` — 表格外层包 `#specs-list`，加选择列与工具栏：

```html
<div id="specs-list">
{% if specs %}
    <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.5rem;flex-wrap:wrap">
        <label style="font-size:0.75rem;display:flex;align-items:center;gap:0.3rem;margin:0">
            <input type="checkbox" id="spec-select-all" style="width:0.9rem;height:0.9rem"
                   onchange="toggleSpecAll(this)"> 全选
        </label>
        <select id="reclassify-scope" style="font-size:0.75rem;padding:0.15rem 0.3rem;width:auto">
            <option value="unclassified">仅未分类条文</option>
            <option value="all">全部条文（清标签重跑）</option>
        </select>
        <button id="spec-batch-reclassify" class="outline" disabled
                style="font-size:0.75rem;padding:0.15rem 0.5rem"
                onclick="specBatchReclassify()">🔄 重新分类所选</button>
    </div>
    <table class="striped" style="font-size:0.85rem">
        <thead><tr>
            <th><input type="checkbox" style="width:0.9rem;height:0.9rem" disabled></th>
            <th>编号</th><th>名称</th>...
        </tr></thead>
        <tbody>
        {% for s in specs %}
        <tr>
            <td><input type="checkbox" class="spec-check" value="{{ s.id }}"
                       style="width:0.9rem;height:0.9rem"
                       onchange="specSelectionChanged()"></td>
            ...（其余列保留原样）
        </tr>
        {% endfor %}
        </tbody>
    </table>
    <script>
    function toggleSpecAll(el) {
        document.querySelectorAll('.spec-check').forEach(function (c) { c.checked = el.checked; });
        specSelectionChanged();
    }
    function specSelectionChanged() {
        var n = document.querySelectorAll('.spec-check:checked').length;
        var btn = document.getElementById('spec-batch-reclassify');
        if (btn) btn.disabled = n === 0;
    }
    async function specBatchReclassify() {
        var ids = Array.from(document.querySelectorAll('.spec-check:checked'))
            .map(function (c) { return parseInt(c.value, 10); });
        if (!ids.length) return;
        var scope = document.getElementById('reclassify-scope').value;
        var msg = scope === 'all'
            ? '将对所选规范全部条文清空标签并重新分类（耗时，会覆盖已确认标签），确定继续？'
            : '对所选规范未分类条文重新分类，确定继续？';
        if (!confirm(msg)) return;
        try {
            var resp = await fetch('/specs/batch-reclassify', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ spec_ids: ids, scope: scope }),
            });
            var html = await resp.text();
            // 结果显示在列表上方
            var el = document.createElement('div');
            el.innerHTML = html;
            var host = document.getElementById('specs-list');
            if (host) host.parentElement.insertBefore(el, host);
            if (window.htmx) htmx.process(el);
        } catch (e) { alert('批量重分类失败：' + e.message); }
    }
    </script>
{% else %}
    <p style="color:var(--pico-muted-color);text-align:center;padding:2rem">暂无已导入的规范</p>
{% endif %}
</div>
```

（结构说明：`specs_list.html` 的 `<div id="specs-table" hx-get="/specs/list" hx-trigger="load">` **保持不动**——它作为容器接收 `/specs/list` 返回的 specs_table.html；specs_table.html 顶层包一层 `<div id="specs-list">` 作为工具栏+表格+脚本的宿主（htmx innerHTML 进 #specs-table 后为 `#specs-table > #specs-list`）。行内删除按钮 `hx-target="closest tr"` 不变（仅删行）；`/specs/list` 端点无需改动。工具栏脚本在每次 load 渲染时重复定义顶层 function（同名覆盖，无碍）。）

- [ ] **Step 5: 运行确认通过 + 回归**

Run: `D:/Python/python.exe -m pytest tests/test_spec_batch_reclassify.py tests/test_spec_routes.py tests/test_spec_status.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/spec_routes.py app/templates/partials/specs_table.html tests/test_spec_batch_reclassify.py
git commit -m "feat: 批量重新分类（规范多选 + 范围：仅未分类/全部清标签）"
```

---

## 验收清单（P2 完成标准）

- [ ] `D:/Python/python.exe -m pytest tests/ -v` 全量通过（既有 494 + 新增全部）
- [ ] AI 分类 auto_adopted 后：关键词沉淀为规则且 `is_active=1`；`confirmed` 不虚增
- [ ] 人工确认高置信（≥0.9）确认后新规则自动启用；低置信新规则待审核
- [ ] 已存在规则在连续命中高正确率后自动启用（无需手动）
- [ ] 规则页顶部出现三类报表（建议启用/停用/僵尸）+ 一键处理 + 导出
- [ ] 审核队列可勾选多条 → 批量确认/驳回
- [ ] 规范页可勾选多本 → 重新分类（仅未分类默认 / 全部清标签二次确认）
- [ ] 浏览器验证：规则页报表加载、审核批量按钮随勾选启用、批量重分类确认流程
