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
    scores, labels = classify_clause("混凝土结构施工", [], SAMPLE_RULES)
    assert "dim1" in scores
    assert "dim2" in scores
    assert "dim3" in scores
    assert "dim4" in scores
    assert "dim5" in scores
    assert "dim6" in scores


def test_classify_clause_keyword_match():
    scores, labels = classify_clause("屋面防水施工应满足设计要求", [], SAMPLE_RULES)
    assert scores["dim5"] > 0  # 屋面匹配工程部位
    assert labels.get("dim5") == "屋面"


def test_classify_clause_material_match():
    scores, labels = classify_clause("混凝土强度等级不应低于C30", [], SAMPLE_RULES)
    assert scores["dim6"] > 0  # 混凝土匹配材料维度
    assert scores["dim4"] > 0  # 混凝土也匹配专业维度


def test_classify_clause_no_match():
    scores, labels = classify_clause("某某某无意义文本", [], SAMPLE_RULES)
    assert all(v == 0.0 for v in scores.values())
    assert labels == {}


def test_should_use_ai_returns_true_for_low_score():
    scores = {"dim4": 0.3, "dim5": 0.4, "dim6": 0.1}
    assert should_use_ai("dim5", scores, {"dim5": 0.7}) is True


def test_should_use_ai_returns_false_for_high_score():
    scores = {"dim4": 0.9, "dim5": 0.8, "dim6": 0.1}
    assert should_use_ai("dim5", scores, {"dim5": 0.7}) is False


def test_classify_clause_parent_path_boost():
    """标签继承：父路径包含关键词时提高得分"""
    scores, labels = classify_clause(
        "模板安装应符合要求",
        ["混凝土分项工程", "模板"],
        SAMPLE_RULES,
    )
    assert scores["dim6"] > 0 or scores["dim4"] > 0
