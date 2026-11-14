"""会话片段：共享设备场景下按身份切换拆出的可追溯行为片段。

一条 `event_seqs` 会话会被拆成 1..n 个连续片段：同一身份（账号）的连续事件组成一段。
- 可归属片段（status=attributable）带 `actor`，可以进入模式挖掘与推荐；
- 待处理片段（status=pending）`actor` 为空，只留档与展示，绝不参与挖掘/推荐。
`events` 保存原始事件 code，`start_index`/`end_index` 指向源会话下标，保证记录不丢失、可回溯。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class SessionFragment(Base):
    __tablename__ = "session_fragments"
    # 唯一性放在 build_key（会话 + 片段号 + 事件区间 + 事件内容的指纹）上：
    # 源会话变更后旧片段会以「源会话变更」待处理留在原表，不与新片段冲突。
    __table_args__ = (UniqueConstraint("build_key", name="uq_fragments_build_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    seq_id: Mapped[int] = mapped_column(ForeignKey("event_seqs.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    fragment_no: Mapped[int] = mapped_column(Integer, default=0)
    start_index: Mapped[int] = mapped_column(Integer, default=0)
    end_index: Mapped[int] = mapped_column(Integer, default=0)
    events: Mapped[list] = mapped_column(JSON, default=list)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    build_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    actor: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    session_owner: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    resolution: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
