"""第 5 章 · 统一错误类型

ToolError      可预期错误（参数传错、缺列），把原因原样返回给模型，让它自己改
FatalToolError 不可恢复错误（服务不可用、磁盘满），直接熔断转人工
区分这两类，是"工具报错不编造"这条的关键。
"""


class ToolError(Exception):
    """工具内部可预期的错误，会原样返回给模型看"""


class FatalToolError(ToolError):
    """不可恢复的错误，直接熔断，不再重试"""
