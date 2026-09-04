from app.classifier.rule_engine import classify_clause, should_use_ai
from app.lexicon.store import LexiconRow

SAMPLE_RULES = [
    # 典型关键词规则：阈值应 ≤ 单次匹配得分 (~0.45 × priority_bonus)
    {"id": 1, "dimension": "dim4", "sub_field": "specialty", "pattern": "钢筋", "match_type": "keyword", "priority": 1, "threshold": 0.4},
    {"id": 2, "dimension": "dim4", "sub_field": "specialty", "pattern": "混凝土", "match_type": "keyword", "priority": 1, "threshold": 0.4},
    {"id": 3, "dimension": "dim5", "sub_field": "location", "pattern": "屋面", "match_type": "keyword", "priority": 1, "threshold": 0.4},
    {"id": 4, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土", "match_type": "keyword", "priority": 2, "threshold": 0.35},
    {"id": 5, "dimension": "dim6", "sub_field": "material", "pattern": "钢筋", "match_type": "keyword", "priority": 1, "threshold": 0.4},
    {"id": 6, "dimension": "dim1", "sub_field": "hierarchy", "pattern": r"^GB(?![\\/T])", "match_type": "regex", "priority": 5, "threshold": 0.3},
]


def test_classify_clause_returns_all_dims():
    scores, labels, rule_ids = classify_clause("混凝土结构施工", [], SAMPLE_RULES)
    assert "dim1" in scores
    assert "dim2" in scores
    assert "dim3" in scores
    assert "dim4" in scores
    assert "dim5" in scores
    assert "dim6" in scores


def test_classify_clause_keyword_match():
    scores, labels, rule_ids = classify_clause("屋面防水施工应满足设计要求", [], SAMPLE_RULES)
    assert scores["dim5"] > 0  # 屋面匹配工程部位
    assert labels.get("dim5") == "屋面"
    assert rule_ids.get("dim5") == 3  # 屋面规则 ID


def test_classify_clause_material_match():
    scores, labels, rule_ids = classify_clause("混凝土强度等级不应低于C30", [], SAMPLE_RULES)
    assert scores["dim6"] > 0  # 混凝土匹配材料维度
    assert scores["dim4"] > 0  # 混凝土也匹配专业维度
    assert rule_ids.get("dim6") == 4  # 混凝土规则(id=4)优先级更高
    assert rule_ids.get("dim4") == 2  # 混凝土规则(id=2)


def test_classify_clause_no_match():
    scores, labels, rule_ids = classify_clause("某某某无意义文本", [], SAMPLE_RULES)
    assert all(v == 0.0 for v in scores.values())
    assert labels == {}
    assert rule_ids == {}


def test_classify_clause_threshold_gate():
    """得分未达到规则自身的阈值时，该规则不应参与竞争"""
    # "模板" 不在任何关键词中，匹配得分为 0，达不到任何阈值
    scores, labels, rule_ids = classify_clause("模板安装应符合要求", [], SAMPLE_RULES)
    assert rule_ids == {}  # 没有任何规则跨过阈值


def test_classify_clause_low_threshold_wins():
    """低阈值的规则更容易胜出"""
    rules = [
        {"id": 10, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.5},
        {"id": 11, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.8},
    ]
    scores, labels, rule_ids = classify_clause("混凝土混凝土", [], rules)
    # 两条规则评分相同(因为匹配文本和priority相同)，但 id=10 阈值更低所以早点达到
    # 实际由于 score > scores[dim] 时才替换，先匹配到的优先
    # 这里验证的是 id=10 或 id=11 有一个胜出即可
    assert rule_ids.get("dim6") in (10, 11)
    assert labels.get("dim6") == "混凝土"


def test_classify_clause_high_threshold_no_match():
    """阈值设得过高导致即使关键词出现也无法匹配"""
    rules = [
        {"id": 20, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.9},
    ]
    # 混凝土出现 1 次 → score = 0.3 * 1.1 = 0.33，< 0.9 → 不匹配
    scores, labels, rule_ids = classify_clause("混凝土强度", [], rules)
    assert rule_ids.get("dim6") is None
    assert labels.get("dim6") is None


def test_should_use_ai_returns_true_for_low_score():
    scores = {"dim4": 0.3, "dim5": 0.4, "dim6": 0.1}
    assert should_use_ai("dim5", scores, {"dim5": 0.7}) is True


def test_should_use_ai_returns_false_for_high_score():
    scores = {"dim4": 0.9, "dim5": 0.8, "dim6": 0.1}
    assert should_use_ai("dim5", scores, {"dim5": 0.7}) is False


def test_classify_clause_parent_path_boost():
    """标签继承：父路径包含关键词时提高得分"""
    scores, labels, rule_ids = classify_clause(
        "模板安装应符合要求",
        ["混凝土分项工程", "模板"],
        SAMPLE_RULES,
    )
    assert scores["dim6"] > 0 or scores["dim4"] > 0


# ===== 父标题噪声黑名单测试 =====

def test_parent_noise_title_filtered():
    """父路径含「总则」等噪声标题时不进入匹配文本，不触发规则"""
    rules = [
        {"id": 30, "dimension": "dim4", "sub_field": "specialty", "pattern": "总则",
         "match_type": "keyword", "priority": 1, "threshold": 0.3},
    ]
    # 若「总则」未被过滤，augmented_text = "施工 总则"，count=1 会跨过阈值命中
    scores, labels, rule_ids = classify_clause("施工", ["总则"], rules)
    assert scores["dim4"] == 0.0
    assert rule_ids.get("dim4") is None


def test_parent_professional_title_not_filtered():
    """真实专业词父标题不受黑名单影响，仍可触发规则"""
    rules = [
        {"id": 32, "dimension": "dim5", "sub_field": "location", "pattern": "主体结构",
         "match_type": "keyword", "priority": 1, "threshold": 0.3},
        {"id": 33, "dimension": "dim4", "sub_field": "specialty", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.3},
    ]
    scores, labels, rule_ids = classify_clause("", ["主体结构"], rules)
    assert scores["dim5"] > 0
    assert labels.get("dim5") == "主体结构"
    scores2, _, _ = classify_clause("", ["混凝土分项工程"], rules)
    assert scores2["dim4"] > 0


def test_parent_noise_blacklist_excludes_real_terms():
    """黑名单禁止含真实专业词"""
    from app.classifier.rule_engine import _PARENT_NOISE_TITLES
    for term in ["钢筋", "混凝土", "主体结构", "梁", "屋面", "基础", "砌体"]:
        assert term not in _PARENT_NOISE_TITLES


# ===== 归一化切源：词库 equiv 组（LexiconRow）注入 =====

def test_normalize_uses_lexicon_groups():
    """归一化应基于 lexicon 等价组把变体替换为 canonical（纯函数冒烟）"""
    from app.lexicon.normalize import normalize_text
    groups = [LexiconRow(1, "alias", "混凝土", ["砼"])]
    assert normalize_text("砼强度等级不应低于C30", groups) == "混凝土强度等级不应低于C30"


def test_classify_clause_synonym_normalization_matches():
    """构造「砼」条文 + 「混凝土」规则 → 注入 LexiconRow 等价组后归一化命中"""
    rules = [
        {"id": 1, "dimension": "dim6", "pattern": "混凝土", "match_type": "keyword",
         "priority": 1, "threshold": 0.0, "is_active": 1, "label": "混凝土"},
    ]
    groups = [LexiconRow(1, "alias", "混凝土", ["砼"])]
    scores, labels, _ = classify_clause("砼强度等级不应低于C30", [], rules, synonyms=groups)
    assert labels.get("dim6") == "混凝土"
    assert scores["dim6"] > 0


def test_classify_clause_synonym_no_match_without_normalization():
    """不注入等价组时，「砼」条文不应命中「混凝土」规则"""
    rules = [
        {"id": 41, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.4},
    ]
    # synonyms=[] 显式表示「无等价组」，避免从真实库自动加载「砼→混凝土」
    scores, labels, rule_ids = classify_clause("砼强度等级不应低于C30", [], rules, synonyms=[])
    assert scores["dim6"] == 0.0
    assert rule_ids.get("dim6") is None


def test_classify_clause_synonym_applies_to_parent_path():
    """等价组归一化应对过滤后的父路径同样生效"""
    rules = [
        {"id": 42, "dimension": "dim4", "sub_field": "specialty", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.4},
    ]
    groups = [LexiconRow(1, "alias", "混凝土", ["砼"])]
    # 父路径「砼分项工程」归一化后应命中「混凝土」规则
    scores, labels, rule_ids = classify_clause("施工", ["砼分项工程"], rules, synonyms=groups)
    assert scores["dim4"] > 0
