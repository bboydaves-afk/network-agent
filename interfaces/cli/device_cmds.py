"""Device inventory management CLI commands."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional, List

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt, Confirm

from devices.registry import list_device_types, get_device_class

console = Console()
err_console = Console(stderr=True)

device_app = typer.Typer(no_args_is_help=True)
cred_app = typer.Typer(no_args_is_help=True, help="Credential management")
device_app.add_typer(cred_app, name="cred", help="Manage stored credentials")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_style(status: str) -> str:
    """Return a Rich markup string for a device status."""
    mapping = {
        "online": "[green]online[/green]",
        "offline": "[red]offline[/red]",
        "degraded": "[yellow]degraded[/yellow]",
    }
    return mapping.get(status, f"[dim]{status}[/dim]")


def _truncate_id(device_id: str, length: int = 8) -> str:
    """Truncate a UUID-style device ID to the first *length* characters."""
    return device_id[:length] if len(device_id) > length else device_id


# =====================================================================
# DEVICE COMMANDS
# =====================================================================

@device_app.command("list")
def list_devices(
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    status_filter: Optional[str] = typer.Option(
        None, "--status", "-s", help="Filter by status (online/offline/degraded)"
    ),
) -> None:
    """List all managed devices."""
    from .app import run_async, get_db

    async def _list():
        db = await get_db()
        return await db.get_all_devices()

    devices = run_async(_list())

    # Apply filters
    if tag:
        devices = [
            d for d in devices
            if tag.lower() in [t.lower() for t in (d.get("tags") or [])]
        ]
    if status_filter:
        devices = [d for d in devices if d.get("status", "").lower() == status_filter.lower()]

    if not devices:
        console.print("[yellow]No devices found matching the given filters.[/yellow]")
        return

    table = Table(
        title="Managed Devices",
        title_style="bold cyan",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("ID", style="dim", max_width=10)
    table.add_column("Hostname", style="bold")
    table.add_column("IP Address", style="cyan")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Location")
    table.add_column("Last Seen", style="dim")

    for d in devices:
        last_seen = d.get("last_seen", "")
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen)
                last_seen = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass

        table.add_row(
            _truncate_id(d.get("id", "")),
            d.get("hostname", ""),
            d.get("ip_address", ""),
            d.get("device_type", ""),
            _status_style(d.get("status", "unknown")),
            d.get("location", "") or "",
            last_seen,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(devices)} device(s)[/dim]")


@device_app.command("add")
def add_device(
    hostname: Optional[str] = typer.Option(None, "--hostname", "-h", help="Device hostname"),
    ip: Optional[str] = typer.Option(None, "--ip", help="Device IP address"),
    device_type: Optional[str] = typer.Option(None, "--type", "-t", help="Device type (e.g. cisco_ios)"),
    protocol: Optional[str] = typer.Option(None, "--protocol", "-p", help="Connection protocol (ssh/snmp/netconf/restconf)"),
    port: Optional[int] = typer.Option(None, "--port", help="Connection port"),
    credential_id: Optional[str] = typer.Option(None, "--cred", "-c", help="Credential ID to use"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Device location"),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags"),
) -> None:
    """Add a new device to the inventory (interactive if options omitted)."""
    from .app import run_async, get_db

    supported_types = list_device_types()
    type_display = ", ".join(supported_types) if supported_types else "cisco_ios, cisco_nxos, arista_eos, juniper_junos, generic"

    # Interactive prompts for any missing required fields
    if not hostname:
        hostname = Prompt.ask("[bold]Hostname[/bold]")
    if not ip:
        ip = Prompt.ask("[bold]IP Address[/bold]")
    if not device_type:
        console.print(f"[dim]Available types: {type_display}[/dim]")
        device_type = Prompt.ask("[bold]Device Type[/bold]", default="cisco_ios")
    if not protocol:
        protocol = Prompt.ask(
            "[bold]Protocol[/bold]",
            choices=["ssh", "snmp", "netconf", "restconf"],
            default="ssh",
        )
    if port is None:
        default_ports = {"ssh": "22", "snmp": "161", "netconf": "830", "restconf": "443"}
        port_str = Prompt.ask(
            "[bold]Port[/bold]",
            default=default_ports.get(protocol, "22"),
        )
        port = int(port_str)
    if not credential_id:
        credential_id = Prompt.ask("[bold]Credential ID[/bold] (leave blank to skip)", default="")
    if not location:
        location = Prompt.ask("[bold]Location[/bold] (optional)", default="")
    if tags is None:
        tags = Prompt.ask("[bold]Tags[/bold] (comma-separated, optional)", default="")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    device_data = {
        "hostname": hostname,
        "ip_address": ip,
        "device_type": device_type,
        "protocol": protocol,
        "port": port,
        "credential_id": credential_id or None,
        "location": location or None,
        "tags": tag_list,
        "status": "unknown",
    }

    async def _add():
        db = await get_db()
        device_id = await db.add_device(device_data)
        return device_id

    try:
        device_id = run_async(_add())
        console.print(Panel(
            f"[green]Device added successfully![/green]\n\n"
            f"  [bold]ID:[/bold]       {device_id}\n"
            f"  [bold]Hostname:[/bold] {hostname}\n"
            f"  [bold]IP:[/bold]       {ip}\n"
            f"  [bold]Type:[/bold]     {device_type}\n"
            f"  [bold]Protocol:[/bold] {protocol}:{port}",
            title="[bold green]Device Added[/bold green]",
            border_style="green",
        ))
    except Exception as exc:
        err_console.print(f"[red]Failed to add device:[/red] {exc}")
        raise typer.Exit(code=1)


@device_app.command("show")
def show_device(
    device_id: str = typer.Argument(..., help="Device ID (or prefix)"),
) -> None:
    """Show detailed information for a device."""
    from .app import run_async, get_db

    async def _get():
        db = await get_db()
        return await db.get_device(device_id)

    device = run_async(_get())
    if not device:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    info_lines = []
    fields = [
        ("ID", "id"),
        ("Hostname", "hostname"),
        ("IP Address", "ip_address"),
        ("Device Type", "device_type"),
        ("Protocol", "protocol"),
        ("Port", "port"),
        ("Status", "status"),
        ("Location", "location"),
        ("Credential ID", "credential_id"),
        ("Vendor", "vendor"),
        ("Model", "model"),
        ("OS Version", "os_version"),
        ("Serial Number", "serial_number"),
        ("Uptime", "uptime"),
        ("Last Seen", "last_seen"),
        ("Created At", "created_at"),
        ("Updated At", "updated_at"),
    ]

    for label, key in fields:
        value = device.get(key, "")
        if key == "status" and value:
            value_display = _status_style(value)
        elif value is None or value == "":
            value_display = "[dim]--[/dim]"
        else:
            value_display = str(value)
        info_lines.append(f"  [bold]{label}:[/bold]  {value_display}")

    # Tags
    tags = device.get("tags", [])
    if tags:
        tag_str = ", ".join(f"[cyan]{t}[/cyan]" for t in tags)
        info_lines.append(f"  [bold]Tags:[/bold]  {tag_str}")
    else:
        info_lines.append("  [bold]Tags:[/bold]  [dim]--[/dim]")

    console.print(Panel(
        "\n".join(info_lines),
        title=f"[bold]Device: {device.get('hostname', device_id)}[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))


@device_app.command("remove")
def remove_device(
    device_id: str = typer.Argument(..., help="Device ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove a device from the inventory."""
    from .app import run_async, get_db

    async def _get():
        db = await get_db()
        return await db.get_device(device_id)

    device = run_async(_get())
    if not device:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)
    ip_addr = device.get("ip_address", "")

    if not force:
        confirmed = Confirm.ask(
            f"Remove device [bold]{hostname}[/bold] ({ip_addr})? This cannot be undone"
        )
        if not confirmed:
            console.print("[dim]Operation cancelled.[/dim]")
            return

    async def _remove():
        db = await get_db()
        await db.delete_device(device_id)

    try:
        run_async(_remove())
        console.print(f"[green]Device [bold]{hostname}[/bold] ({device_id}) removed successfully.[/green]")
    except Exception as exc:
        err_console.print(f"[red]Failed to remove device:[/red] {exc}")
        raise typer.Exit(code=1)


