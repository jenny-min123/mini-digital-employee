"""第 6 章验收：三类质检必须"能拦住东西"，不能永远通过"""
import json
import os

from qc.checks import load_docx_text
from qc.runner import evaluate, run_qc
from tools.data_tools import analyze_sales, read_sales
from tools.doc_tools import write_docx


def _analyze(fixtures):
    sales = read_sales(fixtures["good"])
    return analyze_sales(sales["records"], "2026-W44", out_dir="output/charts")


def _build(tmp_path, analyze, sections=None, extra_para="", image=True, name="周报.docx"):
    """用真模板内容造一份 docx，再按需破坏某一项"""
    sections = sections or [
        {"heading": "本周概览", "paragraphs": [
            "本期（2026-W44，2026-10-26 至 2026-11-01，共 7 天）覆盖 3 家门店，"
            "实现销售额 100000.00 元，订单 1600 笔，客单价 62.50 元。"
            "品类结构上，生鲜贡献销售额 42000.00 元，占比 42.00%；日用百货次之，占比 24.00%。"
            "与上一周期相比，深圳南山店表现最好，环比+2.50%；上海浦东店压力最大，环比-25.00%。"
            "日均销售额 14285.71 元，数据覆盖 7 天，口径与上周一致，可直接横向对比。"]},
        {"heading": "门店表现", "paragraphs": [
            "深圳南山店实现销售额 41000.00 元，订单 683 笔，占全部门店 41.00%，环比+2.50%。"
            "上海静安店实现销售额 35000.00 元，订单 583 笔，占全部门店 35.00%，环比-2.78%。"
            "上海浦东店实现销售额 24000.00 元，订单 400 笔，占全部门店 24.00%，环比-25.00%。"
            "整体来看，销售额最高的门店是深圳南山店，门店间差距较大，需要结合区域客流变化进一步判断。"],
         "image": analyze["charts"]["store_bar"] if image else None},
        {"heading": "异常分析", "paragraphs": [
            "上海浦东店本期销售额 24000.00 元，上期 32000.00 元，环比-25.00%，波动方向为下滑，"
            "已超出 ±15% 的关注阈值。结合周会纪要，相关定性信息为：商场改造导致客流下降，晚间排班不足。"
            "建议由区域负责人在下次周会补充整改说明。" + extra_para],
         "image": analyze["charts"]["mom_bar"] if image else None},
        {"heading": "下周建议", "paragraphs": [
            "针对上海浦东店（环比-25.00%），由区域负责人在下周周会前复盘下滑原因并给出补救计划。"
            "围绕贡献最大的生鲜品类安排下周陈列与促销资源，稳住基本盘。"
            "保持周报口径一致：百分比一律引用系统计算结果，避免人工改数导致前后不一致。"
            "下周同一时间按相同模板出报，便于管理层做周与周之间的横向对比。"
            "数据说明部分保留广州天河店补录情况，避免被误读为经营异常。"]},
    ]
    path = os.path.join(str(tmp_path), name)
    write_docx({"title": "经营周报", "period": "2026-W44",
                "generated_at": "2026-10-27 10:00", "sections": sections}, path)
    return path


def test_healthy_report_passes(tmp_path, fixtures):
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze)
    result = evaluate(path, analyze)
    assert result["ok"], result["blocking_failed"]
    assert not result["warnings"]


def test_missing_section_blocked(tmp_path, fixtures):
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze, sections=[
        {"heading": "本周概览", "paragraphs": ["只有一节"]}])
    result = evaluate(path, analyze)
    assert "structure" in result["blocking_failed"]


def test_fabricated_percentage_blocked(tmp_path, fixtures):
    """正文里凭空多出 环比+99.99%，质检必须抓出来"""
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze, extra_para="另外华东区整体环比+99.99%，需要重点关注。")
    result = evaluate(path, analyze)
    assert "consistency" in result["blocking_failed"]
    item = next(i for i in result["items"] if i["id"] == "consistency")
    assert any("99.99" in bad for bad in item["bad"])


def test_too_short_blocked(tmp_path, fixtures):
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze, sections=[
        {"heading": h, "paragraphs": ["短"], "image": None}
        for h in ["本周概览", "门店表现", "异常分析", "下周建议"]])
    result = evaluate(path, analyze)
    assert "length" in result["blocking_failed"]


def test_sensitive_phone_blocked(tmp_path, fixtures):
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze, extra_para="如有疑问请联系店长 13812345678。")
    result = evaluate(path, analyze)
    assert "sensitive" in result["blocking_failed"]


def test_missing_chart_is_only_warning(tmp_path, fixtures):
    """图表缺失是 warning，不拦截送审 —— severity 由 yaml 控制"""
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze, image=False)
    result = evaluate(path, analyze)
    assert result["ok"] is True
    assert "chart" in result["warnings"]


def test_rules_are_config_driven(tmp_path, fixtures):
    """把上限改成 100 字，同一份文档立刻被判不合格 —— 现场演示就演这个"""
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze)
    strict = {"rules": [{"id": "length", "name": "篇幅", "type": "word_count",
                         "params": {"min": 100, "max": 100}, "severity": "blocking"}]}
    assert evaluate(path, analyze, strict)["ok"] is False


def test_unknown_rule_type_does_not_crash(tmp_path, fixtures):
    analyze = _analyze(fixtures)
    path = _build(tmp_path, analyze)
    conf = {"rules": [{"id": "future", "name": "未来规则", "type": "不存在的类型",
                       "params": {}, "severity": "blocking"}]}
    result = evaluate(path, analyze, conf)
    assert result["ok"] is True           # 未知类型降级成 warning
    assert "future" in result["warnings"]


def test_run_qc_writes_report(tmp_path, fixtures, workdir):
    """质检记录要随交付物一起落盘，这是赛题硬要求"""
    analyze = _analyze(fixtures)
    path = _build(workdir, analyze)
    report = run_qc("t-qc-1", path, analyze)
    assert report["passed"] is True
    assert os.path.exists(report["report_path"])
    with open(report["report_path"], encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["passed"] is True and saved["round"] == 1


def test_run_qc_calls_fix_and_retries(tmp_path, fixtures, workdir):
    """质检不过要自动修一次，改好了就放行"""
    analyze = _analyze(fixtures)
    path = _build(workdir, analyze, extra_para="华东区环比+88.88%。")

    def fix_fn(failed):
        assert failed, "必须把失败项传给修正函数"
        _build(workdir, analyze, name="周报.docx", extra_para="")

    report = run_qc("t-qc-2", path, analyze, fix_fn=fix_fn)
    assert report["passed"] is True
    assert report["round"] == 2


def test_qc_text_loader_includes_tables(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("正文一段")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "门店"
    table.rows[0].cells[1].text = "销售额"
    path = os.path.join(str(tmp_path), "t.docx")
    doc.save(path)
    text = load_docx_text(path)
    assert "正文一段" in text and "门店" in text
