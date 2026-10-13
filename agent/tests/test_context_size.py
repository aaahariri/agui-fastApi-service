"""Regression: no single LLM call may carry the whole document more than once (500 'context limit exceeded')."""

import asyncio
import json

import pytest
from pydantic_ai.ag_ui import StateDeps
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agent as agent_module
import llm_orchestrator as orchestrator

SECTION_CHARS = 6000
SECTIONS = 12


def _doc() -> str:
    body = "نص عربي طويل " * (SECTION_CHARS // 14)
    return "# Report\n\n" + "\n\n".join(f"## Section {i}\n\n- {body}" for i in range(SECTIONS))


def test_per_call_prompt_and_agent_history_stay_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()
    assert len(doc) > 60000
    prompt_sizes: list[int] = []

    async def fake_llm(prompt: str, system_prompt: str = "", max_tokens: int = 4000, temperature: float = 0.7) -> str:
        prompt_sizes.append(len(prompt) + len(system_prompt))
        if "Actual Document Content" in prompt:
            comps = [{"component_type": t, "priority": 1, "zone": "content", "props": {"items": ["x"]}}
                     for t in ("BulletList", "CalloutCard", "StatCard", "HeadlineCard")]
            return json.dumps({"components": comps})
        return json.dumps({"document_type": "report", "title": "t"})

    monkeypatch.setattr(orchestrator, "call_llm", fake_llm)

    history_sizes: list[int] = []
    step = [0]

    async def scripted(messages: list, info: AgentInfo) -> ModelResponse:
        # Reason: ToolReturnPart.metadata is app-only (never sent to the model); count what the model receives.
        history_sizes.append(sum(len(str(getattr(p, "content", p))) for m in messages for p in m.parts))
        step[0] += 1
        if step[0] == 1:
            return ModelResponse(parts=[ToolCallPart("analyze_content", {})])
        if step[0] == 2:
            return ModelResponse(parts=[ToolCallPart("generate_components", {})])
        return ModelResponse(parts=[TextPart("done")])

    state = agent_module.DashboardState(markdown_content=doc)
    with agent_module.agent.override(model=FunctionModel(scripted)):
        asyncio.run(agent_module.agent.run("go", deps=StateDeps(state)))

    assert state.components, "pipeline must still produce components"
    # Every call carries one section (+ prompt template) or the truncated analysis excerpt, never the document.
    assert max(prompt_sizes) < 40000
    # The outer agent must never see the document echoed back through tool results.
    assert max(history_sizes) < 20000
