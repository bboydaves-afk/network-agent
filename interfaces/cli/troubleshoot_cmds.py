"""Diagnostics and troubleshooting CLI commands."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

console = Console()
err_console = Console(stderr=True)

troubleshoot_app = typer.Typer(no_args_is_help=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_bar(value: float, width: int = 20) -> Text:
    """Render a percentage bar."""
    filled = int(value / 100 * width)
    empty = width - filled

    if value >= 90:
        color = "red"
    elif value >= 70:
        color = "yellow"
    else:
        color = "green"

    bar = Text()
    bar.append("[")
    bar.append("=" * filled, style=color)
    bar.append(" " * empty, style="dim")
    bar.append("]")
    bar.append(f" {value:.1f}%")
    return bar


# =====================================================================
# COMMANDS
# =====================================================================

@troubleshoot_app.command("ping")
def ping_test(
    target: str = typer.Argument(..., help="Target IP or hostname to ping"),
    count: int = typer.Option(4, "--count", "-c", help="Number of ping packets"),
    from_device: Optional[str] = typer.Option(
        None, "--from-device", "-f",
        help="Device ID to execute ping from (remote ping)",
    ),
) -> None:
    """Ping a target (locally or from a managed device)."""
    from .app import run_async, get_db, get_cred_manager
    from operations.troubleshoot import Troubleshooter

    async def _ping():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        ts = Troubleshooter(db, cred_mgr)
        return await ts.ping_test(target, count=count, device_id=from_device)

    source_label = f"from device {from_device[:8]}" if from_device else "local"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Pinging {target} ({source_label})...", total=None)
        try:
            result = run_async(_ping())
        except Exception as exc:
            err_console.print(f"[red]Ping failed:[/red] {exc}")
            raise typer.Exit(code=1)

    # Parse result
    sent = result.get("packets_sent", result.get("sent", count))
    received = result.get("packets_received", result.get("received", 0))
    loss = result.get("packet_loss", result.get("loss", 0))
    rtt_min = result.get("rtt_min", result.get("min_rtt", ""))
    rtt_avg = result.get("rtt_avg", result.get("avg_rtt", ""))
    rtt_max = result.get("rtt_max", result.get("max_rtt", ""))
    raw = result.get("raw_output", "")

    if isinstance(loss, float) and loss <= 1.0:
        loss_pct = loss * 100
    else:
        loss_pct = float(loss) if loss else 0

    # Status
    if loss_pct == 0:
        status = "[green]SUCCESS[/green]"
        border = "green"
    elif loss_pct == 100:
        status = "[red]FAILED (100% loss)[/red]"
        border = "red"
    else:
        status = f"[yellow]PARTIAL ({loss_pct:.0f}% loss)[/yellow]"
        border = "yellow"

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold", width=16)
    table.add_column("Value")

    table.add_row("Target", target)
    table.add_row("Source", source_label)
    table.add_row("Packets Sent", str(sent))
    table.add_row("Packets Received", str(received))
    table.add_row("Packet Loss", f"{loss_pct:.1f}%")

    if rtt_min != "":
        table.add_row("RTT Min", f"{float(rtt_min):.2f} ms")
    if rtt_avg != "":
        table.add_row("RTT Avg", f"{float(rtt_avg):.2f} ms")
    if rtt_max != "":
        table.add_row("RTT Max", f"{float(rtt_max):.2f} ms")

    table.add_row("Result", status)

    console.print(Panel(
        table,
        title=f"[bold]Ping Test: {target}[/bold]",
        border_style=border,
        padding=(1, 2),
    ))

    if raw:
        console.print(Panel(raw, title="[dim]Raw Output[/dim]", border_style="dim"))


@troubleshoot_app.command("trace")
def traceroute_test(
    target: str = typer.Argument(..., help="Target IP or hostname"),
    from_device: Optional[str] = typer.Option(
        None, "--from-device", "-f",
        help="Device ID to execute traceroute from",
    ),
) -> None:
    """Traceroute to a target (locally or from a managed device)."""
    from .app import run_async, get_db, get_cred_manager
    from operations.troubleshoot import Troubleshooter

    async def _trace():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        ts = Troubleshooter(db, cred_mgr)
        return await ts.traceroute_test(target, device_id=from_device)

    source_label = f"from device {from_device[:8]}" if from_device else "local"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Traceroute to {target} ({source_label})...", total=None)
        try:
            result = run_async(_trace())
        except Exception as exc:
            err_console.print(f"[red]Traceroute failed:[/red] {exc}")
            raise typer.Exit(code=1)

    hops = result.get("hops", [])
    raw = result.get("raw_output", "")

    if hops:
        table = Table(
            title=f"Traceroute to {target}",
            title_style="bold cyan",
            padding=(0, 1),
        )
        table.add_column("Hop", style="bold", width=5, justify="right")
        table.add_column("IP Address", style="cyan")
        table.add_column("Hostname")
        table.add_column("RTT 1", justify="right")
        table.add_column("RTT 2", justify="right")
        table.add_column("RTT 3", justify="right")

        for hop in hops:
            hop_num = str(hop.get("hop", hop.get("ttl", "")))
            ip = hop.get("ip_address", hop.get("ip", hop.get("address", "*")))
            hostname = hop.get("hostname", hop.get("host", ""))
            rtts = hop.get("rtts", hop.get("rtt", []))

            if isinstance(rtts, (list, tuple)):
                rtt_vals = []
                for r in rtts[:3]:
                    if r is None or r == "*":
                        rtt_vals.append("[dim]*[/dim]")
                    else:
                        rtt_vals.append(f"{float(r):.2f} ms")
                while len(rtt_vals) < 3:
                    rtt_vals.append("[dim]*[/dim]")
            elif isinstance(rtts, (int, float)):
                rtt_vals = [f"{float(rtts):.2f} ms", "[dim]*[/dim]", "[dim]*[/dim]"]
            else:
                rtt_vals = ["[dim]*[/dim]", "[dim]*[/dim]", "[dim]*[/dim]"]

            if ip == "*":
                ip = "[dim]*[/dim]"
                hostname = ""

            table.add_row(hop_num, ip, hostname, *rtt_vals)

        console.print(table)
        console.print(f"\n[dim]{len(hops)} hops traced.[/dim]")
    elif raw:
        console.print(Panel(
            raw,
            title=f"[bold]Traceroute to {target}[/bold]",
            border_style="cyan",
        ))
    else:
        console.print("[yellow]No traceroute results returned.[/yellow]")


@troubleshoot_app.command("port")
def port_check(
    target: str = typer.Argument(..., help="Target IP or hostname"),
    port: int = typer.Argument(..., help="TCP port number to check"),
    timeout: int = typer.Option(5, "--timeout", "-t", help="Connection timeout in seconds"),
) -> None:
    """Check if a TCP port is open on a target."""
    from .app import run_async, get_db, get_cred_manager
    from operations.troubleshoot import Troubleshooter

    async def _check():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        ts = Troubleshooter(db, cred_mgr)
        return await ts.port_check(target, port, timeout=timeout)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Checking {target}:{port}...", total=None)
        try:
            result = run_async(_check())
        except Exception as exc:
            err_console.print(f"[red]Port check failed:[/red] {exc}")
            raise typer.Exit(code=1)

    is_open = result.get("open", result.get("is_open", result.get("status") == "open"))
    response_time = result.get("response_time", result.get("rtt", ""))
    banner = result.get("banner", "")
    service = result.get("service", "")

    if is_open:
        status_display = "[green]OPEN[/green]"
        border = "green"
    else:
        status_display = "[red]CLOSED[/red]"
        border = "red"

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold", width=16)
    table.add_column("Value")

    table.add_row("Target", target)
    table.add_row("Port", str(port))
    table.add_row("Status", status_display)

    if response_time:
        table.add_row("Response Time", f"{float(response_time):.2f} ms")
    if service:
        table.add_row("Service", service)
    if banner:
        table.add_row("Banner", banner[:80])

    console.print(Panel(
        table,
        title=f"[bold]Port Check: {target}:{port}[/bold]",
        border_style=border,
        padding=(1, 2),
    ))


@troubleshoot_app.command("health")
def health_check(
    device_id: str = typer.Argument(..., help="Device ID to check"),
) -> None:
    """Comprehensive health check on a managed device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.troubleshoot import Troubleshooter

    async def _health():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, None

        cred_mgr = await get_cred_manager()
        ts = Troubleshooter(db, cred_mgr)
        result = await ts.check_device_health(device_id)
        return device, result

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Running health check on {device_id[:8]}...", total=None)
        device, result = run_async(_health())

    if device is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    if not result:
        err_console.print(f"[yellow]No health data returned for {hostname}.[/yellow]")
        return

    # Build comprehensive health panel
    cpu = float(result.get("cpu_percent", result.get("cpu", 0)))
    mem = float(result.get("memory_percent", result.get("memory", 0)))
    disk = float(result.get("disk_percent", result.get("disk", 0)))
    temp = result.get("temperature_celsius", result.get("temperature"))
    uptime = result.get("uptime", result.get("uptime_seconds", ""))
    warnings = result.get("warnings", [])
    errors = result.get("errors", [])
    interface_errors = result.get("interface_errors", result.get("interfaces_with_errors", []))

    # Overall status
    issues = []
    if cpu >= 90:
        issues.append("CPU critical")
    elif cpu >= 70:
        issues.append("CPU high")
    if mem >= 90:
        issues.append("Memory critical")
    elif mem >= 70:
        issues.append("Memory high")
    if temp is not None and temp >= 80:
        issues.append("Temperature critical")
    if errors:
        issues.extend(errors if isinstance(errors, list) else [str(errors)])

    if issues:
        overall = f"[red]ISSUES DETECTED[/red]: {', '.join(issues)}"
        border = "red"
    elif warnings:
        overall = f"[yellow]WARNINGS[/yellow]: {len(warnings)} warning(s)"
        border = "yellow"
    else:
        overall = "[green]HEALTHY[/green]"
        border = "green"

    # Main metrics table
    metrics_table = Table(show_header=False, box=None, padding=(0, 2))
    metrics_table.add_column("Metric", style="bold", width=18)
    metrics_table.add_column("Value")

    metrics_table.add_row("Overall Status", overall)
    metrics_table.add_row("CPU", _render_bar(cpu, width=25))
    metrics_table.add_row("Memory", _render_bar(mem, width=25))
    if disk:
        metrics_table.add_row("Disk", _render_bar(disk, width=25))
    if temp is not None:
        t_color = "red" if temp >= 80 else ("yellow" if temp >= 60 else "green")
        metrics_table.add_row("Temperature", f"[{t_color}]{temp:.1f} C[/{t_color}]")
    if uptime:
        if isinstance(uptime, (int, float)):
            d = int(uptime // 86400)
            h = int((uptime % 86400) // 3600)
            m = int((uptime % 3600) // 60)
            metrics_table.add_row("Uptime", f"{d}d {h}h {m}m")
        else:
            metrics_table.add_row("Uptime", str(uptime))

    console.print(Panel(
        metrics_table,
        title=f"[bold]Health Check: {hostname}[/bold]",
        border_style=border,
        padding=(1, 2),
    ))

    # Warnings
    if warnings:
        warn_lines = "\n".join(f"  [yellow]![/yellow] {w}" for w in warnings)
        console.print(Panel(warn_lines, title="[bold yellow]Warnings[/bold yellow]", border_style="yellow"))

    # Interface errors
    if interface_errors:
        intf_table = Table(
            title="Interfaces with Errors",
            title_style="bold red",
            padding=(0, 1),
        )
        intf_table.add_column("Interface", style="bold")
        intf_table.add_column("In Errors", style="red")
        intf_table.add_column("Out Errors", style="red")
        intf_table.add_column("In Discards", style="yellow")
        intf_table.add_column("Out Discards", style="yellow")

        for intf in interface_errors:
            if isinstance(intf, dict):
                intf_table.add_row(
                    intf.get("name", intf.get("interface", "")),
                    str(intf.get("in_errors", 0)),
                    str(intf.get("out_errors", 0)),
                    str(intf.get("in_discards", 0)),
                    str(intf.get("out_discards", 0)),
                )
            else:
                intf_table.add_row(str(intf), "--", "--", "--", "--")

        console.print(intf_table)


@troubleshoot_app.command("interfaces")
def show_interfaces(
    device_id: str = typer.Argument(..., help="Device ID"),
) -> None:
    """Show interface table for a device with status and error highlighting."""
    from .app import run_async, get_db, get_cred_manager
    from operations.troubleshoot import Troubleshooter

    async def _interfaces():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, None

        cred_mgr = await get_cred_manager()
        ts = Troubleshooter(db, cred_mgr)
        result = await ts.check_interface_errors(device_id)
        return device, result

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Fetching interfaces for {device_id[:8]}...", total=None)
        device, result = run_async(_interfaces())

    if device is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    # result can be a list of interface dicts or a dict with "interfaces" key
    interfaces = []
    if isinstance(result, list):
        interfaces = result
    elif isinstance(result, dict):
        interfaces = result.get("interfaces", result.get("data", []))

    if not interfaces:
        console.print(f"[yellow]No interface data returned for {hostname}.[/yellow]")
        return

    table = Table(
        title=f"Interfaces: {hostname}",
        title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("Interface", style="bold")
    table.add_column("Status")
    table.add_column("Protocol")
    table.add_column("IP Address")
    table.add_column("Speed")
    table.add_column("MTU", justify="right")
    table.add_column("In Err", justify="right")
    table.add_column("Out Err", justify="right")
    table.add_column("Description", style="dim", max_width=25)

    for intf in interfaces:
        if isinstance(intf, dict):
            name = intf.get("name", intf.get("interface", ""))
            status = intf.get("status", "unknown")
            proto = intf.get("protocol_status", intf.get("protocol", ""))
            ip_addr = intf.get("ip_address", "")
            speed = intf.get("speed", "")
            mtu = str(intf.get("mtu", "")) if intf.get("mtu") else ""
            in_err = intf.get("in_errors", 0)
            out_err = intf.get("out_errors", 0)
            desc = intf.get("description", "")

            # Color status
            if status.lower() == "up":
                status_display = "[green]up[/green]"
            elif "admin" in status.lower() or status.lower() == "administratively down":
                status_display = "[yellow]admin down[/yellow]"
            else:
                status_display = f"[red]{status}[/red]"

            # Color protocol
            if proto.lower() == "up":
                proto_display = "[green]up[/green]"
            elif proto:
                proto_display = f"[red]{proto}[/red]"
            else:
                proto_display = "[dim]--[/dim]"

            # Highlight errors in red if non-zero
            in_err_str = f"[red]{in_err}[/red]" if int(in_err or 0) > 0 else str(in_err or 0)
            out_err_str = f"[red]{out_err}[/red]" if int(out_err or 0) > 0 else str(out_err or 0)

            table.add_row(
                name,
                status_display,
                proto_display,
                ip_addr or "[dim]--[/dim]",
                speed or "[dim]--[/dim]",
                mtu or "--",
                in_err_str,
                out_err_str,
                desc[:25] if desc else "",
            )
        elif hasattr(intf, "to_dict"):
            # It's an InterfaceInfo dataclass
            d = intf.to_dict()
            name = d.get("name", "")
            status = d.get("status", "unknown")
            proto = d.get("protocol_status", "")
            ip_addr = d.get("ip_address", "")
            speed = d.get("speed", "")
            mtu = str(d.get("mtu", "")) if d.get("mtu") else ""
            in_err = d.get("in_errors", 0)
            out_err = d.get("out_errors", 0)
            desc = d.get("description", "")

            if status.lower() == "up":
                status_display = "[green]up[/green]"
            elif "admin" in status.lower():
                status_display = "[yellow]admin down[/yellow]"
            else:
                status_display = f"[red]{status}[/red]"

            proto_display = "[green]up[/green]" if proto.lower() == "up" else (f"[red]{proto}[/red]" if proto else "[dim]--[/dim]")
            in_err_str = f"[red]{in_err}[/red]" if int(in_err or 0) > 0 else str(in_err or 0)
            out_err_str = f"[red]{out_err}[/red]" if int(out_err or 0) > 0 else str(out_err or 0)

            table.add_row(
                name, status_display, proto_display,
                ip_addr or "[dim]--[/dim]", speed or "[dim]--[/dim]", mtu or "--",
                in_err_str, out_err_str, desc[:25] if desc else "",
            )

    console.print(table)
    console.print(f"\n[dim]{len(interfaces)} interface(s)[/dim]")

    # Highlight error summary
    total_in_errors = sum(
        int(i.get("in_errors", 0) if isinstance(i, dict) else getattr(i, "in_errors", 0))
        for i in interfaces
    )
    total_out_errors = sum(
        int(i.get("out_errors", 0) if isinstance(i, dict) else getattr(i, "out_errors", 0))
        for i in interfaces
    )
    if total_in_errors or total_out_errors:
        console.print(
            f"[red]Total errors: {total_in_errors} in, {total_out_errors} out[/red]"
        )


@troubleshoot_app.command("dns")
def dns_lookup(
    hostname: str = typer.Argument(..., help="Hostname to resolve"),
) -> None:
    """Perform DNS lookup for a hostname."""
    from .app import run_async, get_db, get_cred_manager
    from operations.troubleshoot import Troubleshooter

    async def _dns():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        ts = Troubleshooter(db, cred_mgr)
        return await ts.dns_lookup(hostname)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Resolving {hostname}...", total=None)
        try:
            result = run_async(_dns())
        except Exception as exc:
            err_console.print(f"[red]DNS lookup failed:[/red] {exc}")
            raise typer.Exit(code=1)

    # Result format may vary
    addresses = result.get("addresses", result.get("ips", result.get("results", [])))
    record_type = result.get("record_type", result.get("type", "A"))
    ttl = result.get("ttl", "")
    cname = result.get("cname", "")
    mx_records = result.get("mx_records", result.get("mx", []))
    ns_records = result.get("ns_records", result.get("ns", []))

    if not addresses and not cname and not mx_records and not ns_records:
        console.print(f"[yellow]No DNS records found for {hostname}.[/yellow]")
        return

    lines = [f"  [bold]Hostname:[/bold] {hostname}"]

    if cname:
        lines.append(f"  [bold]CNAME:[/bold]    {cname}")

    if addresses:
        table = Table(show_header=True, box=None, padding=(0, 2))
        table.add_column("Type", style="bold", width=8)
        table.add_column("Address", style="cyan")
        table.add_column("TTL", style="dim")

        for addr in addresses:
            if isinstance(addr, dict):
                table.add_row(
                    addr.get("type", record_type),
                    addr.get("address", addr.get("ip", "")),
                    str(addr.get("ttl", ttl)),
                )
            else:
                # Determine if IPv4 or IPv6
                addr_type = "AAAA" if ":" in str(addr) else "A"
                table.add_row(addr_type, str(addr), str(ttl) if ttl else "--")

        lines.append("")
        console.print(Panel(
            "\n".join(lines),
            title=f"[bold]DNS Lookup: {hostname}[/bold]",
            border_style="cyan",
        ))
        console.print(table)
    else:
        console.print(Panel(
            "\n".join(lines),
            title=f"[bold]DNS Lookup: {hostname}[/bold]",
            border_style="cyan",
        ))

    # MX records
    if mx_records:
        mx_table = Table(title="MX Records", title_style="bold", padding=(0, 1))
        mx_table.add_column("Priority", justify="right")
        mx_table.add_column("Mail Server", style="cyan")

        for mx in mx_records:
            if isinstance(mx, dict):
                mx_table.add_row(
                    str(mx.get("priority", mx.get("preference", ""))),
                    mx.get("host", mx.get("exchange", "")),
                )
            elif isinstance(mx, (list, tuple)) and len(mx) >= 2:
                mx_table.add_row(str(mx[0]), str(mx[1]))
            else:
                mx_table.add_row("--", str(mx))

        console.print(mx_table)

    # NS records
    if ns_records:
        ns_table = Table(title="NS Records", title_style="bold", padding=(0, 1))
        ns_table.add_column("Name Server", style="cyan")

        for ns in ns_records:
            if isinstance(ns, dict):
                ns_table.add_row(ns.get("host", ns.get("nameserver", str(ns))))
            else:
                ns_table.add_row(str(ns))

        console.print(ns_table)

    addr_count = len(addresses) if addresses else 0
    console.print(f"\n[dim]{addr_count} address(es) resolved.[/dim]")
