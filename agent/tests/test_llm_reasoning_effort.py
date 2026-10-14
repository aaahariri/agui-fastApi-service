"""call_llm forwards OPENROUTER_REASONING_EFFORT as reasoning.effort only when set."""
import asyncio
import httpx
import llm_orchestrator as orchestrator

def _run(monkeypatch, effort):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    if effort:
        monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", effort)
    else:
        monkeypatch.delenv("OPENROUTER_REASONING_EFFORT", raising=False)
    seen = {}
    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(orchestrator.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(orchestrator.call_llm("hi")) == "ok"
    return seen

def test_effort_forwarded(monkeypatch):
    assert _run(monkeypatch, "medium")["reasoning"] == {"effort": "medium"}

def test_effort_absent_by_default(monkeypatch):
    assert "reasoning" not in _run(monkeypatch, None)
