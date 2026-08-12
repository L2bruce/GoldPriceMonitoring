"""京东浙商银行积存金实时价格监控工具。

在 macOS 菜单栏显示实时金价，支持：
- 实时价格显示和涨跌幅
- 当天价格走势图
- 自定义价格上下限告警
- macOS 原生弹窗通知

Usage:
    python main.py           # 启动菜单栏应用
    python main.py --debug   # 启动并输出调试日志
"""

import sys
import os
import logging
import subprocess

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
    try:
        os.kill(old_pid, 0)  # 信号 0 只检测进程存在，不发送信号
    except OSError:
        # 进程不存在，清理残留 PID 文件
        os.remove(PID_FILE)
        return True

    # 进程存在，弹出提示
    subprocess.run([
        "osascript", "-e",
        f'display dialog "金价监控已在运行中" with title "提示" '
        f'buttons {{"确定"}} default button "确定" with icon note',
    ], capture_output=True, timeout=10)
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
