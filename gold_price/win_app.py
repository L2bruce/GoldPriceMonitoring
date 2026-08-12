"""Windows 托盘应用主模块（pystray 实现）。

功能与 macOS 菜单栏版（app.py）完全对齐：
- 托盘图标 + 悬浮提示实时金价
- 走势图 / 我的持仓 / 设置 / 退出
- 共享 fetcher / storage / alerter / chart / platform_ui 模块

注意：本模块只在 Windows 上运行，故不 import rumps（其依赖 pyobjc
在 Windows 不可安装）。菜单文字不含 emoji（Windows 菜单渲染不稳定）。
"""

import logging
import threading
from typing import Optional

import pystray
from PIL import Image, ImageDraw, ImageFont

from .fetcher import fetch_price, GoldPrice
from .config import load_config, save_config, AppConfig
from .storage import add_price_record, get_recent_days_records
from .alerter import check_price_alert
from .chart import launch_chart, kill_chart, write_chart_data, is_chart_running
from .platform_ui import show_input_dialog, show_alert

logger = logging.getLogger(__name__)

APP_NAME = "金价监控"
REFRESH_MIN = 5
REFRESH_MAX = 300
DEFAULT_INTERVAL = 10  # 默认刷新间隔（秒）


def _make_tray_image() -> Image.Image:
    """绘制托盘图标：金色圆底 + 白色大写 G（64x64，系统缩放）。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 金色圆形底（#f0b233，与 macOS 图标同色系）
    draw.ellipse((2, 2, size - 2, size - 2), fill=(240, 178, 51, 255))
    # 白色大写 G（优先粗体，Windows 自带字体）
    try:
        font = ImageFont.truetype("arialbd.ttf", 42)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", 42)
        except OSError:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "G", font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(
        ((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]),
        "G",
        fill=(255, 255, 255, 255),
        font=font,
    )
    return img


class GoldPriceTray:
    """Windows 系统托盘应用。"""

    def __init__(self):
        self._config: AppConfig = load_config()
        self._current_price: Optional[GoldPrice] = None
        self._chart_open = False
        self._fetch_error_count = 0
        self._chart_last_key = None  # 图表数据指纹（最后一条记录的 time+price）
        self._timer = None
        self._stopped = False
        self._running = False  # 托盘菜单是否已可见（控制 update_menu 时机）

        # ── 菜单 ──
        # 纯展示项 action=None → 禁用态（灰色），Windows 托盘惯例
        self._price_item = pystray.MenuItem("当前: 加载中...", None)

        self._chart_item = pystray.MenuItem("打开走势图", self._toggle_chart)

        self._holding_grams = pystray.MenuItem("持仓克重: 未设置", None)
        self._holding_cost = pystray.MenuItem("成本均价: 未设置", None)
        self._holding_profit = pystray.MenuItem("持仓收益: --", None)
        self._holding_set = pystray.MenuItem("设置持仓", self._set_holding)
        holding_menu = pystray.Menu(
            self._holding_grams,
            self._holding_cost,
            self._holding_profit,
            pystray.Menu.SEPARATOR,
            self._holding_set,
        )

        self._settings_upper = pystray.MenuItem("价格上限: 不限", self._set_price_upper)
        self._settings_lower = pystray.MenuItem("价格下限: 不限", self._set_price_lower)
        self._settings_interval = pystray.MenuItem(
            f"刷新间隔: {DEFAULT_INTERVAL}秒", self._set_refresh_interval
        )
        self._settings_notify = pystray.MenuItem("通知: 开", self._toggle_notifications)
        settings_menu = pystray.Menu(
            self._settings_upper,
            self._settings_lower,
            self._settings_interval,
            pystray.Menu.SEPARATOR,
            self._settings_notify,
        )

        self._quit_item = pystray.MenuItem("退出", self._quit)

        self._icon = pystray.Icon(
            APP_NAME,
            _make_tray_image(),
            "⏳ 加载中...",
            pystray.Menu(
                self._price_item,
                pystray.Menu.SEPARATOR,
                self._chart_item,
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("我的持仓", holding_menu),
                pystray.MenuItem("设置", settings_menu),
                pystray.Menu.SEPARATOR,
                self._quit_item,
            ),
        )

        self._update_settings_menu()
        self._update_holding_menu()

        # 定时器
        self._setup_timer()
        # 首次拉取
        self._fetch_and_update()

    # ── 生命周期 ──

    def run(self):
        """启动托盘事件循环（阻塞）。"""
        self._running = True
        self._icon.run()

    def _quit(self, icon=None, item=None):
        self._stopped = True
        if self._timer:
            self._timer.cancel()
        self._icon.stop()

    # ── 定时器 ──

    def _setup_timer(self):
        interval = max(REFRESH_MIN, min(self._config.refresh_interval, REFRESH_MAX))
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        try:
            self._fetch_and_update()
        finally:
            if not self._stopped:
                self._setup_timer()

    def _refresh_menu(self):
        """托盘菜单已可见时刷新（文本变化后调用）。"""
        if self._running:
            self._icon.update_menu()

    # ── 设置菜单 ──

    def _update_settings_menu(self):
        cfg = self._config
        self._settings_upper.text = (
            f"价格上限: ¥{cfg.price_upper:.1f}" if cfg.price_upper > 0 else "价格上限: 不限"
        )
        self._settings_lower.text = (
            f"价格下限: ¥{cfg.price_lower:.1f}" if cfg.price_lower > 0 else "价格下限: 不限"
        )
        self._settings_interval.text = f"刷新间隔: {cfg.refresh_interval}秒"
        self._settings_notify.text = (
            "通知: 开" if cfg.notifications_enabled else "通知: 关"
        )
        self._refresh_menu()

    # ── 我的持仓 ──

    def _update_holding_menu(self):
        """刷新持仓菜单显示（克重、成本、收益随当前价实时变化）。"""
        cfg = self._config
        if cfg.holding_grams > 0 and cfg.holding_cost > 0:
            self._holding_grams.text = f"持仓克重: {cfg.holding_grams:.4f} 克"
            self._holding_cost.text = f"成本均价: ¥{cfg.holding_cost:.2f}"
            if self._current_price:
                p = self._current_price.price
                profit = (p - cfg.holding_cost) * cfg.holding_grams
                rate = (p - cfg.holding_cost) / cfg.holding_cost * 100
                arrow = "↑" if profit >= 0 else "↓"
                self._holding_profit.text = (
                    f"持仓收益: {profit:+.2f} 元 {arrow} ({rate:+.2f}%)"
                )
            else:
                self._holding_profit.text = "持仓收益: 加载中..."
        else:
            self._holding_grams.text = "持仓克重: 未设置"
            self._holding_cost.text = "成本均价: 未设置"
            self._holding_profit.text = "持仓收益: --"
        self._refresh_menu()

    def _set_holding(self, icon=None, item=None):
        """设置持仓：两次对话框输入克重与成本，全部成功才保存。"""
        default_g = (
            str(self._config.holding_grams) if self._config.holding_grams > 0 else ""
        )
        result = show_input_dialog(
            title="设置持仓",
            message="请输入积存金持仓克重（克，最多4位小数，留空清除）：",
            default_answer=default_g,
        )
        if result is None:
            return  # 用户取消
        try:
            grams = float(result.strip()) if result.strip() else 0.0
            if grams < 0:
                show_alert("错误", "持仓克重不能为负数")
                return
        except ValueError:
            show_alert("错误", "请输入有效的数字")
            return

        default_c = (
            str(self._config.holding_cost) if self._config.holding_cost > 0 else ""
        )
        result = show_input_dialog(
            title="设置持仓",
            message="请输入成本均价（元/克，2位小数）：",
            default_answer=default_c,
        )
        if result is None:
            return  # 用户取消，不保存
        try:
            cost = float(result.strip()) if result.strip() else 0.0
            if cost < 0:
                show_alert("错误", "成本均价不能为负数")
                return
        except ValueError:
            show_alert("错误", "请输入有效的数字")
            return

        self._config.holding_grams = grams
        self._config.holding_cost = cost
        save_config(self._config)
        self._update_holding_menu()

    # ── 价格获取 ──

    def _fetch_and_update(self):
        try:
            price = fetch_price()
            if price is None:
                self._fetch_error_count += 1
                if self._fetch_error_count >= 3:
                    self._icon.title = "⚠️ 网络异常"
                return

            self._fetch_error_count = 0
            self._current_price = price
            self._update_title(price)

            add_price_record(
                price=price.price,
                change_rate=price.change_rate,
                change_amt=price.change_amt,
                timestamp=price.timestamp,
            )

            # 更新菜单价格展示
            arrow = "↑" if price.price_change >= 0 else "↓"
            self._price_item.text = (
                f"当前: ¥{price.price:.2f}  {arrow} {price.change_rate}"
            )
            # 持仓收益随价格实时刷新
            self._update_holding_menu()

            # 告警检查
            check_price_alert(
                price=price.price,
                price_upper=self._config.price_upper,
                price_lower=self._config.price_lower,
                change_rate=price.change_rate,
                cooldown_seconds=self._config.alert_cooldown,
                enabled=self._config.notifications_enabled,
            )

            # 更新走势图
            if self._chart_open:
                if is_chart_running():
                    self._update_chart()
                else:
                    self._chart_open = False
                    self._chart_item.text = "打开走势图"
                    self._refresh_menu()

            self._refresh_menu()

        except Exception:
            logger.exception("更新价格异常")

    def _update_title(self, price: GoldPrice):
        arrow = "↑" if price.price_change >= 0 else "↓"
        self._icon.title = f"¥{price.price:.2f} {arrow}{price.change_rate}"

    # ── 走势图 ──

    def _toggle_chart(self, icon=None, item=None):
        if self._chart_open:
            kill_chart()
            self._chart_open = False
            self._chart_item.text = "打开走势图"
        else:
            try:
                # 先写入最新数据（30 天全量，覆盖 1/3/7/30 天所有视图），再启动图表服务
                days_data = get_recent_days_records(days=30)
                yesterday = (
                    self._current_price.yesterday_price if self._current_price else 0
                )
                write_chart_data(days_data, yesterday)
                self._chart_last_key = self._chart_data_key(days_data)
                launch_chart()
                self._chart_open = True
                self._chart_item.text = "走势图表 (已打开)"
            except Exception as e:
                logger.exception("打开走势图失败")
                show_alert("错误", f"打开走势图失败:\n{e}")
        self._refresh_menu()

    @staticmethod
    def _chart_data_key(days_data: list):
        """返回图表数据的最新记录指纹，用于判断数据是否变化。"""
        if days_data and days_data[0]["records"]:
            last_rec = days_data[0]["records"][-1]
            return (last_rec["time"], last_rec["price"])
        return None

    def _update_chart(self):
        if not self._chart_open or not is_chart_running():
            return
        try:
            days_data = get_recent_days_records(days=30)
            # 数据没变化就不重写文件（30 天全量约 5MB，每次轮询重写太浪费）
            key = self._chart_data_key(days_data)
            if key == self._chart_last_key:
                return
            self._chart_last_key = key
            yesterday = (
                self._current_price.yesterday_price if self._current_price else 0
            )
            write_chart_data(days_data, yesterday)
        except Exception:
            logger.exception("走势图更新异常")

    # ── 设置 ──

    def _set_price_upper(self, icon=None, item=None):
        default_val = str(self._config.price_upper) if self._config.price_upper > 0 else ""
        result = show_input_dialog(
            title="价格上限",
            message="设置价格上限（元/克），超过此价格将弹窗提醒：",
            default_answer=default_val,
        )
        if result is None:
            return  # 用户取消
        try:
            val = float(result.strip()) if result.strip() else 0.0
            if val < 0:
                show_alert("错误", "价格上限不能为负数")
                return
            self._config.price_upper = val
            save_config(self._config)
            self._update_settings_menu()
        except ValueError:
            show_alert("错误", "请输入有效的数字")

    def _set_price_lower(self, icon=None, item=None):
        default_val = str(self._config.price_lower) if self._config.price_lower > 0 else ""
        result = show_input_dialog(
            title="价格下限",
            message="设置价格下限（元/克），低于此价格将弹窗提醒：",
            default_answer=default_val,
        )
        if result is None:
            return
        try:
            val = float(result.strip()) if result.strip() else 0.0
            if val < 0:
                show_alert("错误", "价格下限不能为负数")
                return
            self._config.price_lower = val
            save_config(self._config)
            self._update_settings_menu()
        except ValueError:
            show_alert("错误", "请输入有效的数字")

    def _set_refresh_interval(self, icon=None, item=None):
        result = show_input_dialog(
            title="刷新间隔",
            message=f"设置数据刷新间隔（{REFRESH_MIN}-{REFRESH_MAX} 秒）：",
            default_answer=str(self._config.refresh_interval),
        )
        if result is None:
            return
        try:
            val = int(result.strip()) if result.strip() else DEFAULT_INTERVAL
            val = max(REFRESH_MIN, min(val, REFRESH_MAX))
            self._config.refresh_interval = val
            save_config(self._config)
            self._update_settings_menu()
            self._setup_timer()
        except ValueError:
            show_alert("错误", "请输入有效的整数")

    def _toggle_notifications(self, icon=None, item=None):
        self._config.notifications_enabled = not self._config.notifications_enabled
        save_config(self._config)
        self._update_settings_menu()
