from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, get_principal, get_settings_dep
from app.db.models import (
    PREFERENCE_CHOICES,
    EngineeringProfile,
    LearnerProfile,
    LearnerSubject,
    LearningPreferences,
)
from app.db.session import get_session
from app.errors import RateLimited, VersionConflict
from app.learning import catalog
from app.learning.context import SubjectChoice
from app.learning.schemas import (
    AcademicCatalogOut,
    EngineeringCatalogOut,
    EngineeringProfileOut,
    EngineeringProfileSet,
    LearnerProfileOut,
    LearnerProfileSet,
    LearningPreferencesOut,
    LearningPreferencesSet,
    ReplaceSubjectsRequest,
    SubjectIn,
    SubjectOut,
    SubjectsOut,
    TutorPreviewOut,
    TutorPreviewRequest,
    UpdateEngineeringProfileRequest,
    UpdateLearnerProfileRequest,
    UpdateLearningPreferencesRequest,
)
from app.learning.service import LearningService
from app.learning.tutor import MAX_TUTOR_BLOCK_CHARS

# The one reach into another domain: the preview shows the "Practice so far" section too.
from app.progress.learning import now, tutor_progress
from app.settings import Settings

router = APIRouter(prefix="/v1", tags=["learning"])

CATALOG_BODY = AcademicCatalogOut.model_validate(catalog.as_json()).model_dump(mode="json", by_alias=True)
CATALOG_HEADERS = {"ETag": catalog.ETAG, "Cache-Control": "public, max-age=3600, must-revalidate"}
ENGINEERING_BODY = EngineeringCatalogOut.model_validate(catalog.engineering_json()).model_dump(
    mode="json", by_alias=True
)
# Behind a session, so a shared cache must not keep it.
ENGINEERING_HEADERS = {"ETag": catalog.ENGINEERING_ETAG, "Cache-Control": "private, max-age=3600, must-revalidate"}


def get_learning_service(db: AsyncSession = Depends(get_session)) -> LearningService:
    return LearningService(db)


def _columns(model: type[BaseModel]) -> dict[str, str]:
    """camelCase field name -> column name."""
    return {field.alias or name: name for name, field in model.model_fields.items()}


PROFILE_COLUMNS = _columns(LearnerProfileSet)
PREFERENCE_COLUMNS = _columns(LearningPreferencesSet)
ENGINEERING_COLUMNS = _columns(EngineeringProfileSet)


def _cached(request: Request, etag: str) -> bool:
    """The client already has this version (`If-None-Match`)."""
    sent = {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}
    return etag in sent or "*" in sent


def profile_out(row: LearnerProfile | None) -> LearnerProfileOut:
    if row is None:
        return LearnerProfileOut.model_validate(
            {name: None for name in LearnerProfileOut.model_fields}
            | {
                "version": 0,
                "weak_topics": [],
            }
        )
    return LearnerProfileOut(
        version=row.version,
        grade=row.grade,
        board=row.board,
        board_state=row.board_state,
        medium=row.medium,
        academic_year=row.academic_year,
        main_goal=row.main_goal,
        target_exam=row.target_exam,
        target_score_percent=row.target_score_percent,
        weak_topics=list(row.weak_topics),
        daily_active_minutes_goal=row.daily_active_minutes_goal,
        weekly_active_minutes_goal=row.weekly_active_minutes_goal,
        preferred_study_time=row.preferred_study_time.strftime("%H:%M") if row.preferred_study_time else None,
        short_term_goal=row.short_term_goal,
        long_term_goal=row.long_term_goal,
        exam_date=row.exam_date,
        updated_at=row.updated_at,
    )


def engineering_out(row: EngineeringProfile | None) -> EngineeringProfileOut:
    if row is None:
        return EngineeringProfileOut(
            version=0,
            role=None,
            experience_level=None,
            focus_areas=[],
            platforms=[],
            certification_goal=None,
            updated_at=None,
        )
    return EngineeringProfileOut(
        version=row.version,
        role=row.role,
        experience_level=row.experience_level,
        focus_areas=list(row.focus_areas),
        platforms=list(row.platforms),
        certification_goal=row.certification_goal,
        updated_at=row.updated_at,
    )


