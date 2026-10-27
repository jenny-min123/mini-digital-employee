"""第 3 章 · 状态流转表 + 代码钉死

核心思想：approve 只能由 reviewer 触发，模型（system 角色）永远拿不到这个权限。
所有判断都是纯 Python 的 if，模型插不上手。
"""
from core.state import Status, TERMINAL_STATUSES


class IllegalTransition(Exception):
    """非法流转：状态不存在、动作不在表里、或角色越权"""


# (当前状态, 动作) -> (下一状态, 允许触发的角色集合)
TRANSITIONS: dict[tuple[Status, str], tuple[Status, set[str]]] = {
    (Status.DRAFT, "submit"):            (Status.ADMITTED, {"initiator"}),
    (Status.ADMITTED, "missing_material"): (Status.PAUSED, {"system"}),
    (Status.ADMITTED, "start"):          (Status.RUNNING, {"system"}),
    (Status.RUNNING, "need_input"):      (Status.PAUSED, {"system"}),
    (Status.RUNNING, "tool_failed"):     (Status.PAUSED, {"system"}),
    (Status.RUNNING, "over_limit"):      (Status.PAUSED, {"system"}),
    (Status.RUNNING, "finish"):          (Status.QC, {"system"}),
    (Status.QC, "pass"):                 (Status.REVIEW, {"system"}),
    (Status.QC, "fail"):                 (Status.PAUSED, {"system"}),
    (Status.REVIEW, "approve"):          (Status.DELIVERED, {"reviewer"}),
    (Status.REVIEW, "reject"):           (Status.REJECTED, {"reviewer"}),
    (Status.REJECTED, "redo"):           (Status.RUNNING, {"initiator", "system"}),
    (Status.PAUSED, "resume"):           (Status.RUNNING, {"initiator"}),
    (Status.PAUSED, "abort"):            (Status.FAILED, {"initiator"}),
}

# 角色常量，避免各处手写字符串拼错
INITIATOR = "initiator"
REVIEWER = "reviewer"
SYSTEM = "system"


def next_status(current: Status | str, action: str, role: str) -> Status:
    """唯一合法的状态推进入口。

    三件事一次校验完：动作是否存在于该状态、角色是否有权、目标是否终态。
    """
    current = Status(current) if not isinstance(current, Status) else current

    if current in TERMINAL_STATUSES:
        raise IllegalTransition(f"状态 {current.value} 是终态，不允许再执行动作 {action}")

    rule = TRANSITIONS.get((current, action))
    if rule is None:
        raise IllegalTransition(f"状态 {current.value} 不允许执行动作 {action}")

    target, allowed_roles = rule
    if role not in allowed_roles:
        raise IllegalTransition(
            f"角色 {role} 无权在 {current.value} 状态执行 {action}"
            f"（允许的角色：{sorted(allowed_roles)}）"
        )
    return target


def can(current: Status | str, action: str, role: str) -> bool:
    """不会抛异常的探测版，界面用它决定按钮是否可点"""
    try:
        next_status(current, action, role)
        return True
    except IllegalTransition:
        return False


def allowed_actions(current: Status | str, role: str) -> list[str]:
    """当前角色在当前状态下能做的动作，给界面渲染按钮用"""
    current = Status(current) if not isinstance(current, Status) else current
    return sorted(
        action
        for (status, action), (_, roles) in TRANSITIONS.items()
        if status == current and role in roles
    )


def as_table() -> list[dict]:
    """把流转表导出成行，技术报告第 3 页直接贴这张表"""
    return [
        {
            "当前状态": status.value,
            "动作": action,
            "下一状态": target.value,
            "谁能触发": " / ".join(sorted(roles)),
        }
        for (status, action), (target, roles) in TRANSITIONS.items()
    ]
