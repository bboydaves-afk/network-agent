"""ACL management CLI commands."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()
acl_app = typer.Typer(no_args_is_help=True)


@acl_app.command("list")
def list_acls(device: str = typer.Argument(..., help="Device ID")):
    """List all ACLs on a device."""
    from .app import run_async, get_db
    from operations.acl import ACLManager

    async def _run():
        db = await get_db()
        mgr = ACLManager(db)
        return await mgr.list_acls(device)

    acls = run_async(_run())
    if not acls:
        console.print("[yellow]No ACLs found. Run 'acls sync' to pull from device.[/yellow]")
        return

    table = Table(title=f"Access Lists - {device}", box=box.ROUNDED)
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Entries", justify="right")
    table.add_column("Bindings")

    for a in acls:
        bindings = a.get("bindings", [])
        bind_str = ", ".join(f"{b['interface']} ({b['direction']})" for b in bindings) if bindings else "none"
        table.add_row(
            a.get("name", ""),
            a.get("acl_type", ""),
            str(a.get("entry_count", 0)),
            bind_str,
        )
    console.print(table)


@acl_app.command("sync")
def sync_acls(device: str = typer.Argument(..., help="Device ID")):
    """Sync ACLs from a live device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.acl import ACLManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ACLManager(db, config_mgr)
        return await mgr.sync_acls(device)

    with console.status("Syncing ACLs..."):
        result = run_async(_run())
    console.print(f"[green]Synced {result.get('acls_synced', 0)} ACLs[/green] from {device}")


@acl_app.command("create")
def create_acl(
    device: str = typer.Argument(..., help="Device ID"),
    name: str = typer.Option(..., prompt=True, help="ACL name"),
    acl_type: str = typer.Option("extended", help="ACL type: standard or extended"),
):
    """Create an ACL on a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.acl import ACLManager

    if not typer.confirm(f"Create ACL '{name}' on {device}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ACLManager(db, config_mgr)
        return await mgr.create_acl(device, name, acl_type)

    result = run_async(_run())
    console.print(f"[green]ACL '{name}' created[/green]")


@acl_app.command("delete")
def delete_acl(
    device: str = typer.Argument(..., help="Device ID"),
    name: str = typer.Argument(..., help="ACL name to delete"),
):
    """Delete an ACL from a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.acl import ACLManager

    if not typer.confirm(f"Delete ACL '{name}' from {device}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ACLManager(db, config_mgr)
        return await mgr.delete_acl(device, name)

    result = run_async(_run())
    console.print(f"[green]ACL '{name}' deleted[/green]")


@acl_app.command("add-entry")
def add_entry(
    device: str = typer.Argument(..., help="Device ID"),
    acl_name: str = typer.Option(..., prompt=True, help="ACL name"),
    sequence: int = typer.Option(..., prompt=True, help="Sequence number"),
    action: str = typer.Option(..., prompt=True, help="Action: permit or deny"),
    protocol: str = typer.Option("ip", help="Protocol"),
    source: str = typer.Option("any", help="Source address"),
    destination: str = typer.Option("any", help="Destination address"),
    dest_port: Optional[str] = typer.Option(None, help="Destination port"),
    log: bool = typer.Option(False, help="Enable logging"),
):
    """Add an entry to an ACL."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.acl import ACLManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ACLManager(db, config_mgr)
        return await mgr.add_entry(device, acl_name, sequence, action, protocol, source, destination, dest_port=dest_port, log_enabled=log)

    result = run_async(_run())
    console.print(f"[green]Entry {sequence} added to ACL '{acl_name}'[/green]")


@acl_app.command("bind")
def bind_acl(
    device: str = typer.Argument(..., help="Device ID"),
    acl_name: str = typer.Argument(..., help="ACL name"),
    interface: str = typer.Argument(..., help="Interface name"),
    direction: str = typer.Option("in", help="Direction: in or out"),
):
    """Apply an ACL to an interface."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.acl import ACLManager

    if not typer.confirm(f"Bind ACL '{acl_name}' to {interface} ({direction}) on {device}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ACLManager(db, config_mgr)
        return await mgr.bind_acl(device, acl_name, interface, direction)

    result = run_async(_run())
    console.print(f"[green]ACL '{acl_name}' bound to {interface} ({direction})[/green]")


@acl_app.command("unbind")
def unbind_acl(
    device: str = typer.Argument(..., help="Device ID"),
    acl_name: str = typer.Argument(..., help="ACL name"),
    interface: str = typer.Argument(..., help="Interface name"),
    direction: str = typer.Option("in", help="Direction: in or out"),
):
    """Remove an ACL from an interface."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.acl import ACLManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = ACLManager(db, config_mgr)
        return await mgr.unbind_acl(device, acl_name, interface, direction)

    result = run_async(_run())
    console.print(f"[green]ACL '{acl_name}' unbound from {interface}[/green]")
