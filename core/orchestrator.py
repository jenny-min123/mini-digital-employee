"""编排层：把表单、状态机、工具、质检、审核串成一条流水线

职责边界（答辩必问）：
- 状态推进：只走 machine.next_status，任何地方都不许直接改 status 字段
- 工具调用：只走 registry.call_with_retry，白名单之外直接拒绝并留痕
- 是否可送审 / 可交付：只由质检结果和状态表决定，模型说了不算
"""
import os
import time
import uuid

from core import config, db, machine, writer
from core.state import STATUS_LABELS, Status
from qc import runner as qc_runner
from tools import registry
from tools.base import FatalToolError, ToolError

OUTPUT_ROOT = "output"


# ---------- 公共 ----------

def init() -> None:
    db.init()


def new_task_id() -> str:
    return uuid.uuid4().hex[:8]


def docx_path(task_id: str) -> str:
    return os.path.join(OUTPUT_ROOT, task_id, "周报.docx")


def _task_or_raise(task_id: str) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise KeyError(f"任务不存在：{task_id}")
    return task


def _move(task_id: str, action: str, role: str) -> Status:
    """唯一的流转入口：查表 -> 改库 -> 留痕"""
    task = _task_or_raise(task_id)
    target = machine.next_status(Status(task["status"]), action, role)
    db.set_status(task_id, target.value)
    db.log_event(task_id, f"transition:{action}", {"from": task["status"], "to": target.value})
    return target


# ---------- 准入 ----------

def validate_form(form: dict) -> list[str]:
    """准入校验：必填项齐不齐、文件在不在、格式对不对

    这一步在花钱调模型之前做完 —— 比工具跑到一半崩掉先进得多。
    """
    problems: list[str] = []
    for field in config.load_form_schema()["fields"]:
        key, label = field["key"], field.get("label", field["key"])
        value = form.get(key)
        if field.get("required") and (value in (None, "", [])):
            problems.append(f"缺少必填项：{label}")
            continue
        if field.get("type") == "file" and value:
            path = str(value)
            if not os.path.exists(path):
                problems.append(f"{label}文件不存在：{path}")
                continue
            ext = os.path.splitext(path)[1].lower()
            accept = [e.lower() for e in field.get("accept", [])]
            if accept and ext not in accept:
                problems.append(f"{label}格式不对，只接受 {accept}，收到 {ext}")
    if form.get("sales_file") and os.path.exists(str(form["sales_file"])):
        if os.path.getsize(str(form["sales_file"])) == 0:
            problems.append("销售明细文件大小为 0，可能没有内容")
    return problems


def submit(form: dict, task_id: str | None = None) -> dict:
    """发起任务：建单 -> submit -> 准入校验 -> ADMITTED 或 PAUSED"""
    init()
    tid = task_id or new_task_id()
    db.create_task(tid, form)
    db.log_event(tid, "task_created", {"period": form.get("period"),
                                       "sales_file": form.get("sales_file")})

    _move(tid, "submit", machine.INITIATOR)

    problems = validate_form(form)
    if problems:
        _move(tid, "missing_material", machine.SYSTEM)
        reason = "；".join(problems) + "。任务已暂停，补齐材料后点继续。"
        db.set_paused(tid, reason)
        db.log_event(tid, "paused", {"reason": reason})
        return {"task_id": tid, "status": Status.PAUSED.value, "problems": problems}

    return {"task_id": tid, "status": Status.ADMITTED.value, "problems": []}


# ---------- 执行 ----------

def _progress(task_id: str) -> tuple[int, int]:
    steps = _task_or_raise(task_id)["context"].get("steps") or []
    return sum(1 for s in steps if s.get("done")), len(steps)


