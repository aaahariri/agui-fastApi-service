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


def test_routing_by_structure_not_length():
    assert should_use_section_path(REPORT)
    short4 = "# T\n\n" + "\n\n".join(f"## S{i}\n\n- x{i}" for i in range(4))
    assert len(short4) < 200 and should_use_section_path(short4)
    assert not should_use_section_path("# Short\n\n## A\n\n- x")
    assert not should_use_section_path("# T\n\n## A\n\n" + "word " * 2000)  # <30k, one section
    assert should_use_section_path("# T\n\n## A\n\n" + "word " * 7000)  # >30k -> pseudo-sections


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
async def test_section_llm_failure_raises():
    async def failing_select(analysis, layout, md, extra):
        raise RuntimeError("llm down")

    with pytest.raises(RuntimeError, match="llm down"):
        [c async for c in orchestrate_sections(
            REPORT, failing_select, lambda s, a: None, lambda s: s, lambda c, s: c,
        )]


async def _none_select(analysis, layout, md, extra):
    return []


@pytest.mark.asyncio
async def test_intro_kept_and_headings_complete():
    out = [c async for c in orchestrate_sections(
        REPORT.replace("# Report\n\n", "# Report\n\nIntro line 7 here\n\n", 1),
        _none_select, lambda s, a: None, lambda s: s, lambda c, s: c,
    )]
    assert (out[0].type, out[0].props) == ("heading", {"text": "Report", "level": 1})
    assert out[1].props["items"][0]["text"] == "Intro line 7 here"
    assert [c.props["text"] for c in out if c.type == "heading" and c.props["level"] == 2] == [
        f"Section {i}" for i in range(1, 6)]
    assert len({c.id for c in out}) == len(out)


@pytest.mark.asyncio
async def test_no_section_long_doc_pseudo_sections_keep_all_numbers():
    doc = "\n\n".join(f"Paragraph {i} revenue {1000 + i} units " + "filler " * 300 for i in range(40))
    assert len(doc) > 30000 and should_use_section_path(doc)
    calls = []

    async def select(analysis, layout, md, extra):
        calls.append(md)
        return []

    out = [c async for c in orchestrate_sections(doc, select, lambda s, a: None, lambda s: s, lambda c, s: c)]
    assert len(calls) >= 4 and all(len(c) < 10000 for c in calls)
    text = " ".join(i["text"] for c in out if c.type == "bulletList" for i in c.props["items"])
    for i in range(40):
        assert f"{1000 + i}" in text
    assert not any(c.type == "heading" for c in out)


def test_too_long_input_raises():
    from section_orchestrator import InputTooLongError, MAX_INPUT_CHARS
    with pytest.raises(InputTooLongError):
        should_use_section_path("x" * (MAX_INPUT_CHARS + 1))


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
    first = [c.type for c in out[1:4]]  # out[0] is the level-1 title
    # heading, LLM callout, then ONE merged gap-fill list (intro paragraph + uncovered bullet)
    assert first == ["heading", "calloutCard", "bulletList"]


def test_gap_fill_never_truncates_and_turns_tables_into_data_table():
    long_prose = "word " * 100 + "ends here."
    body = f"{long_prose}\n\n| Name | Value |\n|---|---|\n| plain | 5 \\| 6 |\n| other | 7 |"
    out = coverage_components(body, [])
    table = next(c for c in out if c.type == "dataTable")
    assert table.props["headers"] == ["Name", "Value"]
    assert table.props["rows"] == [["plain", "5 | 6"], ["other", "7"]]
    texts = [i["text"] for c in out if c.type == "bulletList" for i in c.props["items"]]
    assert long_prose.strip() in texts
    assert not any(t.startswith("|") or "---" in t for t in texts)


def test_covered_table_is_not_duplicated():
    body = "| Name | Value |\n|---|---|\n| Acme | 12 |"
    built = [A2UIComponent(type="dataTable", id="x", props={"headers": ["Name", "Value"], "rows": [["Acme", "12"]]}, zone="content")]
    assert coverage_components(body, built) == []


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


@pytest.mark.asyncio
async def test_call_llm_retries_429_and_5xx_but_not_other_4xx(monkeypatch):
    import httpx
    import llm_orchestrator as lo

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    async def no_sleep(_):
        pass

    monkeypatch.setattr(lo.asyncio, "sleep", no_sleep)

    real = httpx.AsyncClient

    def run(statuses):
        seq = iter(statuses)
        seen = []

        def handler(request):
            seen.append(1)
            code = next(seq)
            if code == 200:
                return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
            return httpx.Response(code, text="err")

        monkeypatch.setattr(lo.httpx, "AsyncClient", lambda **k: real(transport=httpx.MockTransport(handler)))
        return seen

    seen = run([429, 503, 200])
    assert await lo.call_llm("p") == "ok" and len(seen) == 3
    seen = run([429, 429, 429])
    with pytest.raises(Exception, match="429"):
        await lo.call_llm("p")
    assert len(seen) == 3
    seen = run([400, 200])
    with pytest.raises(Exception, match="400"):
        await lo.call_llm("p")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_empty_section_body_skips_llm_and_keeps_heading():
    calls: list[str] = []

    async def recording_select(analysis, layout, md, extra):
        calls.append(md)
        if "Body 2" not in md:
            return []  # the real selector raises on an empty list
        return [{"component_type": "statCard"}]

    doc = "# Report\n\n## Empty\n\n## Section 2\n\nBody 2 with 45% growth\n\n## Section 3\n\n## Section 4\n\nBody 4\n\n## Section 5\n\nBody 5\n"
    out = [c async for c in orchestrate_sections(doc, recording_select, lambda s, a: None, lambda s: s, lambda c, s: c)]
    assert not any(md.startswith("## Empty") or md.startswith("## Section 3") for md in calls)
    assert [c.props["text"] for c in out if c.type == "heading" and c.props["level"] == 2] == [
        "Empty", "Section 2", "Section 3", "Section 4", "Section 5"]


@pytest.mark.asyncio
async def test_section_selector_allows_empty_selection(monkeypatch):
    import llm_orchestrator

    async def empty_llm(*args, **kwargs):
        return '{"components": []}'

    monkeypatch.setattr(llm_orchestrator, "call_llm", empty_llm)
    analysis = {"document_type": "report", "title": "R", "sections": []}
    layout = {"layout_type": "summary_layout"}
    assert await llm_orchestrator.select_components_with_llm(
        analysis, layout, "## T\n\n### heading only", allow_empty=True) == []
    with pytest.raises(ValueError, match="no components"):
        await llm_orchestrator.select_components_with_llm(analysis, layout, "## T\n\ntext")
