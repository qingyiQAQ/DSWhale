"""主窗口：桌宠本体。

职责：
- 创建透明、无边框、置顶的方形窗口，绘制抠图鲸鱼（含按压 Q 弹 + 左吸附镜像）。
- 处理鼠标拖拽、四边四分之一吸附、点击（开气泡 / 刷新）。
- 串联余额、用量、记账、每轮消耗、音效、菜单等服务，用后台线程执行网络请求，
  通过 Qt 信号把结果安全地送回 UI 线程。
- 驱动 60 秒余额自动刷新、每轮消耗轮询、气泡 5 秒自动收起等定时任务。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time

from typing import Optional

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from core import autostart, ledger, pricing, usage
from core.audio import SoundPlayer
from core.balance import BalanceClient
from core.config import Config, ledger_path
from core.random_lines import gif_fallback_lines, pick_idle_remark, pick_random_lines
from core.turn_cost import TurnCostMonitor

from .bubble import BubbleRenderer, BUBBLE_ASPECT
from .menu import MAX_SCALE, MIN_SCALE, MenuWidget

logger = logging.getLogger(__name__)

# 鲸鱼几何参数（与原网页挂件一致）。
WHALE_RATIO = 0.5945   # 鲸鱼占窗口的比例
WHALE_SRC = 610        # 鲸鱼源图边长（DSniang1.png 为 610×610）

# 时间常量（与原项目一致）。
REFRESH_MS = 60000
BUBBLE_MS = 5000
CHANGE_DELAY_MS = 300
CHANGE_SETTLE_MS = 900

# 空闲俏皮话（未点击时随机弹出）。
IDLE_REMARK_MS = 3000     # 俏皮话气泡展示时长
IDLE_TALK_MIN_MS = 10000  # 两次俏皮话的随机间隔下限
IDLE_TALK_MAX_MS = 20000  # 两次俏皮话的随机间隔上限


class _Signals(QObject):
    """跨线程信号桥：工作线程 emit，UI 线程槽函数接收（自动队列连接）。"""

    balance_ready = Signal(object, bool)   # (payload, manual)
    turn_cost_ready = Signal(float)        # amount


class MenuButton(QWidget):
    """鲸鱼右上角的三点菜单按钮（真正的子控件）。

    旧实现是在 paintEvent 里绘制的「假按钮」，其位置落在鲸鱼 PNG 的透明留白上，
    透明窗口（WA_TranslucentBackground）会对其透明像素做鼠标穿透，导致悬停即消失、
    无法点击。改为独立子控件后，控件自绘不透明背景、拥有独立的事件处理，
    鼠标可正常悬停 / 点击。
    """

    clicked = Signal()

    def __init__(self, parent: "PetWindow"):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def paintEvent(self, event) -> None:
        """绘制圆角底 + 三个白点。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        # alpha 217 的底足以让该区域在顶层窗口层面不透明，避免鼠标穿透。
        painter.setBrush(QColor(32, 49, 112, 217))
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.setBrush(QColor("white"))
        bar_w, bar_h, gap = 14.0, 2.0, 4.0
        cx = self.width() / 2.0
        start_y = self.height() / 2.0 - (bar_h * 3 + gap * 2) / 2
        for i in range(3):
            y = start_y + i * (bar_h + gap)
            painter.drawRoundedRect(QRectF(cx - bar_w / 2, y, bar_w, bar_h), 1, 1)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        self._notify_parent("_cancel_btn_hide")

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        self._notify_parent("_schedule_btn_hide")

    def mousePressEvent(self, event) -> None:
        """吞掉按下事件，阻止其传播到父窗口（PetWindow）触发「菜单打开时点击关闭」逻辑。

        否则菜单打开时点按钮：按下先传播到父窗口关闭菜单，松手又 clicked 重新打开，
        表现为「每次点击都展示菜单」而非「展开/关闭」切换。
        """
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        """在按钮内松开左键即触发点击；不冒泡给父窗口，避免触发拖拽 / 气泡。"""
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _notify_parent(self, method: str) -> None:
        """调用父窗口的显隐辅助方法（不存在则忽略，保证独立可测）。"""
        fn = getattr(self.parentWidget(), method, None)
        if callable(fn):
            fn()


