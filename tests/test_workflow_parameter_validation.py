"""Test for duplicate parameter aliases validation in workflow API inputs.

Regression test for issue #4644: Reject duplicate parameter aliases — workflow API inputs.
"""

import pytest
from src.orchestrator.workflow import (
    Workflow,
    WorkflowInputSchema,
    WorkflowParameter,
    WorkflowManager,
    DuplicateAliasError,
)


class TestDuplicateParameterAliases:
    """Regression tests for issue #4644."""

    def test_duplicate_aliases_across_parameters_should_be_rejected(self):
        """Two parameters sharing the same alias must raise DuplicateAliasError."""
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("input_file", aliases=["file", "source"]))
        schema.add_parameter(WorkflowParameter("output_file", aliases=["file", "dest"]))

        with pytest.raises(DuplicateAliasError) as exc_info:
            schema.validate()
        assert "file" in str(exc_info.value)

    def test_alias_matching_another_parameter_name_should_be_rejected(self):
        """An alias that matches another parameter's primary name must be rejected."""
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("source", aliases=["src"]))
        schema.add_parameter(WorkflowParameter("input", aliases=["source"]))

        with pytest.raises(DuplicateAliasError) as exc_info:
            schema.validate()
        assert "source" in str(exc_info.value)

    def test_duplicate_aliases_within_same_parameter_should_be_rejected(self):
        """An alias that matches its own parameter name must be rejected."""
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("name", aliases=["name"]))

        with pytest.raises(DuplicateAliasError) as exc_info:
            schema.validate()
        assert "name" in str(exc_info.value)

    def test_unique_aliases_should_pass_validation(self):
        """Parameters with unique aliases must pass validation."""
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("input_file", aliases=["file", "source"]))
        schema.add_parameter(WorkflowParameter("output_file", aliases=["dest", "target"]))
        schema.add_parameter(WorkflowParameter("mode", aliases=["m"]))

        # Should not raise
        schema.validate()

    def test_no_aliases_should_pass_validation(self):
        """Parameters without any aliases must pass validation."""
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("a"))
        schema.add_parameter(WorkflowParameter("b"))
        schema.add_parameter(WorkflowParameter("c"))

        schema.validate()

    def test_workflow_set_input_schema_rejects_duplicates(self):
        """Workflow.set_input_schema must reject duplicate aliases at binding time."""
        workflow = Workflow("test_workflow")
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("x", aliases=["dup"]))
        schema.add_parameter(WorkflowParameter("y", aliases=["dup"]))

        with pytest.raises(DuplicateAliasError):
            workflow.set_input_schema(schema)

    def test_workflow_manager_register_rejects_duplicates(self):
        """WorkflowManager.register_workflow must reject duplicate aliases."""
        manager = WorkflowManager()
        workflow = Workflow("test_workflow")
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("x", aliases=["dup"]))
        schema.add_parameter(WorkflowParameter("y", aliases=["dup"]))
        workflow.input_schema = schema  # bypass set_input_schema to test register

        with pytest.raises(DuplicateAliasError):
            manager.register_workflow(workflow)

    def test_workflow_manager_execute_rejects_duplicates(self):
        """WorkflowManager.execute_workflow must reject duplicate aliases before execution."""
        manager = WorkflowManager()
        workflow = Workflow("test_workflow")
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("x", aliases=["dup"]))
        schema.add_parameter(WorkflowParameter("y", aliases=["dup"]))
        workflow.input_schema = schema
        manager._workflows[workflow.id] = workflow  # bypass register

        with pytest.raises(DuplicateAliasError):
            manager.execute_workflow(workflow.id)

    def test_workflow_with_valid_schema_executes(self):
        """Workflow with valid schema must execute successfully."""
        manager = WorkflowManager()
        workflow = Workflow("valid_workflow")
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("input", aliases=["i"]))
        schema.add_parameter(WorkflowParameter("output", aliases=["o"]))
        workflow.set_input_schema(schema)

        executed = []
        workflow.add_step(
            __import__("src.orchestrator.workflow", fromlist=["WorkflowStep"]).WorkflowStep(
                "step1", lambda: executed.append(True)
            )
        )

        manager.register_workflow(workflow)
        result = manager.execute_workflow(workflow.id)
        assert result is True
        assert len(executed) == 1

    def test_multiple_duplicates_reported(self):
        """All duplicate aliases must be reported in the error message."""
        schema = WorkflowInputSchema()
        schema.add_parameter(WorkflowParameter("a", aliases=["dup1", "dup2"]))
        schema.add_parameter(WorkflowParameter("b", aliases=["dup1", "dup3"]))
        schema.add_parameter(WorkflowParameter("c", aliases=["dup2", "dup3"]))

        with pytest.raises(DuplicateAliasError) as exc_info:
            schema.validate()
        msg = str(exc_info.value)
        assert "dup1" in msg
        assert "dup2" in msg
        assert "dup3" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])