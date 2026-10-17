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


_LEADING_MARKER_RE = re.compile(r"^([ \t]*)(#{1,6}[ \t]+|[-*+][ \t]+|>[ \t]+)(?=\S)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_UNDERSCORE_RE = re.compile(r"(?<!\w)__(.+?)__(?!\w)", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s*`]+", re.IGNORECASE)
# A marker is followed by a number / sign / currency: it is a comparison or value ("> 10", "+ 5%", "- 3"), not markup.
_VALUE_START_RE = re.compile(r"[\d\u0660-\u0669\u06f0-\u06f9$\u20ac\u00a3\u00a5+\-~<>=]")
# Props that hold machine values, never display text: left untouched.
_RAW_PROP_KEYS = frozenset({
    "url", "imageUrl", "code", "language", "spec", "videoUrl", "thumbnailUrl", "videoId", "href", "src",
})
# Props whose value is a short figure: leading symbols are part of the value, never list/quote markup.
_VALUE_PROP_KEYS = frozenset({"value", "change", "unit"})


def _strip_leading_markers(text: str) -> str:
    """Remove a list/heading/quote marker from each multi-word prose line; keep value-like lines whole."""
    def one(line: str) -> str:
        m = _LEADING_MARKER_RE.match(line)
        if not m:
            return line
        rest = line[m.end():]
        words = rest.split()
        single_hash = m.group(2).strip() == "#"
        if _VALUE_START_RE.match(rest) or len(words) < 2 or (single_hash and len(words) < 3):
            return line
        return m.group(1) + rest

    return "\n".join(one(line) for line in text.split("\n"))


def strip_inline_markdown(text: str, *, markers: bool = True) -> str:
    """
    Drop markdown syntax (line-start `#`/`-`/`>` markers on multi-word prose lines, **bold**, __bold__,
    *italic*, `code`) and keep every word. The blocks have no inline-formatting slot, so a literal marker
    would render as text. Leading symbols before a number, short values and URLs are never touched.
    """
    urls: list[str] = []

    def hide(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return f"\x00{len(urls) - 1}\x00"

    text = _URL_RE.sub(hide, text)
    if markers:
        text = _strip_leading_markers(text)
    for pattern in (_BOLD_RE, _UNDERSCORE_RE, _ITALIC_RE, _CODE_SPAN_RE):
        text = pattern.sub(r"\1", text)
    text = text.strip()
    return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], text)


def _plain(text: str) -> str:
    return strip_inline_markdown(text)


def clean_component(component: A2UIComponent) -> A2UIComponent:
    """Strip inline markdown from every display-text leaf of a component (code and URLs untouched)."""
    if component.type in ("codeBlock", "figureBlock"):
        return component

    def walk(value: Any, markers: bool = True) -> Any:
        if isinstance(value, str):
            return strip_inline_markdown(value, markers=markers)
        if isinstance(value, dict):
            return {
                k: (v if k in _RAW_PROP_KEYS else walk(v, markers and k not in _VALUE_PROP_KEYS))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(v, markers) for v in value]
        return value

    component.props = walk(component.props)
    return component


# Prose components and the props that carry their text. Only these are ever dropped as repeats/blank.
_PROSE_FIELDS: dict[str, tuple[str, ...]] = {
    "tldr": ("content",),
    "headlineCard": ("title", "summary"),
    "calloutCard": ("title", "content"),
    "keyTakeaways": ("title", "items"),
    "bulletList": ("text", "items"),
    "quoteCard": ("quote", "text"),
}
# A repeated piece must be at least this long to count as the same prose (not a label).
MIN_REPEAT_CHARS = 12


def _norm(text: str) -> str:
    return " ".join(text.split())


# Props that never show as text (machine values, ids, enums): excluded from repeat matching.
_HIDDEN_PROP_KEYS = _RAW_PROP_KEYS | frozenset({"type", "id", "zone", "platform", "trend", "changeType", "level"})


def _leaf_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() if k not in _HIDDEN_PROP_KEYS for s in _leaf_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _leaf_strings(v)]
    return []


def _prose_pieces(component: A2UIComponent) -> list[str] | None:
    """Normalised non-empty text pieces of a prose component; None for every other type."""
    fields = _PROSE_FIELDS.get(component.type)
    if fields is None:
        return None
    return [p for f in fields for s in _leaf_strings(component.props.get(f)) if (p := _norm(s))]


def drop_empty_components(components: list[A2UIComponent]) -> list[A2UIComponent]:
    """Remove prose components whose visible text props are ALL blank (they render as an empty placeholder)."""
    return [c for c in components if _prose_pieces(c) != []]


def drop_repeated_prose(components: list[A2UIComponent]) -> list[A2UIComponent]:
    """
    Keep each stretch of prose once. A prose component whose every text piece already appears inside
    another kept component is redundant (a paragraph split into headline + callout + takeaways, then
    gap-filled again as a whole). Longest components are kept first; the survivor moves to the position
    of the first fragment it absorbed, so the text keeps its place in the section. Nothing is reworded.
    """
    pieces = [_prose_pieces(c) for c in components]
    haystack = ["\n".join(_norm(s) for s in _leaf_strings(c.props)) for c in components]
    kept = {i for i, p in enumerate(pieces) if p is None or max(map(len, p), default=0) < MIN_REPEAT_CHARS}
    position = {i: i for i in range(len(components))}
    droppable = sorted(
        (i for i in range(len(components)) if i not in kept),
        key=lambda i: (-sum(map(len, pieces[i] or [])), i),
    )
    for i in droppable:
        owners = []
        for piece in pieces[i] or []:
            pattern = re.compile(r"(?<!\w)" + re.escape(piece) + r"(?!\w)")
            owner = next((k for k in sorted(kept) if pattern.search(haystack[k])), None)
            if owner is None:
                break
            owners.append(owner)
        else:
            for owner in owners:
                if pieces[owner] is not None:  # only prose survivors may move
                    position[owner] = min(position[owner], i)
            continue
        kept.add(i)
    return [components[i] for i in sorted(kept, key=lambda i: (position[i], i))]


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
    heading = (
        [A2UIComponent(type="heading", id="heading", props={"text": title, "level": level}, zone="content")]
        if title else []
    )
    # Reason: a heading with no body has nothing to structure; the selector returns no
    # components for it and that failed the whole request.
    if not body.strip():
        return heading
    # Reason: a failed section must fail the request (caller retries the whole step), never ship degraded.
    async with semaphore:
        text = f"## {title}\n\n{body}" if title else body
        specs = await select_fn(analysis, layout, text, SECTION_INSTRUCTIONS)
    for spec in expand_fn(specs):
        component = build_fn(spec, analysis)
        if component:
            built.append(clean_component(apply_fn(component, spec)))

    # Reason: coverage runs on the built components; the repeat/blank pass then runs over everything
    # (LLM output + gap-fill) so a paragraph split across components and gap-filled whole shows once.
    body_components = drop_repeated_prose(drop_empty_components([*built, *coverage_components(body, built)]))
    return [*heading, *body_components]


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
