"""The /generate prompts must only structure text: copy it verbatim, never condense it."""

import pytest

from llm_orchestrator import select_components_with_llm
import llm_orchestrator
from prompts import (
    COMPONENT_SELECTION_PROMPT,
    VERBATIM_COPY_RULE,
    format_component_selection_prompt,
)
from section_orchestrator import SECTION_INSTRUCTIONS

FORBIDDEN = [
    "be concise",
    "merge related points",
    "drop repetition",
    "minimum words",
    "15-25",
    "stay in range",
    "key points",
    "3-6 components",
]


def _short_doc_prompts() -> tuple[str, str]:
    """Capture the (prompt, system_prompt) the short-document path sends to the LLM."""
    captured: dict[str, str] = {}

    async def fake_call_llm(prompt, system_prompt, **kwargs):
        captured["prompt"] = prompt
        captured["system"] = system_prompt
        return '{"components": [{"component_type": "bulletList", "props": {}}]}'

    return captured, fake_call_llm


@pytest.mark.parametrize("text", [VERBATIM_COPY_RULE, SECTION_INSTRUCTIONS])
def test_verbatim_rule_present(text):
    low = text.lower()
    assert "word for word" in low
    for word in ("shorten", "merge", "paraphrase", "summarise", "translate"):
        assert word in low
    assert "as many components as needed" in low


def test_section_instructions_drop_condense_rules():
    low = SECTION_INSTRUCTIONS.lower()
    for phrase in FORBIDDEN:
        assert phrase not in low


def test_component_selection_prompt_has_rule_and_no_condense_rules():
    prompt = format_component_selection_prompt({}, {})
    low = prompt.lower()
    assert VERBATIM_COPY_RULE in prompt
    assert "{verbatim_rule}" in COMPONENT_SELECTION_PROMPT
    for phrase in ("be concise", "merge related points", "drop repetition", "15-25", "stay in range"):
        assert phrase not in low


@pytest.mark.asyncio
async def test_short_document_path_prompts(monkeypatch):
    captured, fake = _short_doc_prompts()
    monkeypatch.setattr(llm_orchestrator, "call_llm", fake)
    await select_components_with_llm({}, {}, "# Doc\n\nHello world 42.")
    for key in ("prompt", "system"):
        low = captured[key].lower()
        assert "word for word" in low
        for phrase in ("be concise", "merge related points", "drop repetition", "15-25"):
            assert phrase not in low
