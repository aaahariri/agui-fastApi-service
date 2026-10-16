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
   the remaining uncovered items are appended in full as a bulletList.
"""

import asyncio
import re
from typing import Any, AsyncGenerator, Awaitable, Callable

from a2ui_generator import A2UIComponent
from prompts import VERBATIM_COPY_RULE

# Documents with at least this many H2 sections take the section path (routing by
# structure, not length, so a concise report keeps every heading).
MIN_SECTIONS = 4
# Docs without that structure: above PSEUDO_MIN_CHARS they are split on paragraph
# boundaries into ~PSEUDO_CHUNK_CHARS pseudo-sections (lossless); above MAX_INPUT_CHARS
# they are rejected.
PSEUDO_MIN_CHARS = 30000
PSEUDO_CHUNK_CHARS = 7000
MAX_INPUT_CHARS = 120000
MAX_CONCURRENCY = 4
# A source item counts as "covered" when this share of its tokens appears in
# one component's text.
COVERAGE_THRESHOLD = 0.6

SECTION_INSTRUCTIONS = f"""

## THIS IS ONE SECTION OF A LARGER REPORT (overrides the size ranges above)

{VERBATIM_COPY_RULE}
- The section heading is already rendered separately. Do NOT repeat it.
- Use as many components as needed to hold all the text of this section; no component-count limit applies.
- Prefer statCard/metricRow for numbers, calloutCard for key judgements, bulletList for plain lists (text unchanged).
"""

SelectFn = Callable[[dict, dict, str, str], Awaitable[list[dict]]]


def split_report(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(section_title, section_body)]) split on `## ` lines."""
    parts = re.split(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE)
    preamble = parts[0]
    sections = [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]
    return preamble, sections


class InputTooLongError(ValueError):
    """Document exceeds MAX_INPUT_CHARS."""


def split_pseudo_sections(markdown: str, limit: int = PSEUDO_CHUNK_CHARS) -> list[str]:
    """Split on blank lines into chunks of ~limit chars (an oversize paragraph stays whole). Lossless."""
    chunks: list[str] = []
    current = ""
    for para in markdown.split("\n\n"):
        if current and len(current) + len(para) + 2 > limit:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current)
    return chunks


def should_use_section_path(markdown: str) -> bool:
    """True for >=4 `##` sections, or any doc long enough to need lossless pseudo-sectioning."""
    if len(markdown) > MAX_INPUT_CHARS:
        raise InputTooLongError(
            f"Document is {len(markdown)} chars (limit {MAX_INPUT_CHARS}); split it into smaller documents."
        )
    _, sections = split_report(markdown)
    return len(sections) >= MIN_SECTIONS or len(markdown) > PSEUDO_MIN_CHARS


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
        elif stripped.startswith("|"):
            flush()
            if not re.fullmatch(r"[|\s:-]+", stripped):  # table row kept; separator row dropped
                items.append(("row", stripped))
        elif stripped.startswith("```"):
            flush()
        elif stripped.startswith("#"):
            flush()
            items.append(("paragraph", stripped.lstrip("#").strip()))
        else:
            para.append(stripped)
    flush()
    return [(kind, text) for kind, text in items if text]


