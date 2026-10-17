"""LLM_BACKEND=codex: Codex CLI subprocess backend (subprocess mocked, no network, no OpenRouter)."""
import asyncio
import json

import pytest

import codex_backend
import jev_classifier
import llm_orchestrator


def _jsonl(*events):
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


class FakeProc:
    def __init__(self, out=b"", err=b"", code=0):
        self._out, self._err, self.returncode = out, err, code
        self.stdin_data = None

    async def communicate(self, input=None):
        self.stdin_data = input
        return self._out, self._err

    def kill(self):
        pass


OK = _jsonl(
    {"type": "thread.started"},
    {"type": "item.completed", "item": {"type": "reasoning", "text": "x"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
    {"type": "turn.completed", "usage": {"input_tokens": 1}},
)


def _patch_exec(monkeypatch, procs):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return procs.pop(0)

    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", fake_exec)
    return calls


def test_parse_agent_message_and_usage():
    text, err = codex_backend.parse_events(OK.decode())
    assert text == "hello" and err == ""


def test_parse_turn_failed():
    out = _jsonl({"type": "turn.failed", "error": {"message": "boom"}}).decode()
    text, err = codex_backend.parse_events(out)
    assert text == "" and "boom" in err


def test_parse_ignores_non_json_lines():
    text, _ = codex_backend.parse_events("warning: x\n" + OK.decode())
    assert text == "hello"


@pytest.mark.parametrize("model,expected", [
    ("openai/gpt-5.6-sol", "gpt-5.6-sol"), ("gpt-5.6-sol", "gpt-5.6-sol"),
])
def test_model_prefix_stripped(monkeypatch, model, expected):
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("GENERATE_MODEL", model)
    assert codex_backend.codex_model() == expected


def test_codex_model_env_wins(monkeypatch):
    monkeypatch.setenv("CODEX_MODEL", "gpt-x")
    monkeypatch.setenv("GENERATE_MODEL", "openai/other")
    assert codex_backend.codex_model() == "gpt-x"


def test_call_llm_uses_codex_and_no_http(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("GENERATE_MODEL", "openai/gpt-5.6-sol")
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "high")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    proc = FakeProc(OK)
    calls = _patch_exec(monkeypatch, [proc])
    assert asyncio.run(llm_orchestrator.call_llm("USER", system_prompt="SYS")) == "hello"
    args = calls[0][0]
    assert args[0].endswith("codex") and "exec" in args and "gpt-5.6-sol" in args
    assert 'model_reasoning_effort="high"' in args and "--ephemeral" in args and args[-1] == "-"
    stdin = proc.stdin_data.decode()
    assert "SYS" in stdin and "USER" in stdin and stdin.index("SYS") < stdin.index("USER")


def test_default_backend_is_openrouter(monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        asyncio.run(llm_orchestrator.call_llm("x"))


def test_failure_raises_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("GENERATE_MODEL", "m")
    async def no_sleep(s):
        pass

    monkeypatch.setattr(codex_backend.asyncio, "sleep", no_sleep)
    failed = _jsonl({"type": "turn.failed", "error": {"message": "bad"}})
    _patch_exec(monkeypatch, [FakeProc(failed, code=1) for _ in range(3)])
    with pytest.raises(codex_backend.CodexError, match="bad"):
        asyncio.run(llm_orchestrator.call_llm("x"))


def test_rate_limit_retried_with_backoff(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("GENERATE_MODEL", "m")
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(codex_backend.asyncio, "sleep", fake_sleep)
    limited = _jsonl({"type": "error", "message": "429 rate limit"}, {"type": "turn.failed", "error": {"message": "rate limit exceeded"}})
    calls = _patch_exec(monkeypatch, [FakeProc(limited, code=1), FakeProc(OK)])
    assert asyncio.run(llm_orchestrator.call_llm("x")) == "hello"
    assert len(calls) == 2 and sleeps == [90]


def test_concurrency_bounded(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("GENERATE_MODEL", "m")
    monkeypatch.setenv("CODEX_MAX_CONCURRENCY", "2")
    codex_backend._reset_semaphore()
    state = {"cur": 0, "max": 0}

    async def fake_exec(*a, **k):
        class P(FakeProc):
            async def communicate(self, input=None):
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
                await asyncio.sleep(0.01)
                state["cur"] -= 1
                return OK, b""
        return P(OK)

    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", fake_exec)

    async def go():
        return await asyncio.gather(*[llm_orchestrator.call_llm("x") for _ in range(6)])

    assert asyncio.run(go()) == ["hello"] * 6
    assert state["max"] == 2


@pytest.mark.parametrize("marker", ["MODAL_ENVIRONMENT", "MODAL_TASK_ID", "MODAL_IS_REMOTE"])
def test_modal_refuses_codex(monkeypatch, marker):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv(marker, "1")
    with pytest.raises(RuntimeError, match="codex"):
        codex_backend.refuse_codex_when_deployed()


def test_modal_ok_for_openrouter(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openrouter")
    monkeypatch.setenv("MODAL_TASK_ID", "1")
    codex_backend.refuse_codex_when_deployed()


def test_call_llm_refuses_codex_on_modal(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("MODAL_TASK_ID", "1")
    with pytest.raises(RuntimeError):
        asyncio.run(llm_orchestrator.call_llm("x"))


def test_jev_disabled_skips_http_and_uses_heuristic(monkeypatch):
    monkeypatch.setenv("JEV_DISABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    def boom(**kw):
        raise AssertionError("Jev must not be called")

    monkeypatch.setattr(jev_classifier.httpx, "AsyncClient", boom)
    with pytest.raises(jev_classifier.JevUnavailable):
        asyncio.run(jev_classifier.classify_document_type("# Hi\n\nbody"))
    result = asyncio.run(llm_orchestrator.analyze_content_with_llm("# Hi\n\nbody"))
    assert result["reasoning"] == "Fallback to heuristic analysis"
