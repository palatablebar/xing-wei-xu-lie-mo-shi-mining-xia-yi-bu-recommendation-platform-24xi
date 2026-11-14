from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import MINING_CREATE_ROLES
from src.controllers import fragment_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.schemas.mine_run import RebuildIn
from src.utils.pagination import page_params

router = APIRouter(prefix="/fragments", tags=["fragments"])


@router.get("")
def list_fragments(
    status: str | None = None,
    session_id: str | None = None,
    actor: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return fragment_controller.list_fragments(
        db,
        principal,
        status=status,
        session_id=session_id,
        actor=actor,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/pending")
def pending_fragments(
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return fragment_controller.pending(db, principal, page.limit, page.offset)


@router.get("/coverage")
def coverage(db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))) -> dict:
    return fragment_controller.coverage(db)


@router.post("/rebuild", status_code=201)
def rebuild(
    payload: RebuildIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*MINING_CREATE_ROLES)),
) -> dict:
    return fragment_controller.rebuild(db, payload.model_dump(), principal, request)
