"""CLI commands for runbook automation management."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markup import escape

from interfaces.cli.app import run_async, get_db

console = Console()
err_console = Console(stderr=True)

automation_app = typer.Typer(
    name="automation",
    help="Runbook automation management",
    rich_markup_mode="rich",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_automation_engine():
    """Resolve the automation engine from the application context.

    Imports are deferred so the CLI can load quickly and the engine is
    only instantiated when an automation command is actually invoked.
    """
    from automation.engine import AutomationEngine
    from core.database import Database
    import os
    import yaml

    db = await get_db()
    # Try to get the singleton engine if one is already running, otherwise
    # create a lightweight instance just for the CLI query.
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config.yaml",
    )
    try:
        with open(config_path) as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        cfg = {}

    engine = AutomationEngine(db=db, config=cfg.get("automation", {}))
    await engine.start()
    return engine


async def _get_scheduler_manager():
    """Resolve the scheduler manager."""
    from automation.scheduler import SchedulerManager
    engine = await _get_automation_engine()
    return getattr(engine, "scheduler", None)


async def _get_audit_logger():
    """Resolve the audit logger."""
    from automation.audit import AuditLogger
    db = await get_db()
    return AuditLogger(db=db)


def _status_style(status: str) -> str:
    """Return a Rich markup colour for a given status string."""
    status_lower = (status or "unknown").lower()
    colour_map = {
        "enabled": "green",
        "disabled": "red",
        "completed": "green",
        "success": "green",
        "running": "cyan",
        "pending": "yellow",
        "failed": "red",
        "escalated": "magenta",
        "active": "green",
        "paused": "yellow",
    }
    colour = colour_map.get(status_lower, "dim")
    return f"[{colour}]{escape(status)}[/{colour}]"


def _truncate(text: str, length: int = 8) -> str:
    """Truncate a string, adding ellipsis if needed."""
    if not text:
        return "--"
    if len(text) <= length:
        return text
    return text[:length] + "..."


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@automation_app.command("list")
def list_runbooks(
    enabled_only: bool = typer.Option(False, "--enabled-only", help="Show only enabled runbooks"),
) -> None:
    """List all configured automation runbooks."""

    async def _run():
        engine = await _get_automation_engine()
        return engine.list_runbooks()

    runbooks = run_async(_run())

    if enabled_only:
        runbooks = [rb for rb in runbooks if rb.enabled]

    if not runbooks:
        console.print("[dim]No runbooks found.[/dim]")
        return

    table = Table(title="Automation Runbooks", border_style="blue")
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Trigger Type", justify="center")
    table.add_column("Description")
    table.add_column("Actions", justify="right")
    table.add_column("Tags")

    for rb in runbooks:
        status_str = _status_style("enabled" if rb.enabled else "disabled")
        tags_str = ", ".join(rb.tags) if rb.tags else "--"
        table.add_row(
            rb.name,
            status_str,
            rb.trigger_type or "--",
            (rb.description or "--")[:60],
            str(len(rb.actions)),
            tags_str,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(runbooks)} runbook(s)[/dim]")


@automation_app.command("show")
def show_runbook(
    name: str = typer.Argument(..., help="Runbook name"),
) -> None:
    """Show detailed runbook configuration."""

    async def _run():
        engine = await _get_automation_engine()
        return engine.get_runbook(name)

    rb = run_async(_run())

    if not rb:
        err_console.print(f"[red]Runbook '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    # Build detail text
    lines = []
    lines.append(f"[bold]Name:[/bold]        {escape(rb.name)}")
    lines.append(f"[bold]Version:[/bold]     {rb.version}")
    lines.append(f"[bold]Description:[/bold] {escape(rb.description or '--')}")
    lines.append(f"[bold]Enabled:[/bold]     {_status_style('enabled' if rb.enabled else 'disabled')}")
    lines.append(f"[bold]Tags:[/bold]        {', '.join(rb.tags) if rb.tags else '--'}")
    lines.append("")

    # Trigger config
    lines.append("[bold underline]Trigger Configuration[/bold underline]")
    lines.append(f"  Type:     {rb.trigger_type or '--'}")
    if rb.trigger_alert_match:
        lines.append(f"  Alert Match: {json.dumps(rb.trigger_alert_match, default=str)}")
    if rb.trigger_schedule_cron:
        lines.append(f"  Cron:     {rb.trigger_schedule_cron}")
        if rb.trigger_schedule_timezone:
            lines.append(f"  Timezone: {rb.trigger_schedule_timezone}")
    if rb.trigger_webhook_match:
        lines.append(f"  Webhook Match: {json.dumps(rb.trigger_webhook_match, default=str)}")
    lines.append("")

    # Cooldown and limits
    lines.append("[bold underline]Cooldown & Limits[/bold underline]")
    lines.append(f"  Cooldown: {rb.cooldown}s" if rb.cooldown else "  Cooldown: none")
    if rb.limits:
        for key, val in rb.limits.items():
            lines.append(f"  {key}: {val}")
    else:
        lines.append("  Limits: none")
    lines.append("")

    # Conditions
    if rb.conditions:
        lines.append("[bold underline]Conditions[/bold underline]")
        for i, cond in enumerate(rb.conditions, 1):
            lines.append(f"  {i}. {json.dumps(cond, default=str)}")
        lines.append("")

    # Actions
    lines.append("[bold underline]Actions[/bold underline]")
    if rb.actions:
        for i, action in enumerate(rb.actions, 1):
            action_name = action.get("name", action.get("type", f"action_{i}"))
            action_type = action.get("type", "--")
            lines.append(f"  {i}. [cyan]{escape(action_name)}[/cyan] (type: {escape(action_type)})")
            if action.get("description"):
                lines.append(f"     {escape(action['description'])}")
            if action.get("device_id"):
                lines.append(f"     device: {escape(action['device_id'])}")
            if action.get("commands"):
                for cmd in action["commands"][:5]:
                    lines.append(f"     > {escape(str(cmd))}")
                if len(action.get("commands", [])) > 5:
                    lines.append(f"     ... and {len(action['commands']) - 5} more")
    else:
        lines.append("  (no actions)")
    lines.append("")

    # Escalation
    if rb.escalation:
        lines.append("[bold underline]Escalation[/bold underline]")
        for level in rb.escalation:
            level_name = level.get("level", level.get("name", "--"))
            notify = level.get("notify", level.get("channel", "--"))
            lines.append(f"  Level {level_name}: notify={notify}")
    else:
        lines.append("[bold underline]Escalation:[/bold underline] none")

    if rb.file_path:
        lines.append(f"\n[dim]File: {escape(str(rb.file_path))}[/dim]")

    panel_content = "\n".join(lines)
    console.print(Panel(
        panel_content,
        title=f"[bold]Runbook: {escape(rb.name)}[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))


@automation_app.command("run")
def run_runbook(
    name: str = typer.Argument(..., help="Runbook name to execute"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without executing"),
    device_id: Optional[str] = typer.Option(None, "--device-id", help="Override target device ID"),
) -> None:
    """Manually execute an automation runbook."""

    async def _run():
        engine = await _get_automation_engine()
        context = {"trigger": "manual", "trigger_type": "manual"}
        if device_id:
            context["device_id"] = device_id
        exec_id = await engine.execute_runbook(name, context=context, dry_run=dry_run)
        # Wait a moment and then fetch the execution result
        import asyncio
        await asyncio.sleep(1)
        execution = engine.get_execution(exec_id)
        return exec_id, execution

    with console.status("[cyan]Executing runbook...[/cyan]", spinner="dots"):
        exec_id, execution = run_async(_run())

    if execution is None:
        console.print(Panel(
            f"[yellow]Execution started[/yellow]\n"
            f"Execution ID: [bold]{exec_id}[/bold]\n"
            f"Runbook: {name}\n"
            f"Dry Run: {dry_run}\n\n"
            f"[dim]Use 'netagent automation show-exec {exec_id}' to check results.[/dim]",
            title="[bold]Execution Submitted[/bold]",
            border_style="yellow",
        ))
        return

    # Format execution results
    status = execution.get("status", "unknown") if isinstance(execution, dict) else getattr(execution, "status", "unknown")
    duration = execution.get("duration", "--") if isinstance(execution, dict) else getattr(execution, "duration", "--")
    actions_results = execution.get("action_results", []) if isinstance(execution, dict) else getattr(execution, "action_results", [])

    lines = []
    lines.append(f"[bold]Execution ID:[/bold] {exec_id}")
    lines.append(f"[bold]Runbook:[/bold]      {name}")
    lines.append(f"[bold]Status:[/bold]       {_status_style(str(status))}")
    lines.append(f"[bold]Duration:[/bold]     {duration}")
    lines.append(f"[bold]Dry Run:[/bold]      {dry_run}")
    lines.append("")

    if actions_results:
        lines.append("[bold underline]Action Results[/bold underline]")
        for i, ar in enumerate(actions_results, 1):
            if isinstance(ar, dict):
                ar_name = ar.get("name", ar.get("action", f"action_{i}"))
                ar_status = ar.get("status", "unknown")
                ar_output = ar.get("output", ar.get("result", ""))
                ar_duration = ar.get("duration", "--")
            else:
                ar_name = getattr(ar, "name", f"action_{i}")
                ar_status = getattr(ar, "status", "unknown")
                ar_output = getattr(ar, "output", "")
                ar_duration = getattr(ar, "duration", "--")

            lines.append(f"  {i}. [cyan]{escape(str(ar_name))}[/cyan]")
            lines.append(f"     Status:   {_status_style(str(ar_status))}")
            lines.append(f"     Duration: {ar_duration}")
            if ar_output:
                output_str = str(ar_output)[:200]
                lines.append(f"     Output:   {escape(output_str)}")
            lines.append("")

    border = "green" if str(status).lower() in ("completed", "success") else "red"
    console.print(Panel(
        "\n".join(lines),
        title="[bold]Execution Result[/bold]",
        border_style=border,
        padding=(1, 2),
    ))


@automation_app.command("enable")
def enable_runbook(
    name: str = typer.Argument(..., help="Runbook name"),
) -> None:
    """Enable an automation runbook."""

    async def _run():
        engine = await _get_automation_engine()
        await engine.enable_runbook(name)

    run_async(_run())
    console.print(f"[green]Runbook '{escape(name)}' enabled.[/green]")


@automation_app.command("disable")
def disable_runbook(
    name: str = typer.Argument(..., help="Runbook name"),
) -> None:
    """Disable an automation runbook."""

    async def _run():
        engine = await _get_automation_engine()
        await engine.disable_runbook(name)

    run_async(_run())
    console.print(f"[yellow]Runbook '{escape(name)}' disabled.[/yellow]")


@automation_app.command("reload")
def reload_runbooks() -> None:
    """Reload all runbooks from disk."""

    async def _run():
        engine = await _get_automation_engine()
        return await engine.reload_runbooks()

    count = run_async(_run())
    console.print(f"[green]Reloaded {count} runbook(s) from disk.[/green]")


@automation_app.command("history")
def execution_history(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum entries to show"),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
    runbook: Optional[str] = typer.Option(None, "--runbook", help="Filter by runbook name"),
) -> None:
    """Show runbook execution history."""

    async def _run():
        engine = await _get_automation_engine()
        return await engine.list_executions(
            limit=limit, status=status, runbook_name=runbook
        )

    executions = run_async(_run())

    if not executions:
        console.print("[dim]No executions found.[/dim]")
        return

    table = Table(title="Execution History", border_style="blue")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Runbook", style="bold cyan")
    table.add_column("Trigger", justify="center")
    table.add_column("Device", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Started At")

    for ex in executions:
        if isinstance(ex, dict):
            ex_id = ex.get("id", ex.get("execution_id", "--"))
            ex_runbook = ex.get("runbook_name", ex.get("runbook", "--"))
            ex_trigger = ex.get("trigger_type", ex.get("trigger", "--"))
            ex_device = ex.get("device_id", "--")
            ex_status = ex.get("status", "unknown")
            ex_duration = ex.get("duration", "--")
            ex_started = ex.get("started_at", ex.get("created_at", "--"))
        else:
            ex_id = getattr(ex, "id", getattr(ex, "execution_id", "--"))
            ex_runbook = getattr(ex, "runbook_name", getattr(ex, "runbook", "--"))
            ex_trigger = getattr(ex, "trigger_type", getattr(ex, "trigger", "--"))
            ex_device = getattr(ex, "device_id", "--")
            ex_status = getattr(ex, "status", "unknown")
            ex_duration = getattr(ex, "duration", "--")
            ex_started = getattr(ex, "started_at", getattr(ex, "created_at", "--"))

        table.add_row(
            _truncate(str(ex_id), 12),
            str(ex_runbook),
            str(ex_trigger),
            str(ex_device) if ex_device else "--",
            _status_style(str(ex_status)),
            str(ex_duration),
            str(ex_started),
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(executions)} execution(s)[/dim]")


@automation_app.command("show-exec")
def show_execution(
    exec_id: str = typer.Argument(..., help="Execution ID"),
) -> None:
    """Show detailed execution results."""

    async def _run():
        engine = await _get_automation_engine()
        execution = engine.get_execution(exec_id)
        if not execution:
            db = await get_db()
            execution = await db.get_runbook_execution(exec_id)
        return execution

    execution = run_async(_run())

    if not execution:
        err_console.print(f"[red]Execution '{exec_id}' not found.[/red]")
        raise typer.Exit(code=1)

    # Normalize access (dict or object)
    def _get(key, default="--"):
        if isinstance(execution, dict):
            return execution.get(key, default)
        return getattr(execution, key, default)

    lines = []
    lines.append(f"[bold]Execution ID:[/bold]  {_get('id', _get('execution_id', exec_id))}")
    lines.append(f"[bold]Runbook:[/bold]       {_get('runbook_name', _get('runbook'))}")
    lines.append(f"[bold]Trigger:[/bold]       {_get('trigger_type', _get('trigger'))}")
    lines.append(f"[bold]Device:[/bold]        {_get('device_id')}")
    lines.append(f"[bold]Status:[/bold]        {_status_style(str(_get('status', 'unknown')))}")
    lines.append(f"[bold]Duration:[/bold]      {_get('duration')}")
    lines.append(f"[bold]Started:[/bold]       {_get('started_at', _get('created_at'))}")
    lines.append(f"[bold]Completed:[/bold]     {_get('completed_at', _get('ended_at'))}")
    lines.append(f"[bold]Dry Run:[/bold]       {_get('dry_run', False)}")
    lines.append("")

    # Action results
    action_results = _get("action_results", [])
    if not action_results:
        action_results = _get("actions", [])

    if action_results:
        lines.append("[bold underline]Action Results[/bold underline]")
        for i, ar in enumerate(action_results, 1):
            if isinstance(ar, dict):
                ar_name = ar.get("name", ar.get("action", f"action_{i}"))
                ar_status = ar.get("status", "unknown")
                ar_output = ar.get("output", ar.get("result", ""))
                ar_duration = ar.get("duration", "--")
                ar_started = ar.get("started_at", "--")
                ar_error = ar.get("error", "")
            else:
                ar_name = getattr(ar, "name", f"action_{i}")
                ar_status = getattr(ar, "status", "unknown")
                ar_output = getattr(ar, "output", "")
                ar_duration = getattr(ar, "duration", "--")
                ar_started = getattr(ar, "started_at", "--")
                ar_error = getattr(ar, "error", "")

            lines.append(f"  {i}. [cyan]{escape(str(ar_name))}[/cyan]")
            lines.append(f"     Status:   {_status_style(str(ar_status))}")
            lines.append(f"     Duration: {ar_duration}")
            lines.append(f"     Started:  {ar_started}")
            if ar_output:
                output_str = str(ar_output)
                if len(output_str) > 300:
                    output_str = output_str[:300] + "..."
                lines.append(f"     Output:   {escape(output_str)}")
            if ar_error:
                lines.append(f"     [red]Error:    {escape(str(ar_error))}[/red]")
            lines.append("")

    # Error info
    error = _get("error", "")
    if error:
        lines.append(f"[red bold]Error:[/red bold] {escape(str(error))}")

    status_val = str(_get("status", "unknown")).lower()
    border = "green" if status_val in ("completed", "success") else "red" if status_val == "failed" else "yellow"
    console.print(Panel(
        "\n".join(lines),
        title=f"[bold]Execution Detail[/bold]",
        border_style=border,
        padding=(1, 2),
    ))


@automation_app.command("jobs")
def list_jobs() -> None:
    """List all scheduled automation jobs."""

    async def _run():
        engine = await _get_automation_engine()
        scheduler = getattr(engine, "scheduler", None)
        if scheduler is None:
            return []
        return scheduler.list_jobs()

    jobs = run_async(_run())

    if not jobs:
        console.print("[dim]No scheduled jobs found.[/dim]")
        return

    table = Table(title="Scheduled Jobs", border_style="blue")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Type", justify="center")
    table.add_column("Schedule", justify="center")
    table.add_column("Next Run")
    table.add_column("Status", justify="center")

    for job in jobs:
        if isinstance(job, dict):
            j_id = job.get("id", job.get("job_id", "--"))
            j_name = job.get("name", "--")
            j_type = job.get("type", job.get("trigger_type", "--"))
            j_cron = job.get("cron", job.get("schedule", "--"))
            j_next = job.get("next_run", job.get("next_run_time", "--"))
            j_enabled = job.get("enabled", job.get("active", True))
        else:
            j_id = getattr(job, "id", getattr(job, "job_id", "--"))
            j_name = getattr(job, "name", "--")
            j_type = getattr(job, "type", getattr(job, "trigger_type", "--"))
            j_cron = getattr(job, "cron", getattr(job, "schedule", "--"))
            j_next = getattr(job, "next_run", getattr(job, "next_run_time", "--"))
            j_enabled = getattr(job, "enabled", getattr(job, "active", True))

        status_str = _status_style("active" if j_enabled else "paused")

        table.add_row(
            str(j_id),
            str(j_name),
            str(j_type),
            str(j_cron),
            str(j_next),
            status_str,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(jobs)} job(s)[/dim]")


@automation_app.command("run-job")
def run_job(
    job_id: str = typer.Argument(..., help="Scheduled job ID to trigger"),
) -> None:
    """Trigger a scheduled job for immediate execution."""

    async def _run():
        engine = await _get_automation_engine()
        scheduler = getattr(engine, "scheduler", None)
        if scheduler is None:
            raise typer.Exit(code=1)
        await scheduler.run_job_now(job_id)

    try:
        run_async(_run())
        console.print(f"[green]Job '{escape(job_id)}' triggered for immediate execution.[/green]")
    except ValueError as e:
        err_console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@automation_app.command("audit")
def show_audit(
    device: Optional[str] = typer.Option(None, "--device", help="Filter by device ID"),
    actor: Optional[str] = typer.Option(None, "--actor", help="Filter by actor"),
    action_type: Optional[str] = typer.Option(None, "--action-type", help="Filter by action type"),
    limit: int = typer.Option(30, "--limit", "-n", help="Maximum entries to show"),
) -> None:
    """Show recent audit log entries."""

    async def _run():
        audit_logger = await _get_audit_logger()
        return await audit_logger.get_log(
            device_id=device,
            actor=actor,
            action_type=action_type,
            limit=limit,
        )

    entries = run_async(_run())

    if not entries:
        console.print("[dim]No audit entries found.[/dim]")
        return

    table = Table(title="Audit Log", border_style="blue")
    table.add_column("Time", style="dim")
    table.add_column("Actor", style="bold")
    table.add_column("Action", style="cyan")
    table.add_column("Device")
    table.add_column("Description")
    table.add_column("Result", justify="center")

    for entry in entries:
        if isinstance(entry, dict):
            e_time = entry.get("timestamp", entry.get("created_at", "--"))
            e_actor = entry.get("actor", "--")
            e_action = entry.get("action_type", entry.get("action", "--"))
            e_device = entry.get("device_id", "--")
            e_desc = entry.get("description", "--")
            e_result = entry.get("result", entry.get("status", "--"))
        else:
            e_time = getattr(entry, "timestamp", getattr(entry, "created_at", "--"))
            e_actor = getattr(entry, "actor", "--")
            e_action = getattr(entry, "action_type", getattr(entry, "action", "--"))
            e_device = getattr(entry, "device_id", "--")
            e_desc = getattr(entry, "description", "--")
            e_result = getattr(entry, "result", getattr(entry, "status", "--"))

        table.add_row(
            str(e_time),
            str(e_actor),
            str(e_action),
            str(e_device) if e_device else "--",
            str(e_desc)[:80] if e_desc else "--",
            _status_style(str(e_result)) if e_result and e_result != "--" else "--",
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(entries)} audit entries[/dim]")
