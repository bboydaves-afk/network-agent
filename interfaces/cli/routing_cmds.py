"""Routing management CLI commands."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()
routing_app = typer.Typer(no_args_is_help=True)


@routing_app.command("table")
def show_routing_table(
    device: str = typer.Argument(..., help="Device ID"),
    protocol: Optional[str] = typer.Option(None, help="Filter by protocol (static, ospf, connected, bgp)"),
):
    """Show the routing table for a device."""
    from .app import run_async, get_db
    from operations.routing import RoutingManager

    async def _run():
        db = await get_db()
        mgr = RoutingManager(db)
        return await mgr.get_routing_table(device, protocol)

    routes = run_async(_run())
    if not routes:
        console.print("[yellow]No routes found. Run 'routing sync' to pull from device.[/yellow]")
        return

    table = Table(title=f"Routing Table - {device}", box=box.ROUNDED)
    table.add_column("Destination", style="cyan")
    table.add_column("Next Hop")
    table.add_column("Protocol")
    table.add_column("AD/Metric", justify="right")
    table.add_column("Interface")
    table.add_column("VRF", style="dim")

    for r in routes:
        proto = r.get("protocol", "")
        proto_style = {
            "connected": "green", "static": "yellow",
            "ospf": "blue", "bgp": "magenta",
        }.get(proto, "white")
        table.add_row(
            f"{r.get('destination', '')}/{r.get('prefix_length', '')}",
            r.get("next_hop", "") or "direct",
            f"[{proto_style}]{proto}[/{proto_style}]",
            f"{r.get('admin_distance', '')}/{r.get('metric', '')}",
            r.get("interface", ""),
            r.get("vrf", "") or "",
        )
    console.print(table)


@routing_app.command("sync")
def sync_routes(device: str = typer.Argument(..., help="Device ID")):
    """Sync routing table from a live device to the database."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.routing import RoutingManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = RoutingManager(db, config_mgr)
        return await mgr.sync_routes(device)

    with console.status("Syncing routing table..."):
        result = run_async(_run())
    console.print(
        f"[green]Synced {result.get('routes_synced', 0)} routes[/green] "
        f"from {device} (vendor: {result.get('vendor', 'unknown')})"
    )


@routing_app.command("add-static")
def add_static_route(
    device: str = typer.Argument(..., help="Device ID"),
    destination: str = typer.Option(..., prompt=True, help="Destination network (e.g. 10.0.0.0)"),
    prefix_length: int = typer.Option(..., prompt=True, help="Prefix length (e.g. 24)"),
    next_hop: str = typer.Option(..., prompt=True, help="Next-hop IP address"),
    metric: int = typer.Option(0, help="Route metric"),
    vrf: Optional[str] = typer.Option(None, help="VRF name"),
):
    """Add a static route to a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.routing import RoutingManager

    if not typer.confirm(f"Add static route {destination}/{prefix_length} via {next_hop} on {device}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = RoutingManager(db, config_mgr)
        return await mgr.create_static_route(device, destination, prefix_length, next_hop, metric, vrf)

    with console.status("Adding static route..."):
        result = run_async(_run())
    console.print(f"[green]Static route created:[/green] {result.get('destination', '')} via {result.get('next_hop', '')}")


@routing_app.command("del-static")
def del_static_route(
    device: str = typer.Argument(..., help="Device ID"),
    destination: str = typer.Option(..., prompt=True, help="Destination network"),
    prefix_length: int = typer.Option(..., prompt=True, help="Prefix length"),
    next_hop: str = typer.Option(..., prompt=True, help="Next-hop IP"),
):
    """Delete a static route from a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.routing import RoutingManager

    if not typer.confirm(f"Delete static route {destination}/{prefix_length} via {next_hop}?"):
        raise typer.Abort()

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = RoutingManager(db, config_mgr)
        return await mgr.delete_static_route(device, destination, prefix_length, next_hop)

    with console.status("Deleting static route..."):
        result = run_async(_run())
    console.print(f"[green]Static route deleted:[/green] {result.get('destination', '')}")


@routing_app.command("ospf-status")
def ospf_status(device: str = typer.Argument(..., help="Device ID")):
    """Show OSPF status, neighbors, and areas for a device."""
    from .app import run_async, get_db
    from operations.routing import RoutingManager

    async def _run():
        db = await get_db()
        mgr = RoutingManager(db)
        return await mgr.get_ospf_status(device)

    status = run_async(_run())

    if not status.get("ospf_enabled"):
        console.print(f"[yellow]OSPF is not configured on {device}[/yellow]")
        return

    console.print(f"\n[bold]OSPF Status for {device}[/bold]")
    console.print(f"  Process ID: {status.get('process_id', '')}")
    console.print(f"  Router ID:  {status.get('router_id', 'not set')}")
    console.print(f"  Status:     {status.get('status', '')}")

    neighbors = status.get("neighbors", [])
    if neighbors:
        table = Table(title="OSPF Neighbors", box=box.ROUNDED)
        table.add_column("Neighbor ID")
        table.add_column("Neighbor IP")
        table.add_column("State")
        table.add_column("Interface")
        table.add_column("Area")
        table.add_column("Priority", justify="right")

        for n in neighbors:
            state = n.get("state", "")
            state_style = "green" if "FULL" in state.upper() else "yellow"
            table.add_row(
                n.get("neighbor_id", ""),
                n.get("neighbor_ip", ""),
                f"[{state_style}]{state}[/{state_style}]",
                n.get("interface", ""),
                n.get("area", ""),
                str(n.get("priority", "")),
            )
        console.print(table)
    else:
        console.print("  [dim]No OSPF neighbors[/dim]")


@routing_app.command("ospf-sync")
def ospf_sync(device: str = typer.Argument(..., help="Device ID")):
    """Sync OSPF neighbors from a live device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.config_manager import ConfigManager
    from operations.routing import RoutingManager

    async def _run():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        config_mgr = ConfigManager(db, cred_mgr)
        mgr = RoutingManager(db, config_mgr)
        return await mgr.sync_ospf(device)

    with console.status("Syncing OSPF neighbors..."):
        result = run_async(_run())
    console.print(f"[green]Synced {result.get('neighbors_synced', 0)} OSPF neighbors[/green]")
