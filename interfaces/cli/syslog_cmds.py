"""CLI commands for syslog management."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

console = Console()
syslog_app = typer.Typer(no_args_is_help=True)

SEVERITY_NAMES = {0: "EMERG", 1: "ALERT", 2: "CRIT", 3: "ERR", 4: "WARN", 5: "NOTICE", 6: "INFO", 7: "DEBUG"}
SEVERITY_STYLES = {0: "bold red", 1: "bold red", 2: "red", 3: "red", 4: "yellow", 5: "cyan", 6: "green", 7: "dim"}


@syslog_app.command("tail")
def tail(
    count: int = typer.Option(50, "--count", "-c", help="Number of messages"),
    severity: Optional[int] = typer.Option(None, "--severity", "-s", help="Min severity (0-7)"),
    device_id: Optional[str] = typer.Option(None, "--device", "-d"),
) -> None:
    """Show recent syslog messages."""
    from .app import run_async, get_db
    from operations.syslog import SyslogReceiver

    async def _run():
        db = await get_db()
        receiver = SyslogReceiver(db)
        return await receiver.search_messages(device_id=device_id, min_severity=severity, limit=count)

    messages = run_async(_run())
    if not messages:
        console.print("[dim]No syslog messages found.[/dim]")
        return

    table = Table(title="Syslog Messages")
    table.add_column("Time", style="dim")
    table.add_column("Severity")
    table.add_column("Host")
    table.add_column("Message")
    for m in messages:
        sev = m.get("severity", 6)
        sev_name = SEVERITY_NAMES.get(sev, str(sev))
        style = SEVERITY_STYLES.get(sev, "")
        table.add_row(
            m.get("timestamp", ""),
            f"[{style}]{sev_name}[/{style}]",
            m.get("hostname", ""),
            (m.get("message", ""))[:80],
        )
    console.print(table)


@syslog_app.command("search")
def search(
    query: str = typer.Argument(..., help="Search term"),
    count: int = typer.Option(50, "--count", "-c"),
) -> None:
    """Search syslog messages."""
    from .app import run_async, get_db
    from operations.syslog import SyslogReceiver

    async def _run():
        db = await get_db()
        receiver = SyslogReceiver(db)
        return await receiver.search_messages(query=query, limit=count)

    messages = run_async(_run())
    console.print(f"[cyan]Found {len(messages)} messages matching '{query}'[/cyan]")
    for m in messages[:20]:
        sev = m.get("severity", 6)
        sev_name = SEVERITY_NAMES.get(sev, str(sev))
        console.print(f"  [{SEVERITY_STYLES.get(sev, '')}]{sev_name}[/] {m.get('timestamp', '')} {m.get('hostname', '')}: {m.get('message', '')[:100]}")


@syslog_app.command("stats")
def stats() -> None:
    """Show syslog statistics."""
    from .app import run_async, get_db
    from operations.syslog import SyslogReceiver

    async def _run():
        db = await get_db()
        receiver = SyslogReceiver(db)
        return await receiver.get_stats()

    result = run_async(_run())
    console.print(f"[bold]Syslog Statistics[/bold]")
    console.print(f"  Total messages: {result.get('total', 0)}")
    console.print(f"  Last 24h:       {result.get('last_24h', 0)}")
    by_sev = result.get("by_severity", {})
    for sev_num, count in sorted(by_sev.items()):
        sev_name = SEVERITY_NAMES.get(int(sev_num), str(sev_num))
        console.print(f"  {sev_name}: {count}")