@device_app.command("edit")
def edit_device(
    device_id: str = typer.Argument(..., help="Device ID to edit"),
    hostname: Optional[str] = typer.Option(None, "--hostname", help="New hostname"),
    ip: Optional[str] = typer.Option(None, "--ip", help="New IP address"),
    device_type: Optional[str] = typer.Option(None, "--type", help="New device type"),
    protocol: Optional[str] = typer.Option(None, "--protocol", help="New protocol"),
    port: Optional[int] = typer.Option(None, "--port", help="New port"),
    location: Optional[str] = typer.Option(None, "--location", help="New location"),
    credential_id: Optional[str] = typer.Option(None, "--cred", help="New credential ID"),
    set_status: Optional[str] = typer.Option(None, "--status", help="Set device status"),
) -> None:
    """Edit fields on an existing device."""
    from .app import run_async, get_db

    updates: dict = {}
    if hostname is not None:
        updates["hostname"] = hostname
    if ip is not None:
        updates["ip_address"] = ip
    if device_type is not None:
        updates["device_type"] = device_type
    if protocol is not None:
        updates["protocol"] = protocol
    if port is not None:
        updates["port"] = port
    if location is not None:
        updates["location"] = location
    if credential_id is not None:
        updates["credential_id"] = credential_id
    if set_status is not None:
        updates["status"] = set_status

    if not updates:
        err_console.print("[yellow]No fields specified to update. Use --hostname, --ip, etc.[/yellow]")
        raise typer.Exit(code=1)

    async def _edit():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None
        await db.update_device(device_id, updates)
        return await db.get_device(device_id)

    result = run_async(_edit())
    if result is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    console.print(f"[green]Device [bold]{result.get('hostname', device_id)}[/bold] updated successfully.[/green]")
    updated_fields = ", ".join(f"{k}={v}" for k, v in updates.items())
    console.print(f"[dim]Changed: {updated_fields}[/dim]")


