from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal
from app.db.models import MemoryItem
from app.db.session import get_session
from app.errors import NotFound
from app.memory.policy import MemoryKind
from app.memory.proposals import proposal_public
from app.memory.schemas import (
    ApproveProposalRequest,
    CorrectMemoryRequest,
    CreateMemoryRequest,
    DeleteMemoryRequest,
    MemoryExport,
    MemoryOut,
    MemoryPage,
    MemoryPreferencesOut,
    ProposalOut,
    ProposalPage,
    RejectProposalRequest,
    UpdateMemoryPreferencesRequest,
)
from app.memory.service import MemoryService

router = APIRouter(prefix="/v1/memories", tags=["memories"])


def memory_out(item: MemoryItem) -> MemoryOut:
    return MemoryOut(
        id=item.id,
        kind=item.kind,
        content=item.content,
        status=item.status,
        version=item.version,
        supersedes_id=item.supersedes_id,
        source_message_id=item.source_message_id,
        origin=item.origin,
        proposal_id=item.proposal_id,
        last_confirmed_at=item.last_confirmed_at,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
        processor_version=item.processor_version,
        expires_at=item.expires_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def get_memory_service(request: Request, db: AsyncSession = Depends(get_session)) -> MemoryService:
    return MemoryService(db, request.app.state.settings, request.app.state.embeddings)


def preferences_out(preferences) -> MemoryPreferencesOut:
    return MemoryPreferencesOut.model_validate(preferences.public())


@router.get("/preferences", response_model=MemoryPreferencesOut)
async def get_memory_preferences(
    principal: Principal = Depends(get_principal), memory: MemoryService = Depends(get_memory_service)
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    return preferences_out(await memory.get_preferences(principal.user.id))


@router.put("/preferences", response_model=MemoryPreferencesOut)
async def update_memory_preferences(
    body: UpdateMemoryPreferencesRequest,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    return preferences_out(
        await memory.update_preferences(
            principal.user.id,
            automatic_proposals=body.automatic_proposals,
            expected_version=body.expected_version,
        )
    )


@router.get("/proposals", response_model=ProposalPage)
async def list_memory_proposals(
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    rows, next_cursor = await memory.list_proposals(principal.user.id, limit, cursor)
    return ProposalPage(
        items=[ProposalOut.model_validate(proposal_public(row)) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/proposals/{proposal_id}/approve", response_model=MemoryOut)
async def approve_memory_proposal(
    proposal_id: uuid.UUID,
    body: ApproveProposalRequest,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    return memory_out(
        await memory.approve_proposal(
            principal.user.id,
            proposal_id,
            expected_version=body.expected_version,
            kind=body.kind,
            content=body.content,
            expires_at=body.expires_at,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            fields_set=body.model_fields_set,
        )
    )


@router.post("/proposals/{proposal_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_memory_proposal(
    proposal_id: uuid.UUID,
    body: RejectProposalRequest,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    await memory.reject_proposal(principal.user.id, proposal_id, body.expected_version)
    return Response(status_code=204)


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: CreateMemoryRequest,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    return memory_out(
        await memory.create(
            principal.user.id,
            kind=body.kind,
            content=body.content,
            source_message_id=body.source_message_id,
            expires_at=body.expires_at,
        )
    )


@router.get("/export", response_model=MemoryExport)
async def export_memories(
    principal: Principal = Depends(get_principal), memory: MemoryService = Depends(get_memory_service)
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    items = [memory_out(item) for item in await memory.export(principal.user.id)]
    return MemoryExport(exported_at=datetime.now(UTC), items=items)


@router.get("", response_model=MemoryPage)
async def list_memories(
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
    kind: MemoryKind | None = Query(None),
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    rows, next_cursor = await memory.list(principal.user.id, limit, cursor, kind)
    return MemoryPage(items=[memory_out(item) for item in rows], next_cursor=next_cursor)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_memories(
    principal: Principal = Depends(get_principal), memory: MemoryService = Depends(get_memory_service)
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    await memory.delete_all(principal.user.id)
    return Response(status_code=204)


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    return memory_out(await memory.get(principal.user.id, memory_id))


@router.put("/{memory_id}", response_model=MemoryOut)
async def correct_memory(
    memory_id: uuid.UUID,
    body: CorrectMemoryRequest,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    return memory_out(
        await memory.correct(
            principal.user.id,
            memory_id,
            expected_version=body.expected_version,
            kind=body.kind,
            content=body.content,
            expires_at=body.expires_at,
            fields_set=body.model_fields_set,
        )
    )


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    body: DeleteMemoryRequest,
    principal: Principal = Depends(get_principal),
    memory: MemoryService = Depends(get_memory_service),
):
    if not memory.settings.memory_enabled:
        raise NotFound()
    await memory.delete(principal.user.id, memory_id, body.expected_version)
    return Response(status_code=204)
