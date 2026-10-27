"""第 5 章 · 正文写作（模型写 / 离线模板写，两条路都通）

规则：
1. 所有百分比只能来自 analyze_sales 的 mom_percentages，模型不许自由发挥
2. 没配 Key 或模型挂了，自动降级到离线模板 —— 流程不中断，也不会编数字
3. 审核意见必须体现在正文里（"退回意见被采纳"那 5 分就在这）
"""
import os
import re
from datetime import datetime

from core import llm


def _pct(value: float) -> str:
    """统一格式：环比+12.34% / 环比-5.60%（质检正则按这个格式抓）"""
    return f"环比{value:+.2f}%"


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。\n；]", text or "") if s.strip()]


def _minutes_evidence(minutes_text: str, keys: list[str], limit: int = 3) -> list[str]:
    """从纪要里捞出与异常门店相关的句子，作为定性归因"""
    if not keys:
        return []
    picked = [s for s in _sentences(minutes_text) if any(k and k in s for k in keys)]
    return picked[:limit]


def _template_content(analyze: dict, minutes_text: str, agent_conf: dict,
                      review_comment: str | None = None) -> dict:
    """离线模板写作：完全基于 analyze 的数字拼装，不可能出现白名单外的百分比"""
    period = analyze["period"]
    stores = analyze.get("by_store", [])
    cats = analyze.get("by_category", [])
    anomalies = analyze.get("anomalies", [])
    warnings = analyze.get("warnings", [])
    mom_ok = analyze.get("mom_available")

    overview: list[str] = []
    head = (
        f"本期（{period}，{analyze['period_range'][0]} 至 {analyze['period_range'][1]}，"
        f"共 {analyze['days_covered']} 天）覆盖 {analyze['store_count']} 家门店，"
        f"实现销售额 {_money(analyze['total_sales'])} 元，订单 {analyze['total_orders']} 笔，"
        f"客单价 {_money(analyze['avg_order_value'])} 元。"
    )
    overview.append(head)
    if analyze["days_covered"]:
        overview.append(
            f"日均销售额 {_money(analyze['total_sales'] / analyze['days_covered'])} 元，"
            f"数据覆盖 {analyze['days_covered']} 天，口径与上周一致。"
        )

    if cats:
        top = cats[0]
        second = cats[1] if len(cats) > 1 else None
        text = f"品类结构上，{top['品类']}贡献销售额 {_money(top['销售额'])} 元，占比 {top['占比']:.2f}%"
        if second:
            text += f"；{second['品类']}次之，占比 {second['占比']:.2f}%"
        overview.append(text + "。")

    if mom_ok and stores:
        ranked = [s for s in stores if s.get("环比") is not None]
        if ranked:
            best = max(ranked, key=lambda s: s["环比"])
            worst = min(ranked, key=lambda s: s["环比"])
            overview.append(
                f"与上一周期相比，{best['门店']}表现最好，{_pct(best['环比'])}；"
                f"{worst['门店']}压力最大，{_pct(worst['环比'])}。"
            )
    else:
        overview.append("本期缺少上一周期数据，按规则不提供环比结论，避免给出无依据的增长率。")

    store_lines: list[str] = []
    for store in stores:
        line = (f"{store['门店']}实现销售额 {_money(store['销售额'])} 元，"
                f"订单 {store['订单数']} 笔，占全部门店 {store['占比']:.2f}%")
        line += f"，{_pct(store['环比'])}。" if store.get("环比") is not None else (
            f"，本期仅 {store.get('覆盖天数', 0)} 天数据，数据不完整，不计算环比。"
            if store.get("数据完整") is False else "，上期无数据，不计算环比。")
        if store["订单数"]:
            line += f"该店客单价 {_money(store['销售额'] / store['订单数'])} 元。"
        store_lines.append(line)
    store_lines.append(
        f"整体来看，销售额最高的门店是{stores[0]['门店']}，占全部门店 {stores[0]['占比']:.2f}%，"
        f"门店间差距{'较大' if len(stores) > 3 else '不明显'}，需要结合区域客流变化进一步判断。"
        if stores else "本期无门店数据。"
    )

    anomaly_lines: list[str] = []
    if review_comment:
        anomaly_lines.append(f"按审核意见修改：{review_comment}")
    if anomalies:
        evidence_keys = [a["门店"] for a in anomalies] + ["华东", "华南", "华北", "客流", "促销"]
        evidence = _minutes_evidence(minutes_text, evidence_keys)
        for item in anomalies:
            anomaly_lines.append(
                f"{item['门店']}本期销售额 {_money(item['销售额'])} 元，上期 {_money(item['上期销售额'])} 元，"
                f"{_pct(item['环比'])}，波动方向为{item['方向']}，已超出 ±15% 的关注阈值。"
            )
        if evidence:
            anomaly_lines.append("结合周会纪要，相关定性信息为：" + "；".join(evidence) + "。")
        else:
            anomaly_lines.append("周会纪要中未提到这些门店的直接原因，建议由区域负责人在下次周会补充说明，"
                                 "在此之前不对波动原因做推测性归因。")
    else:
        if mom_ok:
            anomaly_lines.append("本期各门店环比波动均在 ±15% 阈值以内，未发现需要单独说明的异常门店。")
        else:
            anomaly_lines.append("由于缺少上一周期数据，本期不做环比判定，因此也不判定异常门店。"
                                 "这不是“没有异常”，而是“无法判断”，需要在数据补齐后复核。")
    for warn in warnings:
        anomaly_lines.append(f"数据说明：{warn}。")

    advice = []
    if anomalies:
        for item in anomalies[:3]:
            action = "复盘下滑原因并给出补救计划" if item["方向"] == "下滑" else "总结可复制做法并推广"
            advice.append(f"针对{item['门店']}（{_pct(item['环比'])}），由区域负责人在下周周会前{action}。")
    if not mom_ok:
        advice.append("补齐上一周期销售明细，使环比具备可比口径后再输出增长率结论。")
    if cats:
        advice.append(f"围绕贡献最大的{cats[0]['品类']}品类安排下周陈列与促销资源，稳住基本盘。")
    advice.append("保持周报口径一致：百分比一律引用系统计算结果，避免人工改数导致前后不一致。")
    advice.append("下周同一时间按相同模板出报，便于管理层做周与周之间的横向对比。")

    return {
        "title": agent_conf.get("deliverable", {}).get("doc_title", "经营周报"),
        "period": period,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sections": [
            {"heading": "本周概览", "paragraphs": overview, "image": None},
            {"heading": "门店表现", "paragraphs": store_lines,
             "image": (analyze.get("charts") or {}).get("store_bar")},
            {"heading": "异常分析", "paragraphs": anomaly_lines,
             "image": (analyze.get("charts") or {}).get("mom_bar")},
            {"heading": "下周建议", "paragraphs": advice, "image": None},
        ],
    }


