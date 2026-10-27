"""第 8 章配套 · 造一份自测用的假数据

企业数据没到之前，用它把 5—8 章全部跑通。
故意设计了几种"坑"，正好对应测试用例表里的边界与异常项：
  - 华东区一家门店 W44 环比跌破 -15%（触发异常门店识别）
  - 一家门店在 W44 只有 1 天数据（对应 T05：数据周期不足）
  - 一家门店全周期销售额偏低（对应占比极小的情况）
"""
import os
import random

import pandas as pd
from docx import Document

random.seed(20261027)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

STORES = [
    ("深圳南山店", "华南区", 1.00),
    ("深圳福田店", "华南区", 0.82),
    ("广州天河店", "华南区", 0.75),
    ("上海静安店", "华东区", 0.90),
    ("上海浦东店", "华东区", 0.55),   # W44 故意做下滑
    ("北京朝阳店", "华北区", 0.68),
]
CATEGORIES = ["生鲜", "日用百货", "饮料", "休闲零食"]

# 2026-W41 ~ W44 的周一
WEEKS = ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"]


def build_sales() -> pd.DataFrame:
    rows = []
    for week_idx, week_start in enumerate(WEEKS):
        start = pd.Timestamp(week_start)
        # 上海浦东店在 W44（最后一个周期）明显下滑，其余门店小幅波动
        for store, region, base in STORES:
            if store == "上海浦东店" and week_idx == len(WEEKS) - 1:
                factor = 0.62
            elif store == "上海浦东店" and week_idx == len(WEEKS) - 2:
                factor = 0.98
            else:
                factor = 1 + random.uniform(-0.06, 0.08)

            # 广州天河店在最后一个周期只有 1 天数据（T05 边界）
            if store == "广州天河店" and week_idx == len(WEEKS) - 1:
                days = [start + pd.Timedelta(days=2)]
            else:
                days = [start + pd.Timedelta(days=d) for d in range(7)]

            for day in days:
                for category in CATEGORIES:
                    share = {"生鲜": 0.42, "日用百货": 0.24, "饮料": 0.19, "休闲零食": 0.15}[category]
                    amount = 42000 * base * factor * share * random.uniform(0.85, 1.15)
                    orders = max(1, int(amount / random.uniform(45, 80)))
                    rows.append({
                        "日期": day.strftime("%Y-%m-%d"),
                        "门店": store,
                        "品类": category,
                        "销售额": round(amount, 2),
                        "订单数": orders,
                        "区域": region,
                    })
    return pd.DataFrame(rows)


MINUTES = """2026 年第 44 周经营周会纪要

时间：2026 年 10 月 30 日 14:00—15:10
参会：总经理、各区域负责人、运营部、财务部

一、整体经营情况
运营部汇报：本周整体销售额与上周基本持平，生鲜品类仍是最大贡献项。华南区客流稳定，周末表现好于工作日。

二、区域问题反馈
华东区负责人说明：上海浦东店本周销售额明显下滑，主要原因是门店所在商场自 10 月 28 日起进行外立面改造，主入口封闭，客流下降约三成；同时店内两名店员离职，排班不足，晚间时段收银台只开一个。
华南区负责人说明：广州天河店本周仅上报一天数据，原因是门店 POS 系统升级导致数据未同步，已于周五晚完成补录，本周数据不完整，不建议据此做环比判断。
华北区负责人说明：北京朝阳店本周开展会员日活动，销售小幅上升，活动效果需再观察一周。

三、下周安排
1. 华东区：跟进商场改造工期，申请在侧门增设引导标识，并协调临聘人员补足晚间排班。
2. 运营部：把广州天河店的 POS 数据补录情况写进周报数据说明，避免误读。
3. 财务部：本周客单价口径保持不变，暂不调整。
4. 全公司：下周继续按现有模板出周报，重点关注异常门店的整改反馈。

四、其他
下次周会定于 11 月 6 日 14:00，各区域需提前一天提交整改说明。
"""


def build_minutes(path: str) -> None:
    doc = Document()
    doc.add_heading("2026 年第 44 周经营周会纪要", level=1)
    for block in MINUTES.split("\n\n")[1:]:
        for line in block.strip().split("\n"):
            doc.add_paragraph(line.strip())
    doc.save(path)


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    sales_path = os.path.join(DATA_DIR, "sample_sales.xlsx")
    minutes_path = os.path.join(DATA_DIR, "sample_minutes.docx")

    df = build_sales()
    df.to_excel(sales_path, index=False)
    build_minutes(minutes_path)

    print(f"已生成 {sales_path}：{len(df)} 行，{df['日期'].min()} ~ {df['日期'].max()}")
    print(f"已生成 {minutes_path}：{len(MINUTES)} 字")
    print("列名刻意用了业务口语（销售额/门店/品类/订单数/区域），可直接被 COLUMN_MAP 识别")


if __name__ == "__main__":
    main()
