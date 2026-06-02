# Experiment — `/api/generate` Latency (baseline-first)

> Living doc. Append results to the tables; never tune more than one lever per trial. Plan ref: `~/.claude/plans/first-create-an-experiment-soft-aho.md`.

## Goal
Measure real `/api/generate` latency (baseline, no changes), confirm output quality, and decide whether it's acceptable as-is. Only if too slow: reduce LLM duration with the cheapest levers (per-call model routing, token caps) **without degrading output quality**. Set caps from data — never blindly.

## Verified facts
- **3 sequential LLM calls** per request via `call_llm()` (`agent/llm_orchestrator.py:128`), each `httpx` timeout 120s:
  | # | step | max_tokens | temp |
  |---|------|-----------|------|
  | 1 | analyze content | 4000 | 0.7 |
  | 2 | select layout | 4000 | 0.7 |
  | 3 | **generate components** (heavy) | 16000 | 0.4 |
- Model = `OPENROUTER_MODEL` from `agent/.env` = `anthropic/claude-sonnet-4` → **all 3 calls = Sonnet-4** (baseline).
- Token usage currently discarded (`llm_orchestrator.py:171`).
- Per-call timing: parse `agent/logs/agent.log` timestamps (`HH:MM:SS` on `[LLM] Analyzing… / response received / Selecting layout / Selecting components / Response received`). Total: `curl -w`. Response `activity_log` has 3 ISO-timestamped phase entries.

## Quality metrics (from response — `validate_component_variety`, `prompts.py:631`)
PASS = `unique_types ≥ 4` AND `max_consecutive_same ≤ 2` AND `no type > 40%` AND valid `layout_type` AND empty `violations`. Also record `len(components)`. Keep each run's full JSON for qualitative fidelity diff across configs.

## Setup / run
```bash
# Terminal 1 — service (config UNCHANGED for baseline)
cd "/Volumes/Storage 4TB SSD/Documents2/github/agui-fastApi-service/agent"
uv run uvicorn main:app --port 8000
# Terminal 2 — logs
tail -f "/Volumes/Storage 4TB SSD/Documents2/github/agui-fastApi-service/agent/logs/agent.log"

# health
curl -s localhost:8000/health   # expect {"status":"healthy","agent_ready":true}

# API_KEY is set in agent/.env → Bearer required. Inputs are .md → build JSON with jq.
KEY=$(grep -E '^API_KEY=' .env | cut -d= -f2-)
run () {  # $1 = idea file basename (e.g. idea-1)
  jq -Rs '{markdown_content: .}' "experiments/inputs/$1.md" \
  | curl -s -o "experiments/runs/baseline-$1.json" \
      -w 'total=%{time_total}s http=%{http_code}\n' \
      -X POST localhost:8000/api/generate \
      -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' --data @-
}
# Discard one warm-up first (excludes import/cold cost), then:
run idea-1 ; run idea-2 ; run idea-3
```
Extract per run from saved JSON, e.g.:
```bash
jq '{components: (.components|length), layout: .layout_type,
     types: ([.components[].type]|unique|length),
     activity: [.activity_log[].timestamp]}' experiments/runs/baseline-idea-1.json
```

## Test inputs (Arabic Fusha, mirror real preview structure; hand-crafted proxies)
| id | idea | size |
|----|------|------|
| idea-1 | توصيل القهوة المختصة للمكاتب | concise |
| idea-2 | حجز ملاعب كرة القدم الخماسية (جدة) | medium |
| idea-3 | منصة B2B لإدارة سلاسل الإمداد للمتاجر الصغيرة | dense/long |
*(Fidelity caveat: not a captured live `generate-preview` output; structurally representative.)*

---

## Phase A — Baseline results (all Sonnet-4, config unchanged) — 2026-06-02

Run on local service (commit unchanged), model `anthropic/claude-sonnet-4` for ALL calls. Total = `curl time_total`; per-call from `logs/agent.log`.

**Actual structure observed = 4 LLM calls (not 3):** `analyze_content` runs TWICE per request (once as agent tool, once inside the orchestrator — same prompt), then `layout`, then `components` (heavy). The heavy components call dominates and scales with output size.

| run | input chars | total_s | analyze×2 | layout_s | components_s | comp_resp chars | components | uniq_types | layout_type (response) | metricRow build | status |
|-----|------------|---------|-----------|----------|--------------|-----------------|-----------|-----------|------------------------|-----------------|--------|
| warmup (tiny) | ~60 | 31.3 | 2 calls | 4 | 10 | — | 9 | 6 | `""` | ok | complete (discarded) |
| idea-1 | 1,725 | **56.7** | 2 calls | 7 | **29** | 7,496 | 19 | 8 | `""` ⚠ (log: data_layout) | **FAILED ×1** ⚠ | complete |
| idea-2 | 2,921 | **73.5** | 2 calls | 4 | **41** | 9,289 | 17 | 7 | `""` ⚠ (log: data_layout) | **FAILED ×1** ⚠ | complete |
| idea-3 | 3,883 | **83.5** | 2 calls | 4 | **52** | 9,705 | 21 | 6 | `""` ⚠ (log: data_layout) | **FAILED ×2** ⚠ | complete |

