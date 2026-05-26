import pytest
from src.agent.registry import AgentRegistry, AgentStatus


class TestAgentRegistry:
    def setup_method(self):
        self.registry = AgentRegistry()
        self.tenant_a = "tenant-a"
        self.tenant_b = "tenant-b"

    def test_register_agent_requires_tenant_id(self):
        with pytest.raises(ValueError, match="tenant_id is required"):
            self.registry.register("test-agent", "worker.processor", "")

    def test_register_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor", self.tenant_a)
        assert agent_id is not None
        assert self.registry.count() == 1
        assert self.registry.count(self.tenant_a) == 1

    def test_get_agent_scoped_to_tenant(self):
        agent_id = self.registry.register("test-agent", "worker.processor", self.tenant_a)
        # Correct tenant can access
        agent = self.registry.get(agent_id, tenant_id=self.tenant_a)
        assert agent is not None
        assert agent["name"] == "test-agent"
        assert agent["type"] == "worker.processor"
        assert agent["tenant_id"] == self.tenant_a
        # Wrong tenant cannot access
        agent = self.registry.get(agent_id, tenant_id=self.tenant_b)
        assert agent is None

    def test_get_nonexistent_agent(self):
        agent = self.registry.get("nonexistent-id", tenant_id=self.tenant_a)
        assert agent is None

    def test_list_agents(self):
        self.registry.register("agent-1", "worker.processor", self.tenant_a)
        self.registry.register("agent-2", "worker.analyzer", self.tenant_a)
        self.registry.register("agent-3", "monitor.watcher", self.tenant_b)
        assert len(self.registry.list()) == 3
        assert len(self.registry.list(tenant_id=self.tenant_a)) == 2
        assert len(self.registry.list(tenant_id=self.tenant_b)) == 1

    def test_list_agents_by_group_scoped_to_tenant(self):
        self.registry.register("agent-1", "worker.processor", self.tenant_a)
        self.registry.register("agent-2", "monitor.watcher", self.tenant_a)
        self.registry.register("agent-3", "worker.processor", self.tenant_b)
        # tenant-a has 1 worker
        workers_a = self.registry.list(group="worker", tenant_id=self.tenant_a)
        assert len(workers_a) == 1
        # tenant-b has 1 worker
        workers_b = self.registry.list(group="worker", tenant_id=self.tenant_b)
        assert len(workers_b) == 1

    def test_update_status_scoped_to_tenant(self):
        agent_id = self.registry.register("test-agent", "worker.processor", self.tenant_a)
        # Correct tenant can update
        assert self.registry.update_status(agent_id, AgentStatus.RUNNING, tenant_id=self.tenant_a)
        agent = self.registry.get(agent_id, tenant_id=self.tenant_a)
        assert agent["status"] == "running"
        # Wrong tenant cannot update
        assert not self.registry.update_status(agent_id, AgentStatus.FAILED, tenant_id=self.tenant_b)

    def test_delete_agent_scoped_to_tenant(self):
        agent_id = self.registry.register("test-agent", "worker.processor", self.tenant_a)
        # Wrong tenant cannot delete
        assert not self.registry.delete(agent_id, tenant_id=self.tenant_b)
        assert self.registry.count(self.tenant_a) == 1
        # Correct tenant can delete
        assert self.registry.delete(agent_id, tenant_id=self.tenant_a)
        assert self.registry.count() == 0

    def test_delete_nonexistent_agent(self):
        assert not self.registry.delete("nonexistent-id", tenant_id=self.tenant_a)

    def test_cross_tenant_isolation(self):
        """Regression: tenant-a agents must not be visible to tenant-b."""
        a1 = self.registry.register("a-worker", "worker.processor", self.tenant_a)
        a2 = self.registry.register("b-worker", "worker.processor", self.tenant_b)
        # Each tenant only sees its own agents
        assert len(self.registry.list(tenant_id=self.tenant_a)) == 1
        assert len(self.registry.list(tenant_id=self.tenant_b)) == 1
        # Cross-tenant get returns None
        assert self.registry.get(a1, tenant_id=self.tenant_b) is None
        assert self.registry.get(a2, tenant_id=self.tenant_a) is None

    def test_resolve_agents_scoped_to_tenant(self):
        self.registry.register("agent-1", "worker.processor", self.tenant_a)
        self.registry.register("agent-2", "worker.processor", self.tenant_b)
        self.registry.register("agent-3", "monitor.watcher", self.tenant_a)
        # Resolve workers for tenant-a only
        workers_a = self.registry.resolve(agent_type="worker.processor", tenant_id=self.tenant_a)
        assert len(workers_a) == 1
        assert workers_a[0]["tenant_id"] == self.tenant_a


class TestAgentRegistryNoTenantBackwards:
    """Backwards-compat: operations without tenant_id work for existing agents."""
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_register_still_requires_tenant_id(self):
        with pytest.raises(ValueError):
            self.registry.register("test", "worker.processor", "")

    def test_register_and_get_without_tenant_param(self):
        """get() without tenant_id still returns the agent (no cross-tenant check)."""
        agent_id = self.registry.register("test-agent", "worker.processor", "default-tenant")
        # Passing no tenant_id param falls back to allowing access
        agent = self.registry.get(agent_id)
        assert agent is not None

# 2019-01-23T10:28:57 update

# 2019-01-28T18:15:57 update

# 2019-02-22T11:46:37 update

# 2019-03-27T14:43:52 update

# 2019-04-12T16:58:25 update

# 2019-05-27T15:15:18 update

# 2019-07-17T14:36:58 update

# 2019-09-06T12:29:31 update

# 2019-11-27T17:43:26 update

# 2019-11-28T08:42:43 update

# 2019-12-03T20:34:02 update

# 2019-12-26T08:15:09 update

# 2020-01-07T09:36:32 update

# 2020-01-10T12:44:52 update

# 2020-07-05T19:33:32 update

# 2020-07-07T14:16:11 update

# 2020-07-28T08:29:39 update

# 2020-08-26T18:58:21 update

# 2020-08-28T09:50:37 update

# 2020-09-17T15:23:33 update

# 2020-09-23T16:22:24 update

# 2020-10-14T13:27:24 update

# 2020-11-20T11:40:04 update

# 2020-12-10T13:55:01 update

# 2020-12-25T20:33:02 update

# 2021-03-22T19:53:48 update

# 2021-03-26T15:02:19 update

# 2021-07-16T20:24:40 update

# 2021-07-22T13:19:23 update

# 2021-08-16T19:11:26 update

# 2021-10-02T13:32:20 update

# 2021-10-23T18:31:31 update

# 2021-10-29T13:55:10 update

# 2022-07-31T17:35:39 update

# 2022-09-27T09:32:34 update

# 2022-11-07T14:44:52 update

# 2023-01-23T14:07:09 update

# 2023-03-16T15:23:38 update

# 2023-07-03T18:33:44 update

# 2023-07-27T09:35:11 update

# 2023-11-16T11:22:59 update

# 2023-12-20T14:25:29 update

# 2024-03-07T17:32:49 update

# 2024-04-10T10:50:42 update

# 2024-06-19T19:57:49 update

# 2024-12-05T18:02:46 update

# 2025-01-15T16:13:24 update

# 2025-03-12T20:58:57 update

# 2025-06-24T20:33:23 update

# 2025-08-25T10:56:35 update

# 2025-09-12T17:09:51 update

# 2025-10-06T20:01:10 update

# 2025-10-14T11:48:40 update

# 2026-01-29T13:09:29 update
