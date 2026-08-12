"""京东浙商银行积存金实时价格获取模块。

提供主接口和备用接口两种方式获取实时金价。
"""

import logging
from typing import Optional
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# API 地址
PRIMARY_API = "https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice"
FALLBACK_API = "https://api.jdjygold.com/gw/generic/hj/h5/m/latestPrice"

# 浙商银行积存金产品 SKU
PRODUCT_SKU = "1961543816"

# 移动端 User-Agent
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

TIMEOUT = 20


@dataclass
class GoldPrice:
    """金价数据模型。"""

    price: float          # 当前价格（元/克）
    yesterday_price: float  # 昨日收盘价
    change_rate: str      # 涨跌幅百分比，如 "+1.10%"
    change_amt: str       # 涨跌额，如 "+10.44"
    timestamp: int        # 数据时间戳（毫秒）

    @property
    def price_change(self) -> float:
        """返回涨跌额数值。"""
        try:
            return float(self.change_amt.replace("+", ""))
        except (ValueError, AttributeError):
            return 0.0

    @property
    def change_rate_float(self) -> float:
        """返回涨跌幅百分比的数值。"""
        try:
            return float(self.change_rate.replace("%", "").replace("+", ""))
        except (ValueError, AttributeError):
            return 0.0


def _parse_response(data: dict) -> Optional[GoldPrice]:
    """解析 API 返回数据为 GoldPrice 对象。"""
    try:
        datas = data["resultData"]["datas"]
        return GoldPrice(
            price=float(datas["price"]),
            yesterday_price=float(datas["yesterdayPrice"]),
            change_rate=datas.get("upAndDownRate", "0%"),
            change_amt=datas.get("upAndDownAmt", "0"),
            timestamp=int(datas.get("time", 0)),
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.error("解析价格数据失败: %s, 原始数据: %s", e, data)
        return None


def fetch_price() -> Optional[GoldPrice]:
    """获取浙商银行积存金实时价格。

    优先使用主接口，失败时自动降级到备用接口。

    Returns:
        GoldPrice 对象，获取失败返回 None。
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": MOBILE_UA,
    }
    payload = {"reqData": {"productSku": PRODUCT_SKU}}

    # 尝试主接口
    try:
        logger.debug("正在通过主接口获取价格...")
        resp = requests.post(PRIMARY_API, json=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success") and data.get("resultCode") == 0:
            price = _parse_response(data)
            if price:
                logger.info("主接口获取成功: 价格=%s, 涨跌幅=%s", price.price, price.change_rate)
                return price
        logger.warning("主接口返回异常: %s", data.get("resultMsg", "未知"))
    except requests.RequestException as e:
        logger.warning("主接口请求失败: %s", e)

    # 降级到备用接口
    try:
        logger.debug("正在通过备用接口获取价格...")
        resp = requests.post(FALLBACK_API, json=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success") and data.get("resultCode") == 0:
            price = _parse_response(data)
            if price:
                logger.info("备用接口获取成功: 价格=%s", price.price)
                return price
        logger.warning("备用接口返回异常: %s", data.get("resultMsg", "未知"))
    except requests.RequestException as e:
        logger.error("备用接口请求失败: %s", e)

    logger.error("所有接口均获取价格失败")
    return None
