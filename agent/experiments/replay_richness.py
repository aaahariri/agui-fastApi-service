"""Replay a report through the section pipeline; print per-step component counts + final shape.
Usage: uv run python experiments/replay_richness.py <report.md> [--dropped]"""
import asyncio, collections, sys, os, json
sys.path.insert(0, os.environ.get('BASE_DIR') or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import section_orchestrator as so
import llm_orchestrator as lo
from functools import partial

STEPS = ["clean_component", "drop_untraceable_components", "drop_empty_components", "drop_repeated_prose",
         "place_labels_before_bodies", "drop_steps_echoed_by_lists", "drop_duplicate_components", "group_short_rows"]
counts: dict[str, list[int]] = collections.defaultdict(list)
gapped: list[str] = []
dropped: list[tuple[str, str]] = []
orig_coverage = so.coverage_components

def wrap(name):
    fn = getattr(so, name)
    def inner(*a, **k):
        out = fn(*a, **k)
        if name == "clean_component":
            counts[name].append(1)
        else:
            counts[name].append(len(out))
            counts[name + "_in"].append(len(a[0]))
            if name in ("drop_untraceable_components", "drop_repeated_prose"):
                kept = {id(c) for c in out}
                for c in a[0]:
                    if id(c) not in kept:
                        dropped.append((name[5:20] + ":" + c.type, json.dumps(c.props, ensure_ascii=False)[:260]))
        return out
    setattr(so, name, inner)

def cov(body, built):
    out = orig_coverage(body, built)
    counts["gap_fill"].append(len(out)); counts["gap_fill_in"].append(len(built))
    for c in out:
        for i in c.props.get("items", []): gapped.append(i["text"][:200])
    return out
so.coverage_components = cov
import re as _re2
_DIAC = _re2.compile("[\u064b-\u065f\u0670\u0640]")
def _noise(t, level):
    if level >= 2:
        t = _DIAC.sub("", t).replace("\u061b", "\u060c").replace(" - ", " ")
        t = t.rstrip(".")
    return t

async def mock_select(analysis, layout, text, extra, allow_empty=False):
    """Deterministic stand-in for a faithful LLM: label+paragraph -> titled callout, short lists -> keyTakeaways,
    figure lines -> statCard. MOCK=1 exact copy, 2 diacritics/punctuation changed, 3 paragraphs split per sentence."""
    level = int(os.environ["MOCK"])
    body = text.split("\n\n", 1)[-1]
    specs, label, bullets = [], "", []
    def flush():
        nonlocal bullets, label
        if bullets:
            if len(bullets) <= 5:
                specs.append({"component_type": "keyTakeaways", "props": {"title": label, "items": [_noise(b, level) for b in bullets]}})
            else:
                specs.append({"component_type": "bulletList", "props": {"items": [{"text": _noise(b, level)} for b in bullets]}})
            bullets, label = [], ""
    for kind, lines in so._source_item_lines(body):
        t = " ".join(lines)
        if kind == "bullet":
            bullets.append(t); continue
        flush()
        words = len(_re2.findall(r"\w+", t))
        if words < 4 and t.rstrip().endswith(":") or (words < 4 and not _re2.search(r"\d", t)):
            label = so.strip_label_colon(t); continue
        m = _re2.match(r"^(.{3,40}?):\s*(\S*\d[\d.,%٪]*\S*)\s*$", t)
        if m:
            specs.append({"component_type": "statCard", "props": {"label": m.group(1), "value": m.group(2), "title": label}}); label = ""; continue
        if level >= 3:
            sents = [x for x in _re2.split(r"(?<=[.\u061f?!\u061b;])\s+", t) if x.strip()]
            for n, sent in enumerate(sents):
                specs.append({"component_type": "calloutCard", "props": {"type": "info", "title": label if n == 0 else "", "content": _noise(sent, level)}})
        else:
            specs.append({"component_type": "calloutCard", "props": {"type": "info", "title": label, "content": _noise(t, level)}})
        label = ""
    flush()
    return specs
if os.environ.get("MOCK"):
    lo.select_components_with_llm = mock_select

import hashlib, pathlib
CACHE = pathlib.Path(os.environ.get("LLM_CACHE", "/tmp/llm_cache.json"))
_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
_call = lo.call_llm
async def cached_call(prompt, system_prompt="", max_tokens=4000, temperature=0.7):
    # Reason: key by the section text alone so a recorded LLM answer can be replayed through any pipeline version.
    section = prompt.split("## Actual Document Content", 1)[-1].split("CRITICAL: You must generate", 1)[0]
    import re as _re
    m = _re.search(r"^## (.+)$", section, _re.M)
    k = "sec:" + (m.group(1).strip() if m else hashlib.sha256(section.encode()).hexdigest())
    if k not in _cache and os.environ.get("OFFLINE"):
        raise SystemExit("no recorded answer for a section (OFFLINE)")
    if k not in _cache:
        _cache[k] = await _call(prompt, system_prompt, max_tokens=min(max_tokens, int(os.environ.get("CAP", "16000"))), temperature=temperature)
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False))
    return _cache[k]
