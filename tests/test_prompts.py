"""QA system prompt 测试（app/ai/prompts.py）"""
from app.ai.prompts import build_system_prompt, PROMPT_MODES


def test_build_system_prompt_rag():
    p = build_system_prompt("rag")
    assert "标注来源" in p
    assert "禁止编造" in p
    assert "次相关" in p


def test_build_system_prompt_verbatim():
    p = build_system_prompt("verbatim")
    assert "禁止归纳" in p
    assert "摘抄" in p
    assert "标注来源" in p


def test_build_system_prompt_unknown_falls_back_to_rag():
    p = build_system_prompt("unknown_mode")
    assert p == PROMPT_MODES["rag"]
