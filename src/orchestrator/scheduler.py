"""Task Scheduler — Priority-based task queuing and dispatch with reducer error isolation."""

import asyncio
import heapq
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


# ── Lifecycle states for reducer state machine ────────────────────────────────

class TaskState(Enum):
    SCHEDULED = "scheduled"   # Enqueued, waiting for delay expiry
    PENDING   = "pending"     # In queue, not yet dequeued
    RUNNING   = "running"     # Dequeued and being processed
    COMPLETED = "completed"   # Successfully finished
    FAILED    = "failed"      # Exhausted retries or terminal error
    DEAD      = "dead"        # Moved to dead-letter after max retries


# ── Reducer error store ─────────────────────────────────────────────────────

class ReducerError:
    """Immutable record of a reducer transition error.

    Private runtime data is explicitly excluded from error records
    to prevent accidental exposure in logs or audit trails.
    """

    __slots__ = ("task_id", "from_state", "to_state", "reason", "attempt",
                 "revision", "timestamp", "sanitized_ctx")

    def __init__(
        self,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        reason: str,
        attempt: int,
        revision: int,
        sanitized_ctx: Optional[Dict[str, Any]] = None,
    ):
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        self.attempt = attempt
        self.revision = revision
        self.timestamp = time.time()
        # Sanitized context — only non-sensitive structural fields allowed
        self.sanitized_ctx = sanitized_ctx or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "attempt": self.attempt,
            "revision": self.revision,
            "timestamp": self.timestamp,
            "sanitized_ctx": self.sanitized_ctx,
        }


class ReducerErrorStore:
    """Persists reducer transition errors separately from the task scheduler.

    Issue #4758: events must be diagnosed even when the reducer rejects
    a transition, so errors are stored here — not silently discarded.
    """

    def __init__(self):
        self._errors: Dict[str, List[ReducerError]] = {}

    def record(
        self,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        reason: str,
        attempt: int,
        revision: int,
        sanitized_ctx: Optional[Dict[str, Any]] = None,
    ) -> ReducerError:
        err = ReducerError(task_id, from_state, to_state, reason,
                           attempt, revision, sanitized_ctx)
        if task_id not in self._errors:
            self._errors[task_id] = []
        self._errors[task_id].append(err)
        logger.warning(
            "ReducerError task_id=%s from=%s to=%s reason=%s attempt=%d rev=%d",
            task_id, from_state.value, to_state.value, reason, attempt, revision,
        )
        return err

    def get_errors(self, task_id: str) -> List[ReducerError]:
        return list(self._errors.get(task_id, []))

    def all_errors(self) -> List[ReducerError]:
        return [e for errors in self._errors.values() for e in errors]

    def clear(self, task_id: Optional[str] = None) -> None:
        if task_id:
            self._errors.pop(task_id, None)
        else:
            self._errors.clear()

    def audit_report(self) -> List[Dict[str, Any]]:
        """Return all errors as a structured audit report (no private data)."""
        return [e.to_dict() for e in self.all_errors()]


# ── Priority queue ───────────────────────────────────────────────────────────

class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


# ── Scheduler ───────────────────────────────────────────────────────────────

