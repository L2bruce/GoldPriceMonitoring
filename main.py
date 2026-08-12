"""京东浙商银行积存金实时价格监控工具。

macOS 菜单栏 / Windows 系统托盘显示实时金价，支持：
- 实时价格显示和涨跌幅
- 价格走势图（浏览器）
- 自定义价格上下限告警
- 平台原生弹窗通知

Usage:
    python main.py           # 启动应用（macOS 菜单栏 / Windows 托盘）
    python main.py --debug   # 启动并输出调试日志
"""

import sys
import os
import logging

from gold_price.platform_ui import is_process_alive, show_alert

PID_FILE = os.path.join(os.path.expanduser("~/.goldprice"), ".pid")


def _check_single_instance() -> bool:
    """检查是否已有实例在运行。如有则弹出提示并返回 False。"""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)

    if not os.path.exists(PID_FILE):
        return True

    try:
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
    except (ValueError, IOError):
        return True

    # 检查进程是否还在运行
    if not is_process_alive(old_pid):
        # 进程不存在，清理残留 PID 文件
        os.remove(PID_FILE)
        return True

    # 进程存在，弹出提示
    show_alert("提示", "金价监控已在运行中")
    return False


def _write_pid():
    """写入当前进程 PID。"""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _cleanup_pid():
    """清理 PID 文件。"""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass


def main():
    """启动金价监控菜单栏应用。"""
    debug = "--debug" in sys.argv or "-d" in sys.argv

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if debug:
        print("🔍 调试模式已开启")

    # 单实例检查
    if not _check_single_instance():
        print("⚠️ 金价监控已在运行中，本次启动已取消")
        sys.exit(0)

    _write_pid()

    try:
        if sys.platform == "win32":
            from gold_price.win_app import GoldPriceTray
            tray = GoldPriceTray()
            tray.run()
        else:
            from gold_price.app import GoldPriceApp
            app = GoldPriceApp()
            app.run()
    finally:
        # 清理子进程
        try:
            from gold_price.chart import kill_chart
            kill_chart()
        except Exception:
            pass
        _cleanup_pid()


if __name__ == "__main__":
    main()
