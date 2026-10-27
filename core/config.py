"""第 4 章 · 配置加载

现场 10 分全靠它：评委改一条 yaml，30 秒内生效，代码一行不动。
所以任何业务参数都不许硬编码在 .py 里，一律从这里读。
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


AGENT_CONF_PATH = _path("agents", "weekly_report_agent.yaml")
QC_RULES_PATH = _path("config", "qc_rules.yaml")
FORM_SCHEMA_PATH = _path("config", "form_schema.yaml")


def load_yaml(path: str) -> dict:
    """每次都重新读盘 —— 故意不做缓存，改完 yaml 立刻生效"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式不对（顶层应为字典）：{path}")
    return data


def load_agent(path: str | None = None) -> dict:
    conf = load_yaml(path or AGENT_CONF_PATH)
    required = ["name", "role", "allowed_tools", "limits"]
    missing = [k for k in required if k not in conf]
    if missing:
        raise ValueError(f"员工配置缺少字段：{missing}")
    conf.setdefault("steps", [])
    conf.setdefault("guardrails", [])
    conf.setdefault("responsibilities", [])
    # steps 允许两种写法：字符串列表，或 [{"name": "read_sales", ...}]
    conf["steps"] = [
        s["name"] if isinstance(s, dict) else str(s)
        for s in conf["steps"]
    ]
    return conf


def load_qc_rules(path: str | None = None) -> dict:
    conf = load_yaml(path or QC_RULES_PATH)
    if "rules" not in conf or not conf["rules"]:
        raise ValueError("质检规则为空：config/qc_rules.yaml 的 rules 列表至少要有 1 条")
    for rule in conf["rules"]:
        for key in ("id", "type", "params"):
            if key not in rule:
                raise ValueError(f"质检规则缺少字段 {key}：{rule}")
        rule.setdefault("name", rule["id"])
        rule.setdefault("severity", "blocking")
    return conf


def load_form_schema(path: str | None = None) -> dict:
    conf = load_yaml(path or FORM_SCHEMA_PATH)
    if "fields" not in conf or not conf["fields"]:
        raise ValueError("表单配置为空：config/form_schema.yaml 的 fields 列表至少要有 1 项")
    return conf


def required_fields() -> list[str]:
    return [f["key"] for f in load_form_schema()["fields"] if f.get("required")]


def limits() -> dict:
    return load_agent()["limits"]
