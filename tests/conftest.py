import sys
import warnings
from pathlib import Path

import pytest

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


def real_store_snapshot() -> dict[str, tuple[str, int]]:
    """真实存储快照：`{口径: (路径, 计数)}`，供会话级守卫使用。

    - 取的是**真实路径**（`app.config` 的模块常量：测试期间未被 patch 的那一份）。
    - ⚠ 必须先 import `app.config` 再测量：该模块导入时会 mkdir 这几个目录
      （`app/config.py:23-26`），否则「目录不存在(-1) → 被导入创建」会被误判成污染。
    - 库/目录不存在或不可读用 `-1` 表示（与「存在但为 0」区分）。
    """
    from app.config import LANCE_DB_PATH, OUTPUT_DIR, UPLOAD_DIR

    rows = -1
    try:
        import lancedb

        db = lancedb.connect(str(LANCE_DB_PATH))
        if "clause_embeddings" in db.table_names():
            rows = db.open_table("clause_embeddings").count_rows()
    except Exception as e:  # 库不存在/不可读 → 用 -1 表示「无表」
        # 不得静默：吞掉异常会让守卫在「库不可读」时退化成恒真断言（GC §4）。
        warnings.warn(f"真实 lance_db 不可读，快照以 -1 表示：{e}")
        rows = -1

    def _count(root: str, pattern: str | None = None) -> int:
        p = Path(root)
        if not p.is_dir():
            return -1
        return len(list(p.glob(pattern))) if pattern else len(list(p.iterdir()))

    return {
        "lance_db": (str(LANCE_DB_PATH), rows),
        "uploads": (str(UPLOAD_DIR), _count(UPLOAD_DIR, "*.md")),
        "outputs": (str(OUTPUT_DIR), _count(OUTPUT_DIR)),
    }


@pytest.fixture(scope="session", autouse=True)
def _guard_real_stores():
    """会话级隔离守卫：整套测试跑完，真实 lance_db / uploads / outputs 必须零增长。

    ⚠ 为什么必须是**跨套件（session 级）**口径：用例内自比（同一用例前后各取一次
    快照、两次之间没有任何写操作）对「跨用例污染」恒真。实测过这一形态的失效——
    每跑一次全量套件真实 lance_db 多 2 行、`data/uploads/` 多 1 个 md，而用例内
    自比照样 PASS，什么也没守住。

    失败即说明**某个用例漏 patch 了消费方模块名**（C-4，例如只 patch 了
    `app.database.DATABASE_PATH`，而导入流程还会写 `LANCE_DB_PATH` 与
    `UPLOAD_DIR`/`OUTPUT_DIR`）。修法是给该用例补 patch，**不是放宽本守卫**。
    """
    before = real_store_snapshot()
    yield
    after = real_store_snapshot()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, "测试会话改动了真实存储（应零增长）：" + "；".join(
        f"{k}（{b[0]}）{b[1]} → {a[1]}" for k, (b, a) in changed.items())


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
