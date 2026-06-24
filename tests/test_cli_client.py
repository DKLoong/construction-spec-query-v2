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
