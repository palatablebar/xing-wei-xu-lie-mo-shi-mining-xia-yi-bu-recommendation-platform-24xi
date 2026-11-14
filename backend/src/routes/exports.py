from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import EXPORT_ROLES
from src.controllers import export_controller
from src.middlewares.rbac import require_roles

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/recommendations.csv")
def recommendations_csv(
    request: Request,
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
):
    return export_controller.recommendations_csv(db, run_id, principal, request)


@router.get("/patterns.csv")
def patterns_csv(
    request: Request,
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
):
    return export_controller.patterns_csv(db, run_id, principal, request)


@router.get("/fragments.csv")
def fragments_csv(
    request: Request,
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
):
    return export_controller.fragments_csv(db, status, principal, request)


@router.get("/mine-runs.csv")
def runs_csv(
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*EXPORT_ROLES)),
):
    return export_controller.runs_csv(db, principal, request)
