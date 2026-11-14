"""挖掘服务：身份重建 → PrefixSpan 挖掘 → 落模式库 → 生成推荐 → 统计覆盖率。

算法本身在 services/prefixspan.py、recommender.py 与 identity.py（纯函数、可单测）；
这里只做 DB 编排：读片段、写模式、写推荐、算指标、失败回滚。

关键约束：
- 只有「可归属片段」进入挖掘与推荐；待处理片段只留档与展示。
- 重建失败时恢复旧版推荐名单（快照 → 回滚），并把失败与差异写进运行记录。
- 每次运行都记录重建前后的片段/身份/推荐差异，供总览、推荐台与审计对照。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import MineStatus, REVIEW_STATUS_LABELS
from src.models.event_def import EventDef
from src.models.event_seq import EventSeq
from src.models.mine_run import MineRun
from src.models.pattern import Pattern
from src.models.recommendation import Recommendation
from src.services import fragment_service, identity, recommender
from src.services.prefixspan import MinedPattern, mine_patterns, min_support_count
from src.utils.errors import AppError
from src.utils.timeutil import iso_utc, local_now, utcnow

PATTERN_MIN_LEN = 2
DEFAULT_TOP_K = 50


@dataclass
class RunSnapshot:
    """重建前的现场：片段统计 + 旧推荐名单（用于失败恢复）。"""

    coverage: dict = field(default_factory=dict)
    recommendations: list[dict] = field(default_factory=list)
    previous_run_id: int | None = None
    previous_rec_count: int = 0

    def as_metrics(self) -> dict:
        return {
            "before": {
                "fragments_total": self.coverage.get("fragments_total", 0),
                "attributable_fragments": self.coverage.get("attributable_fragments", 0),
                "pending_fragments": self.coverage.get("pending_fragments", 0),
                "pending_events": self.coverage.get("pending_events", 0),
                "identities_total": self.coverage.get("identities_total", 0),
                "sessions_total": self.coverage.get("sessions_total", 0),
                "rec_count": self.previous_rec_count,
                "previous_run_id": self.previous_run_id,
            }
        }


def serialize_run(run: MineRun) -> dict:
    return {
        "id": run.id,
        "run_no": run.run_no,
        "min_support_pct": run.min_support_pct,
        "max_len": run.max_len,
        "top_k": run.top_k,
        "trigger": run.trigger,
        "status": run.status,
        "operator_email": run.operator_email,
        "sequence_count": run.sequence_count,
        "pattern_count": run.pattern_count,
        "rec_count": run.rec_count,
        "covered_users": run.covered_users,
        "active_users": run.active_users,
        "coverage_pct": run.coverage_pct,
        "metrics": run.metrics or {},
        "log_text": run.log_text,
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
    }


def next_run_no(db: Session) -> str:
    prefix = f"M{local_now():%Y%m%d}"
    count = db.scalar(select(func.count()).select_from(MineRun).where(MineRun.run_no.like(f"{prefix}%"))) or 0
    return f"{prefix}-{count + 1:04d}"


def get_run(db: Session, run_id: int) -> MineRun:
    row = db.get(MineRun, run_id)
    if row is None:
        raise AppError("NOT_FOUND", 404, {"entity": "mine_run", "id": run_id})
    return row


def latest_run(db: Session) -> MineRun | None:
    return db.scalar(select(MineRun).order_by(MineRun.id.desc()).limit(1))


def serving_run(db: Session) -> MineRun | None:
    """当前正在供数的运行：优先有推荐产出的运行，再退到有模式的运行。

    失败运行、以及重建后没有可归属序列的空跑，都不会顶替旧版名单。
    """
    row = db.scalar(
        select(MineRun)
        .where(MineRun.status.in_(("success", "partial")))
        .where(MineRun.rec_count > 0)
        .order_by(MineRun.id.desc())
        .limit(1)
    )
    if row is not None:
        return row
    row = db.scalar(
        select(MineRun)
        .where(MineRun.status.in_(("success", "partial")))
        .where(MineRun.pattern_count > 0)
        .order_by(MineRun.id.desc())
        .limit(1)
    )
    if row is not None:
        return row
    return db.scalar(
        select(MineRun)
        .where(MineRun.status.in_(("success", "partial")))
        .order_by(MineRun.id.desc())
        .limit(1)
    )


def snapshot_run(db: Session, run_id: int | None = None) -> MineRun | None:
    """上一次可供数的运行（排除当前运行）：失败运行不应取代旧版推荐。"""
    stmt = (
        select(MineRun)
        .where(MineRun.id != run_id)
        .where(MineRun.status.in_(("success", "partial")))
        .where(MineRun.rec_count > 0)
        .order_by(MineRun.id.desc())
        .limit(1)
    )
    row = db.scalar(stmt)
    if row is not None:
        return row
    fallback = select(MineRun).where(MineRun.id != run_id).where(MineRun.status.in_(("success", "partial")))
    return db.scalar(fallback.order_by(MineRun.id.desc()).limit(1))


def list_runs(db: Session, status: str | None, limit: int, offset: int) -> dict:
    stmt = select(MineRun)
    count_stmt = select(func.count()).select_from(MineRun)
    if status:
        stmt = stmt.where(MineRun.status == status)
        count_stmt = count_stmt.where(MineRun.status == status)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(MineRun.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [serialize_run(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _coverage_snapshot(db: Session) -> dict:
    sessions = db.scalar(select(func.count()).select_from(EventSeq)) or 0
    data = fragment_service.coverage(db)
    data["sessions_total"] = sessions
    return data


def _snapshot(db: Session, run_id: int | None = None) -> RunSnapshot:
    """取上一次可成功供数的运行作为旧版；排除当前运行自身，失败运行不顶替。"""
    previous = snapshot_run(db, run_id)
    coverage = _coverage_snapshot(db)
    recs: list[dict] = []
    if previous is not None:
        rows = db.scalars(select(Recommendation).where(Recommendation.run_id == previous.id)).all()
        recs = [
            {
                "run_id": row.run_id,
                "pattern_id": row.pattern_id,
                "user_id": row.user_id,
                "suffix": list(row.suffix or []),
                "next_event": row.next_event,
                "next_module": row.next_module,
                "confidence": row.confidence,
                "lift": row.lift,
                "score": row.score,
                "priority": row.priority,
                "status": row.status,
                "reviewer": row.reviewer,
                "review_note": row.review_note,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    return RunSnapshot(
        coverage=coverage,
        recommendations=recs,
        previous_run_id=previous.id if previous else None,
        previous_rec_count=len(recs),
    )


def _persist_patterns(db: Session, run: MineRun, mined: list[MinedPattern], seq_count: int) -> dict[tuple[str, ...], Pattern]:
    stored: dict[tuple[str, ...], Pattern] = {}
    for rank, item in enumerate(mined, start=1):
        data = item.as_dict(seq_count)
        row = db.scalar(select(Pattern).where(Pattern.run_id == run.id, Pattern.seq_key == data["seq_key"]))
        if row is None:
            row = Pattern(run_id=run.id, seq_key=data["seq_key"])
            db.add(row)
        row.items = data["items"]
        row.length = data["length"]
        row.support_count = data["support_count"]
        row.support_pct = data["support_pct"]
        row.confidence = data["confidence"]
        row.lift = data["lift"]
        row.rank = rank
        db.flush()
        stored[tuple(data["items"])] = row
    return stored


def _persist_recommendations(db: Session, run: MineRun, result: recommender.RecommendResult) -> int:
    by_pattern = {row.id: row for row in db.scalars(select(Pattern).where(Pattern.run_id == run.id)).all()}
    for item in result.recommendations:
        pattern = by_pattern.get(item.pattern_id)
        if pattern is None:
            continue
        exists = db.scalar(
            select(Recommendation).where(
                Recommendation.run_id == run.id,
                Recommendation.user_id == item.user_id,
                Recommendation.pattern_id == item.pattern_id,
                Recommendation.next_event == item.next_event,
            )
        )
        if exists is None:
            exists = Recommendation(
                run_id=run.id,
                pattern_id=item.pattern_id,
                user_id=item.user_id,
                suffix=[],
                next_event=item.next_event,
            )
            db.add(exists)
        exists.suffix = list(item.suffix)
        exists.next_module = item.module
        exists.confidence = item.confidence
        exists.lift = item.lift
        exists.score = item.score
        exists.priority = item.priority
    db.flush()
    return db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.run_id == run.id)) or 0


def _stage(db: Session, sessions_total: int) -> dict:
    """重建片段的统计（主要是给日志与差异用）。"""
    summary = fragment_service.pending_summary(db)
    data = fragment_service.coverage(db)
    data["sessions_total"] = sessions_total
    data.update(summary)
    return data


def diff_snapshot(before: dict, after: dict) -> dict:
    """重建前后差异：片段、身份、推荐数、待处理区、覆盖率。"""
    keys = (
        "fragments_total",
        "attributable_fragments",
        "pending_fragments",
        "pending_events",
        "identities_total",
        "rec_count",
    )
    return {
        "before": {key: before.get(key, 0) for key in keys},
        "after": {key: after.get(key, 0) for key in keys},
        "delta": {key: after.get(key, 0) - before.get(key, 0) for key in keys},
        "pending_reasons_after": after.get("pending_reasons_after", []),
    }


def execute_run(db: Session, run: MineRun) -> MineRun:
    run.status = "running"
    run.started_at = utcnow()
    run.metrics = {}
    snapshot = _snapshot(db, run.id)
    before = dict(snapshot.coverage)
    before["rec_count"] = snapshot.previous_rec_count
    try:
        # 片段重建与本次挖掘写入放在一个 savepoint 内：失败只回滚这一段，
        # 源会话、历史片段、旧版推荐与本次运行记录都保留。
        with db.begin_nested():
            built = fragment_service.build_fragments(db, actor_email=run.operator_email)
            specs = fragment_service.load_specs(db)
            sessions_total = db.scalar(select(func.count()).select_from(EventSeq)) or 0

            attributable = [spec for spec in specs if spec.attributable]
            merged = identity.identity_sequences(specs)
            sequences = [events for _actor, _session, events in merged]
            if not sequences:
                after = _stage(db, sessions_total)
                after["rec_count"] = snapshot.previous_rec_count
                after["pending_reasons_after"] = fragment_service.pending_summary(db)["by_reason"]
                run.status = MineStatus.PARTIAL.value
                run.sequence_count = 0
                run.pattern_count = 0
                run.rec_count = 0
                run.covered_users = 0
                run.active_users = 0
                run.coverage_pct = 0.0
                run.metrics = {
                    **snapshot.as_metrics(),
                    "built": built,
                    "diff": diff_snapshot(before, after),
                    "pending": fragment_service.pending_summary(db),
                    "reason": "no_attributable_sequences",
                }
                run.log_text = (
                    f"身份重建 {built['created']} 新 / {built['updated']} 复用片段，但没有任何可归属片段，"
                    "未生成模式与推荐；旧版推荐名单保持不变。"
                )
            else:
                mined = mine_patterns(
                    sequences, run.min_support_pct, run.max_len, run.top_k, min_len=PATTERN_MIN_LEN
                )
                stored = _persist_patterns(db, run, mined, len(sequences))
                module_of = {row.code: row.module for row in db.scalars(select(EventDef)).all()}
                suffixes = identity.latest_suffixes(specs)
                refs = [
                    recommender.PatternRef(
                        pattern_id=row.id,
                        items=tuple(row.items),
                        confidence=row.confidence,
                        lift=row.lift,
                    )
                    for row in stored.values()
                ]
                result = recommender.recommend(suffixes, refs, module_of)
                rec_count = _persist_recommendations(db, run, result)

                support_top = max((item.support_count for item in mined), default=0)
                run.sequence_count = len(sequences)
                run.pattern_count = len(stored)
                run.rec_count = rec_count
                run.covered_users = result.covered_users
                run.active_users = result.active_users
                run.coverage_pct = result.coverage_pct

                after = _stage(db, sessions_total)
                after["rec_count"] = rec_count
                after["pending_reasons_after"] = fragment_service.pending_summary(db)["by_reason"]
                run.metrics = {
                    **snapshot.as_metrics(),
                    "built": built,
                    "fragments_attributable": len(attributable),
                    "identities": len(suffixes),
                    "pending": fragment_service.pending_summary(db),
                    "diff": diff_snapshot(before, after),
                    "min_support_count": min_support_count(len(sequences), run.min_support_pct),
                    "frequent_total": len(mined),
                    "top_support_count": support_top,
                    "top_support_pct": round(support_top / len(sequences) * 100, 1) if sequences else 0.0,
                    "top_pattern": mined[0].seq_key if mined else None,
                    "avg_sequence_len": round(sum(len(s) for s in sequences) / len(sequences), 2) if sequences else 0.0,
                    "modules_total": len({module for module in module_of.values()}),
                    "convert_base_pct": round(
                        (
                            db.scalar(
                                select(func.count())
                                .select_from(EventSeq)
                                .where(EventSeq.converted.is_(True))
                            )
                            or 0
                        )
                        / len(sequences)
                        * 100,
                        1,
                    )
                    if sequences
                    else 0.0,
                }
                pending = run.metrics["pending"]
                run.status = MineStatus.SUCCESS.value if mined else MineStatus.PARTIAL.value
                run.log_text = (
                    f"身份重建 {built['created']} 新 / {built['updated']} 复用片段，"
                    f"可归属片段 {len(attributable)} 条，待处理 {pending['pending_fragments']} 条"
                    f"（{pending['pending_events']} 事件，{pending['pending_sessions']} 会话）；"
                    f"按身份合并 {len(sequences)} 条序列（平均 {run.metrics['avg_sequence_len']} 事件）；"
                    f"最小支持度 {run.min_support_pct}% => {run.metrics['min_support_count']} 条序列；"
                    f"命中频繁序列 {len(mined)} 条，模式库保留 {len(stored)} 条（top_k={run.top_k}，长度≥{PATTERN_MIN_LEN}）；"
                    f"生成推荐 {rec_count} 条，覆盖 {result.covered_users}/{result.active_users} 位可归属身份"
                    f"（{run.coverage_pct}%）。待处理片段不参与挖掘与推荐。"
                )
        run.finished_at = utcnow()
        db.flush()
        return run
    except Exception as exc:  # noqa: BLE001 - 运行失败要留痕并恢复旧版推荐
        build_error = str(exc)[:300]
        restored = fragment_service.restore_recommendations(db, snapshot, failed_run_id=run.id)
        run.status = MineStatus.FAILED.value
        run.started_at = run.started_at or utcnow()
        run.finished_at = utcnow()
        run.sequence_count = 0
        run.pattern_count = 0
        run.rec_count = 0
        run.covered_users = 0
        run.active_users = restored.get("active_users", 0)
        run.coverage_pct = 0.0
        run.metrics = {
            "error": build_error,
            "restored": restored,
            "before": snapshot.as_metrics()["before"],
            "diff": diff_snapshot(before, before),
        }
        run.log_text = (
            f"身份重建失败：{build_error}；已回滚本次写入（会话与片段记录未删除），"
            f"恢复旧版推荐名单 {restored['restored']} 条，仍来自运行 "
            f"{snapshot.previous_run_id or '无'}（覆盖 {restored['covered_users']}/{restored['active_users']}）。"
        )
        db.flush()
        return run


def create_and_execute(
    db: Session,
    *,
    min_support_pct: float,
    max_len: int,
    top_k: int,
    trigger: str,
    operator_email: str,
    seed_key: str | None = None,
    rebuild_fragments_only: bool = False,
) -> MineRun:
    if seed_key:
        existing = db.scalar(select(MineRun).where(MineRun.seed_key == seed_key))
        if existing is not None:
            return existing
    run = MineRun(
        run_no=next_run_no(db),
        min_support_pct=float(min_support_pct),
        max_len=int(max_len),
        top_k=int(top_k),
        trigger=trigger,
        operator_email=operator_email,
        status="running",
        seed_key=seed_key,
    )
    db.add(run)
    db.flush()
    if rebuild_fragments_only:
        return rebuild_identity(db, run)
    return execute_run(db, run)


def rebuild_identity(db: Session, run: MineRun) -> MineRun:
    """只做身份重建（不重挖）：用于人工触发/兜底，失败不删既有推荐。"""
    run.status = "running"
    run.started_at = utcnow()
    try:
        with db.begin_nested():
            built = fragment_service.build_fragments(db, actor_email=run.operator_email)
            coverage = _coverage_snapshot(db)
            run.status = MineStatus.PARTIAL.value
            run.metrics = {
                "rebuild": built,
                "coverage": coverage,
                "pending": fragment_service.pending_summary(db),
            }
            run.log_text = (
                f"仅重建身份片段：新建 {built['created']} 条、复用 {built['updated']} 条，"
                f"待处理 {coverage['pending_fragments']} 条；推荐名单未改动。"
            )
    except Exception as exc:  # noqa: BLE001
        run.status = MineStatus.FAILED.value
        run.metrics = {"error": str(exc)[:300]}
        run.log_text = f"身份重建失败：{exc}；既有片段与推荐名单保持不变。"
    run.finished_at = utcnow()
    db.flush()
    return run


def review_stats(db: Session, run_id: int | None = None) -> list[dict]:
    stmt = select(Recommendation.status, func.count()).group_by(Recommendation.status)
    if run_id:
        stmt = stmt.where(Recommendation.run_id == run_id)
    rows = db.execute(stmt).all()
    return [
        {"key": key, "label": REVIEW_STATUS_LABELS.get(key, key), "count": count}
        for key, count in sorted(rows, key=lambda item: item[0])
    ]
