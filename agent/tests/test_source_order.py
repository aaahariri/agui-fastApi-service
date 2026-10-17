"""Components follow the source order of their first sentence (property test on captured gpt-5.6-luna section answers)."""
import asyncio
import json
import pathlib

import pytest

import section_orchestrator as so
from a2ui_generator import A2UIComponent
from llm_orchestrator import apply_layout_and_zone, build_a2ui_component, expand_component_specs

FIXTURES = json.loads((pathlib.Path(__file__).parent / "fixtures" / "luna_sections.json").read_text())


def _comp(type_: str, **props) -> A2UIComponent:
    return A2UIComponent(type=type_, id=type_, props=props, zone="content")


def _build(fixture: dict) -> list[A2UIComponent]:
    specs = json.loads(fixture["answer"])["components"]

    async def select(analysis, layout, text, extra, allow_empty=False):
        return specs

    return asyncio.run(so._build_section(
        fixture["title"], fixture["body"], select, build_a2ui_component, expand_component_specs,
        apply_layout_and_zone, asyncio.Semaphore(1),
    ))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f"{f['run']}-{f['title'][:12]}")
def test_every_section_follows_source_order_and_keeps_every_sentence(fixture):
    out = _build(fixture)
    assert so.order_violations(fixture["body"], out) == []
    leaves = so._coverage_leaves(out) + [" ".join(so._coverage_leaves([c])) for c in out]
    missing = [m for _, lines in so._source_item_lines(fixture["body"]) for ln in lines for m in so._missing_sentences(ln, leaves)]
    assert missing == []


def test_fixture_exercises_the_bug(monkeypatch):
    """Without the final ordering pass the captured answers do put text out of source order."""
    monkeypatch.setattr(so, "order_by_source", lambda components, body: components)
    assert sum(bool(so.order_violations(f["body"], _build(f))) for f in FIXTURES) >= 5


def test_intro_line_ending_with_a_colon_precedes_its_list():
    body = "تفاصيل ناقصة، مثل:\n- أول بند في القائمة الطويلة\n- ثاني بند في القائمة الطويلة\n\nجملة ختامية بعد القائمة كلها هنا."
    list_card = _comp("keyTakeaways", title="", items=["أول بند في القائمة الطويلة", "ثاني بند في القائمة الطويلة"])
    intro = _comp("bulletList", items=[{"text": "تفاصيل ناقصة، مثل:"}])  # gap-fill puts uncovered lines last
    closing = _comp("calloutCard", title="", content="جملة ختامية بعد القائمة كلها هنا.")
    out = so.order_by_source([list_card, closing, intro], body)
    assert [c.type for c in out] == ["bulletList", "keyTakeaways", "calloutCard"]
    assert so.order_violations(body, out) == []


def test_gap_fill_list_with_items_from_different_places_is_split_around_cards():
    body = "بند أول من القائمة الأولى هنا.\n\nجملة وسطى تحملها بطاقة واحدة.\n\nبند أخير من القائمة الأخيرة هنا."
    gap = _comp("bulletList", items=[{"text": "بند أول من القائمة الأولى هنا."}, {"text": "بند أخير من القائمة الأخيرة هنا."}])
    middle = _comp("calloutCard", title="", content="جملة وسطى تحملها بطاقة واحدة.")
    out = so.order_by_source([middle, gap], body)
    assert [c.type for c in out] == ["bulletList", "calloutCard", "bulletList"]
    assert [i["text"] for c in out if c.type == "bulletList" for i in c.props["items"]] == [
        "بند أول من القائمة الأولى هنا.", "بند أخير من القائمة الأخيرة هنا.",
    ]


def test_component_without_source_sentence_stays_after_its_predecessor_and_heading_stays_first():
    body = "جملة أولى من التقرير الكامل هنا.\n\nجملة ثانية من التقرير الكامل هنا."
    heading = _comp("heading", text="قسم", level=2)
    first = _comp("calloutCard", title="", content="جملة أولى من التقرير الكامل هنا.")
    figure = _comp("statCard", title="رقم", value="42")
    second = _comp("calloutCard", title="", content="جملة ثانية من التقرير الكامل هنا.")
    out = so.order_by_source([heading, second, first, figure], body)
    assert [c.type for c in out] == ["heading", "calloutCard", "statCard", "calloutCard"]
    assert out[1] is first and out[3] is second


def test_label_shown_with_a_later_sentence_is_hoisted_in_front_of_the_sentences_it_labels():
    body = "مؤشرات الجدوى\nلا توجد مخاوف واضحة بشأن السوق المستهدف اليوم.\nومع ذلك قد تواجه الفكرة منافسة من الشركات المحلية."
    first = _comp("keyTakeaways", title="", items=["لا توجد مخاوف واضحة بشأن السوق المستهدف اليوم."])
    second = _comp("calloutCard", title="مؤشرات الجدوى", content="ومع ذلك قد تواجه الفكرة منافسة من الشركات المحلية.")
    out = so.order_by_source([first, second], body)
    assert so.order_violations(body, out) == []
    assert out[0].type == "headlineCard" and out[0].props["title"] == "مؤشرات الجدوى"
