"""VLAN management CLI commands."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()
vlan_app = typer.Typer(no_args_is_help=True)


@vlan_app.command("list")
def list_vlans(device: str = typer.Argument(..., help="Device ID or hostname")):
    """List all VLANs on a device."""
    from .app import run_async, get_db
    from operations.vlan import VlanManager

    async def _run():
        db = await get_db()
        mgr = VlanManager(db)
        return await mgr.list_vlans(device)

    vlans = run_async(_run())
    if not vlans:
        console.print("[yellow]No VLANs found. Run 'vlans sync' to pull from device.[/yellow]")
        return

    table = Table(title=f"VLANs - {device}", box=box.ROUNDED)
    table.add_column("VLAN ID", style="cyan", justify="right")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Description")
    table.add_column("Synced At", style="dim")

    for v in vlans:
        status_style = "green" if v.get("status") == "active" else "yellow"
        table.add_row(
            str(v.get("vlan_id", "")),
            v.get("name", ""),
            f"[{status_style}]{v.get('status', '')}[/{status_style}]",
            v.get("description", "") or "",
            v.get("synced_at", "") or "",
        )
    console.print(table)


@vlan_app.command("sync")
def sync_vlans(device: str = typer.Argument(..., help="Device ID")):
    """Sync VLANs from a live device to the database."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.vlan import VlanManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = VlanManager(db, config_mgr)
        return await mgr.sync_vlans(device)

    with console.status("Syncing VLANs from device..."):
        result = run_async(_run())
    console.print(
        f"[green]Synced {result.get('vlans_synced', 0)} VLANs[/green] "
        f"from {device} (vendor: {result.get('vendor', 'unknown')})"
    )


@vlan_app.command("create")
def create_vlan(
    device: str = typer.Argument(..., help="Device ID"),
    vlan_id: int = typer.Argument(..., help="VLAN number (1-4094)"),
    name: Optional[str] = typer.Option(None, help="VLAN name"),
    description: Optional[str] = typer.Option(None, help="VLAN description"),
):
    """Create a VLAN on a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.vlan import VlanManager

    if not typer.confirm(f"Create VLAN {vlan_id} on {device}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = VlanManager(db, config_mgr)
        return await mgr.create_vlan(device, vlan_id, name, description)

    with console.status("Creating VLAN..."):
        result = run_async(_run())
    console.print(f"[green]VLAN {vlan_id} created[/green] ({result.get('name', '')})")
    if result.get("commands_sent"):
        console.print(f"[dim]Commands: {result['commands_sent']}[/dim]")


@vlan_app.command("delete")
def delete_vlan(
    device: str = typer.Argument(..., help="Device ID"),
    vlan_id: int = typer.Argument(..., help="VLAN number to delete"),
):
    """Delete a VLAN from a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.vlan import VlanManager

    if not typer.confirm(f"Delete VLAN {vlan_id} from {device}? This also removes interface assignments."):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = VlanManager(db, config_mgr)
        return await mgr.delete_vlan(device, vlan_id)

    with console.status("Deleting VLAN..."):
        result = run_async(_run())
    console.print(f"[green]VLAN {vlan_id} deleted[/green] from {device}")


@vlan_app.command("assign")
def assign_interface(
    device: str = typer.Argument(..., help="Device ID"),
    vlan_id: int = typer.Argument(..., help="VLAN number"),
    interface: str = typer.Argument(..., help="Interface name (e.g. GigabitEthernet0/1)"),
    mode: str = typer.Option("access", help="Port mode: access, trunk, tagged, untagged"),
):
    """Assign an interface to a VLAN."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.vlan import VlanManager

    if not typer.confirm(f"Assign {interface} to VLAN {vlan_id} ({mode} mode) on {device}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = VlanManager(db, config_mgr)
        return await mgr.assign_interface(device, vlan_id, interface, mode)

    with console.status("Assigning interface..."):
        result = run_async(_run())
    console.print(
        f"[green]{interface} assigned to VLAN {vlan_id}[/green] ({mode} mode)"
    )
    if result.get("commands_sent"):
        console.print(f"[dim]Commands: {result['commands_sent']}[/dim]")


@vlan_app.command("summary")
def vlan_summary(device: str = typer.Argument(..., help="Device ID")):
    """Show VLAN summary with interface assignments."""
    from .app import run_async, get_db
    from operations.vlan import VlanManager

    async def _run():
        db = await get_db()
        mgr = VlanManager(db)
        return await mgr.get_vlan_summary(device)

    summary = run_async(_run())
    console.print(f"\n[bold]VLAN Summary for {device}[/bold]  ({summary.get('vlan_count', 0)} VLANs)\n")

    for vlan in summary.get("vlans", []):
        ifaces = vlan.get("interfaces", [])
        iface_str = ", ".join(
            f"{i['interface']} ({i['mode']})" for i in ifaces
        ) if ifaces else "[dim]none[/dim]"
        console.print(
            f"  VLAN [cyan]{vlan['vlan_id']}[/cyan]  "
            f"[bold]{vlan.get('name', '')}[/bold]  "
            f"[{('green' if vlan.get('status') == 'active' else 'yellow')}]"
            f"{vlan.get('status', '')}[/]  "
            f"Interfaces: {iface_str}"
        )
