"""Tests for embedded console session management."""

import time
import pytest
import jwt as _jwt

from src.common.auth import EmbeddedSessionConfig, TokenValidationError
from src.common.sessions import EmbeddedSessionManager, get_session_manager


TEST_SECRET = "test-secret-key-for-sessions-32bytes!"
TEST_AUDIENCE = "embedded-console"
TEST_ISSUER = "https://auth.agent-orchestrator.io"
TEST_TENANT = "tenant-alpha"


def _make_token(
    audience: str = TEST_AUDIENCE,
    issuer: str = TEST_ISSUER,
    tenant: str = TEST_TENANT,
    exp_offset: int = 3600,
    secret: str = TEST_SECRET,
) -> str:
    payload = {
        "aud": audience,
        "iss": issuer,
        "tenant": tenant,
        "exp": int(time.time()) + exp_offset,
    }
    return _jwt.encode(payload, secret, algorithm="HS256")


class TestEmbeddedSessionManager:
    def setup_method(self):
        self.manager = EmbeddedSessionManager(session_ttl=3600)
        self.manager.configure(EmbeddedSessionConfig(
            expected_audience=TEST_AUDIENCE,
            expected_issuer=TEST_ISSUER,
            expected_tenant=TEST_TENANT,
        ))

    # --- session creation ---

    def test_create_session_with_valid_token(self):
        token = _make_token()
        session = self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        assert session.session_id is not None
        assert session.workspace == "ws-1"
        assert session.tenant == TEST_TENANT
        assert session.issuer == TEST_ISSUER
        assert session.expires_at > time.time()

    def test_create_session_wrong_audience_rejected(self):
        token = _make_token(audience="wrong-audience")
        with pytest.raises(TokenValidationError) as exc_info:
            self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        assert exc_info.value.claim == "aud"

    def test_create_session_wrong_issuer_rejected(self):
        token = _make_token(issuer="https://malicious.io")
        with pytest.raises(TokenValidationError) as exc_info:
            self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        assert exc_info.value.claim == "iss"

    def test_create_session_wrong_tenant_rejected(self):
        token = _make_token(tenant="tenant-beta")
        with pytest.raises(TokenValidationError) as exc_info:
            self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        assert exc_info.value.claim == "tenant"

    def test_create_session_expired_token_rejected(self):
        token = _make_token(exp_offset=-10)
        with pytest.raises(TokenValidationError) as exc_info:
            self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        assert exc_info.value.claim == "exp"

    def test_create_session_wrong_secret_rejected(self):
        token = _make_token()
        with pytest.raises(ValueError):
            self.manager.create_session(token, "wrong-secret", workspace="ws-1")

    # --- session retrieval ---

    def test_get_session_returns_session(self):
        token = _make_token()
        session = self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        retrieved = self.manager.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_session_returns_none(self):
        assert self.manager.get_session("does-not-exist") is None

    def test_get_expired_session_returns_none(self):
        # Create a session with an already-expired TTL manager
        manager = EmbeddedSessionManager(session_ttl=0)
        manager.configure(EmbeddedSessionConfig(
            expected_audience=TEST_AUDIENCE,
            expected_issuer=TEST_ISSUER,
            expected_tenant=TEST_TENANT,
        ))
        token = _make_token()
        session = manager.create_session(token, TEST_SECRET, workspace="ws-1")
        # Session should be immediately expired
        assert manager.get_session(session.session_id) is None

    # --- session revocation ---

    def test_revoke_session(self):
        token = _make_token()
        session = self.manager.create_session(token, TEST_SECRET, workspace="ws-1")
        assert self.manager.revoke_session(session.session_id) is True
        assert self.manager.get_session(session.session_id) is None

    def test_revoke_nonexistent_session_returns_false(self):
        assert self.manager.revoke_session("does-not-exist") is False

    # --- cross-tenant isolation ---

    def test_session_is_tenant_scoped(self):
        """Verify cross-tenant token rejection: beta token rejected when config expects alpha."""
        # Alpha token creates a session successfully
        token_alpha = _make_token(tenant="tenant-alpha")
        session_alpha = self.manager.create_session(token_alpha, TEST_SECRET, workspace="ws-alpha")
        assert session_alpha.tenant == "tenant-alpha"
        assert self.manager.get_session(session_alpha.session_id) is not None

        # Beta token is REJECTED because the system is configured for tenant-alpha
        token_beta = _make_token(tenant="tenant-beta")
        with pytest.raises(TokenValidationError) as exc_info:
            self.manager.create_session(token_beta, TEST_SECRET, workspace="ws-beta")
        assert exc_info.value.claim == "tenant"

    def test_tenant_isolation_without_strict_config(self):
        """Each tenant's token creates their own isolated session."""
        # Configure with no fixed tenant (any valid tenant is accepted)
        self.manager.configure(EmbeddedSessionConfig(
            expected_audience=TEST_AUDIENCE,
            expected_issuer=TEST_ISSUER,
            expected_tenant=None,  # Any tenant is valid
        ))
        token_alpha = _make_token(tenant="tenant-alpha")
        token_beta = _make_token(tenant="tenant-beta")
        session_alpha = self.manager.create_session(token_alpha, TEST_SECRET, workspace="ws-alpha")
        session_beta = self.manager.create_session(token_beta, TEST_SECRET, workspace="ws-beta")
        assert session_alpha.tenant == "tenant-alpha"
        assert session_beta.tenant == "tenant-beta"
        assert session_beta.session_id != session_alpha.session_id

    # --- configuration ---

    def test_configure_updates_constraints(self):
        self.manager.configure(EmbeddedSessionConfig(
            expected_audience="new-console",
            expected_issuer="https://new-issuer.io",
            expected_tenant="new-tenant",
        ))
        # New config should reject old token
        token = _make_token()
        with pytest.raises(TokenValidationError):
            self.manager.create_session(token, TEST_SECRET, workspace="ws-1")


class TestGetSessionManager:
    def test_singleton_returns_same_instance(self):
        mgr1 = get_session_manager()
        mgr2 = get_session_manager()
        assert mgr1 is mgr2
