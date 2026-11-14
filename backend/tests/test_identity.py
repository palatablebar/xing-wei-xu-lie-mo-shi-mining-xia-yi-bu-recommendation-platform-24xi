"""身份切分单测：拆片 / 匿名归因 / 冲突待处理 / 身份合并 / 后缀 / 逐位覆盖 / 确定性。"""
from __future__ import annotations

from src.constants.enums import AttributionResolution, FragmentStatus
from src.services.identity import (
    FragmentSpec,
    identity_sequences,
    latest_suffixes,
    pending_summary,
    split_session,
)


def _split(events, trace, *, owner="u001", session_id="s1", rank=0):
    return split_session(
        events, session_id=session_id, session_owner=owner, session_rank=rank, identity_trace=trace
    )


def test_splits_on_identity_switch_and_covers_every_event():
    events = ["board.view", "acct.login", "report.build", "acct.login", "board.save"]
    trace = [None, "u001", "u001", "u002", "u002"]
    fragments = _split(events, trace)
    assert [f.events for f in fragments] == [
        ("board.view",),
        ("acct.login", "report.build"),
        ("acct.login", "board.save"),
    ]
    # 逐位覆盖、互不重叠
    rebuilt: list[str] = []
    cursor = 0
    for fragment in fragments:
        assert fragment.start_index == cursor
        assert fragment.end_index >= fragment.start_index
        rebuilt.extend(fragment.events)
        cursor = fragment.end_index + 1
    assert rebuilt == events
    # 会话内出现 u001 / u002 两个账号 → 开头匿名片段无法唯一归属，进待处理区
    assert fragments[0].actor is None
    assert fragments[0].status == FragmentStatus.PENDING.value
    assert fragments[0].resolution == AttributionResolution.IDENTITY_CONFLICT.value
    assert fragments[1].actor == "u001" and fragments[2].actor == "u002"


def test_anonymous_segment_with_single_candidate_is_attributable():
    events = ["board.view", "board.filter", "acct.login", "report.build"]
    trace = [None, None, "u001", "u001"]
    fragments = _split(events, trace)
    assert len(fragments) == 2
    assert fragments[0].actor == "u001"
    assert fragments[0].status == FragmentStatus.ATTRIBUTABLE.value
    assert fragments[0].resolution == AttributionResolution.ANONYMOUS_SINGLE_CANDIDATE.value
    assert fragments[0].candidates == ("u001",)


def test_trace_missing_falls_back_to_session_owner_without_losing_events():
    events = ["a", "b", "c"]
    fragments = _split(events, None, owner="u009")
    assert len(fragments) == 1
    assert fragments[0].actor == "u009"
    assert list(fragments[0].events) == events


def test_empty_session_yields_no_fragments():
    assert _split([], []) == []


def test_anonymous_segment_with_multiple_candidates_is_pending():
    events = ["board.view", "acct.login", "board.save", "acct.login", "board.filter"]
    trace = [None, "u001", "u001", "u002", "u002"]
    fragments = _split(events, trace)
    anonymous = fragments[0]
    assert anonymous.actor is None
    assert anonymous.status == FragmentStatus.PENDING.value
    assert anonymous.resolution == AttributionResolution.IDENTITY_CONFLICT.value
    assert anonymous.candidates == ("u001", "u002")
    assert anonymous.reason


def test_fully_anonymous_session_is_identity_unknown():
    fragments = _split(["a", "b"], [None, None], owner="u003")
    assert fragments[0].actor is None
    assert fragments[0].resolution == AttributionResolution.IDENTITY_UNKNOWN.value
    assert fragments[0].status == FragmentStatus.PENDING.value


def test_pending_fragments_never_enter_sequences_or_suffixes():
    events = ["a", "b", "acct.login", "c", "acct.login", "d"]
    trace = [None, None, "u001", "u001", "u002", "u002"]
    fragments = _split(events, trace)
    merged = identity_sequences(fragments)
    actors = {actor for actor, _session, _events in merged}
    assert actors == {"u001", "u002"}
    assert all("a" not in seq and "b" not in seq for _a, _s, seq in merged)
    suffixes = latest_suffixes(fragments)
    assert set(suffixes) == {"u001", "u002"}


def test_same_identity_fragments_are_merged_within_session():
    events = ["acct.login", "a", "b"] + ["acct.login", "c"]
    trace = ["u001", "u001", "u001", None, "u001"]
    fragments = _split(events, trace)
    merged = identity_sequences(fragments)
    assert len(merged) == 1
    actor, session_id, seq = merged[0]
    assert actor == "u001" and session_id == "s1"
    assert seq == ["acct.login", "a", "b", "acct.login", "c"]  # 同身份跨待处理片段合并，但不含匿名事件


def test_latest_suffix_uses_newest_session_only():
    old = _split(
        ["acct.login", "board.view", "report.build", "report.export"],
        ["u001"] * 4,
        session_id="s1",
        rank=0,
    )
    new = _split(
        ["acct.login", "board.filter", "board.save", "alert.create"],
        ["u001"] * 4,
        session_id="s2",
        rank=5,
    )
    suffixes = latest_suffixes(old + new, k=3)
    assert suffixes["u001"] == ["board.filter", "board.save", "alert.create"]


def test_pending_summary_counts_fragments_events_sessions():
    conflict = _split(
        ["a", "acct.login", "b", "acct.login", "c"],
        [None, "u1", "u1", "u2", "u2"],
        session_id="s1",
    )
    unknown = _split(["x", "y"], [None, None], session_id="s2")
    summary = pending_summary(conflict + unknown)
    assert summary["pending_fragments"] == 2
    assert summary["pending_events"] == 3  # conflict 的 1 个匿名事件 + unknown 的 2 个
    assert summary["pending_sessions"] == 2
    reasons = {item["resolution"] for item in summary["by_reason"]}
    assert reasons == {
        AttributionResolution.IDENTITY_CONFLICT.value,
        AttributionResolution.IDENTITY_UNKNOWN.value,
    }


def test_split_is_deterministic():
    events = ["a", "acct.login", "b", "acct.login", "c"]
    trace = [None, "u1", "u1", "u2", "u2"]
    first = [fragment.as_dict() for fragment in _split(events, trace)]
    second = [fragment.as_dict() for fragment in _split(events, trace)]
    assert first == second
