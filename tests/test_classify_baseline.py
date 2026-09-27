"""分类标签分布回归：改 parent_path 会让 dim 得分与标签漂移。

`rule_engine.classify_clause(clause_text, parent_path, rules)` 把祖先标题
拼进 `augmented_text` 参与规则匹配，因此解析改造会改变分类结果。本测试冻结
「对同一批固定条文 + 固定规则集，分类结果与既有基线一致」，使漂移可被发现。

**隔离要求**（历史 learning 9/10）：本仓 app/database.py 直接读模块级常量，
无环境变量入口，必须 monkeypatch DATABASE_PATH。

本测试不访问数据库：`classify_clause` 显式传 `synonyms=[]`，不触发词库加载。
"""
import pytest

from app.classifier.rule_engine import classify_clause
from app.parser.md_parser import parse_markdown

# ⚠ 阈值必须 ≤ 0.45（控制器派单前预跑实测）：`_match_score` 的 keyword 分支是
#   `min(1.0, 0.3 + count * 0.15) * (1 + 0.1 * priority)` —— priority=0 时**单次命中 = 0.45**。
#   初稿写的 `threshold: 0.6` 会让**任何**规则都不命中（0.45 < 0.6），
#   于是 `_labels(MD)` 返回空标签、断言①③必然失败（实测：`{'6.1.1': '', '6.2.1': ''}`）。
#   取 0.4：单次命中 0.45 过线；父路径为空时 count=0 → 0.0 < 0.4 → 不命中（断言②成立）。
RULES = [
    {"id": 1, "dimension": "dim4", "pattern": "钢筋", "match_type": "keyword",
     "priority": 0, "threshold": 0.4, "is_active": 1},
    {"id": 2, "dimension": "dim4", "pattern": "模板", "match_type": "keyword",
     "priority": 0, "threshold": 0.4, "is_active": 1},
]

MD = """## 6 混凝土分项工程

### 6.1 模板

#### 6.1.1 一般规定

支架应根据工程结构形式进行设计。

### 6.2 钢筋

#### 6.2.1 原材料

进场时应抽取试件作屈服强度检验。
"""

# ⚠ 工程评审 C8 修正：上述正文**刻意不含**「模板」「钢筋」。
# 初稿的正文含「模板及其支架…」「钢筋进场时…」，于是 dim4 命中来自**正文**，
# 该用例在改造前就已通过——它冻结的是一个与本次改动无关的玩具分布，
# 真实漂移（来自 parent_path 变化）根本不会被发现。
# 现在命中只可能来自祖先标题，耦合才真正被隔离。


def _labels(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in parse_markdown(md):
        _scores, best, _ids = classify_clause(c["content"], c.get("parent_path", []),
                                              RULES, synonyms=[])
        out[c["clause_no"]] = best.get("dim4", "")
    return out


def test_classification_inputs_include_ancestor_titles():
    """固定事实：祖先标题**确实**进入分类匹配文本（这是耦合的机制依据）。

    正文既不含「模板」也不含「钢筋」，标签只能来自 `parent_path`——
    若 `parent_path` 的构成变了（如含自身、或丢了祖先），本用例必须失败。
    """
    labels = _labels(MD)
    assert labels["6.1.1"] == "模板"
    assert labels["6.2.1"] == "钢筋"


def test_label_disappears_when_ancestor_chain_is_broken():
    """反向断言：故意传空 `parent_path` 时标签必须消失。

    这条**证明**上面的命中确实来自祖先标题，而不是正文的偶然包含——
    没有它，`test_classification_inputs_include_ancestor_titles` 无法自证隔离。
    """
    md_clauses = parse_markdown(MD)
    c = next(x for x in md_clauses if x["clause_no"] == "6.1.1")
    _scores, best, _ids = classify_clause(c["content"], [], RULES, synonyms=[])
    assert best.get("dim4", "") != "模板"


def test_classification_labels_stable_against_baseline():
    """基线：与改造后实测一致。**改造若不改变本用例，说明层级未影响分类**；
    若改变，必须在此显式更新并说明理由（不得静默改）。"""
    assert _labels(MD) == {
        "6.1.1": "模板",
        "6.2.1": "钢筋",
    }
