"""
Section-by-section orchestration for long structured reports.

The single-shot orchestrator (llm_orchestrator.orchestrate_dashboard_with_llm)
is a *condenser* told to stay within 15-25 components, so a 10-section / 75k-char
report keeps ~3% of its text and drops every heading. For long structured reports
that is content loss, not visualization.

This module keeps the LLM-driven visual components but makes coverage a
property of the pipeline instead of a prompt hope:

1. Split the markdown on `## ` headings.
2. Emit a deterministic `heading` component per section (never LLM-dependent).
3. Run the component-selection LLM per section (bounded concurrency) with a
   "represent every item" instruction.
4. Deterministic coverage guard: uncovered source items that carry evidence
   (numbers, %, currency, dates, URLs, named entities) are appended verbatim;
   the remaining uncovered items are merged into one compact bulletList.
"""

import asyncio
import re
from typing import Any, AsyncGenerator, Awaitable, Callable

from a2ui_generator import A2UIComponent
from logger import logger

# Documents with at least this many H2 sections AND this many chars take the
# section path. Below it the single-shot visualizer is unchanged (short docs
# fit its 30k window and its condensed output is the intended product).
MIN_SECTIONS = 4
MIN_CHARS = 12000
MAX_CONCURRENCY = 4
# A source item counts as "covered" when this share of its tokens appears in
# one component's text.
COVERAGE_THRESHOLD = 0.6

SECTION_INSTRUCTIONS = """

## THIS IS ONE SECTION OF A LARGER REPORT (overrides the size ranges above)

- The section heading is already rendered separately. Do NOT repeat it.
- Use only the components needed. Minimum words, maximum value: be concise, merge related points, drop repetition and filler.
- Every number, percentage, currency amount, date, named source/competitor and URL MUST be kept.
- Use 3-6 components for this section; the 15-25 range does not apply.
- Prefer statCard/metricRow for numbers, calloutCard for key judgements, bulletList for plain lists.
- Keep the document's language; do not translate.
"""

SelectFn = Callable[[dict, dict, str, str], Awaitable[list[dict]]]


def split_report(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(section_title, section_body)]) split on `## ` lines."""
    parts = re.split(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE)
    preamble = parts[0]
    sections = [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]
    return preamble, sections


def should_use_section_path(markdown: str) -> bool:
    if len(markdown) < MIN_CHARS:
        return False
    _, sections = split_report(markdown)
    return len(sections) >= MIN_SECTIONS


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", re.sub(r"[*_`]", "", text).lower()))


def _component_text(component: A2UIComponent) -> str:
    chunks: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(component.props)
    return " ".join(chunks)


def _is_covered(item: str, component_tokens: list[set[str]]) -> bool:
    wanted = _tokens(item)
    if not wanted:
        return True
    return any(len(wanted & tokens) / len(wanted) >= COVERAGE_THRESHOLD for tokens in component_tokens)


_EVIDENCE_RE = re.compile(
    r"\d|%|https?://|[$\u20ac\u00a3\u00a5\ufdfc]|\b(?:SAR|USD|EUR|AED)\b|\u0631\.\u0633|\u0631\u064a\u0627\u0644"
)
# A capitalised Latin word that is not the first word of the text: brand / competitor / source name.
_NAMED_RE = re.compile(r"(?<=\s)[A-Z][A-Za-z0-9&.-]{2,}")


def has_evidence(text: str) -> bool:
    """True when the text carries a number, currency, date, URL or named entity worth keeping verbatim."""
    return bool(_EVIDENCE_RE.search(text) or _NAMED_RE.search(text))


def extract_source_items(body: str) -> list[tuple[str, str]]:
    """Return [("bullet"|"paragraph", text)] for every list item / prose block."""
    items: list[tuple[str, str]] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            items.append(("paragraph", " ".join(para).strip()))
            para.clear()

    for line in body.splitlines():
        stripped = line.strip()
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", stripped)
        if bullet:
            flush()
            items.append(("bullet", bullet.group(1).strip()))
        elif not stripped:
            flush()
        elif stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("|"):
            flush()
        else:
            para.append(stripped)
    flush()
    return [(kind, text) for kind, text in items if text]


def _plain(text: str) -> str:
    """Drop markdown emphasis markers: a bare `**Title**` line renders as a plain paragraph."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()


def _brief(text: str, limit: int = 200) -> str:
    text = _plain(text)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def _bullet_list(texts: list[str]) -> A2UIComponent:
    return A2UIComponent(
        type="bulletList", id="bullet-list", props={"items": [{"text": t} for t in texts]}, zone="content",
    )


def coverage_components(body: str, built: list[A2UIComponent]) -> list[A2UIComponent]:
    """
    Gap-fill for source text no component covers. Items carrying evidence are kept verbatim in
    ONE bulletList; the remaining uncovered items are trimmed and merged into a second one.
    """
    component_tokens = [_tokens(_component_text(c)) for c in built]
    evidence: list[str] = []
    rest: list[str] = []
    for _, text in extract_source_items(body):
        if _is_covered(text, component_tokens):
            continue
        if has_evidence(text):
            evidence.append(_plain(text))
        else:
            rest.append(_brief(text))
    return [_bullet_list(items) for items in (evidence, rest) if items]


async def _build_section(
    title: str,
    body: str,
    select_fn: SelectFn,
    build_fn: Callable[[dict, dict], A2UIComponent | None],
    expand_fn: Callable[[list[dict]], list[dict]],
    apply_fn: Callable[[A2UIComponent, dict], A2UIComponent],
    semaphore: asyncio.Semaphore,
) -> list[A2UIComponent]:
    analysis = {"document_type": "report", "title": title, "sections": [title]}
    layout = {"layout_type": "summary_layout", "confidence": 1.0, "reasoning": "report section"}
    built: list[A2UIComponent] = []
    try:
        async with semaphore:
            specs = await select_fn(analysis, layout, f"## {title}\n\n{body}", SECTION_INSTRUCTIONS)
        for spec in expand_fn(specs):
            component = build_fn(spec, analysis)
            if component:
                built.append(apply_fn(component, spec))
    except Exception as e:  # coverage guard below still emits the section's text
        logger.error(f"[SECTION] LLM failed for '{title}': {e}")

    heading = A2UIComponent(
        type="heading", id="heading", props={"text": title, "level": 2}, zone="content",
    )
    return [heading, *built, *coverage_components(body, built)]


async def orchestrate_sections(
    markdown: str,
    select_fn: SelectFn,
    build_fn: Callable[[dict, dict], A2UIComponent | None],
    expand_fn: Callable[[list[dict]], list[dict]],
    apply_fn: Callable[[A2UIComponent, dict], A2UIComponent],
) -> AsyncGenerator[A2UIComponent, None]:
    """Yield components section by section, in document order, with unique ids."""
    _, sections = split_report(markdown)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(*[
        _build_section(t, b, select_fn, build_fn, expand_fn, apply_fn, semaphore) for t, b in sections
    ])
    n = 0
    for group in results:
        for component in group:
            n += 1
            # Reason: sections build concurrently; per-task id counters would collide.
            component.id = f"{component.type}-{n}"
            yield component
