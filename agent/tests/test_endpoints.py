"""
Tests for the FastAPI/Starlette HTTP endpoints.

Covers the sync /api/generate endpoint and utility endpoints (/health, /info, GET /).
The AG-UI streaming endpoint (POST /) is not tested here as it requires
a full CopilotKit AG-UI client handshake.
"""

import pytest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from main import app
from agent import DashboardState


@pytest.fixture(autouse=True)
def _disable_api_key_auth():
    """Disable API_KEY auth for all endpoint tests."""
    with patch("main.API_KEY", ""):
        yield


class TestHealthEndpoint:
    """Tests for GET /health."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_health_returns_200(self):
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "agent_ready" in data

    def test_health_reports_agent_ready_based_on_api_key(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            response = self.client.get("/health")
            assert response.json()["agent_ready"] is True

        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get("/health")
            assert response.json()["agent_ready"] is False


class TestInfoEndpoint:
    """Tests for GET /info."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_info_returns_agent_metadata(self):
        response = self.client.get("/info")
        assert response.status_code == 200
        data = response.json()
        assert data["protocol"] == "ag-ui"
        assert "name" in data
        assert "version" in data


class TestRootGetEndpoint:
    """Tests for GET /."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_root_get_returns_endpoint_listing(self):
        response = self.client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "endpoints" in data
        assert "run_agent" in data["endpoints"]
        assert "generate_sync" in data["endpoints"]


class TestSyncGenerateEndpoint:
    """Tests for POST /api/generate."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_missing_body_returns_400(self):
        response = self.client.post(
            "/api/generate",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["error"]

    def test_empty_markdown_returns_400(self):
        response = self.client.post("/api/generate", json={"markdown_content": ""})
        assert response.status_code == 400
        assert "required" in response.json()["error"]

    def test_missing_markdown_field_returns_400(self):
        response = self.client.post("/api/generate", json={"other_field": "value"})
        assert response.status_code == 400
        assert "required" in response.json()["error"]

    def test_oversize_markdown_returns_422(self):
        from section_orchestrator import MAX_INPUT_CHARS
        response = self.client.post("/api/generate", json={"markdown_content": "x" * (MAX_INPUT_CHARS + 1)})
        assert response.status_code == 422
        assert "limit" in response.json()["error"]

    def test_whitespace_only_markdown_returns_400(self):
        response = self.client.post("/api/generate", json={"markdown_content": "   "})
        assert response.status_code == 400

    def test_short_input_runs_classifier_then_components(self):
        """Short doc: classifier runs once, its analysis is reused by the component step; no agent involved."""
        from a2ui_generator import A2UIComponent

        calls = []

        async def fake_classifier(md):
            calls.append("classify")
            return {"document_type": "tutorial"}

        async def fake_orchestrate(md, content_analysis=None, meta=None):
            calls.append(("components", content_analysis))
            meta["layout_type"] = "instructional"
            yield A2UIComponent(type="a2ui.TLDR", id="comp-1", props={"summary": "s"}, zone="hero")

        with patch("llm_orchestrator.analyze_content_with_llm", side_effect=fake_classifier), \
             patch("llm_orchestrator.orchestrate_dashboard_with_llm", side_effect=fake_orchestrate), \
             patch("main.agent") as mock_agent:
            response = self.client.post(
                "/api/generate", json={"markdown_content": "# Test Doc\n\nSome content here."}
            )
            mock_agent.run.assert_not_called()

        assert response.status_code == 200
        data = response.json()
        assert calls == ["classify", ("components", {"document_type": "tutorial"})]
        assert data["document_title"] == "Test Doc"
        assert data["document_type"] == "tutorial"
        assert data["layout_type"] == "instructional"
        assert data["status"] == "complete"
        assert data["progress"] == 100
        assert data["current_step"] == "Dashboard complete!"
        assert data["components"] == [
            {"type": "a2ui.TLDR", "id": "comp-1", "props": {"summary": "s"}, "zone": "hero"}
        ]
        assert data["content_analysis"]["document_type"] == "tutorial"
        assert data["markdown_content"] == "# Test Doc\n\nSome content here."
        assert data["error_message"] is None
        assert [e["message"] for e in data["activity_log"]] == [
            "Starting content analysis",
            "Analysis complete: tutorial",
            "Generated 1 components",
        ]

    def test_section_path_input_skips_classifier(self):
        """Long structured report: the classifier LLM call is never made."""
        from a2ui_generator import A2UIComponent

        async def fake_orchestrate(md, content_analysis=None, meta=None):
            assert content_analysis is None
            meta["layout_type"] = "report_sections"
            yield A2UIComponent(type="a2ui.TLDR", id="comp-1", props={"summary": "s"})

        with patch("section_orchestrator.should_use_section_path", return_value=True), \
             patch("llm_orchestrator.analyze_content_with_llm", new_callable=AsyncMock) as classifier, \
             patch("llm_orchestrator.orchestrate_dashboard_with_llm", side_effect=fake_orchestrate):
            response = self.client.post(
                "/api/generate", json={"markdown_content": "# Long Report\n\n## A\n\ntext"}
            )

        assert response.status_code == 200
        classifier.assert_not_called()
        data = response.json()
        assert data["document_title"] == "Long Report"
        assert data["document_type"] == "report"
        assert data["layout_type"] == "report_sections"
        assert data["status"] == "complete"
        assert len(data["components"]) == 1

    def test_pipeline_error_returns_500(self):
        with patch("llm_orchestrator.analyze_content_with_llm", side_effect=RuntimeError("LLM API timeout")):
            response = self.client.post("/api/generate", json={"markdown_content": "# Test"})
        assert response.status_code == 500
        assert "LLM API timeout" in response.json()["error"]

    def test_response_contains_all_state_fields(self):
        """Verify the response includes every serialized DashboardState field."""

        async def fake_orchestrate(md, content_analysis=None, meta=None):
            return
            yield

        with patch("llm_orchestrator.analyze_content_with_llm", new_callable=AsyncMock, return_value={}), \
             patch("llm_orchestrator.orchestrate_dashboard_with_llm", side_effect=fake_orchestrate):
            response = self.client.post("/api/generate", json={"markdown_content": "# Minimal"})

        assert response.status_code == 200
        data = response.json()
        for name, field in DashboardState.model_fields.items():
            if field.exclude:
                assert name not in data
            else:
                assert name in data, f"Missing field: {name}"


class TestContextOverflowError:
    def test_context_limit_error_is_422_with_clear_message(self):
        err = RuntimeError("status_code: 400, body: {'message': \"This endpoint's maximum context length is 200000 tokens.\"}")
        with patch("llm_orchestrator.analyze_content_with_llm", side_effect=err):
            response = TestClient(app).post("/api/generate", json={"markdown_content": "# T\n\ntext"})
        assert response.status_code == 422
        assert "too large" in response.json()["error"]


class TestDeployedAuthGuard:
    """require_api_key_when_deployed refuses to start without API_KEY on Railway/Cloud Run/Modal."""

    @pytest.mark.parametrize(
        "marker", ["RAILWAY_ENVIRONMENT", "K_SERVICE", "MODAL_IS_REMOTE", "MODAL_TASK_ID"]
    )
    def test_raises_without_key(self, marker):
        from main import require_api_key_when_deployed

        with patch.dict("os.environ", {marker: "1"}, clear=True):
            with pytest.raises(RuntimeError, match=marker):
                require_api_key_when_deployed("")

    def test_ok_with_key_or_local(self):
        from main import require_api_key_when_deployed

        with patch.dict("os.environ", {"MODAL_IS_REMOTE": "1"}, clear=True):
            require_api_key_when_deployed("secret")
        with patch.dict("os.environ", {}, clear=True):
            require_api_key_when_deployed("")
