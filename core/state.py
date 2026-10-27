"""第 3 章 · 状态定义

状态机是本题最核心的 10 分：把"什么状态能做什么动作"写死成一张表，
非法流转直接抛异常，不留给模型任何自由裁量空间。
"""
from enum import Enum


class Status(str, Enum):
    DRAFT = "DRAFT"          # 表单已填，未提交
    ADMITTED = "ADMITTED"    # 准入校验通过，排队中
    RUNNING = "RUNNING"      # 员工正在干活
    QC = "QC"                # 自动质检中
    REVIEW = "REVIEW"        # 等人工审核
    REJECTED = "REJECTED"    # 被退回，待重做
    PAUSED = "PAUSED"        # 暂停：缺材料 / 工具故障 / 超限
    DELIVERED = "DELIVERED"  # 已交付（终态）
    FAILED = "FAILED"        # 已终止（终态）


# 终态：进入后不允许再发生任何流转
TERMINAL_STATUSES = {Status.DELIVERED, Status.FAILED}

# 中文标签，只用于界面展示，不参与任何判断
STATUS_LABELS = {
    Status.DRAFT: "草稿",
    Status.ADMITTED: "已受理",
    Status.RUNNING: "执行中",
    Status.QC: "质检中",
    Status.REVIEW: "待审核",
    Status.REJECTED: "已退回",
    Status.PAUSED: "已暂停",
    Status.DELIVERED: "已交付",
    Status.FAILED: "已终止",
}
