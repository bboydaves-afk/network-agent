"""CLI commands for change management."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()
change_app = typer.Typer(no_args_is_help=True)


@change_app.command("list")
def list_changes(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    device_id: Optional[str] = typer.Option(None, "--device", "-d"),
) -> None:
    """List change requests."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.list_requests(status=status, device_id=device_id)

    changes = run_async(_run())
    if not changes:
        console.print("[dim]No change requests found.[/dim]")
        return

    table = Table(title="Change Requests")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Title", style="bold")
    table.add_column("Device", max_width=8)
    table.add_column("Status")
    table.add_column("Priority")
    table.add_column("Requested By")
    table.add_column("Created")
    for c in changes:
        st = c.get("status", "")
        st_style = {
            "pending": "yellow", "approved": "green", "rejected": "red",
            "applied": "cyan", "failed": "red bold", "rolled_back": "magenta",
        }.get(st, "dim")
        table.add_row(
            c["id"][:8], c.get("title", ""), c.get("device_id", "")[:8],
            f"[{st_style}]{st}[/{st_style}]", c.get("priority", ""), c.get("requested_by", ""),
            c.get("created_at", ""),
        )
    console.print(table)


@change_app.command("request")
def create_request(
    device_id: str = typer.Argument(..., help="Device ID"),
    title: str = typer.Argument(..., help="Change title"),
    commands: str = typer.Argument(..., help="Config commands (newline-separated)"),
    requested_by: str = typer.Option("cli-user", "--by", "-b"),
    priority: str = typer.Option("normal", "--priority", "-p"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
    rollback: Optional[str] = typer.Option(None, "--rollback", "-r", help="Rollback commands (newline-separated)"),
) -> None:
    """Create a new change request."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.create_request(
            device_id=device_id, title=title, config_commands=commands,
            requested_by=requested_by, priority=priority, notes=notes,
            rollback_plan=rollback,
        )

    cr = run_async(_run())
    console.print(f"[green]Change request created:[/green] {cr['id'][:8]} - {title}")
    if rollback:
        console.print(f"[dim]Rollback plan attached[/dim]")


@change_app.command("approve")
def approve(
    change_id: str = typer.Argument(..., help="Change request ID"),
    approved_by: str = typer.Option("cli-admin", "--by", "-b"),
) -> None:
    """Approve a change request."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.approve_request(change_id, approved_by=approved_by)

    result = run_async(_run())
    if result and result.get("status") == "approved":
        console.print(f"[green]Change request {change_id[:8]} approved.[/green]")
    else:
        console.print(f"[yellow]Could not approve — current status: {result.get('status', 'unknown')}[/yellow]")


@change_app.command("reject")
def reject(
    change_id: str = typer.Argument(..., help="Change request ID"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r"),
) -> None:
    """Reject a change request."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.reject_request(change_id, reason=reason)

    result = run_async(_run())
    if result and result.get("status") == "rejected":
        console.print(f"[yellow]Change request {change_id[:8]} rejected.[/yellow]")
    else:
        console.print(f"[dim]Could not reject — current status: {result.get('status', 'unknown')}[/dim]")


@change_app.command("execute")
def execute_change(
    change_id: str = typer.Argument(..., help="Change request ID to execute"),
) -> None:
    """Execute an approved change with pre/post checks and auto-rollback."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.change_management import ChangeManager

    if not typer.confirm(f"Execute change {change_id[:8]}? This will deploy to the device."):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ChangeManager(db, config_mgr)
        return await mgr.execute_change(change_id)

    with console.status("Executing change..."):
        result = run_async(_run())

    status = result.get("status", "unknown")
    if status == "applied":
        console.print(f"[green]Change {change_id[:8]} applied successfully.[/green]")
    elif status == "rolled_back":
        console.print(f"[magenta]Change {change_id[:8]} was rolled back automatically.[/magenta]")
    elif status == "failed":
        console.print(f"[red]Change {change_id[:8]} failed: {result.get('error', '')}[/red]")
    else:
        console.print(f"[dim]Change {change_id[:8]} status: {status}[/dim]")

    # Show steps
    for step in result.get("steps", []):
        step_name = step.get("step", "")
        step_result = step.get("result", step.get("status", ""))
        if isinstance(step_result, dict):
            passed = step_result.get("passed", None)
            if passed is True:
                console.print(f"  [green]✓[/green] {step_name}")
            elif passed is False:
                console.print(f"  [red]✗[/red] {step_name}")
            else:
                console.print(f"  [dim]•[/dim] {step_name}: {step_result.get('status', '')}")
        else:
            console.print(f"  [dim]•[/dim] {step_name}: {step_result}")


@change_app.command("rollback")
def rollback_change(
    change_id: str = typer.Argument(..., help="Change request ID to rollback"),
) -> None:
    """Manually rollback an applied change."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.change_management import ChangeManager

    if not typer.confirm(f"Rollback change {change_id[:8]}? This will deploy rollback commands."):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ChangeManager(db, config_mgr)
        return await mgr.rollback_change(change_id)

    with console.status("Rolling back..."):
        result = run_async(_run())

    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
    elif result.get("status") == "completed":
        console.print(f"[green]Change {change_id[:8]} rolled back successfully.[/green]")
    else:
        console.print(f"[yellow]Rollback status: {result.get('status', 'unknown')}[/yellow]")


@change_app.command("apply")
def apply_change(
    change_id: str = typer.Argument(..., help="Change request ID"),
) -> None:
    """Apply an approved change request (simple deploy, no pre/post checks)."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ChangeManager(db, config_mgr)
        return await mgr.apply_request(change_id)

    result = run_async(_run())
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
    else:
        console.print(f"[green]Change request {change_id[:8]} applied successfully.[/green]")


@change_app.command("detail")
def detail(
    change_id: str = typer.Argument(..., help="Change request ID"),
) -> None:
    """Show details of a change request including approvals and rollbacks."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.get_request(change_id)

    cr = run_async(_run())
    if not cr:
        console.print("[red]Change request not found.[/red]")
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("Title", cr.get("title", ""))
    table.add_row("Status", cr.get("status", ""))
    table.add_row("Priority", cr.get("priority", ""))
    table.add_row("Device", cr.get("device_id", ""))
    table.add_row("Requested By", cr.get("requested_by", ""))
    table.add_row("Approved By", cr.get("approved_by", "") or "")
    table.add_row("Notes", cr.get("notes", "") or "")
    if cr.get("rollback_plan"):
        table.add_row("Rollback Plan", "[green]defined[/green]")
    if cr.get("rollback_status"):
        table.add_row("Rollback Status", cr.get("rollback_status", ""))
    if cr.get("scheduled_at"):
        table.add_row("Scheduled At", cr.get("scheduled_at", ""))
    if cr.get("maintenance_window_start"):
        table.add_row("Maintenance Window", f"{cr.get('maintenance_window_start')} — {cr.get('maintenance_window_end', '')}")
    console.print(Panel(table, title="[bold]Change Request[/bold]", border_style="cyan"))

    console.print("\n[bold]Commands:[/bold]")
    console.print(cr.get("config_commands", ""))

    if cr.get("rollback_plan"):
        console.print("\n[bold]Rollback Plan:[/bold]")
        console.print(cr.get("rollback_plan", ""))

    # Show approvals
    approvals = cr.get("approvals", [])
    if approvals:
        console.print("\n[bold]Approvals:[/bold]")
        for a in approvals:
            status_color = "green" if a.get("status") == "approved" else "red"
            console.print(f"  [{status_color}]{a.get('status', '')}[/{status_color}] by {a.get('approver', '')} at {a.get('decided_at', '')}")

    # Show rollbacks
    rollbacks = cr.get("rollbacks", [])
    if rollbacks:
        console.print("\n[bold]Rollback History:[/bold]")
        for rb in rollbacks:
            status_color = "green" if rb.get("status") == "completed" else "red"
            console.print(f"  [{status_color}]{rb.get('status', '')}[/{status_color}] by {rb.get('executed_by', '')} at {rb.get('executed_at', '')}")


@change_app.command("history")
def change_history(
    device_id: str = typer.Argument(..., help="Device ID"),
    limit: int = typer.Option(20, "--limit", "-l"),
) -> None:
    """Show change history for a device."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.get_change_history(device_id, limit=limit)

    changes = run_async(_run())
    if not changes:
        console.print("[dim]No change history for this device.[/dim]")
        return

    table = Table(title=f"Change History — {device_id}")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Title", style="bold")
    table.add_column("Status")
    table.add_column("Rollback")
    table.add_column("Created")
    table.add_column("Applied")

    for c in changes:
        st = c.get("status", "")
        st_style = {
            "pending": "yellow", "approved": "green", "rejected": "red",
            "applied": "cyan", "failed": "red bold", "rolled_back": "magenta",
        }.get(st, "dim")
        rb = c.get("rollback_status", "") or ("plan" if c.get("rollback_plan") else "none")
        table.add_row(
            c["id"][:8], c.get("title", ""),
            f"[{st_style}]{st}[/{st_style}]",
            rb,
            c.get("created_at", "")[:19],
            (c.get("applied_at", "") or "")[:19],
        )
    console.print(table)


@change_app.command("pending")
def pending_changes() -> None:
    """List all changes awaiting approval."""
    from .app import run_async, get_db
    from operations.change_management import ChangeManager

    async def _run():
        db = await get_db()
        mgr = ChangeManager(db)
        return await mgr.list_pending()

    changes = run_async(_run())
    if not changes:
        console.print("[green]No pending change requests.[/green]")
        return

    console.print(f"[yellow]{len(changes)} change(s) awaiting approval:[/yellow]")
    table = Table()
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Title", style="bold")
    table.add_column("Device", max_width=8)
    table.add_column("Priority")
    table.add_column("Requested By")
    table.add_column("Created")

    for c in changes:
        table.add_row(
            c["id"][:8], c.get("title", ""), c.get("device_id", "")[:8],
            c.get("priority", ""), c.get("requested_by", ""), c.get("created_at", ""),
        )
    console.print(table)
