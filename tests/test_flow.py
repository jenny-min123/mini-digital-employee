"""第 8 章验收：端到端流程（对应测试用例表 T01—T12）

全部走离线模板写作，不需要 API Key，换台电脑 pytest 就能全绿。
"""
import os

import pytest

from core import machine, orchestrator, writer
from core.state import Status
from qc.checks import load_docx_text
from tools import registry


def _form(fixtures, **override):
    form = {"period": "2026-W44",
            "sales_file": fixtures["good"],
            "minutes_file": fixtures["minutes"],
            "scope": ["全部门店"]}
    form.update(override)
    return form


def _run(fixtures, workdir, **override):
    submitted = orchestrator.submit(_form(fixtures, **override))
    assert submitted["problems"] == [], submitted["problems"]
    return submitted["task_id"], orchestrator.execute(submitted["task_id"], prefer_llm=False)


# T01 正常流程
def test_t01_happy_path_reaches_review(fixtures, workdir):
    tid, result = _run(fixtures, workdir)
    assert result["status"] == Status.REVIEW.value
    path = orchestrator.docx_path(tid)
    assert os.path.exists(path)
    text = load_docx_text(path)
    for heading in ["本周概览", "门店表现", "异常分析", "下周建议"]:
        assert heading in text
    assert result["qc"]["passed"] is True
    assert os.path.getsize(path) > 10000   # 含图片，不该是空壳


# T02 数字与源表一致
def test_t02_numbers_come_from_source(fixtures, workdir):
    tid, result = _run(fixtures, workdir)
    analyze = __import__("core.db", fromlist=["db"]).get_task(tid)["context"]["analyze"]
    text = load_docx_text(orchestrator.docx_path(tid))
    assert f"{analyze['total_sales']:,.2f}" in text
    assert "环比" in text


# T03 只有一家门店
def test_t03_single_store(fixtures, workdir, tmp_path, monkeypatch):
    import pandas as pd

    df = pd.read_excel(fixtures["good"])
    one = df[df["门店"] == "深圳南山店"]
    path = str(tmp_path / "one_store.xlsx")
    one.to_excel(path, index=False)
    tid, result = _run(fixtures, workdir, sales_file=path)
    assert result["status"] == Status.REVIEW.value


# T04 销售额全为 0
def test_t04_zero_sales_no_fabrication(fixtures, workdir):
    tid, result = _run(fixtures, workdir, sales_file=fixtures["zero_sales"])
    assert result["status"] == Status.REVIEW.value
    text = load_docx_text(orchestrator.docx_path(tid))
    assert "环比+" not in text and "环比-" not in text


# T05 只有 1 天数据
def test_t05_single_day_marks_insufficient(fixtures, workdir):
    tid, result = _run(fixtures, workdir, sales_file=fixtures["single_day"])
    text = load_docx_text(orchestrator.docx_path(tid))
    assert "数据周期不足" in text or "不具备" in text or "不提供环比" in text
    assert "环比+" not in text


# T07 不传销售表 -> PAUSED，且不进入执行
def test_t07_missing_material_pauses_before_running(fixtures, workdir):
    submitted = orchestrator.submit(_form(fixtures, sales_file=""))
    assert submitted["status"] == Status.PAUSED.value
    assert any("销售明细" in p for p in submitted["problems"])
    with pytest.raises(machine.IllegalTransition):
        orchestrator.execute(submitted["task_id"], prefer_llm=False)
    task = __import__("core.db", fromlist=["db"]).get_task(submitted["task_id"])
    assert task["deliverable"] is None       # 一个工具都没跑，一分钱没花


# T08 缺"销售额"列
def test_t08_missing_column_pauses_with_reason(fixtures, workdir):
    tid, result = _run(fixtures, workdir, sales_file=fixtures["missing_col"])
    assert result["status"] == Status.PAUSED.value
    assert "缺少必要列" in result["reason"]
    assert "已完成 0/4 步" in result["reason"]


# T09 乱码 CSV
def test_t09_garbled_csv_pauses_not_crash(fixtures, workdir):
    tid, result = _run(fixtures, workdir, sales_file=fixtures["garbled_csv"])
    assert result["status"] == Status.PAUSED.value
    assert "无法解析" in result["reason"]


