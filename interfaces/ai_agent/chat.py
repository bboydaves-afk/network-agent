"""Interactive chat session for the AI agent.

Provides a Rich-based terminal UI that lets a user converse with the
``NetworkAIAgent``.  Tool calls, confirmations, and errors are rendered
with coloured panels so the operator can quickly follow what the agent is
doing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .agent import NetworkAIAgent

logger = logging.getLogger(__name__)
console = Console()


class ChatSession:
    """Wraps a ``NetworkAIAgent`` in a REPL-style chat loop.

    Usage::

        agent = NetworkAIAgent(api_key="...", db=db, ...)
        session = ChatSession(agent)
        await session.start()
    """

    def __init__(self, agent: NetworkAIAgent) -> None:
        self.agent = agent

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the interactive chat session.

        The loop runs until the user types ``quit``, ``exit``, or ``bye``
        (or presses Ctrl-C).
        """
        self._print_welcome()

        while True:
            try:
                user_input = Prompt.ask("\n[bold green]You[/bold green]")

                # Exit commands
                if user_input.lower().strip() in ("quit", "exit", "bye"):
                    console.print("[cyan]Goodbye![/cyan]")
                    break

                # Clear conversation
                if user_input.lower().strip() == "clear":
                    self.agent.reset_conversation()
                    console.print("[dim]Conversation cleared.[/dim]")
                    continue

                # Help
                if user_input.lower().strip() in ("help", "?"):
                    self._print_help()
                    continue

                # Blank input
                if not user_input.strip():
                    continue

                # Process through the agent
                with console.status(
                    "[cyan]Thinking...[/cyan]", spinner="dots"
                ):
                    response = await self.agent.chat(user_input)

                self._render_response(response)

            except KeyboardInterrupt:
                console.print("\n[cyan]Goodbye![/cyan]")
                break
            except Exception as exc:
                logger.exception("Chat error: %s", exc)
                console.print(f"[red]Error: {exc}[/red]")

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _print_welcome() -> None:
        """Print the startup banner."""
        console.print(
            Panel(
                "[bold cyan]Network Engineer AI Agent[/bold cyan]\n"
                "Ask me anything about your network. Type 'quit' to exit, "
                "'clear' to reset, or 'help' for examples.\n\n"
                "[dim]Examples:[/dim]\n"
                "  - 'Show me all devices'\n"
                "  - 'What is the CPU usage on the core switch?'\n"
                "  - 'Ping 10.0.0.1 from the router'\n"
                "  - 'Backup all firewall configs'\n"
                "  - 'Scan 192.168.1.0/24 for devices'\n"
                "  - 'Check if port 443 is open on 10.0.0.5'\n"
                "  - 'Show me active alerts'\n"
                "  - 'Compare the last two configs for switch-01'",
                title="NetworkAgent AI",
                border_style="cyan",
            )
        )

    @staticmethod
    def _print_help() -> None:
        """Print available commands and example prompts."""
        table = Table(
            title="Available Commands",
            show_header=True,
            header_style="bold",
        )
        table.add_column("Command", style="cyan")
        table.add_column("Description")

        table.add_row("quit / exit / bye", "Exit the chat session")
        table.add_row("clear", "Reset conversation history")
        table.add_row("help / ?", "Show this help message")

        console.print(table)
        console.print()

        examples_table = Table(
            title="Example Prompts",
            show_header=True,
            header_style="bold",
        )
        examples_table.add_column("Category", style="cyan")
        examples_table.add_column("Prompt")

        examples_table.add_row(
            "Inventory", "List all Cisco devices"
        )
        examples_table.add_row(
            "Health", "Show me the health of switch-core-01"
        )
        examples_table.add_row(
            "Connectivity", "Ping 10.0.0.1 with 10 packets"
        )
        examples_table.add_row(
            "Connectivity", "Traceroute to 8.8.8.8 from router-edge-01"
        )
        examples_table.add_row(
            "Port Check", "Is port 22 open on 192.168.1.100?"
        )
        examples_table.add_row(
            "Configuration", "Backup the config of firewall-01"
        )
        examples_table.add_row(
            "Configuration", "Show me the running config of router-01"
        )
        examples_table.add_row(
            "Configuration", "Compare config backups abc123 and def456"
        )
        examples_table.add_row(
            "Metrics", "Show CPU metrics for switch-01 over the last 48 hours"
        )
        examples_table.add_row(
            "Alerts", "Are there any active alerts?"
        )
        examples_table.add_row(
            "Discovery", "Scan 192.168.1.0/24 for new devices"
        )
        examples_table.add_row(
            "Interfaces", "Show all interfaces on switch-core-01"
        )

        console.print(examples_table)

    @staticmethod
    def _render_response(response: dict[str, Any]) -> None:
        """Render the agent's response, tool calls, and confirmation prompts."""

        # -- Tool calls (show before the main message) ---------------------
        if response.get("tool_calls"):
            for tc in response["tool_calls"]:
                tool_name = tc.get("tool", "unknown")
                tool_input = tc.get("input", {})
                tool_result = tc.get("result", {})

                # Compact input summary
                input_str = json.dumps(tool_input, default=str)
                if len(input_str) > 120:
                    input_str = input_str[:117] + "..."

                # Show whether the call succeeded or failed
                if "error" in tool_result:
                    status_str = f"[red]FAILED: {tool_result['error']}[/red]"
                else:
                    status_str = "[green]OK[/green]"

                console.print(
                    f"  [dim]Tool:[/dim] [cyan]{tool_name}[/cyan]"
                    f"  [dim]{input_str}[/dim]  {status_str}"
                )

        # -- Main message --------------------------------------------------
        message = response.get("message", "")
        if message:
            try:
                rendered = Markdown(message)
            except Exception:
                rendered = Text(message)

            console.print(
                Panel(
                    rendered,
                    title="Agent",
                    border_style="blue",
                )
            )

        # -- Confirmation prompt -------------------------------------------
        if response.get("confirmation_required"):
            details = response.get("confirmation_details", {})
            tool = details.get("tool", "unknown")
            params = details.get("input", {})
            params_formatted = json.dumps(params, indent=2, default=str)

            console.print(
                Panel(
                    f"[bold red]Destructive Operation Requires Confirmation"
                    f"[/bold red]\n\n"
                    f"[bold]Tool:[/bold] {tool}\n"
                    f"[bold]Parameters:[/bold]\n{params_formatted}\n\n"
                    f"[yellow]Type 'yes' to proceed or 'no' to cancel.[/yellow]",
                    border_style="red",
                )
            )


# ---------------------------------------------------------------------------
# Convenience entry-point
# ---------------------------------------------------------------------------


async def run_chat(
    api_key: str,
    db: Any = None,
    config_manager: Any = None,
    monitor: Any = None,
    discovery: Any = None,
    troubleshooter: Any = None,
    credential_manager: Any = None,
    model: str = "claude-sonnet-4-5-20250929",
) -> None:
    """One-shot helper to create an agent and start an interactive session.

    This is useful when wiring the chat into a CLI command::

        @app.command()
        def chat():
            asyncio.run(run_chat(api_key=os.environ["ANTHROPIC_API_KEY"], ...))
    """
    agent = NetworkAIAgent(
        api_key=api_key,
        model=model,
        db=db,
        config_manager=config_manager,
        monitor=monitor,
        discovery=discovery,
        troubleshooter=troubleshooter,
        credential_manager=credential_manager,
    )
    session = ChatSession(agent)
    await session.start()
