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


@pytest.fixture(autouse=True)
def _mock_rerank_for_qa(monkeypatch, tmp_path):
    """隔离 QA 测试环境：

    1. CrossEncoder 精排 mock 返回固定高分，保证候选进入上下文、测试快速确定；
    2. 向量库指向临时目录（不依赖真实 lance_db，避免测试与真实索引耦合）。
    显式 monkeypatch 精排的单元测试会在 fixture 之后覆盖此设置。
    """
    monkeypatch.setattr(
        "app.ai.reranker.rerank",
        lambda question, texts: [0.9] * len(texts),
    )
    monkeypatch.setattr(
        "app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance")
    )


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

    # 「钢筋」可被测试库 5.2.1 的 SQL LIKE 命中（不依赖真实向量库）
    resp = auth_client.post("/qa/ask", json={"question": "钢筋"})
    data = resp.json()
    assert len(data["sources"]) > 0
    for src in data["sources"]:
        assert "code" in src
        assert "clause_no" in src
        assert "clause_id" in src  # 供前端正文出处跳详情弹窗
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


# ═══════════════════════════════════════════
# 分类筛选联动
# ═══════════════════════════════════════════

def _patch_cli_and_hybrid(monkeypatch, query_log):
    """mock CLI 可用 + 记录 hybrid_search 收到的 SearchQuery 序列"""
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true,
    )
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_success,
    )

    def fake_hybrid(query, *a, **k):
        query_log.append(query)
        return [], 0

    monkeypatch.setattr(
        "app.search.hybrid_search.hybrid_search", fake_hybrid,
    )


def test_qa_ask_passes_dim_filters(auth_client, monkeypatch, tmp_path):
    """QA 请求携带分类筛选时透传到第一次 hybrid_search"""
    db_path = tmp_path / "test_qa_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    query_log = []
    _patch_cli_and_hybrid(monkeypatch, query_log)

    resp = auth_client.post("/qa/ask", json={
        "question": "模板",
        "dim4_specialty": "结构",
    })
    assert resp.status_code == 200
    assert query_log, "应调用 hybrid_search"
    first = query_log[0]
    assert first.keyword == "模板"
    assert first.dim4_specialty == "结构"


def test_qa_ask_falls_back_wide_when_dim_filter_sparse(auth_client, monkeypatch, tmp_path):
    """分类筛选候选过少（<3）时放宽回全局检索，保证上下文充足"""
    db_path = tmp_path / "test_qa_dim_sparse.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    query_log = []
    _patch_cli_and_hybrid(monkeypatch, query_log)

    resp = auth_client.post("/qa/ask", json={
        "question": "模板",
        "dim5_location": "屋面",
    })
    assert resp.status_code == 200
    # 第一次带维度（0 条 < 3）→ 第二次放宽为无维度
    assert len(query_log) == 2, "候选不足时应放宽为全局检索"
    assert query_log[0].dim5_location == "屋面"
    assert query_log[1].dim5_location is None


def test_qa_ask_no_dim_no_wide_fallback(auth_client, monkeypatch, tmp_path):
    """未携带分类筛选时不触发放宽分支（仅一次检索）"""
    db_path = tmp_path / "test_qa_no_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    query_log = []
    _patch_cli_and_hybrid(monkeypatch, query_log)

    resp = auth_client.post("/qa/ask", json={"question": "模板"})
    assert resp.status_code == 200
    assert len(query_log) == 1


# ═══════════════════════════════════════════
# 非条文默认隐藏 + 关键词自动放行
# ═══════════════════════════════════════════

def test_qa_ask_default_hides_non_clause(auth_client, monkeypatch, tmp_path):
    """QA 默认隐藏非条文：不含 前言/条文说明 关键词时 include_non_clause=False"""
    db_path = tmp_path / "test_qa_non_def.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    query_log = []
    _patch_cli_and_hybrid(monkeypatch, query_log)

    resp = auth_client.post("/qa/ask", json={"question": "模板设计要求"})
    assert resp.status_code == 200
    assert query_log, "应调用 hybrid_search"
    assert query_log[0].include_non_clause is False


