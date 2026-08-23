"""对话气泡渲染器。

把原网页挂件的气泡（白色 SVG 椭圆 + 尾巴 + 两个小气泡）与三行文字 / gif 动图统一
绘制。本类不是 QWidget，而是一个 QObject 状态机 + 渲染器，由主窗口（PetWindow）
在 paintEvent 中调用 :meth:`paint`，鼠标命中检测用 :meth:`hit_test`。

几何参数与原项目 widget.js / whale-widget-prompt.md 完全一致：
- SVG 画布 1026×700，主椭圆中心 (454,247) rx=373 ry=232，描边 #203170 宽 18。
- 文字块居中于 (44.25% 宽, 38% 高)，左吸附镜像时水平翻转至 55.75%。
- 字号按 --dshw-u = base/1026 联动：A=66、B=128、P=104、C=56。
"""

from __future__ import annotations

import logging
import math

from collections import namedtuple
from typing import Any, Optional

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QObject,
    QPointF,
    Property,
    QRectF,
    Qt,
    QVariantAnimation,
    QPropertyAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMovie, QPainter
from PySide6.QtSvg import QSvgRenderer

logger = logging.getLogger(__name__)

# 气泡 SVG（与网页版完全一致）。
SVG_BUBBLE = (
    '<svg viewBox="0 0 1026 700" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
    '<path fill="#FFFFFF" stroke="#203170" stroke-width="18" stroke-linejoin="round" stroke-linecap="round" '
    'd="M 827 248 A 373 232 0 1 0 81 246 A 373 232 0 0 0 301 465 A 57 32 10 0 0 413 484 A 373 232 0 0 0 827 248 Z"/>'
    '<ellipse cx="352" cy="561" rx="37.5" ry="26" fill="#FFFFFF" stroke="#203170" stroke-width="18"/>'
    '<ellipse cx="442" cy="646" rx="24.5" ry="18" fill="#FFFFFF" stroke="#203170" stroke-width="18"/>'
    "</svg>"
)

# 气泡宽高比（1026 宽 : 700 高）。
BUBBLE_ASPECT = 700.0 / 1026.0

# 文字样式档：size 为 u 单位（= base/1026），weight 为 QFont 字重。
TEXT_STYLES = {
    "A": {"size": 66, "weight": QFont.Weight.DemiBold, "color": "#536ba9"},
    "B": {"size": 128, "weight": QFont.Weight.ExtraBold, "color": "#536ba9"},
    "P": {"size": 104, "weight": QFont.Weight.ExtraBold, "color": "#536ba9"},
    "C": {"size": 56, "weight": QFont.Weight.Normal, "color": "#9fb0d9"},
}

# 文字块中心（未镜像 / 镜像）。
TEXT_CENTER_X = {"normal": 0.4425, "mirrored": 0.5575}
TEXT_CENTER_Y = 0.38
WRAP_MAX_WIDTH_U = 560

# 单行槽位：text 文本、style 样式档(A/B/P/C)、color 覆盖色(空=默认)、wrap 是否自动换行。
Slot = namedtuple("Slot", "text style color wrap")

FONT_FAMILY = "Microsoft YaHei"


def _make_font(size_px: float, weight) -> QFont:
    """按像素大小与字重创建字体。"""
    font = QFont(FONT_FAMILY)
    font.setPixelSize(max(1, int(round(size_px))))
    font.setWeight(weight)
    return font


