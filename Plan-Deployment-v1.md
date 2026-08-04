# Plan-Deployment-v1 — Deploy AG-UI FastAPI service to cloud

**Goal:** Get this service reachable over HTTPS so the Startup45 web app's analyze `/generate` step can call it in prod. Today it runs local-only → prod analyze is down (dies at `/generate`).

**Status:** code-ready, not yet deployed. Dockerfile exists, local Docker test passed. Host = **Railway**. See [Progress & Pending](#progress--pending-2026-06-07) for the live checklist.

---

## Service facts (verified)

| | |
|---|---|
| App | `agent/main.py` → `app` (Starlette ASGI; uvicorn-served) |
| Run cmd | `uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT` — **CWD must be `agent/`** (bare imports, no package prefix) |
| Port | `8000` (env `BACKEND_PORT`) |
| Python | ≥3.10 → pin **3.12** |
| Deps | **uv** + `agent/uv.lock` → `uv sync --frozen`. ⚠️ `requirements.txt` is incomplete (missing `ag-ui-protocol`, `copilotkit`, `python-dateutil`) — **use uv, not pip** |
| System deps | none (pure Python, no models, stateless) |
| Healthcheck | `GET /health` → `{status, agent_ready}`, no auth |
| Inbound contract | `POST /api/generate` Bearer-auth, body `{"markdown_content": str}` → single JSON `DashboardState` (no stream) |
| Max req duration | LLM `httpx` timeout = **120s** (`llm_orchestrator.py:149`) → host MUST allow **>120s** requests |

### Env vars
| Var | Req | Prod value |
|---|---|---|
| `OPENROUTER_API_KEY` | **yes** | OpenRouter key (service non-functional without it) |
| `API_KEY` | **yes (prod)** | Inbound Bearer token. **Must equal the web app's `PYDANTIC_AI_AG_UI_SERVICE_API_KEY`** |
| `ALLOWED_ORIGINS` | **yes (prod)** | `https://www.pearlhq.app` (CORS; default is localhost → blocks prod) |
| `OPENROUTER_MODEL` | no | ⚠️ defaults differ: `agent.py`=`anthropic/claude-sonnet-4`, `llm_orchestrator.py`=`anthropic/claude-haiku-4.5`. Set explicitly to avoid drift |
| `BACKEND_PORT` | no | host-injected port (Railway sets `$PORT` → map to `BACKEND_PORT` or bind `$PORT`) |

---

## Host decision: **Railway**

| Host | Req timeout | Verdict |
|---|---|---|
| **Railway** | 900s | ✅ Chosen — runs vanilla app as-is (Dockerfile/Nixpacks), ~$5–12/mo, no lock-in, generous timeout |
| Modal | 150s hard | ❌ Only 30s margin over the 120s httpx timeout → intermittent 504s; also needs SDK code adoption |
| Render ($7) / Fly | ~100min / configurable | ✅ Viable alternates if Railway dropped |

---

## Steps

1. **Add `Dockerfile`** (repo root or `agent/`):
   - `FROM python:3.12-slim`; install `uv`; `WORKDIR /app/agent`; copy repo; `uv sync --frozen`.
   - CMD: `uv run uvicorn main:app --host 0.0.0.0 --port ${BACKEND_PORT:-8000}`.
   - Add `.dockerignore` (`frontend/`, `sample-documents/`, `.venv`, `__pycache__`, `logs/`).
2. **Railway project** → deploy from this repo (root dir = `agent/` if Nixpacks, or Dockerfile). Bind to Railway `$PORT` (set `BACKEND_PORT=$PORT` or read `PORT`).
3. **Set env vars/secrets** in Railway: `OPENROUTER_API_KEY`, `API_KEY` (generate a strong token), `ALLOWED_ORIGINS=https://www.pearlhq.app`, `OPENROUTER_MODEL`.
4. **Verify** (see below).
5. **Hand off** the public URL + `API_KEY` value → web-app wiring issue (set `PYDANTIC_AI_AG_UI_SERVICE_URL` + `PYDANTIC_AI_AG_UI_SERVICE_API_KEY` in Vercel prod). Tracked in Linear (INN webapp project).

---

## Verify (post-deploy)
```bash
# health (no auth) — expect {"status":"healthy","agent_ready":true}
curl -s https://<railway-url>/health

# generate (auth) — expect 200 + DashboardState JSON
curl -s -X POST https://<railway-url>/api/generate \
  -H "Authorization: Bearer <API_KEY>" -H "Content-Type: application/json" \
  -d '{"markdown_content":"# اختبار\nفكرة تجريبية"}'

# wrong/no key → expect 401
```

## Gotchas
- CWD/imports: must run from `agent/` or imports break.
- Use `uv sync --frozen` (pip path is incomplete).
- Set `ALLOWED_ORIGINS` + `API_KEY` or you get prod CORS blocks / open auth.
- `/generate` is synchronous up to 120s — keep host timeout > 120s (Railway ok).
- Web app consumes the URL as `${PYDANTIC_AI_AG_UI_SERVICE_URL}/api/generate` — set `PYDANTIC_AI_AG_UI_SERVICE_URL` to the **base** URL (no `/api/generate` suffix).

---

## Latency budget & caps — EXPERIMENT FIRST

**Principle:** the 120s `httpx` timeout is a safety ceiling, NOT a target. Customers must never wait near it. **Measure before capping — do not set any hard cap until we have p50/p95 data.**

### Current state (problems)
- `/api/generate` is **synchronous** and makes **multiple sequential LLM calls** (`agent.run()` → `analyze_content` → `generate_components`) → total can exceed 120s.
- Caller (web app `call-generate.ts`) has **no timeout** → hangs until Vercel 300s. (Caller `AbortController` cap is DEFERRED until experiments set the number.)
- Model defaults are inconsistent and arguably backwards: `agent.py`=`claude-sonnet-4` (orchestration), `llm_orchestrator.py`=`claude-haiku-4.5` (the actual generation). Haiku is fine for routine sub-steps but **under-powered for analysis-heavy generation** → both a latency AND quality concern.

### Experiment (do this first)
1. **Instrument** each LLM call: log `model`, `tokens_in`, `tokens_out`, `duration_ms`, + total `/generate` duration and component count.
2. Run **~10 representative Saudi-market ideas**; record **p50/p95** per call and total.
3. Capture **quality** alongside latency (don't optimize latency at the cost of analysis depth).

### Levers to experiment with (not a blanket model swap)
- **Per-task model assignment:** keep a capable model (Sonnet-class) for analysis-heavy generation; route only *routine/structured* sub-steps (classification, layout selection, simple formatting) to a fast/cheap model (Haiku). Make model an explicit per-call choice, not a module default.
- **`max_tokens` caps** on routine/structured calls (biggest easy latency win; large structured outputs dominate).
- **Reduce/parallelize** the sequential round-trips where independent.
- **Prompt trimming** on routine steps.
- **Perceived latency:** the service already streams (SSE on `POST /`) — consider surfacing progress to the UI even if total stays similar.

### Then set caps (from data, layered)
- Caller fetch `AbortController` (web app `call-generate.ts`) = p95 + margin.
- Service overall deadline: `asyncio.wait_for(agent.run(...), N)` → clean timeout response.
- Inner per-call `httpx` timeout lowered from 120s to fit the budget.

**Hypotheses to validate (not yet decisions):** hard cap `/generate` ~45–60s; target p95 ~25–35s.

### Outcome (2026-06-02 — see `Experiment-Latency-v1.md`)
Experiment done. **Shipped:** analyze-call dedup (≈14–20% faster) + metricRow build fix + layout_type now returned → `/generate` 57–83s → 49–67s. **Model: kept Sonnet-4** (Haiku ≈ same speed across 10 runs + lower reliability; not adopted). **No latency caps set** — latency isn't model-bound and is acceptable for the async/polling UX; streaming/output-reduction deferred. Deploy this service to cloud as-is (above), set the two env vars, then resume INN-1101.

---

## Progress & Pending (2026-06-07)

**Done — code-ready (uncommitted):**
- [x] `agent/Dockerfile` + `.dockerignore` — 2-stage, `uv sync --frozen`, binds `$PORT`, keep-alive 130s, `/health` check. Build OK 568MB.
- [x] Crash-fix: `agent.py` lazy model (`model="openrouter:…"`, `defer_model_check=True`) → boots without key, no crash-loop.
- [x] Lean logging: `logger.py` stdout-only; file handler gated behind `LOG_FILE` env (rely on Railway log capture, ephemeral disk).
- [x] Model unified → `anthropic/claude-sonnet-4.6` in `agent.py` + `llm_orchestrator.py` (no Haiku on generation). Fallback: `openai/gpt-5`.
- [x] Hardening: `on_event`→`lifespan`; `_id_counter`→`ContextVar` (per-request); OpenRouter key/model + `HTTP-Referer` read at call-time via env.
- [x] Verified: import OK w/o key · 587 tests pass · local Docker e2e = 18 components, clean 500 on no-key, container stays UP.

**Pending — user/config (blocks go-live):**
- [ ] Rotate `OPENROUTER_API_KEY` + `API_KEY` (currently in `agent/.env` on disk).
- [ ] Railway: **Root Directory=`agent`**, Healthcheck=`/health`; set `OPENROUTER_API_KEY`, `API_KEY`, `ALLOWED_ORIGINS=https://www.pearlhq.app`. `OPENROUTER_MODEL` optional (defaults to sonnet-4.6).
- [ ] Deploy via Railway MCP (**needs fresh session** — MCP not loaded when this work was done).
- [ ] Hand off public URL + `API_KEY` → web app `PYDANTIC_AI_AG_UI_SERVICE_URL`/`_API_KEY` (Vercel prod), resume INN-1101.

**Optional / deferred:**
- [ ] Enable `reasoning:{effort:high}` on OpenRouter call (deeper thinking; +latency).
- [ ] Fix pre-existing test `test_response_contains_all_state_fields` (asserts `analysis_cache`, which is `exclude=True`).
