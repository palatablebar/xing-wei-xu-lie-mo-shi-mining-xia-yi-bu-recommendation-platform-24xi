"""身份切分｜把一条会话按身份切换拆成可追溯片段（纯函数，无 DB、无 IO、确定性）。

背景：共享设备上的匿名会话会在登录后落到不同账号。旧实现把整条会话记给最后登录者，
导致模式、推荐名单与覆盖人数互相矛盾。这里只做身份归因：

- `identity_trace` 与事件逐位对齐：值 = 该事件发生时生效的账号；`None` = 尚未登录（匿名）。
- 没有 `identity_trace` 的旧会话按「整条属于会话记录账号」处理，保证历史记录不丢失。
- 匿名片段：若整条会话只出现一个候选账号，则归到该账号（可归属）；出现多个候选账号
  则判为关联冲突（待处理）；一个候选都没有则判为身份未知（待处理）。
- 片段用 `start_index`/`end_index` 指向源会话，逐事件连续覆盖、互不重叠，可回溯。
- 待处理片段 `actor=None`，永远不进入模式挖掘与推荐。
"""
from __future__ import annotations

from dataclasses import dataclass

from src.constants.enums import (
    ATTRIBUTION_REASON_LABELS,
    AttributionResolution,
    FragmentStatus,
)

LOGIN_EVENT = "acct.login"


@dataclass(frozen=True)
class FragmentSpec:
    session_id: str
    session_owner: str
    session_rank: int
    fragment_no: int
    start_index: int
    end_index: int
    events: tuple[str, ...]
    actor: str | None
    status: str
    resolution: str
    reason: str | None
    candidates: tuple[str, ...]

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def attributable(self) -> bool:
        return self.status == FragmentStatus.ATTRIBUTABLE.value and bool(self.actor)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_owner": self.session_owner,
            "fragment_no": self.fragment_no,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "events": list(self.events),
            "event_count": self.event_count,
            "actor": self.actor,
            "status": self.status,
            "resolution": self.resolution,
            "reason": self.reason,
            "candidates": list(self.candidates),
        }


def _normalized_trace(events: list[str], session_owner: str, identity_trace: list | None) -> list[str | None]:
    """逐位身份标记；trace 缺失或长度不符时按会话记录账号兜底。"""
    if identity_trace is None or len(identity_trace) != len(events):
        return [session_owner] * len(events)
    return [str(value).strip() if value not in (None, "") else None for value in identity_trace]


def split_session(
    events: list[str],
    *,
    session_id: str,
    session_owner: str,
    session_rank: int = 0,
    identity_trace: list | None = None,
) -> list[FragmentSpec]:
    """按身份切换把一条会话拆成连续片段，逐事件完整覆盖。"""
    event_list = list(events or [])
    if not event_list:
        return []
    trace = _normalized_trace(event_list, session_owner, identity_trace)
    candidates = tuple(sorted({marker for marker in trace if marker}))

    runs: list[tuple[int, int, str | None]] = []
    start = 0
    for index in range(1, len(trace) + 1):
        if index == len(trace) or trace[index] != trace[start]:
            runs.append((start, index - 1, trace[start]))
            start = index

    fragments: list[FragmentSpec] = []
    for fragment_no, (begin, end, marker) in enumerate(runs, start=1):
        chunk = tuple(event_list[begin : end + 1])
        if marker is not None:
            actor, resolution = marker, AttributionResolution.LOGIN.value
            status, reason, fragment_candidates = FragmentStatus.ATTRIBUTABLE.value, None, ()
        elif len(candidates) == 1:
            actor = candidates[0]
            status = FragmentStatus.ATTRIBUTABLE.value
            resolution = AttributionResolution.ANONYMOUS_SINGLE_CANDIDATE.value
            reason, fragment_candidates = None, candidates
        else:
            actor = None
            status = FragmentStatus.PENDING.value
            resolution = (
                AttributionResolution.IDENTITY_CONFLICT.value
                if len(candidates) > 1
                else AttributionResolution.IDENTITY_UNKNOWN.value
            )
            reason = ATTRIBUTION_REASON_LABELS[resolution]
            fragment_candidates = candidates
        fragments.append(
            FragmentSpec(
                session_id=session_id,
                session_owner=session_owner,
                session_rank=session_rank,
                fragment_no=fragment_no,
                start_index=begin,
                end_index=end,
                events=chunk,
                actor=actor,
                status=status,
                resolution=resolution,
                reason=reason,
                candidates=fragment_candidates,
            )
        )
    return fragments


def identity_sequences(fragments: list[FragmentSpec]) -> list[tuple[str, str, list[str]]]:
    """把同一会话内可归属片段合到同一身份，返回 [(actor, session_id, 事件序列), ...]。

    每个 (actor, 会话) 一条序列；待处理片段被排除，既不参与拼接，也不为它补位。
    """
    grouped: dict[tuple[str, str], list[FragmentSpec]] = {}
    for fragment in fragments:
        if not fragment.attributable or fragment.actor is None:
            continue
        grouped.setdefault((fragment.actor, fragment.session_id), []).append(fragment)

    merged: list[tuple[str, str, list[str]]] = []
    for (actor, session_id), chunks in grouped.items():
        events: list[str] = []
        for fragment in sorted(chunks, key=lambda item: item.fragment_no):
            events.extend(fragment.events)
        if events:
            merged.append((actor, session_id, events))
    merged.sort(key=lambda item: (item[0], item[1]))
    return merged


def latest_suffixes(fragments: list[FragmentSpec], k: int = 3) -> dict[str, list[str]]:
    """每位可归属身份取最近一条会话的最后 k 个事件。

    会话新旧按 `session_rank`（由调用方按 started_at 排序赋值）。同一会话内该身份的
    多个可归属片段按片段序号拼接后再取尾部，不跨会话混用。
    """
    latest: dict[str, tuple[int, str]] = {}
    by_session: dict[tuple[str, str], list[FragmentSpec]] = {}
    for fragment in fragments:
        if not fragment.attributable or fragment.actor is None:
            continue
        by_session.setdefault((fragment.actor, fragment.session_id), []).append(fragment)
        rank = (fragment.session_rank, fragment.session_id)
        if fragment.actor not in latest or rank > latest[fragment.actor]:
            latest[fragment.actor] = rank

    out: dict[str, list[str]] = {}
    for actor, (rank, session_id) in latest.items():
        chunks = sorted(by_session[(actor, session_id)], key=lambda item: item.fragment_no)
        events: list[str] = []
        for fragment in chunks:
            events.extend(fragment.events)
        if events:
            out[actor] = events[-k:]
    return out


def pending_summary(fragments: list[FragmentSpec]) -> dict:
    """待处理片段的统计，供总览/推荐台/审计展示（按判定原因聚合）。"""
    pending = [fragment for fragment in fragments if not fragment.attributable]
    totals: dict[str, int] = {}
    for fragment in pending:
        totals[fragment.resolution] = totals.get(fragment.resolution, 0) + 1
    return {
        "pending_fragments": len(pending),
        "pending_events": sum(fragment.event_count for fragment in pending),
        "pending_sessions": len({fragment.session_id for fragment in pending}),
        "by_reason": [
            {
                "resolution": resolution,
                "label": ATTRIBUTION_REASON_LABELS.get(resolution, resolution),
                "count": count,
            }
            for resolution, count in sorted(totals.items())
        ],
    }
