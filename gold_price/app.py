"""macOS 菜单栏应用主模块。

基于 rumps 实现菜单栏价格显示、走势图打开、设置管理等功能。
"""

import subprocess
import logging
import threading
from typing import Optional

from PyObjCTools import AppHelper
import rumps

from .fetcher import fetch_price, GoldPrice
from .config import load_config, save_config, AppConfig
from .storage import add_price_record, get_today_records, get_recent_days_records
from .alerter import check_price_alert, reset_alert_cooldown
from .chart import (
    launch_chart,
    open_chart_page,
    write_chart_data,
    is_chart_running,
)

logger = logging.getLogger(__name__)

APP_NAME = "金价监控"
REFRESH_MIN = 5
REFRESH_MAX = 300
DEFAULT_INTERVAL = 10  # 默认刷新间隔（秒）
COOLDOWN_PRESETS = [5, 10, 30, 60]  # 告警周期预设选项（分钟）
DISPLAY_MODES = [
    ("full", "完整模式 (¥价格 ↑+%)"),
    ("compact", "紧凑模式 (价格 ↑+%)"),
    ("minimal", "极简模式 (价格 ↑)"),
    ("icon", "仅图标 (💰)"),
]


def _escape_applescript(s) -> str:
    """转义 AppleScript 字符串中的反斜杠和双引号，防止语法报错或解析异常。"""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _osascript_dialog(title: str, message: str, default_answer: str = "") -> Optional[str]:
    """使用 AppleScript 弹出原生文本输入对话框。

    Returns:
        用户输入的文本，取消返回 None。
    """
    safe_title = _escape_applescript(title)
    safe_message = _escape_applescript(message)
    safe_default = _escape_applescript(default_answer)
    script = (
        f'display dialog "{safe_message}" '
        f'with title "{safe_title}" '
        f'default answer "{safe_default}" '
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
    safe_title = _escape_applescript(title)
    safe_message = _escape_applescript(message)
    script = f'display dialog "{safe_message}" with title "{safe_title}" buttons {{"确定"}} default button "确定"'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)


class _BrightMenuItem(rumps.MenuItem):
    """展示类菜单项：文字清晰饱满、加粗高亮，自适应浅色/深色模式。

    macOS 默认会将无回调的菜单项置为灰色禁用态。
    通过赋予回调、设置加粗字体与保持 enabled，确保在浅色/深色模式下均清晰可见。
    """

    def __init__(self, title, callback=None):
        super().__init__(title, callback=callback)
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
                NSFont,
                NSFontAttributeName,
            )
            font = NSFont.boldSystemFontOfSize_(13.5)
            attr = NSAttributedString.alloc().initWithString_attributes_(
                self._menuitem.title(), {NSFontAttributeName: font}
            )
            self._menuitem.setAttributedTitle_(attr)
            self._menuitem.setEnabled_(True)
        except Exception:
            pass


