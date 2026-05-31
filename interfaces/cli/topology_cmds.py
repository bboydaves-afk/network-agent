"""CLI commands for network topology."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

console = Console()
topology_app = typer.Typer(no_args_is_help=True)


@topology_app.command("discover")
def discover(
    device_id: Optional[str] = typer.Option(None, "--device", "-d", help="Specific device ID (all if omitted)"),
) -> None:
    """Discover network topology via CDP/LLDP."""
    from .app import run_async, get_db
    from operations.topology import TopologyMapper

    async def _run():
        db = await get_db()
        mapper = TopologyMapper(db)
        return await mapper.discover_topology(device_id=device_id)

    result = run_async(_run())
    console.print(f"[green]Discovery complete:[/green] {result.get('neighbors_found', 0)} neighbors found")


@topology_app.command("show")
def show() -> None:
    """Show current topology graph data."""
    from .app import run_async, get_db
    from operations.topology import TopologyMapper

    async def _run():
        db = await get_db()
        mapper = TopologyMapper(db)
        return await mapper.get_topology_graph()

    graph = run_async(_run())
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    console.print(f"[cyan]Topology:[/cyan] {len(nodes)} nodes, {len(edges)} links")

    if nodes:
        table = Table(title="Topology Nodes")
        table.add_column("ID", style="dim", max_width=8)
        table.add_column("Label", style="bold")
        table.add_column("IP")
        table.add_column("Type")
        for n in nodes:
            table.add_row(str(n.get("id", ""))[:8], n.get("label", ""), n.get("ip", ""), n.get("group", ""))
        console.print(table)


@topology_app.command("neighbors")
def neighbors(
    device_id: str = typer.Argument(..., help="Device ID"),
) -> None:
    """Show neighbors for a specific device."""
    from .app import run_async, get_db
    from operations.topology import TopologyMapper

    async def _run():
        db = await get_db()
        mapper = TopologyMapper(db)
        return await mapper.get_device_neighbors(device_id)

    nbrs = run_async(_run())
    if not nbrs:
        console.print("[dim]No neighbors found.[/dim]")
        return

    table = Table(title="Device Neighbors")
    table.add_column("Local Interface", style="bold")
    table.add_column("Neighbor Host")
    table.add_column("Neighbor IP")
    table.add_column("Neighbor Port")
    table.add_column("Protocol")
    for n in nbrs:
        table.add_row(
            n.get("local_interface", ""),
            n.get("neighbor_hostname", ""),
            n.get("neighbor_ip", ""),
            n.get("neighbor_port", ""),
            n.get("protocol", ""),
        )
    console.print(table)


@topology_app.command("snapshots")
def snapshots() -> None:
    """List saved topology snapshots."""
    from .app import run_async, get_db
    from operations.topology import TopologyMapper

    async def _run():
        db = await get_db()
        mapper = TopologyMapper(db)
        return await mapper.list_snapshots()

    snaps = run_async(_run())
    if not snaps:
        console.print("[dim]No snapshots found.[/dim]")
        return

    table = Table(title="Topology Snapshots")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Name", style="bold")
    table.add_column("Devices")
    table.add_column("Links")
    table.add_column("Created")
    for s in snaps:
        table.add_row(s["id"][:8], s.get("name", ""), str(s.get("device_count", 0)), str(s.get("link_count", 0)), s.get("created_at", ""))
    console.print(table)
