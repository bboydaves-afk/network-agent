"""Serial console management CLI commands."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

serial_app = typer.Typer(no_args_is_help=True)


@serial_app.command("ports")
def list_ports() -> None:
    """List available serial ports on this machine."""
    from .app import run_async, get_db, get_cred_manager
    from operations.serial_console import SerialConsoleManager

    async def _list():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = SerialConsoleManager(db, cred_mgr)
        return await mgr.list_serial_ports()

    ports = run_async(_list())

    if not ports:
        console.print("[yellow]No serial ports found.[/yellow]")
        return

    table = Table(title="Available Serial Ports", title_style="bold cyan")
    table.add_column("Device", style="bold")
    table.add_column("Description")
    table.add_column("Manufacturer")
    table.add_column("HWID", style="dim")

    for p in ports:
        table.add_row(
            p["device"],
            p.get("description", ""),
            p.get("manufacturer") or "",
            p.get("hwid", ""),
        )

    console.print(table)


@serial_app.command("connect")
def connect_serial(
    device_id: str = typer.Argument(..., help="Device ID to connect to via serial"),
) -> None:
    """Open a serial console session to a device."""
    from .app import run_async, get_db, get_cred_manager
    from operations.serial_console import SerialConsoleManager

    async def _connect():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = SerialConsoleManager(db, cred_mgr)
        return await mgr.connect_serial(device_id)

    try:
        result = run_async(_connect())
        console.print(Panel(
            f"[green]Connected[/green] to {device_id}\n"
            f"Port: {result.get('serial_port')}\n"
            f"Baudrate: {result.get('baudrate')}",
            title="Serial Connection",
            border_style="green",
        ))
    except Exception as exc:
        err_console.print(f"[red]Connection failed:[/red] {exc}")
        raise typer.Exit(code=1)


@serial_app.command("disconnect")
def disconnect_serial(
    device_id: str = typer.Argument(..., help="Device ID to disconnect"),
) -> None:
    """Close an active serial console session."""
    from .app import run_async, get_db, get_cred_manager
    from operations.serial_console import SerialConsoleManager

    async def _disconnect():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = SerialConsoleManager(db, cred_mgr)
        return await mgr.disconnect_serial(device_id)

    result = run_async(_disconnect())
    status = result.get("status", "unknown")
    if status == "disconnected":
        console.print(f"[green]Disconnected[/green] from {device_id}")
    else:
        console.print(f"[yellow]{status}[/yellow] — {device_id}")


@serial_app.command("send")
def send_command(
    device_id: str = typer.Argument(..., help="Device ID"),
    command: str = typer.Argument(..., help="Command to send"),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Command timeout (seconds)"),
) -> None:
    """Send a command to a device via serial console."""
    from .app import run_async, get_db, get_cred_manager
    from operations.serial_console import SerialConsoleManager

    async def _send():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = SerialConsoleManager(db, cred_mgr)
        await mgr.connect_serial(device_id)
        return await mgr.send_command(device_id, command, timeout=timeout)

    try:
        result = run_async(_send())
        console.print(Panel(
            result.get("output", ""),
            title=f"[bold]{command}[/bold]",
            border_style="cyan",
        ))
    except Exception as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)


@serial_app.command("break")
def send_break(
    device_id: str = typer.Argument(..., help="Device ID"),
    duration: float = typer.Option(0.5, "--duration", "-d", help="Break duration in seconds"),
) -> None:
    """Send a serial break signal (for password recovery)."""
    from .app import run_async, get_db, get_cred_manager
    from operations.serial_console import SerialConsoleManager

    async def _break():
        db = await get_db()
        cred_mgr = await get_cred_manager()
        mgr = SerialConsoleManager(db, cred_mgr)
        return await mgr.send_break(device_id, duration=duration)

    try:
        result = run_async(_break())
        console.print(f"[yellow]Break signal sent[/yellow] (duration={duration}s)")
        if result.get("output"):
            console.print(Panel(result["output"], title="Response", border_style="yellow"))
    except Exception as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)


@serial_app.command("config")
def serial_config(
    device_id: str = typer.Argument(..., help="Device ID"),
    baudrate: Optional[int] = typer.Option(None, "--baudrate", "-b", help="Set baudrate"),
    serial_port: Optional[str] = typer.Option(None, "--port", "-p", help="Set serial port path"),
    parity: Optional[str] = typer.Option(None, "--parity", help="Set parity (N/E/O)"),
    stopbits: Optional[float] = typer.Option(None, "--stopbits", help="Set stop bits (1/1.5/2)"),
) -> None:
    """View or update serial configuration for a device."""
    from .app import run_async, get_db

    async def _config():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            err_console.print(f"[red]Device {device_id} not found[/red]")
            raise typer.Exit(code=1)

        metadata = device.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}

        changed = False
        if baudrate is not None:
            metadata["baudrate"] = baudrate
            changed = True
        if serial_port is not None:
            metadata["serial_port"] = serial_port
            changed = True
        if parity is not None:
            metadata["parity"] = parity
            changed = True
        if stopbits is not None:
            metadata["stopbits"] = stopbits
            changed = True

        if changed:
            await db.update_device(device_id, {"metadata": json.dumps(metadata)})
            console.print("[green]Serial configuration updated.[/green]")

        table = Table(title=f"Serial Config: {device_id}", title_style="bold cyan")
        table.add_column("Parameter", style="bold")
        table.add_column("Value")
        table.add_row("serial_port", str(metadata.get("serial_port", "(not set)")))
        table.add_row("baudrate", str(metadata.get("baudrate", 9600)))
        table.add_row("bytesize", str(metadata.get("bytesize", 8)))
        table.add_row("parity", str(metadata.get("parity", "N")))
        table.add_row("stopbits", str(metadata.get("stopbits", 1)))
        table.add_row("xonxoff", str(metadata.get("xonxoff", False)))
        table.add_row("rtscts", str(metadata.get("rtscts", False)))
        console.print(table)

    run_async(_config())
