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
# A source line counts as "covered" only when its exact text (markdown and a trailing colon aside) is
# inside one component text leaf. Lines shorter than this many words are labels / values: they must be a
# whole leaf (or open one), never a fragment of a longer sentence.
MIN_SUBSTRING_WORDS = 4

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


# Props that never show as text; every other string (URLs included) counts as covered source text.
_NON_TEXT_KEYS = frozenset({"type", "id", "zone", "trend", "changeType", "level", "sentiment"})
_TRAILING_COLON_RE = re.compile(r"\s*[:\uff1a]+\s*$")


def _key(text: str) -> str:
    """Comparison form of a text: markdown syntax, whitespace runs and a trailing colon removed."""
    return _TRAILING_COLON_RE.sub("", " ".join(strip_inline_markdown(text).split()))


def _coverage_leaves(components: list[A2UIComponent]) -> list[str]:
    leaves: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if key := _key(value):
                leaves.append(key)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k not in _NON_TEXT_KEYS:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for component in components:
        walk(component.props)
    return leaves


def _line_covered(line: str, leaves: list[str]) -> bool:
    """Exact-text coverage: a sentence is inside some leaf; a label / value is a leaf or opens one."""
    key = _key(line)
    if not re.search(r"\w", key):
        return True
    if len(key.split()) >= MIN_SUBSTRING_WORDS:
        return any(key in leaf for leaf in leaves)
    return any(leaf == key or (leaf.startswith(key) and leaf[len(key)] in " :\uff1a") for leaf in leaves)


def _label_like(line: str) -> bool:
    return len(_key(line).split()) < MIN_SUBSTRING_WORDS


def _uncovered_text(lines: list[str], leaves: list[str]) -> str:
    """Text of an item that no component carries ('' when every line is covered)."""
    if len(lines) > 1 and any(_key(" ".join(lines)) in leaf for leaf in leaves):
        return ""  # a hard-wrapped paragraph kept whole
    missing = [ln for ln in lines if not _line_covered(ln, leaves)]
    # Reason: a lone missing label would be appended detached from its paragraph; keep them together.
    if missing and len(missing) < len(lines) and all(_label_like(ln) for ln in missing):
        missing = lines
    return " ".join(missing)


_EVIDENCE_RE = re.compile(
    r"\d|%|https?://|[$\u20ac\u00a3\u00a5\ufdfc]|\b(?:SAR|USD|EUR|AED)\b|\u0631\.\u0633|\u0631\u064a\u0627\u0644"
)
# A capitalised Latin word that is not the first word of the text: brand / competitor / source name.
_NAMED_RE = re.compile(r"(?<=\s)[A-Z][A-Za-z0-9&.-]{2,}")


def has_evidence(text: str) -> bool:
    """True when the text carries a number, currency, date, URL or named entity worth keeping verbatim."""
    return bool(_EVIDENCE_RE.search(text) or _NAMED_RE.search(text))


def _source_item_lines(body: str) -> list[tuple[str, list[str]]]:
    """Return [("bullet"|"paragraph"|"row", [source lines])] for every list item / prose block."""
    items: list[tuple[str, list[str]]] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            items.append(("paragraph", para.copy()))
            para.clear()

    for line in body.splitlines():
        stripped = line.strip()
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", stripped)
        if bullet:
            flush()
            items.append(("bullet", [bullet.group(1).strip()]))
        elif not stripped:
            flush()
        elif stripped.startswith("|"):
            flush()
            if not re.fullmatch(r"[|\s:-]+", stripped):  # table row kept; separator row dropped
                items.append(("row", [stripped]))
        elif stripped.startswith("```"):
            flush()
        elif stripped.startswith("#"):
            flush()
            items.append(("paragraph", [stripped.lstrip("#").strip()]))
        else:
            para.append(stripped)
    flush()
    return [(kind, lines) for kind, lines in items if any(lines)]


def extract_source_items(body: str) -> list[tuple[str, str]]:
    """Return [("bullet"|"paragraph"|"row", text)] for every list item / prose block."""
    return [(kind, " ".join(lines).strip()) for kind, lines in _source_item_lines(body)]


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


# Fields that hold a heading / label (never prose): a trailing colon is list-marker punctuation there.
_LABEL_KEYS: dict[str, frozenset[str]] = {
    "heading": frozenset({"text"}),
    "headlineCard": frozenset({"title"}),
    "calloutCard": frozenset({"title"}),
    "stepCard": frozenset({"title"}),
    "keyTakeaways": frozenset({"title"}),
    "statCard": frozenset({"title", "label"}),
    "metricRow": frozenset({"label"}),
}


