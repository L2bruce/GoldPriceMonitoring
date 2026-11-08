"""价格历史数据存储模块。

以 JSON 文件存储价格历史，支持查询统计信息。
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


def clean_invalid_records(history: dict) -> dict:
    """清洗历史上因为取系统时间而导致日期与行情时间戳不匹配的伪造跨天脏数据。"""
    changed = False
    cleaned: Dict[str, list] = {}

    for key, records in history.items():
        if not isinstance(records, list):
            continue
        valid_recs = []
        for r in records:
            ts = r.get("timestamp_ms")
            if ts:
                actual_date = datetime.fromtimestamp(ts / 1000).date().isoformat()
                if actual_date != key:
                    changed = True
                    # 错位数据：归位到 actual_date（若未存在）
                    if actual_date not in cleaned:
                        cleaned[actual_date] = list(history.get(actual_date, []))
                    if not any(x.get("timestamp_ms") == ts for x in cleaned[actual_date]):
                        cleaned[actual_date].append(r)
                    continue
            valid_recs.append(r)

        if valid_recs:
            if key in cleaned:
                existing_ts = {x.get("timestamp_ms") for x in cleaned[key]}
                for r in valid_recs:
                    if r.get("timestamp_ms") not in existing_ts:
                        cleaned[key].append(r)
            else:
                cleaned[key] = valid_recs
        elif key not in cleaned:
            # 该日所有记录均错位且已迁出，丢弃该空 key
            changed = True

    # 排序各天记录
    for k in cleaned:
        cleaned[k].sort(key=lambda x: x.get("timestamp_ms", 0))

    if changed:
        save_history(cleaned)
        return cleaned
    return history


def load_history() -> dict:
    """加载价格历史数据，按日期分组。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return clean_invalid_records(data)
        return {}
    except (json.JSONDecodeError, TypeError):
        return {}


def save_history(history: dict) -> None:
    """原子化保存价格历史数据。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_file = f"{HISTORY_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, HISTORY_FILE)


def cleanup_old_dates(history: dict, keep_days: int = 30) -> dict:
    """按交易日/记录日保留最近 keep_days 天的数据，避免非交易日跳过导致的提前误删。"""
    valid_keys = []
    for key in list(history.keys()):
        try:
            date.fromisoformat(key)
            valid_keys.append(key)
        except (ValueError, TypeError):
            del history[key]

    valid_keys.sort()
    if len(valid_keys) > keep_days:
        keys_to_remove = valid_keys[:-keep_days]
        for key in keys_to_remove:
            del history[key]
    return history


def add_price_record(price: float, change_rate: str, change_amt: str, timestamp: int) -> bool:
    """添加一条价格记录到历史。

    依据行情实际成交时间戳归档到对应日期，避免休市跨天伪造数据。
    同一时间戳（休市）直接跳过，同一分钟内仅保留最新一条。

    Returns:
        bool: 是否成功追加或更新了记录。
    """
    history = load_history()
    history = cleanup_old_dates(history)

    # 时间戳是毫秒；API 返回的是本地时间（北京时间）
    dt = datetime.fromtimestamp(timestamp / 1000)
    target_date = dt.date().isoformat()

    if target_date not in history:
        history[target_date] = []

    # 休市防重：若最新记录的时间戳一致，说明行情无更新，无需重复写入
    if history[target_date]:
        last_rec = history[target_date][-1]
        if last_rec.get("timestamp_ms") == timestamp:
            logger.debug("价格时间戳无变化 (%s)，休市期间跳过记录", timestamp)
            return False

    record = {
        "time": dt.strftime("%H:%M:%S"),
        "price": price,
        "change_rate": change_rate,
        "change_amt": change_amt,
        "timestamp_ms": timestamp,
    }
    # 同一分钟去重：替换该分钟最后一条
    if history[target_date] and history[target_date][-1]["time"][:5] == dt.strftime("%H:%M"):
        history[target_date][-1] = record
    else:
        history[target_date].append(record)

    save_history(history)
    logger.debug("已记录价格: %s at %s (%s)", price, record["time"], target_date)
    return True


def get_today_records() -> List[Dict[str, Any]]:
    """获取当天的所有价格记录。"""
    history = load_history()
    # 优先取今天日期；若今天无记录（如休市），则取最近一个有记录的日期
    today_key = _today_key()
    if today_key in history and history[today_key]:
        return history[today_key]
    valid_keys = sorted([k for k, v in history.items() if v])
    if valid_keys:
        return history[valid_keys[-1]]
    return []


def get_today_stats() -> dict:
    """获取当天（或最近交易日）价格统计信息。"""
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
    """获取最近 N 个交易日的价格记录（按天分组）。

    Returns:
        [{"date": "2026-08-11", "records": [{"time": "09:30", "price": 955.0}, ...]}, ...]
        按日期降序排列（最近一天在前）。
    """
    history = load_history()
    valid_dates = sorted([k for k, v in history.items() if v], reverse=True)
    target_dates = valid_dates[:days]

    result = []
    for key in target_dates:
        result.append({"date": key, "records": history[key]})

    return result
