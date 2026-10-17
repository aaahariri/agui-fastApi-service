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
        return [{"component_type": "calloutCard", "props": {"content": f"first bullet {analysis['title'][-1]}"}}]

    def build(spec, analysis):
        return _stat(spec["props"]["content"])

    out = [c async for c in orchestrate_sections(
        REPORT, select, build, lambda s: s, lambda c, s: c,
    )]
    first = [c.type for c in out[1:5]]  # out[0] is the level-1 title
    # source order: heading, gap-fill (label + intro paragraph), the LLM callout (first bullet), gap-fill (second bullet)
    assert first == ["heading", "bulletList", "calloutCard", "bulletList"]
    assert [i["text"] for i in out[2].props["items"]] == ["Card 1 Intro paragraph number 1 about the market."]


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


# --- t_48d8a80a: inline markdown markers, repeated prose, empty text -------------------------------

def _comp(type_: str, **props) -> A2UIComponent:
    return A2UIComponent(type=type_, id="x", props=props, zone="content")


def _all_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _all_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _all_strings(v)]
    return []


def test_strip_inline_markdown_removes_markers_but_keeps_words():
    from section_orchestrator import strip_inline_markdown

    assert strip_inline_markdown("**المشكلة والحل**: تقدم الفكرة") == "المشكلة والحل: تقدم الفكرة"
    assert strip_inline_markdown("- **Lead**: body") == "Lead: body"
    assert strip_inline_markdown("### خطوات عملية") == "خطوات عملية"
    assert strip_inline_markdown("1. **Core problem**") == "1. Core problem"  # numbering is source text
    assert strip_inline_markdown("__bold__ and `code` and *it*") == "bold and code and it"
    assert strip_inline_markdown("2 * 3 = 6 and a*b") == "2 * 3 = 6 and a*b"  # math/asterisks untouched


def test_clean_component_strips_every_text_leaf_except_code():
    from section_orchestrator import clean_component

    callout = clean_component(_comp("calloutCard", title="1. **Core**", content="**Lead**: body"))
    assert callout.props == {"title": "1. Core", "content": "Lead: body"}
    takeaways = clean_component(_comp("keyTakeaways", items=["- **A**: x", "**B**: y"]))
    assert takeaways.props["items"] == ["A: x", "B: y"]
    bullets = clean_component(_comp("bulletList", items=[{"text": "**A**: x"}]))
    assert bullets.props["items"] == [{"text": "A: x"}]
    table = clean_component(_comp("dataTable", headers=["**H**"], rows=[["**c**", "d"]]))
    assert table.props == {"headers": ["H"], "rows": [["c", "d"]]}
    code = clean_component(_comp("codeBlock", code="x = **kwargs", language="python"))
    assert code.props["code"] == "x = **kwargs"
    link = clean_component(_comp("linkPreview", url="https://a.b/__x__", title="**T**"))
    assert link.props["url"] == "https://a.b/__x__" and link.props["title"] == "T"


VERDICT = "«واعد» — تشير الفكرة إلى فرصة جيدة في السوق السعودي. ويمكن لخدمات تصميم المواقع تلبية احتياجات هذا القطاع."


def test_fragments_of_a_paragraph_are_dropped_and_the_whole_takes_the_first_fragment_position():
    from section_orchestrator import drop_repeated_prose

    first, second = VERDICT.split(". ")[0] + ".", VERDICT.split(". ")[1]
    comps = [
        _comp("heading", text="التقييم المبدئي", level=2),
        _comp("headlineCard", title=first, summary=""),
        _comp("calloutCard", title="", content=second),
        _comp("keyTakeaways", items=[first, second]),
        _comp("statCard", title="x", value="1"),
        _comp("bulletList", items=[{"text": VERDICT}]),
    ]
    out = drop_repeated_prose(comps)
    assert [c.type for c in out] == ["heading", "bulletList", "statCard"]
    assert sum(s.count("تشير الفكرة") for c in out for s in _all_strings(c.props)) == 1
    assert out[1].props["items"][0]["text"] == VERDICT


def test_repeated_prose_keeps_distinct_text_and_non_prose():
    from section_orchestrator import drop_repeated_prose

    comps = [
        _comp("calloutCard", title="", content="a completely different first sentence here"),
        _comp("calloutCard", title="", content="another completely different sentence there"),
        _comp("dataTable", headers=["h"], rows=[["an unrelated table cell with other words"]]),
        _comp("headlineCard", title="short", summary=""),
    ]
    assert drop_repeated_prose(comps) == comps


def test_blank_prose_components_are_dropped():
    from section_orchestrator import drop_empty_components

    comps = [
        _comp("tldr", content="  "),
        _comp("calloutCard", title=" ", content=""),
        _comp("keyTakeaways", items=["", " "]),
        _comp("bulletList", items=[{"text": ""}]),
        _comp("heading", text="H", level=2),
        _comp("tldr", content="real"),
    ]
    assert [c.type for c in drop_empty_components(comps)] == ["heading", "tldr"]


@pytest.mark.asyncio
async def test_section_output_has_no_markers_and_states_each_sentence_once():
    body = f"{VERDICT}\n\n- **عنوان**: نص تفصيلي طويل بما يكفي ليكون بندا"

    async def select(analysis, layout, md, extra):
        s1, s2 = VERDICT.split(". ")[0] + ".", VERDICT.split(". ")[1]
        return [
            {"component_type": "tldr", "props": {"content": VERDICT}},
            {"component_type": "headlineCard", "props": {"title": s1}},
            {"component_type": "calloutCard", "props": {"content": s2}},
            {"component_type": "bulletList", "props": {"items": ["- **عنوان**: نص تفصيلي طويل بما يكفي ليكون بندا"]}},
        ]

    md = "# T\n\n" + "\n\n".join(f"## S{i}\n\n{body}" for i in range(4))
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    text = "\n".join(s for c in out for s in _all_strings(c.props))
    assert "**" not in text
    assert text.count("تشير الفكرة") == 4  # once per section
    assert text.count("نص تفصيلي طويل") == 4


# --- t_48d8a80a review 2: no text may be lost or changed ------------------------------------------

def test_markers_never_eat_comparison_symbols_or_short_values():
    from section_orchestrator import strip_inline_markdown

    for kept in ("> 10 مليون", "+ 5%", "- 3", "# of users", "> 5", "- 3 items left", "- 3"):
        assert strip_inline_markdown(kept) == kept
    assert strip_inline_markdown("> a quoted prose line here") == "a quoted prose line here"


