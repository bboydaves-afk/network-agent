"""Custom exception hierarchy for Network Agent."""

from __future__ import annotations


class NetworkAgentError(Exception):
    """Base exception for all Network Agent errors."""

    def __init__(self, message: str = "An unexpected Network Agent error occurred") -> None:
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Device-related exceptions
# ---------------------------------------------------------------------------

class DeviceConnectionError(NetworkAgentError):
    """Raised when a connection to a network device fails."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Failed to connect to device",
    ) -> None:
        self.device_id = device_id
        detail = f"[device={device_id}] {message}" if device_id else message
        super().__init__(detail)


class DeviceAuthenticationError(NetworkAgentError):
    """Raised when authentication to a network device fails."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Authentication failed for device",
    ) -> None:
        self.device_id = device_id
        detail = f"[device={device_id}] {message}" if device_id else message
        super().__init__(detail)


class DeviceTimeoutError(NetworkAgentError):
    """Raised when a device operation times out."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Operation timed out for device",
        timeout_seconds: float | None = None,
    ) -> None:
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds
        parts: list[str] = []
        if device_id:
            parts.append(f"[device={device_id}]")
        parts.append(message)
        if timeout_seconds is not None:
            parts.append(f"(timeout={timeout_seconds}s)")
        super().__init__(" ".join(parts))


class SerialConnectionError(DeviceConnectionError):
    """Raised when a serial console connection fails."""

    def __init__(
        self,
        device_id: str | None = None,
        serial_port: str = "",
        message: str = "Serial connection failed",
    ) -> None:
        self.serial_port = serial_port
        detail = f"{message} (port={serial_port})" if serial_port else message
        super().__init__(device_id=device_id, message=detail)


class DeviceCommandError(NetworkAgentError):
    """Raised when a command executed on a device returns an error."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Command execution failed on device",
        command: str | None = None,
    ) -> None:
        self.device_id = device_id
        self.command = command
        parts: list[str] = []
        if device_id:
            parts.append(f"[device={device_id}]")
        parts.append(message)
        if command:
            parts.append(f"(command={command!r})")
        super().__init__(" ".join(parts))


# ---------------------------------------------------------------------------
# Configuration-related exceptions
# ---------------------------------------------------------------------------

class ConfigBackupError(NetworkAgentError):
    """Raised when a configuration backup operation fails."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Configuration backup failed",
    ) -> None:
        self.device_id = device_id
        detail = f"[device={device_id}] {message}" if device_id else message
        super().__init__(detail)


class ConfigDeployError(NetworkAgentError):
    """Raised when a configuration deployment operation fails."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Configuration deployment failed",
    ) -> None:
        self.device_id = device_id
        detail = f"[device={device_id}] {message}" if device_id else message
        super().__init__(detail)


class ConfigRollbackError(NetworkAgentError):
    """Raised when a configuration rollback operation fails."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Configuration rollback failed",
    ) -> None:
        self.device_id = device_id
        detail = f"[device={device_id}] {message}" if device_id else message
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Discovery, monitoring, alerting exceptions
# ---------------------------------------------------------------------------

class DiscoveryError(NetworkAgentError):
    """Raised when network discovery operations fail."""

    def __init__(self, message: str = "Network discovery failed") -> None:
        super().__init__(message)


class MonitoringError(NetworkAgentError):
    """Raised when a monitoring operation fails."""

    def __init__(
        self,
        device_id: str | None = None,
        message: str = "Monitoring operation failed",
    ) -> None:
        self.device_id = device_id
        detail = f"[device={device_id}] {message}" if device_id else message
        super().__init__(detail)


class AlertError(NetworkAgentError):
    """Raised when alert processing or notification delivery fails."""

    def __init__(self, message: str = "Alert processing failed") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Infrastructure exceptions
# ---------------------------------------------------------------------------

class CredentialError(NetworkAgentError):
    """Raised when credential storage, retrieval, or decryption fails."""

    def __init__(self, message: str = "Credential operation failed") -> None:
        super().__init__(message)


class DatabaseError(NetworkAgentError):
    """Raised when a database operation fails."""

    def __init__(self, message: str = "Database operation failed") -> None:
        super().__init__(message)


class AutomationError(NetworkAgentError):
    """Base exception for automation operations."""
    def __init__(self, message: str = "Automation operation failed") -> None:
        super().__init__(message=message)


class RunbookError(AutomationError):
    """Error related to a specific runbook."""
    def __init__(self, runbook_name: str = "", message: str = "Runbook error") -> None:
        self.runbook_name = runbook_name
        detail = f"[runbook={runbook_name}] {message}" if runbook_name else message
        super().__init__(message=detail)


class RunbookValidationError(RunbookError):
    """Runbook YAML schema validation failure."""
    pass


class RunbookExecutionError(RunbookError):
    """Runtime execution failure in a runbook."""
    def __init__(self, runbook_name: str = "", execution_id: str = "", message: str = "Execution error") -> None:
        self.execution_id = execution_id
        detail = f"{message} (execution={execution_id})" if execution_id else message
        super().__init__(runbook_name=runbook_name, message=detail)


class RunbookCooldownError(RunbookError):
    """Runbook execution suppressed by cooldown."""
    pass


class ActionError(AutomationError):
    """Error executing a specific action within a runbook."""
    def __init__(self, action_name: str = "", message: str = "Action execution failed") -> None:
        self.action_name = action_name
        detail = f"[action={action_name}] {message}" if action_name else message
        super().__init__(message=detail)