@device_app.command("test")
def test_device(
    device_id: str = typer.Argument(..., help="Device ID to test connectivity"),
) -> None:
    """Test connectivity to a device (connect, get facts, disconnect)."""
    from .app import run_async, get_db, get_cred_manager
    from rich.progress import Progress, SpinnerColumn, TextColumn

    async def _test():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None, "Device not found"

        cred_mgr = await get_cred_manager()

        # Resolve credentials
        username = ""
        password = ""
        enable_secret = ""
        ssh_key_path = ""

        cred_id = device.get("credential_id")
        if cred_id:
            try:
                cred = await cred_mgr.get_credential(cred_id)
                if cred:
                    username = cred.get("username", "")
                    password = cred.get("password", "")
                    enable_secret = cred.get("enable_secret", "")
                    ssh_key_path = cred.get("ssh_key_path", "")
            except Exception:
                pass

        # Create device instance
        try:
            device_cls = get_device_class(device.get("device_type", ""))
        except KeyError as e:
            return device, f"Unsupported device type: {e}"

        dev_instance = device_cls(
            host=device.get("ip_address", ""),
            username=username,
            password=password,
            port=device.get("port", 22),
            device_type=device.get("device_type", ""),
            enable_secret=enable_secret,
            ssh_key_path=ssh_key_path,
        )

        results = {"connect": None, "facts": None, "disconnect": None}
        timings = {}

        # Connect
        t0 = time.time()
        try:
            await dev_instance.connect()
            timings["connect"] = time.time() - t0
            results["connect"] = True
        except Exception as exc:
            timings["connect"] = time.time() - t0
            results["connect"] = str(exc)
            return device, results, timings

        # Get facts
        t0 = time.time()
        try:
            facts = await dev_instance.get_facts()
            timings["facts"] = time.time() - t0
            results["facts"] = facts
        except Exception as exc:
            timings["facts"] = time.time() - t0
            results["facts"] = str(exc)

        # Disconnect
        t0 = time.time()
        try:
            await dev_instance.disconnect()
            timings["disconnect"] = time.time() - t0
            results["disconnect"] = True
        except Exception as exc:
            timings["disconnect"] = time.time() - t0
            results["disconnect"] = str(exc)

        return device, results, timings

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Testing device connectivity...", total=None)
        outcome = run_async(_test())

    if outcome is None or (isinstance(outcome, tuple) and outcome[0] is None):
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    if len(outcome) == 2:
        device, error_msg = outcome
        err_console.print(f"[red]Test failed:[/red] {error_msg}")
        raise typer.Exit(code=1)

    device, results, timings = outcome
    hostname = device.get("hostname", device_id)

    lines = []
    for step_name in ("connect", "facts", "disconnect"):
        result = results.get(step_name)
        elapsed = timings.get(step_name, 0)
        elapsed_str = f"({elapsed:.2f}s)"

        if result is True:
            lines.append(f"  [green]PASS[/green]  {step_name:<12} {elapsed_str}")
        elif isinstance(result, str):
            lines.append(f"  [red]FAIL[/red]  {step_name:<12} {elapsed_str}  {result}")
        elif hasattr(result, "to_dict"):
            lines.append(f"  [green]PASS[/green]  {step_name:<12} {elapsed_str}")
            facts_dict = result.to_dict()
            for k, v in facts_dict.items():
                if v:
                    lines.append(f"           [dim]{k}: {v}[/dim]")
        else:
            lines.append(f"  [yellow]????[/yellow]  {step_name:<12} {elapsed_str}")

    all_passed = (
        results.get("connect") is True
        and results.get("disconnect") is True
        and hasattr(results.get("facts"), "to_dict")
    )
    border = "green" if all_passed else "red"
    title_status = "[green]PASSED[/green]" if all_passed else "[red]FAILED[/red]"

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold]Connectivity Test: {hostname}[/bold] -- {title_status}",
        border_style=border,
        padding=(1, 2),
    ))