def test_markers_stripped_on_every_line_and_bold_spans_newlines():
    from section_orchestrator import strip_inline_markdown

    assert strip_inline_markdown("- first prose line\n- second prose line") == "first prose line\nsecond prose line"
    assert strip_inline_markdown("**bold across\nlines** after") == "bold across\nlines after"


def test_urls_and_machine_keys_are_untouched():
    from section_orchestrator import clean_component, strip_inline_markdown

    assert strip_inline_markdown("https://a.b/__init__/x") == "https://a.b/__init__/x"
    assert strip_inline_markdown("see **https://a.b/__init__** now") == "see https://a.b/__init__ now"
    props = {"videoUrl": "https://v.x/__a__", "thumbnailUrl": "https://v.x/__t__", "videoId": "__id__",
             "href": "/__x__", "src": "/__y__", "title": "**T**"}
    out = clean_component(_comp("videoEmbed", **props)).props
    assert {k: out[k] for k in props if k != "title"} == {k: v for k, v in props.items() if k != "title"}
    assert out["title"] == "T"


def test_stat_values_keep_leading_symbols():
    from section_orchestrator import clean_component

    out = clean_component(_comp("statCard", value="> 10 million users here", label="- a label of words", change="+ 5%"))
    assert out.props["value"] == "> 10 million users here" and out.props["change"] == "+ 5%"
    assert out.props["label"] == "a label of words"


def test_repeat_match_respects_word_boundaries():
    from section_orchestrator import drop_repeated_prose

    a = _comp("calloutCard", title="", content="5% growth in 2024 vs prior")
    b = _comp("calloutCard", title="", content="15% growth in 2024 vs prior")
    assert drop_repeated_prose([a, b]) == [a, b]
    c = _comp("calloutCard", title="", content="نمو ٥٪ في عام ٢٠٢٤ مقارنة")
    d = _comp("calloutCard", title="", content="نمو ١٥٪ في عام ٢٠٢٤ مقارنة")
    assert drop_repeated_prose([c, d]) == [c, d]
    whole = _comp("calloutCard", title="", content="Revenue: 5% growth in 2024 vs prior, then more")
    assert drop_repeated_prose([a, whole]) == [whole]


def test_repeat_haystack_ignores_non_visible_props():
    from section_orchestrator import drop_repeated_prose

    prose = _comp("calloutCard", title="", content="a long enough sentence here")
    other = _comp("linkPreview", url="a long enough sentence here", title="x")
    assert drop_repeated_prose([prose, other]) == [prose, other]


def test_empty_drop_keeps_components_with_any_visible_text():
    from section_orchestrator import drop_empty_components

    head = _comp("headlineCard", title="", summary="summary text kept")
    callout = _comp("calloutCard", title="Title kept", content="")
    blank = _comp("headlineCard", title=" ", summary="")
    assert drop_empty_components([head, callout, blank]) == [head, callout]


FIXTURE = (
    "/Volumes/Storage 4TB SSD/Documents2/GitHub/startup45-WebApp-v1/.workspace/project-management/reports/"
    "e2e-free-tool-2026-10-16/analyze-run3-edited.md"
)


@pytest.mark.asyncio
async def test_every_source_number_survives_the_cleanup_pipeline():
    import os
    import re

    if not os.path.exists(FIXTURE):
        pytest.skip("fixture report not available")
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    md = open(FIXTURE, encoding="utf-8").read()
    _, sections = split_report(md)

    async def select(analysis, layout, text, extra):
        # LLM mock: split the section into sentence-sized components copying text (markdown included).
        specs = []
        for _, item in extract_source_items(text.split("\n\n", 1)[-1]):
            sentences = [s for s in re.split(r"(?<=[.؟!])\s+", item) if s.strip()]
            for i, s in enumerate(sentences):
                kind = ("headlineCard", "calloutCard", "keyTakeaways", "bulletList")[i % 4]
                props = {
                    "headlineCard": {"title": s}, "calloutCard": {"content": f"**{s}**"},
                    "keyTakeaways": {"items": [f"- {s}"]}, "bulletList": {"items": [{"text": s}]},
                }[kind]
                specs.append({"component_type": kind, "props": props})
            specs.append({"component_type": "tldr", "props": {"content": item}})  # whole-item repeat
        return specs or [{"component_type": "tldr", "props": {"content": ""}}]

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    visible = " ".join(s for c in out for s in _all_strings({k: v for k, v in c.props.items() if k not in ("url",)}))
    numbers = re.findall(r"\d[\d,.]*%?|[٠-٩][٠-٩,.٫]*٪?", md)
    assert numbers, "fixture should contain numbers"
    missing = [n for n in set(numbers) if n.strip(",.") not in visible]
    assert not missing, missing


# --- text-loss root cause: partial token overlap counted as "covered" -----------------------------

S1 = "الفكرة واضحة نسبيًا؛ فهي تقدم خدمة تصميم مواقع احترافية."
S2 = "وتتمثل المشكلة المستهدفة في تحسين الحضور الرقمي للشركات الاستشارية."
S3 = "ومع ذلك، قد يكون من الأفضل توضيح ميزات الخدمة التنافسية."


def _gap_text(body: str, built: list[A2UIComponent]) -> str:
    return "\n".join(s for c in coverage_components(body, built) for s in _all_strings(c.props))


def test_partially_covered_paragraph_is_gap_filled_whole():
    # The component holds sentences 2-3 (2/3 of the tokens, above the old 0.6 threshold): sentence 1 was lost.
    gap = _gap_text(f"الوضوح:\n{S1} {S2} {S3}", [_comp("calloutCard", title="الوضوح:", content=f"{S2} {S3}")])
    assert S1 in gap


def test_label_line_missing_from_components_is_gap_filled():
    body = "التحديد:\nالعملاء المستهدفون محددون جيدًا؛ إذ تركز الفكرة على مكاتب المحاماة والشركات الاستشارية في السعودية."
    built = [_comp("keyTakeaways", items=["العملاء المستهدفون محددون جيدًا؛ إذ تركز الفكرة على مكاتب المحاماة والشركات الاستشارية في السعودية."])]
    assert "التحديد" in _gap_text(body, built)


def test_label_inside_a_longer_sentence_does_not_count_as_covered():
    gap = _gap_text("مؤشرات الجدوى:\nنص", [_comp("tldr", content="تظهر مؤشرات الجدوى أولية واعدة ونص")])
    assert "مؤشرات الجدوى" in gap


