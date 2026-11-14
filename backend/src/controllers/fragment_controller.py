"""身份片段控制器：待处理区查询 + 身份重建（写操作记领域审计）。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, fragment_service, mining_service
from src.services.sequence_service import mask_user


def list_fragments(db: Session, principal: dict, **filters) -> dict:
    masked = principal["role"] == "viewer"
    return fragment_service.list_fragments(db, masked=masked, **filters)


def pending(db: Session, principal: dict, limit: int, offset: int) -> dict:
    return fragment_service.pending_fragments(
        db, masked=principal["role"] == "viewer", limit=limit, offset=offset
    )


def coverage(db: Session) -> dict:
    return fragment_service.coverage(db)


def rebuild(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    run = mining_service.create_and_execute(
        db,
        min_support_pct=payload.get("min_support_pct", 3.0),
        max_len=payload.get("max_len", 5),
        top_k=payload.get("top_k", 50),
        trigger=payload.get("trigger") or "manual",
        operator_email=principal["email"],
        rebuild_fragments_only=True,
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="identity.rebuild",
        entity="session_fragment",
        entity_id=None,
        detail={
            "run_no": run.run_no,
            "status": run.status,
            "coverage": (run.metrics or {}).get("coverage"),
            "pending": (run.metrics or {}).get("pending"),
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return mining_service.serialize_run(run)


def mask_value(user_id: str) -> str:
    return mask_user(user_id)
