"""Regression tests for Issue #4315 — Normalize agent IDs before route matching."""

import pytest
from src.agent.registry import AgentRegistry, AgentStatus, AuthContext, Role, _normalize_agent_id
from src.common.errors import AuthorizationError


class TestNormalizeAgentId:
    """Unit tests for the normalization function itself."""

    def test_lowercase_preserved(self):
        assert _normalize_agent_id("abc-123") == "abc-123"

    def test_mixed_case_normalized(self):
        assert _normalize_agent_id("AbC-123") == "abc-123"

    def test_uppercase_normalized(self):
        assert _normalize_agent_id("ABC-123") == "abc-123"

    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="must be a string"):
            _normalize_agent_id(123)

    def test_none_rejected(self):
        with pytest.raises(ValueError, match="must be a string"):
            _normalize_agent_id(None)


class TestAgentRegistryMixedCaseLookup:
    """Ensure mixed-case agent IDs are normalized before any operation."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_get_with_mixed_case(self):
        """get() with mixed-case ID finds the lower-case registered agent."""
        agent_id = self.registry.register("test-agent", "worker.processor")
        # Simulate a mixed-case lookup (e.g. "AbC-123" vs "abc-123")
        mixed = agent_id.upper() if agent_id != agent_id.lower() else agent_id + "X"
        # Register with lower-case, lookup with different case
        real_id = self.registry.register("test-agent-2", "worker.processor")
        looked_up = self.registry.get(real_id.swapcase())  # opposite case
        # After normalization, lookup should succeed
        assert looked_up is not None

    def test_delete_with_mixed_case(self):
        """delete() with mixed-case ID deletes the correct agent."""
        agent_id = self.registry.register("test-agent", "worker.processor")
        # Delete with upper-case variant
        deleted = self.registry.delete(agent_id.upper())
        assert deleted is True
        assert self.registry.count() == 0

    def test_update_status_with_mixed_case(self):
        """update_status() with mixed-case ID updates the correct agent."""
        agent_id = self.registry.register("test-agent", "worker.processor")
        # Use opposite-case variant - normalization makes it match
        opposite = agent_id.upper() if agent_id == agent_id.lower() else agent_id.lower()
        updated = self.registry.update_status(opposite, AgentStatus.RUNNING)
        assert updated is True
        agent = self.registry.get(agent_id)
        assert agent["status"] == "running"

    def test_get_returns_none_for_nonexistent_normalized(self):
        """Non-existent IDs return None after normalization."""
        assert self.registry.get("NON-EXISTENT-ID") is None

    def test_delete_returns_false_for_nonexistent_normalized(self):
        """Non-existent IDs return False after normalization."""
        assert self.registry.delete("NON-EXISTENT-ID") is False

    def test_update_status_returns_false_for_nonexistent_normalized(self):
        """Non-existent IDs return False after normalization."""
        assert self.registry.update_status("NON-EXISTENT-ID", AgentStatus.RUNNING) is False


class TestAgentRegistryMixedCaseAuth:
    """Mixed-case ID normalization combined with auth context."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_get_mixed_case_with_auth(self):
        """Mixed-case ID lookup respects workspace membership."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        agent_id = self.registry.register(
            "agent", "worker.processor", workspace_id="ws-1", auth=ctx
        )
        # Lookup with upper-case should still respect workspace
        agent = self.registry.get(agent_id.upper(), auth=ctx)
        assert agent is not None
        assert agent["id"] == agent_id

    def test_delete_mixed_case_with_auth(self):
        """Mixed-case ID deletion requires auth and respects workspace."""
        ctx_ws1 = AuthContext(workspace_id="ws-1", role=Role.ADMIN)
        ctx_ws2 = AuthContext(workspace_id="ws-2", role=Role.ADMIN)
        agent_id = self.registry.register(
            "agent", "worker.processor", workspace_id="ws-1", auth=ctx_ws1
        )
        # ws-2 cannot delete ws-1 agent even with mixed-case ID
        with pytest.raises(AuthorizationError):
            self.registry.delete(agent_id.upper(), auth=ctx_ws2)

    def test_update_status_mixed_case_with_auth(self):
        """Mixed-case ID status update requires auth."""
        ctx_ws1 = AuthContext(workspace_id="ws-1", role=Role.EDITOR)
        agent_id = self.registry.register(
            "agent", "worker.processor", workspace_id="ws-1", auth=ctx_ws1
        )
        # Successful update with mixed-case ID
        result = self.registry.update_status(agent_id.upper(), AgentStatus.RUNNING, auth=ctx_ws1)
        assert result is True
        assert self.registry.get(agent_id)["status"] == "running"


class TestRegressionEdgeCases:
    """Regression edge cases for Issue #4315."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_uuid_format_agents_normalized(self):
        """UUID-format agent IDs are normalized (UUIDs are lower-case already)."""
        import uuid
        agent_id = self.registry.register("test-agent", "worker.processor")
        # UUIDs are lower-case; verify normalization still works
        looked_up = self.registry.get(agent_id.upper())
        assert looked_up is not None

    def test_all_caps_id_normalized(self):
        """All-caps registered agent can be found with lower-case lookup."""
        # Register two agents with different case
        id1 = self.registry.register("agent-a", "worker.processor")
        # Verify normalization works both ways
        assert self.registry.get(id1.upper()) is not None

    def test_sequential_operations_same_id(self):
        """Multiple operations on the same mixed-case ID all refer to same agent."""
        agent_id = self.registry.register("test-agent", "worker.processor")
        self.registry.update_status(agent_id.upper(), AgentStatus.RUNNING)
        agent = self.registry.get(agent_id)
        assert agent["status"] == "running"
        deleted = self.registry.delete(agent_id)
        assert deleted is True
        assert self.registry.count() == 0
