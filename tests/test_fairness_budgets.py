"""Tests for fairness budget enforcement (Issue #4604)."""

import asyncio
import pytest
from src.orchestrator.scheduler import (
    TaskScheduler,
    FairnessBudgetViolation,
    StaleStateTransition,
    UNLIMITED,
)


class TestFairnessBudgets:
    """Fairness budget isolation per priority class."""

    def setup_method(self):
        self.scheduler = TaskScheduler(default_budget=3, max_retries=3)

    def test_budget_enforced_per_priority_class(self):
        """Each priority class has its own independent budget."""
        # Enqueue 3 tasks (budget=3), 4th raises
        for i in range(3):
            self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        with pytest.raises(FairnessBudgetViolation):
            self.scheduler.enqueue({"type": "task"}, priority_class="urgent")

    def test_different_priority_classes_independent(self):
        """Urgent and normal pools don't share budget slots."""
        # Fill urgent to 3
        for _ in range(3):
            self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        # Normal class should still have its own 3 slots
        for _ in range(3):
            tid = self.scheduler.enqueue({"type": "task"}, priority_class="normal")
        # Normal full now
        with pytest.raises(FairnessBudgetViolation):
            self.scheduler.enqueue({"type": "task"}, priority_class="normal")

    def test_unlimited_priority_class(self):
        """UNLIMITED sentinel allows unlimited enqueues."""
        self.scheduler.set_budget_limit("critical", UNLIMITED)
        for i in range(100):
            self.scheduler.enqueue({"type": "task"}, priority_class="critical")
        # No exception

    def test_budget_released_on_complete(self):
        """Completing a task releases its slot."""
        t1 = self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        t2 = self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        t3 = self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        # All 3 slots used

        # Must dequeue before complete — dequeue moves to in-flight
        task1 = asyncio.run(self.scheduler.dequeue())
        self.scheduler.complete(task1["id"])  # releases 1 slot
        t4 = self.scheduler.enqueue({"type": "task"}, priority_class="urgent")  # OK
        assert t4 is not None

    def test_budget_released_on_fail(self):
        """Failing a task (within retry limit) re-enqueues and releases slot."""
        t1 = self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        # Full

        task = asyncio.run(self.scheduler.dequeue())
        result = self.scheduler.fail(task["id"])
        assert result is True  # retry OK, slot released

        t4 = self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        assert t4 is not None

    def test_budget_released_on_fail_over_retries(self):
        """Failing over max_retries releases the budget (dead task frees its slot)."""
        scheduler = TaskScheduler(default_budget=1, max_retries=2)
        t1 = scheduler.enqueue({"type": "task"}, priority_class="urgent")

        task = asyncio.run(scheduler.dequeue())
        # Fail twice (retries = 0, 1)
        scheduler.fail(task["id"])  # retry 1
        task2 = asyncio.run(scheduler.dequeue())
        result = scheduler.fail(task2["id"])  # retry 2 -> over limit
        assert result is False  # no more retries

        # Task is dead, but its slot IS released so new work can proceed
        t2 = scheduler.enqueue({"type": "task"}, priority_class="urgent")
        assert t2 is not None