def test_exact_text_in_one_component_is_not_duplicated():
    body = f"الوضوح:\n{S1}\n\n- {S2}"
    built = [_comp("headlineCard", title="الوضوح:"), _comp("calloutCard", content=S1), _comp("bulletList", items=[{"text": S2}])]
    assert coverage_components(body, built) == []


def test_label_merged_into_a_component_with_its_body_is_covered():
    body = f"مؤشرات الجدوى:\n{S1}"
    assert coverage_components(body, [_comp("bulletList", items=[{"text": f"مؤشرات الجدوى: {S1}"}])]) == []


# --- repeated verdict / heading echo ---------------------------------------------------------------

def test_component_whose_whole_text_repeats_another_is_dropped_keeping_first():
    from section_orchestrator import drop_duplicate_components

    head = _comp("headlineCard", title="واعد", summary="")
    echo = _comp("calloutCard", type="info", title="", content="واعد")
    long = _comp("tldr", content="ملخص طويل لا يتكرر")
    assert drop_duplicate_components([head, long, echo]) == [head, long]
    assert drop_duplicate_components([echo, head]) == [echo]


def test_component_repeating_the_section_heading_is_dropped_and_distinct_text_kept():
    from section_orchestrator import drop_duplicate_components

    heading = _comp("heading", text="التقييم المبدئي", level=2)
    echo = _comp("headlineCard", title="التقييم المبدئي")
    other = _comp("calloutCard", title="", content="نص مختلف")
    assert drop_duplicate_components([heading, echo, other]) == [heading, other]


# --- trailing colon on headings / labels only --------------------------------------------------------

def test_trailing_colon_stripped_from_headings_and_labels_not_from_prose():
    from section_orchestrator import clean_component

    assert clean_component(_comp("heading", text="مؤشرات الجدوى:", level=3)).props["text"] == "مؤشرات الجدوى"
    assert clean_component(_comp("headlineCard", title="الوضوح：", summary="ملخص:")).props == {"title": "الوضوح", "summary": "ملخص:"}
    assert clean_component(_comp("stepCard", title="الاكتمال:", description="مثل:")).props == {"title": "الاكتمال", "description": "مثل:"}
    assert clean_component(_comp("calloutCard", title="تنبيه:", content="يشمل ما يلي:")).props == {"title": "تنبيه", "content": "يشمل ما يلي:"}
    assert clean_component(_comp("statCard", label="الحجم:", value="5:")).props == {"label": "الحجم", "value": "5:"}
    assert clean_component(_comp("tldr", content="نص ينتهي بنقطتين:")).props["content"] == "نص ينتهي بنقطتين:"


# --- property: every source LINE survives, lossy LLM included -----------------------------------------

def _line_key(text: str) -> str:
    import re
    text = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", "", text.strip())
    text = re.sub(r"[*_`]", "", text)
    return " ".join(text.rstrip(":：").split())


def _source_lines(md: str) -> list[str]:
    import re
    out = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith("|") or re.fullmatch(r"[-*_=\s]+", line):
            continue
        key = _line_key(line)
        if re.search(r"\w", key):
            out.append(key)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["analyze-run3-edited.md", "analyze-run5-edited.md"])
async def test_every_source_line_survives_a_lossy_llm(name):
    import os
    import re

    path = FIXTURE.replace("analyze-run3-edited.md", name)
    if not os.path.exists(path):
        pytest.skip("fixture report not available")
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    md = open(path, encoding="utf-8").read()

    async def select(analysis, layout, text, extra):
        # Lossy LLM mock: per item it silently drops the label line, or the first sentence, or the whole item.
        specs = []
        for n, (_, item) in enumerate(extract_source_items(text.split("\n\n", 1)[-1])):
            sentences = [s for s in re.split(r"(?<=[.؟!])\s+", item) if s.strip()]
            if n % 3 == 0 and len(sentences) > 1:
                sentences = sentences[1:]
            elif n % 3 == 1:
                continue
            specs.append({"component_type": "calloutCard", "props": {"title": "", "content": " ".join(sentences)}})
        return specs or [{"component_type": "tldr", "props": {"content": ""}}]

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    visible = _line_key(" ".join(s for c in out for s in _all_strings({k: v for k, v in c.props.items() if k != "url"})))
    visible_cells = " ".join(visible.split())
    missing = [ln for ln in _source_lines(md) if ln not in visible_cells]
    assert not missing, missing


# --- property (sentence level): no source sentence is lost, whether the LLM paraphrases or splits cards -----

_DIAC = "[\u0610-\u061a\u064b-\u065f\u0670\u0640\u06d6-\u06ed]"


def _fold_independent(text: str) -> str:
    """Folding written independently of the pipeline (diacritics, punctuation, digits, markers, spacing)."""
    import re
    text = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", "", text.strip()).replace("*", "").replace("`", "")
    text = re.sub(_DIAC, "", text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def _lost_sentences(md: str, out: list) -> list[str]:
    """Source sentences (markers stripped before splitting) absent from every single component's text (label + body together)."""
    import re
    texts = [" " + _fold_independent(" ".join(_all_strings({k: v for k, v in c.props.items() if k != "url"}))) + " " for c in out]
    lost = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith("|") or re.fullmatch(r"[-*_=\s]+", line):
            continue
        line = line.replace("*", "").replace("`", "")
        for _ in range(2):  # "### 1. Heading": heading marker, then list number
            line = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", "", line)
        for sentence in re.split(r"(?<=[.\u061f?!\u061b;])\s+", line):
            key = _fold_independent(sentence)
            if key and not any(f" {key} " in t for t in texts):
                lost.append(sentence[:80])
    return lost


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["analyze-run3-edited.md", "analyze-run5-edited.md"])
@pytest.mark.parametrize("mode", ["split_per_sentence", "paraphrase", "split_label_body"])
async def test_no_source_sentence_is_lost_when_the_llm_splits_or_paraphrases(name, mode):
    import os
    import re

    path = FIXTURE.replace("analyze-run3-edited.md", name)
    if not os.path.exists(path):
        pytest.skip("fixture report not available")
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    md = open(path, encoding="utf-8").read()

    async def select(analysis, layout, text, extra, allow_empty=False):
        specs = []
        for _, item in extract_source_items(text.split("\n\n", 1)[-1]):
            sentences = [x for x in re.split(r"(?<=[.؟!])\s+", item) if x.strip()]
            if mode == "split_per_sentence":  # one card per sentence, punctuation and diacritics changed
                specs += [{"component_type": "calloutCard", "props": {"title": "", "content": x.rstrip(".").replace("،", ",")}} for x in sentences]
            elif mode == "paraphrase":  # reworded text that is not in the source
                specs.append({"component_type": "calloutCard", "props": {"title": "", "content": "ملخص بصياغة مختلفة: " + " ".join(sentences[::-1][:1]).replace("ال", "")}})
            else:  # "**label**: body" split into a title and a description
                head, _, body = item.partition(":")
                specs.append({"component_type": "stepCard", "props": {"stepNumber": 1, "title": head.strip(), "description": body.strip() or head}})
        return specs or [{"component_type": "tldr", "props": {"content": ""}}]

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    assert not _lost_sentences(md, out), _lost_sentences(md, out)


