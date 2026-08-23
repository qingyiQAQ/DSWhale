"""无控制台启动入口。

双击此文件（或用 ``pythonw main.py``）启动桌宠，不弹出黑色控制台窗口；
日志仍写入 dshw-pet.log。需要看控制台日志时，仍可用 ``python main.py`` 运行。
"""

import sys

import main

if __name__ == "__main__":
    sys.exit(main.main())
