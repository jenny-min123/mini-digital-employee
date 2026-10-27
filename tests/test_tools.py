"""第 5 章验收：三个工具的入参/出参、异常分支、白名单、重试熔断"""
import json
import os

import pytest

from tools import registry
from tools.base import FatalToolError, ToolError
from tools.data_tools import analyze_sales, read_sales
from tools.doc_tools import read_minutes, write_docx

AGENT = {"allowed_tools": ["read_sales", "analyze_sales", "write_docx"],
         "limits": {"max_tool_retry": 2}}


def test_read_sales_ok(fixtures):
    out = read_sales(fixtures["good"])
    assert out["rows"] > 0
    assert out["date_range"][0] <= out["date_range"][1]
    assert set(["日期", "门店", "品类", "销售额", "订单数"]) <= set(out["columns"])
    assert isinstance(out["records"][0]["销售额"], float)
    json.dumps(out)  # 必须能直接序列化，否则模型/数据库都接不了


def test_column_alias_mapped(fixtures):
    """企业表格叫"销售金额/门店名称"，靠 COLUMN_MAP 兜住"""
    out = read_sales(fixtures["alias_col"])
    assert "销售额" in out["columns"] and "门店" in out["columns"]


def test_missing_column(fixtures):
    with pytest.raises(ToolError, match="缺少必要列"):
        read_sales(fixtures["missing_col"])


def test_missing_file():
    with pytest.raises(ToolError, match="文件不存在"):
        read_sales("data/根本没有这个文件.xlsx")


def test_garbled_csv(fixtures):
    with pytest.raises(ToolError):
        read_sales(fixtures["garbled_csv"])


def test_empty_table(fixtures):
    with pytest.raises(ToolError, match="空表"):
        read_sales(fixtures["empty"])


def test_analyze_mom_and_whitelist(fixtures):
    sales = read_sales(fixtures["good"])
    out = analyze_sales(sales["records"], "2026-W44", out_dir="output/charts")
    assert out["total_sales"] > 0
    assert out["mom_available"] is True
    assert out["mom_percentages"], "有环比数据时必须给出数字白名单"
    # 上海浦东店下滑 25% 左右，应被识别为异常门店
    assert any("浦东" in a["门店"] for a in out["anomalies"])
    assert all(abs(a["环比"]) > 15 for a in out["anomalies"])
    json.dumps(out)
    assert os.path.exists(out["charts"]["store_bar"])


def test_analyze_single_day_has_no_mom(fixtures):
    """T05：只有 1 天数据时不许给环比结论"""
    sales = read_sales(fixtures["single_day"])
    out = analyze_sales(sales["records"], "2026-W44", out_dir="output/charts")
    assert out["mom_available"] is False
    assert out["anomalies"] == []
    assert any("不足" in w or "无数据" in w for w in out["warnings"])


def test_analyze_zero_sales(fixtures):
    """T04：销售额全为 0，不许除零崩掉，也不许编增长率"""
    sales = read_sales(fixtures["zero_sales"])
    out = analyze_sales(sales["records"], "2026-W44", out_dir="output/charts")
    assert out["total_sales"] == 0
    assert out["mom_available"] is False


def test_analyze_empty_records():
    with pytest.raises(ToolError, match="数据为空"):
        analyze_sales([], "2026-W44", out_dir="output/charts")


def test_analyze_bad_period(fixtures):
    sales = read_sales(fixtures["good"])
    with pytest.raises(ToolError, match="统计周期格式"):
        analyze_sales(sales["records"], "第44周", out_dir="output/charts")


def test_analyze_period_without_data(fixtures):
    sales = read_sales(fixtures["good"])
    with pytest.raises(ToolError, match="没有任何数据"):
        analyze_sales(sales["records"], "2026-W30", out_dir="output/charts")


def test_write_docx_idempotent(tmp_path, fixtures):
    """同一个输出路径写两次，只能有一个文件（重试不产生 周报(1).docx）"""
    content = {"title": "测试周报", "period": "2026-W44", "generated_at": "2026-10-27 10:00",
               "sections": [{"heading": "本周概览", "paragraphs": ["测试正文"], "image": None}]}
    path = tmp_path / "out" / "周报.docx"
    first = write_docx(content, str(path))
    second = write_docx(content, str(path))
    assert first["file_path"] == second["file_path"]
    assert len(list((tmp_path / "out").glob("*.docx"))) == 1


def test_write_docx_rejects_empty_content(tmp_path):
    with pytest.raises(ToolError, match="内容为空"):
        write_docx({"title": "x", "sections": []}, str(tmp_path / "a.docx"))


def test_write_docx_raises_on_missing_image(tmp_path):
    content = {"title": "x", "period": "p", "generated_at": "t",
               "sections": [{"heading": "a", "paragraphs": ["b"], "image": "不存在.png"}]}
    with pytest.raises(ToolError, match="图片不存在"):
        write_docx(content, str(tmp_path / "b.docx"))


def test_read_minutes_ok(fixtures):
    out = read_minutes(fixtures["minutes"])
    assert "浦东" in out["text"]


def test_read_minutes_broken(fixtures):
    with pytest.raises(ToolError):
        read_minutes(fixtures["broken_docx"])


def test_tool_whitelist_denies(fixtures):
    """read_minutes 不在这个员工的白名单里，必须被拒绝"""
    with pytest.raises(registry.ToolDenied):
        registry.call_tool(AGENT, "read_minutes", {"file_path": fixtures["minutes"]})


def test_tool_schemas_only_whitelisted():
    schemas = registry.tool_schemas({"allowed_tools": ["read_sales"]})
    assert [s["function"]["name"] for s in schemas] == ["read_sales"]


def test_retry_then_fatal(monkeypatch, fixtures):
    """连续失败必须熔断成 FatalToolError，而不是把空结果返回给模型"""
    calls = {"n": 0}

    def boom(**kwargs):
        calls["n"] += 1
        raise ToolError("模拟工具故障")

    monkeypatch.setitem(registry.REGISTRY, "read_sales", boom)
    monkeypatch.setattr(registry.time, "sleep", lambda *_: None)
    with pytest.raises(FatalToolError, match="连续失败"):
        registry.call_with_retry(AGENT, "read_sales", {"file_path": "x"}, max_retry=3)
    assert calls["n"] == 3


def test_retry_succeeds_second_time(monkeypatch):
    state = {"n": 0}

    def flaky(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise ToolError("第一次抖了一下")
        return {"ok": True}

    monkeypatch.setitem(registry.REGISTRY, "read_sales", flaky)
    monkeypatch.setattr(registry.time, "sleep", lambda *_: None)
    assert registry.call_with_retry(AGENT, "read_sales", {"file_path": "x"}, max_retry=3)["ok"]
    assert state["n"] == 2
