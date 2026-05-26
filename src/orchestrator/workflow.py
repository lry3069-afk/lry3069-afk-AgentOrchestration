"""Workflow Manager — Defines and executes multi-step agent workflows."""

from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DuplicateAliasError(ValueError):
    """Raised when duplicate parameter aliases are detected in workflow API inputs."""

    def __init__(self, duplicates: List[str]):
        self.duplicates = duplicates
        super().__init__(
            f"Duplicate parameter aliases detected: {', '.join(duplicates)}. "
            "Each alias must be unique across all workflow parameters."
        )


class WorkflowParameter:
    """Defines a single input parameter for a workflow, with optional aliases."""

    def __init__(
        self,
        name: str,
        param_type: str = "string",
        required: bool = False,
        default: Any = None,
        aliases: Optional[List[str]] = None,
    ):
        self.name = name
        self.param_type = param_type
        self.required = required
        self.default = default
        self.aliases = aliases or []


class WorkflowInputSchema:
    """Schema for workflow input parameters with alias deduplication validation."""

    def __init__(self):
        self._parameters: Dict[str, WorkflowParameter] = {}

    def add_parameter(self, param: WorkflowParameter) -> None:
        self._parameters[param.name] = param

    def validate(self) -> None:
        """Validate workflow parameter aliases — rejects duplicate aliases.

        Iterates all parameters and their aliases, ensuring no alias appears
        more than once (including as another parameter's primary name).

        Raises:
            DuplicateAliasError: If any duplicate alias is found.
        """
        seen: Dict[str, str] = {}
        duplicates: Set[str] = set()

        # Check primary parameter names first
        for param_name in self._parameters:
            if param_name in seen:
                duplicates.add(param_name)
            else:
                seen[param_name] = param_name

        # Check all aliases against each other and against primary names
        for param_name, param in self._parameters.items():
            for alias in param.aliases:
                if alias in seen and seen[alias] != param_name:
                    duplicates.add(alias)
                elif alias == param_name:
                    duplicates.add(alias)
                else:
                    seen[alias] = param_name

        if duplicates:
            raise DuplicateAliasError(sorted(duplicates))

    def to_dict(self) -> Dict:
        return {
            name: {
                "type": p.param_type,
                "required": p.required,
                "default": p.default,
                "aliases": p.aliases,
            }
            for name, p in self._parameters.items()
        }

    @property
    def parameters(self) -> Dict[str, WorkflowParameter]:
        return self._parameters


class WorkflowStep:
    def __init__(self, name: str, handler: Callable, retries: int = 0, timeout: int = 300):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.retries = retries
        self.timeout = timeout
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None


class Workflow:
    def __init__(self, name: str, description: str = ""):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self.status = StepStatus.PENDING
        self.input_schema: Optional[WorkflowInputSchema] = None

    def add_step(self, step: WorkflowStep) -> "Workflow":
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)

    def set_input_schema(self, schema: WorkflowInputSchema) -> "Workflow":
        schema.validate()
        self.input_schema = schema
        return self


class WorkflowManager:
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}

    def create_workflow(self, name: str, description: str = "") -> Workflow:
        workflow = Workflow(name, description)
        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def register_workflow(self, workflow: Workflow) -> str:
        """Register a workflow with validation before binding.

        Validation runs against the workflow's input_schema (if present)
        to reject duplicate parameter aliases before the workflow can be
        dispatched or executed.

        Raises:
            DuplicateAliasError: If duplicate parameter aliases are detected.
        """
        if workflow.input_schema is not None:
            workflow.input_schema.validate()
        self._workflows[workflow.id] = workflow
        return workflow.id

    def execute_workflow(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        # Enforce validation before execution
        if workflow.input_schema is not None:
            workflow.input_schema.validate()

        workflow.status = StepStatus.RUNNING
        for step in workflow.steps:
            step.status = StepStatus.RUNNING
            try:
                result = step.handler()
                step.result = result
                step.status = StepStatus.COMPLETED
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                workflow.status = StepStatus.FAILED
                return False

        workflow.status = StepStatus.COMPLETED
        return True

# 2019-03-27T19:58:07 update

# 2019-05-09T09:42:56 update

# 2019-12-03T10:07:42 update

# 2020-01-16T18:43:28 update

# 2020-03-20T10:40:15 update

# 2020-04-17T15:36:50 update

# 2020-05-04T14:44:01 update

# 2020-06-16T13:17:31 update

# 2020-08-05T17:00:24 update

# 2020-09-04T08:29:23 update

# 2020-09-09T17:52:02 update

# 2020-10-23T10:57:44 update

# 2020-12-05T20:55:47 update

# 2021-01-15T19:23:40 update

# 2021-02-03T20:43:12 update

# 2021-03-16T12:26:47 update

# 2021-04-20T14:33:28 update

# 2021-10-14T15:03:32 update

# 2021-10-21T17:24:55 update

# 2021-11-16T17:01:08 update

# 2021-11-22T09:51:21 update

# 2021-12-21T16:15:47 update

# 2022-03-23T16:52:27 update

# 2022-12-21T09:25:50 update

# 2023-01-09T09:55:25 update

# 2023-01-13T11:06:15 update

# 2023-01-26T11:00:59 update

# 2023-02-23T08:56:54 update

# 2023-05-17T08:07:16 update

# 2023-06-06T17:09:34 update

# 2023-06-13T10:35:28 update

# 2023-08-24T20:36:06 update

# 2023-10-30T19:10:13 update

# 2024-01-02T08:27:25 update

# 2024-01-24T12:13:15 update

# 2024-02-08T13:35:49 update

# 2024-05-07T16:09:24 update

# 2024-05-11T09:48:46 update

# 2024-05-21T19:25:41 update

# 2024-06-05T12:00:30 update

# 2024-06-25T09:40:26 update

# 2024-09-17T13:49:39 update

# 2024-10-14T17:39:35 update

# 2024-11-27T20:14:35 update

# 2024-12-25T19:31:41 update

# 2025-01-16T13:15:09 update

# 2025-02-05T14:06:59 update

# 2025-02-17T20:55:11 update

# 2025-04-30T19:36:53 update

# 2025-07-17T10:14:40 update

# 2025-08-29T12:13:15 update

# 2025-09-03T13:51:11 update

# 2025-09-19T16:08:24 update

# 2025-11-27T08:38:12 update

# 2026-01-27T13:23:38 update

# 2026-01-28T11:22:50 update
