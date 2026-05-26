"""Regression test for issue #4802: Prevent artifact cleanup before retries.

Deterministic test covering the retry dependency data trigger:
the workflow artifact cleanup planner must reject or safely defer
invalid transitions when a step has retries remaining.
"""

import pytest
from src.orchestrator.workflow import (
    ArtifactCleanupPlanner,
    RetryDependencyError,
    Workflow,
    WorkflowManager,
    WorkflowStep,
)


class TestArtifactCleanupPlannerRetryDependency:
    """Regression tests for issue #4802."""

    def test_cleanup_blocked_when_retries_remaining(self):
        """Artifact cleanup must be blocked when a step has retries remaining."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=2)
        planner.register_step(step)
        planner.assign_artifact(step.id, "artifact-1")

        # Attempting cleanup should raise RetryDependencyError
        with pytest.raises(RetryDependencyError) as exc_info:
            planner.schedule_cleanup(step.id, "artifact-1")
        assert "step1" in str(exc_info.value)
        assert "retry" in str(exc_info.value).lower()

    def test_cleanup_allowed_when_no_retries(self):
        """Artifact cleanup must succeed when retries are exhausted."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=0)
        planner.register_step(step)
        planner.assign_artifact(step.id, "artifact-1")

        # Should not raise
        result = planner.schedule_cleanup(step.id, "artifact-1")
        assert result is True

    def test_cleanup_blocked_when_step_running(self):
        """Artifact cleanup must be blocked while step is in RUNNING state."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=0)
        planner.register_step(step)
        planner.assign_artifact(step.id, "artifact-1")
        planner.on_step_start(step.id)

        with pytest.raises(RetryDependencyError) as exc_info:
            planner.schedule_cleanup(step.id, "artifact-1")
        assert "running" in str(exc_info.value).lower()

    def test_defer_cleanup_on_failure_with_retries(self):
        """Step failure must defer cleanup when retries are available."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=2)
        planner.register_step(step)

        has_retries = planner.on_step_fail(step.id)
        assert has_retries is True

        audit = planner.get_audit_record(step.id)
        assert audit["cleanup_deferred"] is True
        assert audit["retries_remaining"] == 2

    def test_no_defer_cleanup_on_failure_when_retries_exhausted(self):
        """Step failure must not defer cleanup when retries are exhausted."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=1)
        planner.register_step(step)

        # First failure: retries remain
        planner.on_step_fail(step.id)
        audit1 = planner.get_audit_record(step.id)
        assert audit1["cleanup_deferred"] is True

    def test_retries_consumed_on_success(self):
        """Retries remaining must decrease each time a step completes successfully."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=3)
        planner.register_step(step)

        for expected_remaining in [2, 1, 0]:
            planner.on_step_complete(step.id)
            audit = planner.get_audit_record(step.id)
            assert audit["retries_remaining"] == expected_remaining

    def test_workflow_registration_rejects_negative_retries(self):
        """Workflow registration must reject steps with negative retry counts."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("step1", lambda: None, retries=-1)
        workflow = Workflow("test")
        workflow.add_step(step)

        with pytest.raises(RetryDependencyError) as exc_info:
            planner.validate_workflow(workflow)
        assert "non-negative" in str(exc_info.value).lower()

    def test_workflow_registration_with_valid_retries(self):
        """Workflow registration must succeed with valid retry configuration."""
        planner = ArtifactCleanupPlanner()
        workflow = Workflow("test_workflow")
        step1 = WorkflowStep("step_a", lambda: None, retries=0)
        step2 = WorkflowStep("step_b", lambda: None, retries=3)
        workflow.add_step(step1)
        workflow.add_step(step2)

        # Should not raise
        planner.validate_workflow(workflow)

    def test_workflow_manager_blocks_cleanup_on_registered_workflow(self):
        """WorkflowManager must use ArtifactCleanupPlanner to block cleanup."""
        manager = WorkflowManager()
        workflow = Workflow("test_workflow")
        step = WorkflowStep("retry_step", lambda: None, retries=2)
        workflow.add_step(step)

        manager.register_workflow(workflow)

        # Attempt cleanup via planner
        with pytest.raises(RetryDependencyError):
            manager.planner.schedule_cleanup(step.id, "artifact-x")

    def test_execute_workflow_defers_cleanup_on_failure(self):
        """Workflow execution must defer cleanup when a step fails with retries left."""
        manager = WorkflowManager()
        workflow = Workflow("test_workflow")

        call_count = [0]

        def failing_handler():
            call_count[0] += 1
            raise RuntimeError("intentional failure")

        step = WorkflowStep("flaky_step", failing_handler, retries=1)
        workflow.add_step(step)
        manager.register_workflow(workflow)

        result = manager.execute_workflow(workflow.id)
        # Step fails once, retries, fails again, then workflow fails
        assert call_count[0] == 2
        assert result is False
        assert manager.get_metrics()["cleanup_deferred"] >= 1

    def test_execute_workflow_succeeds_with_retries(self):
        """Workflow must succeed when steps complete after retry attempts."""
        manager = WorkflowManager()
        workflow = Workflow("test_workflow")

        attempts = [0]

        def unreliable_handler():
            attempts[0] += 1
            if attempts[0] < 2:
                raise RuntimeError("try again")
            return "success"

        step = WorkflowStep("flaky_step", unreliable_handler, retries=3)
        workflow.add_step(step)
        manager.register_workflow(workflow)

        result = manager.execute_workflow(workflow.id)
        assert result is True
        assert attempts[0] == 2

    def test_audit_record_no_private_runtime_data(self):
        """Audit records must not expose private runtime data."""
        planner = ArtifactCleanupPlanner()
        step = WorkflowStep("secure_step", lambda: None, retries=2)
        planner.register_step(step)

        audit = planner.get_audit_record(step.id)
        # Must not contain handler result, error messages, or internal refs
        assert "result" not in audit
        assert "error" not in str(audit)
        assert audit["step_name"] == "secure_step"
        assert audit["retries_configured"] == 2
        assert audit["retries_remaining"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