def strip_label_colon(text: str) -> str:
    """Drop a trailing ':' / full-width colon from a heading or label."""
    return _TRAILING_COLON_RE.sub("", text)


def clean_component(component: A2UIComponent) -> A2UIComponent:
    """Strip inline markdown from every display-text leaf of a component (code and URLs untouched)."""
    if component.type in ("codeBlock", "figureBlock"):
        return component

    label_keys = _LABEL_KEYS.get(component.type, frozenset())

    def walk(value: Any, markers: bool = True) -> Any:
        if isinstance(value, str):
            return strip_inline_markdown(value, markers=markers)
        if isinstance(value, dict):
            return {
                k: (
                    v if k in _RAW_PROP_KEYS
                    else strip_label_colon(walk(v, markers and k not in _VALUE_PROP_KEYS)) if k in label_keys and isinstance(v, str)
                    else walk(v, markers and k not in _VALUE_PROP_KEYS)
                )
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
_HIDDEN_PROP_KEYS = _RAW_PROP_KEYS | frozenset({"type", "id", "zone", "platform", "trend", "changeType", "level", "sentiment"})


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


def drop_duplicate_components(components: list[A2UIComponent]) -> list[A2UIComponent]:
    """
    Keep the first of any components whose WHOLE visible text is identical (a verdict headline followed
    by a callout holding only that verdict; a card repeating its section heading). Headings are never
    dropped. The text stays once, so nothing is lost.
    """
    seen: set[str] = set()
    kept: list[A2UIComponent] = []
    for component in components:
        text = _key(" ".join(s for s in _leaf_strings(component.props) if s.strip()))
        if text and text in seen and component.type != "heading":
            continue
        seen.add(text)
        kept.append(component)
    return kept


# Fields whose long text must be a verbatim slice of the source (prose + step descriptions).
_TRACE_FIELDS: dict[str, tuple[str, ...]] = {**_PROSE_FIELDS, "stepCard": ("title", "description")}


def _source_haystack(body: str) -> str:
    """Source as displayed text: markdown stripped line by line, hard-wrapped lines joined by a space."""
    return _norm(" ".join(strip_inline_markdown(line) for line in body.splitlines()))


def drop_untraceable_components(components: list[A2UIComponent], body: str) -> list[A2UIComponent]:
    """
    /generate copies the source verbatim. A prose component carrying a sentence-sized piece (>= 4 words)
    that occurs nowhere in the section source is a paraphrase: drop it; gap-fill then restores the
    source text. Short pieces (labels) are not checked.
    """
    haystack = _source_haystack(body)
    kept: list[A2UIComponent] = []
    for component in components:
        fields = _TRACE_FIELDS.get(component.type)
        pieces = [] if fields is None else [
            _norm(strip_inline_markdown(s)) for f in fields for s in _leaf_strings(component.props.get(f))
        ]
        traceable = all(
            len(piece.split()) < MIN_SUBSTRING_WORDS
            or piece in haystack
            or strip_label_colon(piece) in haystack
            for piece in pieces
        )
        if traceable:
            kept.append(component)
    return kept


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text)


def _has_words(hay: list[str], needle: list[str]) -> bool:
    """Whole-word, contiguous containment of one word sequence in another."""
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _step_words(component: A2UIComponent) -> list[str]:
    return _words(" ".join(_leaf_strings([component.props.get("title"), component.props.get("description")])))


def _item_words(component: A2UIComponent) -> list[list[str]]:
    return [w for s in _leaf_strings(component.props.get("items")) if (w := _words(s))]


def drop_steps_echoed_by_lists(components: list[A2UIComponent]) -> list[A2UIComponent]:
    """
    A run of stepCards directly next to a bulletList that carries the same text shows it twice. Keep one:
    the stepCards (richer structure) when the list adds nothing beyond them, otherwise the list that
    holds all the text. Whole-word matching only; nothing is reworded.
    """
    drop: set[int] = set()
    for i, component in enumerate(components):
        if component.type != "bulletList":
            continue
        items = _item_words(component)
        run: list[int] = []
        for rng in (range(i - 1, -1, -1), range(i + 1, len(components))):
            side = []
            for k in rng:
                if components[k].type != "stepCard":
                    break
                side.append(k)
            run += side
        steps = {k: _step_words(components[k]) for k in run}
        if not items or not steps:
            continue
        if all(any(_has_words(w, item) for w in steps.values()) for item in items):
            drop.add(i)
            continue
        drop.update(k for k, w in steps.items() if any(_has_words(item, w) for item in items))
    return [c for i, c in enumerate(components) if i not in drop]


