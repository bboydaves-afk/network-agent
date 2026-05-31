"""Main CLI application with Typer."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Optional

import yaml
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.database import Database
from core.credentials import CredentialManager

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="netagent",
    help="Network Engineer Agent - Manage and monitor your network infrastructure",
    rich_markup_mode="rich",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Global shared state
# ---------------------------------------------------------------------------

class AppState:
    """Holds lazily-initialised shared resources."""

    db: Optional[Database] = None
    cred_manager: Optional[CredentialManager] = None
    config: dict = {}


state = AppState()


# ---------------------------------------------------------------------------
# Config / resource helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.yaml from the project root and cache it on *state*."""
    if state.config:
        return state.config
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config.yaml",
    )
    try:
        with open(config_path) as fh:
            state.config = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        err_console.print(
            f"[red]Config file not found:[/red] {config_path}\n"
            "Create config.yaml in the project root or set NETAGENT_CONFIG."
        )
        raise typer.Exit(code=1)
    return state.config


async def get_db() -> Database:
    """Return the shared Database instance, creating it on first call."""
    if state.db is None:
        cfg = load_config()
        db_path = cfg.get("database", {}).get("path", "./data/network_agent.db")
        state.db = Database(db_path)
        await state.db.initialize()
    return state.db


async def get_cred_manager() -> CredentialManager:
    """Return the shared CredentialManager, creating it on first call."""
    if state.cred_manager is None:
        db = await get_db()
        encryption_key = os.environ.get("NETAGENT_ENCRYPTION_KEY")
        state.cred_manager = CredentialManager(db, encryption_key)
    return state.cred_manager


