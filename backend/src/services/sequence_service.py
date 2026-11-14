"""会话序列服务：浏览、筛选、脱敏、模块路径。

序列浏览展示的是「源会话」（原样保留，便于追溯）；身份切分后的片段见
`fragment_service` 与 `#/sequences` 的片段视图。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import MODULE_LABELS
from src.models.event_def import EventDef
from src.models.event_seq import EventSeq
from src.models.fragment import SessionFragment
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc


def mask_user(user_id: str) -> str:
    if len(user_id) <= 3:
        return user_id[:1] + "**"
    return user_id[:2] + "**" + user_id[-1]


def event_labels(db: Session) -> dict[str, dict]:
    rows = db.scalars(select(EventDef)).all()
    return {row.code: {"label": row.label, "module": row.module, "module_label": MODULE_LABELS.get(row.module, row.module)} for row in rows}


def serialize(sequence: EventSeq, labels: dict[str, dict], *, masked: bool = False, fragments: list[dict] | None = None) -> dict:
    events = list(sequence.events or [])
    decorated = [
        {
            "code": code,
            "label": labels.get(code, {}).get("label", code),
            "module": labels.get(code, {}).get("module", "board"),
            "module_label": labels.get(code, {}).get("module_label", "看板"),
        }
        for code in events
    ]
    data = {
        "id": sequence.id,
        "session_id": sequence.session_id,
        "user_id": mask_user(sequence.user_id) if masked else sequence.user_id,
        "events": events,
        "events_detail": decorated,
        "event_count": sequence.event_count or len(events),
        "modules_path": sequence.modules_path,
        "converted": sequence.converted,
        "started_at": iso_utc(sequence.started_at),
    }
    if fragments is not None:
        data["fragments"] = fragments
    return data


def session_fragments(db: Session, seq_id: int, *, masked: bool = False) -> list[dict]:
    """一条源会话的身份片段（按片段序号），供序列抽屉追溯身份切换。"""
    from src.services import fragment_service

    rows = db.scalars(
        select(SessionFragment)
        .where(SessionFragment.seq_id == seq_id)
        .order_by(SessionFragment.fragment_no)
    ).all()
    return [fragment_service.serialize(row, masked=masked) for row in rows]


def fragment_counts(db: Session) -> dict[str, dict]:
    """每个会话的片段数与待处理数，供列表页提示。"""
    from src.constants.enums import FragmentStatus

    rows = db.execute(
        select(SessionFragment.session_id, SessionFragment.status, func.count()).group_by(
            SessionFragment.session_id, SessionFragment.status
        )
    ).all()
    out: dict[str, dict] = {}
    for session_id, status, count in rows:
        entry = out.setdefault(session_id, {"fragments": 0, "pending": 0})
        entry["fragments"] += count
        if status == FragmentStatus.PENDING.value:
            entry["pending"] += count
    return out


def list_sequences(
    db: Session,
    *,
    q: str | None = None,
    user_id: str | None = None,
    converted: bool | None = None,
    min_len: int | None = None,
    masked: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(EventSeq)
    count_stmt = select(func.count()).select_from(EventSeq)
    if q:
        like = f"%{q.strip()}%"
        clause = EventSeq.session_id.ilike(like) | EventSeq.user_id.ilike(like)
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
    if user_id:
        stmt = stmt.where(EventSeq.user_id == user_id)
        count_stmt = count_stmt.where(EventSeq.user_id == user_id)
    if converted is not None:
        stmt = stmt.where(EventSeq.converted.is_(converted))
        count_stmt = count_stmt.where(EventSeq.converted.is_(converted))
    if min_len:
        stmt = stmt.where(EventSeq.event_count >= min_len)
        count_stmt = count_stmt.where(EventSeq.event_count >= min_len)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(EventSeq.started_at.desc(), EventSeq.id.desc()).limit(limit).offset(offset)).all()
    labels = event_labels(db)
    counts = fragment_counts(db)
    items = []
    for row in rows:
        extra = counts.get(row.session_id, {"fragments": 0, "pending": 0})
        item = serialize(row, labels, masked=masked)
        item.update(extra)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_sequence(db: Session, sequence_id: int, *, masked: bool = False) -> dict:
    row = db.get(EventSeq, sequence_id)
    if row is None:
        raise not_found("sequence", sequence_id)
    return serialize(
        row,
        event_labels(db),
        masked=masked,
        fragments=session_fragments(db, row.id, masked=masked),
    )


def latest_suffixes(db: Session, k: int = 3) -> dict[str, list[str]]:
    """每位可归属身份最近一条会话的最后 k 个事件（待处理片段不参与）。"""
    from src.services import fragment_service

    return fragment_service.latest_suffixes(db)


def sequence_stats(db: Session) -> dict:
    total = db.scalar(select(func.count()).select_from(EventSeq)) or 0
    converted = db.scalar(select(func.count()).select_from(EventSeq).where(EventSeq.converted.is_(True))) or 0
    users = db.scalar(select(func.count(func.distinct(EventSeq.user_id)))) or 0
    avg_len = db.scalar(select(func.avg(EventSeq.event_count))) or 0
    fragment_rows = db.scalars(select(SessionFragment)).all()
    attributable = [row for row in fragment_rows if row.actor]
    pending = [row for row in fragment_rows if not row.actor]
    return {
        "sequences_total": total,
        "converted_total": converted,
        "conversion_pct": round(converted / total * 100, 1) if total else 0.0,
        "users_total": users,
        "identities_total": len({row.actor for row in attributable}),
        "avg_events": round(float(avg_len), 2),
        "fragments_total": len(fragment_rows),
        "attributable_fragments": len(attributable),
        "pending_fragments": len(pending),
        "pending_events": sum(row.event_count for row in pending),
        "pending_sessions": len({row.session_id for row in pending}),
    }
