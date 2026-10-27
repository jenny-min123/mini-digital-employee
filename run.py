"""一键入口（第 7、8 章配套）

  python run.py make-data                        造一份自测假数据
  python run.py demo                             完整跑一遍：提交 -> 执行 -> 质检 -> 退回 -> 重做
  python run.py submit --period 2026-W44 --sales data/sample_sales.xlsx --minutes data/sample_minutes.docx
  python run.py run <task_id>                    执行任务
  python run.py status <task_id>                 查状态与步骤进度
  python run.py list                             列所有任务
  python run.py reject <task_id> -m "意见"        审核退回（意见必填）
  python run.py approve <task_id>                审核通过
  python run.py redo <task_id>                   按意见重做
  python run.py resume <task_id>                 暂停后继续
  python run.py table                            打印状态流转表（技术报告第 3 页）

加 --offline 强制走离线模板写作，不调用模型（演示、断网、没钱时用）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config, db, llm, machine, orchestrator  # noqa: E402
from core.state import STATUS_LABELS, Status  # noqa: E402


def _print_status(tid: str) -> None:
    info = orchestrator.status(tid)
    label = STATUS_LABELS.get(Status(info["status"]), info["status"])
    print(f"  任务 {tid}　状态：{info['status']}（{label}）　重做次数：{info['redo_count']}")
    if info["deliverable"]:
        print(f"  交付物：{info['deliverable']}")
    if info["paused_reason"]:
        print(f"  暂停原因：{info['paused_reason']}")
    steps = info["steps"]
    if steps:
        progress = "　".join(f"{s['name']}{'✓' if s.get('done') else '…'}" for s in steps)
        print(f"  步骤：{progress}")


def _print_qc(result: dict) -> None:
    qc = result.get("qc")
    if not qc:
        return
    print(f"  质检：{'通过' if qc['passed'] else '未通过'}（第 {qc['round']} 轮）")
    for item in qc["items"]:
        flag = "OK  " if item["ok"] else ("拦截" if item["severity"] == "blocking" else "警告")
        print(f"    [{flag}] {item['name']}：{item['msg']}")


def cmd_make_data(_args) -> None:
    from scripts.make_sample_data import main as make

    make()


def cmd_submit(args) -> None:
    form = {"period": args.period, "sales_file": args.sales,
            "minutes_file": args.minutes, "scope": ["全部门店"]}
    result = orchestrator.submit(form)
    print(f"已提交，任务号 {result['task_id']}，状态 {result['status']}")
    if result["problems"]:
        print("  准入校验未通过：")
        for problem in result["problems"]:
            print(f"    - {problem}")


def cmd_run(args) -> None:
    result = orchestrator.execute(args.task_id, prefer_llm=not args.offline)
    _print_status(args.task_id)
    _print_qc(result)


def cmd_status(args) -> None:
    _print_status(args.task_id)


def cmd_list(_args) -> None:
    tasks = db.list_tasks()
    if not tasks:
        print("还没有任务。先跑 python run.py demo")
        return
    print(f"{'task_id':10} {'状态':10} {'周期':10} 交付物")
    for task in tasks:
        label = STATUS_LABELS.get(Status(task["status"]), task["status"])
        print(f"{task['id']:10} {label:10} {str(task['form'].get('period', '-')):10} "
              f"{task.get('deliverable') or '-'}")


def cmd_approve(args) -> None:
    result = orchestrator.approve(args.task_id)
    print(f"已通过，状态 {result['status']}")


def cmd_reject(args) -> None:
    try:
        result = orchestrator.reject(args.task_id, args.message)
    except ValueError as exc:
        print(f"退回失败：{exc}")
        return
    print(f"已退回，状态 {result['status']}，意见：{args.message}")


def cmd_redo(args) -> None:
    result = orchestrator.redo(args.task_id, prefer_llm=not args.offline)
    _print_status(args.task_id)
    _print_qc(result)


def cmd_resume(args) -> None:
    result = orchestrator.resume(args.task_id, prefer_llm=not args.offline)
    _print_status(args.task_id)
    _print_qc(result)


def cmd_table(_args) -> None:
    rows = machine.as_table()
    width = max(len(r["当前状态"]) for r in rows) + 2
    print(f"{'当前状态':<{width}}{'动作':<18}{'下一状态':<12}谁能触发")
    for row in rows:
        print(f"{row['当前状态']:<{width}}{row['动作']:<18}{row['下一状态']:<12}{row['谁能触发']}")


def cmd_demo(args) -> None:
    from scripts.make_sample_data import main as make

    if not os.path.exists("data/sample_sales.xlsx"):
        make()

    print("=" * 66)
    print("迷你数字员工 · 全流程演示（提交 → 执行 → 质检 → 送审 → 退回 → 重做）")
    print(f"写作模式：{'离线模板' if args.offline or not llm.available() else '模型 ' + llm.model_name()}")
    print("=" * 66)

    print("\n[1] 发起任务")
    submitted = orchestrator.submit({
        "period": "2026-W44",
        "sales_file": "data/sample_sales.xlsx",
        "minutes_file": "data/sample_minutes.docx",
        "scope": ["全部门店"],
    })
    tid = submitted["task_id"]
    print(f"  任务号 {tid}，状态 {submitted['status']}")
    if submitted["problems"]:
        print(f"  准入未通过：{submitted['problems']}（这是预期的暂停行为）")
        return

    print("\n[2] 执行（模型/模板写作 + 三个工具）")
    result = orchestrator.execute(tid, prefer_llm=not args.offline)
    _print_status(tid)
    _print_qc(result)
    if result["status"] != Status.REVIEW.value:
        print("\n流程停在暂停态，下面是原因，修好后用 python run.py resume 继续")
        return

    print("\n[3] 审核：模拟主管退回，意见 = 华东区下滑原因没说清楚")
    orchestrator.reject(tid, "华东区下滑原因没说清楚，请在异常分析里补充商场改造和排班不足的影响")
    _print_status(tid)

    print("\n[4] 重做：意见必须出现在第二版正文里")
    result = orchestrator.redo(tid, prefer_llm=not args.offline)
    _print_status(tid)
    _print_qc(result)

    from qc.checks import load_docx_text

    text = load_docx_text(orchestrator.docx_path(tid))
    hit = "华东区" in text or "商场" in text
    print(f"\n[5] 第二版是否体现审核意见：{'是（正文出现华东区/商场改造相关内容）' if hit else '否（意见没被采纳，这一项会判 0 分）'}")

    orchestrator.approve(tid)
    final = orchestrator.status(tid)
    print(f"\n[6] 主管通过，最终状态 {final['status']}，交付物 {final['deliverable']}")
    print(f"\n技术报告可直接引用：python run.py status {tid}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="迷你数字员工 · 经营周报员工")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--offline", action="store_true",
                        help="强制离线模板写作，不调用模型")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("make-data", help="生成自测假数据", parents=[common])
    p.set_defaults(func=cmd_make_data)

    p = sub.add_parser("demo", help="完整演示一遍闭环", parents=[common])
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("submit", help="发起任务", parents=[common])
    p.add_argument("--period", default="2026-W44")
    p.add_argument("--sales", default="data/sample_sales.xlsx")
    p.add_argument("--minutes", default="data/sample_minutes.docx")
    p.set_defaults(func=cmd_submit)

    for name, func, help_text in [
        ("run", cmd_run, "执行任务"),
        ("status", cmd_status, "查任务状态"),
        ("approve", cmd_approve, "审核通过"),
        ("redo", cmd_redo, "按意见重做"),
        ("resume", cmd_resume, "暂停后继续"),
    ]:
        p = sub.add_parser(name, help=help_text, parents=[common])
        p.add_argument("task_id")
        p.set_defaults(func=func)

    p = sub.add_parser("reject", help="审核退回", parents=[common])
    p.add_argument("task_id")
    p.add_argument("-m", "--message", required=True, help="退回意见（必填）")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("list", help="列所有任务", parents=[common])
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("table", help="打印状态流转表", parents=[common])
    p.set_defaults(func=cmd_table)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    config.load_agent()  # 早失败：配置写错了立刻报，别等跑到一半
    config.load_qc_rules()
    db.init()
    args.func(args)


if __name__ == "__main__":
    main()
