"""Network discovery CLI commands."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

discover_app = typer.Typer(no_args_is_help=True)


@discover_app.command("scan")
def scan_subnet(
    subnet: str = typer.Argument(..., help="Subnet to scan (e.g. 192.168.1.0/24)"),
    community: str = typer.Option("public", "--community", "-c", help="SNMP community string"),
    timeout: int = typer.Option(2, "--timeout", "-t", help="SNMP timeout in seconds"),
    port: int = typer.Option(161, "--port", "-p", help="SNMP port"),
) -> None:
    """Scan a subnet using SNMP to discover network devices."""
    from .app import run_async, get_db, get_cred_manager
    from operations.discovery import NetworkDiscovery

    console.print(f"[bold]Scanning subnet [cyan]{subnet}[/cyan] with SNMP community '{community}'...[/bold]\n")

    async def _scan():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        discovery = NetworkDiscovery(db, cred_mgr)
        return await discovery.scan_subnet(
            subnet,
            community=community,
            timeout=timeout,
            port=port,
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"SNMP scanning {subnet}...", total=None)
        try:
            results = run_async(_scan())
        except Exception as exc:
            err_console.print(f"[red]Scan failed:[/red] {exc}")
            raise typer.Exit(code=1)

    if not results:
        console.print("[yellow]No devices discovered on this subnet.[/yellow]")
        return

    table = Table(
        title=f"Discovered Devices on {subnet}",
        title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("IP Address", style="cyan")
    table.add_column("Hostname", style="bold")
    table.add_column("Vendor")
    table.add_column("Model")
    table.add_column("OS Version", style="dim")
    table.add_column("sysDescr", style="dim", max_width=40)

    for dev in results:
        table.add_row(
            dev.get("ip_address", dev.get("ip", "")),
            dev.get("hostname", dev.get("sysName", "")),
            dev.get("vendor", ""),
            dev.get("model", ""),
            dev.get("os_version", ""),
            (dev.get("sys_descr", dev.get("sysDescr", "")) or "")[:40],
        )

    console.print(table)
    console.print(f"\n[green]{len(results)} device(s) discovered.[/green]")


@discover_app.command("ping-sweep")
def ping_sweep(
    subnet: str = typer.Argument(..., help="Subnet to sweep (e.g. 192.168.1.0/24)"),
    timeout: int = typer.Option(1, "--timeout", "-t", help="Ping timeout in seconds"),
    count: int = typer.Option(1, "--count", "-c", help="Ping count per host"),
) -> None:
    """Ping sweep a subnet to find responding hosts."""
    from .app import run_async, get_db, get_cred_manager
    from operations.discovery import NetworkDiscovery

    console.print(f"[bold]Ping sweeping [cyan]{subnet}[/cyan]...[/bold]\n")

    async def _sweep():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        discovery = NetworkDiscovery(db, cred_mgr)
        return await discovery.ping_sweep(subnet, timeout=timeout, count=count)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Sweeping {subnet}...", total=None)
        try:
            results = run_async(_sweep())
        except Exception as exc:
            err_console.print(f"[red]Ping sweep failed:[/red] {exc}")
            raise typer.Exit(code=1)

    if not results:
        console.print("[yellow]No hosts responded.[/yellow]")
        return

    # Results may be a list of dicts or a list of IP strings
    table = Table(
        title=f"Responding Hosts on {subnet}",
        title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=5)
    table.add_column("IP Address", style="cyan")
    table.add_column("Response Time", style="green")
    table.add_column("Status")

    for idx, item in enumerate(results, 1):
        if isinstance(item, dict):
            ip = item.get("ip_address", item.get("ip", ""))
            rtt = item.get("rtt", item.get("response_time", ""))
            status = item.get("status", "alive")
            rtt_display = f"{float(rtt):.2f} ms" if rtt else "--"
        else:
            ip = str(item)
            rtt_display = "--"
            status = "alive"

        status_display = "[green]alive[/green]" if status == "alive" else f"[dim]{status}[/dim]"
        table.add_row(str(idx), ip, rtt_display, status_display)

    console.print(table)
    console.print(f"\n[green]{len(results)} host(s) responded.[/green]")


@discover_app.command("auto-add")
def auto_add(
    subnet: str = typer.Argument(..., help="Subnet to scan and auto-add (e.g. 192.168.1.0/24)"),
    community: str = typer.Option("public", "--community", "-c", help="SNMP community string"),
    credential_id: Optional[str] = typer.Option(None, "--cred", help="Credential ID to assign to discovered devices"),
    timeout: int = typer.Option(2, "--timeout", "-t", help="SNMP timeout in seconds"),
) -> None:
    """Discover devices on a subnet and automatically add them to inventory."""
    from .app import run_async, get_db, get_cred_manager
    from operations.discovery import NetworkDiscovery

    console.print(f"[bold]Auto-discovering and adding devices on [cyan]{subnet}[/cyan]...[/bold]\n")

    async def _auto():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        discovery = NetworkDiscovery(db, cred_mgr)
        return await discovery.auto_discover_and_add(
            subnet,
            community=community,
            credential_id=credential_id,
            timeout=timeout,
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Discovering and adding on {subnet}...", total=None)
        try:
            results = run_async(_auto())
        except Exception as exc:
            err_console.print(f"[red]Auto-add failed:[/red] {exc}")
            raise typer.Exit(code=1)

    if not results:
        console.print("[yellow]No new devices discovered or added.[/yellow]")
        return

    # Results expected to be a list of dicts with added device info
    added = results if isinstance(results, list) else [results]

    table = Table(
        title="Auto-Added Devices",
        title_style="bold green",
        padding=(0, 1),
    )
    table.add_column("ID", style="dim", max_width=10)
    table.add_column("Hostname", style="bold")
    table.add_column("IP Address", style="cyan")
    table.add_column("Type")
    table.add_column("Vendor")
    table.add_column("Status")

    for dev in added:
        dev_id = dev.get("id", dev.get("device_id", ""))
        hostname = dev.get("hostname", "")
        ip = dev.get("ip_address", dev.get("ip", ""))
        dev_type = dev.get("device_type", "")
        vendor = dev.get("vendor", "")
        status = dev.get("status", "added")

        truncated_id = dev_id[:10] if len(str(dev_id)) > 10 else dev_id

        if status in ("added", "new"):
            status_display = "[green]Added[/green]"
        elif status in ("existing", "skipped"):
            status_display = "[yellow]Skipped (exists)[/yellow]"
        else:
            status_display = f"[dim]{status}[/dim]"

        table.add_row(truncated_id, hostname, ip, dev_type, vendor, status_display)

    console.print(table)

    new_count = sum(
        1 for d in added
        if d.get("status", "added") in ("added", "new")
    )
    skip_count = len(added) - new_count
    summary_parts = [f"[green]{new_count} device(s) added[/green]"]
    if skip_count > 0:
        summary_parts.append(f"[yellow]{skip_count} skipped[/yellow]")
    console.print("\n" + ", ".join(summary_parts) + ".")

    if credential_id:
        console.print(f"[dim]Credential {credential_id[:8]} assigned to all new devices.[/dim]")
