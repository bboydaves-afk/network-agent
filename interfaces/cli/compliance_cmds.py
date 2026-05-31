"""CLI commands for compliance reporting."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()
compliance_app = typer.Typer(no_args_is_help=True)


@compliance_app.command("run")
def run_check(
    device_id: str = typer.Argument(..., help="Device ID to check"),
    ruleset: str = typer.Option("cis-network", "--ruleset", "-r", help="Ruleset name"),
) -> None:
    """Run compliance check against a device."""
    from .app import run_async, get_db
    from operations.compliance import ComplianceEngine

    async def _run():
        db = await get_db()
        engine = ComplianceEngine(db)
        return await engine.run_check(device_id, ruleset)

    result = run_async(_run())
    score = result.get("score", 0)
    color = "green" if score >= 80 else "yellow" if score >= 50 else "red"
    console.print(Panel(
        f"Score: [{color}]{score:.1f}%[/{color}]  Passed: {result.get('passed', 0)}/{result.get('total_checks', 0)}",
        title=f"[bold]Compliance: {ruleset}[/bold]",
        border_style=color,
    ))

    failed = [d for d in result.get("details", []) if not d.get("passed")]
    if failed:
        table = Table(title="Failed Checks")
        table.add_column("Check", style="bold")
        table.add_column("Expected")
        table.add_column("Status", style="red")
        for f in failed:
            table.add_row(f.get("name", ""), f.get("expected", ""), "FAIL")
        console.print(table)


@compliance_app.command("report")
def report(
    device_id: str = typer.Argument(..., help="Device ID"),
) -> None:
    """Show latest compliance report for a device."""
    from .app import run_async, get_db
    from operations.compliance import ComplianceEngine

    async def _run():
        db = await get_db()
        engine = ComplianceEngine(db)
        return await engine.get_latest_report(device_id)

    result = run_async(_run())
    if not result:
        console.print("[dim]No compliance reports found. Run a check first.[/dim]")
        return
    console.print(f"[bold]Compliance Report: {result.get('ruleset_name', '')}[/bold]")
    console.print(f"  Score: {result.get('score', 0):.1f}%  Passed: {result.get('passed', 0)}/{result.get('total_checks', 0)}")
    console.print(f"  Checked at: {result.get('created_at', '')}")


@compliance_app.command("rules")
def list_rules(
    ruleset: str = typer.Option("cis-network", "--ruleset", "-r"),
) -> None:
    """List available compliance rules."""
    from .app import run_async, get_db
    from operations.compliance import ComplianceEngine

    async def _run():
        db = await get_db()
        engine = ComplianceEngine(db)
        return engine.list_rules(ruleset)

    rules = run_async(_run())
    if not rules:
        console.print(f"[dim]No rules found for ruleset '{ruleset}'.[/dim]")
        return

    table = Table(title=f"Compliance Rules: {ruleset}")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Description")
    for r in rules:
        table.add_row(r.get("name", ""), r.get("type", ""), r.get("description", ""))
    console.print(table)


@compliance_app.command("history")
def history(
    device_id: str = typer.Argument(..., help="Device ID"),
    limit: int = typer.Option(10, "--limit", "-l"),
) -> None:
    """Show compliance check history for a device."""
    from .app import run_async, get_db
    from operations.compliance import ComplianceEngine

    async def _run():
        db = await get_db()
        engine = ComplianceEngine(db)
        return await engine.get_history(device_id, limit=limit)

    results = run_async(_run())
    if not results:
        console.print("[dim]No compliance history found.[/dim]")
        return

    table = Table(title="Compliance History")
    table.add_column("Date", style="dim")
    table.add_column("Ruleset")
    table.add_column("Score")
    table.add_column("Passed")
    table.add_column("Failed")
    for r in results:
        score = r.get("score", 0)
        color = "green" if score >= 80 else "yellow" if score >= 50 else "red"
        table.add_row(r.get("created_at", ""), r.get("ruleset_name", ""), f"[{color}]{score:.1f}%[/{color}]", str(r.get("passed", 0)), str(r.get("failed", 0)))
    console.print(table)
