"""CLI commands for traffic analysis."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

console = Console()
traffic_app = typer.Typer(no_args_is_help=True)


@traffic_app.command("trends")
def trends(
    device_id: str = typer.Argument(..., help="Device ID"),
    hours: int = typer.Option(24, "--hours", "-h"),
) -> None:
    """Show traffic trends for a device."""
    from .app import run_async, get_db
    from operations.traffic import TrafficAnalyzer

    async def _run():
        db = await get_db()
        analyzer = TrafficAnalyzer(db)
        return await analyzer.get_traffic_trends(device_id, hours=hours)

    result = run_async(_run())
    interfaces = result.get("interfaces", [])
    if not interfaces:
        console.print("[dim]No traffic data available.[/dim]")
        return

    table = Table(title=f"Traffic Trends ({hours}h)")
    table.add_column("Interface", style="bold")
    table.add_column("Avg In (bps)")
    table.add_column("Avg Out (bps)")
    table.add_column("Peak In")
    table.add_column("Peak Out")
    for iface in interfaces:
        table.add_row(
            iface.get("interface", ""),
            str(iface.get("avg_in_bps", 0)),
            str(iface.get("avg_out_bps", 0)),
            str(iface.get("peak_in_bps", 0)),
            str(iface.get("peak_out_bps", 0)),
        )
    console.print(table)


@traffic_app.command("top-interfaces")
def top_interfaces(
    count: int = typer.Option(10, "--count", "-c"),
) -> None:
    """Show top interfaces by bandwidth usage."""
    from .app import run_async, get_db
    from operations.traffic import TrafficAnalyzer

    async def _run():
        db = await get_db()
        analyzer = TrafficAnalyzer(db)
        return await analyzer.get_top_talkers(count=count)

    talkers = run_async(_run())
    if not talkers:
        console.print("[dim]No traffic data available.[/dim]")
        return

    table = Table(title="Top Interfaces by Bandwidth")
    table.add_column("Device", style="bold")
    table.add_column("Interface")
    table.add_column("Total bps", style="cyan")
    for t in talkers:
        table.add_row(t.get("hostname", t.get("device_id", "")[:8]), t.get("interface", ""), str(t.get("total_bps", 0)))
    console.print(table)


@traffic_app.command("report")
def report(
    device_id: str = typer.Argument(..., help="Device ID"),
) -> None:
    """Generate bandwidth report for a device."""
    from .app import run_async, get_db
    from operations.traffic import TrafficAnalyzer

    async def _run():
        db = await get_db()
        analyzer = TrafficAnalyzer(db)
        return await analyzer.get_bandwidth_report(device_id)

    result = run_async(_run())
    console.print(f"[bold]Bandwidth Report: {result.get('hostname', device_id[:8])}[/bold]")
    console.print(f"  95th percentile in:  {result.get('p95_in', 0)} bps")
    console.print(f"  95th percentile out: {result.get('p95_out', 0)} bps")
    console.print(f"  Average utilization: {result.get('avg_utilization', 0):.1f}%")
