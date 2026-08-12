"""macOS 菜单栏应用主模块。

基于 rumps 实现菜单栏价格显示、走势图打开、设置管理等功能。
"""

import subprocess
import logging
from typing import Optional

import rumps

from .fetcher import fetch_price, GoldPrice
from .config import load_config, save_config, AppConfig
from .storage import add_price_record, get_today_records, get_recent_days_records
from .alerter import check_price_alert
from .chart import launch_chart, kill_chart, write_chart_data, is_chart_running

logger = logging.getLogger(__name__)

APP_NAME = "金价监控"
REFRESH_MIN = 5
REFRESH_MAX = 300
DEFAULT_INTERVAL = 10  # 默认刷新间隔（秒）


def _osascript_dialog(title: str, message: str, default_answer: str = "") -> Optional[str]:
    """使用 AppleScript 弹出原生文本输入对话框。

    Returns:
        用户输入的文本，取消返回 None。
    """
    script = (
        f'display dialog "{message}" '
        f'with title "{title}" '
        f'default answer "{default_answer}" '
        f'buttons {{"取消", "确定"}} '
        f'default button "确定"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            # 用户点了取消
            if "User canceled" in result.stderr or result.returncode == 1:
                return None
            logger.warning("对话框异常: %s", result.stderr)
            return None
        # 输出格式: "button returned:确定, text returned:xxx"
        output = result.stdout.strip()
        if "text returned:" in output:
            return output.split("text returned:")[1].strip()
        return None
    except Exception as e:
        logger.error("osascript 对话框失败: %s", e)
        return None


def _osascript_alert(title: str, message: str):
    """使用 AppleScript 弹出提示框。"""
    script = f'display dialog "{message}" with title "{title}" buttons {{"确定"}} default button "确定"'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)


class _BrightMenuItem(rumps.MenuItem):
    """纯展示菜单项：文字强制黑色，不受系统禁用态渲染变暗影响。

    rumps 对 callback 为 None 的项会 setAction_(None)，macOS 渲染偏灰。
    展示项不参与点击：恢复 enabled + 用 AttributedString 显式上色。
    rumps 更新 title（setTitle_）会清空 attributed title，故 setter 里重设。
    """

    def __init__(self, title):
        super().__init__(title)
        try:
            self._menuitem.setEnabled_(True)
        except AttributeError:
            pass
        self._apply_bright()

    @property
    def title(self):
        return self._menuitem.title()

    @title.setter
    def title(self, value):
        self._menuitem.setTitle_(str(value))
        self._apply_bright()

    def _apply_bright(self):
        try:
            from AppKit import (
                NSAttributedString,
                NSColor,
                NSForegroundColorAttributeName,
            )
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.0, 0.0, 0.0, 1.0  # 纯黑（浅色菜单背景上清晰）
            )
            attr = NSAttributedString.alloc().initWithString_attributes_(
                self._menuitem.title(), {NSForegroundColorAttributeName: color}
            )
            self._menuitem.setAttributedTitle_(attr)
        except Exception:
            pass  # 上色失败退回系统默认


def _make_display_item(title: str) -> rumps.MenuItem:
    """创建纯展示菜单项（亮色文字）。"""
    return _BrightMenuItem(title)


