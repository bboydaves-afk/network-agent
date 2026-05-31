"""CLI commands for IP Address Management."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()
ipam_app = typer.Typer(no_args_is_help=True)


@ipam_app.command("subnets")
def list_subnets(
    site_id: Optional[str] = typer.Option(None, "--site", "-s", help="Filter by site"),
) -> None:
    """List all managed subnets."""
    from .app import run_async, get_db
    from operations.ipam import IPAMManager

    async def _run():
        db = await get_db()
        mgr = IPAMManager(db)
        return await mgr.list_subnets(site_id=site_id)

    subnets = run_async(_run())
    if not subnets:
        console.print("[dim]No subnets found.[/dim]")
        return

    table = Table(title="Subnets")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Network", style="bold cyan")
    table.add_column("Name")
    table.add_column("VLAN")
    table.add_column("Gateway")
    table.add_column("Site")
    for s in subnets:
        table.add_row(s["id"][:8], f"{s['network']}/{s['prefix_length']}", s.get("name", "") or "", str(s.get("vlan_id", "")) if s.get("vlan_id") else "", s.get("gateway", "") or "", s.get("site_id", "")[:8] if s.get("site_id") else "")
    console.print(table)


@ipam_app.command("add-subnet")
def add_subnet(
    network: str = typer.Argument(..., help="Network address (e.g. 192.168.1.0)"),
    prefix: int = typer.Argument(..., help="Prefix length (e.g. 24)"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    vlan: Optional[int] = typer.Option(None, "--vlan"),
    gateway: Optional[str] = typer.Option(None, "--gw"),
    site_id: Optional[str] = typer.Option(None, "--site"),
) -> None:
    """Add a subnet to IPAM."""
    from .app import run_async, get_db
    from operations.ipam import IPAMManager

    async def _run():
        db = await get_db()
        mgr = IPAMManager(db)
        return await mgr.add_subnet(network=network, prefix_length=prefix, name=name, vlan_id=vlan, gateway=gateway, site_id=site_id)

    subnet = run_async(_run())
    console.print(f"[green]Subnet added:[/green] {network}/{prefix} ({subnet['id'][:8]})")


@ipam_app.command("utilization")
def utilization(
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
) -> None:
    """Show IP utilization for a subnet."""
    from .app import run_async, get_db
    from operations.ipam import IPAMManager

    async def _run():
        db = await get_db()
        mgr = IPAMManager(db)
        return await mgr.get_utilization(subnet_id)

    util = run_async(_run())
    console.print(Panel(
        f"Total: {util.get('total', 0)}  Used: {util.get('used', 0)}  Free: {util.get('free', 0)}  ({util.get('percent_used', 0):.1f}%)",
        title=f"[bold]Subnet Utilization: {util.get('network', '')}[/bold]",
        border_style="cyan",
    ))


@ipam_app.command("find-free")
def find_free(
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    count: int = typer.Option(5, "--count", "-c"),
) -> None:
    """Find free IP addresses in a subnet."""
    from .app import run_async, get_db
    from operations.ipam import IPAMManager

    async def _run():
        db = await get_db()
        mgr = IPAMManager(db)
        return await mgr.find_free_ips(subnet_id, count=count)

    ips = run_async(_run())
    if not ips:
        console.print("[red]No free IPs available.[/red]")
        return
    for ip in ips:
        console.print(f"  [green]{ip}[/green]")


@ipam_app.command("scan")
def scan_subnet(
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
) -> None:
    """Scan a subnet via ARP/ping to discover active IPs."""
    from .app import run_async, get_db
    from operations.ipam import IPAMManager

    async def _run():
        db = await get_db()
        mgr = IPAMManager(db)
        return await mgr.scan_subnet(subnet_id)

    result = run_async(_run())
    console.print(f"[green]Scan complete:[/green] {result.get('found', 0)} active IPs discovered")
