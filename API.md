# API Contract

Base URL: `http://localhost:8000` (configurable via `BACKEND_PORT` env var)

## Authentication

When `API_KEY` env var is set, all `POST` endpoints require a Bearer token:

```
Authorization: Bearer <API_KEY>
```

Returns `401 {"error": "Unauthorized"}` if missing or invalid. Health and info endpoints (`GET /health`, `GET /info`) are public.

If `API_KEY` is empty or unset, auth is skipped (local dev).

---

## POST / — AG-UI Streaming Endpoint

CopilotKit/AG-UI protocol endpoint. Returns an SSE stream of events as the dashboard is generated.

### Request

Sent by CopilotKit's `HttpAgent`. Body follows the AG-UI `RunAgentInput` schema:

```json
{
  "threadId": "uuid",
  "runId": "uuid",
  "state": {
    "markdown_content": "# Your markdown..."
  },
  "messages": [
    { "id": "uuid", "role": "user", "content": "Please analyze this markdown..." }
  ],
  "tools": [],
  "context": []
}
```

### Response

`Content-Type: text/event-stream`

Events are emitted in SCREAMING_SNAKE_CASE format:

| Event | Description |
|-------|-------------|
| `RUN_STARTED` | Agent run has begun |
| `STATE_SNAPSHOT` | Updated `DashboardState` (components accumulate here) |
| `TOOL_CALL_START` / `TOOL_CALL_END` | Tool execution boundaries |
| `TEXT_MESSAGE_*` | Agent text output chunks |
| `RUN_FINISHED` | Final state + agent output |
| `RUN_ERROR` | Pipeline failure |

Each `STATE_SNAPSHOT` contains the full `DashboardState` (see schema below).

---

## POST /api/generate — Synchronous JSON Endpoint

Same pipeline, no streaming. Runs the agent to completion and returns the final state as a single JSON response.

### Request

```json
{
  "markdown_content": "# Your markdown document here..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `markdown_content` | string | Yes | Non-empty markdown text to transform into a dashboard |

### Response (200)

Returns the complete `DashboardState`:

```json
{
  "markdown_content": "# Your markdown...",
  "document_title": "Extracted Title",
  "document_type": "tutorial",
  "content_analysis": { ... },
  "layout_type": "instructional",
  "components": [
    {
      "type": "tldr",
      "id": "tldr-1",
      "props": { "content": "..." },
      "layout": { "width": "full" },
      "zone": "hero"
    }
  ],
  "status": "complete",
  "progress": 100,
  "current_step": "Dashboard complete!",
  "activity_log": [
    {
      "id": "uuid",
      "message": "Analysis complete: tutorial",
      "timestamp": "2026-03-28T12:00:00",
      "status": "completed"
    }
  ],
  "error_message": null
}
```

### Errors

| Status | Body | Cause |
|--------|------|-------|
| 400 | `{"error": "Invalid JSON body"}` | Malformed JSON |
| 400 | `{"error": "markdown_content is required and must be non-empty"}` | Missing or blank input |
| 500 | `{"error": "<message>"}` | Pipeline or LLM failure |

### Latency

This endpoint blocks until the full pipeline completes (content analysis + component generation). Expect 10-30s depending on document length and LLM response times.

---

## DashboardState Schema

Shared output shape for both endpoints.

| Field | Type | Description |
|-------|------|-------------|
| `markdown_content` | string | Original input |
| `document_title` | string | Extracted document title |
| `document_type` | string | Classification: `tutorial`, `research`, `article`, `guide`, `notes`, `technical_doc`, `overview` |
| `content_analysis` | object | Parsed structure + LLM analysis results |
| `layout_type` | string | Selected layout: `instructional`, `data`, `news`, `list`, `summary`, `reference`, `media` |
| `components` | array | Component specs in camelCase format (see below) |
| `status` | string | `idle` / `analyzing` / `generating` / `complete` / `error` |
| `progress` | integer | 0-100 |
| `current_step` | string | Human-readable status text |
| `activity_log` | array | Timestamped processing events |
| `error_message` | string or null | Error detail when `status` is `error` |

### Component Object

Types use camelCase naming (15 canonical types):

`tldr`, `keyTakeaways`, `statCard`, `metricRow`, `dataTable`, `headlineCard`, `calloutCard`, `quoteCard`, `bulletList`, `codeBlock`, `stepCard`, `comparisonTable`, `vsCard`, `linkPreview`, `profileCard`

```json
{
  "type": "statCard",
  "id": "stat-card-1",
  "props": { "value": "$196B", "label": "Market Size", "trend": "up" },
  "layout": { "width": "third" },
  "zone": "metrics"
}
```

---

## Utility Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Endpoint listing |
| GET | `/info` | Agent name, version, protocol |
| GET | `/health` | `{"status": "healthy", "agent_ready": bool}` |

---

## Retry & Error Recovery

The backend has **no automatic retry** on LLM failures. Specifically:

- **Pydantic AI agent** uses default `retries=1` (tool validation retries only, not HTTP retries).
- **`call_llm()` in the orchestrator** fails immediately on non-200 HTTP responses with no retry or backoff.
- **Content analysis** degrades gracefully: if the LLM call fails, it falls back to heuristic classification. The pipeline continues.
- **Component selection** does not retry: if the LLM call fails, the error propagates and the request fails with 500.
- **Individual component building** is fault-tolerant: a single component failure is skipped, generation continues for the rest.
- **JSON parsing** has truncation recovery: partial/malformed LLM output is recovered where possible.

**Consumer guidance**: Implement retry on your side for 500 errors. A simple retry with backoff (e.g. 1s, 3s, 10s) is sufficient. 400 errors are not retryable.
