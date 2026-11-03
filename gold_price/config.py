"""配置管理模块。

读写 ~/.goldprice/config.json，管理用户可配置的各项参数。
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

# 配置在用户目录（打包成 .app 后项目目录不可写/位置可变）
CONFIG_FILE = os.path.join(os.path.expanduser("~/.goldprice"), "config.json")

DEFAULT_CONFIG = {
    "price_upper": 0.0,        # 价格上限，0 表示不监控上限
    "price_lower": 0.0,        # 价格下限，0 表示不监控下限
    "refresh_interval": 10,     # 刷新间隔（秒），最小 5 秒
    "notifications_enabled": True,  # 是否启用通知
    "alert_cooldown": 1800,     # 告警冷却时间（秒），默认 30 分钟，避免频繁弹窗
    "holding_grams": 0.0,       # 持仓克重（克，4位小数），0 表示未设置
    "holding_cost": 0.0,        # 成本均价（元/克，2位小数），0 表示未设置
    "display_mode": "full",     # 显示模式: full(完整) / compact(紧凑) / minimal(极简) / icon(仅图标)
}


@dataclass
class AppConfig:
    """应用配置。"""

    price_upper: float = 0.0
    price_lower: float = 0.0
    refresh_interval: int = 10
    notifications_enabled: bool = True
    alert_cooldown: int = 1800
    holding_grams: float = 0.0
    holding_cost: float = 0.0
    display_mode: str = "full"

    def validate(self) -> dict[str, str]:
        """校验配置值，返回错误字典（字段名 -> 错误信息）。"""
        errors = {}
        if self.price_upper < 0:
            errors["price_upper"] = "价格上限不能为负数"
        if self.price_lower < 0:
            errors["price_lower"] = "价格下限不能为负数"
        if self.price_upper > 0 and self.price_lower > 0 and self.price_upper <= self.price_lower:
            errors["price_lower"] = "价格上限必须大于下限"
        if self.refresh_interval < 5:
            errors["refresh_interval"] = "刷新间隔不能小于 5 秒"
        if self.alert_cooldown < 0:
            errors["alert_cooldown"] = "冷却时间不能为负数"
        if self.holding_grams < 0:
            errors["holding_grams"] = "持仓克重不能为负数"
        if self.holding_cost < 0:
            errors["holding_cost"] = "成本均价不能为负数"
        if self.display_mode not in ("full", "compact", "minimal", "icon"):
            errors["display_mode"] = "未知显示模式"
        return errors

    @property
    def is_valid(self) -> bool:
        return len(self.validate()) == 0


def load_config() -> AppConfig:
    """加载配置文件，不存在时使用默认值创建。"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)

    if not os.path.exists(CONFIG_FILE):
        save_config(AppConfig())
        return AppConfig()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 合并默认值，确保新字段有默认值
        merged = {**DEFAULT_CONFIG, **data}
        return AppConfig(**{k: merged[k] for k in DEFAULT_CONFIG})
    except (json.JSONDecodeError, TypeError) as e:
        # 文件损坏，重建
        print(f"配置文件损坏 ({e})，已重置为默认值")
        save_config(AppConfig())
        return AppConfig()


def save_config(config: AppConfig) -> None:
    """保存配置到文件。"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(config), f, indent=2, ensure_ascii=False)