def _label_and_body(component: A2UIComponent, source_keys: list[str]) -> tuple[str, str] | None:
    """
    (label, first body line) of a component that labels a body: a title-only headlineCard (body = the
    source line that follows the label) or a calloutCard holding a title and its own content.
    """
    props = component.props
    if component.type == "headlineCard" and isinstance(props.get("title"), str):
        label = _key(props["title"])
        if label and not any(_key(s) for s in _leaf_strings(props.get("summary"))) and label in source_keys:
            at = source_keys.index(label)
            if at + 1 < len(source_keys):
                return label, source_keys[at + 1]
    if component.type == "calloutCard" and isinstance(props.get("title"), str) and isinstance(props.get("content"), str):
        label, content = _key(props["title"]), _key(props["content"])
        if label and content:
            return label, content
    return None


def place_labels_before_bodies(components: list[A2UIComponent], body: str) -> list[A2UIComponent]:
    """
    A label card must come before the body it labels. `drop_repeated_prose` moves a whole-section
    survivor up to its first absorbed fragment, which can leave the label card behind it. When the label's
    body is shown by an EARLIER component: inside a bulletList the label becomes the list item right before
    that body (the card is removed, its text stays once); otherwise the card moves in front of that component.
    Order of everything else and every word are untouched.
    """
    source_keys = [k for line in body.splitlines() if (k := _key(line))]
    out = list(components)
    i = 0
    while i < len(out):
        pair = _label_and_body(out[i], source_keys)
        i += 1
        if pair is None:
            continue
        label, first_line = pair
        k = next((j for j in range(i - 1) if first_line in "\n".join(_key(s) for s in _leaf_strings(out[j].props))), None)
        if k is None:
            continue
        card, owner = out[i - 1], out[k]
        items = owner.props.get("items") if owner.type == "bulletList" else None
        if isinstance(items, list) and all(isinstance(it, dict) and isinstance(it.get("text"), str) for it in items):
            at = next((n for n, it in enumerate(items) if first_line in _key(it["text"])), None)
            if at is not None:
                if at == 0 or _key(items[at - 1]["text"]) != label:
                    owner.props = {**owner.props, "items": [*items[:at], {"text": label}, *items[at:]]}
                del out[i - 1]
                i -= 1
                continue
        del out[i - 1]
        out.insert(k, card)
    return out


# Types the web can place inside a columnLayout (COMPOSABLE_TYPES in lib/utils/agui-block-mapping.ts).
_ROW_TYPES = frozenset({"statCard", "stepCard", "headlineCard", "profileCard", "calloutCard", "linkPreview", "keyTakeaways"})
ROW_MAX_CARDS = 4
ROW_MAX_CHARS = 220
# Cards up to ROW_MAX_HALF_CHARS fit a half-width column. layout.width is NOT consulted: the selector
# stamps "full" on nearly every card (run 7), so honouring it would leave rows empty. A row holding a card
# longer than ROW_MAX_CHARS keeps at most ROW_MAX_LONG_CARDS cards (each column is ~half a reading line).
ROW_MAX_HALF_CHARS = 450
ROW_MAX_LONG_CARDS = 3


def _card_chars(component: A2UIComponent) -> int:
    return len(_norm(" ".join(_leaf_strings(component.props))))


def _is_label_card(component: A2UIComponent) -> bool:
    """A title-only headlineCard labels the body after it: it heads a stretch, it is never a row member."""
    return component.type == "headlineCard" and not any(_norm(s) for s in _leaf_strings(component.props.get("summary")))


def _is_row_card(component: A2UIComponent) -> bool:
    if component.type not in _ROW_TYPES or _is_label_card(component):
        return False
    return _card_chars(component) <= ROW_MAX_HALF_CHARS


