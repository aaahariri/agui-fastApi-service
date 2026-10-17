"""Local-only LLM backend: runs the Codex CLI (ChatGPT/Codex subscription) instead of OpenRouter.

Enabled with LLM_BACKEND=codex. Refused on Modal/Railway/Cloud Run (see refuse_codex_when_deployed).
Non-interactive call pattern mirrors the web repo's scripts/benchmarks/analyze-models/run-benchmark.ts.
"""
import asyncio
import json
import os
import re
import shutil
import tempfile

from logger import logger

CODEX_TIMEOUT_SECONDS = 600
MAX_ATTEMPTS = 3
DEFAULT_MAX_CONCURRENCY = 2
_RATE_LIMIT_RE = re.compile(r"rate|limit|quota|usage|429|too many", re.IGNORECASE)
_DEPLOY_MARKERS = ("MODAL_ENVIRONMENT", "MODAL_TASK_ID", "MODAL_IS_REMOTE", "RAILWAY_ENVIRONMENT", "K_SERVICE")

# Reason: the model reads untrusted web content, so it must have no tools (prompt injection -> file read/exfiltration).
# Verified on codex-cli 0.153.4: with the default flags `cat <file>`/`ls ~` succeed; with these flags no shell,
# patch, image-read, MCP/app or multi-agent tool is exposed and the code-mode `exec` tool fails closed.
HARDENING_ARGS = [
    "--ignore-user-config", "--ignore-rules",
    *[a for f in ("shell_tool", "unified_exec", "code_mode_host", "code_mode", "apps", "plugins", "browser_use",
                  "browser_use_external", "computer_use", "image_generation", "multi_agent", "multi_agent_v2",
                  "hooks", "skill_search", "in_app_browser") for a in ("--disable", f)],
    "-c", 'web_search="disabled"', "-c", "include_apply_patch_tool=false", "-c", "tools.view_image=false",
]
_CHILD_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE")

_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


class CodexError(Exception):
    """The Codex CLI call failed (non-zero exit, turn.failed/error event, empty reply, or timeout)."""


def _deploy_marker() -> str | None:
    return next((m for m in _DEPLOY_MARKERS if os.getenv(m)), None)


def resolved_backend() -> str:
    """LLM_BACKEND if set; else openrouter on a deployed host (cloud marker present), codex locally."""
    explicit = os.getenv("LLM_BACKEND", "").strip().lower()
    if explicit:
        return explicit
    return "openrouter" if _deploy_marker() else "codex"


def backend_is_codex() -> bool:
    return resolved_backend() == "codex"


def refuse_codex_when_deployed() -> None:
    """Raise RuntimeError if LLM_BACKEND=codex on a deployed host: the CLI login is a personal subscription."""
    if not backend_is_codex():
        return
    marker = _deploy_marker()
    if marker:
        raise RuntimeError(f"LLM_BACKEND=codex is local-only and must not run when deployed ({marker} is set)")


def codex_model(model_override: str | None = None) -> str:
    """Per-call override (COMPONENT_MODEL), else CODEX_MODEL, else GENERATE_MODEL/OPENROUTER_MODEL; `vendor/` prefix stripped."""
    if model_override:
        return model_override.split("/", 1)[-1]
    explicit = os.getenv("CODEX_MODEL")
    if explicit:
        return explicit
    model = os.getenv("GENERATE_MODEL") or os.getenv("OPENROUTER_MODEL", "")
    return model.split("/", 1)[-1]


def parse_events(stdout: str) -> tuple[str, str]:
    """Parse `codex exec --json` JSONL into (last agent_message text, concatenated error messages)."""
    text, errors = "", []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
            text = event["item"].get("text", "")
        elif kind in ("turn.failed", "error"):
            message = (event.get("error") or {}).get("message") or event.get("message") or ""
            if message:
                errors.append(message)
    return text, " ".join(errors)


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _semaphores:
        _semaphores[loop] = asyncio.Semaphore(int(os.getenv("CODEX_MAX_CONCURRENCY") or DEFAULT_MAX_CONCURRENCY))
    return _semaphores[loop]


def _reset_semaphore() -> None:
    _semaphores.clear()


def _combine(prompt: str, system_prompt: str) -> str:
    if not system_prompt:
        return prompt
    return f"=== SYSTEM INSTRUCTIONS ===\n{system_prompt}\n\n=== USER REQUEST ===\n{prompt}"


def _isolated_env(home: str) -> dict[str, str]:
    """Child env: a throwaway CODEX_HOME holding only a link to the ChatGPT login, and none of our secrets."""
    real_home = os.getenv("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    auth = os.path.join(real_home, "auth.json")
    if os.path.exists(auth):
        os.symlink(auth, os.path.join(home, "auth.json"))
    env = {k: os.environ[k] for k in _CHILD_ENV_KEYS if k in os.environ}
    env.update(CODEX_HOME=home, HOME=home)
    return env


async def _run_once(model: str, effort: str | None, stdin_prompt: str) -> tuple[str, str]:
    args = [shutil.which("codex") or "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--json", "-m", model]
    if effort:
        args += ["-c", f'model_reasoning_effort="{effort}"']
    args += [*HARDENING_ARGS, "--sandbox", "read-only", "-"]
    # Reason: empty temp cwd so the agent has nothing to read or modify.
    with tempfile.TemporaryDirectory(prefix="codex-llm-") as cwd, tempfile.TemporaryDirectory(prefix="codex-home-") as home:
        env = _isolated_env(home)
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin_prompt.encode()), CODEX_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            raise CodexError(f"codex timed out after {CODEX_TIMEOUT_SECONDS}s")
    text, event_error = parse_events(out.decode(errors="replace"))
    if proc.returncode == 0 and text and not event_error:
        return text, ""
    stderr_tail = err.decode(errors="replace")[-300:]
    return "", event_error or (f"exit {proc.returncode}: {stderr_tail}" if proc.returncode else "no agent_message")


async def call_codex(prompt: str, system_prompt: str = "", model_override: str | None = None) -> str:
    """Run one prompt through the Codex CLI. temperature/max_tokens do not apply."""
    refuse_codex_when_deployed()
    model = codex_model(model_override)
    effort = os.getenv("OPENROUTER_REASONING_EFFORT") or None
    stdin_prompt = _combine(prompt, system_prompt)
    print(f"[llm] backend=codex model={model} reasoning_effort={effort}", flush=True)
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        async with _semaphore():
            try:
                text, last_error = await _run_once(model, effort, stdin_prompt)
            except OSError as exc:
                raise CodexError(f"could not start codex CLI: {exc}") from exc
        if text:
            return text
        logger.error(f"[llm] codex failed (attempt {attempt}/{MAX_ATTEMPTS}): {last_error[:300]}")
        if attempt < MAX_ATTEMPTS:
            rate = bool(_RATE_LIMIT_RE.search(last_error))
            await asyncio.sleep((90 if rate else 10) * attempt)
    raise CodexError(f"codex failed after {MAX_ATTEMPTS} attempts: {last_error[:400]}")
