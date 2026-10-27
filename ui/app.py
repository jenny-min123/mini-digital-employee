"""第 7 章 · 人工审核界面（Streamlit）

四个页面：发起任务 / 任务看板 / 审核台 / 执行日志
表单字段、质检规则全部来自 yaml —— 评委现场加字段、改规则，刷新即生效，代码不动。
"""
import json
import os
import sys
import uuid

import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import config, db, machine, orchestrator  # noqa: E402
from core.state import STATUS_LABELS, Status  # noqa: E402

UPLOAD_DIR = os.path.join(ROOT, "data", "uploads")

STATUS_COLORS = {
    "DRAFT": "#888780", "ADMITTED": "#185FA5", "RUNNING": "#BA7517",
    "QC": "#534AB7", "REVIEW": "#D4537E", "REJECTED": "#993C1D",
    "PAUSED": "#854F0B", "DELIVERED": "#3B6D11", "FAILED": "#A32D2D",
}

st.set_page_config(page_title="迷你数字员工", page_icon="", layout="wide")
db.init()


def badge(status: str) -> str:
    label = STATUS_LABELS.get(Status(status), status) if status in Status.__members__ else status
    color = STATUS_COLORS.get(status, "#5F5E5A")
    return (f"<span style='background:{color};color:#fff;padding:2px 10px;"
            f"border-radius:10px;font-size:12px'>{label}</span>")


def save_upload(uploader, accept: list[str]) -> str | None:
    """把上传文件落到 data/uploads/，返回路径"""
    if uploader is None:
        return None
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = os.path.basename(uploader.name)
    path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:6]}_{safe_name}")
    with open(path, "wb") as f:
        f.write(uploader.getbuffer())
    return path


# ---------- 页面 1：发起任务 ----------

def page_submit() -> None:
    st.header("发起一项周报任务")
    schema = config.load_form_schema()
    form: dict = {}

    with st.form("task_form"):
        cols = st.columns(2)
        index = 0
        for field in schema["fields"]:
            key, label = field["key"], field.get("label", field["key"])
            ftype = field.get("type", "text")
            target = cols[index % 2]
            index += 1
            if ftype == "select":
                options = field.get("options", [])
                default = field.get("default", options[0] if options else None)
                form[key] = target.selectbox(label, options,
                                             index=options.index(default) if default in options else 0)
            elif ftype == "multiselect":
                form[key] = target.multiselect(label, field.get("options", []),
                                               default=field.get("default", []))
            elif ftype == "file":
                up = target.file_uploader(label, type=[e.lstrip(".") for e in field.get("accept", [])])
                form[key] = save_upload(up, field.get("accept", []))
            elif ftype == "number":
                form[key] = target.number_input(label, value=field.get("default", 0))
            else:
                form[key] = target.text_input(label, value=field.get("default", ""))
        submitted = st.form_submit_button("提交任务", type="primary")

    if submitted:
        result = orchestrator.submit(form)
        st.session_state["last_task"] = result["task_id"]
        if result["problems"]:
            st.warning("准入校验未通过，任务已暂停：\n\n" + "\n".join(f"- {p}" for p in result["problems"]))
        else:
            st.success(f"任务 {result['task_id']} 已受理（状态 ADMITTED）")

    tid = st.session_state.get("last_task")
    if tid:
        st.divider()
        info = orchestrator.status(tid)
        st.markdown(f"当前任务 `{tid}`　{badge(info['status'])}", unsafe_allow_html=True)
        if st.button("立即执行", type="primary"):
            with st.spinner("执行中：读数据 → 算指标 → 读纪要 → 写 Word → 自动质检"):
                result = orchestrator.execute(tid, prefer_llm=st.session_state.get("use_llm", True))
            st.session_state["last_result"] = result
            st.rerun()


# ---------- 页面 2：任务看板 ----------

