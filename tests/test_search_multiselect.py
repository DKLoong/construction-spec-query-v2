"""分类树同维多选（OR 语义）测试"""
from tests.conftest import setup_search_data
from app.database import get_db, init_db


def _setup_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)  # dim4: 结构专业/建筑专业
    return auth_client


def test_search_dim4_multiple_values_or(auth_client, monkeypatch, tmp_path):
    """同维多选 → OR 语义：结构专业 或 建筑专业 都命中"""
    client = _setup_db(auth_client, monkeypatch, tmp_path, "ms1.db")
    resp = client.get("/search?dim4_specialty=结构专业&dim4_specialty=建筑专业")
    assert "钢筋" in resp.text and "屋面防水" in resp.text


def test_search_dim4_single_value_compat(auth_client, monkeypatch, tmp_path):
    """单值参数向后兼容"""
    client = _setup_db(auth_client, monkeypatch, tmp_path, "ms2.db")
    resp = client.get("/search?dim4_specialty=结构专业")
    assert "钢筋" in resp.text
    assert "屋面防水" not in resp.text


def test_search_dim6_multiple_or(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "ms3.db")
    resp = client.get("/search?dim6_material=模板工程&dim6_material=防水材料")
    assert "模板设计" in resp.text
    assert "屋面防水" in resp.text
