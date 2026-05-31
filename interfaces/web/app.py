"""FastAPI web application for Network Agent dashboard."""

import os
import yaml
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from core.database import Database
from core.credentials import CredentialManager
from operations.config_manager import ConfigManager
from operations.monitor import MonitoringEngine
from operations.discovery import NetworkDiscovery
from operations.troubleshoot import Troubleshooter
from operations.serial_console import SerialConsoleManager
from alerts.engine import AlertEngine
from alerts.channels.slack import SlackChannel
from alerts.channels.email import EmailChannel
from alerts.channels.webhook import WebhookChannel
from automation.engine import AutomationEngine
from automation.scheduler import SchedulerManager
from automation.audit import AuditLogger
from automation.webhook_ingress import create_webhook_router


def load_config() -> dict:
    """Load application configuration from YAML file or return defaults.

    Searches the following locations in order:
    1. ``../../config.yaml`` relative to this file
    2. ``../../config/config.yaml`` relative to this file
    3. The path specified by the ``NETAGENT_CONFIG`` environment variable
    """
    config_paths = [
        os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml"),
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml"),
        os.environ.get("NETAGENT_CONFIG", ""),
    ]
    for path in config_paths:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh)

    # Sensible defaults when no config file is present
    return {
        "database": {"path": "network_agent.db"},
        "monitoring": {"poll_interval_seconds": 60},
        "agent": {"data_dir": "data"},
        "auth": {
            "secret_key": os.environ.get("NETAGENT_SECRET_KEY", ""),
            "admin_username": os.environ.get("NETAGENT_ADMIN_USER", "admin"),
            "admin_password_hash": os.environ.get("NETAGENT_ADMIN_HASH", ""),
        },
    }


class AppContext:
    """Holds references to all shared application services."""

    db: Database
    cred_manager: CredentialManager
    config_manager: ConfigManager
    monitor: MonitoringEngine
    discovery: NetworkDiscovery
    troubleshooter: Troubleshooter
    monitor_task: asyncio.Task = None
    config: dict = {}
    alert_engine: AlertEngine = None
    automation_engine: AutomationEngine = None
    scheduler_manager: SchedulerManager = None
    audit_logger: AuditLogger = None
    serial_manager: SerialConsoleManager = None


