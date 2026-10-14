# Deploy /generate on Modal

Code: `agent/modal_app.py` (wraps `main.app` unchanged). Not yet deployed.

## One-time setup (founder)
1. `pip install modal` (or `uv tool install modal`), then `modal setup` (or `modal token new`) to authenticate.
2. Create the secret (values never go in git):
   ```
   modal secret create agui-generate-secrets \
     OPENROUTER_API_KEY="$OPENROUTER_API_KEY" API_KEY="$API_KEY" \
     OPENROUTER_MODEL=anthropic/claude-sonnet-4.6 ALLOWED_ORIGINS="https://www.pearlhq.app"
   ```
   `API_KEY` is required: startup refuses to run on Modal without it.

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
