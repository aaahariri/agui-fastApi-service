"""build_a2ui_component accepts the prop shapes gpt-5.6-luna emits (captured from real section answers, 2026-10-17)."""
import pytest

from llm_orchestrator import build_a2ui_component

ANALYSIS = {"title": "تقرير"}
DESC = "تواصل مع مكاتب المحاماة والشركات الاستشارية في السعودية لفهم احتياجاتهم وتحدياتهم."
EXTRA = "واستفسر عن الميزات التي يرغبون في رؤيتها، وكيف يمكن تحسين تجربتهم الحالية."
SENT = "ومع ذلك، قد تواجه الفكرة منافسة من الشركات المحلية والدولية التي تقدم خدمات مماثلة."


def build(component_type: str, props: dict):
    return build_a2ui_component({"component_type": component_type, "props": props}, ANALYSIS)


def text_of(component) -> str:
    return " ".join(str(v) for v in component.props.values() if isinstance(v, str))


def test_step_card_content_list_becomes_description():
    c = build("stepCard", {"title": "إجراء مقابلات", "content": [DESC, EXTRA], "width_hint": "full"})
    assert c is not None and c.props["description"] == f"{DESC} {EXTRA}" and c.props["stepNumber"] == 1


def test_step_card_content_string_and_additional_content():
    c = build("stepCard", {"step": 2, "title": "t", "content": DESC, "additional_content": EXTRA})
    assert c.props["stepNumber"] == 2 and c.props["description"] == f"{DESC} {EXTRA}"


def test_step_card_description_plus_detail():
    c = build("stepCard", {"title": "t", "description": DESC, "detail": EXTRA})
    assert c.props["description"] == f"{DESC} {EXTRA}"


def test_step_card_description_none_falls_back_to_content():
    c = build("stepCard", {"title": "t", "description": None, "content": DESC})
    assert c.props["description"] == DESC


def test_step_card_title_only_keeps_its_text():
    c = build("stepCard", {"title": "تحليل السوق", "description": None})
    assert c is not None and "تحليل السوق" in text_of(c)


def test_headline_card_title_and_content_uses_content_as_summary():
    c = build("headlineCard", {"title": "مؤشرات الجدوى", "content": SENT, "sentiment": "positive"})
    assert c.type.endswith("eadlineCard") and c.props["title"] == "مؤشرات الجدوى" and c.props["summary"] == SENT


def test_headline_card_content_only_keeps_text_without_dropping():
    c = build("headlineCard", {"content": SENT, "width_hint": "half"})
    assert c is not None and SENT in text_of(c)


def test_headline_card_headline_key():
    c = build("headlineCard", {"title": "البدائل", "headline": SENT, "sentiment": "neutral"})
    assert c is not None and "البدائل" in text_of(c) and SENT in text_of(c)


def test_quote_card_without_author_keeps_text_and_title():
    c = build("quoteCard", {"title": "التحديد", "content": SENT})
    assert c is not None and "التحديد" in text_of(c) and SENT in text_of(c)
    assert "author" not in c.props or c.props["author"]


def test_quote_card_quote_key_without_author():
    c = build("quoteCard", {"title": "التحديد", "quote": SENT})
    assert c is not None and SENT in text_of(c)


def test_quote_card_with_author_stays_quote():
    c = build("quoteCard", {"quote": SENT, "author": "فلان"})
    assert c.props["author"] == "فلان"


def test_callout_content_list_is_joined():
    c = build("calloutCard", {"title": "ت", "content": [DESC, EXTRA]})
    assert c.props["content"] == f"{DESC} {EXTRA}"


def test_callout_none_title_and_text_alias():
    c = build("calloutCard", {"title": None, "text": DESC})
    assert c.props["title"] == "" and c.props["content"] == DESC


def test_callout_title_only_keeps_title_text():
    c = build("calloutCard", {"title": "واعد"})
    assert c is not None and "واعد" in text_of(c)


def test_stat_card_with_sentence_value_becomes_text_card_not_a_stat():
    c = build("statCard", {"title": None, "value": SENT, "label": None})
    assert c is not None and SENT in text_of(c)
    c = build("statCard", {"title": "مؤشرات الجدوى", "label": "مؤشرات الجدوى", "value": SENT})
    assert c is not None and SENT in text_of(c) and "مؤشرات الجدوى" in text_of(c)


def test_stat_card_real_figure_stays_stat():
    c = build("statCard", {"label": "النمو", "value": "9٪"})
    assert c.type.endswith("tatCard") and c.props["value"] == "9٪"


def test_profile_card_without_name_keeps_text():
    c = build("profileCard", {"title": "المؤسس", "bio": SENT})
    assert c is not None and SENT in text_of(c)


def test_tldr_content_list_and_none_props():
    c = build("tldr", {"content": [DESC, EXTRA]})
    assert c is not None and DESC[:20] in text_of(c)
    assert build_a2ui_component({"component_type": "calloutCard", "props": None}, ANALYSIS) is None


def test_nothing_to_build_returns_none_and_logs(caplog):
    with caplog.at_level("WARNING"):
        assert build("calloutCard", {"title": None, "content": []}) is None
    assert "SKIP" in caplog.text


def test_vs_card_without_items_keeps_its_text():
    c = build("vsCard", {"title": "فرصة التميّز", "content": SENT})
    assert c is not None and "فرصة التميّز" in text_of(c) and SENT in text_of(c)
