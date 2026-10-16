"""
Modal deployment of the /generate service (wraps main.app unchanged).

Deploy (from the agent/ dir):  modal deploy modal_app.py
See ../DEPLOY-MODAL.md. Docs: https://modal.com/docs/guide/webhooks
"""

from pathlib import Path

import modal

APP_DIR = Path(__file__).parent
REMOTE_DIR = "/app"

# Reason: uv_sync installs from uv.lock (frozen=True default) into /.uv/.venv and puts it on PATH;
# --no-dev skips pytest. Reusing the Dockerfile via Image.from_dockerfile also works but would
# duplicate its uv multi-stage build; uv_sync is the lockfile-native path.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_sync(str(APP_DIR), extra_options="--no-dev")
    .add_local_dir(
        APP_DIR,
        remote_path=REMOTE_DIR,
        ignore=["tests", "logs", "experiments", "__pycache__", ".venv", ".env*", "*.pyc"],
    )
)

app = modal.App("agui-generate", image=image)


# Reason: Modal's HTTP request limit is 150s; longer requests get a 303 to a result URL
# (modal.com/docs/guide/webhook-timeouts). timeout=600 lets the function outlive that.
@app.function(
    secrets=[modal.Secret.from_name("agui-generate-secrets")],  # OPENROUTER_API_KEY, API_KEY, GENERATE_MODEL, OPENROUTER_REASONING_EFFORT
    timeout=600,
    scaledown_window=300,
    # min_containers=1,  # uncomment to avoid cold starts (billed while idle)
    # region="eu",       # optional; adds a 1.15x price multiplier (modal.com/docs/guide/region-selection)
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    import sys

    sys.path.insert(0, REMOTE_DIR)  # main.py uses bare imports (`from agent import agent`)
    from main import app as asgi_app

    return asgi_app