class GoldPriceApp(rumps.App):
    """金价监控菜单栏应用。"""

    def __init__(self):
        super().__init__(
            name=APP_NAME,
            title="⏳ 加载中...",
            quit_button="退出",
        )
        self._config: AppConfig = load_config()
        self._current_price: Optional[GoldPrice] = None
        self._chart_open = False
        self._fetch_error_count = 0
        self._chart_last_key = None  # 图表数据指纹（最后一条记录的 time+price）

        # ── 菜单 ──
        self._price_display = _make_display_item("")

        self._chart_menu = rumps.MenuItem("📈 打开走势图", callback=self._toggle_chart)

        self._settings_upper = rumps.MenuItem(
            "价格上限: 不限", callback=self._set_price_upper
        )
        self._settings_lower = rumps.MenuItem(
            "价格下限: 不限", callback=self._set_price_lower
        )
        self._settings_interval = rumps.MenuItem(
            f"刷新间隔: {DEFAULT_INTERVAL}秒", callback=self._set_refresh_interval
        )
        self._settings_notify = rumps.MenuItem(
            "🔔 通知: 开", callback=self._toggle_notifications
        )

        settings_menu = rumps.MenuItem("⚙️ 设置")
        settings_menu.add(self._settings_upper)
        settings_menu.add(self._settings_lower)
        settings_menu.add(self._settings_interval)
        settings_menu.add(rumps.separator)
        settings_menu.add(self._settings_notify)

        # ── 我的持仓 ──
        self._holding_grams = _make_display_item("持仓克重: 未设置")
        self._holding_cost = _make_display_item("成本均价: 未设置")
        self._holding_profit = _make_display_item("持仓收益: --")
        self._holding_set = rumps.MenuItem("✏️ 设置持仓", callback=self._set_holding)

        holding_menu = rumps.MenuItem("📦 我的持仓")
        holding_menu.add(self._holding_grams)
        holding_menu.add(self._holding_cost)
        holding_menu.add(self._holding_profit)
        holding_menu.add(rumps.separator)
        holding_menu.add(self._holding_set)

        self.menu = [
            self._price_display,
            rumps.separator,
            self._chart_menu,
            holding_menu,
            settings_menu,
            rumps.separator,
            None,  # quit_button
        ]

        self._update_settings_menu()
        self._update_holding_menu()

        # 定时器
        self._setup_timer()
        # 首次拉取
        self._fetch_and_update(None)

    def _setup_timer(self):
        interval = max(REFRESH_MIN, min(self._config.refresh_interval, REFRESH_MAX))
        if hasattr(self, '_timer'):
            try:
                self._timer.stop()
            except Exception:
                pass
        self._timer = rumps.Timer(self._fetch_and_update, interval)
        self._timer.start()

    def _update_settings_menu(self):
        cfg = self._config
        self._settings_upper.title = (
            f"价格上限: ¥{cfg.price_upper:.1f}" if cfg.price_upper > 0 else "价格上限: 不限"
        )
        self._settings_lower.title = (
            f"价格下限: ¥{cfg.price_lower:.1f}" if cfg.price_lower > 0 else "价格下限: 不限"
        )
        self._settings_interval.title = f"刷新间隔: {cfg.refresh_interval}秒"
        self._settings_notify.title = "🔔 通知: 开" if cfg.notifications_enabled else "🔕 通知: 关"

    # ── 我的持仓 ──

    def _update_holding_menu(self):
        """刷新持仓菜单显示（克重、成本、收益随当前价实时变化）。"""
        cfg = self._config
        if cfg.holding_grams > 0 and cfg.holding_cost > 0:
            self._holding_grams.title = f"持仓克重: {cfg.holding_grams:.4f} 克"
            self._holding_cost.title = f"成本均价: ¥{cfg.holding_cost:.2f}"
            if self._current_price:
                p = self._current_price.price
                profit = (p - cfg.holding_cost) * cfg.holding_grams
                rate = (p - cfg.holding_cost) / cfg.holding_cost * 100
                arrow = "↑" if profit >= 0 else "↓"
                self._holding_profit.title = (
                    f"持仓收益: {profit:+.2f} 元 {arrow} ({rate:+.2f}%)"
                )
            else:
                self._holding_profit.title = "持仓收益: 加载中..."
        else:
            self._holding_grams.title = "持仓克重: 未设置"
            self._holding_cost.title = "成本均价: 未设置"
            self._holding_profit.title = "持仓收益: --"

    def _set_holding(self, sender):
        """设置持仓：两次对话框输入克重与成本，全部成功才保存。"""
        default_g = (
            str(self._config.holding_grams) if self._config.holding_grams > 0 else ""
        )
        result = _osascript_dialog(
            title="设置持仓",
            message="请输入积存金持仓克重（克，最多4位小数，留空清除）：",
            default_answer=default_g,
        )
        if result is None:
            return  # 用户取消
        try:
            grams = float(result.strip()) if result.strip() else 0.0
            if grams < 0:
                _osascript_alert("错误", "持仓克重不能为负数")
                return
        except ValueError:
            _osascript_alert("错误", "请输入有效的数字")
            return

        default_c = (
            str(self._config.holding_cost) if self._config.holding_cost > 0 else ""
        )
        result = _osascript_dialog(
            title="设置持仓",
            message="请输入成本均价（元/克，2位小数）：",
            default_answer=default_c,
        )
        if result is None:
            return  # 用户取消，不保存
        try:
            cost = float(result.strip()) if result.strip() else 0.0
            if cost < 0:
                _osascript_alert("错误", "成本均价不能为负数")
                return
        except ValueError:
            _osascript_alert("错误", "请输入有效的数字")
            return

        self._config.holding_grams = grams
        self._config.holding_cost = cost
        save_config(self._config)
        self._update_holding_menu()

    # ── 价格获取 ──

    def _fetch_and_update(self, sender):
        try:
            price = fetch_price()
            if price is None:
                self._fetch_error_count += 1
                if self._fetch_error_count >= 3:
                    self.title = "⚠️ 网络异常"
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
            self._price_display.title = (
                f"💰 当前: ¥{price.price:.2f}  {arrow} {price.change_rate}"
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
                    self._chart_menu.title = "📈 打开走势图"

        except Exception:
            logger.exception("更新价格异常")

    def _update_title(self, price: GoldPrice):
        arrow = "↑" if price.price_change >= 0 else "↓"
        self.title = f"¥{price.price:.2f} {arrow}{price.change_rate}"

    # ── 走势图 ──

    def _toggle_chart(self, sender):
        if self._chart_open:
            kill_chart()
            self._chart_open = False
            self._chart_menu.title = "📈 打开走势图"
        else:
            try:
                # 先写入最新数据（30 天全量，覆盖 1/3/7/30 天所有视图），再启动图表进程
                days_data = get_recent_days_records(days=30)
                yesterday = (
                    self._current_price.yesterday_price if self._current_price else 0
                )
                write_chart_data(days_data, yesterday)
                self._chart_last_key = self._chart_data_key(days_data)
                launch_chart()
                self._chart_open = True
                self._chart_menu.title = "📈 走势图表 (已打开)"
            except Exception as e:
                logger.exception("打开走势图失败")
                _osascript_alert("错误", f"打开走势图失败:\n{e}")

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

    def _set_price_upper(self, sender):
        default_val = str(self._config.price_upper) if self._config.price_upper > 0 else ""
        result = _osascript_dialog(
            title="价格上限",
            message="设置价格上限（元/克），超过此价格将弹窗提醒：",
            default_answer=default_val,
        )
        if result is None:
            return  # 用户取消
        try:
            val = float(result.strip()) if result.strip() else 0.0
            if val < 0:
                _osascript_alert("错误", "价格上限不能为负数")
                return
            self._config.price_upper = val
            save_config(self._config)
            self._update_settings_menu()
        except ValueError:
            _osascript_alert("错误", "请输入有效的数字")

    def _set_price_lower(self, sender):
        default_val = str(self._config.price_lower) if self._config.price_lower > 0 else ""
        result = _osascript_dialog(
            title="价格下限",
            message="设置价格下限（元/克），低于此价格将弹窗提醒：",
            default_answer=default_val,
        )
        if result is None:
            return
        try:
            val = float(result.strip()) if result.strip() else 0.0
            if val < 0:
                _osascript_alert("错误", "价格下限不能为负数")
                return
            self._config.price_lower = val
            save_config(self._config)
            self._update_settings_menu()
        except ValueError:
            _osascript_alert("错误", "请输入有效的数字")

    def _set_refresh_interval(self, sender):
        result = _osascript_dialog(
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
            _osascript_alert("错误", "请输入有效的整数")

    def _toggle_notifications(self, sender):
        self._config.notifications_enabled = not self._config.notifications_enabled
        save_config(self._config)
        self._update_settings_menu()