# --- round 3: untraceable text, step/bullet echo, standalone labels, side-by-side rows ----------------

V1 = "ينطوي استهداف مكاتب المحاماة والشركات الاستشارية بخدمة تصميم مواقع احترافية على إمكانات قوية."
V2 = "يتطلب هذا القطاع حضورًا رقميًا متميزًا وخدمات مخصصة لضمان فرص النجاح."


def test_component_whose_text_is_not_in_the_source_is_dropped_and_gap_fill_restores_the_source():
    from section_orchestrator import drop_untraceable_components

    body = f"واعد\n\n{V1} {V2}"
    paraphrase = _comp("calloutCard", title="", content="يمتلك هذا الاستهداف إمكانات كبيرة جدا في السوق السعودي الواعد")
    verbatim = _comp("tldr", content=f"{V1} {V2}")
    label = _comp("headlineCard", title="واعد", summary="")
    kept = drop_untraceable_components([label, paraphrase, verbatim], body)
    assert kept == [label, verbatim]
    # nothing traceable built -> gap-fill carries the whole source text
    assert V1 in _gap_text(body, drop_untraceable_components([paraphrase], body))


def test_traceable_text_survives_markdown_and_hard_wraps():
    from section_orchestrator import drop_untraceable_components

    body = f"- **{V1}**\n  {V2}"
    c = _comp("calloutCard", title="", content=f"{V1} {V2}")
    assert drop_untraceable_components([c], body) == [c]


def test_callout_echoing_headline_and_summary_is_dropped():
    from section_orchestrator import drop_repeated_prose

    head = _comp("headlineCard", title="واعد", summary="")
    summary = _comp("tldr", content=f"{V1} {V2}")
    echo = _comp("calloutCard", title="واعد", content=V1)
    assert drop_repeated_prose([head, summary, echo]) == [head, summary]


def test_step_cards_inside_the_bullet_that_follows_are_dropped():
    from section_orchestrator import drop_steps_echoed_by_lists

    s1 = _comp("stepCard", stepNumber=1, title="إجراء مقابلات مع عملاء", description="تحدث مع مكاتب المحاماة لفهم احتياجاتها.")
    s2 = _comp("stepCard", stepNumber=2, title="تطوير نموذج أولي", description="أنشئ تصميمًا أوليًا للموقع.")
    bullets = _comp("bulletList", items=[
        {"text": "إجراء مقابلات مع عملاء: تحدث مع مكاتب المحاماة لفهم احتياجاتها. واستفسر عن التحديات."},
        {"text": "تطوير نموذج أولي: أنشئ تصميمًا أوليًا للموقع. واستخدمه لجمع الملاحظات."},
    ])
    assert drop_steps_echoed_by_lists([s1, s2, bullets]) == [bullets]
    assert drop_steps_echoed_by_lists([bullets, s1, s2]) == [bullets]


def test_bullet_list_adding_nothing_over_the_step_cards_is_dropped():
    from section_orchestrator import drop_steps_echoed_by_lists

    s1 = _comp("stepCard", stepNumber=1, title="إجراء مقابلات مع عملاء", description="تحدث مع مكاتب المحاماة لفهم احتياجاتها.")
    s2 = _comp("stepCard", stepNumber=2, title="تطوير نموذج أولي", description="أنشئ تصميمًا أوليًا للموقع.")
    bullets = _comp("bulletList", items=[
        {"text": "إجراء مقابلات مع عملاء: تحدث مع مكاتب المحاماة لفهم احتياجاتها."},
        {"text": "تطوير نموذج أولي: أنشئ تصميمًا أوليًا للموقع."},
    ])
    assert drop_steps_echoed_by_lists([s1, s2, bullets]) == [s1, s2]


def test_step_and_list_matching_is_word_bounded_and_needs_adjacency():
    from section_orchestrator import drop_steps_echoed_by_lists

    step = _comp("stepCard", stepNumber=1, title="خطة", description="أنشئ صفحة")
    near = _comp("bulletList", items=[{"text": "خطة: أنشئ صفحة الهبوط"}])
    split = _comp("bulletList", items=[{"text": "خطة التسويق: أنشئ صفحة الهبوط"}])
    same = _comp("bulletList", items=[{"text": "خطة: أنشئ صفحة"}])
    sep = _comp("heading", text="عنوان", level=2)
    assert drop_steps_echoed_by_lists([step, near]) == [near]
    assert drop_steps_echoed_by_lists([step, same]) == [step]
    assert drop_steps_echoed_by_lists([step, sep, near]) == [step, sep, near]
    assert drop_steps_echoed_by_lists([step, split]) == [step, split]  # words not contiguous
    unrelated = _comp("stepCard", stepNumber=1, title="خطة", description="أنش")  # substring of a word only
    assert drop_steps_echoed_by_lists([unrelated, near]) == [unrelated, near]


def test_standalone_label_from_gap_fill_has_no_trailing_colon():
    body = f"العملاء المحتملون:\n\n{V1}\n\nإشارات الطلب：\n\nمثال: يشمل ما يلي، وهذه جملة طويلة تنتهي بنقطتين:"
    texts = [i["text"] for c in coverage_components(body, [_comp("tldr", content=V1)]) for i in c.props["items"]]
    assert "العملاء المحتملون" in texts and "إشارات الطلب" in texts
    assert all(t != "العملاء المحتملون:" for t in texts)
    # a label that opens its own paragraph keeps its colon (it is prose there)
    kept = [i["text"] for c in coverage_components("الوضوح:\nنص الفقرة الأولى هنا", []) for i in c.props["items"]]
    assert kept == ["الوضوح: نص الفقرة الأولى هنا"]
    # longer than five words: untouched
    long_label = "هذا سطر طويل من ستة كلمات تماما:"
    assert [i["text"] for c in coverage_components(long_label, []) for i in c.props["items"]] == [long_label]


