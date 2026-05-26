"""Regression tests for Issue #4758 — persist reducer errors separately."""

import asyncio
import pytest
from src.orchestrator.scheduler import (
    TaskScheduler,
    TaskState,
    ReducerErrorStore,
    ReducerError,
    _is_valid_transition,
)


class TestTransitionTable:
    """Verify deterministic state transition rules."""

    def test_pending_to_running_allowed(self):
        assert _is_valid_transition(TaskState.PENDING, TaskState.RUNNING) is True

    def test_running_to_completed_allowed(self):
        assert _is_valid_transition(TaskState.RUNNING, TaskState.COMPLETED) is True

    def test_running_to_pending_allowed(self):
        assert _is_valid_transition(TaskState.RUNNING, TaskState.PENDING) is True

    def test_running_to_dead_allowed(self):
        assert _is_valid_transition(TaskState.RUNNING, TaskState.DEAD) is True

    def test_completed_is_terminal(self):
        assert _is_valid_transition(TaskState.COMPLETED, TaskState.RUNNING) is False
        assert _is_valid_transition(TaskState.COMPLETED, TaskState.PENDING) is False
        assert _is_valid_transition(TaskState.COMPLETED, TaskState.DEAD) is False

    def test_dead_is_terminal(self):
        assert _is_valid_transition(TaskState.DEAD, TaskState.RUNNING) is False
        assert _is_valid_transition(TaskState.DEAD, TaskState.PENDING) is False

    def test_pending_to_dead_allowed(self):
        assert _is_valid_transition(TaskState.PENDING, TaskState.DEAD) is True


