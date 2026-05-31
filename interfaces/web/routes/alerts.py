"""Alert management API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status

from core.models import AlertRuleCreate

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


# ---- Alerts ------------------------------------------------------------------


@router.get("/")
async def list_alerts(
    status_filter: Optional[str] = None,
    limit: int = 50,
):
    """Return alerts, optionally filtered by status (active/acknowledged/resolved)."""
    ctx = _ctx()
    kwargs = {"limit": limit}
    if status_filter:
        kwargs["status"] = status_filter
    alerts = await ctx.db.get_alerts(**kwargs)
    return {"alerts": alerts, "total": len(alerts)}


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Mark an alert as acknowledged."""
    ctx = _ctx()
    alert = await ctx.db.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    updated = await ctx.db.update_alert(alert_id, status="acknowledged")

    # Broadcast alert update via WebSocket
    from interfaces.web.websockets import manager
    await manager.broadcast("alerts", {
        "type": "alert_update",
        "alert_id": alert_id,
        "status": "acknowledged",
        "data": updated,
    })

    return updated


@router.post("/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    """Mark an alert as resolved."""
    ctx = _ctx()
    alert = await ctx.db.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    updated = await ctx.db.update_alert(alert_id, status="resolved")

    from interfaces.web.websockets import manager
    await manager.broadcast("alerts", {
        "type": "alert_update",
        "alert_id": alert_id,
        "status": "resolved",
        "data": updated,
    })

    return updated


# ---- Alert Rules -------------------------------------------------------------


@router.get("/rules")
async def list_alert_rules():
    """Return all configured alert rules."""
    ctx = _ctx()
    rules = await ctx.db.get_alert_rules()
    return {"rules": rules, "total": len(rules)}


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_alert_rule(rule: AlertRuleCreate):
    """Create a new alert rule."""
    ctx = _ctx()
    created = await ctx.db.add_alert_rule(rule)
    return created


@router.put("/rules/{rule_id}")
async def update_alert_rule(rule_id: str, body: dict):
    """Update an existing alert rule."""
    ctx = _ctx()
    existing = await ctx.db.get_alert_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    updated = await ctx.db.update_alert_rule(rule_id, body)
    return updated


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(rule_id: str):
    """Delete an alert rule."""
    ctx = _ctx()
    existing = await ctx.db.get_alert_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await ctx.db.delete_alert_rule(rule_id)
    return None
