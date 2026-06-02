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

    def test_whitespace_only_markdown_returns_400(self):
        response = self.client.post("/api/generate", json={"markdown_content": "   "})
        assert response.status_code == 400

    @patch("main.agent")
    def test_successful_generation_returns_dashboard_state(self, mock_agent):
        """Mock the agent to verify the endpoint wiring without real LLM calls."""

        async def fake_run(prompt, *, deps, **kwargs):
            state = deps.state
            state.document_title = "Test Document"
            state.document_type = "article"
            state.status = "complete"
            state.progress = 100
            state.components = [
                {
                    "type": "a2ui.TLDR",
                    "id": "comp-1",
                    "props": {"summary": "Test summary"},
                    "zone": "hero",
                }
            ]

        mock_agent.run = AsyncMock(side_effect=fake_run)

        response = self.client.post(
            "/api/generate",
            json={"markdown_content": "# Test\n\nSome content here."},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify DashboardState shape
        assert data["document_title"] == "Test Document"
        assert data["document_type"] == "article"
        assert data["status"] == "complete"
        assert data["progress"] == 100
        assert len(data["components"]) == 1
        assert data["components"][0]["type"] == "a2ui.TLDR"
        assert data["markdown_content"] == "# Test\n\nSome content here."
        assert data["error_message"] is None

    @patch("main.agent")
    def test_agent_error_returns_500(self, mock_agent):
        """Verify that agent exceptions become 500 responses."""
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM API timeout"))

        response = self.client.post(
            "/api/generate",
            json={"markdown_content": "# Test"},
        )

        assert response.status_code == 500
        assert "LLM API timeout" in response.json()["error"]

    @patch("main.agent")
    def test_response_contains_all_state_fields(self, mock_agent):
        """Verify the response includes every DashboardState field."""
        mock_agent.run = AsyncMock()

        response = self.client.post(
            "/api/generate",
            json={"markdown_content": "# Minimal"},
        )

        assert response.status_code == 200
        data = response.json()

        expected_fields = DashboardState.model_fields.keys()
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    @patch("main.agent")
    def test_prompt_includes_markdown_content(self, mock_agent):
        """Verify the agent receives the markdown in its prompt."""
        mock_agent.run = AsyncMock()
        markdown = "# Special Content\n\nVery unique text."

        self.client.post("/api/generate", json={"markdown_content": markdown})

        call_args = mock_agent.run.call_args
        prompt = call_args[0][0]
        assert "Special Content" in prompt
        assert "Very unique text." in prompt
