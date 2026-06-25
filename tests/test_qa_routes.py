"""QA 路由 HTTP 集成测试"""
import pytest


# ═══════════════════════════════════════════
# 测试数据辅助函数
# ═══════════════════════════════════════════

def _setup_qa_data(conn):
    """写入测试用的规范与条文数据"""
    conn.execute(
        "INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')"
    )
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计",
         "模板及其支架应根据工程结构形式进行设计。模板应能可靠承受混凝土侧压力。"),
        ("5.2.1", "钢筋原材料",
         "钢筋进场时应抽取试件作屈服强度、抗拉强度、伸长率检验。"),
    ]
    for no, title, content in clauses_data:
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, no, title, content),
        )


# ═══════════════════════════════════════════
# Mock 辅助函数
# ═══════════════════════════════════════════

def _mock_cli_success(self, prompt, context="", work_dir=None, timeout=60):
    """模拟 CLI 成功返回"""
    from app.ai.cli_client import CLIResponse
    return CLIResponse(
        success=True,
        content="根据规范，模板应能承受混凝土侧压力。",
        duration_ms=100,
    )


def _mock_cli_error(self, prompt, context="", work_dir=None, timeout=60):
    """模拟 CLI 返回错误"""
    from app.ai.cli_client import CLIResponse
    return CLIResponse(
        success=False, content="", error="CLI 调用超时", duration_ms=60000,
    )


def _mock_is_available_true(self):
    return True


def _mock_is_available_false(self):
    return False


# ═══════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════

def test_qa_ask_requires_auth(client):
    """未登录不能访问 QA 接口"""
    resp = client.post("/qa/ask", json={"question": "测试"}, follow_redirects=False)
    assert resp.status_code == 302


def test_qa_ask_empty_question(auth_client):
    """空问题返回 400"""
    resp = auth_client.post("/qa/ask", json={"question": ""})
    assert resp.status_code == 400
    assert "不能为空" in resp.json()["detail"]


def test_qa_ask_returns_json(auth_client, monkeypatch, tmp_path):
    """成功问答返回 JSON 格式 QAResponse"""
    db_path = tmp_path / "test_qa.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true,
    )
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_success,
    )

    resp = auth_client.post("/qa/ask", json={"question": "模板设计要求"})
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "sources" in data
    assert len(data["answer"]) > 0
    assert data["cli_used"] == "claude"


def test_qa_ask_with_sources(auth_client, monkeypatch, tmp_path):
    """验证 sources 字段格式正确"""
    db_path = tmp_path / "test_qa_src.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true,
    )
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_success,
    )

    resp = auth_client.post("/qa/ask", json={"question": "钢筋检验"})
    data = resp.json()
    assert len(data["sources"]) > 0
    for src in data["sources"]:
        assert "code" in src
        assert "clause_no" in src
        assert isinstance(src["code"], str)
        assert isinstance(src["clause_no"], str)


def test_qa_ask_no_results(auth_client, monkeypatch, tmp_path):
    """搜索无结果时仍能调用 CLI 并返回空 sources"""
    db_path = tmp_path / "test_qa_none.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true,
    )
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_success,
    )

    resp = auth_client.post("/qa/ask", json={"question": "不存在的关键词xyz"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources"] == []
    assert len(data["answer"]) > 0  # CLI 仍返回答案


def test_qa_ask_cli_unavailable(auth_client, monkeypatch, tmp_path):
    """CLI 不可用时返回 503"""
    db_path = tmp_path / "test_qa_ua.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_false,
    )

    resp = auth_client.post("/qa/ask", json={"question": "测试"})
    assert resp.status_code == 503
    assert "不可用" in resp.json()["detail"]


def test_qa_ask_cli_error(auth_client, monkeypatch, tmp_path):
    """CLI 返回错误时给出降级回答"""
    db_path = tmp_path / "test_qa_err.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true,
    )
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_error,
    )

    resp = auth_client.post("/qa/ask", json={"question": "测试"})
    assert resp.status_code == 200
    data = resp.json()
    assert "抱歉" in data["answer"] or "错误" in data["answer"]
    assert data["sources"] is not None


def test_qa_ask_backend_selection(auth_client, monkeypatch, tmp_path):
    """选择 codex 后端并使用正确命令"""
    db_path = tmp_path / "test_qa_codex.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr(
        "app.ai.cli_client.CodexCLI.is_available", _mock_is_available_true,
    )
    monkeypatch.setattr(
        "app.ai.cli_client.CodexCLI._run_cli", _mock_cli_success,
    )

    resp = auth_client.post(
        "/qa/ask", json={"question": "测试", "backend": "codex"},
    )
    assert resp.status_code == 200
    assert resp.json()["cli_used"] == "codex"
