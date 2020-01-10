"""Agent Sandbox — Isolated execution environment for agents."""

import os
import tempfile
import resource
from typing import Dict, Optional
from pathlib import Path


class ResourceLimits:
    def __init__(self, cpu_time: int = 60, memory_mb: int = 512, disk_mb: int = 100):
        self.cpu_time = cpu_time
        self.memory_mb = memory_mb
        self.disk_mb = disk_mb


class AgentSandbox:
    def __init__(self, base_path: Optional[str] = None):
        self.base_path = Path(base_path or tempfile.mkdtemp(prefix="ao_sandbox_"))
        self._sandboxes: Dict[str, Path] = {}

    def create(self, agent_id: str, limits: Optional[ResourceLimits] = None) -> Path:
        sandbox_path = self.base_path / agent_id
        sandbox_path.mkdir(parents=True, exist_ok=True)
        self._sandboxes[agent_id] = sandbox_path
        return sandbox_path

    def destroy(self, agent_id: str) -> bool:
        sandbox = self._sandboxes.pop(agent_id, None)
        if sandbox and sandbox.exists():
            import shutil
            shutil.rmtree(sandbox, ignore_errors=True)
            return True
        return False

    def get_path(self, agent_id: str) -> Optional[Path]:
        return self._sandboxes.get(agent_id)

    def apply_limits(self, agent_id: str, limits: ResourceLimits) -> None:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_time, limits.cpu_time))
            mem_bytes = limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except (ValueError, resource.error) as e:
            pass

    def cleanup_all(self) -> None:
        for agent_id in list(self._sandboxes.keys()):
            self.destroy(agent_id)

# 2019-01-10T19:56:24 update

# 2019-02-07T17:00:55 update

# 2019-05-15T17:22:36 update

# 2019-06-14T10:08:48 update

# 2019-06-21T11:59:12 update

# 2019-06-26T13:24:25 update

# 2019-07-05T09:17:12 update

# 2019-07-09T11:23:07 update

# 2019-07-10T16:17:07 update

# 2019-08-15T10:32:06 update

# 2019-08-18T17:28:35 update

# 2019-10-15T13:00:49 update

# 2019-10-25T10:56:15 update

# 2020-01-10T10:39:46 update
