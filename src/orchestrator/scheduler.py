"""Task Scheduler — Priority-based task queuing and dispatch."""

import asyncio
import heapq
import logging
import time
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Sentinel value for "no budget limit" on a priority class
UNLIMITED = -1


class FairnessBudgetViolation(Exception):
    """Raised when a task would violate fairness budget constraints."""
    pass


class StaleStateTransition(Exception):
    """Raised when an invalid or stale state transition is attempted."""
    pass


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


class TaskScheduler:
    def __init__(self, default_budget: int = 10, max_retries: int = 3):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, float] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._max_retries = max_retries
        # Fairness budgets: priority_class -> (used_slots, max_slots)
        self._fairness_budgets: Dict[str, tuple] = {}
        # Budget limits per priority class (None = use default_budget)
        self._budget_limits: Dict[str, int] = {}
        self._default_budget = default_budget
        # Tracks valid state transitions: agent_id -> last_known_state
        self._agent_state_cache: Dict[str, str] = {}
        # Liveness fence: reject stale transitions older than this (seconds)
        self._state_fence_seconds = 300

    def set_budget_limit(self, priority_class: str, limit: int) -> None:
        """Set max fairness budget slots for a priority class. Pass UNLIMITED for no cap."""
        self._budget_limits[priority_class] = limit

    def _get_budget(self, priority_class: str) -> tuple:
        """Return (used, max) slots for a priority class."""
        if priority_class not in self._fairness_budgets:
            limit = self._budget_limits.get(priority_class, self._default_budget)
            self._fairness_budgets[priority_class] = (0, limit)
        return self._fairness_budgets[priority_class]

    def _consume_budget(self, priority_class: str) -> None:
        """Atomically consume one slot from a priority class budget."""
        used, max_slots = self._get_budget(priority_class)
        if max_slots != UNLIMITED and used >= max_slots:
            logger.warning(
                f"Fairness budget exhausted for priority_class={priority_class} "
                f"(used={used}, max={max_slots})"
            )
            raise FairnessBudgetViolation(
                f"Budget exhausted for priority class '{priority_class}'"
            )
        self._fairness_budgets[priority_class] = (used + 1, max_slots)

    def _release_budget(self, priority_class: str) -> None:
        """Release one slot back to a priority class budget."""
        if priority_class not in self._fairness_budgets:
            return
        used, max_slots = self._fairness_budgets[priority_class]
        self._fairness_budgets[priority_class] = (max(0, used - 1), max_slots)

    def _validate_state_transition(self, task: Dict) -> None:
        """
        Validate state transition preconditions for urgent workflow lanes.
        Raises StaleStateTransition if the transition is stale, duplicate, or policy-violating.
        """
        agent_id = task.get("target_agent")
        priority_class = task.get("priority_class", "default")
        expected_state = task.get("expected_agent_state")
        task_id = task.get("id", "unknown")

        # Check for duplicate in-flight task for the same agent
        for in_flight_task in self._in_flight.values():
            if (
                in_flight_task.get("target_agent") == agent_id
                and in_flight_task["id"] != task_id
                and in_flight_task.get("priority_class") == priority_class
            ):
                logger.warning(
                    f"Duplicate in-flight task for agent={agent_id} "
                    f"priority_class={priority_class}, task_id={task_id}"
                )
                raise StaleStateTransition(
                    f"Duplicate task for agent={agent_id} in priority_class={priority_class}"
                )

        # Validate agent state cache for urgent lanes
        if priority_class == "urgent" and agent_id:
            cached_state = self._agent_state_cache.get(agent_id)
            if cached_state and expected_state and cached_state != expected_state:
                age = time.time() - task.get("enqueued_at", time.time())
                if age > self._state_fence_seconds:
                    logger.warning(
                        f"Stale transition rejected: agent={agent_id} "
                        f"cached={cached_state} expected={expected_state} age={age:.1f}s"
                    )
                    raise StaleStateTransition(
                        f"Stale state transition for agent={agent_id}: "
                        f"expected {expected_state}, cached {cached_state} (age={age:.1f}s)"
                    )
            # Update cache with expected state
            if expected_state:
                self._agent_state_cache[agent_id] = expected_state

    def update_agent_state(self, agent_id: str, state: str) -> None:
        """Update the cached state for an agent. Call this when agent lifecycle changes."""
        self._agent_state_cache[agent_id] = state

    def enqueue(self, task: Dict, queue: str = "default", priority: int = 0,
                priority_class: str = "default") -> str:
        """Enqueue a task. Raises FairnessBudgetViolation if budget is exhausted."""
        is_retry = "id" in task  # already had an id -> re-enqueue after fail
        if not is_retry:
            task_id = str(uuid4())
            task["id"] = task_id
            task.setdefault("enqueued_at", time.time())
            task["retries"] = 0
        task["priority_class"] = priority_class
        task["queue"] = queue

        # Enforce fairness budget before accepting (skip for retries — budget already consumed)
        if not is_retry:
            self._consume_budget(priority_class)

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        return task["id"]

    def schedule(self, task: Dict, delay: float, queue: str = "default", priority: int = 0) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        self._scheduled[task_id] = time.time() + delay
        return task_id

    async def dequeue(self, queue: str = "default", timeout: float = 1.0) -> Optional[Dict]:
        """
        Async dequeue with fairness budget enforcement and atomic state preconditions.
        Raises FairnessBudgetViolation or StaleStateTransition on policy violations.
        """
        now = time.time()
        expired = [tid for tid, t in self._scheduled.items() if t <= now]
        for tid in expired:
            task = self._scheduled.pop(tid)
            if task:
                try:
                    self.enqueue(task, queue,
                                 priority=task.get("priority", 0),
                                 priority_class=task.get("priority_class", "default"))
                except FairnessBudgetViolation:
                    pass  # skip expired tasks whose budget is exhausted

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                self._validate_state_transition(task)
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            self._release_budget(task.get("priority_class", "default"))
        return True
        return task is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            priority_class = task.get("priority_class", "default")
            task["retries"] += 1
            self._release_budget(priority_class)
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue,
                             priority=task.get("priority", 0),
                             priority_class=priority_class)
                return True
        return False