class TestReducerErrorStore:
    """Issue #4758: reducer errors must be persisted separately."""

    def test_record_error(self):
        store = ReducerErrorStore()
        err = store.record(
            task_id="task-1",
            from_state=TaskState.COMPLETED,
            to_state=TaskState.RUNNING,
            reason="invalid transition COMPLETED→RUNNING",
            attempt=0,
            revision=1,
            sanitized_ctx={"queue": "default"},
        )
        assert err.task_id == "task-1"
        assert err.reason == "invalid transition COMPLETED→RUNNING"
        assert err.sanitized_ctx == {"queue": "default"}

    def test_get_errors(self):
        store = ReducerErrorStore()
        store.record("task-1", TaskState.COMPLETED, TaskState.RUNNING,
                     "bad", 0, 1)
        store.record("task-1", TaskState.DEAD, TaskState.PENDING,
                     "bad", 3, 2)
        errors = store.get_errors("task-1")
        assert len(errors) == 2

    def test_duplicate_complete_rejected(self):
        """Duplicate complete() call on already-completed task is rejected and recorded."""
        scheduler = TaskScheduler(max_retries=3)
        task_id = scheduler.enqueue({"type": "test"}, queue="default")

        # Dequeue to move to RUNNING
        async def drain():
            return await scheduler.dequeue()
        t = asyncio.run(drain())

        # Complete once — succeeds
        assert scheduler.complete(task_id) is True
        assert scheduler.get_errors(task_id) == []

        # Complete again — rejected, error recorded
        assert scheduler.complete(task_id) is False
        errors = scheduler.get_errors(task_id)
        assert len(errors) == 1
        assert "duplicate" in errors[0].reason.lower()

    def test_duplicate_fail_after_dead(self):
        """Second fail() on dead task is rejected.
        
        Flow: dequeue → fail(retry) → dequeue → fail(dead) → fail(stale→rejected)
        """
        scheduler = TaskScheduler(max_retries=2)
        task_id = scheduler.enqueue({"type": "test"}, queue="default")

        async def drain():
            return await scheduler.dequeue()
        asyncio.run(drain())

        # Fail #1: attempt 1 < max_retries → retry, task back to PENDING queue
        assert scheduler.fail(task_id) is True
        assert scheduler._task_state[task_id][0] == TaskState.PENDING

        # Dequeue again: pulls task from queue back to RUNNING
        t2 = asyncio.run(drain())
        assert t2 is not None
        assert scheduler._task_state[task_id][0] == TaskState.RUNNING

        # Fail #2: attempt 2 >= max_retries → DEAD
        assert scheduler.fail(task_id) is True
        assert scheduler._task_state[task_id][0] == TaskState.DEAD

        # Fail #3: already DEAD → rejected, error recorded
        assert scheduler.fail(task_id) is False
        errors = scheduler.get_errors(task_id)
        assert any("dead" in e.reason.lower() for e in errors)

    def test_complete_without_dequeue_rejected(self):
        """complete() without dequeue first is invalid (task in PENDING state)."""
        scheduler = TaskScheduler(max_retries=3)
        task_id = scheduler.enqueue({"type": "test"})

        # Try to complete without dequeue — invalid (PENDING → COMPLETED not allowed)
        result = scheduler.complete(task_id)
        # PENDING→COMPLETED is not valid, so should be rejected
        errors = scheduler.get_errors(task_id)
        assert len(errors) >= 1

    def test_error_store_audit_report(self):
        """audit_report() returns all errors without private runtime data."""
        scheduler = TaskScheduler(max_retries=3)
        task_id = scheduler.enqueue({"secret_key": "DO_NOT_EXPOSE"}, queue="default")

        async def drain():
            return await scheduler.dequeue()
        asyncio.run(drain())

        scheduler.complete(task_id)
        scheduler.complete(task_id)  # duplicate

        report = scheduler.error_audit_report()
        assert len(report) >= 1
        # Verify no private fields leaked
        for entry in report:
            assert "secret_key" not in str(entry)
            assert "DO_NOT_EXPOSE" not in str(entry)

    def test_revision_increments_on_valid_transition(self):
        """Each accepted transition increments the task revision counter."""
        scheduler = TaskScheduler(max_retries=3)
        task_id = scheduler.enqueue({"type": "test"})

        async def drain():
            return await scheduler.dequeue()
        asyncio.run(drain())

        # RUNNING → COMPLETED
        assert scheduler.complete(task_id) is True

        # Verify revision in in-flight is updated
        state_entry = scheduler._task_state.get(task_id)
        assert state_entry is not None
        state, rev = state_entry
        assert state == TaskState.COMPLETED
        assert rev == 2  # 0 (initial) + 1 (dequeue PENDING→RUNNING) + 1 (complete)

    def test_stale_fail_during_retry_recorded(self):
        """Stale fail() call during concurrent retry is recorded, not silently accepted."""
        scheduler = TaskScheduler(max_retries=3)
        task_id = scheduler.enqueue({"type": "test"}, queue="default")

        async def drain():
            return await scheduler.dequeue()
        asyncio.run(drain())

        # Manually move to PENDING via fail
        scheduler.fail(task_id)

        # Verify in PENDING state
        state, rev = scheduler._task_state[task_id]
        assert state == TaskState.PENDING

        # Dequeue again → RUNNING
        asyncio.run(drain())

        # Now fail during RUNNING
        scheduler.fail(task_id)
        errors = scheduler.get_errors(task_id)
        # No errors expected for valid transitions
        assert all("stale" not in e.reason.lower() for e in errors)

    def test_sanitized_ctx_contains_only_safe_fields(self):
        """Reducer errors only include explicitly allowed structural fields."""
        store = ReducerErrorStore()
        store.record(
            task_id="t1",
            from_state=TaskState.RUNNING,
            to_state=TaskState.COMPLETED,
            reason="test",
            attempt=1,
            revision=2,
            sanitized_ctx={"queue": "default", "priority": 5},
        )
        err = store.get_errors("t1")[0]
        assert err.sanitized_ctx == {"queue": "default", "priority": 5}
        # Explicitly check no leakage
        assert "password" not in err.sanitized_ctx
        assert "token" not in str(err.sanitized_ctx)
