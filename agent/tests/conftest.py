import pytest

_ENV = ("LLM_BACKEND", "COMPONENT_MODEL", "CODEX_MODEL", "MODAL_ENVIRONMENT", "MODAL_TASK_ID", "MODAL_IS_REMOTE",
        "RAILWAY_ENVIRONMENT", "K_SERVICE", "JEV_DISABLED")


@pytest.fixture(autouse=True)
def _llm_env(monkeypatch):
    """Tests are hermetic: no leaked backend/cloud markers; the default for a bare test is the OpenRouter path (mocked)."""
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_BACKEND", "openrouter")