class TaskScheduler:
    def __init__(self, max_retries: int = 3):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, float] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._max_retries = max_retries
        self._error_store = ReducerErrorStore()
        # Lifecycle state machine: task_id → (state, revision)
        self._task_state: Dict[str, tuple] = {}

    # ── State machine helpers ──────────────────────────────────────────────

    def _get_state(self, task_id: str) -> TaskState:
        entry = self._task_state.get(task_id)
        if entry is None:
            return TaskState.PENDING
        return entry[0]

    def _set_state(
        self,
        task_id: str,
        new_state: TaskState,
        err_store: ReducerErrorStore,
    ) -> tuple:
        """Atomically advance task state with revision increment.

        Returns (ok, from_state, revision).
        Raises ReducerError on invalid transition (logged to error_store).
        """
        entry = self._task_state.get(task_id)
        if entry is None:
            from_state = TaskState.PENDING
            revision = 0
        else:
            from_state, revision = entry

        # Validate transition is legal
        valid = _is_valid_transition(from_state, new_state)
        if not valid:
            task = self._in_flight.get(task_id, {})
            err_store.record(
                task_id=task_id,
                from_state=from_state,
                to_state=new_state,
                reason=f"invalid transition {from_state.value}→{new_state.value}",
                attempt=task.get("retries", 0),
                revision=revision,
                sanitized_ctx={
                    "queue": task.get("queue", "default"),
                    "priority": task.get("priority", 0),
                },
            )
            return False, from_state, revision

        # Accept transition
        self._task_state[task_id] = (new_state, revision + 1)
        return True, from_state, revision

    # ── Enqueue ───────────────────────────────────────────────────────────

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        task.setdefault("retries", 0)
        task.setdefault("enqueued_at", time.time())
        task["_revision"] = 0
        task["_state"] = TaskState.PENDING.value

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        self._task_state[task_id] = (TaskState.PENDING, 0)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        self._scheduled[task_id] = time.time() + delay
        self._task_state[task_id] = (TaskState.SCHEDULED, 0)
        return task_id

    # ── Dequeue ───────────────────────────────────────────────────────────

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = time.time()
        expired = [tid for tid, t in self._scheduled.items() if t <= now]
        for tid in expired:
            self._scheduled.pop(tid, None)
            # Re-enqueue as pending (scheduler.py enqueue, not self.enqueue)
            task = {"id": tid, "queue": queue, "retries": 0,
                    "enqueued_at": now, "priority": 0, "_revision": 0}
            if queue not in self._queues:
                self._queues[queue] = PriorityQueue()
            self._queues[queue].push(task, 0)
            s, rev = self._task_state.get(tid, (TaskState.PENDING, 0))
            self._task_state[tid] = (TaskState.PENDING, rev)

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                task_id = task["id"]
                # Advance PENDING → RUNNING with guard
                ok, from_state, rev = self._set_state(
                    task_id, TaskState.RUNNING, self._error_store
                )
                if not ok:
                    # Transition rejected — task stayed in previous state;
                    # log but still move to in_flight for diagnostics
                    self._in_flight[task_id] = task
                    return task
                task["_revision"] = rev
                self._in_flight[task_id] = task
                return task
        return None

    # ── Lifecycle ops (reducer transitions) ──────────────────────────────

    def complete(self, task_id: str) -> bool:
        """Complete a task — reducer enforces valid state transition."""
        task = self._in_flight.get(task_id)
        if task is None:
            # Task not in flight — check state machine
            state, rev = self._task_state.get(task_id, (None, 0))
            if state is None:
                return False  # Unknown task, silent no-op
            if state == TaskState.COMPLETED:
                # Duplicate complete — record as rejected duplicate transition
                self._error_store.record(
                    task_id=task_id,
                    from_state=TaskState.COMPLETED,
                    to_state=TaskState.COMPLETED,
                    reason="duplicate complete() call on already-completed task",
                    attempt=0, revision=rev,
                    sanitized_ctx={"task_id": task_id},
                )
                return False
            if state in (TaskState.RUNNING, TaskState.FAILED, TaskState.DEAD):
                self._error_store.record(
                    task_id=task_id,
                    from_state=state,
                    to_state=TaskState.COMPLETED,
                    reason=f"complete() called on {state.value} task — duplicate or stale",
                    attempt=0, revision=rev,
                    sanitized_ctx={"task_id": task_id},
                )
            # PENDING or SCHEDULED: invalid to complete without dequeue first
            # (record as rejected transition)
            elif state in (TaskState.PENDING, TaskState.SCHEDULED):
                self._error_store.record(
                    task_id=task_id,
                    from_state=state,
                    to_state=TaskState.COMPLETED,
                    reason=f"complete() called on {state.value} task — must dequeue first",
                    attempt=0, revision=rev,
                    sanitized_ctx={"task_id": task_id},
                )
            return False

        ok, from_state, rev = self._set_state(
            task_id, TaskState.COMPLETED, self._error_store
        )
        if not ok:
            self._error_store.record(
                task_id=task_id,
                from_state=from_state,
                to_state=TaskState.COMPLETED,
                reason="invalid transition to COMPLETED",
                attempt=task.get("retries", 0),
                revision=rev,
                sanitized_ctx={
                    "queue": task.get("queue", "default"),
                    "priority": task.get("priority", 0),
                },
            )
            return False

        self._in_flight.pop(task_id, None)
        return True

    def fail(self, task_id: str, queue: str = "default") -> bool:
        """Fail a task — reducer enforces retry/dead-letter policy."""
        task = self._in_flight.get(task_id)
        if task is None:
            state, rev = self._task_state.get(task_id, (None, 0))
            if state is None:
                return False  # Unknown task
            if state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.DEAD):
                self._error_store.record(
                    task_id=task_id,
                    from_state=state,
                    to_state=TaskState.FAILED,
                    reason=f"fail() called on {state.value} task — stale transition",
                    attempt=0, revision=rev,
                    sanitized_ctx={"task_id": task_id},
                )
                return False
            if state == TaskState.PENDING:
                # Task is queued — extract it and fail it
                found = self._extract_from_queue(task_id, queue)
                if not found:
                    return False
                task = found
            elif state == TaskState.SCHEDULED:
                self._scheduled.pop(task_id, None)
            else:
                return False

        attempt = task.get("retries", 0) + 1
        task["retries"] = attempt

        if attempt >= self._max_retries:
            ok, from_state, rev = self._set_state(
                task_id, TaskState.DEAD, self._error_store
            )
            if not ok:
                self._error_store.record(
                    task_id=task_id,
                    from_state=from_state,
                    to_state=TaskState.DEAD,
                    reason="stale or duplicate fail() call on dead task",
                    attempt=attempt,
                    revision=rev,
                    sanitized_ctx={
                        "queue": task.get("queue", "default"),
                        "priority": task.get("priority", 0),
                        "max_retries": self._max_retries,
                    },
                )
                return False
            self._in_flight.pop(task_id, None)
            return True

        ok, from_state, rev = self._set_state(
            task_id, TaskState.PENDING, self._error_store
        )
        if not ok:
            self._error_store.record(
                task_id=task_id,
                from_state=from_state,
                to_state=TaskState.PENDING,
                reason=f"stale fail() call during retry (attempt {attempt})",
                attempt=attempt,
                revision=rev,
                sanitized_ctx={
                    "queue": task.get("queue", "default"),
                    "priority": task.get("priority", 0),
                },
            )
            return False

        task["_revision"] = rev
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, task.get("priority", 0))
        self._in_flight.pop(task_id, None)
        return True

    def _extract_from_queue(self, task_id: str, queue: str) -> Optional[Dict]:
        """Pop a task by ID from a priority queue (linear scan)."""
        if queue not in self._queues:
            return None
        pq = self._queues[queue]
        # Rebuild heap without the target task
        items = []
        while len(pq._queue):
            _, _, item = heapq.heappop(pq._queue)
            if item["id"] == task_id:
                return item
            items.append((-(item.get("priority", 0)), pq._counter, item))
            pq._counter += 1
        # Re-push non-matching items
        for item in items:
            heapq.heappush(pq._queue, item)
        return None

    # ── Error store accessor ──────────────────────────────────────────────

    def get_errors(self, task_id: str) -> List[ReducerError]:
        return self._error_store.get_errors(task_id)

    def error_audit_report(self) -> List[Dict[str, Any]]:
        return self._error_store.audit_report()


# ── Transition table ────────────────────────────────────────────────────────

def _is_valid_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """Deterministic transition table for the task reducer state machine."""
    allowed = {
        TaskState.SCHEDULED: {TaskState.PENDING},
        TaskState.PENDING:    {TaskState.RUNNING, TaskState.DEAD},
        TaskState.RUNNING:   {TaskState.COMPLETED, TaskState.PENDING, TaskState.DEAD},
        TaskState.COMPLETED: set(),          # Terminal — no further transitions
        TaskState.FAILED:    set(),          # Terminal — no further transitions
        TaskState.DEAD:      set(),          # Terminal — no further transitions
    }
    return to_state in allowed.get(from_state, set())