class TestStaleStateTransitions:
    """Atomic state precondition validation for urgent workflow lanes."""

    def setup_method(self):
        self.scheduler = TaskScheduler(default_budget=10, max_retries=3)

    def test_duplicate_inflight_rejected(self):
        """Urgent lane rejects a second task for the same agent while first is in-flight."""
        t1 = self.scheduler.enqueue(
            {"type": "urgent_workflow", "target_agent": "agent-1"},
            priority_class="urgent",
        )
        task = asyncio.run(self.scheduler.dequeue())

        # Second task for same agent while first still in-flight
        t2 = self.scheduler.enqueue(
            {"type": "urgent_workflow", "target_agent": "agent-1"},
            priority_class="urgent",
        )
        with pytest.raises(StaleStateTransition):
            asyncio.run(self.scheduler.dequeue())

    def test_different_agents_same_urgent_class_ok(self):
        """Different agents can have in-flight urgent tasks simultaneously."""
        self.scheduler.enqueue(
            {"type": "urgent", "target_agent": "agent-1"},
            priority_class="urgent",
        )
        self.scheduler.enqueue(
            {"type": "urgent", "target_agent": "agent-2"},
            priority_class="urgent",
        )
        t1 = asyncio.run(self.scheduler.dequeue())
        t2 = asyncio.run(self.scheduler.dequeue())
        assert t1["target_agent"] != t2["target_agent"]

    def test_stale_transition_rejected_after_fence(self):
        """Transitions with stale expected state are rejected after fence threshold."""
        self.scheduler._state_fence_seconds = 1  # 1-second fence
        # Pre-cache agent state
        self.scheduler.update_agent_state("agent-1", "RUNNING")
        # Enqueue with stale expected state (older than fence)
        old_task = {
            "type": "urgent",
            "target_agent": "agent-1",
            "expected_agent_state": "IDLE",  # stale, cache says RUNNING
            "priority_class": "urgent",
        }
        old_task["enqueued_at"] = self.scheduler._state_fence_seconds + 10
        self.scheduler.enqueue(old_task, priority_class="urgent")

        with pytest.raises(StaleStateTransition):
            asyncio.run(self.scheduler.dequeue())

    def test_valid_state_transition_accepted(self):
        """Correct state transition is accepted."""
        self.scheduler.update_agent_state("agent-1", "RUNNING")
        self.scheduler.enqueue(
            {
                "type": "urgent",
                "target_agent": "agent-1",
                "expected_agent_state": "RUNNING",
                "priority_class": "urgent",
            },
            priority_class="urgent",
        )
        task = asyncio.run(self.scheduler.dequeue())
        assert task["target_agent"] == "agent-1"

    def test_non_urgent_lanes_skip_stale_check(self):
        """Non-urgent lanes don't enforce state cache checks."""
        self.scheduler.update_agent_state("agent-1", "RUNNING")
        self.scheduler.enqueue(
            {
                "type": "normal",
                "target_agent": "agent-1",
                "expected_agent_state": "IDLE",  # wrong but not urgent
                "priority_class": "normal",
            },
            priority_class="normal",
        )
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None  # no StaleStateTransition


class TestBudgetAuditLogging:
    """Audit trail for budget decisions."""

    def setup_method(self):
        self.scheduler = TaskScheduler(default_budget=2)

    def test_budget_exhaustion_raises_exception(self):
        """Budget exhaustion raises FairnessBudgetViolation (proves enforcement)."""
        sched = TaskScheduler(default_budget=1)
        sched.enqueue({"type": "task"}, priority_class="urgent")
        with pytest.raises(FairnessBudgetViolation):
            sched.enqueue({"type": "task"}, priority_class="urgent")

    def test_urgent_budget_reports_list_slots(self):
        """Budget report shows used/max per priority class."""
        self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        self.scheduler.enqueue({"type": "task"}, priority_class="urgent")
        used, max_slots = self.scheduler._get_budget("urgent")
        assert used == 2
        assert max_slots == 2


class TestRegressionCoverage:
    """Regression: existing TaskScheduler tests must still pass."""

    def test_enqueue_returns_id(self):
        scheduler = TaskScheduler()
        tid = scheduler.enqueue({"type": "test"})
        assert tid is not None

    def test_dequeue_respects_priority(self):
        scheduler = TaskScheduler()
        scheduler.enqueue({"type": "low"}, priority=1)
        scheduler.enqueue({"type": "high"}, priority=10)
        task = asyncio.run(scheduler.dequeue())
        assert task["type"] == "high"

    def test_complete_removes_from_inflight(self):
        scheduler = TaskScheduler()
        scheduler.enqueue({"type": "test"})
        task = asyncio.run(scheduler.dequeue())
        assert scheduler.complete(task["id"]) is True

    def test_fail_within_retries_reenqueues(self):
        scheduler = TaskScheduler(max_retries=3)
        scheduler.enqueue({"type": "test"})
        task = asyncio.run(scheduler.dequeue())
        result = scheduler.fail(task["id"])
        assert result is True
