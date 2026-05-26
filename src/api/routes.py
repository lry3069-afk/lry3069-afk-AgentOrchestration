"""API route definitions."""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import List, Dict, Optional

from src.agent import AgentRegistry, AgentStatus
from src.api.jwt_validator import validate_embedded_session_token, JWTValidationError

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


@router.post("/console/session")
async def create_embedded_console_session(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header[7:]
    try:
        claims = validate_embedded_session_token(token)
    except JWTValidationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {
        "status": "active",
        "session_id": claims.sub,
        "tenant_id": claims.tenant_id,
        "expires_at": claims.exp,
        "console_url": f"/console/{claims.tenant_id}/{claims.sub}",
    }