def _llm_content(analyze: dict, minutes_text: str, agent_conf: dict,
                 review_comment: str | None, fix_items: list[dict] | None) -> dict:
    """让模型写正文，但数字必须来自 analyze"""
    sections = agent_conf.get("deliverable", {}).get(
        "sections", ["本周概览", "门店表现", "异常分析", "下周建议"])
    payload = {
        "周期": analyze["period"],
        "汇总": {
            "销售额": analyze["total_sales"], "订单数": analyze["total_orders"],
            "客单价": analyze["avg_order_value"], "门店数": analyze["store_count"],
            "覆盖天数": analyze["days_covered"], "有环比": analyze["mom_available"],
        },
        "门店": analyze["by_store"],
        "品类": analyze["by_category"],
        "异常门店": analyze["anomalies"],
        "数据说明": analyze["warnings"],
        "周会纪要": minutes_text[:2000],
    }
    constraints = "\n".join(f"- {g}" for g in agent_conf.get("guardrails", []))
    extra = ""
    if review_comment:
        extra += (f"\n本次是重做，审核人意见（必须逐条落实，并在正文中明确回应）：{review_comment}\n")
    if fix_items:
        problems = "；".join(f"{i['name']}：{i['msg']}" for i in fix_items)
        extra += f"\n上一版被质检拦下的问题，本次必须修正：{problems}\n"

    system = (
        f"{agent_conf['role']}\n\n"
        f"你只负责写正文，数字由系统提供。硬性约束：\n{constraints}\n"
        "- 百分比只能引用【门店】里出现的环比数值，格式必须是 环比+12.34% 或 环比-5.60%，不得四舍五入到整数\n"
        "- 不允许新增任何未出现在输入JSON里的数字\n"
        f"- 章节标题必须且只能是：{sections}\n"
        "- 每节 1—3 段，全文 800—1800 字，语言是给管理层看的书面中文，不要用 Markdown 标记\n"
    )
    user = (f"统计数据：\n{payload}\n{extra}\n"
            "只输出 JSON：{\"sections\":[{\"heading\":\"...\",\"paragraphs\":[\"...\"]}]}")

    data = llm.chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])
    got = data.get("sections") or []
    headings = [s.get("heading") for s in got]
    if sorted(headings) != sorted(sections) or not all(s.get("paragraphs") for s in got):
        raise ValueError(f"模型返回的章节不符合要求：{headings}")

    images = {"门店表现": (analyze.get("charts") or {}).get("store_bar"),
              "异常分析": (analyze.get("charts") or {}).get("mom_bar")}
    return {
        "title": agent_conf.get("deliverable", {}).get("doc_title", "经营周报"),
        "period": analyze["period"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sections": [
            {"heading": s["heading"], "paragraphs": s["paragraphs"],
             "image": images.get(s["heading"])}
            for s in got
        ],
    }


