"""JWT validation for embedded session exchange.

Validates audience, issuer, tenant, and expiration claims before
issuing or accepting an embedded console session token.
"""

import time
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class TokenValidationError(Exception):
    """Raised when a JWT fails validation."""
    def __init__(self, reason: str, claim: Optional[str] = None):
        self.reason = reason
        self.claim = claim
        super().__init__(reason)


class EmbeddedSessionConfig:
    """Configuration constraints for embedded session issuance."""

    def __init__(
        self,
        expected_audience: str,
        expected_issuer: str,
        expected_tenant: Optional[str] = None,
        allow_wildcard_audience: bool = False,
    ):
        self.expected_audience = expected_audience
        self.expected_issuer = expected_issuer
        self.expected_tenant = expected_tenant
        self.allow_wildcard_audience = allow_wildcard_audience

    def get(self) -> Dict[str, Any]:
        return {
            "audience": self.expected_audience,
            "issuer": self.expected_issuer,
            "tenant": self.expected_tenant,
            "allow_wildcard_audience": self.allow_wildcard_audience,
        }


# Default config — override via set_config() at startup.
_config: Optional[EmbeddedSessionConfig] = None


def set_config(config: EmbeddedSessionConfig) -> None:
    global _config
    _config = config


def get_config() -> EmbeddedSessionConfig:
    global _config
    if _config is None:
        # Permissive default — do not use in production without config.
        _config = EmbeddedSessionConfig(
            expected_audience="embedded-console",
            expected_issuer="https://auth.agent-orchestrator.io",
            expected_tenant=None,
            allow_wildcard_audience=False,
        )
    return _config


def validate_token_claims(
    payload: Dict[str, Any],
    require_audience: bool = True,
    require_tenant: bool = True,
) -> None:
    """Validate audience, issuer, tenant, and expiration of a decoded JWT payload.

    Raises TokenValidationError on any validation failure.
    """
    cfg = get_config()

    # Expiration check
    exp = payload.get("exp")
    if exp is not None and exp < time.time():
        raise TokenValidationError("Token has expired", "exp")

    # Audience check
    aud = payload.get("aud")
    if require_audience:
        if aud is None:
            raise TokenValidationError("Token is missing 'aud' claim", "aud")
        if isinstance(aud, list):
            aud_list = aud
        else:
            aud_list = [aud]

        if cfg.allow_wildcard_audience and "*" in aud_list:
            pass  # Wildcard is explicitly allowed
        elif cfg.expected_audience not in aud_list:
            raise TokenValidationError(
                f"Token audience '{aud}' does not match expected '{cfg.expected_audience}'",
                "aud",
            )

    # Issuer check
    iss = payload.get("iss")
    if iss is None:
        raise TokenValidationError("Token is missing 'iss' claim", "iss")
    if iss != cfg.expected_issuer:
        raise TokenValidationError(
            f"Token issuer '{iss}' does not match expected '{cfg.expected_issuer}'",
            "iss",
        )

    # Tenant check
    if require_tenant:
        tenant = payload.get("tenant")
        if tenant is None:
            raise TokenValidationError("Token is missing 'tenant' claim", "tenant")
        if cfg.expected_tenant is not None and tenant != cfg.expected_tenant:
            raise TokenValidationError(
                f"Token tenant '{tenant}' does not match expected '{cfg.expected_tenant}'",
                "tenant",
            )


def decode_and_validate_token(
    token: str,
    secret: str,
    require_audience: bool = True,
    require_tenant: bool = True,
) -> Dict[str, Any]:
    """Decode a JWT and validate all required claims.

    Uses HS256 for symmetric verification. audience/issuer/tenant/expiration
    are validated against the configured EmbeddedSessionConfig.

    Returns the decoded payload on success.

    Raises TokenValidationError on any failure.
    Raises ValueError on malformed tokens.
    """
    import jwt

    cfg = get_config()
    decode_options = {"verify_exp": True, "verify_iss": True}
    if require_audience:
        decode_options["verify_aud"] = True

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=cfg.expected_audience if require_audience else None,
            options=decode_options,
        )
    except jwt.ExpiredSignatureError:
        raise TokenValidationError("Token has expired", "exp")
    except jwt.InvalidAudienceError:
        raise TokenValidationError("Token audience is invalid", "aud")
    except jwt.InvalidIssuerError:
        raise TokenValidationError("Token issuer is invalid", "iss")
    except jwt.DecodeError as e:
        raise ValueError(f"Malformed token: {e}")

    validate_token_claims(payload, require_audience=require_audience, require_tenant=require_tenant)
    return payload