def run_async(coro):
    """Run an async coroutine from synchronous Typer command code.

    Uses the running loop if one exists, otherwise creates a new one.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already inside an event loop (e.g. Jupyter) -- create a task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Register sub-command groups
# ---------------------------------------------------------------------------

from .device_cmds import device_app  # noqa: E402
from .config_cmds import config_app  # noqa: E402
from .monitor_cmds import monitor_app  # noqa: E402
from .discover_cmds import discover_app  # noqa: E402
from .troubleshoot_cmds import troubleshoot_app  # noqa: E402
from .site_cmds import site_app  # noqa: E402
from .topology_cmds import topology_app  # noqa: E402
from .firmware_cmds import firmware_app  # noqa: E402
from .ipam_cmds import ipam_app  # noqa: E402
from .traffic_cmds import traffic_app  # noqa: E402
from .syslog_cmds import syslog_app  # noqa: E402
from .compliance_cmds import compliance_app  # noqa: E402
from .change_cmds import change_app  # noqa: E402
from .rotation_cmds import rotation_app  # noqa: E402
from .firewall_cmds import firewall_app  # noqa: E402
from .vlan_cmds import vlan_app  # noqa: E402
from .routing_cmds import routing_app  # noqa: E402
from .acl_cmds import acl_app  # noqa: E402
from .serial_cmds import serial_app  # noqa: E402

app.add_typer(device_app, name="devices", help="Device inventory management")
app.add_typer(config_app, name="config", help="Configuration management")
app.add_typer(monitor_app, name="monitor", help="Monitoring and metrics")
app.add_typer(discover_app, name="discover", help="Network discovery")
app.add_typer(troubleshoot_app, name="diag", help="Diagnostics and troubleshooting")
app.add_typer(site_app, name="sites", help="Site management")
app.add_typer(topology_app, name="topology", help="Network topology mapping")
app.add_typer(firmware_app, name="firmware", help="Firmware/OS management")
app.add_typer(ipam_app, name="ipam", help="IP address management")
app.add_typer(traffic_app, name="traffic", help="Traffic analysis")
app.add_typer(syslog_app, name="syslog", help="Syslog management")
app.add_typer(compliance_app, name="compliance", help="Compliance reporting")
app.add_typer(change_app, name="change", help="Change management")
app.add_typer(rotation_app, name="creds", help="Credential rotation")
app.add_typer(firewall_app, name="firewall", help="Firewall policy & rule management")
app.add_typer(vlan_app, name="vlans", help="VLAN management")
app.add_typer(routing_app, name="routing", help="Routing & OSPF management")
app.add_typer(acl_app, name="acls", help="ACL management")
app.add_typer(serial_app, name="serial", help="Serial console management")


# ---------------------------------------------------------------------------
# Root-level commands
# ---------------------------------------------------------------------------

@app.callback()
def main() -> None:
    """Network Engineer Agent -- Your AI-powered network operations assistant."""


@app.command()
def status() -> None:
    """Show agent status and summary dashboard."""

    cfg = load_config()

    # -- Banner -----------------------------------------------------------
    banner_text = Text()
    banner_text.append("NetworkAgent v1.0", style="bold cyan")
    banner_text.append("\nAI-Powered Network Operations Assistant", style="dim")
    console.print(Panel(banner_text, border_style="cyan", padding=(1, 2)))

    # -- Database status --------------------------------------------------
    db_path = cfg.get("database", {}).get("path", "./data/network_agent.db")
    abs_db_path = os.path.abspath(db_path)
    db_exists = os.path.isfile(abs_db_path)

    info_table = Table(show_header=False, box=None, padding=(0, 2))
    info_table.add_column("Key", style="bold")
    info_table.add_column("Value")

    info_table.add_row("Database", abs_db_path)
    info_table.add_row(
        "DB Status",
        "[green]Connected[/green]" if db_exists else "[yellow]Not initialised[/yellow]",
    )
    info_table.add_row("Config", os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.yaml",
        )
    ))
    info_table.add_row(
        "Encryption Key",
        "[green]Set[/green]" if os.environ.get("NETAGENT_ENCRYPTION_KEY") else "[red]Not set[/red]",
    )
    info_table.add_row("Timestamp", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))

    console.print(Panel(info_table, title="[bold]System Information[/bold]", border_style="blue"))

    # -- Device summary (requires DB) ------------------------------------
    if db_exists:
        try:
            async def _summary():
                db = await get_db()
                devices = await db.list_devices()
                return devices

            devices = run_async(_summary())

            total = len(devices)
            online = sum(1 for d in devices if d.get("status") == "online")
            offline = sum(1 for d in devices if d.get("status") == "offline")
            degraded = sum(1 for d in devices if d.get("status") == "degraded")
            unknown = total - online - offline - degraded

            summary_table = Table(show_header=False, box=None, padding=(0, 2))
            summary_table.add_column("Key", style="bold")
            summary_table.add_column("Value")
            summary_table.add_row("Total Devices", str(total))
            summary_table.add_row("Online", f"[green]{online}[/green]")
            summary_table.add_row("Offline", f"[red]{offline}[/red]")
            summary_table.add_row("Degraded", f"[yellow]{degraded}[/yellow]")
            if unknown:
                summary_table.add_row("Unknown", f"[dim]{unknown}[/dim]")

            console.print(Panel(
                summary_table,
                title="[bold]Device Summary[/bold]",
                border_style="green",
            ))

            # Active alerts
            try:
                async def _alerts():
                    db = await get_db()
                    return await db.get_active_alerts()

                active_alerts = run_async(_alerts())
                if active_alerts:
                    console.print(Panel(
                        f"[bold red]{len(active_alerts)} active alert(s)[/bold red]  --  "
                        "run [cyan]netagent monitor alerts[/cyan] for details",
                        title="[bold]Alerts[/bold]",
                        border_style="red",
                    ))
                else:
                    console.print(Panel(
                        "[green]No active alerts[/green]",
                        title="[bold]Alerts[/bold]",
                        border_style="green",
                    ))
            except Exception:
                console.print("[dim]Alert data unavailable.[/dim]")

        except Exception as exc:
            console.print(f"[yellow]Could not load device summary:[/yellow] {exc}")
    else:
        console.print(
            "[dim]Database not initialised. Run a command to auto-create it.[/dim]"
        )

    console.print()
    console.print("[dim]Use [cyan]netagent --help[/cyan] for available commands.[/dim]")
