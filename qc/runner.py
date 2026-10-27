"""第 6 章 · 质检执行器：跑规则 + 自动定向修正 + 重试上限

规则来源是 config/qc_rules.yaml，改 yaml 即改行为。
两次修不好就置 PAUSED 转人工，绝不放宽标准让它"蒙混过关"。
"""
import json
import os

from core import config, db
from qc.checks import load_docx_text, run_check


def evaluate(docx_path: str, analyze_result: dict | None = None,
             rules_conf: dict | None = None) -> dict:
    """跑一遍全部规则，不做修正。给测试和界面复用。"""
    rules_conf = rules_conf or config.load_qc_rules()
    ctx = {
        "docx_path": docx_path,
        "text": load_docx_text(docx_path),
        "analyze_result": analyze_result or {},
    }
    items = [run_check(rule, ctx) for rule in rules_conf["rules"]]
    blocking = [i for i in items if i["severity"] == "blocking" and not i["ok"]]
    return {
        "ok": not blocking,
        "items": items,
        "blocking_failed": [i["id"] for i in blocking],
        "warnings": [i["id"] for i in items if i["severity"] == "warning" and not i["ok"]],
    }


def run_qc(task_id: str, docx_path: str, analyze_result: dict,
           fix_fn=None, rules_conf: dict | None = None) -> dict:
    """完整质检流程：检查 -> 不过就定向修 -> 最多 max_qc_retry 次

    fix_fn(failed_items) -> 重新生成 docx（由编排层提供）
    """
    limits = config.limits()
    max_fix = int(limits.get("max_qc_retry", 2))
    report = {"task_id": task_id, "round": 0, "passed": False, "items": [], "history": []}

    for attempt in range(1, max_fix + 2):  # 1 次初检 + max_fix 次修正
        result = evaluate(docx_path, analyze_result, rules_conf)
        report.update({
            "round": attempt,
            "items": result["items"],
            "blocking_failed": result["blocking_failed"],
            "warnings": result["warnings"],
        })
        report["history"].append({
            "round": attempt,
            "blocking_failed": result["blocking_failed"],
        })

        if result["ok"]:
            report["passed"] = True
            break
        if attempt > max_fix or fix_fn is None:
            break

        db.log_event(task_id, "qc_failed", {"round": attempt, "items": result["blocking_failed"]})
        failed = [i for i in result["items"]
                  if i["severity"] == "blocking" and not i["ok"]]
        fix_fn(failed)

    # 质检记录随交付物一起存盘
    out_dir = os.path.join("output", task_id)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "qc_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    report["report_path"] = report_path

    db.set_qc_report(task_id, report)
    db.log_event(task_id, "qc_passed" if report["passed"] else "qc_blocked",
                 {"round": report["round"], "failed": report.get("blocking_failed")})
    return report
