"""告警模块。

检测价格是否超出设定范围，通过平台原生弹窗提醒（macOS / Windows）。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

from .platform_ui import show_critical_alert

logger = logging.getLogger(__name__)

# 上次告警时间记录（用于冷却）
_last_alert_time: Dict[str, datetime] = {}


def send_notification(title: str, message: str, sound: bool = True) -> bool:
    """弹出不可忽略的模态告警弹窗（fire-and-forget）。

    macOS: display alert as critical（比 notification 更醒目，
    后者可能被通知中心静默收纳而不弹窗；自带系统提示音）。
    Windows: MessageBoxW 原生弹窗。

    Args:
        title: 对话框标题
        message: 对话框内容
        sound: 是否带提示音

    Returns:
        是否发送成功。
    """
    return show_critical_alert(title, message, sound=sound)


def check_price_alert(
    price: float,
    price_upper: float,
    price_lower: float,
    change_rate: str,
    cooldown_seconds: int = 300,
    enabled: bool = True,
) -> Optional[str]:
    """检查价格是否越界，越界则发送通知。

    Args:
        price: 当前价格
        price_upper: 价格上限（0 表示不限）
        price_lower: 价格下限（0 表示不限）
        change_rate: 涨跌幅字符串
        cooldown_seconds: 冷却时间（秒）
        enabled: 是否启用通知

    Returns:
        'upper' / 'lower' / None — 触发告警的方向，未触发返回 None。
    """
    if not enabled:
        return None

    alert_type = None

    if price_upper > 0 and price > price_upper:
        alert_type = "upper"
    elif price_lower > 0 and price < price_lower:
        alert_type = "lower"

    if alert_type is None:
        return None

    # 冷却检查
    now = datetime.now()
    last = _last_alert_time.get(alert_type)
    if last and (now - last) < timedelta(seconds=cooldown_seconds):
        logger.debug("告警冷却中: type=%s, 距上次=%ds", alert_type, (now - last).seconds)
        return None

    # 发送通知
    if alert_type == "upper":
        title = "⚠️ 金价突破上限"
        message = f"当前价格 ¥{price}/克，已超过上限 ¥{price_upper}/克（{change_rate}）"
    else:
        title = "📉 金价跌破下限"
        message = f"当前价格 ¥{price}/克，已跌破下限 ¥{price_lower}/克（{change_rate}）"

    if send_notification(title, message):
        _last_alert_time[alert_type] = now
        return alert_type

    return None