def test_qa_ask_keyword_tiaowenshuoming_passes_through(auth_client, monkeypatch, tmp_path):
    """问题含「条文说明」时自动放行 include_non_clause=True"""
    db_path = tmp_path / "test_qa_non_kw.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    query_log = []
    _patch_cli_and_hybrid(monkeypatch, query_log)

    resp = auth_client.post("/qa/ask", json={"question": "条文说明中的钢筋要求"})
    assert resp.status_code == 200
    assert query_log
    assert query_log[0].include_non_clause is True


def test_qa_ask_keyword_qianyan_passes_through(auth_client, monkeypatch, tmp_path):
    """问题含「前言」时自动放行 include_non_clause=True"""
    db_path = tmp_path / "test_qa_non_qy.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    query_log = []
    _patch_cli_and_hybrid(monkeypatch, query_log)

    resp = auth_client.post("/qa/ask", json={"question": "规范的编写前言"})
    assert resp.status_code == 200
    assert query_log
    assert query_log[0].include_non_clause is True


# ═══════════════════════════════════════════
# CrossEncoder 精排降级链（_rerank_scored 单元测试）
# ═══════════════════════════════════════════

def _make_candidates(n):
    """构造 n 条候选条文"""
    return [
        {"spec_code": "GB 50204", "clause_no": str(i),
         "content": f"条文内容{i}，用于精排打分排序测试。"}
        for i in range(n)
    ]


def test_rerank_scored_crossencoder_desc(monkeypatch):
    """CrossEncoder 可用时返回 (候选, 分数) 按分数降序全量"""
    from app.routes import qa_routes

    candidates = _make_candidates(8)
    scores = [0.1, 0.9, 0.3, 0.8, 0.2, 0.7, 0.4, 0.5]

    def fake_rerank(question, texts):
        assert question == "模板设计要求"
        assert len(texts) == 8
        return scores

    monkeypatch.setattr("app.ai.reranker.rerank", fake_rerank)

    ranked = qa_routes._rerank_scored("模板设计要求", candidates)

    assert qa_routes._last_rerank_used == "crossencoder"
    # 全量返回、按分数降序（clause_no 为 str(i) 从 0 起；条数截断在编排层 dynamic_select）
    assert [c["clause_no"] for c, _ in ranked] == ["1", "3", "5", "7", "6", "2", "4", "0"]
    assert [s for _, s in ranked] == sorted(scores, reverse=True)


def test_rerank_scored_falls_back_to_vector(monkeypatch):
    """CrossEncoder 不可用（返回 None）时回退 bi-encoder 向量"""
    from app.routes import qa_routes

    candidates = _make_candidates(3)
    monkeypatch.setattr("app.ai.reranker.rerank", lambda q, t: None)
    # mock embed_texts：q=[1,1]，条文 i 向量=[i+1,1] → dot=[2,3,4] → 降序为 2,1,0
    monkeypatch.setattr(
        "app.ai.embedding.embed_texts",
        lambda texts: [[float(i + 1), 1.0] for i in range(len(texts))],
    )

    ranked = qa_routes._rerank_scored("模板设计要求", candidates)

    assert qa_routes._last_rerank_used == "vector"
    assert [c["clause_no"] for c, _ in ranked] == ["2", "1", "0"]


def test_rerank_scored_short_circuit_single(monkeypatch):
    """候选 ≤1 时直接返回，不打分、不分层"""
    from app.routes import qa_routes

    candidates = _make_candidates(1)
    called = {"rerank": False}

    def fake_rerank(q, t):
        called["rerank"] = True
        return [1.0] * len(t)

    monkeypatch.setattr("app.ai.reranker.rerank", fake_rerank)

    ranked = qa_routes._rerank_scored("模板设计要求", candidates)

    assert ranked == [(candidates[0], 1.0)]
    assert not called["rerank"]
    assert qa_routes._last_rerank_used == "none"


