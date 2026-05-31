"""CLI commands for firmware management."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

console = Console()
firmware_app = typer.Typer(no_args_is_help=True)


@firmware_app.command("status")
def firmware_status() -> None:
    """Show firmware compliance status across all devices."""
    from .app import run_async, get_db
    from operations.firmware import FirmwareManager

    async def _run():
        db = await get_db()
        mgr = FirmwareManager(db)
        return await mgr.check_compliance()

    result = run_async(_run())
    compliant = result.get("compliant", [])
    non_compliant = result.get("non_compliant", [])
    unknown = result.get("unknown", [])
    console.print(f"[green]Compliant: {len(compliant)}[/green]  [red]Non-compliant: {len(non_compliant)}[/red]  [dim]Unknown: {len(unknown)}[/dim]")

    if non_compliant:
        table = Table(title="Non-Compliant Devices")
        table.add_column("Hostname", style="bold")
        table.add_column("Current Version", style="red")
        table.add_column("Recommended")
        for d in non_compliant:
            table.add_row(d.get("hostname", ""), d.get("current_version", ""), d.get("recommended_version", ""))
        console.print(table)


@firmware_app.command("catalog")
def catalog() -> None:
    """List firmware catalog entries."""
    from .app import run_async, get_db
    from operations.firmware import FirmwareManager

    async def _run():
        db = await get_db()
        mgr = FirmwareManager(db)
        return await mgr.list_catalog()

    entries = run_async(_run())
    if not entries:
        console.print("[dim]Firmware catalog is empty.[/dim]")
        return

    table = Table(title="Firmware Catalog")
    table.add_column("Vendor", style="bold")
    table.add_column("Model Pattern")
    table.add_column("Version", style="cyan")
    table.add_column("EOL Date")
    table.add_column("Recommended")
    for e in entries:
        rec = "[green]Yes[/green]" if e.get("is_recommended") else ""
        table.add_row(e.get("vendor", ""), e.get("model_pattern", "") or "*", e.get("version", ""), e.get("eol_date", "") or "", rec)
    console.print(table)


@firmware_app.command("add")
def add_firmware(
    vendor: str = typer.Argument(..., help="Vendor name"),
    version: str = typer.Argument(..., help="Firmware version"),
    model_pattern: Optional[str] = typer.Option(None, "--model", "-m"),
    eol_date: Optional[str] = typer.Option(None, "--eol"),
    recommended: bool = typer.Option(False, "--recommended/--no-recommended"),
) -> None:
    """Add a firmware entry to the catalog."""
    from .app import run_async, get_db
    from operations.firmware import FirmwareManager

    async def _run():
        db = await get_db()
        mgr = FirmwareManager(db)
        return await mgr.add_catalog_entry(vendor=vendor, version=version, model_pattern=model_pattern, eol_date=eol_date, is_recommended=recommended)

    entry = run_async(_run())
    console.print(f"[green]Firmware entry added:[/green] {vendor} {version} ({entry['id'][:8]})")


@firmware_app.command("eol")
def eol_devices() -> None:
    """List devices running EOL firmware."""
    from .app import run_async, get_db
    from operations.firmware import FirmwareManager

    async def _run():
        db = await get_db()
        mgr = FirmwareManager(db)
        return await mgr.get_eol_devices()

    devices = run_async(_run())
    if not devices:
        console.print("[green]No devices running EOL firmware.[/green]")
        return

    table = Table(title="EOL Firmware Devices")
    table.add_column("Hostname", style="bold")
    table.add_column("Version", style="red")
    table.add_column("EOL Date")
    for d in devices:
        table.add_row(d.get("hostname", ""), d.get("os_version", ""), d.get("eol_date", ""))
    console.print(table)