ctx = AppContext()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    config = load_config()
    ctx.config = config

    # Initialise database
    ctx.db = Database(config["database"]["path"])
    await ctx.db.initialize()

    # Initialise services
    enc_key = os.environ.get("NETAGENT_ENCRYPTION_KEY")
    ctx.cred_manager = CredentialManager(ctx.db, enc_key)

    data_dir = config["agent"].get("data_dir", "data")
    ctx.config_manager = ConfigManager(ctx.db, ctx.cred_manager, data_dir)

    poll_interval = config["monitoring"].get("poll_interval_seconds", 60)
    ctx.monitor = MonitoringEngine(ctx.db, ctx.cred_manager, poll_interval)

    ctx.discovery = NetworkDiscovery(ctx.db)
    ctx.troubleshooter = Troubleshooter(ctx.db, ctx.cred_manager)
    ctx.serial_manager = SerialConsoleManager(ctx.db, ctx.cred_manager)

    # Alert engine
    ctx.alert_engine = AlertEngine(ctx.db)

    # Register notification channels
    alert_cfg = config.get("alerts", {}).get("channels", {})
    slack_url = os.environ.get(alert_cfg.get("slack", {}).get("webhook_url_env", ""), "")
    if slack_url:
        ctx.alert_engine.register_channel(SlackChannel(slack_url))
    email_cfg = alert_cfg.get("email", {})
    if email_cfg.get("smtp_host"):
        ctx.alert_engine.register_channel(EmailChannel(
            smtp_host=email_cfg["smtp_host"],
            smtp_port=email_cfg.get("smtp_port", 587),
            smtp_user=email_cfg.get("smtp_user", ""),
            smtp_password=os.environ.get(email_cfg.get("smtp_password_env", ""), ""),
            from_address=email_cfg.get("from_address", ""),
        ))
    webhook_url = alert_cfg.get("webhook", {}).get("default_url", "")
    if webhook_url:
        ctx.alert_engine.register_channel(WebhookChannel(webhook_url))

    # Pass alert engine to monitor
    ctx.monitor._alert_engine = ctx.alert_engine

    # Audit logger
    ctx.audit_logger = AuditLogger(ctx.db)

    # Automation engine
    auto_cfg = config.get("automation", {})
    if auto_cfg.get("enabled", True):
        ctx.automation_engine = AutomationEngine(
            db=ctx.db,
            alert_engine=ctx.alert_engine,
            config_manager=ctx.config_manager,
            monitor=ctx.monitor,
            troubleshooter=ctx.troubleshooter,
            discovery=ctx.discovery,
            credential_manager=ctx.cred_manager,
            audit_logger=ctx.audit_logger,
            runbook_dir=auto_cfg.get("runbook_dir", "./data/runbooks"),
            dry_run=auto_cfg.get("dry_run", False),
            max_global_executions=auto_cfg.get("max_concurrent_executions", 10),
        )
        await ctx.automation_engine.start()

    if ctx.automation_engine:
        webhook_router = create_webhook_router(ctx.automation_engine, ctx.audit_logger, ctx.db)
        app.include_router(webhook_router, prefix="/api/webhooks", tags=["Webhooks"])

    # Scheduler
    sched_cfg = config.get("scheduler", {})
    if sched_cfg.get("enabled", True):
        ctx.scheduler_manager = SchedulerManager(
            automation_engine=ctx.automation_engine,
            config_manager=ctx.config_manager,
            discovery=ctx.discovery,
            db=ctx.db,
            audit_logger=ctx.audit_logger,
            config=sched_cfg,
        )
        await ctx.scheduler_manager.start()

    # Start background monitoring loop
    ctx.monitor_task = asyncio.create_task(ctx.monitor.start_polling_loop())

    yield

    # Shutdown: stop automation and scheduler first
    if ctx.scheduler_manager:
        await ctx.scheduler_manager.stop()
    if ctx.automation_engine:
        await ctx.automation_engine.stop()

    # Shutdown: close serial sessions
    if ctx.serial_manager:
        await ctx.serial_manager.disconnect_all()

    # Shutdown: cancel monitoring task
    if ctx.monitor_task and not ctx.monitor_task.done():
        ctx.monitor_task.cancel()
        try:
            await ctx.monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Network Agent", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS -- restrict to explicit origins (set NETAGENT_CORS_ORIGINS env var)
