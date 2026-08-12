"""平台 UI 抽象层。

统一封装 macOS / Windows 的平台边界操作：
- 输入对话框（设置持仓、价格上下限、刷新间隔）
- 提示/告警弹窗
- 进程存活检测（单实例锁）

macOS 使用 osascript（AppleScript），Windows 使用标准库
tkinter / ctypes，均为零额外依赖。
"""

import os
import subprocess
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def show_input_dialog(title: str, message: str, default_answer: str = "") -> Optional[str]:
    """弹出原生文本输入对话框。

    Returns:
        用户输入的文本，取消返回 None。
    """
    if sys.platform == "darwin":
        return _mac_input_dialog(title, message, default_answer)
    return _win_input_dialog(title, message, default_answer)


def show_alert(title: str, message: str) -> None:
    """弹出普通提示框（模态，确定按钮）。"""
    if sys.platform == "darwin":
        _mac_alert(title, message)
    else:
        _win_alert(title, message)


def show_critical_alert(title: str, message: str, sound: bool = True) -> bool:
    """弹出不可忽略的醒目告警弹窗（fire-and-forget，不阻塞调用方）。

    Args:
        title: 对话框标题
        message: 对话框内容
        sound: 是否带提示音

    Returns:
        是否发送成功。
    """
    if sys.platform == "darwin":
        return _mac_critical_alert(title, message, sound)
    return _win_critical_alert(title, message, sound)


def is_process_alive(pid: int) -> bool:
    """检测指定 PID 的进程是否仍在运行。

    信号 0 只检测进程存在，不发送信号。
    Windows 下 os.kill 不支持信号 0，改用 kernel32 API。
    """
    if sys.platform == "win32":
        return _win_is_process_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── macOS 实现（osascript）────────────────────────


def _mac_input_dialog(title: str, message: str, default_answer: str = "") -> Optional[str]:
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


def _mac_alert(title: str, message: str):
    script = (
        f'display dialog "{message}" with title "{title}" '
        f'buttons {{"确定"}} default button "确定"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)


def _mac_critical_alert(title: str, message: str, sound: bool = True) -> bool:
    # display alert 始终以模态弹窗显示，无法被忽略
    # giving up after 30 秒后自动消失；as critical 自带系统提示音
    script = (
        f'display alert "{title}" message "{message}" '
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


# ── Windows 实现（tkinter / ctypes）───────────────


def _win_input_dialog(title: str, message: str, default_answer: str = "") -> Optional[str]:
    """使用 tkinter simpledialog 弹出输入框。

    在独立临时 Tk 实例上运行，创建后立即销毁，不依赖主循环。
    """
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except ImportError:
        logger.error("tkinter 不可用，无法弹出输入对话框")
        return None

    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口，只显示对话框
    try:
        result = simpledialog.askstring(title, message, initialvalue=default_answer, parent=root)
        return result
    finally:
        root.destroy()


def _win_alert(title: str, message: str):
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        # tkinter 缺失时回退到 ctypes MessageBoxW
        _win_message_box(title, message, MB_ICON_INFORMATION)
        return
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showinfo(title, message, parent=root)
    finally:
        root.destroy()


def _win_critical_alert(title: str, message: str, sound: bool = True) -> bool:
    """使用 Win32 MessageBoxW 弹出模态告警（fire-and-forget 线程）。"""
    try:
        import threading
        threading.Thread(
            target=_win_message_box,
            args=(title, message, 0x30 if sound else 0x10),  # MB_ICONWARNING|MB_ICONERROR
            daemon=True,
        ).start()
        logger.info("告警弹窗已发送: %s - %s", title, message)
        return True
    except Exception as e:
        logger.error("告警弹窗失败: %s", e)
        return False


def _win_message_box(title: str, message: str, flags: int) -> None:
    """Win32 MessageBoxW：零依赖原生弹窗。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, flags)
    except Exception as e:
        logger.error("MessageBoxW 失败: %s", e)


def _win_is_process_alive(pid: int) -> bool:
    """Windows 下检测进程是否存在（OpenProcess + GetExitCodeProcess）。"""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False  # 进程不存在或无权限
        try:
            exit_code = wintypes.DWORD()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return True  # 无法获取退出码，保守认为存活
            # STILL_ACTIVE = 259 表示进程仍在运行
            return exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception as e:
        logger.error("进程存活检测失败: %s", e)
        return True  # 检测失败时保守认为存活，避免重复启动
