"""/v1/programs: NH programs for the accounts their invoices open (docs/api-contract.md, "NH programs").

Every answer is decided on the server from the owner's invoice rows (app/programs/access.py): a program, month or
invoice that isn't this account's is the 404 of a missing resource, and a failed claim is one generic answer whatever
the reason. PROGRAMS_MODE=off (or an account qa doesn't list): every route is a 404 except the list, which is empty.
Logs: ids and outcomes, never an invoice number."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, client_info, get_auth_service, get_principal
from app.auth.service import AuthService
from app.db.models import Program, ProgramModule
from app.db.session import get_session
from app.errors import NotFound
from app.logging import get_logger
from app.programs import service
from app.programs.access import programs_allow
from app.programs.schemas import (
    SLUG,
    BankTopicOut,
    ClaimOut,
    ClaimRequest,
    MonthDetailOut,
    ProgramListOut,
    ProgramMaterial,
    ProgramMonthOut,
    ProgramOut,
    ProgramRef,
)

router = APIRouter(prefix="/v1/programs", tags=["programs"])
log = get_logger("app.programs")
NO_STORE = {"Cache-Control": "no-store"}
NOT_VERIFIED = "We couldn't verify this invoice."


def _material(material: service.Material) -> ProgramMaterial:
    return ProgramMaterial(documents=material.documents, lessons=material.lessons, questions=material.questions)


def _program_out(program: Program, months: list[tuple[ProgramModule, service.Material]]) -> ProgramOut:
    return ProgramOut(
        slug=program.slug,
        title=program.title,
        months=[
            ProgramMonthOut(month=module.month, title=module.title, material=_material(material))
            for module, material in months
        ],
    )


def _require_allowed(request: Request, principal: Principal) -> None:
    if not programs_allow(principal.user, request.app.state.settings):
        raise NotFound()


@router.get("", response_model=ProgramListOut)
async def list_programs(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """The programs this account's invoices open now (the sidebar), and whether to ask for an invoice."""
    response.headers.update(NO_STORE)
    if not programs_allow(principal.user, request.app.state.settings):
        return ProgramListOut(enabled=False, programs=[], invoice_prompt=False)
    programs = await service.open_programs(db, principal.user.id)
    months = await service.months(db, [program.id for program in programs])
    prompt = not programs and principal.user.invoice_prompt_done_at is None
    return ProgramListOut(
        enabled=True,
        programs=[_program_out(program, months.get(program.id, [])) for program in programs],
        invoice_prompt=prompt,
    )


@router.post("/claims", response_model=ClaimOut, responses={201: {"model": ClaimOut}})
async def claim_invoice(
    body: ClaimRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
):
    """Claim an NH invoice for this account: 201 (claimed now), 200 (this account's already), else one generic 404
    (invoice_not_verified) whether it is unknown, revoked, expired or another account's. Counted before the lookup:
    5 an hour and 20 a day per account, 30 a day per network address."""
    response.headers.update(NO_STORE)
    _require_allowed(request, principal)
    user_id = principal.user.id  # read now: a rollback below expires the session's objects
    await auth.throttle_invoice(principal.user, client_info(request).ip)  # commits the count
    number = service.normalize_invoice(body.invoice_number)
    secret = request.app.state.settings.secret_key.get_secret_value()
    fingerprint = service.invoice_fingerprint(number, secret) if number else None
    found = await service.claim(db, user_id, body.invoice_number)
    if found is None:
        await db.rollback()
        log.info("program claim refused", extra={"user_id": str(user_id), "invoice": fingerprint})
        raise NotFound(NOT_VERIFIED, code="invoice_not_verified")
    await db.commit()
    log.info(
        "program claim",
        extra={
            "user_id": str(user_id),
            "invoice": fingerprint,
            "program": found.program.slug,
            "status": found.status,
        },
    )
    response.status_code = status.HTTP_201_CREATED if found.status == "claimed" else status.HTTP_200_OK
    return ClaimOut(status=found.status, program=ProgramRef(slug=found.program.slug, title=found.program.title))


@router.post("/invoice-prompt/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_invoice_prompt(
    request: Request, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
):
    """ "Continue without an invoice": the first-run prompt is not shown again (Settings can still add one)."""
    _require_allowed(request, principal)
    await service.dismiss_prompt(db, principal.user.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


@router.get("/{slug}", response_model=ProgramOut)
async def get_program(
    request: Request,
    response: Response,
    slug: str = Path(pattern=SLUG, max_length=60),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(NO_STORE)
    _require_allowed(request, principal)
    program = await service.open_program(db, principal.user.id, slug)
    if program is None:
        raise NotFound()
    months = await service.months(db, [program.id])
    return _program_out(program, months.get(program.id, []))


@router.get("/{slug}/months/{month}", response_model=MonthDetailOut)
async def get_month(
    request: Request,
    response: Response,
    slug: str = Path(pattern=SLUG, max_length=60),
    month: int = Path(ge=1, le=12),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    response.headers.update(NO_STORE)
    _require_allowed(request, principal)
    found = await service.open_month(db, principal.user.id, slug, month)
    if found is None:
        raise NotFound()
    program, module = found
    months = await service.months(db, [program.id])
    material = next((mat for item, mat in months.get(program.id, []) if item.id == module.id), service.Material())
    lesson_ids, topics, quiz = await service.month_detail(db, module)
    return MonthDetailOut(
        program=ProgramRef(slug=program.slug, title=program.title),
        month=module.month,
        title=module.title,
        material=_material(material),
        lesson_ids=lesson_ids,
        topics=[BankTopicOut(topic_id=t[0], title=t[1], chapter=t[2], book=t[3]) for t in topics],
        quiz_available=quiz,
    )
