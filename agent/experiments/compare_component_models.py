"""Replay a report through the section pipeline on the Codex backend with COMPONENT_MODEL fixed; print one JSON line of metrics.
Usage: COMPONENT_MODEL=gpt-5.6-luna uv run python experiments/compare_component_models.py <report.md> <out.json>"""
import asyncio, collections, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm_orchestrator as lo
import section_orchestrator as so

calls, gap_chars = [], [0]
_call = lo.call_llm
_cov = so.coverage_components


async def timed(prompt, system_prompt="", max_tokens=4000, temperature=0.7, model=None):
    t = time.time()
    rec = {"model": model, "start": t}
    calls.append(rec)
    try:
        out = await _call(prompt, system_prompt, max_tokens=max_tokens, temperature=temperature, model=model)
    except Exception as e:
        rec.update(error=str(e)[:300], secs=time.time() - t)
        raise
    rec.update(secs=time.time() - t, chars=len(out), raw=out)
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", out)
    try:
        json.loads(m.group(1).strip() if m else out)
        rec["strict_json"] = True
    except Exception:
        rec["strict_json"] = False
    return out


def cov(body, built):
    out = _cov(body, built)
    for c in out:
        gap_chars[0] += sum(len(i["text"]) for i in c.props.get("items", []))
    return out


lo.call_llm = timed
so.coverage_components = cov


def k(t):
    return " ".join(re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", "", t.strip()).replace("*", "").replace("`", "").rstrip(":：").split())


async def main(path, out_path):
    md = open(path).read()
    comps = [c async for c in lo.orchestrate_dashboard_with_llm(md)]
    types = collections.Counter(c.type for c in comps)
    rows = collections.defaultdict(list)
    for c in comps:
        g = (c.layout or {}).get("group")
        if g:
            rows[g].append({"type": c.type, "titled": bool(c.props.get("title") or c.props.get("label")), "title": str(c.props.get("title") or c.props.get("label") or "")[:40]})
    total = sum(len(" ".join(so._leaf_strings(c.props))) for c in comps if c.type != "heading")
    visible = k(" ".join(x for c in comps for x in so._leaf_strings({kk: v for kk, v in c.props.items() if kk != "url"})))
    lines = [k(l) for l in md.splitlines() if l.strip() and not l.strip().startswith("|") and not re.fullmatch(r"[-*_=\s]+", l.strip())]
    miss = [l for l in lines if re.search(r"\w", l) and l not in visible]
    sel = [c for c in calls if "raw" in c or "error" in c]
    res = {"types": dict(types), "n": len(comps), "gap_share": round(gap_chars[0] / max(total, 1), 3), "rows": list(rows.values()),
           "missing": len(miss), "missing_ex": [m[:70] for m in miss[:3]], "calls": len(sel), "call_models": sorted({str(c["model"]) for c in sel}),
           "secs": [round(c["secs"], 1) for c in sel], "strict_json_fail": sum(1 for c in sel if c.get("strict_json") is False),
           "errors": [c["error"] for c in sel if "error" in c],
           "comps": [{"type": c.type, "props": c.props} for c in comps]}
    json.dump(res, open(out_path, "w"), ensure_ascii=False)
    print(json.dumps({k_: v for k_, v in res.items() if k_ != "comps"}, ensure_ascii=False))

asyncio.run(main(sys.argv[1], sys.argv[2]))
