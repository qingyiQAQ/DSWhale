"""汉堡菜单。

一个独立置顶的无边框面板，点击鲸鱼右上角的三点按钮时在按钮上方弹出。包含以下设置行
（与原网页挂件的菜单一一对应）：

1. 大小：滑块（0.6–2.5） + 数字框（1–20，线性映射）。
2. 音效：下拉框（小黄鸭 / 音效1）。
3. 音量：滑块（0–1） + 百分比文本。
4. 用量：下拉框（小鲸鱼记账 / 实时·令牌）。
5. 峰谷：下拉框（默认 / 梁文峰谷 / !?强强?!）。
6. 气泡：开关。
7. 每轮消耗提示：开关 + 自动关闭秒数。
8. 避让滚动条：开关 + 宽度（桌面端无滚动条，仅 UI 一致 + 持久化）。
另附「退出桌宠」按钮（桌面端扩展）。

所有控件的变化通过 Qt 信号抛给主窗口处理并持久化。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# 样式常量。
ACCENT = "#203170"
# 面板背景/边框由 paintEvent 手动绘制（顶层 QWidget 的样式表 background 不生效），此处仅设子控件样式。
PANEL_STYLE = """
QLabel { color: #203170; font-size: 12px; }
QSlider::groove:horizontal { height: 4px; background: rgba(32, 49, 112, 0.2); border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #203170; }
QComboBox {
    border: 1px solid rgba(32, 49, 112, 0.4);
    border-radius: 6px;
    padding: 2px 6px;
    color: #203170;
    background: rgba(32, 49, 112, 0.08);
    font-size: 12px;
}
QComboBox:hover { background: rgba(32, 49, 112, 0.16); }
QComboBox QAbstractItemView {
    background: #ffffff;
    color: #203170;
    border: 1px solid rgba(32, 49, 112, 0.4);
    border-radius: 6px;
    selection-background-color: rgba(32, 49, 112, 0.15);
    selection-color: #203170;
    outline: none;
}
QSpinBox {
    border: 1px solid rgba(32, 49, 112, 0.4);
    border-radius: 6px;
    padding: 2px 4px;
    color: #203170;
    background: #ffffff;
    font-size: 12px;
}
QSpinBox:disabled { color: rgba(32, 49, 112, 0.4); background: rgba(32, 49, 112, 0.06); }
QCheckBox { color: #203170; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QPushButton {
    border: 1px solid rgba(32, 49, 112, 0.4);
    border-radius: 6px;
    padding: 6px 8px;
    color: #203170;
    background: rgba(32, 49, 112, 0.08);
    font-size: 12px;
}
QPushButton:hover {
    background: #e0433f;
    border-color: #e0433f;
    color: #ffffff;
}
QPushButton:pressed {
    background: #c93430;
    border-color: #c93430;
    color: #ffffff;
}
"""

MIN_SCALE = 0.6
MAX_SCALE = 2.5
SCALE_STEP = 0.1


def scale_to_index(scale: float) -> int:
    """scale(0.6–2.5) -> 索引(0–19)。"""
    return int(round((float(scale) - MIN_SCALE) / SCALE_STEP))


def index_to_scale(index: int) -> float:
    """索引(0–19) -> scale(0.6–2.5)。"""
    return round(MIN_SCALE + int(index) * SCALE_STEP, 2)


class MenuWidget(QWidget):
    """汉堡菜单面板。"""

    scale_changed = Signal(float)
    sound_set_changed = Signal(str)
    volume_changed = Signal(float)
    usage_mode_changed = Signal(str)
    peak_mode_changed = Signal(str)
    bubble_on_changed = Signal(bool)
    idle_talk_on_changed = Signal(bool)
    turn_cost_on_changed = Signal(bool)
    turn_cost_close_changed = Signal(int)
    scroll_gap_on_changed = Signal(bool)
    scroll_gap_changed = Signal(int)
    autostart_on_changed = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("dshwMenu")
        self.setStyleSheet(PANEL_STYLE)

        self._build()

    def paintEvent(self, event) -> None:
        """手动绘制白色圆角背景 + 边框。

        顶层 QWidget 的样式表 background 不会真正绘制（WA_TranslucentBackground 下尤其如此），
        因此这里显式画布背景，保证面板白底、圆角外透明。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(32, 49, 112, 89), 1.0))
        painter.drawRoundedRect(rect, 10, 10)

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(5)  # 行间距 5px（对齐原项目 .dshwv-menu-row 的 margin）。

        # 1. 大小。
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(0, 19)
        self.scale_slider.setValue(scale_to_index(1.5))
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 20)
        self.scale_spin.setValue(10)
        self.scale_spin.setFixedWidth(44)
        self.scale_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.scale_slider.valueChanged.connect(self._on_scale_slider)
        self.scale_spin.valueChanged.connect(self._on_scale_spin)
        root.addLayout(self._row("大小", self.scale_slider, self.scale_spin))

        # 2. 音效。
        self.sound_combo = QComboBox()
        self.sound_combo.addItem("小黄鸭", "duck")
        self.sound_combo.addItem("音效1", "fx1")
        self.sound_combo.currentIndexChanged.connect(self._on_sound_set)
        root.addLayout(self._row("音效", self.sound_combo))

        # 3. 音量（0–1，步长 0.05，用 0–20 整数表示）。
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 20)
        self.volume_slider.setValue(18)  # 18 * 0.05 = 0.9
        self.volume_label = QLabel("90%")
        self.volume_label.setFixedWidth(44)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume_slider.valueChanged.connect(self._on_volume)
        root.addLayout(self._row("音量", self.volume_slider, self.volume_label))

        # 4. 用量。
        self.usage_combo = QComboBox()
        self.usage_combo.addItem("小鲸鱼记账 (推荐)", "ledger")
        self.usage_combo.addItem("实时·令牌 (用法：去问dsh)", "token")
        self.usage_combo.currentIndexChanged.connect(self._on_usage_mode)
        root.addLayout(self._row("用量", self.usage_combo))

        # 5. 峰谷。
        self.peak_combo = QComboBox()
        self.peak_combo.addItem("默认", "default")
        self.peak_combo.addItem("梁文峰谷", "liangwen")
        self.peak_combo.addItem("!?强强?!", "qiangqiang")
        self.peak_combo.currentIndexChanged.connect(self._on_peak_mode)
        root.addLayout(self._row("峰谷", self.peak_combo))

        # 6. 气泡（无文字开关 + 独立 label，对齐原项目）。
        self.bubble_check = QCheckBox()
        self.bubble_check.setChecked(True)
        self.bubble_check.setToolTip("开启/关闭思考气泡")
        self.bubble_check.toggled.connect(self.bubble_on_changed)
        root.addLayout(self._pack_row("气泡", self.bubble_check))

        # 6b. 闲置对话（气泡选项下的子开关）：未点击时每 10-20 秒随机弹一句俏皮话。
        self.idle_talk_check = QCheckBox()
        self.idle_talk_check.setChecked(True)
        self.idle_talk_check.setToolTip("未点击时每 10-20 秒随机弹一句俏皮话，3 秒后消失")
        self.idle_talk_check.toggled.connect(self.idle_talk_on_changed)
        root.addLayout(self._pack_row("闲置对话", self.idle_talk_check))

        # 7. 每轮消耗提示 + 自动关闭 + 秒。
        self.turn_cost_check = QCheckBox()
        self.turn_cost_check.setChecked(True)
        self.turn_cost_check.setToolTip("每轮对话结束后自动显示本轮消耗金额")
        self.turn_cost_check.toggled.connect(self.turn_cost_on_changed)
        self.turn_cost_close_spin = QSpinBox()
        self.turn_cost_close_spin.setRange(0, 3600)
        self.turn_cost_close_spin.setValue(5)
        self.turn_cost_close_spin.setFixedWidth(44)
        self.turn_cost_close_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.turn_cost_close_spin.setToolTip("填 0 表示不自动关闭，需手动点击关闭")
        self.turn_cost_close_spin.valueChanged.connect(self.turn_cost_close_changed)
        root.addLayout(self._pack_row(
            "每轮消耗提示", self.turn_cost_check,
            "自动关闭", self.turn_cost_close_spin, "秒",
        ))

        # 分割线（对齐原项目 .dshwv-menu-sep）。
        root.addWidget(self._separator())

        # 8. 避让滚动条 + 宽度 + px（桌面端无滚动条，仅 UI 一致 + 持久化）。
        self.scroll_gap_check = QCheckBox()
        self.scroll_gap_check.setChecked(False)
        self.scroll_gap_check.setToolTip("开启后挂件右侧按设定像素避开滚动条；关闭则贴边（盖住滚动条）")
        self.scroll_gap_check.toggled.connect(self._on_scroll_gap_on)
        self.scroll_gap_spin = QSpinBox()
        self.scroll_gap_spin.setRange(0, 3600)
        self.scroll_gap_spin.setValue(17)
        self.scroll_gap_spin.setFixedWidth(44)
        self.scroll_gap_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.scroll_gap_spin.setEnabled(False)  # 默认避让关 → 宽度不可改，勾选后启用
        self.scroll_gap_spin.setToolTip("避让滚动条的像素宽度，填 0 表示贴边")
        self.scroll_gap_spin.valueChanged.connect(self.scroll_gap_changed)
        root.addLayout(self._pack_row(
            "避让滚动条", self.scroll_gap_check,
            "宽度", self.scroll_gap_spin, "px",
        ))

        # 9. 开机自启（桌面端扩展：登录 Windows 时自动无控制台启动桌宠）。
        self.autostart_check = QCheckBox()
        self.autostart_check.setChecked(False)
        self.autostart_check.setToolTip("勾选后登录 Windows 自动启动桌宠（写入注册表 Run 项）")
        self.autostart_check.toggled.connect(self.autostart_on_changed)
        root.addLayout(self._pack_row("开机自启", self.autostart_check))

        # 退出按钮（桌面端扩展：提供关闭桌宠的入口）。
        self.quit_button = QPushButton("退出桌宠")
        self.quit_button.clicked.connect(self.quit_requested)
        root.addWidget(self.quit_button)

    def _row(self, label: str, *controls) -> QHBoxLayout:
        """构造一行（label + 控件们）；首个控件拉伸填充（对齐原项目 flex:1）。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QLabel(label))
        for i, control in enumerate(controls):
            row.addWidget(control, 1 if i == 0 else 0)
        return row

    def _pack_row(self, *items) -> QHBoxLayout:
        """构造左对齐的紧凑行（label 与控件相邻、无拉伸，对齐原项目 checkbox 行）。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for item in items:
            if isinstance(item, str):
                row.addWidget(QLabel(item))
            else:
                row.addWidget(item)
        row.addStretch(1)
        return row

    def _separator(self) -> QFrame:
        """一条水平分割线（对齐原项目 .dshwv-menu-sep）。"""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(32, 49, 112, 0.25); border: none;")
        return sep

    # ------------------------------------------------------------------ #
    # 控件回调
    # ------------------------------------------------------------------ #
    def _on_scale_slider(self, index: int) -> None:
        scale = index_to_scale(index)
        if self.scale_spin.value() != scale_to_index(scale) + 1:
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(scale_to_index(scale) + 1)
            self.scale_spin.blockSignals(False)
        self.scale_changed.emit(scale)

    def _on_scale_spin(self, value: int) -> None:
        scale = index_to_scale(value - 1)
        if self.scale_slider.value() != value - 1:
            self.scale_slider.blockSignals(True)
            self.scale_slider.setValue(value - 1)
            self.scale_slider.blockSignals(False)
        self.scale_changed.emit(scale)

    def _on_sound_set(self) -> None:
        self.sound_set_changed.emit(self.sound_combo.currentData())

    def _on_volume(self, value: int) -> None:
        vol = value * 0.05
        self.volume_label.setText(f"{value * 5}%")
        self.volume_changed.emit(vol)

    def _on_usage_mode(self) -> None:
        self.usage_mode_changed.emit(self.usage_combo.currentData())

    def _on_peak_mode(self) -> None:
        self.peak_mode_changed.emit(self.peak_combo.currentData())

    def _on_scroll_gap_on(self, on: bool) -> None:
        """勾选「避让滚动条」时启用宽度框，并抛出信号持久化。"""
        self.scroll_gap_spin.setEnabled(on)
        self.scroll_gap_on_changed.emit(on)

    # ------------------------------------------------------------------ #
    # 外部接口
    # ------------------------------------------------------------------ #
    def populate(self, settings: dict) -> None:
        """按设置填充控件（阻断信号，避免触发回调）。"""
        self.scale_slider.blockSignals(True)
        self.scale_spin.blockSignals(True)
        self.sound_combo.blockSignals(True)
        self.volume_slider.blockSignals(True)
        self.usage_combo.blockSignals(True)
        self.peak_combo.blockSignals(True)
        self.bubble_check.blockSignals(True)
        self.idle_talk_check.blockSignals(True)
        self.turn_cost_check.blockSignals(True)
        self.turn_cost_close_spin.blockSignals(True)
        self.scroll_gap_check.blockSignals(True)
        self.scroll_gap_spin.blockSignals(True)
        self.autostart_check.blockSignals(True)

        scale = float(settings.get("scale", 1.5))
        index = scale_to_index(scale)
        self.scale_slider.setValue(index)
        self.scale_spin.setValue(index + 1)

        sound_set = settings.get("soundSet", "duck")
        self._select_combo(self.sound_combo, sound_set if sound_set in ("duck", "fx1") else "duck")

        vol = float(settings.get("vol", 0.9))
        vol = max(0.0, min(1.0, vol))
        self.volume_slider.setValue(int(round(vol * 20)))
        self.volume_label.setText(f"{int(round(vol * 100))}%")

        usage_mode = settings.get("usageMode", "ledger")
        self._select_combo(self.usage_combo, usage_mode if usage_mode in ("ledger", "token") else "ledger")

        peak_mode = settings.get("peakMode", "default")
        self._select_combo(
            self.peak_combo,
            peak_mode if peak_mode in ("default", "liangwen", "qiangqiang") else "default",
        )

        self.bubble_check.setChecked(settings.get("bubbleOn", True))
        self.idle_talk_check.setChecked(settings.get("idleTalkOn", True))
        self.turn_cost_check.setChecked(settings.get("turnCostOn", True))
        close_ms = int(settings.get("turnCostCloseMs", 5000) or 0)
        self.turn_cost_close_spin.setValue(max(0, close_ms // 1000))

        scroll_gap_on = bool(settings.get("scrollGapOn", False))
        self.scroll_gap_check.setChecked(scroll_gap_on)
        self.scroll_gap_spin.setValue(max(0, int(settings.get("scrollGapPx", 17) or 0)))
        self.scroll_gap_spin.setEnabled(scroll_gap_on)

        self.autostart_check.setChecked(settings.get("autostartOn", False))

        self.scale_slider.blockSignals(False)
        self.scale_spin.blockSignals(False)
        self.sound_combo.blockSignals(False)
        self.volume_slider.blockSignals(False)
        self.usage_combo.blockSignals(False)
        self.peak_combo.blockSignals(False)
        self.bubble_check.blockSignals(False)
        self.idle_talk_check.blockSignals(False)
        self.turn_cost_check.blockSignals(False)
        self.turn_cost_close_spin.blockSignals(False)
        self.scroll_gap_check.blockSignals(False)
        self.scroll_gap_spin.blockSignals(False)
        self.autostart_check.blockSignals(False)

    @staticmethod
    def _select_combo(combo: QComboBox, data: str) -> None:
        """按 data 选中下拉项。"""
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)
