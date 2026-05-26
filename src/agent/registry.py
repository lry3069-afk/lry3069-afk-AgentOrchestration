"""Agent Registry — Manages agent lifecycle and metadata."""

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
    DISABLED = "disabled"


class DuplicateExternalIdError(ValueError):
    """Raised when a service account external ID is already in use."""

    def __init__(self, external_id: str, existing_agent_id: str):
        super().__init__(
            f"External ID '{external_id}' already in use by agent {existing_agent_id}"
        )
        self.external_id = external_id
        self.existing_agent_id = existing_agent_id


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        # Maps external_id -> agent_id for active (non-disabled, non-terminated) accounts
        self._external_id_index: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # External ID uniqueness helpers  (Issue #4844)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_active(status: str) -> bool:
        """An account is 'active' unless explicitly disabled or terminated."""
        return status not in (
            AgentStatus.DISABLED.value,
            AgentStatus.TERMINATED.value,
        )

    def _check_external_id_uniqueness(
        self,
        external_id: Optional[str],
        organization: str,
        exclude_agent_id: Optional[str] = None,
    ) -> None:
        """Raise DuplicateExternalIdError if external_id is already in active use."""
        if not external_id:
            return

        key = f"{organization}::{external_id}"
        existing = self._external_id_index.get(key)
        if existing is None:
            return
        if existing == exclude_agent_id:
            return
        # Verify the existing account is still active
        if existing in self._agents:
            if self._is_active(self._agents[existing]["status"]):
                raise DuplicateExternalIdError(external_id, existing)
        # Stale index entry — clean up
        self._external_id_index.pop(key, None)

    def _index_external_id(
        self,
        agent_id: str,
        external_id: Optional[str],
        organization: str,
    ) -> None:
        """Register the external_id -> agent_id mapping for active accounts."""
        if not external_id:
            return
        key = f"{organization}::{external_id}"
        self._external_id_index[key] = agent_id

    def _unindex_external_id(self, agent_id: str) -> None:
        """Remove all external_id mappings for a given agent_id."""
        to_remove = [
            k for k, v in self._external_id_index.items() if v == agent_id
        ]
        for k in to_remove:
            self._external_id_index.pop(k, None)

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
        external_id: Optional[str] = None,
        organization: str = "default",
    ) -> str:
        """Register a new agent (or service account).

        For service accounts (`agent_type` starts with "service_account"),
        `external_id` must be unique within the organization across all
        active accounts.  Duplicate active external IDs are rejected.
        """
        # --- External ID uniqueness check ---
        self._check_external_id_uniqueness(external_id, organization)

        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config or {},
            "external_id": external_id,
            "organization": organization,
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)

        # Index external_id for active accounts
        self._index_external_id(agent_id, external_id, organization)

        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def list(
        self,
        status: Optional[AgentStatus] = None,
        group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        return list(agents)

    def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        if agent_id not in self._agents:
            return False
        old_status = self._agents[agent_id]["status"]
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()

        # Maintain external_id index when transitioning between active/inactive
        ext_id = self._agents[agent_id].get("external_id")
        org = self._agents[agent_id].get("organization", "default")
        was_active = self._is_active(old_status)
        is_active = self._is_active(status.value)

        if was_active and not is_active:
            self._unindex_external_id(agent_id)
        elif not was_active and is_active:
            # Re-index — but check for conflicts first
            self._check_external_id_uniqueness(ext_id, org, exclude_agent_id=agent_id)
            self._index_external_id(agent_id, ext_id, org)

        return True

    # ------------------------------------------------------------------
    # Service account specific operations  (Issue #4844)
    # ------------------------------------------------------------------

    def update_service_account(
        self,
        agent_id: str,
        name: Optional[str] = None,
        external_id: Optional[str] = None,
        config: Optional[Dict] = None,
    ) -> bool:
        """Update a service account's metadata.

        Changing external_id triggers uniqueness validation — the new
        value must not collide with another active account in the same
        organization.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return False

        org = agent.get("organization", "default")
        old_ext_id = agent.get("external_id")

        if external_id is not None and external_id != old_ext_id:
            self._check_external_id_uniqueness(external_id, org, exclude_agent_id=agent_id)
            agent["external_id"] = external_id
            # Update index
            if self._is_active(agent["status"]):
                self._unindex_external_id(agent_id)
                self._index_external_id(agent_id, external_id, org)

        if name is not None:
            agent["name"] = name
        if config is not None:
            agent["config"] = config

        agent["updated_at"] = time.time()
        return True

    def disable(self, agent_id: str) -> bool:
        """Disable a service account — releases the external_id for reuse."""
        return self.update_status(agent_id, AgentStatus.DISABLED)

    def restore(self, agent_id: str) -> bool:
        """Restore a disabled service account.

        The external_id is re-validated against active accounts.  If
        another account has claimed the external_id in the meantime, the
        restore is rejected.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return False
        if agent["status"] != AgentStatus.DISABLED.value:
            return False

        ext_id = agent.get("external_id")
        org = agent.get("organization", "default")
        self._check_external_id_uniqueness(ext_id, org, exclude_agent_id=agent_id)
        return self.update_status(agent_id, AgentStatus.PENDING)

    def find_by_external_id(
        self,
        external_id: str,
        organization: str = "default",
    ) -> Optional[Dict[str, Any]]:
        """Look up an active service account by its external ID."""
        key = f"{organization}::{external_id}"
        agent_id = self._external_id_index.get(key)
        if agent_id is None:
            return None
        return self._agents.get(agent_id)

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        # Clean up external_id index
        self._unindex_external_id(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)

# 2026-05-26T11:00:00 update — external ID uniqueness for service accounts (#4844)