def _make_display_item(title: str, callback=None) -> rumps.MenuItem:
    """创建清晰展示菜单项。"""
    return _BrightMenuItem(title, callback=callback)


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
        self._is_fetching = False    # 异步拉取防重标记

        # ── 菜单 ──
        self._price_display = _make_display_item("", callback=self._open_chart)

        self._chart_menu = rumps.MenuItem("📈 打开走势图", callback=self._open_chart)

        self._settings_upper = rumps.MenuItem(
            "价格上限: 不限", callback=self._set_price_upper
        )
        self._settings_lower = rumps.MenuItem(
            "价格下限: 不限", callback=self._set_price_lower
        )
        self._settings_cooldown = rumps.MenuItem("告警周期: 30分钟")
        self._cooldown_items = {}
        for mins in COOLDOWN_PRESETS:
            item = rumps.MenuItem(
                f"{mins}分钟",
                callback=self._make_cooldown_callback(mins),
            )
            self._settings_cooldown.add(item)
            self._cooldown_items[mins] = item

        self._cooldown_custom_item = rumps.MenuItem(
            "自定义...",
            callback=self._set_cooldown_custom,
        )
        self._settings_cooldown.add(self._cooldown_custom_item)

        self._settings_display_mode = rumps.MenuItem("显示样式: 完整模式")
        self._display_mode_items = {}
        for mode_key, mode_label in DISPLAY_MODES:
            item = rumps.MenuItem(
                mode_label,
                callback=self._make_display_mode_callback(mode_key),
            )
            self._settings_display_mode.add(item)
            self._display_mode_items[mode_key] = item

        self._settings_interval = rumps.MenuItem(
            f"刷新间隔: {DEFAULT_INTERVAL}秒", callback=self._set_refresh_interval
        )
        self._settings_notify = rumps.MenuItem(
            "🔔 通知: 开", callback=self._toggle_notifications
        )

        settings_menu = rumps.MenuItem("⚙️ 设置")
        settings_menu.add(self._settings_upper)
        settings_menu.add(self._settings_lower)
        settings_menu.add(self._settings_cooldown)
        settings_menu.add(rumps.separator)
        settings_menu.add(self._settings_display_mode)
        settings_menu.add(self._settings_interval)
        settings_menu.add(rumps.separator)
        settings_menu.add(self._settings_notify)

        # ── 我的持仓 ──
        self._holding_grams = _make_display_item("持仓克重: 未设置", callback=self._set_holding)
        self._holding_cost = _make_display_item("成本均价: 未设置", callback=self._set_holding)
        self._holding_profit = _make_display_item("持仓收益: --", callback=self._set_holding)
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

        # 禁用菜单和子菜单的自动置灰，确保所有信息项始终保持清晰高亮
        try:
            if hasattr(self.menu, "_menu") and self.menu._menu:
                self.menu._menu.setAutoenablesItems_(False)
            holding_sub = holding_menu._menuitem.submenu()
            if holding_sub:
                holding_sub.setAutoenablesItems_(False)
        except Exception:
            pass

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

        # 告警周期
        mins = cfg.alert_cooldown // 60
        if mins > 0 and cfg.alert_cooldown % 60 == 0:
            self._settings_cooldown.title = f"告警周期: {mins}分钟"
        elif mins > 0:
            self._settings_cooldown.title = f"告警周期: {mins}分{cfg.alert_cooldown % 60}秒"
        else:
            self._settings_cooldown.title = f"告警周期: {cfg.alert_cooldown}秒"

        matched = False
        for p_mins, item in self._cooldown_items.items():
            if cfg.alert_cooldown == p_mins * 60:
                item.state = 1
                matched = True
            else:
                item.state = 0
        self._cooldown_custom_item.state = 1 if not matched else 0

        # 显示样式
        mode_titles = {
            "full": "完整模式",
            "compact": "紧凑模式",
            "minimal": "极简模式",
            "icon": "仅图标",
        }
        current_mode = getattr(cfg, "display_mode", "full")
        self._settings_display_mode.title = f"显示样式: {mode_titles.get(current_mode, '完整模式')}"
        for m_key, item in self._display_mode_items.items():
            item.state = 1 if current_mode == m_key else 0

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

    def _fetch_and_update(self, sender=None):
        """定时器触发的价格拉取（转入后台工作线程，不阻塞 UI 主线程）。"""
        if self._is_fetching:
            logger.debug("上一轮价格获取尚未完成，跳过本次触发")
            return

        def _worker():
            self._is_fetching = True
            try:
                price = fetch_price()
                try:
                    AppHelper.callAfter(self._on_price_fetched, price)
                except Exception:
                    self._on_price_fetched(price)
            except Exception:
                logger.exception("异步获取价格工作线程异常")
            finally:
                self._is_fetching = False

        threading.Thread(target=_worker, daemon=True).start()

    def _on_price_fetched(self, price: Optional[GoldPrice]):
        """在 UI 主线程中接收拉取结果并更新界面、历史记录和告警。"""
        try:
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

        except Exception:
            logger.exception("更新价格异常")

    def _update_title(self, price: GoldPrice):
        arrow = "↑" if price.price_change >= 0 else "↓"
        mode = getattr(self._config, "display_mode", "full")
        if mode == "icon":
            self.title = "💰"
        elif mode == "minimal":
            self.title = f"{price.price:.2f} {arrow}"
        elif mode == "compact":
            self.title = f"{price.price:.2f} {arrow}{price.change_rate}"
        else:  # "full"
            self.title = f"¥{price.price:.2f} {arrow}{price.change_rate}"

    # ── 走势图 ──

    def _open_chart(self, sender):
        """打开走势图：无论当前是否已打开，单击后浏览器一定重新弹出页面。

        之前是开关式（toggle）：图表已打开时再点会变成"关闭"，页面没弹出，
        用户只能再点一次——表现为"要点击两次才能弹出"。拆成打开/关闭两项后，
        点击打开始终弹出页面。
        """
        try:
            if is_chart_running():
                # 已打开：刷新最新数据，再弹一次页面（新标签页，保证页面可见）
                self._update_chart()
            else:
                # 先写入最新数据（30 天全量，覆盖 1/3/7/30 天所有视图），再启动服务
                days_data = get_recent_days_records(days=30)
                yesterday = (
                    self._current_price.yesterday_price if self._current_price else 0
                )
                write_chart_data(days_data, yesterday)
                self._chart_last_key = self._chart_data_key(days_data)
                launch_chart()
            self._chart_open = True
            open_chart_page()
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
            if val > 0 and self._config.price_lower > 0 and val <= self._config.price_lower:
                _osascript_alert("错误", f"价格上限 (¥{val:.1f}) 必须大于价格下限 (¥{self._config.price_lower:.1f})")
                return
            self._config.price_upper = val
            save_config(self._config)
            reset_alert_cooldown("upper")
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
            if val > 0 and self._config.price_upper > 0 and val >= self._config.price_upper:
                _osascript_alert("错误", f"价格下限 (¥{val:.1f}) 必须小于价格上限 (¥{self._config.price_upper:.1f})")
                return
            self._config.price_lower = val
            save_config(self._config)
            reset_alert_cooldown("lower")
            self._update_settings_menu()
        except ValueError:
            _osascript_alert("错误", "请输入有效的数字")

    def _make_cooldown_callback(self, minutes: int):
        def _callback(sender):
            self._config.alert_cooldown = minutes * 60
            save_config(self._config)
            self._update_settings_menu()
        return _callback

    def _set_cooldown_custom(self, sender):
        current_mins = max(1, self._config.alert_cooldown // 60)
        result = _osascript_dialog(
            title="告警周期",
            message="设置告警周期（分钟，越界告警后在此周期内不再弹窗）：",
            default_answer=str(current_mins),
        )
        if result is None:
            return
        try:
            val = int(result.strip()) if result.strip() else current_mins
            if val < 1:
                _osascript_alert("错误", "告警周期不能小于 1 分钟")
                return
            self._config.alert_cooldown = val * 60
            save_config(self._config)
            self._update_settings_menu()
        except ValueError:
            _osascript_alert("错误", "请输入有效的整数")

    def _make_display_mode_callback(self, mode: str):
        def _callback(sender):
            self._config.display_mode = mode
            save_config(self._config)
            self._update_settings_menu()
            if self._current_price:
                self._update_title(self._current_price)
            elif mode == "icon":
                self.title = "💰"
        return _callback

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