def _run_steps(task_id: str, agent: dict, state: dict, prefer_llm: bool) -> dict:
    """按 employee.yaml 里的 steps 顺序调工具，每步都落检查点"""
    form = _task_or_raise(task_id)["form"]
    limits = agent.get("limits", {})
    max_seconds = int(limits.get("max_seconds", 300))
    started = time.time()

    for step in list(agent["steps"]):
        if time.time() - started > max_seconds:
            raise TimeoutError(f"超过单任务时限 {max_seconds} 秒")

        done = {s["name"]: s.get("done") for s in _task_or_raise(task_id)["context"]["steps"]}
        if done.get(step):
            continue

        if step == "read_sales":
            out = registry.call_with_retry(
                agent, "read_sales", {"file_path": str(form["sales_file"])}, task_id)
            state["sales"] = out
            db.log_event(task_id, "tool_call", {"tool": step, "rows": out["rows"]})

        elif step == "analyze_sales":
            records = (state.get("sales") or {}).get("records")
            if not records:  # 断点恢复时重新读一次，工具是只读的，重跑安全
                state["sales"] = registry.call_with_retry(
                    agent, "read_sales", {"file_path": str(form["sales_file"])}, task_id)
                records = state["sales"]["records"]
            out = registry.call_with_retry(agent, "analyze_sales", {
                "records": records,
                "period": str(form.get("period")),
                "scope": form.get("scope") or ["全部门店"],
            }, task_id)
            state["analyze"] = out
            db.update_task(task_id, context={**_task_or_raise(task_id)["context"], "analyze": out})
            db.log_event(task_id, "tool_call", {
                "tool": step, "total_sales": out["total_sales"],
                "anomalies": len(out["anomalies"])})

        elif step == "read_minutes":
            out = registry.call_with_retry(
                agent, "read_minutes", {"file_path": str(form["minutes_file"])}, task_id)
            state["minutes"] = out["text"]
            db.log_event(task_id, "tool_call", {"tool": step, "chars": out["chars"]})

        elif step == "write_docx":
            analyze = state.get("analyze") or _task_or_raise(task_id)["context"].get("analyze")
            if not analyze:
                raise FatalToolError("缺少分析结果，无法生成周报（请从头重跑本任务）")
            minutes_text = state.get("minutes") or ""
            content = writer.build_content(
                analyze, minutes_text, agent,
                review_comment=_task_or_raise(task_id).get("review_comment"),
                prefer_llm=prefer_llm,
            )
            out = registry.call_with_retry(agent, "write_docx", {
                "content": content, "out_path": docx_path(task_id)}, task_id)
            db.set_deliverable(task_id, out["file_path"])
            db.log_event(task_id, "tool_call", {"tool": step, "file": out["file_path"]})

        else:
            raise FatalToolError(f"employee.yaml 里配置了未实现的步骤：{step}")

        db.mark_step(task_id, step, True)

    return state


def execute(task_id: str, prefer_llm: bool = True) -> dict:
    """执行任务：RUNNING -> 工具链 -> 质检 -> REVIEW 或 PAUSED

    只有"质检全过"才会进 REVIEW；质检不过自动定向修 2 次，仍不过就暂停转人工。
    """
    task = _task_or_raise(task_id)
    agent = config.load_agent()
    db.init_steps(task_id, list(agent["steps"]))

    if task["status"] == Status.ADMITTED.value:
        _move(task_id, "start", machine.SYSTEM)
    elif task["status"] != Status.RUNNING.value:
        raise machine.IllegalTransition(
            f"状态 {task['status']} 不能执行；只有 ADMITTED / RUNNING 可以执行")

    state: dict = {}
    try:
        _run_steps(task_id, agent, state, prefer_llm)
    except TimeoutError as exc:
        _move(task_id, "over_limit", machine.SYSTEM)
        db.set_paused(task_id, f"{exc}；已完成 {_progress(task_id)[0]}/{_progress(task_id)[1]} 步")
        db.log_event(task_id, "paused", {"reason": str(exc)})
        return {"task_id": task_id, "status": Status.PAUSED.value, "reason": str(exc)}
    except FatalToolError as exc:
        done, total = _progress(task_id)
        _move(task_id, "tool_failed", machine.SYSTEM)
        reason = f"工具执行失败：{exc}。已完成 {done}/{total} 步，补齐条件后可点继续。"
        db.set_paused(task_id, reason)
        db.log_event(task_id, "paused", {"reason": reason})
        return {"task_id": task_id, "status": Status.PAUSED.value, "reason": reason}
    except Exception as exc:  # 模型不可用等意外
        done, total = _progress(task_id)
        _move(task_id, "tool_failed", machine.SYSTEM)
        reason = f"执行中断：{exc}。已完成 {done}/{total} 步。"
        db.set_paused(task_id, reason)
        db.log_event(task_id, "paused", {"reason": reason})
        return {"task_id": task_id, "status": Status.PAUSED.value, "reason": reason}

    _move(task_id, "finish", machine.SYSTEM)  # -> QC

    analyze = state.get("analyze") or _task_or_raise(task_id)["context"].get("analyze") or {}

    def fix_fn(failed_items: list[dict]) -> None:
        """自动定向修正：只重写正文，不整篇推倒重来"""
        content = writer.build_content(
            analyze, state.get("minutes", ""), agent,
            review_comment=_task_or_raise(task_id).get("review_comment"),
            fix_items=failed_items,
            prefer_llm=prefer_llm,
        )
        registry.call_with_retry(agent, "write_docx",
                                 {"content": content, "out_path": docx_path(task_id)}, task_id)

    report = qc_runner.run_qc(task_id, docx_path(task_id), analyze, fix_fn=fix_fn)

    if report["passed"]:
        _move(task_id, "pass", machine.SYSTEM)  # -> REVIEW
        return {"task_id": task_id, "status": Status.REVIEW.value, "qc": report}

    _move(task_id, "fail", machine.SYSTEM)
    failed_names = [i["name"] for i in report["items"]
                    if i["severity"] == "blocking" and not i["ok"]]
    reason = (f"自动质检未通过（已自动修正 {report['round']} 轮）：{failed_names}。"
              f"任务已暂停，请人工介入。")
    db.set_paused(task_id, reason)
    db.log_event(task_id, "paused", {"reason": reason})
    return {"task_id": task_id, "status": Status.PAUSED.value, "reason": reason, "qc": report}


