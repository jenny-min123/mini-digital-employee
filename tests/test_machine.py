"""第 3 章验收：状态机"""
import pytest

from core import machine
from core.state import Status


def test_illegal_transition_rejected():
    """已交付的任务不能再被批准"""
    with pytest.raises(machine.IllegalTransition):
        machine.next_status(Status.DELIVERED, "approve", "reviewer")


def test_model_cannot_approve():
    """模型（system 角色）永远不能自己批准自己 —— 这是全题最关键的一条"""
    with pytest.raises(machine.IllegalTransition):
        machine.next_status(Status.REVIEW, "approve", "system")


def test_reviewer_cannot_start():
    """审核人不能替业务方发起执行"""
    with pytest.raises(machine.IllegalTransition):
        machine.next_status(Status.ADMITTED, "start", "reviewer")


def test_unknown_action_rejected():
    with pytest.raises(machine.IllegalTransition):
        machine.next_status(Status.RUNNING, "乱写的动作", "system")


def test_happy_path():
    assert machine.next_status(Status.DRAFT, "submit", "initiator") is Status.ADMITTED
    assert machine.next_status(Status.ADMITTED, "start", "system") is Status.RUNNING
    assert machine.next_status(Status.RUNNING, "finish", "system") is Status.QC
    assert machine.next_status(Status.QC, "pass", "system") is Status.REVIEW
    assert machine.next_status(Status.REVIEW, "approve", "reviewer") is Status.DELIVERED


def test_reject_requires_reviewer_and_leads_to_rejected():
    assert machine.next_status(Status.REVIEW, "reject", "reviewer") is Status.REJECTED
    assert machine.next_status(Status.REJECTED, "redo", "initiator") is Status.RUNNING


def test_can_is_side_effect_free():
    assert machine.can(Status.REVIEW, "approve", "system") is False
    assert machine.can(Status.REVIEW, "approve", "reviewer") is True


def test_allowed_actions_by_role():
    assert machine.allowed_actions(Status.REVIEW, "reviewer") == ["approve", "reject"]
    assert machine.allowed_actions(Status.REVIEW, "system") == []


def test_table_export_covers_every_rule():
    rows = machine.as_table()
    assert len(rows) == len(machine.TRANSITIONS)
    assert {"当前状态", "动作", "下一状态", "谁能触发"} <= set(rows[0])
