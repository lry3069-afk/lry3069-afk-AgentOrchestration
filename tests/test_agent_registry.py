import pytest
from src.agent.registry import AgentRegistry, AgentStatus, AuthContext, Role
from src.common.errors import AuthorizationError


class TestAgentRegistry:
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_register_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        assert agent_id is not None
        assert self.registry.count() == 1

    def test_get_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        agent = self.registry.get(agent_id)
        assert agent is not None
        assert agent["name"] == "test-agent"
        assert agent["type"] == "worker.processor"

    def test_get_nonexistent_agent(self):
        agent = self.registry.get("nonexistent-id")
        assert agent is None

    def test_list_agents(self):
        self.registry.register("agent-1", "worker.processor")
        self.registry.register("agent-2", "worker.analyzer")
        self.registry.register("agent-3", "monitor.watcher")
        assert len(self.registry.list()) == 3

    def test_list_agents_by_group(self):
        self.registry.register("agent-1", "worker.processor")
        self.registry.register("agent-2", "monitor.watcher")
        workers = self.registry.list(group="worker")
        assert len(workers) == 1

    def test_update_status(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        assert self.registry.update_status(agent_id, AgentStatus.RUNNING)
        agent = self.registry.get(agent_id)
        assert agent["status"] == "running"

    def test_delete_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        assert self.registry.delete(agent_id)
        assert self.registry.count() == 0

    def test_delete_nonexistent_agent(self):
        assert not self.registry.delete("nonexistent-id")


class TestAuthContext:
    def test_workspace_and_role_defaults(self):
        ctx = AuthContext(workspace_id="ws-1")
        assert ctx.workspace_id == "ws-1"
        assert ctx.role == Role.VIEWER
        assert ctx.is_anonymous is False

    def test_explicit_role(self):
        ctx = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        assert ctx.is_admin() is True
        assert ctx.can_mutate() is True

    def test_editor_can_mutate(self):
        ctx = AuthContext(workspace_id="ws-1", role=Role.EDITOR)
        assert ctx.is_admin() is False
        assert ctx.can_mutate() is True

    def test_viewer_cannot_mutate(self):
        ctx = AuthContext(workspace_id="ws-1", role=Role.VIEWER)
        assert ctx.can_mutate() is False

    def test_stale_detection(self):
        ctx = AuthContext(workspace_id="ws-1")
        assert ctx.is_stale(max_age_seconds=300.0) is False
        # Force staleness by backdating
        ctx._last_checked = ctx._created_at - 301.0
        assert ctx.is_stale(max_age_seconds=300.0) is True

    def test_refresh_resets_staleness(self):
        ctx = AuthContext(workspace_id="ws-1")
        ctx._last_checked = ctx._created_at - 400.0
        assert ctx.is_stale(max_age_seconds=300.0) is True
        ctx.refresh()
        assert ctx.is_stale(max_age_seconds=300.0) is False


class TestWorkspaceMembership:
    """Regression tests for Issue #4687 — enforce workspace membership on agent operations."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_cross_workspace_get_denied(self):
        """Agent in ws-1 cannot be read with ws-2 auth context."""
        ctx_ws1 = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        ctx_ws2 = AuthContext(workspace_id="ws-2", role=Role.ADMIN)
        agent_id = self.registry.register(
            "agent", "worker.processor", workspace_id="ws-1", auth=ctx_ws1
        )
        with pytest.raises(AuthorizationError, match="workspace"):
            self.registry.get(agent_id, auth=ctx_ws2)

    def test_same_workspace_get_allowed(self):
        """Agent in ws-1 can be read with ws-1 auth context."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        agent_id = self.registry.register(
            "agent", "worker.processor", workspace_id="ws-1", auth=ctx
        )
        agent = self.registry.get(agent_id, auth=ctx)
        assert agent is not None
        assert agent["id"] == agent_id

    def test_anonymous_denied(self):
        """Anonymous principals cannot access agents."""
        ctx_anonymous = AuthContext(workspace_id="ws-1", is_anonymous=True)
        agent_id = self.registry.register("agent", "worker.processor", workspace_id="ws-1")
        with pytest.raises(AuthorizationError, match="Anonymous"):
            self.registry.get(agent_id, auth=ctx_anonymous)

    def test_stale_credential_denied(self):
        """Stale credentials are rejected."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        ctx._last_checked = ctx._created_at - 400.0
        agent_id = self.registry.register("agent", "worker.processor", workspace_id="ws-1")
        with pytest.raises(AuthorizationError, match="Stale"):
            self.registry.get(agent_id, auth=ctx)

    def test_revoked_credential_denied(self):
        """Revoked credentials are rejected on every use."""
        import time
        cred_id = "cred-123"
        ctx = AuthContext(workspace_id="ws-1", role=Role.ADMIN, credential_id=cred_id)
        agent_id = self.registry.register("agent", "worker.processor", workspace_id="ws-1")
        self.registry.revoke_credential(cred_id)
        with pytest.raises(AuthorizationError, match="revoked"):
            self.registry.get(agent_id, auth=ctx)

    def test_viewer_cannot_mutate(self):
        """VIEWER role cannot mutate (update_status, delete)."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.VIEWER)
        agent_id = self.registry.register("agent", "worker.processor", workspace_id="ws-1")
        with pytest.raises(AuthorizationError, match="cannot mutate"):
            self.registry.update_status(agent_id, AgentStatus.RUNNING, auth=ctx)

    def test_editor_can_mutate(self):
        """EDITOR role can mutate."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.EDITOR)
        agent_id = self.registry.register("agent", "worker.processor", workspace_id="ws-1")
        result = self.registry.update_status(agent_id, AgentStatus.RUNNING, auth=ctx)
        assert result is True

    def test_admin_can_mutate(self):
        """ADMIN role can mutate."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        agent_id = self.registry.register("agent", "worker.processor", workspace_id="ws-1")
        result = self.registry.delete(agent_id, auth=ctx)
        assert result is True
        assert self.registry.count() == 0

    def test_list_respects_workspace(self):
        """list() returns only agents in the auth context's workspace."""
        ctx_ws1 = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        ctx_ws2 = AuthContext(workspace_id="ws-2", role=Role.ADMIN)
        self.registry.register("agent-ws1", "worker.processor", workspace_id="ws-1", auth=ctx_ws1)
        self.registry.register("agent-ws2", "worker.processor", workspace_id="ws-2", auth=ctx_ws2)
        agents_ws1 = self.registry.list(auth=ctx_ws1)
        assert all(a["workspace_id"] == "ws-1" for a in agents_ws1)
        assert len(agents_ws1) == 1

    def test_register_requires_mutate_permission(self):
        """Registration with auth requires admin/editor role."""
        ctx_viewer = AuthContext(workspace_id="ws-1", role=Role.VIEWER)
        with pytest.raises(AuthorizationError, match="cannot mutate"):
            self.registry.register("agent", "worker.processor", auth=ctx_viewer)

    def test_credential_revocation_static(self):
        """revoke_credential and is_credential_revoked work at class level."""
        cred_id = "cred-456"
        assert AgentRegistry.is_credential_revoked(cred_id) is False
        AgentRegistry.revoke_credential(cred_id)
        assert AgentRegistry.is_credential_revoked(cred_id) is True
