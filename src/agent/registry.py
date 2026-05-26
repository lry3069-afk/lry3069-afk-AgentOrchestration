"""Agent Registry — Manages agent lifecycle and metadata with workspace isolation."""

import json
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class Role(Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class AuthContext:
    """Captures the authenticated principal's workspace and role."""

    def __init__(
        self,
        workspace_id: str,
        role: Role = Role.VIEWER,
        principal_id: Optional[str] = None,
        is_anonymous: bool = False,
        credential_id: Optional[str] = None,
    ):
        self.workspace_id = workspace_id
        self.role = role
        self.principal_id = principal_id
        self.is_anonymous = is_anonymous
        self.credential_id = credential_id
        self._created_at = time.time()
        self._last_checked = self._created_at

    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    def can_mutate(self) -> bool:
        return self.role in (Role.ADMIN, Role.EDITOR)

    def is_stale(self, max_age_seconds: float = 300.0) -> bool:
        """Return True if credential has not been refreshed recently."""
        return (time.time() - self._last_checked) > max_age_seconds

    def refresh(self) -> None:
        """Mark credential as freshly validated."""
        self._last_checked = time.time()


# Module-level credential cache for revocation support
_revoked_credentials: Dict[str, float] = {}  # credential_id -> revocation_timestamp


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}

    # ── Workspace-scoping helpers ────────────────────────────────────────────

    def _check_membership(
        self, auth: AuthContext, agent_id: str, require_mutate: bool = False
    ) -> None:
        """Raise AuthorizationError if auth context is invalid or lacks access."""
        from src.common.errors import AuthorizationError

        if auth.is_anonymous:
            raise AuthorizationError("Anonymous principals cannot access agents")

        if auth.is_stale():
            raise AuthorizationError("Stale credentials — re-authenticate and retry")

        credential_id = getattr(auth, "credential_id", None)
        if credential_id and credential_id in _revoked_credentials:
            raise AuthorizationError("Credential has been revoked")

        if require_mutate and not auth.can_mutate():
            raise AuthorizationError(
                f"Role '{auth.role.value}' cannot mutate agents (admin/editor required)"
            )

        # Workspace check only applies when an actual agent_id is specified
        if agent_id and agent_id not in ("<registration>",):
            agent = self._agents.get(agent_id)
            if agent is not None:
                agent_workspace = agent.get("workspace_id")
                if agent_workspace and agent_workspace != auth.workspace_id:
                    raise AuthorizationError(
                        f"Agent {agent_id} is in workspace {agent_workspace}, "
                        f"but principal belongs to {auth.workspace_id}"
                    )

    # ── Registration ─────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
        workspace_id: Optional[str] = None,
        auth: Optional[AuthContext] = None,
    ) -> str:
        if auth is not None:
            self._check_membership(auth, agent_id="<registration>", require_mutate=True)

        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        agent = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "workspace_id": workspace_id or "default",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        self._agents[agent_id] = agent
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        return agent_id

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, agent_id: str, auth: Optional[AuthContext] = None) -> Optional[Dict[str, Any]]:
        if auth is not None:
            self._check_membership(auth, agent_id)
        return self._agents.get(agent_id)

    def list(
        self,
        status: Optional[AgentStatus] = None,
        group: Optional[str] = None,
        workspace_id: Optional[str] = None,
        auth: Optional[AuthContext] = None,
    ) -> List[Dict[str, Any]]:
        if auth is not None:
            effective_workspace = auth.workspace_id
        else:
            effective_workspace = workspace_id

        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        if effective_workspace:
            agents = [a for a in agents if a.get("workspace_id") == effective_workspace]
        return list(agents)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def update_status(
        self, agent_id: str, status: AgentStatus, auth: Optional[AuthContext] = None
    ) -> bool:
        if auth is not None:
            self._check_membership(auth, agent_id, require_mutate=True)
        if agent_id not in self._agents:
            return False
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        return True

    def delete(self, agent_id: str, auth: Optional[AuthContext] = None) -> bool:
        if auth is not None:
            self._check_membership(auth, agent_id, require_mutate=True)
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        return True

    # ── Credential management ──────────────────────────────────────────────────

    @staticmethod
    def revoke_credential(credential_id: str) -> None:
        """Revoke a credential immediately — any auth context using it will be rejected."""
        _revoked_credentials[credential_id] = time.time()

    @staticmethod
    def is_credential_revoked(credential_id: str) -> bool:
        return credential_id in _revoked_credentials

    def count(self) -> int:
        return len(self._agents)