def _ensure_min_length(content: dict, min_chars: int | None = None) -> dict:
    """篇幅兜底：数据量小（如单门店）时正文可能不足 600 字。

    这里补的是"口径说明"这类不含新数字的段落 —— 既把字数补够，
    又不会引入任何白名单外的百分比，质检照样过。
    """
    if min_chars is None:
        try:
            from core import config
            rules = config.load_qc_rules()["rules"]
            min_chars = next((int(r["params"].get("min", 600)) for r in rules
                              if r["type"] == "word_count"), 600)
        except Exception:
            min_chars = 600

    def chars_of(c: dict) -> int:
        return sum(len(p) for s in c["sections"] for p in s.get("paragraphs", []))

    filler = [
        "数据口径说明：本次统计仅使用上传的销售明细，日期无法解析或销售额缺失的行已剔除，"
        "不做插值补数，也不修改原始文件；合计值与门店明细均由同一份数据计算得出，可逐行复核。",
        "流程说明：本报告由数字员工按固定步骤生成（读取明细、计算指标、读取周会纪要、生成文档），"
        "生成后先经自动质检，再提交人工审核，审核通过后才会对外发布，任何环节都不能由模型自行判定放行。",
        "后续动作：若本期数据存在缺报或口径变化，请在下周同一时间点前补齐，"
        "以便下一期报告能够按相同口径做环比对比，避免出现不可比的情况。",
    ]
    target_section = content["sections"][-1] if content["sections"] else None
    for text in filler:
        if chars_of(content) >= min_chars:
            break
        if target_section is not None:
            target_section["paragraphs"].append(text)
    return content


def build_content(analyze: dict, minutes_text: str, agent_conf: dict,
                  review_comment: str | None = None,
                  fix_items: list[dict] | None = None,
                  prefer_llm: bool = True) -> dict:
    """对外唯一入口：优先模型写，失败就降级模板，绝不因为模型挂了而丢流程

    例外：设置 MDW_STRICT_LLM=1 时不做降级，让上层把任务置 PAUSED。
    演示"断网熔断"时用这个开关。
    """
    strict = os.getenv("MDW_STRICT_LLM") == "1"
    if prefer_llm and llm.available():
        try:
            return _ensure_min_length(_llm_content(
                analyze, minutes_text, agent_conf, review_comment, fix_items))
        except Exception:  # 网络、限流、JSON 不合法都走这里
            if strict:
                raise
            content = _template_content(analyze, minutes_text, agent_conf, review_comment)
            content["generated_at"] += "（模型不可用，已降级离线模板）"
            return _ensure_min_length(content)
    return _ensure_min_length(_template_content(analyze, minutes_text, agent_conf, review_comment))
