"""Firewall management CLI commands."""
from __future__ import annotations
import json
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()
firewall_app = typer.Typer(no_args_is_help=True)


@firewall_app.command("rules")
def list_rules(device: str = typer.Argument(..., help="Device ID or hostname")):
    """List firewall rules for a device."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.get_rules(device)
    rules = run_async(_run())
    if not rules:
        console.print("[yellow]No firewall rules found.[/yellow]")
        return
    table = Table(title=f"Firewall Rules - {device}", box=box.ROUNDED)
    table.add_column("#", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Src Zone")
    table.add_column("Dst Zone")
    table.add_column("Src Addr")
    table.add_column("Dst Addr")
    table.add_column("Services")
    table.add_column("Action")
    table.add_column("Enabled")
    table.add_column("Log")
    for r in rules:
        action_style = "green" if r.get("action") == "allow" else "red"
        src_addrs = r.get("source_addresses", [])
        if isinstance(src_addrs, str):
            src_addrs = json.loads(src_addrs)
        dst_addrs = r.get("dest_addresses", [])
        if isinstance(dst_addrs, str):
            dst_addrs = json.loads(dst_addrs)
        svcs = r.get("services", [])
        if isinstance(svcs, str):
            svcs = json.loads(svcs)
        table.add_row(
            str(r.get("position", "")), r.get("name", ""),
            r.get("source_zone", ""), r.get("dest_zone", ""),
            ", ".join(src_addrs) if src_addrs else "any",
            ", ".join(dst_addrs) if dst_addrs else "any",
            ", ".join(svcs) if svcs else "any",
            f"[{action_style}]{r.get('action', '')}[/{action_style}]",
            "Yes" if r.get("enabled") else "No",
            "Yes" if r.get("log_enabled") else "No",
        )
    console.print(table)


@firewall_app.command("add-rule")
def add_rule(
    device: str = typer.Argument(..., help="Device ID or hostname"),
    name: str = typer.Option(..., prompt=True, help="Rule name"),
    source_zone: str = typer.Option("any", help="Source zone"),
    dest_zone: str = typer.Option("any", help="Destination zone"),
    source_addrs: str = typer.Option("any", help="Source addresses (comma-separated)"),
    dest_addrs: str = typer.Option("any", help="Destination addresses (comma-separated)"),
    services: str = typer.Option("any", help="Services (comma-separated)"),
    action: str = typer.Option("allow", help="Action: allow|deny|reject|drop"),
    log: bool = typer.Option(False, help="Enable logging"),
):
    """Create a firewall rule on a device."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        rule_data = {
            "name": name, "source_zone": source_zone, "dest_zone": dest_zone,
            "source_addresses": [a.strip() for a in source_addrs.split(",")],
            "dest_addresses": [a.strip() for a in dest_addrs.split(",")],
            "services": [s.strip() for s in services.split(",")],
            "action": action, "log_enabled": log,
        }
        return await mgr.create_rule(device, rule_data)
    result = run_async(_run())
    console.print(Panel(f"[green]Rule created:[/green] {result.get('rule_id', '')}", title="Firewall Rule"))


@firewall_app.command("delete-rule")
def delete_rule(
    device: str = typer.Argument(..., help="Device ID"),
    rule_id: str = typer.Argument(..., help="Rule ID to delete"),
):
    """Delete a firewall rule."""
    if not typer.confirm(f"Delete rule {rule_id}?"):
        raise typer.Abort()
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.delete_rule(device, rule_id)
    result = run_async(_run())
    console.print(f"[green]Rule deleted:[/green] {result.get('rule_id', '')}")


