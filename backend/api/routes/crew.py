"""Crew management API routes."""
from fastapi import APIRouter, HTTPException
from typing import List

from database.models import CrewMember
from database.crew_repository import get_sign_on_crew, get_sign_off_crew, get_crew_by_id

router = APIRouter(prefix="/crew", tags=["crew"])


@router.get("/sign-on", response_model=List[dict])
async def list_sign_on_crew():
    """Return all crew available for sign-on."""
    return await get_sign_on_crew()


@router.get("/sign-off", response_model=List[dict])
async def list_sign_off_crew():
    """Return all crew currently onboard (available for sign-off)."""
    return await get_sign_off_crew()


@router.get("/{crew_id}", response_model=dict)
async def get_crew_member(crew_id: str):
    crew = await get_crew_by_id(crew_id)
    if not crew:
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    return crew