class PetWindow(QWidget):
    """透明置顶的桌宠窗口。"""

    def __init__(self, assets_dir: str):
        # Tool 窗口：不占任务栏 / Alt-Tab，桌宠只漂浮在桌面（无应用图标）。
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("DeepSeek 小鲸鱼")
        self.setWindowIcon(QIcon(os.path.join(assets_dir, "DSniang1.png")))

        self._assets_dir = assets_dir

        # ---------------- 配置与凭据 ----------------
        self._config = Config()
        self._api_key = self._config.resolve_credential("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY")
        self._platform_token = self._config.resolve_credential("DEEPSEEK_PLATFORM_TOKEN", "DEEPSEEK_PLATFORM_TOKEN")
        self._ledger_path = ledger_path()
        if not self._api_key:
            logger.warning("[pet] 未配置 DEEPSEEK_API_KEY，余额将显示错误提示（不影响其它功能）")

        # ---------------- 服务 ----------------
        self.balance = BalanceClient(self._api_key)
        self.sound = SoundPlayer(
            assets_dir,
            sound_set=self._config.get("soundSet", "duck"),
            volume=float(self._config.get("vol", 0.9)),
            enabled=float(self._config.get("vol", 0.9)) > 0,
        )
        self._signals = _Signals()
        self._signals.balance_ready.connect(self._on_balance_ready)
        self._signals.turn_cost_ready.connect(self._on_turn_cost_ready)
        self.turn_cost = TurnCostMonitor(self._config.get("dshServer", "http://127.0.0.1:3080"), self._signals.turn_cost_ready.emit)

        # ---------------- 气泡与菜单 ----------------
        self.bubble = BubbleRenderer(os.path.join(assets_dir, "rua.gif"), self)
        self.bubble.changed.connect(self.update)
        self.menu = MenuWidget()
        self.menu.populate(self._config.settings)

        # ---------------- 状态 ----------------
        self._scale = max(MIN_SCALE, min(MAX_SCALE, float(self._config.get("scale", 1.5))))
        self._mirrored = False
        self._h_anchor: Optional[str] = "right"
        self._v_anchor: Optional[str] = "bottom"
        self._squish = 0.0
        self._squish_anim: Optional[QVariantAnimation] = None

        self._dragging = False
        self._drag_start = QPointF()
        self._drag_orig = QPoint()
        self._drag_moved = False
        self._show_menu_btn = False
        self._menu_open = False
        self._settle_anim: Optional[QVariantAnimation] = None

        self._balance: Optional[float] = None
        self._currency = "CNY"
        self._today_usage: Optional[float] = None
        self._is_peak = False
        self._shown: Optional[float] = None
        self._status = "loading"       # loading / ok / error / changing
        self._message = ""
        self._balance_busy = False

        self._bubble_on = bool(self._config.get("bubbleOn", True))
        self._idle_talk_on = bool(self._config.get("idleTalkOn", True))
        self._turn_cost_on = bool(self._config.get("turnCostOn", True))
        self._autostart_on = bool(self._config.get("autostartOn", False))
        self._peak_mode = self._config.get("peakMode", "default")
        self._bubble_random_active = False
        self._cost_bubble_active = False
        self._bubble_timer: Optional[QTimer] = None
        self._cost_bubble_timer: Optional[QTimer] = None

        # 菜单接线放在状态初始化之后（_wire_menu 会读取 _turn_cost_on）。
        self._wire_menu()

        # 三点菜单按钮（真正的子控件，见 MenuButton 类说明）。
        self.menu_button = MenuButton(self)
        self.menu_button.clicked.connect(self._toggle_menu)
        self._btn_hide_timer = QTimer(self)
        self._btn_hide_timer.setSingleShot(True)
        self._btn_hide_timer.timeout.connect(self._hide_menu_btn)
        self.menu_button.hide()

        # ---------------- 资源与窗口尺寸 ----------------
        self._load_assets()
        self.resize(self._compute_base(QGuiApplication.primaryScreen()), self._compute_base(QGuiApplication.primaryScreen()))
        self._restore_or_default_pos()

        # ---------------- 后台线程与定时器 ----------------
        self._stop_event = threading.Event()
        self._turn_thread = threading.Thread(target=self._turn_poll_loop, daemon=True)
        self._turn_thread.start()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(lambda: self._refresh_balance_async(False))
        self._refresh_timer.start(REFRESH_MS)

        # 空闲俏皮话：未点击时每 3-5 秒随机弹一句（单次触发，每次到点后重新排定）。
        self._idle_talk_timer = QTimer(self)
        self._idle_talk_timer.setSingleShot(True)
        self._idle_talk_timer.timeout.connect(self._on_idle_talk)
        self._arm_idle_talk()

        # 屏幕尺寸变化时按锚点重新吸附。
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen.availableGeometryChanged.connect(self._settle_anchored)

        # 初始渲染（气泡保持关闭，仅填充内容）。
        self._render()
        # 首次余额加载。
        self._refresh_balance_async(False)

        logger.info("[pet] 桌宠启动完成：scale=%.2f 模式=%s 记账文件=%s",
                    self._scale, self._config.get("usageMode", "ledger"), self._ledger_path)

    # ------------------------------------------------------------------ #
    # 资源加载
    # ------------------------------------------------------------------ #
    def _load_assets(self) -> None:
        """加载鲸鱼图片（QPixmap 用于绘制，QImage 用于透明命中检测）。"""
        path = os.path.join(self._assets_dir, "DSniang1.png")
        self._whale_pixmap = QPixmap(path)
        self._whale_image = QImage(path)
        if self._whale_pixmap.isNull():
            logger.warning("[pet] 鲸鱼图片加载失败: %s", path)

    # ------------------------------------------------------------------ #
    # 窗口尺寸与位置
    # ------------------------------------------------------------------ #
    def _current_screen(self):
        center = self.frameGeometry().center()
        return QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()

    def _work_area(self):
        return self._current_screen().availableGeometry()

    def _compute_base(self, screen=None) -> int:
        """按缩放与屏幕尺寸计算窗口边长（对齐原项目 --dshw-base 公式）。"""
        if screen is None:
            screen = self._current_screen()
        geo = screen.availableGeometry()
        base = min(250, min(geo.width(), geo.height()) * 0.28) * self._scale
        return int(round(max(122, min(base, 625))))

    def _restore_or_default_pos(self) -> None:
        """恢复上次位置锚点，或落到主屏右下角。"""
        pos = self._config.get("pos")
        geo = QGuiApplication.primaryScreen().availableGeometry()
        base = self.width()
        if isinstance(pos, dict) and pos.get("hAnchor") in ("left", "right") and pos.get("vAnchor") in ("top", "bottom"):
            try:
                h_anchor = pos["hAnchor"]
                v_anchor = pos["vAnchor"]
                h_dist = int(pos["hDist"])
                v_dist = int(pos["vDist"])
                x = geo.left() + h_dist if h_anchor == "left" else geo.right() - h_dist - base
                y = geo.top() + v_dist if v_anchor == "top" else geo.bottom() - v_dist - base
                x = max(geo.left(), min(x, geo.right() - base))
                y = max(geo.top(), min(y, geo.bottom() - base))
                self.move(x, y)
                self._mirrored = (h_anchor == "left")
                self._h_anchor = h_anchor
                self._v_anchor = v_anchor
                self._position_menu_button()  # 恢复镜像锚点时按钮跟随换边
                logger.info("[pet] 恢复位置锚点 %s/%s", h_anchor, v_anchor)
                return
            except (TypeError, ValueError):
                pass
        self.move(geo.right() - base, geo.bottom() - base)
        self._mirrored = False
        self._h_anchor = "right"
        self._v_anchor = "bottom"

    def _apply_scale(self, scale: float) -> None:
        """调整缩放并保持鲸鱼所在角固定（未镜像=右下角，镜像=左下角）。"""
        self._scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        base = self._compute_base()
        old = self.geometry()
        fixed = QPoint(old.left(), old.bottom()) if self._mirrored else QPoint(old.right(), old.bottom())
        self.resize(base, base)
        new_left = fixed.x() if self._mirrored else fixed.x() - base
        new_top = fixed.y() - base
        area = self._work_area()
        new_left = max(area.left(), min(new_left, area.right() - base))
        new_top = max(area.top(), min(new_top, area.bottom() - base))
        self.move(new_left, new_top)
        logger.info("[pet] 缩放调整为 %.2f（窗口 %dx%d）", self._scale, base, base)

    def _save_pos(self) -> None:
        """把当前相对屏幕边界的锚点距离持久化（跨启动记忆位置）。"""
        geo = self.frameGeometry()
        area = self._work_area()
        left_dist = geo.left() - area.left()
        right_dist = area.right() - geo.right()
        top_dist = geo.top() - area.top()
        bottom_dist = area.bottom() - geo.bottom()
        self._config.set("pos", {
            "hAnchor": "left" if left_dist <= right_dist else "right",
            "hDist": int(round(min(left_dist, right_dist))),
            "vAnchor": "top" if top_dist <= bottom_dist else "bottom",
            "vDist": int(round(min(top_dist, bottom_dist))),
        })

    def _settle_anchored(self) -> None:
        """屏幕尺寸变化时按锚点重新贴边。"""
        area = self._work_area()
        base = self.width()
        if self._h_anchor == "right":
            x = area.right() - base
        elif self._h_anchor == "left":
            x = area.left()
        else:
            x = self.x()
        if self._v_anchor == "bottom":
            y = area.bottom() - base
        elif self._v_anchor == "top":
            y = area.top()
        else:
            y = self.y()
        x = max(area.left(), min(x, area.right() - base))
        y = max(area.top(), min(y, area.bottom() - base))
        self.move(x, y)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._paint_whale(painter)
        bubble_h = int(round(self.width() * BUBBLE_ASPECT))
        self.bubble.paint(painter, self.width(), bubble_h, self._mirrored)

    def _paint_whale(self, painter: QPainter) -> None:
        """绘制鲸鱼（按压 Q 弹 + 左吸附镜像，围绕底部中心缩放）。"""
        if self._whale_pixmap.isNull():
            return
        base = self.width()
        size = int(round(base * WHALE_RATIO))
        # 鲸鱼始终贴屏幕边缘：未镜像贴右（right:0），镜像贴左（left:0）。
        # 之前固定右下锚点，导致镜像后鲸鱼身体仍停在窗口右侧，永远到不了屏幕最左侧。
        left = 0 if self._mirrored else base - size
        top = base - size
        painter.save()
        painter.translate(left, top)
        cx = size / 2.0
        cy = float(size)  # 底部
        painter.translate(cx, cy)
        sx = 1.0 + 0.05 * self._squish
        sy = 1.0 - 0.12 * self._squish
        if self._mirrored:
            sx = -sx
        painter.scale(sx, sy)
        painter.translate(-cx, -cy)
        painter.drawPixmap(QRectF(0, 0, size, size), self._whale_pixmap, QRectF(0, 0, WHALE_SRC, WHALE_SRC))
        painter.restore()

    def resizeEvent(self, event) -> None:
        """窗口尺寸变化时重新定位菜单按钮。"""
        super().resizeEvent(event)
        self._position_menu_button()

    def _position_menu_button(self) -> None:
        """把菜单按钮定位到鲸鱼顶角（未镜像=右上，镜像=左上，对齐原项目 right:4px/left:4px）。"""
        base = self.width()
        size = 26
        x = 4 if self._mirrored else base - 4 - size
        self.menu_button.move(x, int(base * 0.4055) + 4)

    def _update_menu_btn_visibility(self) -> None:
        """按悬停 / 菜单开关状态更新按钮可见性。"""
        self.menu_button.setVisible(self._show_menu_btn or self._menu_open)

    def _schedule_btn_hide(self) -> None:
        """鼠标离开（父窗口或按钮）后延迟隐藏，给「移到按钮」留出时间。"""
        if self._menu_open:
            return
        self._btn_hide_timer.start(250)

    def _cancel_btn_hide(self) -> None:
        """取消延迟隐藏并立即显示按钮。"""
        self._btn_hide_timer.stop()
        self._show_menu_btn = True
        self.menu_button.setVisible(True)

    def _hide_menu_btn(self) -> None:
        """延迟超时：真正隐藏按钮（菜单打开时不隐藏）。"""
        if self._menu_open:
            return
        self._show_menu_btn = False
        self.menu_button.setVisible(False)

    # ------------------------------------------------------------------ #
    # 鼠标交互
    # ------------------------------------------------------------------ #
    def enterEvent(self, event) -> None:
        self._show_menu_btn = True
        self._cancel_btn_hide()

    def leaveEvent(self, event) -> None:
        if not self._menu_open:
            self._schedule_btn_hide()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._arm_idle_talk()  # 任何左键交互都重置空闲计时
        pos = event.position()

        # 菜单打开时：按下关闭菜单，不进入拖拽/气泡（对齐原项目 onDocPointerDown）。
        if self._menu_open:
            self._toggle_menu()
            return

        # 1. 气泡打开时点击气泡 -> 切换台词 / 关闭。
        if self.bubble.is_open and self.bubble.hit_test(
            pos.x(), pos.y(), self.width(), int(round(self.width() * BUBBLE_ASPECT)), self._mirrored
        ):
            self._on_bubble_click()
            return

        # 2. 鲸鱼不透明区域 -> 开始拖拽。
        if not self._is_whale_hit(pos):
            return
        self._dragging = True
        # 用全局坐标记录起点：窗口随鼠标移动时，局部坐标原点也跟着变，
        # 若用 event.position() 求增量会陷入反馈，导致鲸鱼只以鼠标一半速度移动。
        self._drag_start = event.globalPosition()
        self._drag_orig = self.pos()
        self._drag_moved = False
        self._press_down()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        delta = event.globalPosition() - self._drag_start
        if delta.x() ** 2 + delta.y() ** 2 >= 9:
            self._drag_moved = True
        area = self._work_area()
        base = self.width()
        new_x = max(area.left(), min(self._drag_orig.x() + int(delta.x()), area.right() - base))
        new_y = max(area.top(), min(self._drag_orig.y() + int(delta.y()), area.bottom() - base))
        self.move(new_x, new_y)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._press_up()
        if not self._drag_moved:
            # 点击：打开气泡 + 手动刷新余额。
            self._show_bubble()
            self._refresh_balance_async(True)
        else:
            self._snap_and_settle()

    def _is_whale_hit(self, pos: QPointF) -> bool:
        """鲸鱼透明命中检测（按 alpha 通道，与网页版 isWhaleHit 一致）。"""
        base = self.width()
        size = int(round(base * WHALE_RATIO))
        left = 0 if self._mirrored else base - size
        top = base - size
        x, y = pos.x(), pos.y()
        if not (left <= x < left + size and top <= y < top + size):
            return False
        if self._whale_image.isNull():
            return True  # 无 alpha 数据时按整个矩形命中
        lx = (x - left) / size * WHALE_SRC
        ly = (y - top) / size * WHALE_SRC
        if self._mirrored:
            lx = WHALE_SRC - lx
        ix, iy = int(lx), int(ly)
        if ix < 0 or iy < 0 or ix >= WHALE_SRC or iy >= WHALE_SRC:
            return False
        return self._whale_image.pixelColor(ix, iy).alpha() > 10

    def _press_down(self) -> None:
        """按压：Q 弹压扁 + 播放按压音效。"""
        self._start_squish_anim(1.0)
        self.sound.play_press()

    def _press_up(self) -> None:
        """松手：回弹 + 播放松手音效。"""
        self._start_squish_anim(0.0)
        self.sound.play_release()

    def _start_squish_anim(self, target: float) -> None:
        """驱动 Q 弹缩放动画（松手用 OutBack 过冲回弹）。"""
        if self._squish_anim is not None:
            self._squish_anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(self._squish)
        anim.setEndValue(target)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.OutBack if target == 0.0 else QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self._on_squish_value)
        anim.start()
        self._squish_anim = anim

    def _on_squish_value(self, value: float) -> None:
        self._squish = float(value)
        self.update()

    def _snap_and_settle(self) -> None:
        """拖拽结束：四边四分之一吸附（左/右/上/下，角落可组合）。"""
        geo = self.frameGeometry()
        area = self._work_area()
        w, h = geo.width(), geo.height()
        left, top = geo.left(), geo.top()
        center_x = left + w / 2.0
        center_y = top + h / 2.0

        if center_x < area.left() + area.width() / 4.0:
            self._h_anchor, target_x = "left", area.left()
        elif center_x > area.left() + area.width() * 3 / 4.0:
            self._h_anchor, target_x = "right", area.right() - w
        else:
            self._h_anchor, target_x = None, left

        if center_y < area.top() + area.height() / 4.0:
            self._v_anchor, target_y = "top", area.top()
        elif center_y > area.top() + area.height() * 3 / 4.0:
            self._v_anchor, target_y = "bottom", area.bottom() - h
        else:
            self._v_anchor, target_y = None, top

        self._mirrored = (self._h_anchor == "left")
        self._position_menu_button()  # 镜像切换时按钮跟随换边（右上<->左上）
        self._animate_move_to(QPoint(target_x, target_y))
        self._save_pos()

    def _animate_move_to(self, target: QPoint) -> None:
        """平滑移动到目标点（160ms 缓动）。"""
        start = self.pos()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda v: self.move(
            round(start.x() + (target.x() - start.x()) * float(v)),
            round(start.y() + (target.y() - start.y()) * float(v)),
        ))
        anim.start()
        self._settle_anim = anim

    # ------------------------------------------------------------------ #
    # 气泡交互
    # ------------------------------------------------------------------ #
    def _show_bubble(self) -> None:
        """打开气泡展示余额 + 今日已用，5 秒后自动收起。"""
        if not self._bubble_on:
            return
        if self._cost_bubble_active:
            return
        self._bubble_random_active = False
        self._render()
        self.bubble.open()
        self._restart_bubble_timer()

    def _hide_bubble(self) -> None:
        """收起气泡（内容保留，下次打开时再恢复为余额内容）。"""
        self._bubble_random_active = False
        self.bubble.close()

    def _restart_bubble_timer(self, ms: int = BUBBLE_MS) -> None:
        if self._bubble_timer is not None:
            self._bubble_timer.stop()
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)
        self._bubble_timer.start(ms)

    def _on_bubble_click(self) -> None:
        """点击气泡：消耗泡泡->关闭；首次->切随机台词；再次->关闭。"""
        if self._cost_bubble_active:
            self._hide_cost_bubble()
            return
        if self._bubble_random_active:
            self._hide_bubble()
            return
        self._bubble_random_active = True
        lines = pick_random_lines(self._is_peak, self._peak_mode, self._today_usage, self._currency)
        if lines.get("gif") and not self.bubble.gif_available:
            lines = gif_fallback_lines()
        self.bubble.swap_text(lambda: self.bubble.set_random(lines, self.bubble.gif_available))
        self._restart_bubble_timer()

    def _show_cost_bubble(self, amount: float) -> None:
        """弹出「上一轮对话消耗」金额泡泡。"""
        if not self._bubble_on or not self._turn_cost_on:
            return
        self._cancel_bubble_timer()
        self._cost_bubble_active = True
        self._bubble_random_active = False
        self.bubble.set_cost(float(amount), self._currency or "CNY")
        self.bubble.open()
        close_ms = int(self._config.get("turnCostCloseMs", 5000) or 0)
        if close_ms > 0:
            if self._cost_bubble_timer is not None:
                self._cost_bubble_timer.stop()
            self._cost_bubble_timer = QTimer(self)
            self._cost_bubble_timer.setSingleShot(True)
            self._cost_bubble_timer.timeout.connect(self._hide_cost_bubble)
            self._cost_bubble_timer.start(close_ms)

    def _hide_cost_bubble(self) -> None:
        self._cost_bubble_active = False
        self._hide_bubble()

    def _cancel_bubble_timer(self) -> None:
        if self._bubble_timer is not None:
            self._bubble_timer.stop()
            self._bubble_timer = None

    # ------------------------------------------------------------------ #
    # 空闲俏皮话
    # ------------------------------------------------------------------ #
    def _arm_idle_talk(self) -> None:
        """重新排定下一次空闲俏皮话（随机 10-20 秒）；关闭闲置对话则停止计时。"""
        if not self._idle_talk_on:
            self._idle_talk_timer.stop()
            return
        self._idle_talk_timer.start(random.randint(IDLE_TALK_MIN_MS, IDLE_TALK_MAX_MS))

    def _on_idle_talk(self) -> None:
        """空闲计时到点：弹一句随机俏皮话，并重新排定下一次。"""
        self._arm_idle_talk()
        if not self._idle_talk_on or not self._bubble_on or self._cost_bubble_active:
            return
        # 余额气泡展示中不打断：余额变化时应覆盖俏皮话，而非被俏皮话覆盖。
        if self.bubble.is_open and not self._bubble_random_active:
            return
        self._show_idle_remark()

    def _show_idle_remark(self) -> None:
        """展示一句随机俏皮话，3 秒后气泡自动消失。"""
        self._bubble_random_active = True
        self.bubble.set_random(pick_idle_remark(), self.bubble.gif_available)
        if not self.bubble.is_open:
            self.bubble.open()
        self._restart_bubble_timer(IDLE_REMARK_MS)

    # ------------------------------------------------------------------ #
    # 余额与用量
    # ------------------------------------------------------------------ #
    def _refresh_balance_async(self, manual: bool = False) -> None:
        """在后台线程发起余额刷新（去重保护）。"""
        if self._balance_busy:
            return
        self._balance_busy = True
        threading.Thread(target=self._balance_worker, args=(manual,), daemon=True).start()

    def _balance_worker(self, manual: bool) -> None:
        """后台线程：拉取余额 + 记账 + 今日已用，结果经信号送回 UI 线程。"""
        try:
            payload = self._get_balance_payload()
        except Exception as exc:  # noqa: BLE001 —— 兜底避免工作线程静默死亡
            payload = {"ok": False, "code": "ERROR", "error": f"余额服务异常: {exc}"}
            logger.error("[pet] 余额工作线程异常: %s", exc)
        finally:
            self._balance_busy = False
        self._signals.balance_ready.emit(payload, manual)

    def _get_balance_payload(self) -> dict:
        """组合余额 + 记账 + 今日已用（对应原项目 getBalancePayload）。"""
        payload = self.balance.get_balance()
        if not payload.get("ok"):
            return payload
        balance_val = float(payload["totalBalance"])
        currency = payload["currency"]
        led = ledger.record_usage(balance_val, currency, self._ledger_path)
        # 每轮消耗降级：余额更新驱动余额差值估算。
        self.turn_cost.on_balance_update(balance_val)
        payload["isPeak"] = pricing.is_peak_time(time.time())
        mode = self._config.get("usageMode", "ledger")
        if mode == "ledger":
            payload["todayUsage"] = led.get("todayUsage", 0)
            payload["usageMode"] = "ledger"
            return payload
        token = self._platform_token
        if token:
            result = usage.fetch_usage(token)
            if isinstance(result.get("amount"), (int, float)):
                payload["todayUsage"] = result["amount"]
                payload["usageMode"] = "token"
                return payload
        payload["todayUsage"] = led.get("todayUsage", 0)
        payload["usageMode"] = "ledger"
        return payload

    def _on_balance_ready(self, payload: dict, manual: bool) -> None:
        """UI 线程：处理余额结果，更新状态与动画。"""
        if payload.get("ok"):
            nb = float(payload["totalBalance"])
            nc = str(payload.get("currency") or "CNY")
            changed = self._balance is not None and (nb != self._balance or nc != self._currency)
            currency_changed = self._currency is not None and nc != self._currency
            self._balance = nb
            self._currency = nc
            self._message = ""
            self._today_usage = payload.get("todayUsage")
            self._is_peak = bool(payload.get("isPeak"))
            if changed and not currency_changed:
                if manual:
                    self._roll_to(nb, nc)
                    self._status = "ok"
                    self._render()
                else:
                    # 自动刷新且余额变化：弹气泡 + 延迟数字滚动。
                    self._show_bubble()
                    self._status = "changing"
                    QTimer.singleShot(CHANGE_DELAY_MS, lambda: self._roll_to(nb, nc))
                    QTimer.singleShot(CHANGE_DELAY_MS + CHANGE_SETTLE_MS, self._finish_change)
            else:
                if self._shown is None:
                    self._shown = nb
                self._status = "ok"
                self._render()
        else:
            self._status = "error"
            self._message = str(payload.get("error") or "获取失败")
            self._render()

    def _roll_to(self, target: float, currency: str) -> None:
        """数字滚动动画（从当前显示值滚到目标值）。"""
        self.bubble.roll_amount(self._shown, target, currency)
        self._shown = target

    def _finish_change(self) -> None:
        if self._status == "changing":
            self._status = "ok"
            self._render()

    def _render(self) -> None:
        """把余额 / 用量状态渲染到气泡（消耗泡泡显示期间不覆盖）。"""
        if self._cost_bubble_active:
            return
        currency = self._currency or "CNY"
        if self._status == "error":
            amount = self._shown
            hint = (self._message or "获取失败 · 点击重试")[:14]
        elif self._balance is None:
            amount = self._shown
            hint = "加载中…"
        else:
            amount = self._shown if self._shown is not None else self._balance
            hint = "今日已用 " + self._fmt(self._today_usage, currency)
        self.bubble.set_normal(amount, currency, hint)

    @staticmethod
    def _fmt(amount, currency: str) -> str:
        if amount is None:
            return "--"
        try:
            fixed = f"{float(amount):.2f}"
        except (TypeError, ValueError):
            fixed = "--"
        return f"¥ {fixed}" if currency == "CNY" else f"{fixed} {currency}"

    # ------------------------------------------------------------------ #
    # 每轮消耗
    # ------------------------------------------------------------------ #
    def _on_turn_cost_ready(self, amount: float) -> None:
        self._show_cost_bubble(amount)

    def _turn_poll_loop(self) -> None:
        """后台线程：每秒轮询一次 DSH 每轮消耗接口。"""
        logger.info("[pet] 每轮消耗轮询线程已启动")
        while not self._stop_event.is_set():
            try:
                self.turn_cost.poll_last_turn()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[pet] 每轮消耗轮询异常: %s", exc)
            self._stop_event.wait(1.0)

    # ------------------------------------------------------------------ #
    # 菜单
    # ------------------------------------------------------------------ #
    def _wire_menu(self) -> None:
        """连接菜单信号。"""
        self.menu.scale_changed.connect(self._on_scale)
        self.menu.sound_set_changed.connect(self._on_sound_set)
        self.menu.volume_changed.connect(self._on_volume)
        self.menu.usage_mode_changed.connect(self._on_usage_mode)
        self.menu.peak_mode_changed.connect(self._on_peak_mode)
        self.menu.bubble_on_changed.connect(self._on_bubble_on)
        self.menu.idle_talk_on_changed.connect(self._on_idle_talk_on)
        self.menu.turn_cost_on_changed.connect(self._on_turn_cost_on)
        self.menu.turn_cost_close_changed.connect(self._on_turn_cost_close)
        self.menu.scroll_gap_on_changed.connect(self._on_scroll_gap_on)
        self.menu.scroll_gap_changed.connect(self._on_scroll_gap_changed)
        self.menu.autostart_on_changed.connect(self._on_autostart_on)
        self.menu.quit_requested.connect(QApplication.instance().quit)
        self.menu.turn_cost_close_spin.setEnabled(self._turn_cost_on)

    def _toggle_menu(self) -> None:
        self._menu_open = not self._menu_open
        if self._menu_open:
            self._position_menu()
            self.menu.show()
            self.menu.raise_()
            self.menu.activateWindow()
        else:
            self.menu.hide()
        self._update_menu_btn_visibility()

    def _position_menu(self) -> None:
        """把菜单定位到三点按钮上方（右吸附贴右上角，左吸附贴左上角）。"""
        self.menu.adjustSize()
        btn_global = self.menu_button.mapToGlobal(QPoint(0, 0))
        menu_size = self.menu.sizeHint()
        x = btn_global.x() if self._mirrored else btn_global.x() + self.menu_button.width() - menu_size.width()
        y = btn_global.y() - menu_size.height() - 4
        area = self._work_area()
        x = max(area.left(), min(x, area.right() - menu_size.width()))
        y = max(area.top(), y)
        self.menu.move(x, y)

    def _on_scale(self, scale: float) -> None:
        self._apply_scale(scale)
        self._config.set("scale", scale)

    def _on_sound_set(self, sound_set: str) -> None:
        self.sound.set_sound_set(sound_set)
        self._config.set("soundSet", sound_set)

    def _on_volume(self, vol: float) -> None:
        self.sound.set_volume(vol)
        self.sound.set_enabled(vol > 0)
        self._config.set("vol", vol)
        self._config.set("sound", vol > 0)

    def _on_usage_mode(self, mode: str) -> None:
        self._config.set("usageMode", mode)
        logger.info("[pet] 用量模式切换为 %s", mode)
        self._refresh_balance_async(False)

    def _on_peak_mode(self, mode: str) -> None:
        self._peak_mode = mode
        self._config.set("peakMode", mode)

    def _on_bubble_on(self, on: bool) -> None:
        self._bubble_on = on
        self._config.set("bubbleOn", on)
        if not on:
            self._hide_cost_bubble()

    def _on_idle_talk_on(self, on: bool) -> None:
        """闲置对话开关：开启时重新排定计时，关闭时停止并收起当前俏皮话。"""
        self._idle_talk_on = on
        self._config.set("idleTalkOn", on)
        if on:
            self._arm_idle_talk()
        else:
            self._idle_talk_timer.stop()
            if self._bubble_random_active:
                self._hide_bubble()

    def _on_autostart_on(self, on: bool) -> None:
        """开机自启开关：写入 / 删除注册表 Run 项并持久化。"""
        self._autostart_on = on
        self._config.set("autostartOn", on)
        if not autostart.set_enabled(on):
            logger.warning("[pet] 开机自启设置未生效，请检查注册表权限")

    def _on_turn_cost_on(self, on: bool) -> None:
        self._turn_cost_on = on
        self._config.set("turnCostOn", on)
        self.menu.turn_cost_close_spin.setEnabled(on)
        if not on:
            self._hide_cost_bubble()

    def _on_turn_cost_close(self, seconds: int) -> None:
        self._config.set("turnCostCloseMs", max(0, int(seconds)) * 1000)

    def _on_scroll_gap_on(self, on: bool) -> None:
        """桌面端无滚动条，「避让滚动条」开关仅持久化、不影响窗口贴边（与原项目 UI 一致）。"""
        self._config.set("scrollGapOn", on)

    def _on_scroll_gap_changed(self, px: int) -> None:
        self._config.set("scrollGapPx", max(0, int(px)))

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:
        self._stop_event.set()
        self._save_pos()
        self._config.save()
        self.menu.close()
        logger.info("[pet] 桌宠已退出")
        super().closeEvent(event)