def subjects_out(version: int, rows: list[LearnerSubject]) -> SubjectsOut:
    return SubjectsOut(
        version=version,
        items=[
            SubjectOut(code=row.subject_code, is_priority=row.is_priority, needs_improvement=row.needs_improvement)
            for row in rows
        ],
    )


def preferences_out(row: LearningPreferences | None) -> LearningPreferencesOut:
    values = {name: getattr(row, name) if row else "auto" for name in PREFERENCE_CHOICES}
    return LearningPreferencesOut(
        version=row.version if row else 0, updated_at=row.updated_at if row else None, **values
    )


def _conflict(current) -> VersionConflict:
    return VersionConflict(current=current.model_dump(mode="json", by_alias=True))


def _choices(items: list[SubjectIn]) -> list[SubjectChoice]:
    return [SubjectChoice(item.code, item.is_priority, item.needs_improvement) for item in items]


# ----------------------------------------------------------------------- catalog
@router.get("/catalog/academic", response_model=AcademicCatalogOut, tags=["catalog"])
async def academic_catalog(request: Request):
    """The values a learner profile may use. Public and the same for everyone: cache it by its
    ETag (`If-None-Match` answers 304). Codes are permanent; `deprecated` ones are shown but can
    no longer be chosen anew."""
    if _cached(request, catalog.ETAG):
        return Response(status_code=304, headers=CATALOG_HEADERS)
    return JSONResponse(content=CATALOG_BODY, headers=CATALOG_HEADERS)


@router.get("/catalog/engineering", response_model=EngineeringCatalogOut, tags=["catalog"])
async def engineering_catalog(request: Request, principal: Principal = Depends(get_principal)):
    """The values an Engineering Profile may use. The same for everyone, behind a session: cache it
    privately by its ETag (`If-None-Match` answers 304). Codes are permanent; `deprecated` ones are
    shown but can no longer be chosen anew."""
    if _cached(request, catalog.ENGINEERING_ETAG):
        return Response(status_code=304, headers=ENGINEERING_HEADERS)
    return JSONResponse(content=ENGINEERING_BODY, headers=ENGINEERING_HEADERS)


# ----------------------------------------------------------- engineering profile
@router.get("/engineering/profile", response_model=EngineeringProfileOut, tags=["engineering"])
async def get_engineering_profile(
    principal: Principal = Depends(get_principal), learning: LearningService = Depends(get_learning_service)
):
    """What the assistant is told about the user's work. Version 0 and empty values until first saved."""
    user_id = principal.user.id
    return engineering_out(await learning.get_engineering(user_id))


@router.patch("/engineering/profile", response_model=EngineeringProfileOut, tags=["engineering"])
async def update_engineering_profile(
    body: UpdateEngineeringProfileRequest,
    principal: Principal = Depends(get_principal),
    learning: LearningService = Depends(get_learning_service),
):
    """Versioned edit: `{version, set, reset}` (docs/api-contract.md). Codes must be in the engineering
    catalog (422 `unknown_code`); a deprecated one may only be kept (422 `deprecated`). Lists are
    distinct, at most 8 focus areas and 12 platforms. A stale version is 409 with the current profile."""
    user_id = principal.user.id
    changes = body.set.model_dump(include=body.set.model_fields_set)
    reset = [ENGINEERING_COLUMNS[name] for name in body.reset]
    try:
        row = await learning.update_engineering(user_id, body.version, changes, reset)
    except VersionConflict:
        raise _conflict(engineering_out(await learning.get_engineering(user_id))) from None
    return engineering_out(row)


# ----------------------------------------------------------------------- profile
@router.get("/learning/profile", response_model=LearnerProfileOut)
async def get_learner_profile(
    principal: Principal = Depends(get_principal), learning: LearningService = Depends(get_learning_service)
):
    user_id = principal.user.id
    return profile_out(await learning.get_profile(user_id))


