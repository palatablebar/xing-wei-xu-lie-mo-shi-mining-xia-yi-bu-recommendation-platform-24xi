"""身份切分对挖掘链路的影响（SQLite 内存库集成测试）。

覆盖：
- 共享设备会话不再把整条行为记给最后登录者；
- 待处理片段留档但不参与挖掘与推荐；
- 重建前后差异可查；
- 重建失败时旧版推荐名单恢复、片段与会话记录不丢。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.models import Base
from src.models.event_def import EventDef
from src.models.event_seq import EventSeq
from src.models.fragment import SessionFragment
from src.models.mine_run import MineRun
from src.models.recommendation import Recommendation
from src.services import fragment_service, mining_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for code, module in [
            ("acct.login", "account"),
            ("board.view", "board"),
            ("board.filter", "board"),
            ("report.build", "report"),
            ("report.export", "report"),
            ("alert.create", "alert"),
        ]:
            session.add(EventDef(code=code, module=module, label=code, weight=1))
        session.flush()
        yield session
        session.rollback()


def _add_session(db: Session, session_id: str, owner: str, events: list[str], trace: list, hour: int = 0, converted=False):
    db.add(
        EventSeq(
            session_id=session_id,
            user_id=owner,
            events=events,
            event_count=len(events),
            identity_trace=trace,
            modules_path="",
            started_at=datetime(2026, 10, 1, 8) + timedelta(hours=hour),
            converted=converted,
        )
    )
    db.flush()


def _seed_normal(db: Session, count: int = 6) -> None:
    """普通会话：半数是完整四步，半数是三步前缀，保证能挖出可推荐的下一个事件。"""
    for index in range(count):
        events = ["acct.login", "board.view", "report.build", "report.export"]
        if index % 2:
            events = events[:-1]
        _add_session(
            db,
            f"n{index:03d}",
            f"u{index:03d}",
            events,
            [f"u{index:03d}"] * len(events),
            hour=index,
        )


def _seed_shared(db: Session) -> None:
    # 匿名浏览 → 甲登录 → 切到乙
    events = ["board.view", "acct.login", "report.build", "acct.login", "report.export", "board.filter"]
    trace = [None, "u900", "u900", "u901", "u901", None]
    _add_session(db, "shared-1", "u901", events, trace, hour=100)


def test_shared_session_is_split_not_credited_to_last_login(db: Session):
    _seed_normal(db)
    _seed_shared(db)
    run = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    assert run.status == "success"
    fragments = db.scalars(select(SessionFragment).where(SessionFragment.session_id == "shared-1")).all()
    # 匿名开头 / 甲 / 乙 / 匿名尾巴 = 4 段
    assert len(fragments) == 4
    # 两段匿名（开头与尾巴）都进待处理区，不记给最后登录的 u901
    pending = [row for row in fragments if row.status == "pending"]
    assert len(pending) == 2
    assert all(row.actor is None for row in pending)
    # 甲、乙各自的片段分别可归属
    actors = {row.actor for row in fragments if row.actor}
    assert actors == {"u900", "u901"}
    # 待处理片段保留原始事件下标，逐位可回溯
    for row in pending:
        assert row.end_index >= row.start_index
        assert row.event_count == len(row.events)


def test_pending_fragments_excluded_from_recommendations(db: Session):
    _seed_normal(db)
    _seed_shared(db)
    run = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all()
    users = {row.user_id for row in recs}
    # u901 的可归属片段只有「登录 + 导出」，不足以产生推荐；绝不能把整条共享会话记给他
    assert "u901" not in users or all("board.view" not in row.suffix for row in recs if row.user_id == "u901")
    pending_users = {row.actor for row in db.scalars(select(SessionFragment)).all() if not row.actor}
    assert None in pending_users


def test_run_records_before_after_diff_and_pending_reasons(db: Session):
    _seed_normal(db)
    _seed_shared(db)
    run = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    metrics = run.metrics
    assert metrics["diff"]["before"]["fragments_total"] == 0
    assert metrics["diff"]["after"]["fragments_total"] >= 1
    assert metrics["pending"]["pending_fragments"] >= 1
    reasons = {item["resolution"] for item in metrics["pending"]["by_reason"]}
    assert "identity_conflict" in reasons or "identity_unknown" in reasons


def test_second_run_reuses_fragments_and_stays_idempotent(db: Session):
    _seed_normal(db)
    _seed_shared(db)
    first = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    before = db.scalar(select(func.count()).select_from(SessionFragment))
    second = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    after = db.scalar(select(func.count()).select_from(SessionFragment))
    assert after == before  # 片段不重复膨胀
    assert second.metrics["built"]["created"] == 0
    assert first.id != second.id


def test_failure_restores_old_recommendations_and_keeps_records(db: Session, monkeypatch):
    _seed_normal(db)
    _seed_shared(db)
    first = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    old_recs = db.scalars(select(Recommendation).where(Recommendation.run_id == first.id)).all()
    old_count = len(old_recs)
    assert old_count > 0
    old_review = {row.id: row.status for row in old_recs}

    def boom(*_args, **_kwargs):
        raise RuntimeError("identity rebuild exploded")

    monkeypatch.setattr(fragment_service, "build_fragments", boom)
    second = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    assert second.status == "failed"
    assert second.metrics["restored"]["restored"] == old_count
    # 旧版推荐名单原样恢复（含复核状态）
    restored = db.scalars(select(Recommendation).where(Recommendation.run_id == first.id)).all()
    assert {row.id: row.status for row in restored} == old_review
    # 会话与片段记录一条都没少
    assert db.scalar(select(func.count()).select_from(EventSeq)) == 7
    assert db.scalar(select(func.count()).select_from(SessionFragment)) > 0
    # 失败运行不留下可用于推荐的半成品
    assert db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.run_id == second.id)) == 0


def test_latest_run_after_failure_points_at_restored_mode(db: Session, monkeypatch):
    _seed_normal(db)
    first = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(fragment_service, "build_fragments", boom)
    second = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    assert second.status == "failed"
    assert "恢复旧版推荐名单" in (second.log_text or "")
    # 旧运行仍可查询，推荐仍挂在旧 run 上
    assert db.get(MineRun, first.id).rec_count == first.rec_count


def test_shortened_source_session_retires_stale_fragments_to_pending(db: Session):
    _seed_normal(db)
    _seed_shared(db)
    first = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    assert first.status == "success"
    before = db.scalar(select(func.count()).select_from(SessionFragment))
    assert before > 0
    # 源会话被削减：旧片段不能丢，要降级为「源会话变更」待处理
    shared = db.scalar(select(EventSeq).where(EventSeq.session_id == "shared-1"))
    shared.events = list(shared.events)[:2]
    shared.identity_trace = list(shared.identity_trace)[:2]
    shared.event_count = 2
    db.flush()
    second = mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    assert second.status == "success"
    after = db.scalar(select(func.count()).select_from(SessionFragment))
    assert after >= before  # 只增不删
    retired = db.scalars(
        select(SessionFragment).where(SessionFragment.resolution == "source_changed")
    ).all()
    assert retired
    assert all(row.status == "pending" and row.actor is None for row in retired)
    # 降级片段不产生推荐
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == second.id)).all()
    assert all(row.user_id for row in recs)


def test_attributable_fragment_events_match_source_slice(db: Session):
    _seed_normal(db)
    _seed_shared(db)
    mining_service.create_and_execute(
        db, min_support_pct=30, max_len=5, top_k=50, trigger="manual", operator_email="analyst@x.io"
    )
    shared = db.scalar(select(EventSeq).where(EventSeq.session_id == "shared-1"))
    for fragment in db.scalars(
        select(SessionFragment).where(SessionFragment.session_id == "shared-1")
    ).all():
        sliced = list(shared.events)[fragment.start_index : fragment.end_index + 1]
        assert list(fragment.events) == sliced
