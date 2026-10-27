"""第 3 章 · SQLite 持久化

程序重启后任务不能丢，这是"断点恢复"的地基。
tasks 表存任务当前快照，events 表存事件流（时间轴页面直接读它）。
"""
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get(
    "MDW_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tasks.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    form           TEXT,      -- 表单 JSON
    context        TEXT,      -- 执行上下文 JSON（含 steps 断点）
    review_comment TEXT,      -- 审核意见
    qc_report      TEXT,      -- 质检记录 JSON
    deliverable    TEXT,      -- 交付物路径
    paused_reason  TEXT,      -- 暂停原因（给用户看的提示语）
    redo_count     INTEGER DEFAULT 0,
    created_at     REAL,
    updated_at     REAL
);
CREATE TABLE IF NOT EXISTS events(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    ts      REAL,
    kind    TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, ts);
"""


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


# ---------- 写 ----------

def create_task(task_id: str, form: dict) -> dict:
    """幂等：同一个 task_id 重复创建不会产生两条记录"""
    now = time.time()
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO tasks(id,status,form,context,redo_count,created_at,updated_at)"
            " VALUES(?,?,?,?,0,?,?)",
            (task_id, "DRAFT", _dumps(form), _dumps({"steps": []}), now, now),
        )
    return get_task(task_id)


def set_status(task_id: str, status: str) -> None:
    status = status.value if hasattr(status, "value") else str(status)
    with conn() as c:
        c.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, time.time(), task_id))


def update_task(task_id: str, **fields) -> None:
    """通用字段更新，JSON 字段自动序列化"""
    if not fields:
        return
    allowed = {"form", "context", "review_comment", "qc_report", "deliverable",
               "paused_reason", "redo_count"}
    cols, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"不支持的字段：{key}")
        cols.append(f"{key}=?")
        values.append(value if key == "redo_count" else (
            _dumps(value) if isinstance(value, (dict, list)) else value
        ))
    values.extend([time.time(), task_id])
    with conn() as c:
        c.execute(f"UPDATE tasks SET {', '.join(cols)}, updated_at=? WHERE id=?", values)


def set_review_comment(task_id: str, comment: str) -> None:
    update_task(task_id, review_comment=comment)


def set_qc_report(task_id: str, report: dict) -> None:
    update_task(task_id, qc_report=report)


def set_deliverable(task_id: str, path: str) -> None:
    update_task(task_id, deliverable=path)


def set_paused(task_id: str, reason: str) -> None:
    update_task(task_id, paused_reason=reason)


def inc_redo(task_id: str) -> int:
    with conn() as c:
        c.execute("UPDATE tasks SET redo_count=COALESCE(redo_count,0)+1, updated_at=? WHERE id=?",
                  (time.time(), task_id))
    return get_task(task_id)["redo_count"]


def log_event(task_id: str, kind: str, payload: dict | None = None) -> None:
    with conn() as c:
        c.execute("INSERT INTO events(task_id,ts,kind,payload) VALUES(?,?,?,?)",
                  (task_id, time.time(), kind, _dumps(payload or {})))


# ---------- 读 ----------

def get_task(task_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    task["form"] = _loads(task.get("form"), {})
    task["context"] = _loads(task.get("context"), {"steps": []})
    task["qc_report"] = _loads(task.get("qc_report"), None)
    return task


def list_tasks(status: str | None = None, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM tasks"
    params: list = []
    if status:
        sql += " WHERE status=?"
        params.append(status.value if hasattr(status, "value") else status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    out = []
    for row in rows:
        task = dict(row)
        task["form"] = _loads(task.get("form"), {})
        task["context"] = _loads(task.get("context"), {"steps": []})
        task["qc_report"] = _loads(task.get("qc_report"), None)
        out.append(task)
    return out


def get_events(task_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM events WHERE task_id=? ORDER BY ts", (task_id,)).fetchall()
    return [
        {"ts": r["ts"], "kind": r["kind"], "payload": _loads(r["payload"], {})}
        for r in rows
    ]


# ---------- 断点 ----------

def init_steps(task_id: str, names: list[str]) -> None:
    task = get_task(task_id)
    if task is None:
        raise KeyError(f"任务不存在：{task_id}")
    steps = task["context"].get("steps") or []
    if not steps:
        steps = [{"name": n, "done": False} for n in names]
    ctx = {**task["context"], "steps": steps}
    update_task(task_id, context=ctx)


def mark_step(task_id: str, name: str, done: bool = True, note: str = "") -> None:
    task = get_task(task_id)
    ctx = task["context"] if task else {"steps": []}
    for step in ctx.get("steps", []):
        if step["name"] == name:
            step["done"] = done
            if note:
                step["note"] = note
            break
    else:
        ctx.setdefault("steps", []).append({"name": name, "done": done, "note": note})
    update_task(task_id, context=ctx)


def first_pending_step(task_id: str) -> str | None:
    task = get_task(task_id)
    for step in (task["context"].get("steps") or []):
        if not step.get("done"):
            return step["name"]
    return None
