"""第 5 章 · 工具注册表 + 白名单 + 重试熔断

三件事都在这一层做，模型碰不到：
1. 白名单：不在 employee.yaml 的 allowed_tools 里，直接 PermissionError 并留痕
2. 重试：ToolError 退避重试（1s / 2s / 4s）
3. 熔断：连续失败就抛 FatalToolError，上层把任务置 PAUSED，绝不让模型编结果
"""
import time

from core import db
from tools.base import FatalToolError, ToolError
from tools.data_tools import analyze_sales, read_sales
from tools.doc_tools import read_minutes, write_docx

# name -> 可执行函数
REGISTRY = {
    "read_sales": read_sales,
    "analyze_sales": analyze_sales,
    "read_minutes": read_minutes,
    "write_docx": write_docx,
}

# Function Calling 用的工具声明（模型只会看到白名单内的那几个）
SCHEMAS = {
    "read_sales": {
        "type": "function",
        "function": {
            "name": "read_sales",
            "description": "读取销售明细 Excel/CSV，返回行数、列名、日期范围和记录列表",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "销售明细文件路径"}},
                "required": ["file_path"],
            },
        },
    },
    "analyze_sales": {
        "type": "function",
        "function": {
            "name": "analyze_sales",
            "description": "计算汇总指标、门店环比、异常门店并生成图表，返回数字白名单",
            "parameters": {
                "type": "object",
                "properties": {
                    "records": {"type": "array", "description": "read_sales 返回的 records"},
                    "period": {"type": "string", "description": "统计周期，如 2026-W44"},
                    "scope": {"type": "array", "items": {"type": "string"}, "description": "门店范围"},
                },
                "required": ["records", "period"],
            },
        },
    },
    "read_minutes": {
        "type": "function",
        "function": {
            "name": "read_minutes",
            "description": "读取周会纪要 docx，返回纯文本",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "纪要文件路径"}},
                "required": ["file_path"],
            },
        },
    },
    "write_docx": {
        "type": "function",
        "function": {
            "name": "write_docx",
            "description": "把结构化内容写成 Word 周报，固定路径覆盖写（幂等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "object", "description": "标题、周期、章节列表"},
                    "out_path": {"type": "string", "description": "输出 docx 路径"},
                },
                "required": ["content", "out_path"],
            },
        },
    },
}


class ToolDenied(PermissionError):
    """越权调用：该员工没有这个工具"""


def tool_schemas(agent_conf: dict) -> list[dict]:
    return [SCHEMAS[name] for name in agent_conf.get("allowed_tools", []) if name in SCHEMAS]


def call_tool(agent_conf: dict, name: str, args: dict, task_id: str | None = None):
    """唯一的工具调用入口：先查白名单，再执行"""
    if name not in agent_conf.get("allowed_tools", []):
        if task_id:
            db.log_event(task_id, "tool_denied", {"tool": name, "args_keys": sorted(args)})
        raise ToolDenied(f"工具 {name} 未对本员工开放（白名单：{agent_conf.get('allowed_tools')}）")
    if name not in REGISTRY:
        raise ToolError(f"工具 {name} 尚未实现")
    return REGISTRY[name](**args)


def call_with_retry(agent_conf: dict, name: str, args: dict,
                    task_id: str | None = None, max_retry: int | None = None):
    """退避重试 + 熔断。FatalToolError 不重试，直接抛。"""
    limit = max_retry if max_retry is not None else agent_conf.get("limits", {}).get("max_tool_retry", 3)
    last: Exception | None = None
    for attempt in range(1, limit + 1):
        try:
            return call_tool(agent_conf, name, args, task_id)
        except FatalToolError:
            raise
        except ToolDenied:
            raise
        except ToolError as exc:
            last = exc
            if task_id:
                db.log_event(task_id, "tool_retry", {"tool": name, "attempt": attempt, "error": str(exc)})
            if attempt < limit:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s
    raise FatalToolError(f"工具 {name} 连续失败 {limit} 次：{last}")