# ---------- 审核流转 ----------

def approve(task_id: str, role: str = machine.REVIEWER) -> dict:
    """通过：只有 reviewer 能做，模型（system）永远被拒绝"""
    _move(task_id, "approve", role)
    db.log_event(task_id, "delivered", {"file": _task_or_raise(task_id).get("deliverable")})
    return {"task_id": task_id, "status": Status.DELIVERED.value}


def reject(task_id: str, comment: str, role: str = machine.REVIEWER) -> dict:
    """退回：意见必填，且会写进下一版提示词"""
    if not (comment or "").strip():
        raise ValueError("退回必须填写意见，否则重做时模型无从下手")
    _move(task_id, "reject", role)
    db.set_review_comment(task_id, comment.strip())
    db.log_event(task_id, "review_rejected", {"comment": comment.strip()})
    return {"task_id": task_id, "status": Status.REJECTED.value}


def redo(task_id: str, prefer_llm: bool = True) -> dict:
    """重做：清空步骤检查点，把审核意见带进新一轮执行"""
    db.inc_redo(task_id)
    db.update_task(task_id, context={"steps": []}, paused_reason=None)
    _move(task_id, "redo", machine.INITIATOR)
    db.log_event(task_id, "redo", {"comment": _task_or_raise(task_id).get("review_comment")})
    return execute(task_id, prefer_llm=prefer_llm)


def resume(task_id: str, role: str = machine.INITIATOR, prefer_llm: bool = True) -> dict:
    """继续：PAUSED -> RUNNING，从第一个未完成的步骤接着跑"""
    _move(task_id, "resume", role)
    db.update_task(task_id, paused_reason=None)
    db.log_event(task_id, "resumed", {"from_step": db.first_pending_step(task_id)})
    return execute(task_id, prefer_llm=prefer_llm)


def abort(task_id: str, role: str = machine.INITIATOR) -> dict:
    _move(task_id, "abort", role)
    return {"task_id": task_id, "status": Status.FAILED.value}


# ---------- 查询 ----------

def status(task_id: str) -> dict:
    task = _task_or_raise(task_id)
    return {
        "task_id": task_id,
        "status": task["status"],
        "label": STATUS_LABELS.get(Status(task["status"]), task["status"]),
        "redo_count": task.get("redo_count", 0),
        "deliverable": task.get("deliverable"),
        "paused_reason": task.get("paused_reason"),
        "steps": task["context"].get("steps", []),
        "qc_passed": (task.get("qc_report") or {}).get("passed"),
    }