lo.call_llm = cached_call
_ms = getattr(so, '_missing_sentences', None)
def ms(line, leaves):
    out = _ms(line, leaves)
    if out and os.environ.get('MS'): print("  MISSING-SENT:", [x[:90] for x in out], "| LEAVES-near:", [l[:70] for l in leaves if any(w in l for w in so._fold(out[0]).split()[:3])][:3])
    return out
if _ms: so._missing_sentences = ms
for n in STEPS:
    wrap(n)

async def main(path, show):
    md = open(path).read()
    comps = []
    async for c in lo.orchestrate_dashboard_with_llm(md):
        comps.append(c)
    types = collections.Counter(c.type for c in comps)
    if os.environ.get("SEQ"):
        for c in comps: print("  SEQ", c.type, so._card_shape(c) if hasattr(so, "_card_shape") else "", len(" ".join(so._leaf_strings(c.props))), (c.layout or {}).get("group"))
    print("FINAL", len(comps), dict(types))
    for n in ["drop_untraceable_components", "gap_fill", "drop_empty_components", "drop_repeated_prose", "place_labels_before_bodies",
              "drop_steps_echoed_by_lists", "drop_duplicate_components", "group_short_rows"]:
        print(f"{n}: in={sum(counts.get(n + '_in', []))} out={sum(counts[n])}")
    print("built_by_llm(after clean):", sum(counts["clean_component"]))
    rows = collections.defaultdict(list)
    for c in comps:
        g = (c.layout or {}).get("group")
        if g: rows[g].append(f"{c.type}:{(c.props.get('title') or c.props.get('label') or '')[:25]}")
    print("ROWS", len(rows)); [print(" ", k, v) for k, v in rows.items()]
    bl = sum(len(" ".join(i["text"] for i in c.props.get("items", []))) for c in comps if c.type == "bulletList")
    tot = sum(len(" ".join(so._leaf_strings(c.props))) for c in comps if c.type != "heading")
    print(f"BULLET_SHARE {bl}/{tot} = {bl/max(tot,1):.2f}")
    # missing source lines: independent, strict metric (line must appear contiguously, markup/colon aside)
    import re
    def k(t): return " ".join(re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", "", t.strip()).replace("*", "").replace("`", "").rstrip(":\uff1a").split())
    visible = k(" ".join(x for c in comps for x in so._leaf_strings({kk: v for kk, v in c.props.items() if kk != "url"})))
    lines = [k(l) for l in md.splitlines() if l.strip() and not l.strip().startswith("|") and not re.fullmatch(r"[-*_=\s]+", l.strip())]
    miss = [l for l in lines if re.search(r"\w", l) and l not in visible]
    print("MISSING_LINES", len(miss), [m[:60] for m in miss[:3]])
    if show:
        print("GAPFILLED", len(gapped)); [print("  +", g) for g in gapped]
        print("DROPPED", len(dropped))
        for t, p in dropped[:int(os.environ.get('N','14'))]: print(" -", t, p)

asyncio.run(main(sys.argv[1], "--dropped" in sys.argv))
