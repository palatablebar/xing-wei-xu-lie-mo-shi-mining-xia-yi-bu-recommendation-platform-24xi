"""共享枚举（后端唯一定义处）。前端副本见 frontend/site/js/config.js。

README「共享枚举出现位置」一节列出全部出现点，改动需两侧同步。
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Module(StrEnum):
    """12 个埋点事件归属的 6 个功能模块（推荐「同模块最多 2 条」按此判定）。"""

    ACCOUNT = "account"
    BOARD = "board"
    REPORT = "report"
    ALERT = "alert"
    INTEGRATION = "integration"
    BILLING = "billing"


MODULE_LABELS: dict[str, str] = {
    Module.ACCOUNT.value: "账户",
    Module.BOARD.value: "看板",
    Module.REPORT.value: "报表",
    Module.ALERT.value: "告警",
    Module.INTEGRATION.value: "集成",
    Module.BILLING.value: "计费",
}

MODULE_ORDER: list[str] = [
    Module.ACCOUNT.value,
    Module.INTEGRATION.value,
    Module.BOARD.value,
    Module.REPORT.value,
    Module.ALERT.value,
    Module.BILLING.value,
]


class MineStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


MINE_STATUS_LABELS: dict[str, str] = {
    MineStatus.QUEUED.value: "排队中",
    MineStatus.RUNNING.value: "挖掘中",
    MineStatus.SUCCESS.value: "成功",
    MineStatus.PARTIAL.value: "部分成功",
    MineStatus.FAILED.value: "失败",
}

MINE_STATUS_ORDER: list[str] = [
    MineStatus.QUEUED.value,
    MineStatus.RUNNING.value,
    MineStatus.SUCCESS.value,
    MineStatus.PARTIAL.value,
    MineStatus.FAILED.value,
]


class FragmentStatus(StrEnum):
    """会话片段身份归属状态：可归属的片段才能进入模式挖掘与推荐。"""

    ATTRIBUTABLE = "attributable"
    PENDING = "pending"


FRAGMENT_STATUS_LABELS: dict[str, str] = {
    FragmentStatus.ATTRIBUTABLE.value: "可归属",
    FragmentStatus.PENDING.value: "待处理",
}


class AttributionResolution(StrEnum):
    """片段身份的判定方式（写进片段，便于追溯为什么不给某账号推荐）。"""

    LOGIN = "login"  # 片段内有明确登录信号
    SESSION_OWNER = "session_owner"  # 无身份切换信号，整条会话归原记录账号
    ANONYMOUS_SINGLE_CANDIDATE = "anonymous_single_candidate"  # 匿名片段，会话内仅一个登录账号
    IDENTITY_CONFLICT = "identity_conflict"  # 匿名片段，会话内多个登录账号，无法唯一归属
    IDENTITY_UNKNOWN = "identity_unknown"  # 匿名片段，会话内没有登录信号
    SOURCE_CHANGED = "source_changed"  # 源会话事件缩短后遗留的旧片段，保留在待处理区


RESOLUTION_LABELS: dict[str, str] = {
    AttributionResolution.LOGIN.value: "登录信号",
    AttributionResolution.SESSION_OWNER.value: "会话单账号（无切换信号）",
    AttributionResolution.ANONYMOUS_SINGLE_CANDIDATE.value: "匿名片段 · 单一候选账号",
    AttributionResolution.IDENTITY_CONFLICT.value: "匿名片段 · 多候选账号冲突",
    AttributionResolution.IDENTITY_UNKNOWN.value: "匿名片段 · 无候选账号",
    AttributionResolution.SOURCE_CHANGED.value: "源会话变更 · 旧片段",
}


ATTRIBUTION_REASON_LABELS: dict[str, str] = {
    AttributionResolution.IDENTITY_CONFLICT.value: "共享设备同一会话出现多个登录账号，匿名片段无法唯一归属",
    AttributionResolution.IDENTITY_UNKNOWN.value: "会话内没有登录信号，匿名片段无法归属到任何账号",
    AttributionResolution.SOURCE_CHANGED.value: "源会话事件已变化，该旧片段保留备查但不再参与挖掘与推荐",
}


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    SEED = "seed"


TRIGGER_LABELS: dict[str, str] = {
    TriggerKind.MANUAL.value: "手动",
    TriggerKind.SCHEDULE.value: "定时",
    TriggerKind.SEED.value: "初始化",
}


class ReviewStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


REVIEW_STATUS_LABELS: dict[str, str] = {
    ReviewStatus.PENDING.value: "待复核",
    ReviewStatus.CONFIRMED.value: "已采纳",
    ReviewStatus.DISMISSED.value: "已驳回",
}

REVIEW_STATUS_ORDER: list[str] = [
    ReviewStatus.PENDING.value,
    ReviewStatus.CONFIRMED.value,
    ReviewStatus.DISMISSED.value,
]


class RecPriority(StrEnum):
    """推荐优先级：按 置信度 × 提升度 分档，运营按优先级排队。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


REC_PRIORITY_LABELS: dict[str, str] = {
    RecPriority.HIGH.value: "高",
    RecPriority.MEDIUM.value: "中",
    RecPriority.LOW.value: "低",
}

REC_PRIORITY_ORDER: list[str] = [RecPriority.HIGH.value, RecPriority.MEDIUM.value, RecPriority.LOW.value]
