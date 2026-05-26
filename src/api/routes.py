"""API route definitions."""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import List, Dict, Optional

from src.agent import AgentRegistry, AgentStatus
from src.api.middleware import generate_csrf_token

router = APIRouter()
registry = AgentRegistry()


@router.get("/agents")
async def list_agents(
    status: Optional[str] = None, group: Optional[str] = None
):
    status_filter = AgentStatus(status) if status else None
    return {"agents": registry.list(status=status_filter, group=group)}


@router.post("/agents")
async def register_agent(name: str, agent_type: str, config: Optional[Dict] = None):
    agent_id = registry.register(name, agent_type, config)
    return {"agent_id": agent_id, "status": "registered"}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    if not registry.delete(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted"}


@router.post("/agents/{agent_id}/start")
async def start_agent(agent_id: str):
    if not registry.update_status(agent_id, AgentStatus.RUNNING):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "started"}


@router.post("/agents/{agent_id}/stop")
async def stop_agent(agent_id: str):
    if not registry.update_status(agent_id, AgentStatus.PAUSED):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "stopped"}


@router.get("/agents/count")
async def agent_count():
    return {"count": registry.count()}


@router.post("/org/switch")
async def switch_organization(request: Request):
    session_id = request.cookies.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=401, detail="No active session")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    target_org = body.get("organization_id", "")
    if not target_org:
        raise HTTPException(status_code=400, detail="Missing organization_id")
    # CSRF validation is handled by middleware
    # If we reach here, CSRF token is valid
    return {
        "status": "switched",
        "organization_id": target_org,
        "message": f"Active organization switched to {target_org}",
    }


@router.get("/csrf/token")
async def get_csrf_token(request: Request, organization_id: str):
    session_id = request.cookies.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=401, detail="No active session")
    if not organization_id:
        raise HTTPException(status_code=400, detail="Missing organization_id")
    token = generate_csrf_token(session_id, organization_id)
    return {"csrf_token": token, "organization_id": organization_id}