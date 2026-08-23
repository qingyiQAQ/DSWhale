"""开机自启模块（Windows）。

通过写 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` 注册表项实现
开机自启：登录 Windows 时自动用 pythonw（无控制台）运行 ``main.pyw``。
非 Windows 平台下所有函数安全降级为空操作 / 返回 False（不影响主流程）。
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Run 键路径与桌宠固定的值名。
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "DSWhale"

# main.pyw 的绝对路径（位于项目根目录）。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENTRY = os.path.join(_PROJECT_ROOT, "main.pyw")

_IS_WINDOWS = sys.platform == "win32"


def _pythonw_path() -> str:
    """返回 pythonw.exe 的绝对路径；不存在则退回当前解释器（会带控制台）。"""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else sys.executable


def is_enabled() -> bool:
    """当前是否已写入开机自启注册表项。"""
    if not _IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """写入 / 删除开机自启注册表项；返回是否成功。"""
    if not _IS_WINDOWS:
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                cmd = f'"{_pythonw_path()}" "{_ENTRY}"'
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
                logger.info("[autostart] 已写入开机自启: %s", cmd)
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass  # 本就不存在
                logger.info("[autostart] 已移除开机自启")
        finally:
            winreg.CloseKey(key)
        return True
    except OSError as exc:
        logger.warning("[autostart] 开机自启设置失败: %s", exc)
        return False
