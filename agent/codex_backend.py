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

_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


class CodexError(Exception):
    """The Codex CLI call failed (non-zero exit, turn.failed/error event, empty reply, or timeout)."""


def backend_is_codex() -> bool:
    return os.getenv("LLM_BACKEND", "openrouter").strip().lower() == "codex"


def refuse_codex_when_deployed() -> None:
    """Raise RuntimeError if LLM_BACKEND=codex on a deployed host: the CLI login is a personal subscription."""
    if not backend_is_codex():
        return
    marker = next((m for m in _DEPLOY_MARKERS if os.getenv(m)), None)
    if marker:
        raise RuntimeError(f"LLM_BACKEND=codex is local-only and must not run when deployed ({marker} is set)")


def codex_model() -> str:
    """CODEX_MODEL if set, else GENERATE_MODEL/OPENROUTER_MODEL with any `vendor/` prefix stripped."""
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


async def _run_once(model: str, effort: str | None, stdin_prompt: str) -> tuple[str, str]:
    args = [shutil.which("codex") or "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--json", "-m", model]
    if effort:
        args += ["-c", f'model_reasoning_effort="{effort}"']
    args += ["--sandbox", "read-only", "-"]
    # Reason: empty temp cwd so the agent has nothing to read or modify.
    with tempfile.TemporaryDirectory(prefix="codex-llm-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdin=asyncio.subprocess.PIPE,
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


async def call_codex(prompt: str, system_prompt: str = "") -> str:
    """Run one prompt through the Codex CLI. temperature/max_tokens do not apply."""
    refuse_codex_when_deployed()
    model = codex_model()
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
