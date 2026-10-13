"""Tests for the section-by-section path that keeps long reports from being condensed away."""

import asyncio

import pytest

from a2ui_generator import A2UIComponent
from section_orchestrator import (
    coverage_components,
    extract_source_items,
    orchestrate_sections,
    should_use_section_path,
    split_report,
)

REPORT = "# Report\n\n" + "\n\n".join(
    f"## Section {i}\n\n**Card {i}**\nIntro paragraph number {i} about the market.\n- first bullet {i}\n- second bullet {i} with numbers 42%"
    for i in range(1, 6)
) + "\n\n" + ("filler " * 2000)


def _stat(text: str) -> A2UIComponent:
    return A2UIComponent(type="calloutCard", id="x", props={"title": "t", "content": text})


def test_split_report_titles_and_bodies():
    pre, sections = split_report(REPORT)
    assert pre.startswith("# Report")
    assert [t for t, _ in sections] == [f"Section {i}" for i in range(1, 6)]
    assert "first bullet 2" in sections[1][1]


def test_should_use_section_path_thresholds():
    assert should_use_section_path(REPORT)
    assert not should_use_section_path("# Short\n\n## A\n\n- x")
    assert not should_use_section_path("# T\n\n## A\n\n" + "word " * 5000)  # long but one section


def test_extract_source_items_kinds():
    items = extract_source_items("**Card**\nIntro line\n\n- b1\n- b2\n1. n1")
    assert items == [("paragraph", "**Card** Intro line"), ("bullet", "b1"), ("bullet", "b2"), ("bullet", "n1")]


def test_coverage_adds_only_missing_text_verbatim():
    body = "- alpha beta gamma delta\n- omega sigma tau 99 upsilon\n\nA lone paragraph about 7 zebras and giraffes"
    built = [_stat("alpha beta gamma delta covered here")]
    out = coverage_components(body, built)
    assert [c.type for c in out] == ["bulletList"]
    texts = [i["text"] for i in out[0].props["items"]]
    assert texts[0] == "omega sigma tau 99 upsilon" and texts[1].startswith("A lone paragraph")


@pytest.mark.asyncio
async def test_orchestrate_sections_headings_and_fallback_when_llm_fails():
    async def failing_select(analysis, layout, md, extra):
        raise RuntimeError("llm down")

    out = [c async for c in orchestrate_sections(
        REPORT, failing_select, lambda s, a: None, lambda s: s, lambda c, s: c,
    )]
    headings = [c.props["text"] for c in out if c.type == "heading"]
    assert headings == [f"Section {i}" for i in range(1, 6)]
    bullets = [i["text"] for c in out if c.type == "bulletList" for i in c.props["items"]]
    assert len(bullets) >= 10 and "second bullet 3 with numbers 42%" in bullets
    assert len({c.id for c in out}) == len(out)


@pytest.mark.asyncio
async def test_orchestrate_sections_keeps_llm_components_between_heading_and_gap_fill():
    async def select(analysis, layout, md, extra):
        assert "ONE SECTION" in extra
        return [{"component_type": "calloutCard", "props": {"content": f"first bullet {analysis['title'][-1]} and more"}}]

    def build(spec, analysis):
        return _stat(spec["props"]["content"])

    out = [c async for c in orchestrate_sections(
        REPORT, select, build, lambda s: s, lambda c, s: c,
    )]
    first = [c.type for c in out[:3]]
    # heading, LLM callout, then ONE merged gap-fill list (intro paragraph + uncovered bullet)
    assert first == ["heading", "calloutCard", "bulletList"]


def test_gap_fill_keeps_evidence_verbatim_and_merges_the_rest():
    body = (
        "- Market grew 42% to 3.1B SAR in 2025\n"
        "- see https://example.com/report for method\n"
        "- competitor Noon leads delivery\n"
        "- generally the situation is quite complex and nuanced\n"
        "- another vague remark about things in general\n"
    )
    out = coverage_components(body, [])
    texts = [[i["text"] for i in c.props["items"]] for c in out]
    assert len(out) == 2 and all(c.type == "bulletList" for c in out)
    assert texts[0] == [
        "Market grew 42% to 3.1B SAR in 2025",
        "see https://example.com/report for method",
        "competitor Noon leads delivery",
    ]
    assert texts[1] == [
        "generally the situation is quite complex and nuanced",
        "another vague remark about things in general",
    ]


def test_gap_fill_has_evidence_detection():
    from section_orchestrator import has_evidence
    assert has_evidence("revenue 15 million") and has_evidence("حجم السوق 12 مليار ريال")
    assert has_evidence("rival Careem entered")
    assert not has_evidence("this is plain filler prose without anything specific")


# --- condense instead of truncate (llm_orchestrator.fit_document_to_budget) ---

@pytest.mark.asyncio
async def test_short_document_is_untouched(monkeypatch):
    import llm_orchestrator as lo

    async def boom(*a, **k):
        raise AssertionError("LLM must not be called under budget")

    monkeypatch.setattr(lo, "call_llm", boom)
    doc = "x" * lo.TARGET_INPUT_CHARS
    assert await lo.fit_document_to_budget(doc) == doc


@pytest.mark.asyncio
async def test_over_budget_is_condensed_not_truncated(monkeypatch):
    import llm_orchestrator as lo

    calls: list[str] = []

    async def fake_llm(prompt, system_prompt="", **k):
        calls.append(prompt)
        return prompt.replace("word ", "")  # drops filler, keeps facts

    monkeypatch.setattr(lo, "call_llm", fake_llm)
    doc = "\n\n".join(f"Paragraph {i} revenue {i}% " + "word " * 400 for i in range(25))  # ~55k chars
    assert len(doc) > 40000
    out = await lo.fit_document_to_budget(doc)
    assert len(calls) >= 2  # chunked
    assert "content truncated" not in out
    assert len(out) <= lo.HARD_MAX_INPUT_CHARS and "Paragraph 24" in out


@pytest.mark.asyncio
async def test_still_over_hard_cap_after_condense_fails_loudly(monkeypatch):
    import llm_orchestrator as lo

    async def identity(prompt, system_prompt="", **k):
        return prompt

    monkeypatch.setattr(lo, "call_llm", identity)
    doc = "\n\n".join("para " * 400 for _ in range(30))  # ~60k chars, no shrink
    with pytest.raises(lo.InputTooLongError, match="limit 40000"):
        await lo.fit_document_to_budget(doc)
