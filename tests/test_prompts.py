"""QA system prompt 测试（app/ai/prompts.py）"""
from app.ai.prompts import build_system_prompt, PROMPT_MODES


def test_build_system_prompt_rag():
    p = build_system_prompt("rag")
    assert "标注来源" in p
    assert "禁止编造" in p
    assert "次相关" in p
    assert "公式必须用 $...$" in p  # 公式定界符约束，避免 AI 用普通括号
    assert "黑体" in p and "查看原文" in p  # 强条判定提示（黑体才是法定强条）


def test_build_system_prompt_verbatim():
    p = build_system_prompt("verbatim")
    assert "禁止归纳" in p
    assert "摘抄" in p
    assert "标注来源" in p
    assert "保留 $...$" in p  # 摘抄保留公式定界符


def test_build_system_prompt_unknown_falls_back_to_rag():
    p = build_system_prompt("unknown_mode")
    assert p == PROMPT_MODES["rag"]
