"""Orchestrator API client SDK with idempotency key and retry support."""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from src.api.idempotency import IdempotencyStore


class OrchestratorClient:
    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
    ):
        self.base_url = base_url or os.getenv(
            "AO_API_URL", "https://api.agent-orchestrator.io"
        )
        self.api_key = api_key or os.getenv("AO_API_KEY", "")
        self._session = None
        # Retry config for destructive actions
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._idempotency_store = IdempotencyStore()

    def _request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        idempotency_key: Optional[str] = None,
        _retry_count: int = 0,
    ) -> Dict:
        url = f"{self.base_url}/api/v2{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, headers=headers, method=method)

        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code >= 500 and _retry_count < self._max_retries:
                # Server error — retry with backoff
                delay = self._retry_base_delay * (2 ** _retry_count)
                time.sleep(delay)
                return self._request(
                    method, path, data,
                    idempotency_key=idempotency_key,
                    _retry_count=_retry_count + 1,
                )
            return {"error": e.code, "message": e.reason}

    def _destructive_request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict:
        """Issue a destructive (mutation) request with idempotency key and retry.

        If idempotency_key is not provided, one is auto-generated so callers
        can safely retry without knowing the key in advance.
        """
        if idempotency_key is None:
            idempotency_key = IdempotencyStore.generate_key()
        return self._request(method, path, data, idempotency_key=idempotency_key)

    def register_agent(
        self, name: str, agent_type: str, config: Dict = None
    ) -> Dict:
        return self._request("POST", "/agents", {
            "name": name,
            "agent_type": agent_type,
            "config": config or {},
        })

    def list_agents(self, status: str = None) -> Dict:
        path = "/agents"
        if status:
            path += f"?status={status}"
        return self._request("GET", path)

    def get_agent(self, agent_id: str) -> Dict:
        return self._request("GET", f"/agents/{agent_id}")

    def delete_agent(
        self, agent_id: str, idempotency_key: Optional[str] = None
    ) -> Dict:
        """Delete an agent. Uses idempotency key to prevent double-delete."""
        return self._destructive_request(
            "DELETE", f"/agents/{agent_id}", idempotency_key=idempotency_key
        )

    def start_agent(
        self, agent_id: str, idempotency_key: Optional[str] = None
    ) -> Dict:
        """Start an agent. Uses idempotency key to prevent double-start."""
        return self._destructive_request(
            "POST", f"/agents/{agent_id}/start", idempotency_key=idempotency_key
        )

    def stop_agent(
        self, agent_id: str, idempotency_key: Optional[str] = None
    ) -> Dict:
        """Stop an agent. Uses idempotency key to prevent double-stop."""
        return self._destructive_request(
            "POST", f"/agents/{agent_id}/stop", idempotency_key=idempotency_key
        )

    def revoke_agent(
        self, agent_id: str, idempotency_key: Optional[str] = None
    ) -> Dict:
        """Revoke an agent. Uses idempotency key to prevent double-revoke."""
        return self._destructive_request(
            "POST", f"/agents/{agent_id}/revoke",
            {"agent_id": agent_id},
            idempotency_key=idempotency_key,
        )


# 2019-01-22T18:13:52 update

# 2019-04-10T16:03:03 update

# 2019-06-26T09:36:49 update

# 2019-08-16T09:00:29 update

# 2019-09-23T14:45:51 update

# 2019-10-21T11:37:23 update

# 2020-01-10T10:26:44 update

# 2020-01-17T13:18:12 update

# 2020-02-12T09:30:00 update

# 2020-03-08T08:00:00 update

# 2020-03-16T19:59:51 update

# 2020-03-30T17:37:00 update

# 2021-02-05T19:46:18 update

# 2021-02-22T16:54:35 update

# 2021-03-19T15:58:33 update

# 2021-04-15T08:14:27 update

# 2021-05-31T14:33:51 update

# 2021-07-15T18:08:00 update

# 2021-08-24T11:47:11 update

# 2021-12-30T12:02:36 update

# 2022-01-20T13:18:44 update

# 2022-06-17T10:50:00 update

# 2022-11-15T19:15:18 update

# 2023-05-15T18:16:27 update

# 2023-06-22T14:34:00 update

# 2023-07-13T18:44:28 update

# 2023-08-23T19:53:34 update

# 2023-11-17T08:37:45 update

# 2024-01-31T16:24:31 update

# 2024-01-31T08:14:03 update

# 2024-02-01T09:10:23 update

# 2024-07-22T19:04:53 update

# 2024-09-03T09:17:20 update

# 2024-11-13T11:27:07 update

# 2025-01-16T20:56:50 update

# 2025-04-14T16:10:30 update

# 2025-04-16T08:42:38 update

# 2025-05-02T08:11:40 update

# 2025-07-04T18:10:30 update

# 2025-07-23T09:21:21 update

# 2025-09-05T17:05:59 update

# 2025-09-09T15:51:23 update

# 2025-11-14T11:34:48 update

# 2025-12-18T18:16:23 update

# 2025-12-18T14:56:18 update

# 2025-12-18T12:41:47 update

# 2026-02-17T20:41:29 update

# 2026-03-18T12:20:20 update

# 2026-03-20T13:32:14 update

# 2026-03-31T16:25:41 update

# 2026-04-07T11:14:09 update

# 2026-05-11T08:44:28 update

# 2026-05-14T13:49:57 update
