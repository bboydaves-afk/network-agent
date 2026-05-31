"""Exception hierarchy for the automation / runbook engine."""

from __future__ import annotations

from core.exceptions import NetworkAgentError


class AutomationError(NetworkAgentError):
    """Base exception for all automation-related errors."""

    def __init__(self, message: str = "An automation error occurred") -> None:
        super().__init__(message)


class RunbookError(AutomationError):
    """Error tied to a specific runbook."""

    def __init__(
        self,
        message: str = "Runbook error",
        runbook_name: str | None = None,
    ) -> None:
        self.runbook_name = runbook_name
        if runbook_name:
            detail = f"[runbook={runbook_name}] {message}"
        else:
            detail = message
        super().__init__(detail)


class RunbookValidationError(RunbookError):
    """YAML schema or structural validation failure when loading a runbook."""

    def __init__(
        self,
        message: str = "Runbook validation failed",
        runbook_name: str | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self.errors = errors or []
        if self.errors:
            detail = f"{message}: {'; '.join(self.errors)}"
        else:
            detail = message
        super().__init__(detail, runbook_name=runbook_name)


class RunbookExecutionError(RunbookError):
    """Runtime failure during runbook execution."""

    def __init__(
        self,
        message: str = "Runbook execution failed",
        runbook_name: str | None = None,
        execution_id: str | None = None,
    ) -> None:
        self.execution_id = execution_id
        if execution_id:
            detail = f"{message} (execution_id={execution_id})"
        else:
            detail = message
        super().__init__(detail, runbook_name=runbook_name)


class RunbookCooldownError(RunbookError):
    """Raised when a runbook execution is suppressed by cooldown."""

    def __init__(
        self,
        message: str = "Runbook execution suppressed by cooldown",
        runbook_name: str | None = None,
        remaining_seconds: float | None = None,
    ) -> None:
        self.remaining_seconds = remaining_seconds
        if remaining_seconds is not None:
            detail = f"{message} ({remaining_seconds:.0f}s remaining)"
        else:
            detail = message
        super().__init__(detail, runbook_name=runbook_name)


class ActionError(AutomationError):
    """Failure while executing a single action step."""

    def __init__(
        self,
        message: str = "Action execution failed",
        action_name: str | None = None,
    ) -> None:
        self.action_name = action_name
        if action_name:
            detail = f"[action={action_name}] {message}"
        else:
            detail = message
        super().__init__(detail)