def page_board() -> None:
    st.header("任务看板")
    tasks = db.list_tasks()
    if not tasks:
        st.info("还没有任务。左侧切到「发起任务」先提一个。")
        return

    for task in tasks:
        with st.container(border=True):
            left, right = st.columns([3, 2])
            with left:
                st.markdown(f"**{task['form'].get('period', '-')}**　{badge(task['status'])}",
                            unsafe_allow_html=True)
                st.caption(f"任务号 {task['id']}　重做 {task.get('redo_count', 0)} 次")
                steps = task["context"].get("steps") or []
                if steps:
                    st.progress(sum(1 for s in steps if s.get("done")) / len(steps),
                                text="　".join(f"{s['name']}{'✓' if s.get('done') else '…'}" for s in steps))
                if task.get("paused_reason"):
                    st.warning(task["paused_reason"], icon=None)
            with right:
                if task["status"] in ("ADMITTED", "RUNNING") and st.button("执行", key=f"run_{task['id']}"):
                    with st.spinner("执行中"):
                        orchestrator.execute(task["id"])
                    st.rerun()
                if task["status"] == "PAUSED" and st.button("补齐后继续", key=f"resume_{task['id']}"):
                    with st.spinner("从断点继续"):
                        orchestrator.resume(task["id"])
                    st.rerun()
                deliverable = task.get("deliverable")
                if deliverable and os.path.exists(deliverable):
                    with open(deliverable, "rb") as f:
                        st.download_button("下载周报", f, file_name=os.path.basename(deliverable),
                                           key=f"dl_{task['id']}")


# ---------- 页面 3：审核台 ----------

def page_review() -> None:
    st.header("待审核任务")
    pending = db.list_tasks(status="REVIEW")
    if not pending:
        st.info("没有待审核的任务。任务必须通过自动质检才会出现在这里。")
        return

    for task in pending:
        with st.container(border=True):
            st.subheader(f"任务 {task['id']}　{task['form'].get('period', '')}")
            qc = task.get("qc_report") or {}
            st.markdown(f"自动质检：{'全部通过' if qc.get('passed') else '未通过'}　"
                        f"（共 {qc.get('round', 0)} 轮）")
            for item in qc.get("items", []):
                icon = "通过" if item["ok"] else ("拦截" if item["severity"] == "blocking" else "警告")
                st.markdown(f"- **{item['name']}**（{icon}）：{item['msg']}")

            deliverable = task.get("deliverable")
            if deliverable and os.path.exists(deliverable):
                with open(deliverable, "rb") as f:
                    st.download_button("下载待审周报", f, file_name=os.path.basename(deliverable),
                                       key=f"rv_{task['id']}")

            col1, col2 = st.columns(2)
            if col1.button("通过", type="primary", key=f"ok_{task['id']}"):
                orchestrator.approve(task["id"])
                st.success("已交付")
                st.rerun()

            comment = col2.text_input("退回意见（必填）", key=f"cm_{task['id']}",
                                      placeholder="例如：华东区下滑原因没说清楚")
            if col2.button("退回修改", key=f"no_{task['id']}"):
                if not comment.strip():
                    st.error("退回必须填写意见")
                else:
                    orchestrator.reject(task["id"], comment)
                    st.warning("已退回，可到看板点「重做」触发第二版")
                    st.rerun()


# ---------- 页面 4：执行日志 ----------

def page_logs() -> None:
    st.header("执行日志（事件流）")
    tasks = db.list_tasks()
    if not tasks:
        st.info("暂无任务")
        return
    options = {f"{t['id']}｜{t['form'].get('period', '-')}｜{t['status']}": t["id"] for t in tasks}
    picked = st.selectbox("选择任务", list(options))
    tid = options[picked]

    events = db.get_events(tid)
    import datetime

    for event in events:
        ts = datetime.datetime.fromtimestamp(event["ts"]).strftime("%H:%M:%S")
        payload = json.dumps(event["payload"], ensure_ascii=False)
        st.markdown(f"`{ts}`　**{event['kind']}**　{payload}")

    if not events:
        st.caption("这个任务还没有事件记录")


# ---------- 侧边栏 ----------

st.sidebar.title("迷你数字员工")
page = st.sidebar.radio("导航", ["发起任务", "任务看板", "审核台", "执行日志"])

with st.sidebar.expander("运行设置"):
    from core import llm

    st.session_state["use_llm"] = st.checkbox(
        "调用大模型写正文", value=llm.available(),
        help="没配 Key 或网络不通时取消勾选，走离线模板，流程照样跑通")
    st.caption(f"当前模型：{llm.model_name() if llm.available() else '未配置 Key'}")
    st.caption(f"工具白名单：{', '.join(config.load_agent()['allowed_tools'])}")

if page == "发起任务":
    page_submit()
elif page == "任务看板":
    page_board()
elif page == "审核台":
    page_review()
else:
    page_logs()