def test_rerank_scored_exception_falls_back_to_vector(monkeypatch):
    """CrossEncoder 精排抛异常时回退 bi-encoder 向量"""
    from app.routes import qa_routes

    candidates = _make_candidates(3)
    monkeypatch.setattr(
        "app.ai.reranker.rerank",
        lambda q, t: (_ for _ in ()).throw(RuntimeError("精排异常")),
    )
    monkeypatch.setattr(
        "app.ai.embedding.embed_texts",
        lambda texts: [[1.0, 0.0] for _ in texts],
    )

    ranked = qa_routes._rerank_scored("模板设计要求", candidates)

    assert qa_routes._last_rerank_used == "vector"
    assert len(ranked) == 3


# ═══════════════════════════════════════════
# QA 优化新增：原文摘抄模式 / 元数据过滤开关 / 埋点
# ═══════════════════════════════════════════

def test_qa_mode_verbatim_passes_system_prompt(auth_client, monkeypatch, tmp_path):
    """mode=verbatim 时后端收到摘抄 system prompt（指令区含禁止归纳）"""
    db_path = tmp_path / "test_qa_verbatim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr("app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true)
    captured = {}

    def _mock_cli_capture(self, prompt, context="", work_dir=None, timeout=60):
        from app.ai.cli_client import CLIResponse
        captured["prompt"] = prompt
        return CLIResponse(success=True, content="原文摘抄", duration_ms=100)

    monkeypatch.setattr("app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_capture)

    resp = auth_client.post("/qa/ask", json={"question": "钢筋", "mode": "verbatim"})
    assert resp.status_code == 200
    assert "[系统指令]" in captured["prompt"]
    assert "禁止归纳" in captured["prompt"]


def test_qa_include_invalid_controls_meta_filter(auth_client, monkeypatch, tmp_path):
    """include_invalid 控制元数据过滤：默认过滤废止，True 时保留"""
    db_path = tmp_path / "test_qa_invalid.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES ('GB-OLD', '旧规范', '废止')"
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?,?,?,?)",
            (spec_id, "1.0.1", "", "钢筋旧规范内容"),
        )

    monkeypatch.setattr("app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true)
    monkeypatch.setattr("app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_success)

    # 默认（include_invalid=False）：废止条文被元数据过滤 → sources 空
    resp = auth_client.post("/qa/ask", json={"question": "钢筋"})
    assert resp.json()["sources"] == []

    # include_invalid=True：跳过过滤 → 废止条文进入上下文 → sources 非空
    resp2 = auth_client.post("/qa/ask", json={"question": "钢筋", "include_invalid": True})
    assert len(resp2.json()["sources"]) > 0


def test_qa_trace_log_emitted(auth_client, monkeypatch, tmp_path, caplog):
    """QA 请求落埋点日志（[QA_TRACE] 字段齐全）"""
    db_path = tmp_path / "test_qa_trace.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_qa_data(conn)

    monkeypatch.setattr("app.ai.cli_client.ClaudeCodeCLI.is_available", _mock_is_available_true)
    monkeypatch.setattr("app.ai.cli_client.ClaudeCodeCLI._run_cli", _mock_cli_success)

    with caplog.at_level("INFO", logger="app.routes.qa_routes"):
        auth_client.post("/qa/ask", json={"question": "钢筋"})

    trace_lines = [r.message for r in caplog.records if "[QA_TRACE]" in r.message]
    assert len(trace_lines) == 1
    # 「钢筋」仅命中 1 条候选 → _rerank_scored 候选 ≤1 短路，rerank_used="none"
    assert '"rerank_used": "none"' in trace_lines[0]
    assert '"high_count"' in trace_lines[0]
    assert '"context_tokens"' in trace_lines[0]
