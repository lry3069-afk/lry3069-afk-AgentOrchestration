"""Tests for JWT audience, issuer, tenant, and expiration validation."""

import time
import pytest
import jwt as _jwt

from src.common.auth import (
    EmbeddedSessionConfig,
    TokenValidationError,
    validate_token_claims,
    decode_and_validate_token,
    set_config,
)

TEST_SECRET = "test-secret-key-for-jwt-validation-32b"
TEST_AUDIENCE = "embedded-console"
TEST_ISSUER = "https://auth.agent-orchestrator.io"


def _make_token(payload: dict, secret: str = TEST_SECRET, **overrides) -> str:
    """Create a signed JWT with optional expiry."""
    exp = int(time.time()) + 3600
    full = {
        "aud": TEST_AUDIENCE,
        "iss": TEST_ISSUER,
        "tenant": "tenant-alpha",
        "exp": exp,
        **payload,
    }
    full.update(overrides)
    return _jwt.encode(full, secret, algorithm="HS256")


class TestValidateTokenClaims:
    def setup_method(self):
        set_config(EmbeddedSessionConfig(
            expected_audience=TEST_AUDIENCE,
            expected_issuer=TEST_ISSUER,
            expected_tenant="tenant-alpha",
        ))

    # --- audience validation ---

    def test_valid_audience(self):
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        validate_token_claims(payload, require_audience=True, require_tenant=True)  # no raise

    def test_wrong_audience_rejected(self):
        payload = {"aud": "wrong-audience", "iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True)
        assert exc_info.value.claim == "aud"

    def test_missing_audience_rejected(self):
        payload = {"iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True)
        assert exc_info.value.claim == "aud"

    def test_wildcard_audience_allowed_when_configured(self):
        set_config(EmbeddedSessionConfig(
            expected_audience=TEST_AUDIENCE,
            expected_issuer=TEST_ISSUER,
            allow_wildcard_audience=True,
        ))
        payload = {"aud": ["*"], "iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        validate_token_claims(payload, require_audience=True, require_tenant=False)  # no raise

    # --- issuer validation ---

    def test_valid_issuer(self):
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        validate_token_claims(payload, require_audience=True, require_tenant=True)  # no raise

    def test_wrong_issuer_rejected(self):
        payload = {"aud": TEST_AUDIENCE, "iss": "https://malicious-issuer.io", "tenant": "tenant-alpha", "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True, require_tenant=True)
        assert exc_info.value.claim == "iss"

    def test_missing_issuer_rejected(self):
        payload = {"aud": TEST_AUDIENCE, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True, require_tenant=True)
        assert exc_info.value.claim == "iss"

    # --- tenant validation ---

    def test_valid_tenant(self):
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() + 3600}
        validate_token_claims(payload, require_audience=True, require_tenant=True)  # no raise

    def test_wrong_tenant_rejected(self):
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "tenant": "tenant-beta", "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True, require_tenant=True)
        assert exc_info.value.claim == "tenant"

    def test_missing_tenant_rejected(self):
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True, require_tenant=True)
        assert exc_info.value.claim == "tenant"

    # --- expiration validation ---

    def test_expired_token_rejected(self):
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "tenant": "tenant-alpha", "exp": time.time() - 10}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True, require_tenant=True)
        assert exc_info.value.claim == "exp"

    def test_missing_exp_accepted(self):
        """Tokens without explicit exp are treated as non-expiring (common for refresh tokens)."""
        payload = {"aud": TEST_AUDIENCE, "iss": TEST_ISSUER, "tenant": "tenant-alpha"}
        validate_token_claims(payload, require_audience=True, require_tenant=True)  # no raise

    # --- multi-issuer scenario ---

    def test_multi_issuer_wrong_one_rejected(self):
        payload = {"aud": TEST_AUDIENCE, "iss": "https://legacy-auth.io", "tenant": "tenant-alpha", "exp": time.time() + 3600}
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token_claims(payload, require_audience=True, require_tenant=True)
        assert exc_info.value.claim == "iss"


class TestDecodeAndValidateToken:
    def setup_method(self):
        set_config(EmbeddedSessionConfig(
            expected_audience=TEST_AUDIENCE,
            expected_issuer=TEST_ISSUER,
            expected_tenant="tenant-alpha",
        ))

    def test_valid_token_decoded(self):
        token = _make_token({"foo": "bar"})
        payload = decode_and_validate_token(token, TEST_SECRET)
        assert payload["foo"] == "bar"

    def test_wrong_secret_rejected(self):
        token = _make_token({})
        with pytest.raises(ValueError):
            decode_and_validate_token(token, "wrong-secret")

    def test_malformed_token_raises(self):
        with pytest.raises(ValueError):
            decode_and_validate_token("not.a.valid.jwt", TEST_SECRET)

    def test_expired_token_via_decode(self):
        token = _make_token({}, exp=int(time.time()) - 100)
        with pytest.raises(TokenValidationError) as exc_info:
            decode_and_validate_token(token, TEST_SECRET)
        assert exc_info.value.claim == "exp"

    def test_wrong_audience_via_decode(self):
        token = _make_token({}, aud="wrong")
        with pytest.raises(TokenValidationError) as exc_info:
            decode_and_validate_token(token, TEST_SECRET)
        assert exc_info.value.claim == "aud"