@router.patch("/learning/profile", response_model=LearnerProfileOut)
async def update_learner_profile(
    body: UpdateLearnerProfileRequest,
    principal: Principal = Depends(get_principal),
    learning: LearningService = Depends(get_learning_service),
):
    """Versioned edit: `{version, set, reset}` (docs/api-contract.md). Catalog codes must exist;
    a deprecated one may only be kept. `boardState` needs board `state`."""
    user_id = principal.user.id
    changes = body.set.model_dump(include=body.set.model_fields_set)
    reset = [PROFILE_COLUMNS[name] for name in body.reset]
    try:
        row = await learning.update_profile(user_id, body.version, changes, reset)
    except VersionConflict:
        raise _conflict(profile_out(await learning.get_profile(user_id))) from None
    return profile_out(row)


# ---------------------------------------------------------------------- subjects
@router.get("/learning/subjects", response_model=SubjectsOut)
async def get_subjects(
    principal: Principal = Depends(get_principal), learning: LearningService = Depends(get_learning_service)
):
    user_id = principal.user.id
    return subjects_out(*await learning.get_subjects(user_id))


@router.put("/learning/subjects", response_model=SubjectsOut)
async def replace_subjects(
    body: ReplaceSubjectsRequest,
    principal: Principal = Depends(get_principal),
    learning: LearningService = Depends(get_learning_service),
):
    """Replaces the whole list; its order is the order to show. Versioned like a PATCH."""
    user_id = principal.user.id
    try:
        version, rows = await learning.replace_subjects(user_id, body.version, _choices(body.items))
    except VersionConflict:
        raise _conflict(subjects_out(*await learning.get_subjects(user_id))) from None
    return subjects_out(version, rows)


# ------------------------------------------------------------------- preferences
@router.get("/learning/preferences", response_model=LearningPreferencesOut)
async def get_learning_preferences(
    principal: Principal = Depends(get_principal), learning: LearningService = Depends(get_learning_service)
):
    user_id = principal.user.id
    return preferences_out(await learning.get_preferences(user_id))


@router.patch("/learning/preferences", response_model=LearningPreferencesOut)
async def update_learning_preferences(
    body: UpdateLearningPreferencesRequest,
    principal: Principal = Depends(get_principal),
    learning: LearningService = Depends(get_learning_service),
):
    """Versioned edit: `{version, set, reset}`; `reset` returns a preference to `auto`."""
    user_id = principal.user.id
    changes = body.set.model_dump(include=body.set.model_fields_set)
    reset = [PREFERENCE_COLUMNS[name] for name in body.reset]
    try:
        row = await learning.update_preferences(user_id, body.version, changes, reset)
    except VersionConflict:
        raise _conflict(preferences_out(await learning.get_preferences(user_id))) from None
    return preferences_out(row)


# ------------------------------------------------------------------ tutor preview
@router.post("/learning/tutor-preview", response_model=TutorPreviewOut)
async def tutor_preview(
    body: TutorPreviewRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    learning: LearningService = Depends(get_learning_service),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    """What the assistant would be told with these (unsaved) edits: the same function the chat
    uses, with the same "Practice so far", so the preview is exactly what the next message is
    told. Nothing is saved."""
    user_id = principal.user.id
    if not request.app.state.tutor_preview_limiter.allow(str(user_id)):
        raise RateLimited()
    progress = await tutor_progress(db, user_id, principal.user.timezone, settings.default_timezone, now())
    instructions = await learning.preview_instructions(
        user_id,
        body.profile.model_dump(include=body.profile.model_fields_set) if body.profile else None,
        _choices(body.subjects) if body.subjects is not None else None,
        body.preferences.model_dump(include=body.preferences.model_fields_set) if body.preferences else None,
        body.engineering.model_dump(include=body.engineering.model_fields_set) if body.engineering else None,
        progress,
    )
    return TutorPreviewOut(instructions=instructions, max_chars=MAX_TUTOR_BLOCK_CHARS)
