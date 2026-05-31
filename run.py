"""
Network Engineer Agent - Unified Entry Point

Usage:
    python run.py cli [command]    - Run CLI commands
    python run.py web              - Start web dashboard
    python run.py chat             - Start AI chat agent
    python run.py init             - Initialize database and default config
"""
import sys
import os
import asyncio
import logging
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import yaml
except ImportError:
    print("Error: PyYAML is not installed. Run: pip install pyyaml")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional at import time; .env simply won't be loaded
    pass


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with console and file handlers.

    The log file is written to ``<project_root>/data/logs/network_agent.log``.
    The log directory is created if it does not exist.
    """
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logs")
    os.makedirs(log_dir, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [logging.StreamHandler()]
    try:
        file_handler = logging.FileHandler(
            os.path.join(log_dir, "network_agent.log"), encoding="utf-8"
        )
        handlers.append(file_handler)
    except OSError as exc:
        # If we can't write the log file, fall back to console-only
        print(f"Warning: Could not open log file: {exc}")

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load ``config.yaml`` from the project root.

    Exits with an error message if the file is missing or malformed.
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if not os.path.exists(config_path):
        print(f"Error: config.yaml not found at {config_path}")
        print("Run 'python run.py init' to generate a default configuration.")
        sys.exit(1)

    try:
        with open(config_path, encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        print(f"Error: config.yaml is malformed: {exc}")
        sys.exit(1)

    if not isinstance(config, dict):
        print("Error: config.yaml must contain a YAML mapping at the top level.")
        sys.exit(1)

    return config


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------

async def init_database(config: dict) -> None:
    """Create the SQLite database, apply the schema, and load default alert
    rules.  Safe to run multiple times (idempotent).
    """
    from core.database import Database

    db_path = config.get("database", {}).get("path", "./data/network_agent.db")
    db = Database(db_path)
    await db.initialize()

    # Load default alert rules if the alerts.rules module provides them.
    try:
        from alerts.rules import load_default_rules
        await load_default_rules(db)
        print("Default alert rules loaded.")
    except ImportError:
        print("Warning: alerts.rules module not found; skipping default rules.")
    except Exception as exc:
        print(f"Warning: Could not load default alert rules: {exc}")

    print(f"Database initialized at {os.path.abspath(db_path)}")


def generate_default_config() -> None:
    """Write a default ``config.yaml`` if one does not already exist."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if os.path.exists(config_path):
        print(f"config.yaml already exists at {config_path}")
        return

    default_config = {
        "agent": {
            "name": "NetworkAgent",
            "data_dir": "./data",
            "log_level": "INFO",
        },
        "database": {
            "path": "./data/network_agent.db",
        },
        "credentials": {
            "encryption_key_env": "NETAGENT_ENCRYPTION_KEY",
        },
        "monitoring": {
            "poll_interval_seconds": 60,
            "metrics_retention_days": 30,
        },
        "alerts": {
            "channels": {
                "slack": {"webhook_url_env": "SLACK_WEBHOOK_URL"},
                "email": {
                    "smtp_host": "",
                    "smtp_port": 587,
                    "smtp_user": "",
                    "smtp_password_env": "SMTP_PASSWORD",
                    "from_address": "",
                },
                "webhook": {"default_url": ""},
            },
        },
        "web": {
            "host": "0.0.0.0",
            "port": 8080,
            "secret_key_env": "NETAGENT_SECRET_KEY",
        },
        "ai_agent": {
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 4096,
        },
    }

    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(default_config, fh, default_flow_style=False, sort_keys=False)

    print(f"Default config.yaml created at {config_path}")


