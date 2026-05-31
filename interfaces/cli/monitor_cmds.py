"""Monitoring and metrics CLI commands."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.text import Text

console = Console()
err_console = Console(stderr=True)

monitor_app = typer.Typer(no_args_is_help=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity_style(severity: str) -> str:
    """Return Rich markup for alert severity."""
    mapping = {
        "critical": "[bold red]CRITICAL[/bold red]",
        "high": "[red]HIGH[/red]",
        "warning": "[yellow]WARNING[/yellow]",
        "medium": "[yellow]MEDIUM[/yellow]",
        "low": "[blue]LOW[/blue]",
        "info": "[dim]INFO[/dim]",
    }
    return mapping.get(severity.lower(), f"[dim]{severity.upper()}[/dim]")


def _render_bar(value: float, width: int = 20) -> Text:
    """Render a simple horizontal bar showing percentage."""
    filled = int(value / 100 * width)
    empty = width - filled

    if value >= 90:
        color = "red"
    elif value >= 70:
        color = "yellow"
    else:
        color = "green"

    bar = Text()
    bar.append("[" )
    bar.append("=" * filled, style=color)
    bar.append(" " * empty, style="dim")
    bar.append("]")
    bar.append(f" {value:.1f}%")
    return bar


# =====================================================================
# COMMANDS
# =====================================================================

@monitor_app.command("dashboard")
def dashboard() -> None:
    """Show a monitoring dashboard summary."""
    from .app import run_async, get_db, get_cred_manager
    from operations.monitor import MonitoringEngine

    async def _dashboard():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        engine = MonitoringEngine(db, cred_mgr)
        return await engine.get_dashboard_summary()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading dashboard...", total=None)
        try:
            summary = run_async(_dashboard())
        except Exception as exc:
            err_console.print(f"[red]Failed to load dashboard:[/red] {exc}")
            raise typer.Exit(code=1)

    # -- Device counts panel -----------------------------------------------
    total = summary.get("total_devices", 0)
    online = summary.get("online_devices", summary.get("online", 0))
    offline = summary.get("offline_devices", summary.get("offline", 0))
    degraded = summary.get("degraded_devices", summary.get("degraded", 0))

    device_table = Table(show_header=False, box=None, padding=(0, 2))
    device_table.add_column("Label", style="bold")
    device_table.add_column("Count")
    device_table.add_row("Total", str(total))
    device_table.add_row("Online", f"[green]{online}[/green]")
    device_table.add_row("Offline", f"[red]{offline}[/red]")
    device_table.add_row("Degraded", f"[yellow]{degraded}[/yellow]")

    console.print(Panel(device_table, title="[bold]Device Status[/bold]", border_style="cyan"))

    # -- Average metrics panel ---------------------------------------------
    avg_cpu = summary.get("avg_cpu", summary.get("average_cpu", 0.0))
    avg_mem = summary.get("avg_memory", summary.get("average_memory", 0.0))

    metrics_lines = []
    cpu_bar = _render_bar(avg_cpu)
    mem_bar = _render_bar(avg_mem)

    metrics_table = Table(show_header=False, box=None, padding=(0, 2))
    metrics_table.add_column("Metric", style="bold", width=12)
    metrics_table.add_column("Bar")
    metrics_table.add_row("Avg CPU", cpu_bar)
    metrics_table.add_row("Avg Memory", mem_bar)

    console.print(Panel(metrics_table, title="[bold]Fleet Averages[/bold]", border_style="blue"))

    # -- Top consumers panel -----------------------------------------------
    top_cpu = summary.get("top_cpu_devices", summary.get("top_cpu", []))
    top_mem = summary.get("top_memory_devices", summary.get("top_memory", []))

    if top_cpu or top_mem:
        top_table = Table(title_style="bold", padding=(0, 1))
        top_table.add_column("Device", style="bold")
        top_table.add_column("CPU %")
        top_table.add_column("Memory %")

        # Merge top lists
        seen = set()
        combined = []
        for item in top_cpu:
            name = item.get("hostname", item.get("device_id", "?"))
            if name not in seen:
                seen.add(name)
                combined.append(item)
        for item in top_mem:
            name = item.get("hostname", item.get("device_id", "?"))
            if name not in seen:
                seen.add(name)
                combined.append(item)

        for item in combined[:10]:
            name = item.get("hostname", item.get("device_id", "?"))
            cpu = item.get("cpu_percent", item.get("cpu", 0))
            mem = item.get("memory_percent", item.get("memory", 0))
            cpu_color = "red" if cpu >= 90 else ("yellow" if cpu >= 70 else "green")
            mem_color = "red" if mem >= 90 else ("yellow" if mem >= 70 else "green")
            top_table.add_row(name, f"[{cpu_color}]{cpu:.1f}[/{cpu_color}]", f"[{mem_color}]{mem:.1f}[/{mem_color}]")

        console.print(Panel(top_table, title="[bold]Top Resource Consumers[/bold]", border_style="yellow"))

    # -- Active alerts panel -----------------------------------------------
    active_alerts = summary.get("active_alerts", summary.get("alerts", []))
    alert_count = len(active_alerts) if isinstance(active_alerts, list) else active_alerts

    if isinstance(active_alerts, list) and active_alerts:
        alert_table = Table(padding=(0, 1))
        alert_table.add_column("Severity")
        alert_table.add_column("Device", style="bold")
        alert_table.add_column("Message")
        alert_table.add_column("Time", style="dim")

        for alert in active_alerts[:5]:
            alert_table.add_row(
                _severity_style(alert.get("severity", "info")),
                alert.get("device_hostname", alert.get("device_id", "?")[:8]),
                alert.get("message", "")[:60],
                str(alert.get("timestamp", alert.get("created_at", "")))[:19],
            )
        console.print(Panel(alert_table, title="[bold]Active Alerts[/bold]", border_style="red"))
        if isinstance(active_alerts, list) and len(active_alerts) > 5:
            console.print(f"[dim]...and {len(active_alerts) - 5} more. Run 'netagent monitor alerts' to see all.[/dim]")
    elif isinstance(alert_count, int) and alert_count > 0:
        console.print(Panel(
            f"[bold red]{alert_count} active alert(s)[/bold red]",
            title="[bold]Alerts[/bold]",
            border_style="red",
        ))
    else:
        console.print(Panel("[green]No active alerts[/green]", title="[bold]Alerts[/bold]", border_style="green"))

    console.print(f"\n[dim]Dashboard updated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC[/dim]")


@monitor_app.command("poll")
def poll_device(
    device_id: str = typer.Argument(..., help="Device ID to poll"),
) -> None:
    """Poll a single device and display health metrics."""
    from .app import run_async, get_db, get_cred_manager
    from operations.monitor import MonitoringEngine

    async def _poll():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, None

        cred_mgr = await get_cred_manager()
        engine = MonitoringEngine(db, cred_mgr)
        metrics = await engine.poll_device(device_id)
        return device, metrics

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Polling {device_id[:8]}...", total=None)
        device, metrics = run_async(_poll())

    if device is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    if not metrics:
        err_console.print(f"[yellow]No metrics returned for {hostname}.[/yellow]")
        return

    # Build metrics display
    cpu = metrics.get("cpu_percent", metrics.get("cpu", 0))
    mem = metrics.get("memory_percent", metrics.get("memory", 0))
    temp = metrics.get("temperature_celsius", metrics.get("temperature"))
    uptime = metrics.get("uptime", metrics.get("uptime_seconds", ""))
    disk = metrics.get("disk_percent", metrics.get("disk", 0))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold", width=16)
    table.add_column("Value")

    table.add_row("CPU", _render_bar(float(cpu)))
    table.add_row("Memory", _render_bar(float(mem)))
    if disk:
        table.add_row("Disk", _render_bar(float(disk)))
    if temp is not None:
        temp_color = "red" if temp >= 80 else ("yellow" if temp >= 60 else "green")
        table.add_row("Temperature", f"[{temp_color}]{temp:.1f} C[/{temp_color}]")
    if uptime:
        if isinstance(uptime, (int, float)):
            days = int(uptime // 86400)
            hours = int((uptime % 86400) // 3600)
            mins = int((uptime % 3600) // 60)
            uptime_str = f"{days}d {hours}h {mins}m"
        else:
            uptime_str = str(uptime)
        table.add_row("Uptime", uptime_str)

    table.add_row("Status", f"[green]{device.get('status', 'polled')}[/green]")
    table.add_row("Polled At", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))

    console.print(Panel(
        table,
        title=f"[bold]Health Metrics: {hostname}[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))


@monitor_app.command("poll-all")
def poll_all() -> None:
    """Poll all managed devices and show results."""
    from .app import run_async, get_db, get_cred_manager
    from operations.monitor import MonitoringEngine

    async def _get_devices():
        db = await get_db()
        return await db.get_all_devices()

    devices = run_async(_get_devices())

    if not devices:
        console.print("[yellow]No devices to poll.[/yellow]")
        return

    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Polling all devices...", total=len(devices))

        for device in devices:
            dev_id = device.get("id", "")
            hostname = device.get("hostname", dev_id[:8])
            progress.update(task, description=f"Polling {hostname}...")

            async def _poll_one(did=dev_id):
                db = await get_db()
                cred_mgr = await get_cred_manager()
                engine = MonitoringEngine(db, cred_mgr)
                return await engine.poll_device(did)

            try:
                metrics = run_async(_poll_one())
                results.append((hostname, "ok", metrics))
            except Exception as exc:
                results.append((hostname, "error", str(exc)))

            progress.advance(task)

    # Results table
    table = Table(title="Poll Results", title_style="bold cyan", padding=(0, 1))
    table.add_column("Device", style="bold")
    table.add_column("Status")
    table.add_column("CPU %")
    table.add_column("Memory %")
    table.add_column("Temperature")

    for hostname, status, data in results:
        if status == "ok" and isinstance(data, dict):
            cpu = data.get("cpu_percent", data.get("cpu", 0))
            mem = data.get("memory_percent", data.get("memory", 0))
            temp = data.get("temperature_celsius", data.get("temperature"))
            cpu_color = "red" if cpu >= 90 else ("yellow" if cpu >= 70 else "green")
            mem_color = "red" if mem >= 90 else ("yellow" if mem >= 70 else "green")
            temp_str = f"{temp:.1f} C" if temp is not None else "--"
            table.add_row(
                hostname,
                "[green]OK[/green]",
                f"[{cpu_color}]{cpu:.1f}[/{cpu_color}]",
                f"[{mem_color}]{mem:.1f}[/{mem_color}]",
                temp_str,
            )
        else:
            error_msg = str(data)[:40] if status == "error" else "No data"
            table.add_row(hostname, f"[red]ERROR[/red]", "--", "--", error_msg)

    console.print(table)
    ok_count = sum(1 for _, s, _ in results if s == "ok")
    console.print(f"\n[green]{ok_count}[/green]/{len(results)} devices polled successfully.")


@monitor_app.command("metrics")
def show_metrics(
    device_id: str = typer.Argument(..., help="Device ID"),
    metric: Optional[str] = typer.Option(None, "--metric", "-m", help="Filter by metric name (cpu, memory, disk, temperature)"),
    hours: int = typer.Option(24, "--hours", "-h", help="Hours of history to show"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max number of entries"),
) -> None:
    """Show recent metrics history for a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.monitor import MonitoringEngine

    async def _metrics():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, None

        cred_mgr = await get_cred_manager()
        engine = MonitoringEngine(db, cred_mgr)
        data = await engine.get_device_metrics(
            device_id,
            metric_name=metric,
            hours=hours,
            limit=limit,
        )
        return device, data

    device, data = run_async(_metrics())

    if device is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)

    if not data:
        console.print(f"[yellow]No metrics data for {hostname} in the last {hours} hours.[/yellow]")
        return

    table = Table(
        title=f"Metrics History: {hostname} (last {hours}h)",
        title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("Timestamp", style="dim")
    table.add_column("CPU %")
    table.add_column("Memory %")
    table.add_column("Disk %")
    table.add_column("Temp")

    for entry in data:
        ts = entry.get("timestamp", entry.get("collected_at", ""))
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                ts = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

        cpu = entry.get("cpu_percent", entry.get("cpu", ""))
        mem = entry.get("memory_percent", entry.get("memory", ""))
        disk = entry.get("disk_percent", entry.get("disk", ""))
        temp = entry.get("temperature_celsius", entry.get("temperature", ""))

        def _color_val(val):
            if val == "" or val is None:
                return "--"
            v = float(val)
            color = "red" if v >= 90 else ("yellow" if v >= 70 else "green")
            return f"[{color}]{v:.1f}[/{color}]"

        table.add_row(
            ts,
            _color_val(cpu),
            _color_val(mem),
            _color_val(disk),
            f"{float(temp):.1f} C" if temp not in ("", None) else "--",
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(data)} entries[/dim]")


@monitor_app.command("live")
def live_monitor(
    device_id: str = typer.Argument(..., help="Device ID to monitor"),
    interval: int = typer.Option(5, "--interval", "-i", help="Refresh interval in seconds"),
) -> None:
    """Live monitoring with auto-refresh (CPU/memory bars, Ctrl+C to stop)."""
    from .app import run_async, get_db, get_cred_manager
    from operations.monitor import MonitoringEngine

    async def _get_device():
        db = await get_db()
        return await db.get_device(device_id)

    device = run_async(_get_device())
    if not device:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)
    console.print(f"[bold]Live monitoring [cyan]{hostname}[/cyan] (refresh every {interval}s, Ctrl+C to stop)[/bold]\n")

    def _build_panel(metrics_data: dict | None, error: str | None = None) -> Panel:
        """Build a Rich Panel from metrics data."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        if error:
            return Panel(
                f"[red]Error:[/red] {error}\n\n[dim]Last attempt: {now}[/dim]",
                title=f"[bold]Live: {hostname}[/bold]",
                border_style="red",
            )

        if not metrics_data:
            return Panel(
                f"[dim]Waiting for data...[/dim]\n\n[dim]{now}[/dim]",
                title=f"[bold]Live: {hostname}[/bold]",
                border_style="yellow",
            )

        cpu = float(metrics_data.get("cpu_percent", metrics_data.get("cpu", 0)))
        mem = float(metrics_data.get("memory_percent", metrics_data.get("memory", 0)))
        disk = float(metrics_data.get("disk_percent", metrics_data.get("disk", 0)))
        temp = metrics_data.get("temperature_celsius", metrics_data.get("temperature"))
        uptime = metrics_data.get("uptime", metrics_data.get("uptime_seconds", ""))

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Metric", style="bold", width=14)
        table.add_column("Value")

        table.add_row("CPU", _render_bar(cpu, width=30))
        table.add_row("Memory", _render_bar(mem, width=30))
        if disk:
            table.add_row("Disk", _render_bar(disk, width=30))
        if temp is not None:
            t_color = "red" if temp >= 80 else ("yellow" if temp >= 60 else "green")
            table.add_row("Temperature", f"[{t_color}]{temp:.1f} C[/{t_color}]")
        if uptime:
            if isinstance(uptime, (int, float)):
                d = int(uptime // 86400)
                h = int((uptime % 86400) // 3600)
                m = int((uptime % 3600) // 60)
                table.add_row("Uptime", f"{d}d {h}h {m}m")
            else:
                table.add_row("Uptime", str(uptime))

        table.add_row("", "")
        table.add_row("Updated", f"[dim]{now}[/dim]")

        border = "green" if cpu < 90 and mem < 90 else "red"
        return Panel(table, title=f"[bold]Live: {hostname}[/bold]", border_style=border, padding=(1, 2))

    try:
        with Live(
            _build_panel(None),
            console=console,
            refresh_per_second=1,
            screen=False,
        ) as live:
            while True:
                async def _poll():
                    db = await get_db()
                    cred_mgr = await get_cred_manager()
                    engine = MonitoringEngine(db, cred_mgr)
                    return await engine.poll_device(device_id)

                try:
                    metrics = run_async(_poll())
                    live.update(_build_panel(metrics))
                except Exception as exc:
                    live.update(_build_panel(None, error=str(exc)))

                time.sleep(interval)

    except KeyboardInterrupt:
        console.print(f"\n[dim]Live monitoring stopped for {hostname}.[/dim]")


@monitor_app.command("alerts")
def list_alerts(
    severity: Optional[str] = typer.Option(None, "--severity", "-s", help="Filter by severity"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max alerts to show"),
) -> None:
    """List active alerts."""
    from .app import run_async, get_db

    async def _alerts():
        db = await get_db()
        return await db.get_active_alerts()

    try:
        alerts = run_async(_alerts())
    except Exception as exc:
        err_console.print(f"[red]Failed to fetch alerts:[/red] {exc}")
        raise typer.Exit(code=1)

    if severity:
        alerts = [a for a in alerts if a.get("severity", "").lower() == severity.lower()]

    if not alerts:
        console.print("[green]No active alerts.[/green]")
        return

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "high": 1, "warning": 2, "medium": 2, "low": 3, "info": 4}
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", "info").lower(), 5))

    table = Table(
        title="Active Alerts",
        title_style="bold red",
        padding=(0, 1),
    )
    table.add_column("Severity")
    table.add_column("Device", style="bold")
    table.add_column("Message")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_column("Threshold")
    table.add_column("Time", style="dim")

    for alert in alerts[:limit]:
        ts = alert.get("timestamp", alert.get("created_at", ""))
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                ts = dt.strftime("%m-%d %H:%M")
            except (ValueError, TypeError):
                pass

        table.add_row(
            _severity_style(alert.get("severity", "info")),
            alert.get("device_hostname", alert.get("device_id", "?")[:8]),
            alert.get("message", "")[:50],
            alert.get("metric_name", alert.get("metric", "")),
            str(alert.get("metric_value", alert.get("value", ""))),
            str(alert.get("threshold", "")),
            str(ts),
        )

    console.print(table)
    console.print(f"\n[dim]Showing {min(len(alerts), limit)} of {len(alerts)} active alert(s)[/dim]")