# 2019-04-25T08:37:12 update

# 2019-06-04T16:40:00 update

# 2019-07-11T12:01:28 update

# 2019-08-02T12:20:21 update

# 2019-08-23T10:38:50 update

# 2019-10-31T13:55:52 update

# 2019-11-04T20:12:32 update

# 2019-12-13T12:22:36 update

# 2020-02-01T10:32:37 update

# 2020-02-26T09:44:38 update

# 2020-03-09T19:00:55 update

# 2020-05-01T18:40:34 update

# 2020-05-12T15:10:31 update

# 2020-06-30T13:24:19 update

# 2020-09-22T16:00:45 update

# 2020-10-20T10:52:48 update

# 2020-10-21T12:18:08 update

# 2020-11-06T12:35:01 update

# 2020-12-09T08:09:33 update

# 2021-01-07T08:20:36 update

# 2021-10-02T15:23:16 update

# 2021-10-06T16:14:57 update

# 2021-10-06T09:27:41 update

# 2021-11-19T08:37:40 update

# 2022-03-01T16:39:54 update

# 2022-05-26T13:43:07 update

# 2022-06-02T10:50:58 update

# 2022-06-14T10:46:48 update

# 2022-07-31T16:44:34 update

# 2022-08-30T18:20:12 update

# 2022-11-04T14:47:03 update

# 2022-12-06T10:36:49 update

# 2022-12-22T13:21:12 update

# 2022-12-26T12:24:50 update

# 2023-03-09T08:09:55 update

# 2023-05-01T10:07:37 update

# 2023-06-08T14:32:15 update

# 2023-07-14T17:24:18 update

# 2023-12-14T08:38:31 update

# 2024-02-20T13:43:58 update

# 2024-03-24T08:52:42 update

# 2024-03-28T15:27:17 update

# 2024-03-29T18:10:33 update

# 2024-04-15T20:18:31 update

# 2024-05-27T13:11:52 update

# 2024-05-27T16:42:56 update

# 2024-06-20T13:03:45 update

# 2024-06-28T12:32:58 update

# 2024-07-10T14:10:16 update

# 2024-07-26T14:18:59 update

# 2024-08-12T08:21:05 update

# 2024-08-21T16:58:40 update

# 2024-09-27T19:54:30 update

# 2024-10-21T13:47:42 update

# 2024-11-11T09:19:27 update

# 2024-12-24T08:23:41 update

# 2025-02-14T10:35:15 update

# 2025-03-31T18:09:40 update

# 2025-06-21T17:32:49 update

# 2025-07-21T16:52:28 update

# 2025-08-20T19:45:16 update

# 2025-11-04T18:54:24 update

# 2025-12-09T20:17:36 update

# 2026-01-12T15:42:32 update

# 2026-01-23T14:41:20 update

# 2026-03-18T14:43:07 update

# 2026-04-13T11:43:19 update