# ---------------------------------------------------------------------------
_cors_origins_raw = os.environ.get("NETAGENT_CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()] if _cors_origins_raw else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Global authentication middleware -- protects all /api/ routes
# ---------------------------------------------------------------------------
# Paths that do NOT require authentication:
_AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/health",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce JWT authentication on all /api/ endpoints except exempt paths."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")

        # Only protect /api/ routes (static files, root page, etc. are exempt)
        if path.startswith("/api") and path not in _AUTH_EXEMPT_PATHS:
            # Allow CORS preflight requests through
            if request.method == "OPTIONS":
                return await call_next(request)

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid Authorization header"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            token = auth_header[len("Bearer "):]
            try:
                from .auth import verify_token
                verify_token(token)
            except Exception:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

        return await call_next(request)


app.add_middleware(AuthMiddleware)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include API routers
from .routes import devices, configs, monitoring, alerts, discovery, troubleshoot  # noqa: E402
from .routes import automation as automation_routes  # noqa: E402
from .routes import audit as audit_routes  # noqa: E402
from .routes import sites as sites_routes  # noqa: E402
from .routes import topology as topology_routes  # noqa: E402
from .routes import firmware as firmware_routes  # noqa: E402
from .routes import ipam as ipam_routes  # noqa: E402
from .routes import traffic as traffic_routes  # noqa: E402
from .routes import syslog as syslog_routes  # noqa: E402
from .routes import compliance as compliance_routes  # noqa: E402
from .routes import changes as changes_routes  # noqa: E402
from .routes import credentials as credentials_routes  # noqa: E402
from .routes import firewall as firewall_routes  # noqa: E402
from .routes import vlans as vlans_routes  # noqa: E402
from .routes import routing as routing_routes  # noqa: E402
from .routes import acls as acls_routes  # noqa: E402
from .routes import tool_api as tool_api_routes  # noqa: E402
from .routes import serial as serial_routes  # noqa: E402

app.include_router(devices.router, prefix="/api/devices", tags=["Devices"])
app.include_router(configs.router, prefix="/api/configs", tags=["Configs"])
app.include_router(monitoring.router, prefix="/api/monitoring", tags=["Monitoring"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(discovery.router, prefix="/api/discovery", tags=["Discovery"])
app.include_router(troubleshoot.router, prefix="/api/diag", tags=["Diagnostics"])
app.include_router(automation_routes.router, prefix="/api/automation", tags=["Automation"])
app.include_router(audit_routes.router, prefix="/api/audit", tags=["Audit"])
app.include_router(sites_routes.router, prefix="/api/sites", tags=["Sites"])
app.include_router(topology_routes.router, prefix="/api/topology", tags=["Topology"])
app.include_router(firmware_routes.router, prefix="/api/firmware", tags=["Firmware"])
app.include_router(ipam_routes.router, prefix="/api/ipam", tags=["IPAM"])
app.include_router(traffic_routes.router, prefix="/api/traffic", tags=["Traffic"])
app.include_router(syslog_routes.router, prefix="/api/syslog", tags=["Syslog"])
app.include_router(compliance_routes.router, prefix="/api/compliance", tags=["Compliance"])
app.include_router(changes_routes.router, prefix="/api/changes", tags=["Changes"])
app.include_router(credentials_routes.router, prefix="/api/credentials", tags=["Credentials"])
app.include_router(firewall_routes.router, prefix="/api/firewall", tags=["Firewall"])
app.include_router(vlans_routes.router, prefix="/api/vlans", tags=["VLANs"])
app.include_router(routing_routes.router, prefix="/api/routing", tags=["Routing"])
app.include_router(acls_routes.router, prefix="/api/acls", tags=["ACLs"])
app.include_router(tool_api_routes.router, tags=["Tool API"])
app.include_router(serial_routes.router, prefix="/api/serial", tags=["Serial Console"])

# Wire up WebSocket endpoints
from .websockets import setup_websockets  # noqa: E402

setup_websockets(app)


@app.get("/")
async def root():
    """Serve the main dashboard HTML page."""
    from fastapi.responses import FileResponse

    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/api/status")
async def agent_status():
    """Return high-level agent status summary."""
    devices = await ctx.db.list_devices()
    active_alerts = await ctx.db.get_alerts(status="active")
    return {
        "name": "NetworkAgent",
        "version": "1.0.0",
        "total_devices": len(devices),
        "online_devices": sum(1 for d in devices if d.get("status") == "online"),
        "offline_devices": sum(1 for d in devices if d.get("status") == "offline"),
        "active_alerts": len(active_alerts),
        "monitoring_active": ctx.monitor_task is not None and not ctx.monitor_task.done(),
    }


# ---------------------------------------------------------------------------
# Login rate limiter -- 5 attempts per IP per 60-second window
# ---------------------------------------------------------------------------
import time as _time
import collections as _collections
import logging as _logging

_login_logger = _logging.getLogger("network_agent.auth")
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 60
_login_attempts: dict[str, _collections.deque] = {}


def _check_login_rate(client_ip: str) -> bool:
    """Return True if the IP is allowed to attempt login, False if rate-limited."""
    now = _time.monotonic()
    if client_ip not in _login_attempts:
        _login_attempts[client_ip] = _collections.deque()

    window = _login_attempts[client_ip]
    # Evict timestamps outside the window
    while window and window[0] < now - _LOGIN_WINDOW_SECONDS:
        window.popleft()

    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        return False

    window.append(now)
    return True


@app.post("/api/auth/login")
async def login(request: Request, body: dict):
    """Authenticate with username/password and return a JWT."""
    client_ip = request.client.host if request.client else "unknown"

    if not _check_login_rate(client_ip):
        _login_logger.warning("Rate limit exceeded for login from %s", client_ip)
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many login attempts. Try again in 60 seconds."},
        )

    from .auth import authenticate_admin

    username = body.get("username", "")
    password = body.get("password", "")
    token = authenticate_admin(username, password)
    if token is None:
        _login_logger.warning("Failed login attempt for user '%s' from %s", username, client_ip)
        from fastapi import HTTPException, status as http_status

        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    _login_logger.info("Successful login for user '%s' from %s", username, client_ip)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/health")
async def health_check():
    """Simple health-check endpoint."""
    return {"status": "ok"}