def group_short_rows(components: list[A2UIComponent], section_id: str) -> list[A2UIComponent]:
    """
    Mark runs of 2-4 consecutive half-width-sized cards as one side-by-side row via `layout.group`.
    A heading, a label card, a long prose block (> ROW_MAX_HALF_CHARS) or any non-card component ends a run; a run with a
    mid-size card holds at most 3 per row. Longer runs split into balanced rows (never a lone
    card). Order and content are never changed.
    """
    runs: list[list[int]] = []
    current: list[int] = []
    for i, component in enumerate(components):
        if _is_row_card(component):
            current.append(i)
            continue
        runs.append(current)
        current = []
    runs.append(current)
    row = 0
    for run in runs:
        if len(run) < 2:
            continue
        longest = max(_card_chars(components[k]) for k in run)
        per_row = ROW_MAX_CARDS if longest <= ROW_MAX_CHARS else ROW_MAX_LONG_CARDS
        rows = -(-len(run) // per_row)
        base, extra = divmod(len(run), rows)
        start = 0
        for r in range(rows):
            size = base + (1 if r < extra else 0)
            row += 1
            for k in run[start:start + size]:
                components[k].layout = {**(components[k].layout or {}), "group": f"{section_id}-row-{row}"}
            start += size
    return components


def _bullet_list(texts: list[str]) -> A2UIComponent:
    return A2UIComponent(
        type="bulletList", id="bullet-list", props={"items": [{"text": t} for t in texts]}, zone="content",
    )


_TABLE_BLOCK_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*\n?){3,}", re.MULTILINE)


def _split_cells(line: str) -> list[str]:
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _table_components(body: str, leaves: list[str]) -> tuple[str, list[A2UIComponent]]:
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
        if not all(_line_covered(c, leaves) for row in rows for c in row):
            tables.append(A2UIComponent(
                type="dataTable", id="data-table", props={"headers": headers, "rows": rows}, zone="content",
            ))
        return "\n"

    return _TABLE_BLOCK_RE.sub(take, body), tables


MAX_LABEL_WORDS = 5


def _standalone_label(text: str) -> str:
    """A short label-only line emitted on its own drops its trailing colon (format, not content)."""
    return strip_label_colon(text) if _TRAILING_COLON_RE.search(text) and len(text.split()) <= MAX_LABEL_WORDS else text


def coverage_components(body: str, built: list[A2UIComponent]) -> list[A2UIComponent]:
    """
    Gap-fill for source text no component carries verbatim. Markdown tables become a dataTable; items
    carrying evidence are kept verbatim in ONE bulletList; the remaining uncovered items are merged in
    full (never cut) into a second one. Coverage is exact text, never token overlap: a component holding
    two of an item's three sentences, or the body without its label, leaves the item uncovered.
    """
    leaves = _coverage_leaves(built)
    body, tables = _table_components(body, leaves)
    evidence: list[str] = []
    rest: list[str] = []
    for _, lines in _source_item_lines(body):
        text = _uncovered_text(lines, leaves)
        if not text:
            continue
        (evidence if has_evidence(text) else rest).append(_standalone_label(_plain(text)))
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
    section_id: str = "s0",
) -> list[A2UIComponent]:
    analysis = {"document_type": "report", "title": title or "Report", "sections": [title] if title else []}
    layout = {"layout_type": "summary_layout", "confidence": 1.0, "reasoning": "report section"}
    built: list[A2UIComponent] = []
    heading = (
        [A2UIComponent(type="heading", id="heading", props={"text": strip_label_colon(title), "level": level}, zone="content")]
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
    # Reason: paraphrased LLM text is dropped BEFORE coverage so gap-fill restores the verbatim source.
    built = drop_untraceable_components(built, body)
    body_components = drop_repeated_prose(drop_empty_components([*built, *coverage_components(body, built)]))
    # Reason: the repeat pass can leave a label card behind the body it labels.
    body_components = place_labels_before_bodies(body_components, body)
    # Reason: after the repeat pass, because it can move a survivor next to the cards that echo it.
    body_components = drop_steps_echoed_by_lists(body_components)
    return group_short_rows(drop_duplicate_components([*heading, *body_components]), section_id)


def _opening(preamble: str) -> list[A2UIComponent]:
    """Text before the first `##`: title line -> level-1 heading, remaining lines kept as the intro."""
    out: list[A2UIComponent] = []
    rest = preamble.strip()
    m = re.match(r"#\s+(.+)", rest)
    if m:
        out.append(A2UIComponent(type="heading", id="heading", props={"text": strip_label_colon(m.group(1).strip()), "level": 1}, zone="content"))
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
        _build_section(t, b, select_fn, build_fn, expand_fn, apply_fn, semaphore, section_id=f"s{n}") for n, (t, b) in enumerate(jobs, 1)
    ])]
    n = 0
    for group in results:
        for component in group:
            n += 1
            # Reason: sections build concurrently; per-task id counters would collide.
            component.id = f"{component.type}-{n}"
            yield component
