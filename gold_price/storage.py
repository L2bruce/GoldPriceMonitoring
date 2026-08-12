"""价格历史数据存储模块。

以 JSON 文件存储当天价格历史，支持查询统计信息。
"""

import json
import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# 数据目录在用户目录（打包成 .app 后项目目录不可写/位置可变）
DATA_DIR = os.path.expanduser("~/.goldprice")
HISTORY_FILE = os.path.join(DATA_DIR, "price_history.json")


def _today_key() -> str:
    """返回当天日期字符串，如 '2026-08-11'。"""
    return date.today().isoformat()


def load_history() -> dict:
    """加载价格历史数据，按日期分组。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def save_history(history: dict) -> None:
    """保存价格历史数据。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def cleanup_old_dates(history: dict, keep_days: int = 30) -> dict:
    """清理超过 keep_days 天的旧数据。"""
    today = date.today()
    keys_to_remove = []
    for key in history:
        try:
            d = date.fromisoformat(key)
            if (today - d).days > keep_days:
                keys_to_remove.append(key)
        except (ValueError, TypeError):
            keys_to_remove.append(key)
    for key in keys_to_remove:
        del history[key]
    return history


def add_price_record(price: float, change_rate: str, change_amt: str, timestamp: int) -> None:
    """添加一条价格记录到当天历史。

    API 时间戳为北京时间（本地时间），直接按本地时间转换。
    同一分钟内去重：只保留该分钟最新的一条，避免数据膨胀
    （30 天保留期下，每分钟一条约 4 万条记录）。
    """
    history = load_history()
    history = cleanup_old_dates(history)

    today = _today_key()
    if today not in history:
        history[today] = []

    # 时间戳是毫秒；API 返回的是本地时间（北京时间），不要转 UTC
    dt = datetime.fromtimestamp(timestamp / 1000)

    record = {
        "time": dt.strftime("%H:%M:%S"),
        "price": price,
        "change_rate": change_rate,
        "change_amt": change_amt,
        "timestamp_ms": timestamp,
    }
    # 同一分钟去重：替换该分钟最后一条
    if history[today] and history[today][-1]["time"][:5] == dt.strftime("%H:%M"):
        history[today][-1] = record
    else:
        history[today].append(record)
    save_history(history)
    logger.debug("已记录价格: %s at %s", price, record["time"])


def get_today_records() -> List[Dict[str, Any]]:
    """获取当天的所有价格记录。"""
    history = load_history()
    return history.get(_today_key(), [])


def get_today_stats() -> dict:
    """获取当天价格统计信息。"""
    records = get_today_records()
    if not records:
        return {
            "open": None,
            "high": None,
            "low": None,
            "latest": None,
            "count": 0,
        }

    prices = [r["price"] for r in records]
    return {
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "latest": prices[-1],
        "count": len(prices),
        "first_time": records[0]["time"],
        "latest_time": records[-1]["time"],
    }


def get_recent_days_records(days: int = 3) -> List[Dict[str, Any]]:
    """获取最近 N 天的价格记录（按天分组）。

    Returns:
        [{"date": "2026-08-11", "records": [{"time": "09:30", "price": 955.0}, ...]}, ...]
        按日期降序排列（最近一天在前）。
    """
    history = load_history()
    today = date.today()
    result = []

    for i in range(days):
        d = today - timedelta(days=i)
        key = d.isoformat()
        if key in history and history[key]:
            result.append({"date": key, "records": history[key]})

    return result
