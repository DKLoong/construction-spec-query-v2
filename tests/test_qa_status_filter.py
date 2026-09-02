"""QA 状态过滤测试（status_filter 覆盖默认 status_allow）"""
from unittest.mock import AsyncMock, MagicMock
from app.database import get_db, init_db


class _FakeResp:
    def __init__(self, content="回答"): self.content, self.success, self.error = content, True, None


def _mock_backend(monkeypatch):
    """patch cli_client.get_backend（qa_ask 函数内 from cli_client import）+ 替换 _rerank_scored"""
    import app.ai.cli_client as cc
    import app.routes.qa_routes as qr
    fake = MagicMock()
    fake.is_available.return_value = True
    fake.ask = AsyncMock(return_value=_FakeResp())
    fake.command = "fake"
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    # rerank 不真正加载模型（替换 qa_routes 模块内全局名）
    monkeypatch.setattr(qr, "_rerank_scored", lambda q, c: ([(d, 0.9) for d in c]))
    return fake


def _setup(auth_client, monkeypatch, tmp_path):
    from tests.conftest import setup_search_data
    from app.search.tokenize import build_search_text
    db_path = tmp_path / "qsf.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
            ("GBJ 10-1989", "旧规范", "废止"),
        )
        sid = conn.execute("SELECT id FROM specifications WHERE code='GBJ 10-1989'").fetchone()["id"]
        # 废止条文带 search_text（含「钢筋」关键词，与 valid 5.2.1 同词命中），
        # 使「钢筋」能同时召回现行 + 废止条文，供过滤/放行两种语义确定性断言
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (sid, "1.0.1", "旧条文", "钢筋废止旧条文内容。",
             build_search_text("1.0.1", "旧条文", "钢筋废止旧条文内容。")),
        )


def test_qa_status_filter_only_current_excludes_obsolete(auth_client, monkeypatch, tmp_path):
    _mock_backend(monkeypatch)
    _setup(auth_client, monkeypatch, tmp_path)
    # status_filter=现行 → 废止条文不进候选 → 上下文不含旧条文 → 回答不含它
    resp = auth_client.post("/qa/ask", json={
        "question": "混凝土施工要求", "status_filter": "现行",
    })
    assert resp.status_code == 200
    # 断言 metadata 过滤生效：通过 trace 不可直接断言，改断言 sources 不含旧条文
    assert all("GBJ 10-1989" not in (s.get("code") or "") for s in resp.json().get("sources", []))


def test_qa_status_filter_empty_passes_invalid(auth_client, monkeypatch, tmp_path):
    _mock_backend(monkeypatch)
    _setup(auth_client, monkeypatch, tmp_path)
    # status_filter 显式传 ""（全不勾）→ 放行非现行，废止条文可进入 sources
    resp = auth_client.post("/qa/ask", json={
        "question": "钢筋", "status_filter": "",
    })
    assert resp.status_code == 200
    codes = [s.get("code") or "" for s in resp.json().get("sources", [])]
    assert "GBJ 10-1989" in codes, "显式空串应放行废止条文进入 sources"


def test_qa_status_filter_absent_defaults_to_current(auth_client, monkeypatch, tmp_path):
    """缺参（旧客户端不传 status_filter）→ 旧语义：settings 默认白名单过滤废止"""
    _mock_backend(monkeypatch)
    _setup(auth_client, monkeypatch, tmp_path)
    resp = auth_client.post("/qa/ask", json={"question": "钢筋"})
    assert resp.status_code == 200
    codes = [s.get("code") or "" for s in resp.json().get("sources", [])]
    assert "GB 50204" in codes, "现行规范条文应保留"
    assert "GBJ 10-1989" not in codes, "废止条文应被默认过滤"