@device_app.command("tags")
def manage_tags(
    device_id: str = typer.Argument(..., help="Device ID"),
    add: Optional[str] = typer.Option(None, "--add", "-a", help="Add tag(s) (comma-separated)"),
    remove: Optional[str] = typer.Option(None, "--remove", "-r", help="Remove tag(s) (comma-separated)"),
) -> None:
    """Show, add, or remove tags for a device."""
    from .app import run_async, get_db

    async def _manage():
        db = await get_db()
        device = await db.get_device(device_id)
        if not device:
            return None

        current_tags = list(device.get("tags") or [])

        if add:
            new_tags = [t.strip() for t in add.split(",") if t.strip()]
            for t in new_tags:
                if t not in current_tags:
                    current_tags.append(t)
            await db.update_device(device_id, {"tags": current_tags})

        if remove:
            rm_tags = {t.strip().lower() for t in remove.split(",") if t.strip()}
            current_tags = [t for t in current_tags if t.lower() not in rm_tags]
            await db.update_device(device_id, {"tags": current_tags})

        # Re-fetch
        device = await db.get_device(device_id)
        return device

    device = run_async(_manage())
    if device is None:
        err_console.print(f"[red]Device not found:[/red] {device_id}")
        raise typer.Exit(code=1)

    hostname = device.get("hostname", device_id)
    tags = device.get("tags") or []
    if tags:
        tag_str = "  ".join(f"[cyan]{t}[/cyan]" for t in tags)
        console.print(f"[bold]{hostname}[/bold] tags: {tag_str}")
    else:
        console.print(f"[bold]{hostname}[/bold] has no tags.")


# =====================================================================
# CREDENTIAL COMMANDS
# =====================================================================

