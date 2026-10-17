"""
Document-type classification with Jev (TypeSafe System One) via OpenRouter.

Jev is a decision model: one request, typed answers with a probability per option. Two Choice
questions over the SAME options in opposite order go in that one request (option order can bias a
Choice); their probability distributions are averaged and the top type wins. If the averaged top
probability is below DOC_TYPE_MIN_PROBABILITY the classifier abstains and the caller falls back to
the heuristic. Title and entities are not Jev's job (the parser provides them).

Contract: POST https://openrouter.ai/api/v1/systemone {model, state, questions} with the OpenRouter
key; choice answers carry `probabilities`. Benchmark: .workspace/project-management/reports/jev-benchmark
in the web repo (100% on the document-type set at 0.3 s vs 5.4 s for the chat model).
"""
import os
import re

import httpx

JEV_URL = "https://openrouter.ai/api/v1/systemone"
DEFAULT_JEV_MODEL = "typesafe/jev-1.13"  # pinned version; override with JEV_MODEL
DOC_TYPE_MIN_PROBABILITY = 0.5  # averaged top probability below this -> abstain
MAX_DOCUMENT_CHARS = 30000  # same cap as format_content_analysis_prompt
REQUEST_TIMEOUT_SECONDS = 15.0


class JevUnavailable(Exception):
    """Jev could not give a usable answer (no key, HTTP/network error, bad reply, or abstained)."""


def _document_types() -> dict[str, str]:
    """The document types and their descriptions, parsed from the analysis prompt so there is one source."""
    from prompts import CONTENT_ANALYSIS_PROMPT

    types = dict(re.findall(r"^- \*\*([a-z_]+)\*\*: (.+)$", CONTENT_ANALYSIS_PROMPT, flags=re.MULTILINE))
    if not types:
        raise JevUnavailable("no document types found in CONTENT_ANALYSIS_PROMPT")
    return types


def _choice(criteria: dict[str, str]) -> dict:
    return {
        "type": "choice",
        "instructions": "What type of document is `document`, judged by its primary purpose and structure?",
        "criteria": criteria,
    }


async def classify_document_type(markdown: str) -> tuple[str, float]:
    """Return (document_type, averaged probability). Raises JevUnavailable when there is no usable answer."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise JevUnavailable("OPENROUTER_API_KEY is not set")

    types = _document_types()
    body = {
        "model": os.getenv("JEV_MODEL") or DEFAULT_JEV_MODEL,
        "state": {"document": markdown[:MAX_DOCUMENT_CHARS]},
        "questions": {
            "document_type": _choice(types),
            "document_type_reversed": _choice(dict(reversed(list(types.items())))),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"})
        response.raise_for_status()
        answers = response.json()["answers"]
        distributions = [answers[q]["probabilities"] for q in ("document_type", "document_type_reversed")]
        averaged = {t: sum(d.get(t, 0.0) for d in distributions) / len(distributions) for t in types}
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise JevUnavailable(f"Jev request failed: {exc}") from exc

    best = max(averaged, key=lambda t: averaged[t])
    if averaged[best] < DOC_TYPE_MIN_PROBABILITY:
        raise JevUnavailable(f"Jev abstained: top probability {averaged[best]:.2f} for '{best}'")
    return best, averaged[best]
