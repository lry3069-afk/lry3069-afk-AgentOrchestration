import pytest
import json
import base64
import hmac
import hashlib
import time
from src.api.jwt_auth import JWTValidator


def _make_jwt(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(
        json.dumps(header).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    signing_input = f"{header_b64}.{payload_b64}"
    sig = hmac.new(
        secret.encode(), signing_input.encode(), hashlib.sha256
    ).hexdigest()
    return f"{header_b64}.{payload_b64}.{sig}"


class TestJWTValidator:
    def setup_method(self):
        self.validator = JWTValidator(
            expected_audience="agent-orchestrator-console",
            expected_issuer="auth.agent-orchestrator.io",
            token_secret="test-jwt-secret-key-32byte!",
            max_age_seconds=300,
        )
        self.valid_payload = {
            "aud": "agent-orchestrator-console",
            "iss": "auth.agent-orchestrator.io",
            "tenant": "workspace-abc",
            "sub": "user-123",
            "exp": int(time.time()) + 120,
            "iat": int(time.time()),
        }

    def test_valid_token_passes_all_checks(self):
        token = _make_jwt(self.valid_payload, self.validator.token_secret)
        is_valid, error, payload = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is True
        assert error == ""
        assert payload["aud"] == "agent-orchestrator-console"

    def test_reject_wrong_audience(self):
        payload = {**self.valid_payload, "aud": "other-service"}
        token = _make_jwt(payload, self.validator.token_secret)
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is False
        assert "audience mismatch" in error.lower()

    def test_reject_wrong_issuer(self):
        payload = {**self.valid_payload, "iss": "evil.issuer.com"}
        token = _make_jwt(payload, self.validator.token_secret)
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is False
        assert "issuer mismatch" in error.lower()

    def test_tenant_mismatch_fails_before_session_creation(self):
        token = _make_jwt(self.valid_payload, self.validator.token_secret)
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="wrong-tenant"
        )
        assert is_valid is False
        assert "tenant mismatch" in error.lower()

    def test_reject_expired_token(self):
        expired_payload = {
            **self.valid_payload,
            "exp": int(time.time()) - 60,
        }
        token = _make_jwt(expired_payload, self.validator.token_secret)
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is False
        assert "expired" in error

    def test_reject_not_yet_valid_token(self):
        future_payload = {
            **self.valid_payload,
            "nbf": int(time.time()) + 3600,
            "exp": int(time.time()) + 7200,
        }
        token = _make_jwt(future_payload, self.validator.token_secret)
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is False
        assert "not yet valid" in error

    def test_reject_invalid_signature(self):
        token = _make_jwt(self.valid_payload, "wrong-secret-key")
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is False
        assert "signature" in error

    def test_multi_issuer_token_rejected(self):
        # Token minted by a different issuer should fail
        other_validator = JWTValidator(
            expected_audience="agent-orchestrator-console",
            expected_issuer="auth.agent-orchestrator.io",
            token_secret="test-jwt-secret-key-32byte!",
        )
        payload = {**self.valid_payload, "iss": "another-issuer.io"}
        token = _make_jwt(payload, other_validator.token_secret)
        is_valid, error, _ = self.validator.validate_token(
            token, expected_tenant="workspace-abc"
        )
        assert is_valid is False
        assert "issuer mismatch" in error.lower()
