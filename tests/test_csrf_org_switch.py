"""Tests for CSRF token management in organization switch."""

import pytest
import time
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.common.csrf import CSRFTokenManager


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-session-token-abc123"}


class TestCSRFTokenGeneration:
    def test_generate_returns_token(self, client, auth_headers):
        response = client.get("/api/v2/org/csrf-token?target_org_id=org-1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "csrf_token" in data
        assert data["expires_in"] == 300

    def test_generate_requires_auth(self, client):
        response = client.get("/api/v2/org/csrf-token?target_org_id=org-1")
        assert response.status_code == 401

    def test_generate_requires_bearer_token(self, client):
        response = client.get(
            "/api/v2/org/csrf-token?target_org_id=org-1",
            headers={"Authorization": "Basic invalid"}
        )
        assert response.status_code == 401


class TestOrganizationSwitch:
    def test_switch_with_valid_token(self, client, auth_headers):
        # Get CSRF token
        token_resp = client.get("/api/v2/org/csrf-token?target_org_id=org-1", headers=auth_headers)
        csrf_token = token_resp.json()["csrf_token"]

        # Switch organization
        response = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers={**auth_headers, "X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        assert response.json()["active_organization"] == "org-1"

    def test_switch_without_csrf_token(self, client, auth_headers):
        response = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers=auth_headers
        )
        assert response.status_code == 403

    def test_switch_with_mismatched_org(self, client, auth_headers):
        # Get token for org-1
        token_resp = client.get("/api/v2/org/csrf-token?target_org_id=org-1", headers=auth_headers)
        csrf_token = token_resp.json()["csrf_token"]

        # Try to use it for org-2
        response = client.post(
            "/api/v2/org/switch?target_org_id=org-2",
            headers={**auth_headers, "X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 403

    def test_switch_with_different_session(self, client, auth_headers):
        # Get token for session A
        token_resp = client.get("/api/v2/org/csrf-token?target_org_id=org-1", headers=auth_headers)
        csrf_token = token_resp.json()["csrf_token"]

        # Try to use it with session B
        other_headers = {"Authorization": "Bearer different-session-token"}
        response = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers={**other_headers, "X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 403

    def test_token_cannot_be_reused(self, client, auth_headers):
        # Get CSRF token
        token_resp = client.get("/api/v2/org/csrf-token?target_org_id=org-1", headers=auth_headers)
        csrf_token = token_resp.json()["csrf_token"]

        # First use succeeds
        response1 = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers={**auth_headers, "X-CSRF-Token": csrf_token}
        )
        assert response1.status_code == 200

        # Second use fails (token already used)
        response2 = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers={**auth_headers, "X-CSRF-Token": csrf_token}
        )
        assert response2.status_code == 403

    def test_switch_with_invalid_token_format(self, client, auth_headers):
        response = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers={**auth_headers, "X-CSRF-Token": "not-a-valid-token!!!"}
        )
        assert response.status_code == 403

    def test_switch_with_expired_token(self, client, auth_headers, monkeypatch):
        # Get CSRF token
        token_resp = client.get("/api/v2/org/csrf-token?target_org_id=org-1", headers=auth_headers)
        csrf_token = token_resp.json()["csrf_token"]

        # Simulate time passing beyond expiry
        monkeypatch.setattr(time, "time", lambda: time.time() + 600)

        response = client.post(
            "/api/v2/org/switch?target_org_id=org-1",
            headers={**auth_headers, "X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 403


class TestCSRFTokenManagerUnit:
    def test_generate_and_validate(self):
        mgr = CSRFTokenManager(secret="test-secret")
        token = mgr.generate("session-1", "org-1")
        valid, error = mgr.validate(token, "session-1", "org-1")
        assert valid
        assert error == ""

    def test_validate_wrong_session(self):
        mgr = CSRFTokenManager(secret="test-secret")
        token = mgr.generate("session-1", "org-1")
        valid, error = mgr.validate(token, "session-2", "org-1")
        assert not valid
        assert "session" in error.lower()

    def test_validate_wrong_org(self):
        mgr = CSRFTokenManager(secret="test-secret")
        token = mgr.generate("session-1", "org-1")
        valid, error = mgr.validate(token, "session-1", "org-2")
        assert not valid
        assert "organization" in error.lower()

    def test_validate_reused_token(self):
        mgr = CSRFTokenManager(secret="test-secret")
        token = mgr.generate("session-1", "org-1")
        valid, _ = mgr.validate(token, "session-1", "org-1")
        assert valid
        valid, error = mgr.validate(token, "session-1", "org-1")
        assert not valid
        assert "already used" in error.lower()

    def test_validate_expired_token(self, monkeypatch):
        mgr = CSRFTokenManager(secret="test-secret")
        token = mgr.generate("session-1", "org-1", ttl=1)
        monkeypatch.setattr(time, "time", lambda: time.time() + 10)
        valid, error = mgr.validate(token, "session-1", "org-1")
        assert not valid
        assert "expired" in error.lower()

    def test_validate_empty_token(self):
        mgr = CSRFTokenManager(secret="test-secret")
        valid, error = mgr.validate("", "session-1", "org-1")
        assert not valid

    def test_validate_none_token(self):
        mgr = CSRFTokenManager(secret="test-secret")
        valid, error = mgr.validate(None, "session-1", "org-1")
        assert not valid
