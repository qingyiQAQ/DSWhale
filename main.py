"""桌宠入口。

启动流程：
1. 配置日志（控制台 + 可选文件）。
2. 创建 QApplication（QMediaPlayer 等依赖它）。
3. 实例化 PetWindow（透明置顶窗口）。
4. 运行事件循环；捕获未处理异常并写日志，避免桌宠静默崩溃。

用法：
    python main.py
"""

from __future__ import annotations

import logging
import os
import sys

# 把项目根目录加入 sys.path，保证 ``python main.py`` 从任意 cwd 启动都能导入 core / ui。
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.pet_window import PetWindow  # noqa: E402

# 项目根目录下的 assets 目录（鲸鱼图 / gif / 音效）。
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
LOG_FILE = os.path.join(ROOT_DIR, "dshw-pet.log")


def setup_logging() -> None:
    """配置日志：控制台 INFO 级 + 文件 DEBUG 级（便于排查网络 / 线程问题）。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 控制台：INFO 及以上（运行时关键事件可见，避免 DEBUG 刷屏）。
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件：DEBUG 及以上（完整记录，含每轮消耗轮询等细节）。
    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("[main] 日志文件不可写，仅输出到控制台: %s", exc)


def main() -> int:
    """程序入口。"""
    setup_logging()
    logger = logging.getLogger("main")

    # 高 DPI 缩放（PySide6 默认已开启，此处显式声明更稳妥）。
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("DeepSeek 小鲸鱼")
    # Windows：为进程设置独立 AppUserModelID，避免任务栏把窗口归到 python.exe 名下。
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("qingyi.dswhale.pet")
        except Exception:  # noqa: BLE001 —— 非关键，失败不影响运行
            pass
    # 应用图标：使用鲸鱼图（任务栏 / Alt-Tab / 任务管理器里显示的图标）。
    app.setWindowIcon(QIcon(os.path.join(ASSETS_DIR, "DSniang1.png")))
    app.setQuitOnLastWindowClosed(False)  # 菜单关闭不退出，仅主窗口关闭时退出。

    if not os.path.isfile(os.path.join(ASSETS_DIR, "DSniang1.png")):
        logger.warning("[main] 未找到鲸鱼图片 assets/DSniang1.png，桌宠将无法显示身体")

    logger.info("[main] 正在启动 DeepSeek 小鲸鱼桌宠...")
    pet = PetWindow(ASSETS_DIR)
    pet.show()

    # 兜底异常钩子：事件循环中的未捕获异常写日志，避免静默崩溃。
    def excepthook(exc_type, exc, tb):
        logger.critical("未捕获异常", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook

    exit_code = app.exec()
    logger.info("[main] 桌宠退出，代码 %s", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
