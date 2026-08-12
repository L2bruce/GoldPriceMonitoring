"""一次性初始化：导入最近 30 天金价历史估算数据。

积存金价格与上海金/沪金价格挂钩，本脚本抓取沪金期货主力合约（AU0）的
日线（开/高/低/收），为每个交易日生成 4 个估算数据点，写入价格历史，
让走势图的 3天/7天/30天 视图立即有内容。

- 估算记录带 "source": "estimate" 标记
- 今天（以及任何含真实记录的日期）不会被覆盖，真实数据由 app 持续记录
- 可重复运行（幂等）

Usage:
    python init_history.py
"""

import json
import re
import requests
from datetime import datetime, date, timedelta

from gold_price.storage import load_history, save_history

SINA_KLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_=/InnerFuturesNewService.getDailyKLine?symbol=AU0"
)
DAYS = 30  # 导入最近 30 个交易日的日线

# 每个交易日的估算数据点（时间, 价格构造）
POINTS = [
    ("09:30", "o"),          # 开盘
    ("11:00", "mid1"),       # 开盘与收盘之间
    ("13:30", "mid2"),       # 最高与最低之间
    ("15:00", "c"),          # 收盘
]


def fetch_daily_kline():
    """抓取沪金期货主力合约全部日线（新浪财经）。"""
    resp = requests.get(SINA_KLINE_URL, timeout=15)
    resp.raise_for_status()
    m = re.search(r"\((\[.*\])\)\s*;?\s*$", resp.text, re.S)
    if not m:
        raise RuntimeError("无法解析新浪返回数据")
    rows = json.loads(m.group(1))
    if not rows:
        raise RuntimeError("新浪返回数据为空")
    return rows


def _point_price(row, kind):
    o, h, l, c = (float(row[k]) for k in ("o", "h", "l", "c"))
    if kind == "o":
        return o
    if kind == "c":
        return c
    if kind == "mid1":
        return round((o + c) / 2, 2)
    return round((h + l) / 2, 2)  # mid2


def build_day_records(row, prev_close):
    """由日线生成当天的估算记录列表。"""
    today = date.today()
    d = date.fromisoformat(row["d"])
    records = []
    for time_str, kind in POINTS:
        price = _point_price(row, kind)
        dt = datetime(d.year, d.month, d.day,
                      int(time_str[:2]), int(time_str[3:5]))
        if prev_close:
            change_amt = price - prev_close
            change_rate = f"{change_amt / prev_close * 100:+.2f}%"
        else:
            change_amt, change_rate = 0.0, "0.00%"
        records.append({
            "time": time_str + ":00",
            "price": round(price, 2),
            "change_rate": change_rate,
            "change_amt": f"{change_amt:+.2f}",
            "timestamp_ms": int(dt.timestamp() * 1000),
            "source": "estimate",
        })
    return records


def main():
    rows = fetch_daily_kline()
    history = load_history()

    # 最近 DAYS 个交易日，升序
    klines = rows[-DAYS:]
    prev_close = None
    imported_days = 0
    skipped_days = 0

    for row in klines:
        day_key = row["d"]
        if day_key == date.today().isoformat():
            continue  # 今天已有真实数据，不覆盖

        records = build_day_records(row, prev_close)
        prev_close = float(row["c"])

        # 幂等：该天已有非估算数据则跳过
        existing = history.get(day_key, [])
        if any(r.get("source") != "estimate" for r in existing):
            skipped_days += 1
            continue

        history[day_key] = records
        imported_days += 1

    save_history(history)
    print(f"导入完成: {imported_days} 天估算数据 (跳过 {skipped_days} 天含真实数据的天)")
    print(f"历史现在共 {len(history)} 天: {min(history)} ~ {max(history)}")


if __name__ == "__main__":
    main()