# T10 断网 / 模型不可用 -> 熔断暂停，不编造
def test_t10_llm_outage_pauses(fixtures, workdir, monkeypatch):
    monkeypatch.setenv("MDW_STRICT_LLM", "1")
    monkeypatch.setattr(writer.llm, "available", lambda: True)
    monkeypatch.setattr(writer.llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(
        ConnectionError("网络不可用")))
    submitted = orchestrator.submit(_form(fixtures))
    result = orchestrator.execute(submitted["task_id"], prefer_llm=True)
    assert result["status"] == Status.PAUSED.value
    assert "已完成 3/4 步" in result["reason"]
    assert not os.path.exists(orchestrator.docx_path(submitted["task_id"]))


def test_t10b_llm_outage_degrades_without_strict(fixtures, workdir, monkeypatch):
    """默认模式：模型挂了就降级模板，流程不中断（演示不翻车）"""
    monkeypatch.delenv("MDW_STRICT_LLM", raising=False)
    monkeypatch.setattr(writer.llm, "available", lambda: True)
    monkeypatch.setattr(writer.llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(
        TimeoutError("超时")))
    submitted = orchestrator.submit(_form(fixtures))
    result = orchestrator.execute(submitted["task_id"], prefer_llm=True)
    assert result["status"] == Status.REVIEW.value
    assert "离线模板" in load_docx_text(orchestrator.docx_path(submitted["task_id"]))


# T11 敏感信息被拦
def test_t11_sensitive_blocked_by_qc(fixtures, workdir, monkeypatch):
    original = writer._template_content

    def leaky(analyze, minutes_text, agent_conf, review_comment=None):
        content = original(analyze, minutes_text, agent_conf, review_comment)
        content["sections"][0]["paragraphs"].append("如有疑问联系店长 13900001111。")
        return content

    monkeypatch.setattr(writer, "_template_content", leaky)
    tid, result = _run(fixtures, workdir)
    assert result["status"] == Status.PAUSED.value
    assert "敏感信息" in result["reason"]
    report = __import__("core.db", fromlist=["db"]).get_task(tid)["qc_report"]
    assert "sensitive" in report["blocking_failed"]


# T12 退回意见必须被采纳
def test_t12_review_comment_adopted(fixtures, workdir):
    tid, result = _run(fixtures, workdir)
    assert result["status"] == Status.REVIEW.value

    with pytest.raises(ValueError):
        orchestrator.reject(tid, "   ")

    comment = "华东区下滑原因没说清楚，请补充商场改造和排班不足的影响"
    orchestrator.reject(tid, comment)
    second = orchestrator.redo(tid, prefer_llm=False)
    assert second["status"] == Status.REVIEW.value
    text = load_docx_text(orchestrator.docx_path(tid))
    assert "华东区" in text or "商场" in text
    task = __import__("core.db", fromlist=["db"]).get_task(tid)
    assert task["redo_count"] == 1


# 模型不能自己批准
def test_model_cannot_self_approve(fixtures, workdir):
    tid, _ = _run(fixtures, workdir)
    with pytest.raises(machine.IllegalTransition):
        orchestrator.approve(tid, role=machine.SYSTEM)
    orchestrator.approve(tid)
    assert orchestrator.status(tid)["status"] == Status.DELIVERED.value


# 断点恢复：工具故障后暂停，修好后从断点继续
def test_resume_continues_from_checkpoint(fixtures, workdir, monkeypatch):
    from tools.base import FatalToolError

    def broken(**kwargs):
        raise FatalToolError("纪要服务不可用")

    monkeypatch.setitem(registry.REGISTRY, "read_minutes", broken)
    submitted = orchestrator.submit(_form(fixtures))
    paused = orchestrator.execute(submitted["task_id"], prefer_llm=False)
    assert paused["status"] == Status.PAUSED.value
    assert "已完成 2/4 步" in paused["reason"]

    monkeypatch.undo()
    resumed = orchestrator.resume(submitted["task_id"], prefer_llm=False)
    assert resumed["status"] == Status.REVIEW.value


# 事件流留痕
def test_events_recorded(fixtures, workdir):
    tid, _ = _run(fixtures, workdir)
    from core import db

    kinds = [e["kind"] for e in db.get_events(tid)]
    assert "task_created" in kinds
    assert any(k.startswith("tool_call") for k in kinds)
    assert "qc_passed" in kinds
    assert "transition:finish" in kinds
