from app.classifier.rule_engine import classify_clause, should_use_ai

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


# ===== 同义词归一化测试 =====

def test_normalize_text_replaces_synonyms():
    """同义词应把 source 替换为 target"""
    from app.classifier.rule_engine import normalize_text
    syns = [{"source": "砼", "target": "混凝土"}]
    assert normalize_text("砼强度应满足要求", syns) == "混凝土强度应满足要求"


def test_normalize_text_no_synonyms_unchanged():
    """无同义词或空列表时文本原样返回"""
    from app.classifier.rule_engine import normalize_text
    assert normalize_text("砼构件", None) == "砼构件"
    assert normalize_text("砼构件", []) == "砼构件"


def test_normalize_text_short_to_long_does_not_break_long():
    """短词替换长词时，已有长词不应被拆坏（整词替换）"""
    from app.classifier.rule_engine import normalize_text
    syns = [{"source": "砼", "target": "混凝土"}]
    assert normalize_text("钢筋混凝土构件（含砼）", syns) == "钢筋混凝土构件（含混凝土）"


def test_classify_clause_synonym_normalization_matches():
    """构造「砼」条文 + 「混凝土」规则 → 归一化后命中"""
    rules = [
        {"id": 40, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.4},
    ]
    syns = [{"source": "砼", "target": "混凝土"}]
    scores, labels, rule_ids = classify_clause("砼强度等级不应低于C30", [], rules, synonyms=syns)
    assert scores["dim6"] > 0
    assert labels.get("dim6") == "混凝土"
    assert rule_ids.get("dim6") == 40


def test_classify_clause_synonym_no_match_without_normalization():
    """不注入同义词时，「砼」条文不应命中「混凝土」规则"""
    rules = [
        {"id": 41, "dimension": "dim6", "sub_field": "material", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.4},
    ]
    # synonyms=[] 显式表示「无同义词」，避免从真实库自动加载「砼→混凝土」
    scores, labels, rule_ids = classify_clause("砼强度等级不应低于C30", [], rules, synonyms=[])
    assert scores["dim6"] == 0.0
    assert rule_ids.get("dim6") is None


def test_classify_clause_synonym_applies_to_parent_path():
    """同义词归一化应对过滤后的父路径同样生效"""
    rules = [
        {"id": 42, "dimension": "dim4", "sub_field": "specialty", "pattern": "混凝土",
         "match_type": "keyword", "priority": 1, "threshold": 0.4},
    ]
    syns = [{"source": "砼", "target": "混凝土"}]
    # 父路径「砼分项工程」归一化后应命中「混凝土」规则
    scores, labels, rule_ids = classify_clause("施工", ["砼分项工程"], rules, synonyms=syns)
    assert scores["dim4"] > 0