**Baseline summary**
- **Latency (`/generate` alone): 57–83s**, scaling with input size. This is ONE step of the analyze pipeline → total customer wait is higher. **Far above a good UX budget.**
- **Cost breakdown** (idea-2, 73.5s): analyze#1 ~6s + analyze#2 ~8s (**redundant**) + layout 4s + **components 41s** + build/overhead ~10s. The **heavy components call = ~51–62% of total**.
- `max_tokens` is NOT binding: outputs are ~7.5–9.7k chars (≈2–3k tokens) vs the 16k cap → lowering `max_tokens` won't help latency.

**Issues surfaced (beyond latency — pre-existing, not introduced here)**
1. **Redundant analysis:** content analyzed twice per request (~6–8s wasted). Clean dedup win, zero quality cost.
2. **`metricRow` build bug:** `generate_metric_row() got an unexpected keyword argument 'metrics'` in EVERY run → metricRow components silently dropped ("expanded from N specs" > final count). **Quality defect today.**
3. **`layout_type` lost:** layout is selected (`data_layout` per log) but the response `layout_type` is `""` — computed then not returned.
4. Raw output leans on `bulletList` (max-consecutive 4–5, dominance 42–43%) → fails the service's variety gate on raw output, BUT the web app's `groupListComponents` (`call-generate.ts`) merges consecutive bullets, mitigating UX impact. Caveat, not a hard fail.

**DECISION: baseline is NOT sufficient (latency) → Phase B.** Founder direction: keep Sonnet on the heavy components call; Step 1 = dedup + fix the 2 bugs (commit before/after).

---

## Phase B — Step 1: dedup duplicate analyze + fix metricRow + layout_type — 2026-06-02

Model unchanged (all Sonnet-4). Changes: `agent.py` (cache raw analysis, pass it through, persist layout), `llm_orchestrator.py` (orchestrator reuses precomputed analysis, emits selected layout via `meta`), `a2ui_generator.py` (`generate_metric_row` accepts a `metrics` array). +4 unit tests; full suite 476 pass.

| run | total_s baseline → postfix | Δ | components (b→p) | uniq (b→p) | layout_type (b→p) | metricRow build fails (b→p) |
|-----|----------------------------|-----|------------------|-----------|-------------------|-----------------------------|
| idea-1 | 56.7 → **48.7** | −14% | 19→16 | 8→8 | `""` → `data_layout` ✅ | 1 → **0** ✅ |
| idea-2 | 73.5 → **62.9** | −14% | 17→20 | 7→7 | `""` → `data_layout` ✅ | 1 → **0** ✅ |
| idea-3 | 83.5 → **67.1** | −20% | 21→22 | 6→9 | `""` → `data_layout` ✅ | 2 → **0** ✅ (1 metricRow now survives) |

**Verification (post-fix log):** "Analyzing content" = 3 total (1/run, was 2/run) + 3 "Reusing precomputed content analysis" → **dedup confirmed**. "Failed to build metricRow" = **0** (was 4). All runs return `data_layout`.

**Outcome:** latency −14–20% (the dedup), both pre-existing quality bugs fixed, no quality regression (component/variety deltas are LLM nondeterminism at temp 0.4/0.7; raw `max_consecutive`/`dominance` are further mitigated by the web app's `groupListComponents`). Heavy components call (Sonnet, ~29–52s) remains the dominant cost → candidate for a future step (route routine analyze+layout to Haiku; reduce/stream the heavy call) if more latency is needed.

---

## Phase B — Iteration log (future levers, if more latency needed)
One lever per trial; re-run idea-1/2/3; compare latency AND quality vs baseline. Candidate levers: (1) route routine analyze+layout calls → Haiku-4.5 (keep heavy components on Sonnet per founder); (2) reduce/stream the heavy components call; (3) token+duration logging in `call_llm` to confirm token headroom.

| trial | lever | config | total_s (Δ vs base) | components | unique_types | quality_pass | fidelity vs base | verdict |
|-------|-------|--------|---------------------|-----------|--------------|--------------|------------------|---------|
| | | | | | | | | |

**Accept** a lever only if latency improves AND quality gates hold AND fidelity not degraded.

## Final recommendation
_(fill: chosen model routing + token settings + the data-derived caps: caller `AbortController` in `startup45-WebApp-v1/inngest/steps/analyze/call-generate.ts`, service `asyncio.wait_for` deadline, inner `httpx` timeout = p95 + margin. Then back-port to `Plan-Deployment-v1.md` Latency section.)_
