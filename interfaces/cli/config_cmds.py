"""Configuration management CLI commands."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Confirm

console = Console()
err_console = Console(stderr=True)

config_app = typer.Typer(no_args_is_help=True)


@config_app.command("backup")
def backup_config(
    device_id: str = typer.Argument(..., help="Device ID to backup"),
    config_type: str = typer.Option("running", "--type", "-t", help="Config type: running, startup, candidate"),
) -> None:
    """Backup the configuration of a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager

    async def _backup():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, "Device not found"

        cred_mgr = await get_cred_manager()
        mgr = ConfigManager(db, cred_mgr)
        result = await mgr.backup_config(device_id, config_type=config_type)
        return device, result

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Backing up config for {device_id[:8]}...", total=None)
        outcome = run_async(_backup())

    if outcome is None or outcome[0] is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    device, result = outcome
    hostname = device.get("hostname", device_id)

    if isinstance(result, dict):
        console.print(Panel(
            f"[green]Configuration backed up successfully![/green]\n\n"
            f"  [bold]Device:[/bold]    {hostname}\n"
            f"  [bold]Backup ID:[/bold] {result.get('id', 'N/A')}\n"
            f"  [bold]Type:[/bold]      {config_type}\n"
            f"  [bold]Hash:[/bold]      {result.get('hash', 'N/A')}\n"
            f"  [bold]Size:[/bold]      {result.get('size', 'N/A')} bytes\n"
            f"  [bold]Stored:[/bold]    {result.get('timestamp', datetime.utcnow().isoformat())}",
            title="[bold green]Config Backup Complete[/bold green]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[green]Configuration backed up successfully![/green]\n\n"
            f"  [bold]Device:[/bold]    {hostname}\n"
            f"  [bold]Backup ID:[/bold] {result}\n"
            f"  [bold]Type:[/bold]      {config_type}",
            title="[bold green]Config Backup Complete[/bold green]",
            border_style="green",
        ))


@config_app.command("backup-all")
def backup_all(
    config_type: str = typer.Option("running", "--type", "-t", help="Config type to backup"),
) -> None:
    """Backup configuration for all managed devices."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager

    async def _get_devices():
        db = await get_db()
        return await db.get_all_devices()

    devices = run_async(_get_devices())

    if not devices:
        console.print("[yellow]No devices found to backup.[/yellow]")
        return

    successes = []
    failures = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Backing up all devices...", total=len(devices))

        for device in devices:
            dev_id = device.get("id", "")
            hostname = device.get("hostname", dev_id[:8])
            progress.update(task, description=f"Backing up {hostname}...")

            async def _backup_one(did=dev_id):
                db = await get_db()
                cred_mgr = await get_cred_manager()
                mgr = ConfigManager(db, cred_mgr)
                return await mgr.backup_config(did, config_type=config_type)

            try:
                result = run_async(_backup_one())
                successes.append((hostname, result))
            except Exception as exc:
                failures.append((hostname, str(exc)))

            progress.advance(task)

    # Summary
    table = Table(title="Backup Results", title_style="bold cyan", padding=(0, 1))
    table.add_column("Device", style="bold")
    table.add_column("Status")
    table.add_column("Details", style="dim")

    for hostname, result in successes:
        backup_id = result.get("id", str(result)) if isinstance(result, dict) else str(result)
        table.add_row(hostname, "[green]SUCCESS[/green]", f"ID: {backup_id[:8] if len(str(backup_id)) > 8 else backup_id}")

    for hostname, error in failures:
        table.add_row(hostname, "[red]FAILED[/red]", error[:60])

    console.print(table)
    console.print(
        f"\n[green]{len(successes)} succeeded[/green], "
        f"[red]{len(failures)} failed[/red] out of {len(devices)} devices."
    )


@config_app.command("history")
def config_history(
    device_id: str = typer.Argument(..., help="Device ID to show history for"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of entries to show"),
) -> None:
    """Show configuration backup history for a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager

    async def _history():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, None

        cred_mgr = await get_cred_manager()
        mgr = ConfigManager(db, cred_mgr)
        history = await mgr.get_config_history(device_id, limit=limit)
        return device, history

    device, history = run_async(_history())

    if device is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    if not history:
        console.print(f"[yellow]No config history for {hostname}.[/yellow]")
        return

    table = Table(
        title=f"Config History: {hostname}",
        title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("Backup ID", style="dim", max_width=10)
    table.add_column("Date", style="bold")
    table.add_column("Type")
    table.add_column("Hash", style="dim", max_width=16)
    table.add_column("Size")

    for entry in history:
        backup_id = str(entry.get("id", ""))
        timestamp = entry.get("timestamp", entry.get("created_at", ""))
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp)
                timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass
        config_type = entry.get("config_type", entry.get("type", "running"))
        config_hash = entry.get("hash", entry.get("config_hash", ""))[:16]
        size = entry.get("size", "")
        size_display = f"{size} B" if size else "--"

        table.add_row(
            backup_id[:10] if len(backup_id) > 10 else backup_id,
            timestamp,
            config_type,
            config_hash,
            str(size_display),
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(history)} entries (limit: {limit})[/dim]")


