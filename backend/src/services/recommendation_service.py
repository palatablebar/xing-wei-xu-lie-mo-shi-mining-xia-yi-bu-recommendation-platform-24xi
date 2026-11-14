"""推荐服务：名单、详情、复核、用户脱敏（viewer 只看脱敏 ID）。

推荐只由「可归属片段」产生；本服务同时返回待处理片段与重建前后差异，
供推荐台展示「哪些片段被排除、为什么、重建前后名单差多少」。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import MODULE_LABELS, REC_PRIORITY_LABELS, REVIEW_STATUS_LABELS
from src.models.event_def import EventDef
from src.models.mine_run import MineRun
from src.models.pattern import Pattern
from src.models.recommendation import Recommendation
from src.services import fragment_service
from src.services.sequence_service import mask_user
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc, utcnow


def event_labels(db: Session) -> dict[str, dict]:
    rows = db.scalars(select(EventDef)).all()
    return {
        row.code: {"code": row.code, "label": row.label, "module": row.module, "module_label": MODULE_LABELS.get(row.module, row.module)}
        for row in rows
    }


def serialize(rec: Recommendation, labels: dict[str, dict], run_no: str | None = None, *, masked: bool = False) -> dict:
    next_label = labels.get(rec.next_event, {"label": rec.next_event, "module": rec.next_module})
    suffix = [{"code": code, "label": labels.get(code, {}).get("label", code)} for code in (rec.suffix or [])]
    return {
        "id": rec.id,
        "run_id": rec.run_id,
        "run_no": run_no,
        "pattern_id": rec.pattern_id,
        "user_id": mask_user(rec.user_id) if masked else rec.user_id,
        "suffix": rec.suffix or [],
        "suffix_detail": suffix,
        "next_event": rec.next_event,
        "next_label": next_label.get("label", rec.next_event),
        "next_module": rec.next_module,
        "next_module_label": MODULE_LABELS.get(rec.next_module, rec.next_module),
        "confidence": rec.confidence,
        "lift": rec.lift,
        "score": rec.score,
        "priority": rec.priority,
        "priority_label": REC_PRIORITY_LABELS.get(rec.priority, rec.priority),
        "status": rec.status,
        "status_label": REVIEW_STATUS_LABELS.get(rec.status, rec.status),
        "reviewer": rec.reviewer,
        "review_note": rec.review_note,
        "created_at": iso_utc(rec.created_at),
        "updated_at": iso_utc(rec.updated_at),
    }


def list_recommendations(
    db: Session,
    *,
    run_id: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    module: str | None = None,
    q: str | None = None,
    masked: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(Recommendation)
    count_stmt = select(func.count()).select_from(Recommendation)
    if run_id:
        stmt = stmt.where(Recommendation.run_id == run_id)
        count_stmt = count_stmt.where(Recommendation.run_id == run_id)
    if status:
        stmt = stmt.where(Recommendation.status == status)
        count_stmt = count_stmt.where(Recommendation.status == status)
    if priority:
        stmt = stmt.where(Recommendation.priority == priority)
        count_stmt = count_stmt.where(Recommendation.priority == priority)
    if module:
        stmt = stmt.where(Recommendation.next_module == module)
        count_stmt = count_stmt.where(Recommendation.next_module == module)
    if q:
        like = f"%{q.strip()}%"
        clause = Recommendation.user_id.ilike(like) | Recommendation.next_event.ilike(like)
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        stmt.order_by(Recommendation.score.desc(), Recommendation.id).limit(limit).offset(offset)
    ).all()
    labels = event_labels(db)
    run_nos = {run.id: run.run_no for run in db.scalars(select(MineRun)).all()}
    return {
        "items": [serialize(row, labels, run_nos.get(row.run_id), masked=masked) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def context(db: Session, run_id: int | None, *, masked: bool = False) -> dict:
    """推荐台的上下文：待处理片段（排除原因）+ 该运行的重建前后差异。

    未指定运行时优先取「正在供数的运行」，失败运行不会顶替旧版名单。
    """
    from src.services import mining_service

    if run_id:
        run = db.get(MineRun, run_id)
    else:
        run = mining_service.serving_run(db)
    metrics = (run.metrics or {}) if run else {}
    pending = fragment_service.pending_fragments(db, masked=masked, limit=20)
    previous_id = (metrics.get("before") or {}).get("previous_run_id")
    # 失败运行时，真正在供数的是上一次可用运行；从库里补全它的信息供页面提示
    serving = mining_service.serving_run(db)
    recovered = (
        {"run_no": serving.run_no, "rec_count": serving.rec_count, "coverage_pct": serving.coverage_pct}
        if serving and run and run.status == "failed"
        else None
    )
    return {
        "run_id": run.id if run else None,
        "run_no": run.run_no if run else None,
        "run_status": run.status if run else None,
        "serving_run_id": serving.id if serving else None,
        "pending": pending["summary"],
        "pending_samples": pending["items"],
        "diff": metrics.get("diff"),
        "built": metrics.get("built"),
        "restored": metrics.get("restored"),
        "failed_run": (
            {"run_no": run.run_no, "status": run.status, "error": metrics.get("error")}
            if run and run.status == "failed"
            else None
        ),
        "recovered_from_run": previous_id,
        "recovered_run": recovered,
    }


def get_recommendation(db: Session, rec_id: int) -> Recommendation:
    row = db.get(Recommendation, rec_id)
    if row is None:
        raise not_found("recommendation", rec_id)
    return row


def detail(db: Session, rec_id: int, *, masked: bool = False) -> dict:
    row = get_recommendation(db, rec_id)
    labels = event_labels(db)
    run = db.get(MineRun, row.run_id)
    pattern = db.get(Pattern, row.pattern_id)
    data = serialize(row, labels, run.run_no if run else None, masked=masked)
    data["pattern"] = (
        {
            "id": pattern.id,
            "items": pattern.items,
            "items_detail": [labels.get(code, {"code": code, "label": code}) for code in (pattern.items or [])],
            "support_count": pattern.support_count,
            "support_pct": pattern.support_pct,
            "confidence": pattern.confidence,
            "lift": pattern.lift,
        }
        if pattern
        else None
    )
    data["run"] = {"id": run.id, "run_no": run.run_no, "coverage_pct": run.coverage_pct} if run else None
    return data


def review(db: Session, rec: Recommendation, *, status: str, note: str | None, reviewer: str) -> Recommendation:
    rec.status = status
    rec.review_note = note
    rec.reviewer = reviewer
    rec.updated_at = utcnow()
    db.flush()
    return rec


def priority_counts(db: Session, run_id: int | None = None) -> list[dict]:
    stmt = select(Recommendation.priority, func.count()).group_by(Recommendation.priority)
    if run_id:
        stmt = stmt.where(Recommendation.run_id == run_id)
    rows = db.execute(stmt).all()
    order = {"high": 0, "medium": 1, "low": 2}
    return [
        {"key": key, "label": REC_PRIORITY_LABELS.get(key, key), "count": count}
        for key, count in sorted(rows, key=lambda item: order.get(item[0], 9))
    ]
