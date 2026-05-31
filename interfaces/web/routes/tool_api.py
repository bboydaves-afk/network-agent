"""Tool API endpoints for fleet orchestrator integration.

Exposes TOOLS and TOOL_HANDLERS from the AI agent interface over HTTP,
allowing the fleet orchestrator to discover and execute tools remotely.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tool-api"])


class ToolExecuteRequest(BaseModel):
    tool_name: str
    params: dict[str, Any] = {}


@router.get("/api/tools")
async def list_tools(_user: dict = Depends(get_current_user)):
    """List all available AI tools."""
    from interfaces.ai_agent.tools import NETWORK_TOOLS

    return {"tools": list(NETWORK_TOOLS), "count": len(NETWORK_TOOLS)}


@router.post("/api/tools/execute")
async def execute_tool(req: ToolExecuteRequest, _user: dict = Depends(get_current_user)):
    """Execute an AI tool by name."""
    try:
        from interfaces.ai_agent.handlers import TOOL_HANDLERS
    except ImportError:
        raise HTTPException(status_code=501, detail="Tool execution not available")

    from ..app import ctx

    handler = TOOL_HANDLERS.get(req.tool_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {req.tool_name}")

    db = ctx.db if hasattr(ctx, 'db') else None
    cred_mgr = ctx.cred_manager if hasattr(ctx, 'cred_manager') else None
    config = ctx.config_manager if hasattr(ctx, 'config_manager') else {}

    try:
        result = await handler(req.params, db, cred_mgr, config)
        if result is None:
            return {"status": "ok"}
        if not isinstance(result, dict):
            return {"result": result}
        return result
    except Exception as exc:
        logger.exception("Tool execution error (%s): %s", req.tool_name, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/tools/search")
async def search_tools(q: str = "", _user: dict = Depends(get_current_user)):
    """Search tools by name or description."""
    from interfaces.ai_agent.tools import NETWORK_TOOLS

    if not q:
        return {"tools": list(NETWORK_TOOLS), "count": len(NETWORK_TOOLS), "query": q}

    q_lower = q.lower()
    matches = [
        t for t in NETWORK_TOOLS
        if q_lower in t.get("name", "").lower()
        or q_lower in t.get("description", "").lower()
    ]
    return {"tools": matches, "count": len(matches), "query": q}
