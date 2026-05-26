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


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        self._external_id_index: Dict[str, str] = {}  # external_id -> agent_id
        self._org_external_index: Dict[str, Dict[str, str]] = {}  # org_id -> {external_id -> agent_id}

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
        external_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> str:
        agent_id = str(uuid.uuid4())
        timestamp = time.time()

        # Validate external ID uniqueness if provided
        if external_id:
            if organization_id:
                # Check within organization
                if organization_id in self._org_external_index:
                    if external_id in self._org_external_index[organization_id]:
                        raise ValueError(
                            f"External ID '{external_id}' already exists in organization '{organization_id}'"
                        )
            else:
                # Global uniqueness check
                if external_id in self._external_id_index:
                    raise ValueError(f"External ID '{external_id}' already exists")

        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
            "external_id": external_id,
            "organization_id": organization_id,
        }

        # Index by group
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)

        # Index by external ID
        if external_id:
            self._external_id_index[external_id] = agent_id
            if organization_id:
                if organization_id not in self._org_external_index:
                    self._org_external_index[organization_id] = {}
                self._org_external_index[organization_id][external_id] = agent_id

        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def get_by_external_id(
        self, external_id: str, organization_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if organization_id:
            if organization_id in self._org_external_index:
                agent_id = self._org_external_index[organization_id].get(external_id)
                return self._agents.get(agent_id) if agent_id else None
            return None
        agent_id = self._external_id_index.get(external_id)
        return self._agents.get(agent_id) if agent_id else None

    def list(self, status: Optional[AgentStatus] = None, group: Optional[str] = None) -> List[Dict[str, Any]]:
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
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)

        # Remove from external ID indices
        external_id = agent.get("external_id")
        if external_id:
            if external_id in self._external_id_index:
                del self._external_id_index[external_id]
            org_id = agent.get("organization_id")
            if org_id and org_id in self._org_external_index:
                if external_id in self._org_external_index[org_id]:
                    del self._org_external_index[org_id][external_id]
                # Clean up empty org dict
                if not self._org_external_index[org_id]:
                    del self._org_external_index[org_id]

        return True

    def count(self) -> int:
        return len(self._agents)

    def validate_external_id_uniqueness(
        self, external_id: str, organization_id: Optional[str] = None
    ) -> bool:
        if organization_id:
            if organization_id in self._org_external_index:
                return external_id not in self._org_external_index[organization_id]
            return True
        return external_id not in self._external_id_index