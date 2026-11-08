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
import fcntl

LOCK_FILE = os.path.join(os.path.expanduser("~/.goldprice"), ".lock")
_lock_file_obj = None


def _acquire_single_instance() -> bool:
    """使用内核级文件锁检查是否已有实例在运行。若已被占用则弹出提示并返回 False。"""
    global _lock_file_obj
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    try:
        _lock_file_obj = open(LOCK_FILE, "a+")
        fcntl.flock(_lock_file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_obj.seek(0)
        _lock_file_obj.truncate()
        _lock_file_obj.write(f"{os.getpid()}\n")
        _lock_file_obj.flush()
        return True
    except (BlockingIOError, IOError):
        # 已有实例持有锁，弹出提示
        subprocess.run([
            "osascript", "-e",
            'display dialog "金价监控已在运行中" with title "提示" '
            'buttons {"确定"} default button "确定" with icon note',
        ], capture_output=True, timeout=10)
        return False


def _release_single_instance():
    """显式释放锁并关闭文件。"""
    global _lock_file_obj
    if _lock_file_obj:
        try:
            fcntl.flock(_lock_file_obj.fileno(), fcntl.LOCK_UN)
            _lock_file_obj.close()
        except Exception:
            pass
        _lock_file_obj = None


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
    if not _acquire_single_instance():
        print("⚠️ 金价监控已在运行中，本次启动已取消")
        sys.exit(0)

    try:
        from gold_price.app import GoldPriceApp
        app = GoldPriceApp()
        app.run()
    finally:
        # 清理子进程与锁
        try:
            from gold_price.chart import kill_chart
            kill_chart()
        except Exception:
            pass
        _release_single_instance()


if __name__ == "__main__":
    main()
