"""CSRF token management for organization switch actions."""

import hashlib
import hmac
import os
import time
import secrets
from typing import Optional, Tuple

DEFAULT_EXPIRY = 300  # 5 minutes


class CSRFTokenManager:
    """Issues and validates target-bound CSRF tokens for organization switches."""

    def __init__(self, secret: Optional[str] = None):
        self._secret = secret.encode("utf-8") if secret else os.urandom(32)
        self._used_tokens: set = set()

    def generate(self, session_id: str, target_org_id: str, ttl: int = DEFAULT_EXPIRY) -> str:
        """Generate a CSRF token bound to the session and target organization."""
        timestamp = int(time.time())
        payload = f"{session_id}:{target_org_id}:{timestamp}"
        sig = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        token_data = f"{payload}:{sig}"
        return base64.urlsafe_b64encode(token_data.encode("utf-8")).decode("utf-8").rstrip("=")

    def validate(self, token: str, session_id: str, target_org_id: str, ttl: int = DEFAULT_EXPIRY) -> Tuple[bool, str]:
        """Validate a CSRF token.

        Returns (is_valid, error_message).
        """
        if not token:
            return False, "Missing CSRF token"

        # Prevent token reuse
        if token in self._used_tokens:
            return False, "CSRF token already used"

        try:
            # Normalize base64 padding
            padded = token + "=" * (4 - len(token) % 4) if len(token) % 4 else token
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        except Exception:
            return False, "Invalid CSRF token format"

        parts = decoded.rsplit(":", 1)
        if len(parts) != 2:
            return False, "Invalid CSRF token structure"

        payload, provided_sig = parts
        expected_sig = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, provided_sig):
            return False, "CSRF token signature mismatch"

        payload_parts = payload.split(":")
        if len(payload_parts) != 3:
            return False, "Invalid CSRF token payload"

        token_session_id, token_org_id, timestamp_str = payload_parts

        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return False, "Invalid CSRF token timestamp"

        if int(time.time()) - timestamp > ttl:
            return False, "CSRF token expired"

        if token_session_id != session_id:
            return False, "CSRF token bound to different session"

        if token_org_id != target_org_id:
            return False, "CSRF token bound to different organization"

        # Mark token as used (single-use)
        self._used_tokens.add(token)

        # Cleanup old tokens periodically
        if len(self._used_tokens) > 10000:
            self._used_tokens.clear()

        return True, ""


# Global instance
csrf_manager = CSRFTokenManager()
