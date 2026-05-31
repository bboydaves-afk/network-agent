"""CLI commands for site management."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
site_app = typer.Typer(no_args_is_help=True)


@site_app.command("list")
def list_sites(
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Filter by region"),
) -> None:
    """List all sites."""
    from .app import run_async, get_db
    from operations.sites import SiteManager

    async def _run():
        db = await get_db()
        mgr = SiteManager(db)
        return await mgr.list_sites(region=region)

    sites = run_async(_run())

    if not sites:
        console.print("[dim]No sites found.[/dim]")
        return

    table = Table(title="Sites", show_lines=False)
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Name", style="bold cyan")
    table.add_column("Location")
    table.add_column("Region")
    table.add_column("Contact")

    for s in sites:
        table.add_row(
            s["id"][:8],
            s.get("name", ""),
            s.get("location", "") or "",
            s.get("region", "") or "",
            s.get("contact", "") or "",
        )

    console.print(table)


@site_app.command("add")
def add_site(
    name: str = typer.Argument(..., help="Site name"),
    location: Optional[str] = typer.Option(None, "--location", "-l"),
    region: Optional[str] = typer.Option(None, "--region", "-r"),
    description: Optional[str] = typer.Option(None, "--desc", "-d"),
    contact: Optional[str] = typer.Option(None, "--contact", "-c"),
) -> None:
    """Create a new site."""
    from .app import run_async, get_db
    from operations.sites import SiteManager

    async def _run():
        db = await get_db()
        mgr = SiteManager(db)
        return await mgr.create_site(name, location, region, description, contact)

    site = run_async(_run())
    console.print(f"[green]Site created:[/green] {site['name']} ({site['id'][:8]})")


@site_app.command("delete")
def delete_site(
    site_id: str = typer.Argument(..., help="Site ID"),
) -> None:
    """Delete a site."""
    from .app import run_async, get_db
    from operations.sites import SiteManager

    async def _run():
        db = await get_db()
        mgr = SiteManager(db)
        return await mgr.delete_site(site_id)

    deleted = run_async(_run())
    if deleted:
        console.print("[green]Site deleted.[/green]")
    else:
        console.print("[red]Site not found.[/red]")


@site_app.command("devices")
def site_devices(
    site_id: str = typer.Argument(..., help="Site ID"),
) -> None:
    """List devices assigned to a site."""
    from .app import run_async, get_db
    from operations.sites import SiteManager

    async def _run():
        db = await get_db()
        mgr = SiteManager(db)
        return await mgr.get_site_devices(site_id)

    devices = run_async(_run())

    if not devices:
        console.print("[dim]No devices in this site.[/dim]")
        return

    table = Table(title="Site Devices")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Hostname", style="bold")
    table.add_column("IP Address")
    table.add_column("Type")
    table.add_column("Status")

    for d in devices:
        status = d.get("status", "unknown")
        style = {"online": "green", "offline": "red", "degraded": "yellow"}.get(status, "dim")
        table.add_row(
            d["id"][:8],
            d.get("hostname", ""),
            d.get("ip_address", ""),
            d.get("device_type", ""),
            f"[{style}]{status}[/{style}]",
        )

    console.print(table)


@site_app.command("summary")
def site_summary(
    site_id: str = typer.Argument(..., help="Site ID"),
) -> None:
    """Show summary for a site."""
    from .app import run_async, get_db
    from operations.sites import SiteManager

    async def _run():
        db = await get_db()
        mgr = SiteManager(db)
        return await mgr.get_site_summary(site_id)

    summary = run_async(_run())

    if summary.get("error"):
        console.print(f"[red]{summary['error']}[/red]")
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("Name", summary.get("name", ""))
    table.add_row("Location", summary.get("location", "") or "")
    table.add_row("Region", summary.get("region", "") or "")
    table.add_row("Total Devices", str(summary.get("total_devices", 0)))
    table.add_row("Online", f"[green]{summary.get('online', 0)}[/green]")
    table.add_row("Offline", f"[red]{summary.get('offline', 0)}[/red]")
    table.add_row("Degraded", f"[yellow]{summary.get('degraded', 0)}[/yellow]")

    console.print(Panel(table, title=f"[bold]Site: {summary.get('name', '')}[/bold]", border_style="cyan"))


@site_app.command("assign")
def assign_device(
    device_id: str = typer.Argument(..., help="Device ID"),
    site_id: str = typer.Argument(..., help="Site ID (or 'none' to unassign)"),
) -> None:
    """Assign a device to a site."""
    from .app import run_async, get_db
    from operations.sites import SiteManager

    actual_site_id = None if site_id.lower() == "none" else site_id

    async def _run():
        db = await get_db()
        mgr = SiteManager(db)
        return await mgr.assign_device_to_site(device_id, actual_site_id)

    updated = run_async(_run())
    if updated:
        if actual_site_id:
            console.print(f"[green]Device assigned to site {site_id[:8]}.[/green]")
        else:
            console.print("[green]Device unassigned from site.[/green]")
    else:
        console.print("[red]Device not found.[/red]")
