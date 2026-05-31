"""Change management API routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


def _engine():
    from operations.change_management import ChangeManager
    return ChangeManager(_ctx().db, getattr(_ctx(), "config_manager", None))


@router.get("/")
async def list_changes(status_filter: Optional[str] = None, device_id: Optional[str] = None):
    """List change requests with optional filters."""
    return {"changes": await _engine().list_requests(status=status_filter, device_id=device_id)}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_change(body: dict):
    """Create a new change request."""
    required = ["device_id", "title", "config_commands"]
    for field in required:
        if field not in body:
            raise HTTPException(400, f"{field} is required")
    return await _engine().create_request(
        device_id=body["device_id"],
        title=body["title"],
        config_commands=body["config_commands"],
        requested_by=body.get("requested_by", "web-user"),
        priority=body.get("priority", "normal"),
        notes=body.get("notes"),
        rollback_plan=body.get("rollback_plan"),
        scheduled_at=body.get("scheduled_at"),
        maintenance_window_start=body.get("maintenance_window_start"),
        maintenance_window_end=body.get("maintenance_window_end"),
    )


@router.get("/pending")
async def pending_changes():
    """List all change requests awaiting approval."""
    pending = await _engine().list_pending()
    return {"pending": pending, "count": len(pending)}


@router.get("/pending/count")
async def pending_count():
    """Get count of pending change requests."""
    return {"count": await _engine().get_pending_count()}


@router.get("/history/{device_id}")
async def change_history(device_id: str, limit: int = 50):
    """Get full change history for a device."""
    history = await _engine().get_change_history(device_id, limit=limit)
    return {"device_id": device_id, "changes": history, "total": len(history)}


@router.get("/{change_id}")
async def get_change(change_id: str):
    """Get a single change request with approvals and rollbacks."""
    cr = await _engine().get_request(change_id)
    if not cr:
        raise HTTPException(status_code=404, detail="Change request not found")
    return cr


@router.post("/{change_id}/approve")
async def approve_change(change_id: str, body: dict = {}):
    """Approve a pending change request."""
    return await _engine().approve_request(
        change_id,
        approved_by=body.get("approved_by", "web-admin"),
        notes=body.get("notes"),
    )


@router.post("/{change_id}/reject")
async def reject_change(change_id: str, body: dict = {}):
    """Reject a pending change request."""
    return await _engine().reject_request(
        change_id,
        rejected_by=body.get("rejected_by", "web-admin"),
        reason=body.get("reason"),
    )


@router.post("/{change_id}/execute")
async def execute_change(change_id: str):
    """Execute an approved change with pre/post checks and auto-rollback."""
    result = await _engine().execute_change(change_id)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.post("/{change_id}/rollback")
async def rollback_change(change_id: str, body: dict = {}):
    """Rollback an applied/failed change using its rollback plan."""
    result = await _engine().rollback_change(
        change_id,
        executed_by=body.get("executed_by", "web-admin"),
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.post("/{change_id}/apply")
async def apply_change(change_id: str):
    """Apply an approved change (simple deploy, no pre/post checks)."""
    return await _engine().apply_request(change_id)
