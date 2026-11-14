"""身份切分服务：把会话落成片段、重建可归属身份序列、维护待处理区、审计与回滚。

落库口径（幂等，不丢记录）：
- 以 `(session, 片段序号, start_index, end_index, 事件列表)` 指纹为稳定键复用已有片段行；
- 已存在的片段只更新身份判定字段，绝不因重建而删除源会话或片段；
- 源会话事件变化后不再生成的旧片段，降级为「源会话变更」待处理留档；
- 重建失败时整体回滚并恢复旧版推荐名单（见 `mining_service.execute_run` 的备份/恢复）。
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    ATTRIBUTION_REASON_LABELS,
    FRAGMENT_STATUS_LABELS,
    RESOLUTION_LABELS,
    AttributionResolution,
    FragmentStatus,
)
from src.models.event_seq import EventSeq
from src.models.fragment import SessionFragment
from src.services import identity
from src.services.sequence_service import mask_user
from src.utils.timeutil import iso_utc, utcnow


def _fingerprint(session_id: str, fragment) -> str:
    payload = json.dumps(
        [session_id, fragment.fragment_no, fragment.start_index, fragment.end_index, list(fragment.events)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def build_fragments(db: Session, *, actor_email: str | None = None) -> dict:
    """扫描全部会话，按身份切换重建片段；逐位覆盖、保留历史、幂等。

    源会话事件发生变化时，旧片段不删除，而是降级为「源会话变更」待处理片段留档，
    既保证记录不丢失，也保证它们不会再参与挖掘与推荐。
    """
    rows = db.scalars(
        select(EventSeq).order_by(EventSeq.started_at, EventSeq.id)
    ).all()
    existing = {row.build_key: row for row in db.scalars(select(SessionFragment)).all() if row.build_key}
    rank_of = {row.session_id: index for index, row in enumerate(rows)}
    rendered: set[str] = set()
    created = updated = retired = 0
    session_count = 0
    for row in rows:
        specs = identity.split_session(
            list(row.events or []),
            session_id=row.session_id,
            session_owner=row.user_id,
            session_rank=rank_of.get(row.session_id, 0),
            identity_trace=row.identity_trace,
        )
        if not specs:
            continue
        session_count += 1
        for spec in specs:
            key = _fingerprint(row.session_id, spec)
            target = existing.get(key)
            if target is None:
                target = SessionFragment(seq_id=row.id, session_id=row.session_id)
                db.add(target)
                created += 1
            else:
                updated += 1
            rendered.add(key)
            target.build_key = key
            target.seq_id = row.id
            target.fragment_no = spec.fragment_no
            target.start_index = spec.start_index
            target.end_index = spec.end_index
            target.events = list(spec.events)
            target.event_count = spec.event_count
            target.actor = spec.actor
            target.session_owner = spec.session_owner
            target.status = spec.status
            target.resolution = spec.resolution
            target.reason = spec.reason
            target.candidates = list(spec.candidates)
            target.started_at = row.started_at
            target.updated_at = utcnow()
    # 未再生成的历史片段：降级为待处理留档，不删除
    for key, row in existing.items():
        if key in rendered:
            continue
        if row.status == FragmentStatus.PENDING.value and row.resolution == AttributionResolution.SOURCE_CHANGED.value:
            continue
        row.actor = None
        row.status = FragmentStatus.PENDING.value
        row.resolution = AttributionResolution.SOURCE_CHANGED.value
        row.reason = ATTRIBUTION_REASON_LABELS[AttributionResolution.SOURCE_CHANGED.value]
        row.candidates = []
        row.updated_at = utcnow()
        retired += 1
    db.flush()
    return {
        "sessions": session_count,
        "created": created,
        "updated": updated,
        "retired": retired,
        "reused": len(existing),
        "actor_email": actor_email,
    }


def load_specs(db: Session) -> list[identity.FragmentSpec]:
    """从片段表还原纯函数入参（供推荐后缀与统计复用同一口径）。"""
    rows = db.execute(
        select(SessionFragment, EventSeq)
        .join(EventSeq, SessionFragment.seq_id == EventSeq.id)
        .order_by(EventSeq.started_at, EventSeq.id, SessionFragment.fragment_no)
    ).all()
    rank_of: dict[str, int] = {}
    specs: list[identity.FragmentSpec] = []
    for fragment, seq in rows:
        if fragment.session_id not in rank_of:
            rank_of[fragment.session_id] = len(rank_of)
        specs.append(
            identity.FragmentSpec(
                session_id=fragment.session_id,
                session_owner=fragment.session_owner,
                session_rank=rank_of[fragment.session_id],
                fragment_no=fragment.fragment_no,
                start_index=fragment.start_index,
                end_index=fragment.end_index,
                events=tuple(fragment.events or []),
                actor=fragment.actor,
                status=fragment.status,
                resolution=fragment.resolution,
                reason=fragment.reason,
                candidates=tuple(fragment.candidates or []),
            )
        )
    return specs


def attributable_sequences(db: Session) -> list[tuple[str, str, list[str]]]:
    return identity.identity_sequences(load_specs(db))


def latest_suffixes(db: Session) -> dict[str, list[str]]:
    return identity.latest_suffixes(load_specs(db))


def resolution_label(resolution: str) -> str:
    return RESOLUTION_LABELS.get(resolution, resolution)


def status_label(status: str) -> str:
    return FRAGMENT_STATUS_LABELS.get(status, status)


def serialize(fragment: SessionFragment, *, masked: bool = False) -> dict:
    actor = mask_user(fragment.actor) if masked and fragment.actor else fragment.actor
    return {
        "id": fragment.id,
        "seq_id": fragment.seq_id,
        "session_id": fragment.session_id,
        "fragment_no": fragment.fragment_no,
        "start_index": fragment.start_index,
        "end_index": fragment.end_index,
        "events": list(fragment.events or []),
        "event_count": fragment.event_count,
        "actor": actor,
        "session_owner": mask_user(fragment.session_owner) if masked else fragment.session_owner,
        "status": fragment.status,
        "status_label": status_label(fragment.status),
        "resolution": fragment.resolution,
        "resolution_label": resolution_label(fragment.resolution),
        "reason": fragment.reason,
        "candidates": list(fragment.candidates or []),
        "attributable": fragment.status == FragmentStatus.ATTRIBUTABLE.value and bool(fragment.actor),
        "started_at": iso_utc(fragment.started_at),
    }


def list_fragments(
    db: Session,
    *,
    status: str | None = None,
    session_id: str | None = None,
    actor: str | None = None,
    masked: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(SessionFragment)
    count_stmt = select(func.count()).select_from(SessionFragment)
    if status:
        stmt = stmt.where(SessionFragment.status == status)
        count_stmt = count_stmt.where(SessionFragment.status == status)
    if session_id:
        stmt = stmt.where(SessionFragment.session_id == session_id)
        count_stmt = count_stmt.where(SessionFragment.session_id == session_id)
    if actor:
        stmt = stmt.where(SessionFragment.actor == actor)
        count_stmt = count_stmt.where(SessionFragment.actor == actor)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        stmt.order_by(SessionFragment.session_id, SessionFragment.fragment_no).limit(limit).offset(offset)
    ).all()
    return {
        "items": [serialize(row, masked=masked) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def pending_fragments(db: Session, *, masked: bool = False, limit: int = 20, offset: int = 0) -> dict:
    data = list_fragments(
        db,
        status=FragmentStatus.PENDING.value,
        masked=masked,
        limit=limit,
        offset=offset,
    )
    data["summary"] = pending_summary(db)
    return data


def pending_summary(db: Session) -> dict:
    """待处理区汇总：片段数、事件数、涉及会话数、按排除原因聚合。"""
    rows = db.scalars(
        select(SessionFragment).where(SessionFragment.status == FragmentStatus.PENDING.value)
    ).all()
    totals: dict[str, int] = defaultdict(int)
    reasons: dict[str, str] = {}
    for row in rows:
        totals[row.resolution] += 1
        if row.resolution not in reasons and row.reason:
            reasons[row.resolution] = row.reason
    return {
        "pending_fragments": len(rows),
        "pending_events": sum(row.event_count for row in rows),
        "pending_sessions": len({row.session_id for row in rows}),
        "by_reason": [
            {
                "resolution": resolution,
                "label": resolution_label(resolution),
                "count": count,
                "reason": reasons.get(resolution),
            }
            for resolution, count in sorted(totals.items())
        ],
    }


def pending_lines(db: Session, limit: int = 5) -> list[dict]:
    rows = db.scalars(
        select(SessionFragment)
        .where(SessionFragment.status == FragmentStatus.PENDING.value)
        .order_by(SessionFragment.session_id, SessionFragment.fragment_no)
        .limit(limit)
    ).all()
    return [
        {
            "session_id": row.session_id,
            "fragment_no": row.fragment_no,
            "actor": row.actor,
            "session_owner": row.session_owner,
            "reason": row.reason,
            "candidates": list(row.candidates or []),
        }
        for row in rows
    ]


def coverage(db: Session) -> dict:
    """覆盖人数按「可归属身份」口径统计，与推荐 active_users 保持一致。"""
    fragments = db.scalars(select(SessionFragment)).all()
    attributable = [row for row in fragments if row.status == FragmentStatus.ATTRIBUTABLE.value and row.actor]
    pending = [row for row in fragments if row.status == FragmentStatus.PENDING.value]
    actors = {row.actor for row in attributable}
    owners = {row.session_owner for row in fragments}
    return {
        "fragments_total": len(fragments),
        "attributable_fragments": len(attributable),
        "pending_fragments": len(pending),
        "pending_events": sum(row.event_count for row in pending),
        "identities_total": len(actors),
        "session_owners_total": len(owners),
    }


def restore_recommendations(db: Session, snapshot, *, failed_run_id: int | None) -> dict:
    """重建失败后恢复旧版推荐名单。

    - 若失败运行与旧版运行是不同记录，先清掉失败运行可能写入的半成品推荐；
    - 按快照把旧版推荐名单逐条写回（若记录仍在则复用，避免唯一键冲突）；
    - 返回恢复条数与覆盖人数，供运行日志与审计展示。
    """
    from src.models.recommendation import Recommendation

    if failed_run_id is not None and failed_run_id != snapshot.previous_run_id:
        db.query(Recommendation).filter(Recommendation.run_id == failed_run_id).delete()
    restored = 0
    users = 0
    for item in snapshot.recommendations:
        exists = db.scalar(
            select(Recommendation).where(
                Recommendation.run_id == item["run_id"],
                Recommendation.user_id == item["user_id"],
                Recommendation.pattern_id == item["pattern_id"],
                Recommendation.next_event == item["next_event"],
            )
        )
        if exists is None:
            exists = Recommendation(
                run_id=item["run_id"],
                pattern_id=item["pattern_id"],
                user_id=item["user_id"],
                next_event=item["next_event"],
            )
            db.add(exists)
        exists.suffix = list(item["suffix"])
        exists.next_module = item["next_module"]
        exists.confidence = item["confidence"]
        exists.lift = item["lift"]
        exists.score = item["score"]
        exists.priority = item["priority"]
        exists.status = item["status"]
        exists.reviewer = item["reviewer"]
        exists.review_note = item["review_note"]
        exists.created_at = item["created_at"]
        exists.updated_at = item["updated_at"]
        restored += 1
    users = len({item["user_id"] for item in snapshot.recommendations})
    db.flush()
    return {
        "restored": restored,
        "covered_users": users,
        "active_users": snapshot.coverage.get("identities_total", users),
    }
