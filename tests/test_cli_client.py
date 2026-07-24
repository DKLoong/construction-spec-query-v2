import inspect
from app.ai.cli_client import get_backend, ClaudeCodeCLI, CodexCLI, CLIBackend


def test_get_backend_claude():
    backend = get_backend("claude")
    assert isinstance(backend, ClaudeCodeCLI)
    assert backend.command == "claude"


def test_get_backend_codex():
    backend = get_backend("codex")
    assert isinstance(backend, CodexCLI)
    assert backend.command == "codex"


def test_get_backend_default():
    backend = get_backend("unknown")
    assert isinstance(backend, ClaudeCodeCLI)


def test_claude_is_available_mocked(monkeypatch):
    class FakeResult:
        returncode = 0
    def fake_run(*args, **kwargs):
        return FakeResult()
    monkeypatch.setattr("subprocess.run", fake_run)
    backend = ClaudeCodeCLI()
    assert backend.is_available() is True


def test_codex_is_available_mocked(monkeypatch):
    class FakeResult:
        returncode = 0
    def fake_run(*args, **kwargs):
        return FakeResult()
    monkeypatch.setattr("subprocess.run", fake_run)
    backend = CodexCLI()
    assert backend.is_available() is True


def test_cli_backend_abstract():
    assert inspect.isabstract(CLIBackend)


# ============================================================
# Task 5: get_backend() 多后端支持测试
# ============================================================

def test_get_backend_api(monkeypatch, tmp_path):
    """doubao 等返回 APIBackend"""
    db_path = tmp_path / "test_api_backend.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.doubao.api_key', 'sk-test')"
        )

    from app.ai.cli_client import get_backend
    from app.ai.api_client import APIBackend
    backend = get_backend("doubao")
    assert isinstance(backend, APIBackend)
    assert backend.model == "doubao-1.5-pro-32k"


def test_get_backend_custom(monkeypatch, tmp_path):
    """custom 读取自定义配置"""
    db_path = tmp_path / "test_custom.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.custom.base_url', 'https://my.api.com/v1')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.custom.api_key', 'sk-custom')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.custom.model', 'my-model')"
        )

    from app.ai.cli_client import get_backend
    from app.ai.api_client import APIBackend
    backend = get_backend("custom")
    assert isinstance(backend, APIBackend)
    assert backend.base_url == "https://my.api.com/v1"
    assert backend.model == "my-model"


def test_get_backend_default_from_settings(monkeypatch, tmp_path):
    """不传 name 时从 settings 读取 ai.backend"""
    db_path = tmp_path / "test_default.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('ai.backend', 'codex')")

    from app.ai.cli_client import get_backend, CodexCLI
    backend = get_backend()  # 不传参数
    assert isinstance(backend, CodexCLI)