def _plain(text: str) -> str:
    """Drop markdown emphasis markers: a bare `**Title**` line renders as a plain paragraph."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()


def _bullet_list(texts: list[str]) -> A2UIComponent:
    return A2UIComponent(
        type="bulletList", id="bullet-list", props={"items": [{"text": t} for t in texts]}, zone="content",
    )


_TABLE_BLOCK_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*\n?){3,}", re.MULTILINE)


def _split_cells(line: str) -> list[str]:
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _table_components(body: str, component_tokens: list[set[str]]) -> tuple[str, list[A2UIComponent]]:
    """
    Pull markdown tables (header, separator, rows) out of the body. A table no component already
    covers becomes ONE dataTable (every cell kept); covered tables are just removed from the body.
    """
    tables: list[A2UIComponent] = []

    def take(match: re.Match[str]) -> str:
        lines = [ln for ln in match.group(0).splitlines() if ln.strip()]
        if not re.fullmatch(r"[|\s:-]+", lines[1].strip()):
            return match.group(0)  # not a real table: leave rows to the item path
        headers = _split_cells(lines[0])
        rows = [(_split_cells(ln) + [""] * len(headers))[: len(headers)] for ln in lines[2:]]
        if not _is_covered(" ".join(c for row in rows for c in row), component_tokens):
            tables.append(A2UIComponent(
                type="dataTable", id="data-table", props={"headers": headers, "rows": rows}, zone="content",
            ))
        return "\n"

    return _TABLE_BLOCK_RE.sub(take, body), tables


def coverage_components(body: str, built: list[A2UIComponent]) -> list[A2UIComponent]:
    """
    Gap-fill for source text no component covers. Markdown tables become a dataTable; items carrying
    evidence are kept verbatim in ONE bulletList; the remaining uncovered items are merged in full
    (never cut) into a second one.
    """
    component_tokens = [_tokens(_component_text(c)) for c in built]
    body, tables = _table_components(body, component_tokens)
    evidence: list[str] = []
    rest: list[str] = []
    for _, text in extract_source_items(body):
        if _is_covered(text, component_tokens):
            continue
        if has_evidence(text):
            evidence.append(_plain(text))
        else:
            rest.append(_plain(text))
    return [*tables, *(_bullet_list(items) for items in (evidence, rest) if items)]


async def _build_section(
    title: str,
    body: str,
    select_fn: SelectFn,
    build_fn: Callable[[dict, dict], A2UIComponent | None],
    expand_fn: Callable[[list[dict]], list[dict]],
    apply_fn: Callable[[A2UIComponent, dict], A2UIComponent],
    semaphore: asyncio.Semaphore,
    level: int = 2,
) -> list[A2UIComponent]:
    analysis = {"document_type": "report", "title": title or "Report", "sections": [title] if title else []}
    layout = {"layout_type": "summary_layout", "confidence": 1.0, "reasoning": "report section"}
    built: list[A2UIComponent] = []
    # Reason: a failed section must fail the request (caller retries the whole step), never ship degraded.
    async with semaphore:
        text = f"## {title}\n\n{body}" if title else body
        specs = await select_fn(analysis, layout, text, SECTION_INSTRUCTIONS)
    for spec in expand_fn(specs):
        component = build_fn(spec, analysis)
        if component:
            built.append(apply_fn(component, spec))

    heading = (
        [A2UIComponent(type="heading", id="heading", props={"text": title, "level": level}, zone="content")]
        if title else []
    )
    return [*heading, *built, *coverage_components(body, built)]


def _opening(preamble: str) -> list[A2UIComponent]:
    """Text before the first `##`: title line -> level-1 heading, remaining lines kept as the intro."""
    out: list[A2UIComponent] = []
    rest = preamble.strip()
    m = re.match(r"#\s+(.+)", rest)
    if m:
        out.append(A2UIComponent(type="heading", id="heading", props={"text": m.group(1).strip(), "level": 1}, zone="content"))
        rest = rest[m.end():]
    return [*out, *coverage_components(rest, [])]


async def orchestrate_sections(
    markdown: str,
    select_fn: SelectFn,
    build_fn: Callable[[dict, dict], A2UIComponent | None],
    expand_fn: Callable[[list[dict]], list[dict]],
    apply_fn: Callable[[A2UIComponent, dict], A2UIComponent],
) -> AsyncGenerator[A2UIComponent, None]:
    """Yield components section by section, in document order, with unique ids."""
    preamble, sections = split_report(markdown)
    if len(sections) >= MIN_SECTIONS:
        jobs = [(t, b) for t, b in sections]
        opening = _opening(preamble)
    else:  # no usable structure: lossless pseudo-sections, no invented headings
        jobs = [("", chunk) for chunk in split_pseudo_sections(markdown)]
        opening = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    results = [opening, *await asyncio.gather(*[
        _build_section(t, b, select_fn, build_fn, expand_fn, apply_fn, semaphore) for t, b in jobs
    ])]
    n = 0
    for group in results:
        for component in group:
            n += 1
            # Reason: sections build concurrently; per-task id counters would collide.
            component.id = f"{component.type}-{n}"
            yield component
