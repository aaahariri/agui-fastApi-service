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
