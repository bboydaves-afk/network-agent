"""REST API routes for runbook automation management."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter()


class RunbookCreateRequest(BaseModel):
    yaml_content: str


class RunbookExecuteRequest(BaseModel):
    context: Optional[dict] = None
    dry_run: bool = False


@router.get("/runbooks")
async def list_runbooks():
    """List all configured runbooks."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        return []
    runbooks = ctx.automation_engine.list_runbooks()
    return [
        {
            "name": rb.name,
            "description": rb.description,
            "enabled": rb.enabled,
            "trigger_type": rb.trigger_type,
            "tags": rb.tags,
            "version": rb.version,
            "actions_count": len(rb.actions),
            "cooldown": rb.cooldown,
        }
        for rb in runbooks
    ]


@router.get("/runbooks/{name}")
async def get_runbook(name: str):
    """Get detailed runbook configuration."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    rb = ctx.automation_engine.get_runbook(name)
    if not rb:
        raise HTTPException(status_code=404, detail=f"Runbook '{name}' not found")
    return {
        "name": rb.name,
        "version": rb.version,
        "description": rb.description,
        "enabled": rb.enabled,
        "tags": rb.tags,
        "trigger_type": rb.trigger_type,
        "trigger_alert_match": rb.trigger_alert_match,
        "trigger_schedule_cron": rb.trigger_schedule_cron,
        "trigger_schedule_timezone": rb.trigger_schedule_timezone,
        "trigger_webhook_match": rb.trigger_webhook_match,
        "conditions": rb.conditions,
        "cooldown": rb.cooldown,
        "limits": rb.limits,
        "actions": rb.actions,
        "escalation": rb.escalation,
        "file_path": rb.file_path,
    }


@router.post("/runbooks")
async def create_runbook(req: RunbookCreateRequest):
    """Create a new runbook from YAML content."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    try:
        rb = await ctx.automation_engine.add_runbook(req.yaml_content)
        await ctx.audit_logger.log(
            actor="api", action_type="runbook_created",
            description=f"Runbook '{rb.name}' created", runbook_name=rb.name,
        )
        return {"status": "created", "name": rb.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/runbooks/{name}")
async def update_runbook(name: str, req: RunbookCreateRequest):
    """Update an existing runbook."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    try:
        rb = await ctx.automation_engine.update_runbook(name, req.yaml_content)
        await ctx.audit_logger.log(
            actor="api", action_type="runbook_updated",
            description=f"Runbook '{name}' updated", runbook_name=name,
        )
        return {"status": "updated", "name": rb.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/runbooks/{name}")
async def delete_runbook(name: str):
    """Delete a runbook."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    deleted = await ctx.automation_engine.delete_runbook(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Runbook '{name}' not found")
    await ctx.audit_logger.log(
        actor="api", action_type="runbook_deleted",
        description=f"Runbook '{name}' deleted", runbook_name=name,
    )
    return {"status": "deleted", "name": name}


@router.post("/runbooks/{name}/enable")
async def enable_runbook(name: str):
    """Enable a runbook."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    await ctx.automation_engine.enable_runbook(name)
    return {"status": "enabled", "name": name}


@router.post("/runbooks/{name}/disable")
async def disable_runbook(name: str):
    """Disable a runbook."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    await ctx.automation_engine.disable_runbook(name)
    return {"status": "disabled", "name": name}


@router.post("/runbooks/{name}/execute")
async def execute_runbook(name: str, req: RunbookExecuteRequest = RunbookExecuteRequest()):
    """Manually execute a runbook."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    try:
        exec_id = await ctx.automation_engine.execute_runbook(
            name, context=req.context, dry_run=req.dry_run
        )
        return {"status": "executing", "execution_id": exec_id, "runbook": name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/runbooks/reload")
async def reload_runbooks():
    """Reload all runbooks from disk."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    count = await ctx.automation_engine.reload_runbooks()
    return {"status": "reloaded", "count": count}


@router.get("/executions")
async def list_executions(limit: int = 50, status: str = None, runbook_name: str = None):
    """List runbook execution history."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        return []
    return await ctx.automation_engine.list_executions(
        limit=limit, status=status, runbook_name=runbook_name
    )


@router.get("/executions/{exec_id}")
async def get_execution(exec_id: str):
    """Get details of a specific execution."""
    from interfaces.web.app import ctx
    if not ctx.automation_engine:
        raise HTTPException(status_code=503, detail="Automation not enabled")
    execution = ctx.automation_engine.get_execution(exec_id)
    if not execution:
        # Try database
        execution = await ctx.db.get_runbook_execution(exec_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution


@router.get("/jobs")
async def list_scheduled_jobs():
    """List all scheduled jobs."""
    from interfaces.web.app import ctx
    if not ctx.scheduler_manager:
        return []
    return ctx.scheduler_manager.list_jobs()


@router.post("/jobs/{job_id}/run")
async def run_job_now(job_id: str):
    """Trigger immediate execution of a scheduled job."""
    from interfaces.web.app import ctx
    if not ctx.scheduler_manager:
        raise HTTPException(status_code=503, detail="Scheduler not enabled")
    try:
        await ctx.scheduler_manager.run_job_now(job_id)
        return {"status": "triggered", "job_id": job_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    """Pause a scheduled job."""
    from interfaces.web.app import ctx
    if not ctx.scheduler_manager:
        raise HTTPException(status_code=503, detail="Scheduler not enabled")
    ctx.scheduler_manager.pause_job(job_id)
    return {"status": "paused", "job_id": job_id}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """Resume a paused job."""
    from interfaces.web.app import ctx
    if not ctx.scheduler_manager:
        raise HTTPException(status_code=503, detail="Scheduler not enabled")
    ctx.scheduler_manager.resume_job(job_id)
    return {"status": "resumed", "job_id": job_id}
