"""REST API routes for audit log access."""

import logging
from fastapi import APIRouter, HTTPException
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def get_audit_log(
    device_id: Optional[str] = None,
    actor: Optional[str] = None,
    execution_id: Optional[str] = None,
    action_type: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
):
    """Query the audit log with optional filters."""
    from interfaces.web.app import ctx
    if not ctx.audit_logger:
        return []
    entries = await ctx.audit_logger.get_log(
        device_id=device_id,
        actor=actor,
        execution_id=execution_id,
        action_type=action_type,
        since=since,
        limit=limit,
    )
    return entries


@router.get("/{entry_id}")
async def get_audit_entry(entry_id: str):
    """Get a specific audit log entry."""
    from interfaces.web.app import ctx
    if not ctx.audit_logger:
        raise HTTPException(status_code=503, detail="Audit logger not available")
    entry = await ctx.audit_logger.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    return entry


@router.get("/execution/{execution_id}")
async def get_execution_audit(execution_id: str):
    """Get all audit entries for a specific runbook execution."""
    from interfaces.web.app import ctx
    if not ctx.audit_logger:
        return []
    return await ctx.audit_logger.get_execution_audit(execution_id)
