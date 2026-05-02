"""v0.4.4 Phase E runtime API smoke tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from session.schemas import RuntimeResponse


def test_runtime_api_routes_are_registered():
    from main import app

    paths = {route.path for route in app.routes}

    assert "/api/sessions/{session_id}/runtime" in paths
    assert "/api/sessions/{session_id}/retry" in paths
    assert "/api/sessions/{session_id}/prompt" in paths
    assert "/api/sessions/{session_id}/permissions/pending" in paths


def test_runtime_response_exposes_recovery_fields():
    response = RuntimeResponse(
        session_id=uuid.uuid4(),
        status="idle",
        phase="error",
        last_message_seq=8,
        pending_permissions_count=0,
        resumable=True,
        retry_kind="model_continuation",
        provider_error_category="provider_timeout",
        checkpoint_state_kind="next_model_after_tool_result",
        last_error="ReadTimeout",
        updated_at=datetime.now(timezone.utc),
    )
    data = response.model_dump(mode="json")

    assert data["resumable"] is True
    assert data["retry_kind"] == "model_continuation"
    assert data["provider_error_category"] == "provider_timeout"
    assert data["checkpoint_state_kind"] == "next_model_after_tool_result"


def test_runtime_health_endpoint_smoke():
    from main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