@cred_app.command("list")
def cred_list() -> None:
    """List all stored credentials (passwords masked)."""
    from .app import run_async, get_cred_manager

    async def _list():
        cred_mgr = await get_cred_manager()
        return await cred_mgr.list_all()

    credentials = run_async(_list())

    if not credentials:
        console.print("[yellow]No credentials stored.[/yellow]")
        console.print("[dim]Use 'netagent devices cred add' to create one.[/dim]")
        return

    table = Table(
        title="Stored Credentials",
        title_style="bold cyan",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("ID", style="dim", max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Username")
    table.add_column("Password", style="dim")
    table.add_column("SSH Key", style="dim")
    table.add_column("SNMP Community", style="dim")
    table.add_column("Enable Secret", style="dim")

    for c in credentials:
        cred_id = c.get("id", "")
        password = c.get("password", "")
        masked_pw = "****" if password else "--"
        ssh_key = c.get("ssh_key_path", "")
        ssh_display = ssh_key if ssh_key else "--"
        snmp = c.get("snmp_community", "")
        snmp_display = "****" if snmp else "--"
        enable = c.get("enable_secret", "")
        enable_display = "****" if enable else "--"

        table.add_row(
            _truncate_id(cred_id),
            c.get("name", ""),
            c.get("username", ""),
            masked_pw,
            ssh_display,
            snmp_display,
            enable_display,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(credentials)} credential(s)[/dim]")


@cred_app.command("add")
def cred_add(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Credential name"),
    username: Optional[str] = typer.Option(None, "--username", "-u", help="Username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Password (prompted securely if omitted)", hide_input=True),
    ssh_key_path: Optional[str] = typer.Option(None, "--ssh-key", help="Path to SSH private key"),
    snmp_community: Optional[str] = typer.Option(None, "--snmp-community", help="SNMP community string"),
    enable_secret: Optional[str] = typer.Option(None, "--enable-secret", help="Enable/privilege secret", hide_input=True),
) -> None:
    """Add a new credential (interactive prompts for missing fields)."""
    from .app import run_async, get_cred_manager

    if not name:
        name = Prompt.ask("[bold]Credential Name[/bold]")
    if not username:
        username = Prompt.ask("[bold]Username[/bold]")
    if password is None:
        password = Prompt.ask("[bold]Password[/bold]", password=True, default="")
    if ssh_key_path is None:
        ssh_key_path = Prompt.ask("[bold]SSH Key Path[/bold] (optional)", default="")
    if snmp_community is None:
        snmp_community = Prompt.ask("[bold]SNMP Community[/bold] (optional)", default="")
    if enable_secret is None:
        enable_secret = Prompt.ask("[bold]Enable Secret[/bold] (optional)", password=True, default="")

    cred_data = {
        "name": name,
        "username": username,
        "password": password,
        "ssh_key_path": ssh_key_path or None,
        "snmp_community": snmp_community or None,
        "enable_secret": enable_secret or None,
    }

    async def _add():
        cred_mgr = await get_cred_manager()
        return await cred_mgr.add_credential(cred_data)

    try:
        cred_id = run_async(_add())
        console.print(Panel(
            f"[green]Credential stored successfully![/green]\n\n"
            f"  [bold]ID:[/bold]       {cred_id}\n"
            f"  [bold]Name:[/bold]     {name}\n"
            f"  [bold]Username:[/bold] {username}",
            title="[bold green]Credential Added[/bold green]",
            border_style="green",
        ))
    except Exception as exc:
        err_console.print(f"[red]Failed to add credential:[/red] {exc}")
        raise typer.Exit(code=1)


@cred_app.command("remove")
def cred_remove(
    cred_id: str = typer.Argument(..., help="Credential ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a stored credential."""
    from .app import run_async, get_cred_manager

    if not force:
        confirmed = Confirm.ask(f"Remove credential [bold]{cred_id}[/bold]? This cannot be undone")
        if not confirmed:
            console.print("[dim]Operation cancelled.[/dim]")
            return

    async def _remove():
        cred_mgr = await get_cred_manager()
        await cred_mgr.delete_credential(cred_id)

    try:
        run_async(_remove())
        console.print(f"[green]Credential [bold]{cred_id}[/bold] removed successfully.[/green]")
    except Exception as exc:
        err_console.print(f"[red]Failed to remove credential:[/red] {exc}")
        raise typer.Exit(code=1)


@cred_app.command("test")
def cred_test(
    cred_id: str = typer.Argument(..., help="Credential ID to test"),
    device_ip: str = typer.Argument(..., help="Device IP to test against"),
    device_type: str = typer.Option("cisco_ios", "--type", "-t", help="Device type for connection test"),
    port: int = typer.Option(22, "--port", "-p", help="Connection port"),
) -> None:
    """Test a credential by attempting to connect to a device."""
    from .app import run_async, get_cred_manager
    from rich.progress import Progress, SpinnerColumn, TextColumn

    async def _test():
        cred_mgr = await get_cred_manager()
        cred = await cred_mgr.get_credential(cred_id)
        if not cred:
            return None, "Credential not found"

        try:
            device_cls = get_device_class(device_type)
        except KeyError as e:
            return cred, f"Unsupported device type: {e}"

        dev = device_cls(
            host=device_ip,
            username=cred.get("username", ""),
            password=cred.get("password", ""),
            port=port,
            device_type=device_type,
            enable_secret=cred.get("enable_secret", ""),
            ssh_key_path=cred.get("ssh_key_path", ""),
        )

        t0 = time.time()
        try:
            await dev.connect()
            elapsed = time.time() - t0
            await dev.disconnect()
            return cred, True, elapsed
        except Exception as exc:
            elapsed = time.time() - t0
            return cred, str(exc), elapsed

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Testing credential against {device_ip}...", total=None)
        outcome = run_async(_test())

    if outcome is None or outcome[0] is None:
        err_console.print(f"[red]Credential not found:[/red] {cred_id}")
        raise typer.Exit(code=1)

    if len(outcome) == 2:
        _, error_msg = outcome
        err_console.print(f"[red]Test failed:[/red] {error_msg}")
        raise typer.Exit(code=1)

    cred, result, elapsed = outcome
    cred_name = cred.get("name", cred_id)

    if result is True:
        console.print(Panel(
            f"[green]Authentication successful![/green]\n\n"
            f"  [bold]Credential:[/bold] {cred_name}\n"
            f"  [bold]Target:[/bold]     {device_ip}:{port}\n"
            f"  [bold]Elapsed:[/bold]    {elapsed:.2f}s",
            title="[bold green]Credential Test PASSED[/bold green]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red]Authentication failed![/red]\n\n"
            f"  [bold]Credential:[/bold] {cred_name}\n"
            f"  [bold]Target:[/bold]     {device_ip}:{port}\n"
            f"  [bold]Error:[/bold]      {result}\n"
            f"  [bold]Elapsed:[/bold]    {elapsed:.2f}s",
            title="[bold red]Credential Test FAILED[/bold red]",
            border_style="red",
        ))
        raise typer.Exit(code=1)
