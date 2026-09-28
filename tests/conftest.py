import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _mock_search_rerank(monkeypatch):
    """检索页 CE 精排默认 mock 为原序（避免测试环境真实加载 CE/embedding 模型）。

    ce_rerank=True 的测试会显式 monkeypatch 覆盖此设置来验证精排被触发。
    """
    import app.search.hybrid_search as hs
    monkeypatch.setattr(
        hs, "rerank_candidates",
        lambda question, candidates: ([(d, 1.0) for d in candidates], "none"),
    )


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """把四处运行时路径全部指向 tmp_path。

    ⚠ **C-4：patch 的必须是「消费方所在模块」的名字**。`app.search.vector_search`
    第 4 行是 `from app.config import LANCE_DB_PATH`、`app.routes.import_routes`
    第 7 行是 `from app.config import UPLOAD_DIR, OUTPUT_DIR` —— 导入时值已绑定到
    各自模块的命名空间，patch `app.config.*` **不会**改变它们（本仓既有 20+ 处测试
    用的正是下面这套目标）。只 patch `DATABASE_PATH` 会让 pytest 往**真实**
    `lance_db` 写夹具向量、并往 `data/uploads/` 堆垃圾 md（实测累积 147 个）。

    ⚠ **C-5**：`app/config.py:16-19` 其实**有** `os.getenv` 入口
    （`DATABASE_PATH`/`LANCE_DB_PATH`/`UPLOAD_DIR`/`OUTPUT_DIR`），但只在**模块导入时**
    生效，测试期间改动已太晚 —— 所以这里仍只能用 monkeypatch，理由要写对。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_db"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "outputs"))
    for d in ("lance_db", "uploads", "outputs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


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


@pytest.fixture()
def qa_db(tmp_path, monkeypatch):
    """隔离数据库（仅建表、无用户），供不走 HTTP 的单元测试使用。

    需要它是因为 run_health_check 会写 system_logs / 快照表，
    会话持久化层测试也要真库——不隔离会污染 dev 库。
    走 patch 模块常量的方式（见测试基础设施 §1），不要用环境变量。
    """
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "qa_unit.db"))
    init_db()


def setup_search_data(conn):
    """写入 3 条测试条文（共享 helper，供搜索相关测试使用），INSERT 带 search_text"""
    from app.search.tokenize import build_search_text
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
               dim4_specialty, dim5_location, dim6_material, search_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6,
             build_search_text(no, title, content)[0]),
        )
