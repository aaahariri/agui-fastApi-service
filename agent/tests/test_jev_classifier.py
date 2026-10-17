"""Document-type classification via Jev (System One) with the heuristic as fallback.

httpx is mocked with MockTransport; no network.
"""
import asyncio
import json

import httpx
import pytest

import jev_classifier
import llm_orchestrator


TYPES = ["tutorial", "research", "article", "guide", "notes", "technical_doc", "overview"]


def _probs(top: str, p: float) -> dict:
    rest = (1 - p) / (len(TYPES) - 1)
    return {t: (p if t == top else rest) for t in TYPES}


def _answer(top: str, p: float) -> dict:
    return {"type": "choice", "choice": top, "probabilities": _probs(top, p), "confidence": p}


def _install(monkeypatch, handler):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("JEV_MODEL", raising=False)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        jev_classifier.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


def test_one_request_two_option_orders_default_model(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"document_type": _answer("tutorial", 0.9), "document_type_reversed": _answer("tutorial", 0.88)},
        })

    _install(monkeypatch, handler)
    doc_type, confidence = asyncio.run(jev_classifier.classify_document_type("# T\n\nStep 1 ..."))

    assert (doc_type, round(confidence, 2)) == ("tutorial", 0.89)
    assert seen["url"] == "https://openrouter.ai/api/v1/systemone"
    assert seen["auth"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"]["document"].startswith("# T")
    qs = body["questions"]
    assert set(qs) == {"document_type", "document_type_reversed"}
    assert list(qs["document_type"]["criteria"]) == TYPES
    assert list(qs["document_type_reversed"]["criteria"]) == TYPES[::-1]


def test_model_from_env(monkeypatch):
    seen = {}

    def handler(request):
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"model": "m", "answers": {
            "document_type": _answer("guide", 0.9), "document_type_reversed": _answer("guide", 0.9)}})

    _install(monkeypatch, handler)
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-9.9")
    asyncio.run(jev_classifier.classify_document_type("doc"))
    assert seen["model"] == "typesafe/jev-9.9"


def test_disagreeing_orders_are_averaged(monkeypatch):
    # order A leans article (0.5), reversed says guide (0.95): guide wins on averaged probability
    def handler(request):
        return httpx.Response(200, json={"model": "m", "answers": {
            "document_type": _answer("article", 0.5), "document_type_reversed": _answer("guide", 0.95)}})

    _install(monkeypatch, handler)
    doc_type, _ = asyncio.run(jev_classifier.classify_document_type("doc"))
    assert doc_type == "guide"


def test_low_probability_abstains(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"model": "m", "answers": {
            "document_type": _answer("article", 0.3), "document_type_reversed": _answer("guide", 0.3)}})

    _install(monkeypatch, handler)
    with pytest.raises(jev_classifier.JevUnavailable):
        asyncio.run(jev_classifier.classify_document_type("doc"))


def test_http_error_raises_unavailable(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(529, text="overloaded"))
    with pytest.raises(jev_classifier.JevUnavailable):
        asyncio.run(jev_classifier.classify_document_type("doc"))


def test_missing_key_raises_without_a_request(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(jev_classifier.JevUnavailable):
        asyncio.run(jev_classifier.classify_document_type("doc"))


def test_malformed_reply_raises_unavailable(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(200, json={"model": "m", "answers": {"document_type": {"type": "noul", "noul": 1}}}))
    with pytest.raises(jev_classifier.JevUnavailable):
        asyncio.run(jev_classifier.classify_document_type("doc"))


# --- analyze_content_with_llm integration -----------------------------------

DOC = "# Building APIs\n\nSome intro text.\n\n## Setup\n\nInstall FastAPI with pip.\n"


def test_analyze_uses_jev_type_and_keeps_parser_title_and_entities(monkeypatch):
    async def fake(markdown):
        return "research", 0.93

    async def boom(*a, **k):
        raise AssertionError("the chat LLM must not be called")

    monkeypatch.setattr(llm_orchestrator, "classify_document_type", fake)
    monkeypatch.setattr(llm_orchestrator, "call_llm", boom)
    result = asyncio.run(llm_orchestrator.analyze_content_with_llm(DOC))

    assert result["document_type"] == "research"
    assert result["confidence"] == 0.93
    assert result["title"] == "Building APIs"
    assert "FastAPI" in result["entities"]["technologies"]


def test_analyze_falls_back_to_heuristic_on_jev_error(monkeypatch):
    async def fake(markdown):
        raise jev_classifier.JevUnavailable("down")

    monkeypatch.setattr(llm_orchestrator, "classify_document_type", fake)
    result = asyncio.run(llm_orchestrator.analyze_content_with_llm(DOC))

    assert result["document_type"] in TYPES
    assert result["confidence"] == 0.5
    assert "heuristic" in result["reasoning"].lower()
    assert result["title"] == "Building APIs"
