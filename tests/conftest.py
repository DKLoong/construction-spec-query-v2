import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _mock_search_rerank(monkeypatch):
    """检索页精排默认 mock 为原序（避免测试环境真实加载 CE/embedding 模型）。

    精排接入 hybrid_search 后，默认把所有检索走成「原序」——现有测试断言
    的 RRF/SQL 顺序不变；精排集成测试通过显式 monkeypatch 覆盖此设置。
    """
    import app.search.hybrid_search as hs
    monkeypatch.setattr(
        hs, "rerank_candidates",
        lambda question, candidates: ([(d, 1.0) for d in candidates], "none"),
    )


@pytest.fixture
def test_dir(tmp_path):
    """提供临时目录作为测试用的 data 根目录"""
    return tmp_path


@pytest.fixture
def app():
    from app.main import app
    return app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def auth_client(client, monkeypatch, tmp_path):
    """已认证的 TestClient"""
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test_auth.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    pwd_hash = hash_password("test123")
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", pwd_hash))
    resp = client.post("/login", data={"username": "admin", "password": "test123"},
                       follow_redirects=False)
    # Set cookie on client for subsequent requests
    if "set-cookie" in resp.headers:
        client.cookies.set("access_token", resp.cookies.get("access_token"))
    return client


def setup_search_data(conn):
    """写入 3 条测试条文（共享 helper，供搜索相关测试使用）"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。",
         "结构专业", "主体结构", "模板工程"),
        ("5.2.1", "钢筋原材料", "钢筋进场时应抽取试件作屈服强度检验。",
         "结构专业", "主体结构", "金属材料,钢筋"),
        ("6.1.1", "屋面防水", "屋面防水层应采用卷材或涂膜防水。",
         "建筑专业", "屋面", "防水材料"),
    ]
    for no, title, content, dim4, dim5, dim6 in clauses_data:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content,
               dim4_specialty, dim5_location, dim6_material)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6),
        )
