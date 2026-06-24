import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
