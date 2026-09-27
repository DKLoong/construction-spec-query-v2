"""分类标签分布回归：改 parent_path 会让 dim 得分与标签漂移。

`rule_engine.classify_clause(clause_text, parent_path, rules)` 把祖先标题
拼进 `augmented_text` 参与规则匹配，因此解析改造会改变分类结果。本测试冻结
「对同一批固定条文 + 固定规则集，分类结果与既有基线一致」，使漂移可被发现。

**数据库隔离**（历史 learning 9/10）：本仓 app/database.py 直接读模块级常量、
无环境变量入口，历史上须 monkeypatch DATABASE_PATH；**本测试不需要**——三条断言
全程显式传 `synonyms=[]`，`classify_clause` 因此不进入 `load_equivalent_groups()`，
既不读词库也不碰数据库。

**口径边界（窄口径，须知）**：本基线只冻结 dim4 的两条 keyword 规则 + 下方固定样例。
**不覆盖**：`regex` / `exact` 分支、非空 `label` 规则的赋值路径、`_PARENT_NOISE_TITLES`
噪声过滤、以及 `synonyms` 词库归一化对匹配文本的影响——改动这些路径时本文件不会报警。
"""
import pytest

from app.classifier.rule_engine import classify_clause
from app.parser.md_parser import parse_markdown

# ⚠ 阈值必须 **< 0.45**——注意**不能取 0.45**（曾写作 `≤ 0.45`，照做必坏）：
#   `_match_score` 的 keyword 分支是
#   `min(1.0, 0.3 + count * 0.15) * (1 + 0.1 * priority)`，priority=0 时**单次命中**的
#   返回值 repr 为 `0.44999999999999996`（浮点表示），故 `0.44999999999999996 >= 0.45`
#   为 **False**、而 `>= 0.4` 为 True。取 0.4：单次命中过线；
#   父路径为空时 count=0 → 0.0 < 0.4 → 不命中（断言②成立）。
#   初稿写的 `threshold: 0.6` 同样会让**任何**规则都不命中（0.45 < 0.6），
#   于是 `_labels(MD)` 返回空标签、断言①③必然失败
#   （实测：`{'6.1.1': '', '6.2.1': '', '7.1.1': ''}` —— ⚠ M19，fix round 1 补了 `7.1.1`
#   样例后本字典应为 **3 键**，原先记的 2 键已过期。失败事实不变、只是键数过期。
#   这正是 R-T10-8 的实例：**一次「补样例」的 fix 会让同一文件里多处计数同时过期**，
#   而上轮只更新了其中一处（`_labels` 的期望字典改了，这行注释没改）。改本文件里任何
#   计数值前，**先 grep 全文件**看还有几处写着同一个数。）
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

## 7 支撑体系

### 7.1 构造要求

#### 7.1.1 模板支架

应按本规范的规定执行。
"""

# ⚠ 工程评审 C8 修正：上述正文**刻意不含**「模板」「钢筋」。
# 初稿的正文含「模板及其支架…」「钢筋进场时…」，于是 dim4 命中来自**正文**，
# 该用例在改造前就已通过——它冻结的是一个与本次改动无关的玩具分布，
# 真实漂移（来自 parent_path 变化）根本不会被发现。
# 现在命中只可能来自祖先标题，耦合才真正被隔离。
#
# ⚠ fix round 1（复核 Finding 1 实测证伪后补强）：**仅靠 6.1.1 / 6.2.1 两组样例，
#   测不出「parent_path 含自身」这一漂移**——6.1.1 的自身标题「一般规定」在
#   `_PARENT_NOISE_TITLES`（rule_engine.py:7-10）里被过滤；6.2.1 的自身标题「原材料」
#   不含任何 pattern，加不加自身对 `augmented_text` 都无影响（实测两组样例在
#   「正确 parent」与「parent + [自身标题]」两种模式下的标签完全相同）。
#   而「含自身」正是本批**真实出现过**的形态：main 分支 md_parser.py:267 曾为
#   `parent_path = [t[1] for t in title_stack]`（含自身），同文件 230/254 行却是
#   `title_stack[:-1]`，直到 6a8e9b5 才统一为 `stack[:-1]`。
#   故补第三组 7.1.1：**自身标题含要求词「模板」，而祖先（支撑体系/构造要求）与正文都不含**。
#   实测：正确 parent → label `''`；parent 含自身 → label `'模板'` ⇒ 可区分。
#   样例规格实测（曾误记为「8 行」）：21 个内容行 / 非空 11 行 / 解析出 3 条条文
#   （`MD.split("\n")` 因结尾换行返回 22 项，末项为空串）。


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

    「含自身」这半边由 `7.1.1` 承担：它的自身标题「模板支架」含要求词，
    而祖先与正文都不含 —— 故 `parent_path` 一旦含自身，`7.1.1` 的期望值 `""`
    就会被命中成 `"模板"`，本断言随即失败（另两组样例对此不敏感，见上文注释）。
    """
    labels = _labels(MD)
    assert labels["6.1.1"] == "模板"
    assert labels["6.2.1"] == "钢筋"
    assert labels["7.1.1"] == ""


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
        "7.1.1": "",
    }
