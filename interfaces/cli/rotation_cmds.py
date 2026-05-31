"""CLI commands for credential rotation."""
from __future__ import annotations
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

console = Console()
rotation_app = typer.Typer(no_args_is_help=True)


@rotation_app.command("rotate")
def rotate(
    credential_id: str = typer.Argument(..., help="Credential ID to rotate"),
    initiated_by: str = typer.Option("cli-admin", "--by", "-b"),
) -> None:
    """Rotate a stored credential across all assigned devices."""
    from .app import run_async, get_db
    from operations.credential_rotation import CredentialRotator

    async def _run():
        db = await get_db()
        rotator = CredentialRotator(db)
        return await rotator.rotate_credential(credential_id, initiated_by=initiated_by)

    result = run_async(_run())
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
    else:
        console.print(f"[green]Rotation complete:[/green] {result.get('devices_updated', 0)} devices updated, {result.get('devices_failed', 0)} failed")


@rotation_app.command("verify")
def verify(
    credential_id: str = typer.Argument(..., help="Credential ID to verify"),
) -> None:
    """Verify credential works on all assigned devices."""
    from .app import run_async, get_db
    from operations.credential_rotation import CredentialRotator

    async def _run():
        db = await get_db()
        rotator = CredentialRotator(db)
        return await rotator.verify_all_devices(credential_id)

    result = run_async(_run())
    ok = result.get("success", 0)
    fail = result.get("failed", 0)
    console.print(f"[green]OK: {ok}[/green]  [red]Failed: {fail}[/red]")


@rotation_app.command("history")
def rotation_history(
    credential_id: Optional[str] = typer.Option(None, "--cred", "-c"),
    limit: int = typer.Option(20, "--limit", "-l"),
) -> None:
    """Show credential rotation history."""
    from .app import run_async, get_db
    from operations.credential_rotation import CredentialRotator

    async def _run():
        db = await get_db()
        rotator = CredentialRotator(db)
        return await rotator.get_rotation_history(credential_id=credential_id, limit=limit)

    rotations = run_async(_run())
    if not rotations:
        console.print("[dim]No rotation history found.[/dim]")
        return

    table = Table(title="Credential Rotations")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Credential", max_width=8)
    table.add_column("Status")
    table.add_column("Updated")
    table.add_column("Failed")
    table.add_column("Started")
    for r in rotations:
        st = r.get("status", "")
        st_style = {"completed": "green", "failed": "red", "in_progress": "yellow", "partial": "yellow"}.get(st, "dim")
        table.add_row(
            r["id"][:8], r.get("credential_id", "")[:8],
            f"[{st_style}]{st}[/{st_style}]",
            str(r.get("devices_updated", 0)), str(r.get("devices_failed", 0)),
            r.get("started_at", ""),
        )
    console.print(table)
