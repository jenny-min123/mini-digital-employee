# 测试用例表（12 条）与自动化映射

技术报告"自测用例覆盖度 5 分"直接引用这张表。左边是手册 8.1 节的用例编号，
右边是对应的 pytest 用例 —— 每条都有自动化证据，评委问起来直接 `pytest -v` 现场跑。

| 编号 | 类型 | 输入 | 期望行为 | 自动化用例 | 现状 |
|---|---|---|---|---|---|
| T01 | 正常 | W44 完整销售表 + 纪要 | 一路走到 REVIEW，含 4 章节 + 图表 | `test_flow.py::test_t01_happy_path_reaches_review` | 通过 |
| T02 | 正常 | 另一份数据 | 报告数字与源表一致 | `test_flow.py::test_t02_numbers_come_from_source` | 通过 |
| T03 | 正常 | 只有一家门店 | 正常出报告，不除零 | `test_flow.py::test_t03_single_store` | 通过 |
| T04 | 边界 | 销售额全为 0 | 出报告且不编造增长率 | `test_flow.py::test_t04_zero_sales_no_fabrication` | 通过 |
| T05 | 边界 | 只有 1 天数据 | 标注"数据周期不足"，不给环比结论 | `test_flow.py::test_t05_single_day_marks_insufficient` | 通过 |
| T06 | 边界 | 字数卡在 3000 上限附近 | 质检不误杀 | `test_qc.py::test_healthy_report_passes` + `test_rules_are_config_driven` | 通过 |
| T07 | 异常 | 不传销售表 | PAUSED，不进入执行 | `test_flow.py::test_t07_missing_material_pauses_before_running` | 通过 |
| T08 | 异常 | 缺"销售额"列 | 第一步就停，报"缺少必要列" | `test_flow.py::test_t08_missing_column_pauses_with_reason` | 通过 |
| T09 | 异常 | CSV 内容是乱码 | 报"无法解析"，不崩溃 | `test_flow.py::test_t09_garbled_csv_pauses_not_crash` | 通过 |
| T10 | 异常 | 断网 / 模型不可用 | 熔断暂停，不编造 | `test_flow.py::test_t10_llm_outage_pauses` | 通过 |
| T11 | 异常 | 纪要含手机号 | 质检拦住敏感信息，拒绝送审 | `test_flow.py::test_t11_sensitive_blocked_by_qc` | 通过 |
| T12 | 异常 | 审核退回后重做 | 新版本含意见内容 | `test_flow.py::test_t12_review_comment_adopted` | 通过 |

## 另外两条"硬要求"的自动化证据

- 模型不能自己批准：`test_machine.py::test_model_cannot_approve`、`test_flow.py::test_model_cannot_self_approve`
- 断点恢复：`test_flow.py::test_resume_continues_from_checkpoint`

## 企业 5 条公开用例怎么接

拿到企业用例后，把每条做成 `tests/cases/caseNN.json`：

```json
{
  "id": "case01",
  "period": "2026-W44",
  "sales_file": "data/case01_sales.xlsx",
  "minutes_file": "data/case01_minutes.docx",
  "expect": {"status": "REVIEW", "must_contain": ["本周概览", "异常分析"]}
}
```

再写一个 `run_cases.py` 批量跑，输出通过率表。表里每一行都要有实际运行结果，不要只写"预期通过"。