class BubbleRenderer(QObject):
    """气泡状态机与渲染器。"""

    # 需要主窗口重绘时发出。
    changed = Signal()

    def __init__(self, gif_path: str, parent: Optional[QObject] = None):
        super().__init__(parent)

        # SVG 气泡渲染器。
        self._svg = QSvgRenderer()
        self._svg.load(QByteArray(SVG_BUBBLE.encode("utf-8")))

        # gif 动图（可选，缺失时降级为文字）。
        self._movie = QMovie(gif_path)
        self.gif_available = self._movie.isValid()
        if self.gif_available:
            self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self._movie.frameChanged.connect(lambda _frame: self.changed.emit())
            self._movie.start()
        else:
            logger.warning("[bubble] gif 加载失败，随机台词 gif 将降级为文字: %s", gif_path)

        # 动画状态。
        self._opacity = 0.0
        self._bubble_scale = 0.7
        self._text_opacity = 1.0
        self._anims: list = []  # 保持动画引用，防止被 GC

        # 内容状态。
        self._mode = "normal"           # normal / random / cost
        self._amount_value: Optional[float] = None
        self._amount_currency = "CNY"
        self._hint_text = ""
        self._random_slots: list[Optional[Slot]] = [None, None, None]
        self._gif_active = False

        self._is_open = False

    # ------------------------------------------------------------------ #
    # 动画属性（供 QPropertyAnimation 驱动）
    # ------------------------------------------------------------------ #
    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float) -> None:
        self._opacity = float(value)
        self.changed.emit()

    opacity = Property(float, _get_opacity, _set_opacity)

    def _get_bubble_scale(self) -> float:
        return self._bubble_scale

    def _set_bubble_scale(self, value: float) -> None:
        self._bubble_scale = float(value)
        self.changed.emit()

    bubble_scale = Property(float, _get_bubble_scale, _set_bubble_scale)

    def _get_text_opacity(self) -> float:
        return self._text_opacity

    def _set_text_opacity(self, value: float) -> None:
        self._text_opacity = float(value)
        self.changed.emit()

    text_opacity = Property(float, _get_text_opacity, _set_text_opacity)

    @property
    def is_open(self) -> bool:
        return self._is_open

    # ------------------------------------------------------------------ #
    # 开合动画
    # ------------------------------------------------------------------ #
    def open(self) -> None:
        """打开气泡（淡入 + 弹性放大）。"""
        self._is_open = True
        self._animate_prop(b"opacity", self._opacity, 1.0, 200, QEasingCurve.Type.InOutCubic)
        self._animate_prop(b"bubble_scale", self._bubble_scale, 1.0, 240, QEasingCurve.Type.OutBack)

    def close(self) -> None:
        """关闭气泡（淡出 + 缩小）。"""
        self._is_open = False
        self._animate_prop(b"opacity", self._opacity, 0.0, 180, QEasingCurve.Type.InCubic)
        self._animate_prop(b"bubble_scale", self._bubble_scale, 0.7, 180, QEasingCurve.Type.InCubic)

    def swap_text(self, apply_fn) -> None:
        """淡出 -> 应用新内容 -> 淡入（用于切换随机台词）。"""
        out = QPropertyAnimation(self, b"text_opacity")
        out.setStartValue(self._text_opacity)
        out.setEndValue(0.0)
        out.setDuration(190)
        out.finished.connect(lambda: self._swap_apply(apply_fn))
        out.start()
        self._anims.append(out)

    def _swap_apply(self, apply_fn) -> None:
        apply_fn()
        back = QPropertyAnimation(self, b"text_opacity")
        back.setStartValue(0.0)
        back.setEndValue(1.0)
        back.setDuration(220)
        back.start()
        self._anims.append(back)

    # ------------------------------------------------------------------ #
    # 内容设置
    # ------------------------------------------------------------------ #
    def set_normal(self, amount_value: Optional[float], currency: str, hint_text: str) -> None:
        """设置正常内容（余额 + 今日已用）。"""
        self._mode = "normal"
        self._amount_value = amount_value
        self._amount_currency = currency or "CNY"
        self._hint_text = hint_text
        self._gif_active = False
        self.changed.emit()

    def set_cost(self, amount_value: float, currency: str) -> None:
        """设置「上一轮消耗」内容（第二行红色金额）。"""
        self._mode = "cost"
        self._amount_value = amount_value
        self._amount_currency = currency or "CNY"
        self._gif_active = False
        self.changed.emit()

    def set_random(self, lines_dict: dict[str, Any], gif_ok: bool) -> None:
        """设置随机台词内容（三行文字或 gif）。"""
        self._mode = "random"
        self._gif_active = bool(lines_dict.get("gif")) and gif_ok
        lines = lines_dict.get("lines")
        slots: list[Optional[Slot]] = []
        if isinstance(lines, list):
            for line in lines:
                if line is None:
                    slots.append(None)
                else:
                    text, style, color, wrap = line
                    slots.append(Slot(text, style, color or "", bool(wrap)))
        if len(slots) < 3:
            slots.extend([None] * (3 - len(slots)))
        self._random_slots = slots
        self.changed.emit()

    def set_amount(self, amount_value: float, currency: str) -> None:
        """直接设置金额（不带动画）。"""
        self._amount_value = amount_value
        self._amount_currency = currency or "CNY"
        self.changed.emit()

    def roll_amount(self, from_val, to_val: float, currency: str, duration_ms: int = 700) -> None:
        """余额数字滚动动画（三次方缓出）。"""
        self._amount_currency = currency or "CNY"
        if from_val is None or not (isinstance(from_val, (int, float)) and math.isfinite(from_val)):
            from_val = to_val
        anim = QVariantAnimation(self)
        anim.setStartValue(float(from_val))
        anim.setEndValue(float(to_val))
        anim.setDuration(duration_ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self._on_roll_value)
        anim.start()
        self._anims.append(anim)

    def _on_roll_value(self, value: float) -> None:
        self._amount_value = float(value)
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paint(self, painter: QPainter, width: int, height: int, mirrored: bool) -> None:
        """在 (0,0,width,height) 区域绘制气泡、文字与 gif。"""
        if width <= 0 or height <= 0:
            return
        w = float(width)
        h = float(height)

        # 气泡 SVG：弹性缩放 + 水平镜像 + 整体透明度。
        painter.save()
        painter.translate(w / 2, h / 2)
        s = self._bubble_scale
        painter.scale(-s if mirrored else s, s)
        painter.translate(-w / 2, -h / 2)
        painter.setOpacity(self._opacity)
        self._svg.render(painter, QRectF(0, 0, w, h))
        painter.restore()

        # 文字（独立于镜像，保持可读）。
        if self._opacity > 0.01 and self._text_opacity > 0.01:
            painter.save()
            painter.setOpacity(self._opacity * self._text_opacity)
            self._draw_text(painter, w, h, mirrored)
            painter.restore()

        # gif 动图。
        if self._gif_active and self.gif_available and self._opacity > 0.01:
            painter.save()
            painter.setOpacity(self._opacity)
            self._draw_gif(painter, w, h, mirrored)
            painter.restore()

    def _draw_text(self, painter: QPainter, w: float, h: float, mirrored: bool) -> None:
        """绘制三行文字（垂直堆叠、水平居中）。"""
        slots = [s for s in self._current_slots() if s is not None]
        if not slots:
            return
        u = w / 1026.0
        text_cx = TEXT_CENTER_X["mirrored" if mirrored else "normal"] * w
        text_cy = TEXT_CENTER_Y * h

        # 先量出每行高度，用于垂直居中堆叠。
        blocks = []
        for slot in slots:
            style = TEXT_STYLES[slot.style]
            font = _make_font(style["size"] * u, style["weight"])
            fm = QFontMetrics(font)
            if slot.wrap:
                max_w = int(WRAP_MAX_WIDTH_U * u)
                rect = fm.boundingRect(
                    QRectF(0, 0, max_w, 10000).toRect(),
                    Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                    slot.text,
                )
                block_h = rect.height()
            else:
                block_h = fm.height()
            blocks.append((slot, font, block_h))

        gap = int(round(9 * u)) if len(blocks) > 1 else 0
        total = sum(block_h for _, _, block_h in blocks) + gap * (len(blocks) - 1)
        y = text_cy - total / 2

        for slot, font, block_h in blocks:
            style = TEXT_STYLES[slot.style]
            painter.setFont(font)
            painter.setPen(QColor(slot.color or style["color"]))
            if slot.wrap:
                max_w = WRAP_MAX_WIDTH_U * u
                rect = QRectF(text_cx - max_w / 2, y, max_w, block_h)
                painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, slot.text)
            else:
                # 不换行文本同样以椭圆中心 text_cx 居中；否则会按整宽居中到 50% 处，偏离椭圆中心。
                painter.drawText(QRectF(text_cx - w / 2, y, w, block_h), Qt.AlignmentFlag.AlignHCenter, slot.text)
            y += block_h + gap

    def _draw_gif(self, painter: QPainter, w: float, h: float, mirrored: bool) -> None:
        """绘制 gif 动图（居中，镜像时水平翻转）。"""
        pm = self._movie.currentPixmap()
        if pm.isNull():
            return
        u = w / 1026.0
        max_w = int(WRAP_MAX_WIDTH_U * u)
        max_h = int(400 * u)
        scaled = pm.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        cx = TEXT_CENTER_X["mirrored" if mirrored else "normal"] * w
        cy = TEXT_CENTER_Y * h
        painter.save()
        painter.translate(cx, cy)
        if mirrored:
            painter.scale(-1, 1)
        painter.drawPixmap(-scaled.width() // 2, -scaled.height() // 2, scaled)
        painter.restore()

    # ------------------------------------------------------------------ #
    # 命中检测
    # ------------------------------------------------------------------ #
    def hit_test(self, x: float, y: float, width: int, height: int, mirrored: bool) -> bool:
        """判断 (x,y)（气泡局部坐标）是否落在气泡内（主椭圆 + 两个小气泡）。"""
        if width <= 0 or height <= 0:
            return False
        nx = x / width * 1026.0
        ny = y / height * 700.0
        if mirrored:
            nx = 1026.0 - nx

        def in_ellipse(px, py, cx, cy, rx, ry) -> bool:
            return ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0

        return (
            in_ellipse(nx, ny, 454, 247, 373, 232)
            or in_ellipse(nx, ny, 352, 561, 37.5, 26)
            or in_ellipse(nx, ny, 442, 646, 24.5, 18)
        )

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _current_slots(self) -> list[Optional[Slot]]:
        """按当前模式解析三行槽位。"""
        if self._mode == "random":
            return self._random_slots
        if self._mode == "cost":
            return [
                Slot("上一轮对话消耗:", "A", "", False),
                Slot(self._amount_text(), "B", "#e0433f", False),
                None,
            ]
        # normal
        return [
            Slot("DeepSeek 余额", "A", "", False),
            Slot(self._amount_text(), "B", "", False),
            Slot(self._hint_text or "", "C", "", False),
        ]

    def _amount_text(self) -> str:
        """格式化金额（None 显示 …，CNY 显示 ¥）。"""
        if self._amount_value is None:
            return "…"
        try:
            fixed = f"{float(self._amount_value):.2f}"
        except (TypeError, ValueError):
            fixed = "--"
        return f"¥ {fixed}" if self._amount_currency == "CNY" else f"{fixed} {self._amount_currency}"

    def _animate_prop(self, prop: bytes, start: float, end: float, duration: int, easing) -> None:
        anim = QPropertyAnimation(self, prop)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setDuration(duration)
        anim.setEasingCurve(easing)
        anim.start()
        self._anims.append(anim)