@config_app.command("show")
def show_config(
    backup_id: str = typer.Argument(..., help="Backup ID to display"),
    language: str = typer.Option("cisco", "--lang", "-l", help="Syntax highlighting language"),
) -> None:
    """Display a configuration backup with syntax highlighting."""
    from .app import run_async, get_db

    async def _get_config():
        db = await get_db()
        return await db.get_config_backup(backup_id)

    backup = run_async(_get_config())

    if not backup:
        err_console.print(f"[red]Backup not found:[/red] {backup_id}")
        raise typer.Exit(code=1)

    config_text = backup.get("config", backup.get("content", ""))
    timestamp = backup.get("timestamp", backup.get("created_at", ""))
    config_type = backup.get("config_type", backup.get("type", ""))
    device_id = backup.get("device_id", "")

    # Header info
    console.print(Panel(
        f"  [bold]Backup ID:[/bold]  {backup_id}\n"
        f"  [bold]Device:[/bold]     {device_id[:8] if device_id else 'N/A'}\n"
        f"  [bold]Type:[/bold]       {config_type}\n"
        f"  [bold]Timestamp:[/bold]  {timestamp}",
        title="[bold]Config Backup Details[/bold]",
        border_style="blue",
    ))

    # Config content with syntax highlighting
    if config_text:
        syntax = Syntax(
            config_text,
            language,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        console.print(Panel(syntax, title="[bold]Configuration[/bold]", border_style="cyan"))
    else:
        console.print("[yellow]Configuration content is empty.[/yellow]")


@config_app.command("diff")
def diff_configs(
    backup_id_1: str = typer.Argument(..., help="First backup ID"),
    backup_id_2: str = typer.Argument(..., help="Second backup ID"),
    context_lines: int = typer.Option(3, "--context", "-c", help="Lines of context around changes"),
) -> None:
    """Show unified diff between two configuration backups."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager

    async def _diff():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = ConfigManager(db, cred_mgr)
        return await mgr.diff_configs(backup_id_1, backup_id_2, context_lines=context_lines)

    try:
        diff_result = run_async(_diff())
    except Exception as exc:
        err_console.print(f"[red]Failed to compute diff:[/red] {exc}")
        raise typer.Exit(code=1)

    if isinstance(diff_result, str):
        diff_text = diff_result
    elif isinstance(diff_result, dict):
        diff_text = diff_result.get("diff", diff_result.get("unified_diff", ""))
    else:
        diff_text = str(diff_result)

    if not diff_text or diff_text.strip() == "":
        console.print("[green]No differences found between the two configurations.[/green]")
        return

    console.print(Panel(
        f"[bold]Comparing:[/bold] {backup_id_1[:10]} <-> {backup_id_2[:10]}",
        border_style="blue",
    ))

    # Colorize diff output
    lines = diff_text.split("\n")
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        else:
            console.print(f"[dim]{line}[/dim]")


@config_app.command("deploy")
def deploy_config(
    device_id: str = typer.Argument(..., help="Device ID to deploy config to"),
    commands: str = typer.Option(..., "--commands", "-c", help="Commands to deploy (semicolon-separated)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview commands without deploying"),
    backup_first: bool = typer.Option(True, "--backup/--no-backup", help="Backup config before deploying"),
) -> None:
    """Deploy configuration commands to a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager

    cmd_list = [c.strip() for c in commands.split(";") if c.strip()]

    if not cmd_list:
        err_console.print("[red]No commands provided.[/red]")
        raise typer.Exit(code=1)

    # Fetch device for display
    async def _get_device():
        db = await get_db()
        return await db.get_device(device_id)

    device = run_async(_get_device())
    if not device:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    # Show preview
    console.print(Panel(
        "\n".join(f"  [cyan]{i+1}.[/cyan] {cmd}" for i, cmd in enumerate(cmd_list)),
        title=f"[bold]Commands to deploy to {hostname}[/bold]",
        border_style="yellow",
    ))

    if dry_run:
        console.print("[yellow]Dry run mode -- no commands will be deployed.[/yellow]")
        return

    # Confirm
    confirmed = Confirm.ask(
        f"Deploy {len(cmd_list)} command(s) to [bold]{hostname}[/bold]?"
    )
    if not confirmed:
        console.print("[dim]Deployment cancelled.[/dim]")
        return

    async def _deploy():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = ConfigManager(db, cred_mgr)
        return await mgr.deploy_config(
            device_id,
            commands=cmd_list,
            backup_first=backup_first,
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Deploying to {hostname}...", total=None)
        try:
            result = run_async(_deploy())
        except Exception as exc:
            err_console.print(f"\n[red]Deployment failed:[/red] {exc}")
            raise typer.Exit(code=1)

    if isinstance(result, dict):
        output = result.get("output", result.get("result", ""))
        backup_id = result.get("backup_id", "")
        lines = [f"[green]Deployment successful![/green]\n"]
        lines.append(f"  [bold]Device:[/bold]     {hostname}")
        lines.append(f"  [bold]Commands:[/bold]   {len(cmd_list)}")
        if backup_id:
            lines.append(f"  [bold]Pre-backup:[/bold] {backup_id}")
        if output:
            lines.append(f"\n  [bold]Output:[/bold]\n{output}")
        console.print(Panel("\n".join(lines), title="[bold green]Deployment Complete[/bold green]", border_style="green"))
    else:
        console.print(Panel(
            f"[green]Deployment successful![/green]\n\n"
            f"  [bold]Device:[/bold]   {hostname}\n"
            f"  [bold]Commands:[/bold] {len(cmd_list)}\n"
            f"  [bold]Result:[/bold]   {result}",
            title="[bold green]Deployment Complete[/bold green]",
            border_style="green",
        ))


@config_app.command("rollback")
def rollback_config(
    device_id: str = typer.Argument(..., help="Device ID to rollback"),
    backup_id: str = typer.Argument(..., help="Backup ID to rollback to"),
) -> None:
    """Rollback device configuration to a previous backup."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager

    async def _get_device():
        db = await get_db()
        return await db.get_device(device_id)

    device = run_async(_get_device())
    if not device:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    confirmed = Confirm.ask(
        f"Rollback [bold]{hostname}[/bold] to backup [bold]{backup_id[:10]}[/bold]? "
        "This will overwrite the current running config"
    )
    if not confirmed:
        console.print("[dim]Rollback cancelled.[/dim]")
        return

    async def _rollback():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = ConfigManager(db, cred_mgr)
        return await mgr.rollback_config(device_id, backup_id)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Rolling back {hostname}...", total=None)
        try:
            result = run_async(_rollback())
        except Exception as exc:
            err_console.print(f"\n[red]Rollback failed:[/red] {exc}")
            raise typer.Exit(code=1)

    if isinstance(result, dict):
        console.print(Panel(
            f"[green]Rollback successful![/green]\n\n"
            f"  [bold]Device:[/bold]    {hostname}\n"
            f"  [bold]Restored:[/bold]  Backup {backup_id[:10]}\n"
            f"  [bold]Backup:[/bold]    {result.get('pre_rollback_backup_id', 'N/A')}",
            title="[bold green]Rollback Complete[/bold green]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[green]Rollback successful![/green]\n\n"
            f"  [bold]Device:[/bold]   {hostname}\n"
            f"  [bold]Restored:[/bold] Backup {backup_id[:10]}",
            title="[bold green]Rollback Complete[/bold green]",
            border_style="green",
        ))
