"""Embedded console session management with strict JWT validation.

An embedded console session is a short-lived token minted for a dashboard
widget or iframe that needs to display an agent orchestration view.
Only tokens whose audience, issuer, tenant, and expiration claims pass
validation may create an embedded session.
"""

from typing import Optional, Dict, Any
import time
import uuid
import logging

from src.common.auth import (
    EmbeddedSessionConfig,
    TokenValidationError,
    decode_and_validate_token,
    set_config,
    get_config,
)

logger = logging.getLogger(__name__)


class EmbeddedSession:
    """A validated embedded console session."""

    def __init__(
        self,
        session_id: str,
        workspace: str,
        tenant: str,
        issuer: str,
        created_at: float,
        expires_at: float,
        max_age: int = 3600,
    ):
        self.session_id = session_id
        self.workspace = workspace
        self.tenant = tenant
        self.issuer = issuer
        self.created_at = created_at
        self.expires_at = expires_at
        self.max_age = max_age

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "tenant": self.tenant,
            "issuer": self.issuer,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class EmbeddedSessionManager:
    """Manages embedded console sessions with strict JWT boundary enforcement."""

    def __init__(self, session_ttl: int = 3600):
        self._sessions: Dict[str, EmbeddedSession] = {}
        self._session_ttl = session_ttl

    def create_session(
        self,
        token: str,
        secret: str,
        workspace: str,
        require_tenant: bool = True,
    ) -> EmbeddedSession:
        """Create an embedded session after validating the provided JWT.

        Validates audience, issuer, tenant, and expiration before issuing
        the embedded session. Raises TokenValidationError on failure.
        """
        # Decode and validate the incoming JWT
        payload = decode_and_validate_token(
            token,
            secret,
            require_audience=True,
            require_tenant=require_tenant,
        )

        tenant = payload.get("tenant") or workspace
        now = time.time()
        session_id = str(uuid.uuid4())
        expires_at = now + self._session_ttl

        session = EmbeddedSession(
            session_id=session_id,
            workspace=workspace,
            tenant=tenant,
            issuer=payload.get("iss", ""),
            created_at=now,
            expires_at=expires_at,
            max_age=self._session_ttl,
        )

        self._sessions[session_id] = session
        logger.info(
            f"Created embedded session {session_id} for tenant={tenant}, "
            f"workspace={workspace}",
        )
        return session

    def get_session(self, session_id: str) -> Optional[EmbeddedSession]:
        """Retrieve a session, removing it if expired."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired():
            self._sessions.pop(session_id, None)
            return None
        return session

    def revoke_session(self, session_id: str) -> bool:
        """Revoke an embedded session immediately."""
        if session_id in self._sessions:
            logger.info(f"Revoked embedded session {session_id}")
            del self._sessions[session_id]
            return True
        return False

    def configure(self, config: EmbeddedSessionConfig) -> None:
        """Apply the embedded session security configuration."""
        set_config(config)
        logger.info(
            f"EmbeddedSessionManager configured: "
            f"aud={config.expected_audience}, "
            f"iss={config.expected_issuer}, "
            f"tenant={config.expected_tenant}",
        )


# Module-level singleton
_session_manager: Optional[EmbeddedSessionManager] = None


def get_session_manager() -> EmbeddedSessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = EmbeddedSessionManager()
    return _session_manager
