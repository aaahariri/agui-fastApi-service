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


def test_explicit_openrouter_works_locally(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openrouter")
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


def test_hardening_flags_env_and_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("GENERATE_MODEL", "m")
    monkeypatch.setenv("API_KEY", "super-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")
    fake_home = tmp_path / "realcodex"
    fake_home.mkdir()
    (fake_home / "auth.json").write_text("{}")
    (fake_home / "config.toml").write_text("x")
    monkeypatch.setenv("CODEX_HOME", str(fake_home))
    seen = {}

    async def fake_exec(*args, **kwargs):
        import os
        seen["args"], seen["kwargs"] = args, kwargs
        home = kwargs["env"]["CODEX_HOME"]
        seen["home_files"] = sorted(os.listdir(home))
        seen["cwd_files"] = os.listdir(kwargs["cwd"])
        return FakeProc(OK)

    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(llm_orchestrator.call_llm("x")) == "hello"
    args, env = list(seen["args"]), seen["kwargs"]["env"]
    for flag in ("--ignore-user-config", "--ignore-rules"):
        assert flag in args
    disabled = {args[i + 1] for i, a in enumerate(args) if a == "--disable"}
    assert {"shell_tool", "unified_exec", "code_mode_host", "apps", "plugins", "multi_agent"} <= disabled
    assert 'web_search="disabled"' in args and "include_apply_patch_tool=false" in args and "tools.view_image=false" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert seen["home_files"] == ["auth.json"] and seen["cwd_files"] == []
    assert env["CODEX_HOME"] != str(fake_home) and "API_KEY" not in env and "OPENROUTER_API_KEY" not in env


@pytest.mark.parametrize("marker", ["MODAL_ENVIRONMENT", "MODAL_TASK_ID", "MODAL_IS_REMOTE", "RAILWAY_ENVIRONMENT", "K_SERVICE"])
def test_unset_backend_is_openrouter_on_cloud(monkeypatch, marker):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.setenv(marker, "1")
    assert codex_backend.resolved_backend() == "openrouter"
    codex_backend.refuse_codex_when_deployed()  # no error


def test_unset_backend_is_codex_locally(monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert codex_backend.resolved_backend() == "codex"
    assert codex_backend.backend_is_codex()


def test_explicit_openrouter_wins_locally_and_explicit_codex_refused_on_cloud(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "openrouter")
    assert codex_backend.resolved_backend() == "openrouter"
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("K_SERVICE", "x")
    with pytest.raises(RuntimeError):
        codex_backend.refuse_codex_when_deployed()


def test_component_model_override_codex(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "codex")
    monkeypatch.setenv("GENERATE_MODEL", "openai/gpt-5.6-sol")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.6-sol")
    assert codex_backend.codex_model("openai/gpt-5.6-luna") == "gpt-5.6-luna"
    assert codex_backend.codex_model(None) == "gpt-5.6-sol"


def _spy_select(monkeypatch, component_model):
    seen = {}

    async def fake_call(prompt, system_prompt="", max_tokens=4000, temperature=0.7, model=None):
        seen["model"] = model
        return json.dumps({"components": []})

    monkeypatch.setattr(llm_orchestrator, "call_llm", fake_call)
    if component_model:
        monkeypatch.setenv("COMPONENT_MODEL", component_model)
    asyncio.run(llm_orchestrator.select_components_with_llm({}, {}, "# t\n\nbody", allow_empty=True))
    return seen.get("model")


def test_select_components_uses_component_model(monkeypatch):
    assert _spy_select(monkeypatch, "openai/gpt-5.6-luna") == "openai/gpt-5.6-luna"


def test_select_components_falls_back_to_default_model(monkeypatch):
    assert _spy_select(monkeypatch, None) is None


def test_component_model_reaches_openrouter_payload(monkeypatch):
    import httpx
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GENERATE_MODEL", "openai/gpt-5.6-sol")
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(llm_orchestrator.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    asyncio.run(llm_orchestrator.call_llm("x", model="openai/gpt-5.6-luna"))
    assert seen["model"] == "openai/gpt-5.6-luna"