def _short(type_: str, text: str = "نص قصير") -> A2UIComponent:
    key = {"statCard": "label", "calloutCard": "content", "headlineCard": "title", "stepCard": "title"}[type_]
    return _comp(type_, **{key: text})


def _groups(components: list[A2UIComponent]) -> list[str | None]:
    return [(c.layout or {}).get("group") for c in components]


def test_runs_of_short_cards_get_one_row_group_each():
    from section_orchestrator import group_short_rows

    three = [_short("statCard"), _short("statCard"), _short("statCard")]
    pair = [_short("statCard"), _short("statCard")]
    heading = _comp("heading", text="عنوان", level=2)
    out = group_short_rows([*three, heading, *pair], "s2")
    g = _groups(out)
    assert g[0] == g[1] == g[2] and g[0]
    assert g[3] is None
    assert g[4] == g[5] and g[4] and g[4] != g[0]
    assert [c.type for c in out] == [c.type for c in [*three, heading, *pair]]  # order untouched


def test_long_or_prose_components_break_a_row_and_lone_cards_stay_ungrouped():
    from section_orchestrator import group_short_rows

    long_callout = _short("calloutCard", "كلمة " * 100)
    out = group_short_rows([_short("statCard"), long_callout, _short("statCard"), _short("statCard"), _comp("bulletList", items=[{"text": "x"}]), _short("calloutCard")], "s1")
    g = _groups(out)
    assert g[0] is None and g[1] is None and g[2] == g[3] and g[2] and g[4] is None and g[5] is None


def test_rows_hold_at_most_four_cards_and_never_leave_a_single():
    from section_orchestrator import group_short_rows

    out = group_short_rows([_short("statCard") for _ in range(5)], "s1")
    sizes = sorted(len([c for c in out if (c.layout or {}).get("group") == g]) for g in set(_groups(out)))
    assert sizes == [2, 3]
    out = group_short_rows([_short("statCard") for _ in range(4)], "s1")
    assert len(set(_groups(out))) == 1


def test_web_non_composable_types_are_never_grouped():
    from section_orchestrator import group_short_rows

    out = group_short_rows([_comp("vsCard", left="أ", right="ب"), _comp("vsCard", left="أ", right="ب"), _comp("bulletList", items=[{"text": "x"}]), _short("statCard")], "s1")
    assert _groups(out) == [None, None, None, None]


