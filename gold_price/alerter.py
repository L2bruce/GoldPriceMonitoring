"""告警模块。

检测价格是否超出设定范围，通过 macOS 原生通知弹窗提醒。
"""

import subprocess
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# 上次告警时间记录（用于冷却）
_last_alert_time: Dict[str, datetime] = {}


def reset_alert_cooldown(alert_type: Optional[str] = None) -> None:
    """重置告警冷却时间。

    Args:
        alert_type: 'upper' / 'lower' / None（None 表示重置全部）。
    """
    global _last_alert_time
    if alert_type:
        _last_alert_time.pop(alert_type, None)
    else:
        _last_alert_time.clear()
    logger.debug("告警冷却已重置: %s", alert_type or "全部")


def _escape_applescript(s) -> str:
    """转义 AppleScript 字符串中的反斜杠和双引号。"""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def send_notification(title: str, message: str, sound: bool = True) -> bool:
    """使用 macOS display alert 弹出不可忽略的模态弹窗。

    display alert 比 display notification 更醒目，
    后者可能被通知中心静默收纳而不弹窗。
    display alert as critical 自带系统提示音。

    Args:
        title: 对话框标题
        message: 对话框内容
        sound: 保留参数，display alert as critical 自带提示音

    Returns:
        是否发送成功。
    """
    safe_title = _escape_applescript(title)
    safe_msg = _escape_applescript(message)
    # display alert 始终以模态弹窗显示，无法被忽略
    # giving up after 30 秒后自动消失
    script = (
        f'display alert "{safe_title}" message "{safe_msg}" '
        f'as critical giving up after 30'
    )
    try:
        # 使用 Popen 不等待对话框关闭，fire-and-forget
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("告警弹窗已发送: %s - %s", title, message)
        return True
    except Exception as e:
        logger.error("告警弹窗失败: %s", e)
        return False


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
    cooldown_str = (
        f"{cooldown_seconds // 60}分钟"
        if cooldown_seconds >= 60
        else f"{cooldown_seconds}秒"
    )
    if alert_type == "upper":
        title = "⚠️ 金价突破上限"
        message = (
            f"当前价格 ¥{price}/克，已超过上限 ¥{price_upper}/克（{change_rate}）。\n"
            f"（{cooldown_str}内不再重复提示）"
        )
    else:
        title = "📉 金价跌破下限"
        message = (
            f"当前价格 ¥{price}/克，已跌破下限 ¥{price_lower}/克（{change_rate}）。\n"
            f"（{cooldown_str}内不再重复提示）"
        )

    if send_notification(title, message):
        _last_alert_time[alert_type] = now
        return alert_type

    return None
