# Deploy /generate on Modal

Code: `agent/modal_app.py` (wraps `main.app` unchanged). Not yet deployed.

## One-time setup (founder)
1. `pip install modal` (or `uv tool install modal`), then `modal setup` (or `modal token new`) to authenticate.
2. Secret `agui-generate-secrets` (values never go in git). For a brand-new workspace only, run
   `startup45-WebApp-v1/scripts/agui/modal-create-secret.sh`: it refuses if the secret exists (never `--force`),
   sets `API_KEY` + `ALLOWED_ORIGINS`, and a placeholder `OPENROUTER_API_KEY=SET_IN_MODAL_UI`.
   **`OPENROUTER_API_KEY` is managed only in the Modal UI; no script may overwrite the secret.**
   `API_KEY` is required: startup refuses to run on Modal without it.
   Model config lives in the secret (edit in the Modal UI, then redeploy): `GENERATE_MODEL` (e.g. `openai/gpt-5.6-sol`)
   and optional `OPENROUTER_REASONING_EFFORT` (`low|medium|high`). Code prefers `GENERATE_MODEL` over legacy
   `OPENROUTER_MODEL`; with neither set it falls back to `anthropic/claude-sonnet-4.6`. Locally, set the same vars in `agent/.env`.

## Deploy
```
cd agent && modal deploy modal_app.py
```
Prints the `https://<workspace>--agui-generate-web.modal.run` URL. Set in the web app:
`PYDANTIC_AI_AG_UI_SERVICE_URL=<that url>` and `PYDANTIC_AI_AG_UI_SERVICE_API_KEY=<API_KEY>`.

## Notes
- Modal HTTP requests time out at 150s; longer ones get a 303 redirect to a result URL, which `fetch` follows
  (modal.com/docs/guide/webhook-timeouts). Function `timeout=600`.
- Verify: `curl -s <url>/health`, then a real `POST <url>/api/generate` with `curl -L -H "Authorization: Bearer $API_KEY"`.