@firewall_app.command("nat")
def list_nat(device: str = typer.Argument(..., help="Device ID")):
    """List NAT rules for a device."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.get_nat_rules(device)
    rules = run_async(_run())
    if not rules:
        console.print("[yellow]No NAT rules found.[/yellow]")
        return
    table = Table(title=f"NAT Rules - {device}", box=box.ROUNDED)
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Src Zone")
    table.add_column("Dst Zone")
    table.add_column("Original Src")
    table.add_column("Original Dst")
    table.add_column("Translated Src")
    table.add_column("Translated Dst")
    table.add_column("Enabled")
    for r in rules:
        table.add_row(
            r.get("name", ""), r.get("nat_type", ""),
            r.get("source_zone", ""), r.get("dest_zone", ""),
            r.get("original_source", ""), r.get("original_dest", ""),
            r.get("translated_source", ""), r.get("translated_dest", ""),
            "Yes" if r.get("enabled") else "No",
        )
    console.print(table)


@firewall_app.command("zones")
def list_zones(device: str = typer.Argument(..., help="Device ID")):
    """List firewall zones for a device."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.get_zones(device)
    zones = run_async(_run())
    if not zones:
        console.print("[yellow]No firewall zones found.[/yellow]")
        return
    table = Table(title=f"Firewall Zones - {device}", box=box.ROUNDED)
    table.add_column("Name", style="cyan")
    table.add_column("Interfaces")
    table.add_column("Security Level")
    table.add_column("Description")
    for z in zones:
        ifaces = z.get("interfaces", [])
        if isinstance(ifaces, str): ifaces = json.loads(ifaces)
        table.add_row(z.get("name",""), ", ".join(ifaces) if ifaces else "-", str(z.get("security_level",0)), z.get("description","") or "-")
    console.print(table)


@firewall_app.command("objects")
def list_objects(device: str = typer.Argument(..., help="Device ID")):
    """List firewall address/service objects for a device."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.get_objects(device)
    objects = run_async(_run())
    if not objects:
        console.print("[yellow]No firewall objects found.[/yellow]")
        return
    table = Table(title=f"Firewall Objects - {device}", box=box.ROUNDED)
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Value")
    table.add_column("Members")
    table.add_column("Description")
    for o in objects:
        members = o.get("members", [])
        if isinstance(members, str): members = json.loads(members)
        table.add_row(o.get("name",""), o.get("object_type",""), o.get("value","") or "-", ", ".join(members) if members else "-", o.get("description","") or "-")
    console.print(table)


@firewall_app.command("summary")
def show_summary(device: str = typer.Argument(..., help="Device ID")):
    """Show firewall summary for a device."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.get_summary(device)
    s = run_async(_run())
    NL = chr(10)
    panel_text = (
        f"[cyan]Rules:[/cyan]      {s.get('rule_count', 0)} "
        f"({s.get('enabled_rules', 0)} enabled, {s.get('disabled_rules', 0)} disabled)" + NL +
        f"[cyan]NAT Rules:[/cyan]  {s.get('nat_rule_count', 0)}" + NL +
        f"[cyan]Zones:[/cyan]      {s.get('zone_count', 0)}" + NL +
        f"[cyan]Objects:[/cyan]    {s.get('object_count', 0)}" + NL +
        f"[cyan]Last Sync:[/cyan]  {s.get('last_sync', 'Never')}" + NL +
        NL + "[bold]Action Breakdown:[/bold]" + NL
    )
    for action, count in s.get("action_breakdown", {}).items():
        style = "green" if action == "allow" else "red"
        panel_text += f"  [{style}]{action}:[/{style}] {count}" + NL
    console.print(Panel(panel_text, title=f"Firewall Summary - {device}"))


@firewall_app.command("sync")
def sync_rules(device: str = typer.Argument(..., help="Device ID")):
    """Sync firewall rules from live device to database."""
    from .app import run_async, get_db
    from operations.firewall import FirewallManager
    async def _run():
        db = await get_db()
        mgr = FirewallManager(db)
        return await mgr.sync_rules(device)
    console.print(f"[cyan]Syncing firewall rules from {device}...[/cyan]")
    result = run_async(_run())
    console.print(f"[green]Synced {result.get('rules_synced', 0)} rules from {device}[/green]")