def run_init(config: dict) -> None:
    """Handle the ``init`` sub-command: ensure config exists, then create DB."""
    # Ensure data directory exists
    data_dir = config.get("agent", {}).get("data_dir", "./data")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "configs"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "templates"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "runbooks"), exist_ok=True)

    asyncio.run(init_database(config))


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def run_cli() -> None:
    """Launch the Typer-based CLI interface.

    All remaining ``sys.argv`` items after ``cli`` are forwarded to Typer.
    """
    try:
        from interfaces.cli.app import app
    except ImportError as exc:
        print(f"Error: Could not import CLI module: {exc}")
        print("Ensure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)

    try:
        app()
    except SystemExit:
        raise
    except Exception as exc:
        logging.getLogger("run").exception("CLI error: %s", exc)
        print(f"\nFatal error in CLI: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Web mode
# ---------------------------------------------------------------------------

def run_web(config: dict) -> None:
    """Launch the FastAPI web dashboard via Uvicorn."""
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is not installed. Run: pip install uvicorn")
        sys.exit(1)

    try:
        # Verify the web app module can be imported before starting uvicorn
        from interfaces.web.app import app  # noqa: F401
    except ImportError as exc:
        print(f"Error: Could not import web application: {exc}")
        print("Ensure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)

    host = config.get("web", {}).get("host", "0.0.0.0")
    port = config.get("web", {}).get("port", 8080)

    print()
    print("=" * 54)
    print("  Network Agent Web Dashboard")
    print(f"  Starting on http://{host}:{port}")
    print("=" * 54)
    print()

    try:
        ssl_kwargs = {}
        ssl_cert = os.environ.get("VOLTSYS_SSL_CERT")
        ssl_key = os.environ.get("VOLTSYS_SSL_KEY")
        if ssl_cert and ssl_key and os.path.isfile(ssl_cert) and os.path.isfile(ssl_key):
            ssl_kwargs = {"ssl_certfile": ssl_cert, "ssl_keyfile": ssl_key}

        uvicorn.run(
            "interfaces.web.app:app",
            host=host,
            port=port,
            log_level="info",
            reload=False,
            **ssl_kwargs,
        )
    except OSError as exc:
        if "address already in use" in str(exc).lower() or "10048" in str(exc):
            print(f"\nError: Port {port} is already in use.")
            print(f"Either stop the process using port {port} or change the port in config.yaml.")
        else:
            print(f"\nError starting web server: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nWeb dashboard stopped.")


# ---------------------------------------------------------------------------
# Chat mode
# ---------------------------------------------------------------------------

async def run_chat(config: dict) -> None:
    """Launch the interactive AI chat agent session.

    Initialises all required services (database, credential manager,
    config manager, monitoring engine, discovery, troubleshooter) and
    hands control to the ``ChatSession`` interactive loop.
    """
    # Late imports to avoid loading heavy dependencies when not needed
    from core.database import Database
    from core.credentials import CredentialManager
    from operations.config_manager import ConfigManager
    from operations.monitor import MonitoringEngine
    from operations.discovery import NetworkDiscovery
    from operations.troubleshoot import Troubleshooter
    from interfaces.ai_agent.agent import NetworkAIAgent
    from interfaces.ai_agent.chat import ChatSession

    logger = logging.getLogger("run.chat")

    # --- Database ---
    db_path = config.get("database", {}).get("path", "./data/network_agent.db")
    db = Database(db_path)
    await db.initialize()
    logger.info("Database ready at %s", db_path)

    # --- Credential Manager ---
    enc_key = os.environ.get("NETAGENT_ENCRYPTION_KEY")
    if not enc_key:
        print("Warning: NETAGENT_ENCRYPTION_KEY is not set.")
        print("Credential encryption will use a fallback key. Set it for production use.")
    cred_manager = CredentialManager(db, enc_key)

    # --- Operations layer ---
    data_dir = config.get("agent", {}).get("data_dir", "./data")
    config_manager = ConfigManager(db, cred_manager, data_dir)

    poll_interval = config.get("monitoring", {}).get("poll_interval_seconds", 60)
    monitor = MonitoringEngine(db, cred_manager, poll_interval)

    discovery = NetworkDiscovery(db)
    troubleshooter = Troubleshooter(db, cred_manager)

    # --- AI Agent ---
    api_key_env = config.get("ai_agent", {}).get("api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        print(f"Error: {api_key_env} environment variable is not set.")
        print(f"Set it with:")
        print(f"  Windows:  set {api_key_env}=your-key-here")
        print(f"  Linux:    export {api_key_env}=your-key-here")
        sys.exit(1)

    model = config.get("ai_agent", {}).get("model", "claude-sonnet-4-5-20250929")
    max_tokens = config.get("ai_agent", {}).get("max_tokens", 4096)

    agent = NetworkAIAgent(
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        db=db,
        config_manager=config_manager,
        monitor=monitor,
        discovery=discovery,
        troubleshooter=troubleshooter,
        credential_manager=cred_manager,
    )

    print()
    print("=" * 54)
    print("  Network Agent AI Chat")
    print(f"  Model: {model}")
    print("  Type 'exit' or 'quit' to end the session.")
    print("=" * 54)
    print()

    session = ChatSession(agent)
    try:
        await session.start()
    except KeyboardInterrupt:
        print("\nChat session ended.")
    finally:
        logger.info("Chat session terminated.")


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse the first CLI argument and dispatch to the appropriate mode."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    # Special case: --help / -h at the top level
    if command in ("--help", "-h", "help"):
        print(__doc__)
        sys.exit(0)

    # For the 'init' command, generate a default config first if needed
    if command == "init":
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.yaml"
        )
        if not os.path.exists(config_path):
            generate_default_config()
        config = load_config()
        setup_logging(config.get("agent", {}).get("log_level", "INFO"))
        run_init(config)
        return

    # All other commands require an existing config
    config = load_config()
    setup_logging(config.get("agent", {}).get("log_level", "INFO"))

    if command == "cli":
        # Forward remaining args to Typer
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        run_cli()
    elif command == "web":
        run_web(config)
    elif command == "chat":
        try:
            asyncio.run(run_chat(config))
        except KeyboardInterrupt:
            print("\nChat session ended.")
        except Exception as exc:
            logging.getLogger("run").exception("Chat error: %s", exc)
            print(f"\nFatal error in chat mode: {exc}")
            sys.exit(1)
    else:
        print(f"Unknown command: {command!r}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
