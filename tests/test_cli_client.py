"""分类 prompt 构建函数测试"""
from app.ai.cli_client import build_classify_prompt


def _sample_batch():
    return [
        {
            "clause_id": 101,
            "content": "I 级、II 级接头应能承受规定的高应力循环。<div style='text-align:center'>表3.0.6</div>",
            "spec_code": "JGJ107-2016",
            "spec_title": "钢筋机械连接技术规程",
            "clause_no": "3.0.6",
        },
        {
            "clause_id": 102,
            "content": "接头应满足强度和变形要求。",
            "spec_code": "JGJ107-2016",
            "spec_title": "钢筋机械连接技术规程",
            "clause_no": "3.0.3",
        },
    ]


def test_prompt_contains_dimension_label():
    prompt = build_classify_prompt(_sample_batch(), "dim6")
    assert "材料/工艺" in prompt


def test_prompt_contains_candidate_labels():
    prompt = build_classify_prompt(
        _sample_batch(), "dim6", ["钢筋", "混凝土", "砌体"]
    )
    assert "候选标签" in prompt
    assert "钢筋" in prompt and "混凝土" in prompt


def test_prompt_without_candidate_labels():
    """不提供候选标签时不出现候选标签段"""
    prompt = build_classify_prompt(_sample_batch(), "dim6")
    assert "候选标签" not in prompt


def test_prompt_html_cleaned():
    """条文内容中的 HTML 残留应在 prompt 中清理"""
    prompt = build_classify_prompt(_sample_batch(), "dim6")
    assert "<div" not in prompt
    assert "<div style" not in prompt
    assert "表3.0.6" in prompt or "表 3.0.6" in prompt


def test_prompt_contains_spec_context():
    """prompt 应附带规范编号/名称/条文号上下文"""
    prompt = build_classify_prompt(_sample_batch(), "dim6")
    assert "JGJ107-2016" in prompt
    assert "钢筋机械连接技术规程" in prompt
    assert "条文号 3.0.6" in prompt


def test_prompt_contains_clause_ids():
    """每条条文保留 clause_id 供结果回填"""
    prompt = build_classify_prompt(_sample_batch(), "dim6")
    assert "ID:101" in prompt
    assert "ID:102" in prompt


def test_prompt_json_instruction():
    prompt = build_classify_prompt(_sample_batch(), "dim6")
    assert "clause_id" in prompt
    assert "confidence" in prompt