def _half(type_: str, chars: int, **props: object) -> A2UIComponent:
    c = _comp(type_, **props) if props else _comp(type_, content="كلمة " * (chars // 5))
    c.layout = {"width": "full"}  # the selector stamps "full" on nearly every card; rows must not depend on it
    return c


def test_run7_section_shape_untitled_takeaways_and_titled_callout_are_not_paired():
    """Run 7 (clip 3182): an untitled keyTakeaways next to a titled calloutCard looked broken; they stay full width."""
    from section_orchestrator import group_short_rows

    label = _comp("headlineCard", title="تقييم وصف الفكرة", summary="")
    takeaways = _half("keyTakeaways", 0, title="", items=["كلمة " * 38])
    callout = _half("calloutCard", 0, type="info", title="مؤشرات الجدوى", content="كلمة " * 60)
    bullets = _comp("bulletList", items=[{"text": "تسعير"}])
    out = group_short_rows([label, takeaways, callout, bullets], "s2")
    g = _groups(out)
    assert g == [None, None, None, None]
    assert [c.type for c in out] == ["headlineCard", "keyTakeaways", "calloutCard", "bulletList"]


def test_long_half_cards_hold_at_most_three_per_row_without_a_lone_card():
    from section_orchestrator import group_short_rows

    for n, sizes in ((3, [3]), (4, [2, 2]), (5, [2, 3]), (7, [2, 2, 3])):
        out = group_short_rows([_half("calloutCard", 400) for _ in range(n)], "s1")
        counts = sorted(len([c for c in out if (c.layout or {}).get("group") == g]) for g in set(_groups(out)))
        assert counts == sizes, (n, counts)


def test_a_long_prose_card_and_a_heading_break_the_row_and_width_hint_is_ignored():
    from section_orchestrator import group_short_rows

    long_full = _comp("calloutCard", content="كلمة " * 100)
    heading = _comp("heading", text="ع", level=2)
    out = group_short_rows([_short("statCard"), long_full, _short("statCard"), heading, _short("statCard"), _short("statCard")], "s1")
    g = _groups(out)
    assert g[:4] == [None] * 4 and g[4] == g[5] and g[4]


def test_label_card_is_never_a_row_member_but_a_news_headline_with_summary_is():
    from section_orchestrator import group_short_rows

    news = lambda: _comp("headlineCard", title="عنوان", summary="ملخص")  # noqa: E731
    out = group_short_rows([_comp("headlineCard", title="شدة المشكلة", summary=""), news(), news()], "s1")
    assert _groups(out) == [None, "s1-row-1", "s1-row-1"]


SEC_BODY = (
    "فرصة السوق\n\nتتمثل الفجوة في الحاجة المتزايدة إلى حضور قوي على الإنترنت.\n\n"
    "العملاء المحتملون\n\nالعملاء هم مكاتب المحاماة والشركات الاستشارية في السعودية. وتناسبهم الخدمة لاعتيادهم على الاستشارات.\n\n"
    "إشارات الطلب\n\nمن المؤشرات الأولية نمو عدد الشركات القانونية والاستشارية في السعودية. كما يزداد تنافس هذه المؤسسات على الظهور الرقمي.\n\n"
    "لماذا الآن\n\nيعد التوقيت مناسبا في ظل التحول الرقمي المتسارع في السعودية."
)


def test_callout_label_left_behind_by_a_moved_survivor_is_placed_before_its_body():
    """Run 7 live shape: a whole-section list absorbed the fragments and moved up; 'إشارات الطلب' trailed it."""
    from section_orchestrator import place_labels_before_bodies

    body_line = "من المؤشرات الأولية نمو عدد الشركات القانونية والاستشارية في السعودية. كما يزداد تنافس هذه المؤسسات على الظهور الرقمي."
    survivor = _comp("bulletList", items=[
        {"text": "تتمثل الفجوة في الحاجة المتزايدة إلى حضور قوي على الإنترنت."},
        {"text": "العملاء هم مكاتب المحاماة والشركات الاستشارية في السعودية. وتناسبهم الخدمة لاعتيادهم على الاستشارات."},
        {"text": body_line},
        {"text": "لماذا الآن"},
        {"text": "يعد التوقيت مناسبا في ظل التحول الرقمي المتسارع في السعودية."},
    ])
    comps = [
        _comp("headlineCard", title="فرصة السوق", summary=""),
        survivor,
        _comp("calloutCard", type="info", title="إشارات الطلب", content="من المؤشرات الأولية نمو عدد الشركات"),
    ]
    out = place_labels_before_bodies(comps, SEC_BODY)
    assert [c.type for c in out] == ["headlineCard", "bulletList"]
    texts = [i["text"] for i in out[1].props["items"]]
    assert texts.index("إشارات الطلب") + 1 == texts.index(body_line)


def test_label_headline_trailing_the_list_that_holds_its_body_is_moved_into_it():
    from section_orchestrator import place_labels_before_bodies

    body = "المشكلة الجوهرية\n\nتعاني مكاتب المحاماة من غياب مواقع احترافية فعالة.\n\nشدة المشكلة\n\nتكتسب هذه المشكلة أهمية كبيرة لدى الشريحة المستهدفة."
    comps = [
        _comp("bulletList", items=[{"text": "تعاني مكاتب المحاماة من غياب مواقع احترافية فعالة."}, {"text": "تكتسب هذه المشكلة أهمية كبيرة لدى الشريحة المستهدفة."}]),
        _comp("headlineCard", title="شدة المشكلة", summary=""),
    ]
    out = place_labels_before_bodies(comps, body)
    assert [c.type for c in out] == ["bulletList"]
    assert [i["text"] for i in out[0].props["items"]][1] == "شدة المشكلة"


def test_label_headline_before_its_body_and_label_over_a_non_list_owner():
    from section_orchestrator import place_labels_before_bodies

    body = "الوضوح\n\nالفكرة واضحة عمومًا وتقدم خدمة محددة للعملاء."
    ok = [_comp("headlineCard", title="الوضوح", summary=""), _comp("tldr", content="الفكرة واضحة عمومًا وتقدم خدمة محددة للعملاء.")]
    assert place_labels_before_bodies(ok, body) == ok
    late = [ok[1], ok[0]]
    assert [c.type for c in place_labels_before_bodies(late, body)] == ["headlineCard", "tldr"]


@pytest.mark.asyncio
async def test_orchestrated_section_groups_rows_and_stays_lossless():
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    md = "# T\n\n" + "\n\n".join(
        f"## قسم {i}\n\nالنص الأول {i}\n\nالنص الثاني {i}\n\nالنص الثالث {i}" for i in range(1, 5)
    )

    async def select(analysis, layout, text, extra):
        return [{"component_type": "calloutCard", "props": {"title": "", "content": ln}} for ln in text.split("\n\n")[1:] if ln.strip()]

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    grouped = [c for c in out if (c.layout or {}).get("group")]
    assert grouped
    ids = {c.layout["group"] for c in grouped}
    assert len(ids) == 4 and all(g.startswith("s") and "-row-" in g for g in ids)
    visible = " ".join(s for c in out for s in _all_strings(c.props))
    assert all(f"النص {w} {i}" in visible for i in range(1, 5) for w in ("الأول", "الثاني", "الثالث"))


@pytest.mark.asyncio
async def test_step_cards_echoing_a_gap_filled_list_are_dropped_in_the_pipeline():
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    t1, d1 = "إجراء مقابلات مع عملاء محتملين", "تحدث مع مكاتب المحاماة لفهم احتياجاتها في تصميم المواقع."
    md = "# T\n\n" + "\n\n".join(f"## قسم {i}\n\nنص عادي رقم {i} للقسم" for i in range(1, 4)) + \
        f"\n\n## الخطوات\n\n- {t1}: {d1} واستفسر عن التحديات التي تواجهها."

    async def select(analysis, layout, text, extra):
        if "الخطوات" in text:
            return [{"component_type": "stepCard", "props": {"stepNumber": 1, "title": t1, "description": d1}}]
        return [{"component_type": "tldr", "props": {"content": "x"}}]

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    assert not [c for c in out if c.type == "stepCard"]
    assert t1 in " ".join(s for c in out for s in _all_strings(c.props))


@pytest.mark.asyncio
async def test_run7_pipeline_keeps_labels_before_bodies_pairs_half_cards_and_loses_no_line():
    """Selector echoes the run-7 failure: fragments + a label card; the whole-section gap-fill moves up."""
    from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

    md = "# T\n\n" + "\n\n".join(f"## قسم {i}\n\nنص عادي رقم {i} للقسم" for i in range(1, 4)) + "\n\n## فرصة السوق والعملاء المحتملون\n\n" + SEC_BODY

    async def select(analysis, layout, text, extra):
        if "إشارات الطلب" not in text:
            return [{"component_type": "tldr", "props": {"content": ln}} for ln in text.split("\n\n")[1:] if ln.strip()]
        return [
            {"component_type": "headlineCard", "props": {"title": "فرصة السوق", "summary": ""}},
            {"component_type": "keyTakeaways", "props": {"title": "", "items": ["العملاء هم مكاتب المحاماة والشركات الاستشارية في السعودية."]}, "layout": {"width": "half"}},
            {"component_type": "calloutCard", "props": {"type": "info", "title": "إشارات الطلب", "content": "من المؤشرات الأولية نمو عدد الشركات القانونية والاستشارية في السعودية."}, "layout": {"width": "half"}},
        ]

    out = [c async for c in orchestrate_sections(md, select, build_a2ui_component, expand_component_specs, apply_layout_and_zone)]
    visible = _line_key(" ".join(s for c in out for s in _all_strings({k: v for k, v in c.props.items() if k != "url"})))
    assert [ln for ln in _source_lines(md) if ln not in " ".join(visible.split())] == []
    flat = [(c.type, _all_strings(c.props)) for c in out]
    label_at = next(n for n, (t, st) in enumerate(flat) if "إشارات الطلب" in st)
    body_at = next(n for n, (t, st) in enumerate(flat) if any("من المؤشرات الأولية" in x for x in st))
    assert label_at <= body_at


# --- round 4: normalised traceability, sentence-level coverage, compatible rows -------------------------

def test_traceability_survives_diacritics_punctuation_digits_and_list_markers():
    from section_orchestrator import drop_untraceable_components

    body = "- الفكرة واضحة نسبيًا؛ فهي تقدم خدمة تصميم مواقع احترافية.\n- ينمو القطاع بنسبة ٥٠٪ سنويا في «السعودية» اليوم."
    variants = [
        "الفكرة واضحة نسبيا، فهي تقدم خدمة تصميم مواقع احترافية",  # no diacritics, ، for ؛, no final dot
        "الفكـــرة واضحة نسبيًا؛ فهي تقدم خدمة تصميم مواقع احترافية.",  # tatweel
        "ينمو القطاع بنسبة 50٪ سنويا في \"السعودية\" اليوم",  # Latin digits, plain quotes
        "الفكرة واضحة نسبيًا؛   فهي تقدم خدمة تصميم مواقع احترافية. ينمو القطاع بنسبة ٥٠٪ سنويا في «السعودية» اليوم.",  # two items re-joined
    ]
    for text in variants:
        c = _comp("calloutCard", title="", content=text)
        assert drop_untraceable_components([c], body) == [c], text


def test_a_real_paraphrase_is_still_dropped():
    from section_orchestrator import drop_untraceable_components

    body = "- الفكرة واضحة نسبيًا؛ فهي تقدم خدمة تصميم مواقع احترافية."
    para = _comp("calloutCard", title="", content="تبدو الفكرة جلية وتقدم خدمة لتصميم المواقع بشكل محترف")
    assert drop_untraceable_components([para], body) == []


def test_sentences_split_across_cards_count_as_covered_and_a_line_with_a_missing_sentence_is_filled_whole():
    body = f"{S1} {S2} {S3}"
    split = [_comp("headlineCard", title=S1, summary=""), _comp("calloutCard", title="", content=S2), _comp("tldr", content=S3)]
    assert coverage_components(body, split) == []
    gap = coverage_components(body, split[:2])
    assert [i["text"] for i in gap[0].props["items"]] == [body]


def test_diacritic_variants_count_as_covered():
    body = f"{S1}"
    variant = _comp("calloutCard", title="", content=S1.replace("ً", "").replace("؛", "،"))
    assert coverage_components(body, [variant]) == []


def _titled(type_: str, text: str = "نص قصير", title: str = "عنوان") -> A2UIComponent:
    key = {"statCard": "label", "calloutCard": "content", "keyTakeaways": "items"}[type_]
    props = {key: [text] if type_ == "keyTakeaways" else text}
    if type_ == "statCard":
        props["value"] = "42"
    else:
        props["title"] = title
    return _comp(type_, **props)


def _untitled(type_: str, text: str = "نص قصير") -> A2UIComponent:
    c = _titled(type_, text, title="")
    if type_ == "statCard":
        c.props["label"] = ""
    return c


def test_titled_and_untitled_cards_are_never_paired():
    from section_orchestrator import group_short_rows

    out = group_short_rows([_titled("calloutCard"), _untitled("calloutCard")], "s1")
    assert _groups(out) == [None, None]
    out = group_short_rows([_titled("calloutCard"), _titled("calloutCard", title="آخر")], "s1")
    assert _groups(out) == ["s1-row-1", "s1-row-1"]


def test_stat_cards_pair_with_stat_cards_and_text_cards_with_text_cards_only():
    from section_orchestrator import group_short_rows

    out = group_short_rows([_titled("statCard", "أ"), _titled("statCard", "ب"), _titled("calloutCard"), _titled("keyTakeaways")], "s1")
    g = _groups(out)
    assert g[0] == g[1] == "s1-row-1" and g[2] == g[3] == "s1-row-2"
    out = group_short_rows([_titled("statCard", "أ"), _titled("calloutCard")], "s1")
    assert _groups(out) == [None, None]


def test_row_members_have_similar_length_longest_at_most_twice_shortest():
    from section_orchestrator import group_short_rows

    short, mid, longer = _titled("calloutCard", "كلمة " * 10), _titled("calloutCard", "كلمة " * 15), _titled("calloutCard", "كلمة " * 40)
    out = group_short_rows([short, mid, longer], "s1")
    assert _groups(out) == ["s1-row-1", "s1-row-1", None]
    out = group_short_rows([_titled("calloutCard", "كلمة " * 10), _titled("calloutCard", "كلمة " * 40)], "s1")
    assert _groups(out) == [None, None]


def test_title_only_headline_card_never_joins_a_row():
    from section_orchestrator import group_short_rows

    label = _comp("headlineCard", title="عنوان", summary="")
    out = group_short_rows([label, _titled("calloutCard"), _titled("calloutCard", title="آخر")], "s1")
    assert _groups(out) == [None, "s1-row-1", "s1-row-1"]


def test_partly_carried_line_gets_only_its_missing_sentence_inside_the_neighbouring_card():
    from section_orchestrator import place_partial_lines

    body = f"{S1} {S2} {S3}"
    card = _comp("calloutCard", type="info", title="", content=f"{S1} {S2}")
    other = _comp("statCard", label="x", value="1")
    out = place_partial_lines(body, [card, other])
    assert [c.type for c in out] == ["calloutCard", "statCard"]
    assert out[0].props["content"] == f"{S1} {S2} {S3}"  # contiguous, cards kept, nothing duplicated
    assert coverage_components(body, out) == []


def test_missing_first_sentence_is_prepended_and_unsplittable_card_gets_a_bullet_beside_it():
    from section_orchestrator import place_partial_lines

    body = f"{S1} {S2} {S3}"
    out = place_partial_lines(body, [_comp("calloutCard", type="info", title="", content=f"{S2} {S3}")])
    assert [c.type for c in out] == ["calloutCard"] and out[0].props["content"] == f"{S1} {S2} {S3}"
    stat = _comp("statCard", label=S2, value="9")  # not a prose card: gets a bulletList next to it
    out = place_partial_lines(f"{S1} {S2}", [stat])
    assert [c.type for c in out] == ["bulletList", "statCard"] and out[0].props["items"] == [{"text": S1}]


def test_untouched_lines_are_left_to_the_gap_fill_and_a_missing_label_card_precedes_its_body():
    from section_orchestrator import place_partial_lines

    card = _comp("calloutCard", type="info", title="", content=S1)
    assert place_partial_lines(f"{S2}\n\n{S3}", [card]) == [card]
    out = place_partial_lines(f"الوضوح\n{S1}", [card])
    assert [c.type for c in out] == ["headlineCard", "calloutCard"] and out[0].props["title"] == "الوضوح"


def test_a_label_card_is_not_dropped_as_a_repeat_of_the_paragraph_that_contains_its_words():
    from section_orchestrator import drop_repeated_prose

    label = _comp("headlineCard", title="فرصة التميّز", summary="")
    para = _comp("tldr", content="تتمثل فرصة التميّز في تقديم خدمة متكاملة لتصميم المواقع الاحترافية.")
    assert drop_repeated_prose([label, para]) == [label, para]


def test_variant_wording_is_snapped_back_to_the_exact_source_text():
    from section_orchestrator import snap_to_source

    body = f"الوضوح:\n{S1} {S2}\n- ينمو القطاع بنسبة ٥٠٪ سنويا."
    variant = _comp("calloutCard", type="info", title="الوضوح", content=S1.replace("ً", "").replace("؛", "،").rstrip("."))
    both = _comp("tldr", content=f"{S1} {S2}".replace("ً", ""))
    digits = _comp("keyTakeaways", title="", items=["ينمو القطاع بنسبة 50٪ سنويا"])
    other = _comp("tldr", content="نص آخر لا علاقة له بالمصدر أبدا")
    snap_to_source([variant, both, digits, other], body)
    assert variant.props["content"] == S1 and variant.props["title"] == "الوضوح"
    assert both.props["content"] == f"{S1} {S2}"
    assert digits.props["items"] == ["ينمو القطاع بنسبة ٥٠٪ سنويا."]
    assert other.props["content"] == "نص آخر لا علاقة له بالمصدر أبدا"


def test_source_units_stays_linear_on_a_huge_item():
    # Reason: every sentence run was enumerated (O(n^2) keys of O(n) length) — a 120k-char item exhausted memory.
    from section_orchestrator import _source_units
    body = " ".join(f"جملة رقم {i} في فقرة طويلة." for i in range(3000))
    units = _source_units(body)
    assert len(units) <= 3000 * 6 + 1


@pytest.mark.parametrize("text", [
    "استحوذت شركة أرامكو على حصة كبيرة",
    "أطلقت منصة سلة خدمة جديدة",
    "صرّحت مؤسسة النقد بذلك",
    "ظهر منافس باسم «نون» في السوق",
    'ظهر منافس باسم "نون" في السوق',
    "نستخدم WhatsApp للتواصل مع العملاء",
    "نستخدم iPhone للتصوير",
])
def test_arabic_source_names_count_as_evidence(text):
    from section_orchestrator import has_evidence
    assert has_evidence(text)


@pytest.mark.parametrize("text", [
    "هذا نص عام بدون أي تفاصيل محددة",
    "تحتاج الشركات الاستشارية إلى مواقع جيدة",
    "هناك شركة تعمل في هذا المجال منذ فترة",
    "generally the situation is quite complex",
])
def test_plain_arabic_prose_is_not_evidence(text):
    from section_orchestrator import has_evidence
    assert not has_evidence(text)


def test_section_instructions_nudge_card_variety_positively():
    from section_orchestrator import SECTION_INSTRUCTIONS
    text = SECTION_INSTRUCTIONS.lower()
    assert "use the card type that fits each piece" in text
    assert "avoid using one type for everything" in text


def test_latin_brand_check_is_linear_on_a_huge_token():
    # Reason: a lookahead regex re-scanned the rest of a long [a-z…] run at every start position (quadratic).
    import time
    from section_orchestrator import has_evidence
    text = "نص عربي " + "a" * 200_000 + " " + "a-" * 50_000
    t0 = time.perf_counter()
    has_evidence(text)
    assert time.perf_counter() - t0 < 1.0


# --- repeated titles on cards split from one source paragraph; steps rows hold steps only ----------

def test_repeated_title_stays_on_first_card_of_one_source_paragraph():
    from section_orchestrator import dedupe_repeated_titles

    s1, s2, s3 = "الجملة الأولى من الفقرة هنا.", "الجملة الثانية من الفقرة هنا.", "الجملة الثالثة من الفقرة هنا."
    body = f"المشكلة الجوهرية\n\n{s1} {s2} {s3}\n\nفقرة أخرى مختلفة تماما."
    comps = [_comp("calloutCard", type="info", title="المشكلة الجوهرية", content=s) for s in (s1, s2, s3)]
    out = dedupe_repeated_titles(comps, body)
    assert [c.props["title"] for c in out] == ["المشكلة الجوهرية", "", ""]
    assert [c.props["content"] for c in out] == [s1, s2, s3]


def test_same_title_over_different_source_items_is_kept():
    from section_orchestrator import dedupe_repeated_titles

    a, b = "نص الفقرة الأولى كاملا.", "نص الفقرة الثانية كاملا."
    comps = [_comp("calloutCard", title="مؤشرات", content=a), _comp("calloutCard", title="مؤشرات", content=b)]
    out = dedupe_repeated_titles(comps, f"مؤشرات\n\n{a}\n\nمؤشرات\n\n{b}")
    assert [c.props["title"] for c in out] == ["مؤشرات", "مؤشرات"]


def test_non_consecutive_or_untraceable_repeats_are_untouched():
    from section_orchestrator import dedupe_repeated_titles

    a = "نص الفقرة الأولى كاملا."
    comps = [_comp("calloutCard", title="ت", content=a), _comp("bulletList", items=[{"text": "x"}]), _comp("calloutCard", title="ت", content="نص غير موجود في المصدر")]
    out = dedupe_repeated_titles(comps, f"ت\n\n{a}")
    assert [c.props.get("title") for c in out if c.type == "calloutCard"] == ["ت", "ت"]


def test_step_cards_never_share_a_row_with_other_cards():
    from section_orchestrator import group_short_rows

    mixed = group_short_rows([_comp("calloutCard", title="أ", content="نص قصير"), _comp("stepCard", title="ب", description="نص قصير")], "s1")
    assert _groups(mixed) == [None, None]
    steps = group_short_rows([_comp("stepCard", title="أ", description="نص قصير"), _comp("stepCard", title="ب", description="نص قصير")], "s1")
    assert _groups(steps)[0] and _groups(steps)[0] == _groups(steps)[1]
