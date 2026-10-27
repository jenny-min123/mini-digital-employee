"""第 5 章 · 工具一 + 工具二：读销售明细、算指标画图

两个要点：
1. COLUMN_MAP 做列名映射 —— 企业给的表格叫法五花八门，硬编码列名一定翻车
2. 返回值必须 JSON 安全 —— Timestamp / numpy 类型直接进 json.dumps 会炸
"""
import os
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")  # 不开窗口，直接出图（服务器必备）
import matplotlib.pyplot as plt
import pandas as pd

from tools.base import ToolError

# 中文不乱码（Windows 上 Microsoft YaHei 一定有，其余是跨平台兜底）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 标准列名
REQUIRED = ["日期", "门店", "品类", "销售额", "订单数"]
OPTIONAL = ["区域"]

# 一个门店一周至少要有几天数据，才算"覆盖完整"、才允许算环比
MIN_STORE_DAYS = 3

# 企业表格里的常见别名 -> 标准列名
COLUMN_MAP = {
    "日期": ["日期", "销售日期", "交易日期", "date", "date_day", "日期(天)"],
    "门店": ["门店", "门店名称", "店铺", "店名", "store", "store_name"],
    "品类": ["品类", "商品品类", "类别", "分类", "category"],
    "销售额": ["销售额", "销售金额", "金额", "营业额", "sales", "amount"],
    "订单数": ["订单数", "订单量", "单量", "交易笔数", "orders", "order_count"],
    "区域": ["区域", "大区", "地区", "region"],
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把各种叫法统一成标准列名；认不出来的保持原样，由后面的缺列校验兜底"""
    lookup = {}
    for std, aliases in COLUMN_MAP.items():
        for alias in aliases:
            lookup[str(alias).strip().lower()] = std
    renamed = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in lookup and lookup[key] not in renamed.values():
            renamed[col] = lookup[key]
    return df.rename(columns=renamed)


def read_sales(file_path: str) -> dict:
    """读销售明细，返回行数、列名、日期范围、记录列表"""
    if not os.path.exists(file_path):
        raise ToolError(f"文件不存在：{file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path)
        elif ext == ".csv":
            df = None
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    df = pd.read_csv(file_path, encoding=enc)
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
            if df is None:
                raise ToolError("CSV 无法解析：编码既不是 UTF-8 也不是 GBK，文件可能已损坏")
        else:
            raise ToolError(f"不支持的文件类型 {ext}，只接受 .xlsx / .csv")
    except ToolError:
        raise
    except Exception as exc:  # 文件损坏、格式错乱
        raise ToolError(f"文件无法解析，请确认是 xlsx / csv 格式：{exc}") from exc

    df = _normalize_columns(df)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ToolError(f"缺少必要列：{missing}，实际列为 {list(df.columns)}")
    if df.empty:
        raise ToolError("销售明细是空表，没有任何数据行")

    df = df.dropna(subset=["销售额"])
    try:
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    except Exception as exc:
        raise ToolError(f"日期列无法解析：{exc}") from exc
    bad_dates = int(df["日期"].isna().sum())
    df = df.dropna(subset=["日期"])
    if df.empty:
        raise ToolError("日期列全部无法解析，请检查日期格式")

    for col in ("销售额", "订单数"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    records = [
        {
            "日期": row["日期"].strftime("%Y-%m-%d"),
            "门店": str(row["门店"]),
            "品类": str(row["品类"]),
            "销售额": float(row["销售额"]),
            "订单数": int(row["订单数"]),
            **({"区域": str(row["区域"])} if "区域" in df.columns else {}),
        }
        for row in df.to_dict("records")
    ]

    return {
        "rows": len(records),
        "columns": list(df.columns),
        "date_range": [df["日期"].min().strftime("%Y-%m-%d"), df["日期"].max().strftime("%Y-%m-%d")],
        "dropped_bad_date_rows": bad_dates,
        "records": records,
    }


def _week_start(period: str) -> date:
    """'2026-W44' -> 该周周一。支持 '2026W44' / '2026-w44'"""
    text = str(period).strip().upper().replace("-W", "W")
    try:
        year_s, week_s = text.split("W")
        return date.fromisocalendar(int(year_s), int(week_s), 1)
    except Exception as exc:
        raise ToolError(f"统计周期格式应为 2026-W44，收到：{period}") from exc


def _chart_store_bar(by_store: pd.DataFrame, period: str, out_dir: str) -> str:
    path = os.path.join(out_dir, "store_bar.png")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = ["#c0392b"] * len(by_store)  # 涨用红
    ax.bar(by_store["门店"], by_store["销售额"], color=colors)
    ax.set_title(f"{period} 各门店销售额")
    ax.set_ylabel("销售额（元）")
    for i, (name, value) in enumerate(zip(by_store["门店"], by_store["销售额"])):
        ax.text(i, value, f"{value:,.0f}", ha="center", va="bottom", fontsize=8)
    plt.xticks(rotation=30)
    plt.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _chart_mom_bar(mom_df: pd.DataFrame, period: str, out_dir: str) -> str:
    path = os.path.join(out_dir, "mom_bar.png")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    # 中国习惯：涨=红，跌=绿
    colors = ["#c0392b" if v >= 0 else "#27ae60" for v in mom_df["环比"]]
    ax.bar(mom_df["门店"], mom_df["环比"], color=colors)
    ax.axhline(0, color="#555", linewidth=0.8)
    ax.axhline(15, color="#999", linewidth=0.6, linestyle="--")
    ax.axhline(-15, color="#999", linewidth=0.6, linestyle="--")
    ax.set_title(f"{period} 各门店销售额环比（虚线为 ±15% 阈值）")
    ax.set_ylabel("环比（%）")
    plt.xticks(rotation=30)
    plt.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def analyze_sales(records: list, period: str, scope: list | None = None,
                  out_dir: str = "output/charts") -> dict:
    """算汇总指标、门店环比、异常门店，并出两张图

    环比是"本周 vs 上周"的真实口径，不是拿门店排序硬凑的近似值。
    数据不足时明确告诉上层"不能给环比结论"，绝不编一个数字出来。
    """
    if not records:
        raise ToolError("数据为空，无法分析")

    df = pd.DataFrame(records)
    df["日期"] = pd.to_datetime(df["日期"])
    df["销售额"] = pd.to_numeric(df["销售额"], errors="coerce").fillna(0)
    df["订单数"] = pd.to_numeric(df["订单数"], errors="coerce").fillna(0)

    # 门店范围筛选
    if scope and "全部门店" not in scope and "区域" in df.columns:
        df = df[df["区域"].isin(scope)]
        if df.empty:
            raise ToolError(f"所选门店范围 {scope} 在本期数据中没有记录")

    cur_start = _week_start(period)
    cur_end = cur_start + timedelta(days=7)
    prev_start = cur_start - timedelta(days=7)

    cur = df[(df["日期"] >= pd.Timestamp(cur_start)) & (df["日期"] < pd.Timestamp(cur_end))]
    prev = df[(df["日期"] >= pd.Timestamp(prev_start)) & (df["日期"] < pd.Timestamp(cur_start))]
    if cur.empty:
        raise ToolError(
            f"统计周期 {period}（{cur_start} 起）内没有任何数据，"
            f"实际数据范围 {df['日期'].min().date()} ~ {df['日期'].max().date()}"
        )

    total = float(cur["销售额"].sum())
    orders = int(cur["订单数"].sum())
    days_covered = int(cur["日期"].dt.date.nunique())

    by_store = (cur.groupby("门店", as_index=False)
                .agg(销售额=("销售额", "sum"), 订单数=("订单数", "sum"))
                .sort_values("销售额", ascending=False))
    by_store["占比"] = (by_store["销售额"] / total * 100).round(2) if total else 0.0

    prev_by_store = prev.groupby("门店", as_index=False).agg(上期销售额=("销售额", "sum"))
    merged = by_store.merge(prev_by_store, on="门店", how="left")

    # 门店级的覆盖天数：只有 1 天数据的门店，环比是伪信号，必须排除
    coverage = {k: int(v) for k, v in cur.groupby("门店")["日期"].nunique().items()}
    merged["覆盖天数"] = [coverage.get(s, 0) for s in merged["门店"]]
    merged["数据完整"] = merged["覆盖天数"] >= MIN_STORE_DAYS

    merged["环比"] = [
        round((row["销售额"] / row["上期销售额"] - 1) * 100, 2)
        if (pd.notna(row["上期销售额"]) and row["上期销售额"] and row["数据完整"])
        else None
        for _, row in merged.iterrows()
    ]

    mom_available = bool(merged["环比"].notna().any()) and not prev.empty
    warnings: list[str] = []
    if prev.empty:
        warnings.append(f"上一周期（{prev_start} 起）无数据，本期不提供环比结论")
    incomplete = merged[~merged["数据完整"]]
    if not incomplete.empty:
        detail = "、".join(f"{r['门店']}（{int(r['覆盖天数'])} 天）"
                          for _, r in incomplete.iterrows())
        warnings.append(f"以下门店本期数据不完整，不计算环比也不判定异常：{detail}")
    if not prev.empty:
        no_prev = merged[merged["数据完整"] & merged["环比"].isna()]["门店"].tolist()
        if no_prev:
            warnings.append(f"以下门店上期无数据，不计算环比：{no_prev}")
    if days_covered <= 2:
        warnings.append(f"统计周期内仅有 {days_covered} 天数据，数据周期不足")

    anomalies = []
    if mom_available:
        flagged = merged[merged["环比"].notna() & (merged["环比"].abs() > 15)]
        anomalies = [
            {"门店": row["门店"], "环比": float(row["环比"]),
             "销售额": float(row["销售额"]), "上期销售额": float(row["上期销售额"]),
             "方向": "上升" if row["环比"] > 0 else "下滑"}
            for _, row in flagged.iterrows()
        ]

    by_category = (cur.groupby("品类", as_index=False)
                   .agg(销售额=("销售额", "sum"), 订单数=("订单数", "sum"))
                   .sort_values("销售额", ascending=False))
    by_category["占比"] = (by_category["销售额"] / total * 100).round(2) if total else 0.0

    daily = (cur.assign(日期=cur["日期"].dt.strftime("%Y-%m-%d"))
             .groupby("日期", as_index=False)["销售额"].sum())

    os.makedirs(out_dir, exist_ok=True)
    chart_store = _chart_store_bar(by_store, period, out_dir)
    chart_mom = None
    if mom_available:
        chart_mom = _chart_mom_bar(merged[merged["环比"].notna()], period, out_dir)

    # 数字白名单：正文里允许出现的所有百分比，质检拿它当唯一真值来源
    mom_values = [round(float(v), 2) for v in merged["环比"].dropna()]
    allowed = mom_values + [round(float(v), 2) for v in by_store["占比"]]
    allowed += [round(float(v), 2) for v in by_category["占比"]]

    return {
        "period": period,
        "period_range": [cur_start.strftime("%Y-%m-%d"), (cur_end - timedelta(days=1)).strftime("%Y-%m-%d")],
        "days_covered": days_covered,
        "total_sales": round(total, 2),
        "total_orders": orders,
        "avg_order_value": round(total / orders, 2) if orders else 0.0,
        "store_count": int(by_store["门店"].nunique()),
        "by_store": [
            {"门店": r["门店"], "销售额": round(float(r["销售额"]), 2),
             "订单数": int(r["订单数"]), "占比": float(r["占比"]),
             "覆盖天数": int(r["覆盖天数"]), "数据完整": bool(r["数据完整"]),
             "环比": (None if r.get("环比") is None or pd.isna(r["环比"])
                      else round(float(r["环比"]), 2))}
            for r in merged.to_dict("records")
        ],
        "by_category": [
            {"品类": r["品类"], "销售额": round(float(r["销售额"]), 2),
             "订单数": int(r["订单数"]), "占比": float(r["占比"])}
            for r in by_category.to_dict("records")
        ],
        "daily": [{"日期": str(r["日期"]), "销售额": round(float(r["销售额"]), 2)}
                  for r in daily.to_dict("records")],
        "mom_available": mom_available,
        "anomalies": anomalies,
        "warnings": warnings,
        "charts": {"store_bar": chart_store, "mom_bar": chart_mom},
        "mom_percentages": mom_values,
        "allowed_percentages": sorted(set(allowed)),
    }
