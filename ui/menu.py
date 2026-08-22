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

所有控件的变化通过 Qt 信号抛给主窗口处理并持久化。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
PANEL_STYLE = """
QWidget#dshwMenu {
    background: rgba(255, 255, 255, 0.95);
    border: 1px solid rgba(32, 49, 112, 0.35);
    border-radius: 10px;
}
QLabel { color: #203170; font-size: 12px; }
QSlider::groove:horizontal { height: 4px; background: rgba(32, 49, 112, 0.2); border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #203170; }
QComboBox, QSpinBox {
    border: 1px solid rgba(32, 49, 112, 0.4);
    border-radius: 6px;
    padding: 2px 6px;
    color: #203170;
    background: #ffffff;
    font-size: 12px;
}
QCheckBox { color: #203170; font-size: 12px; }
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
    turn_cost_on_changed = Signal(bool)
    turn_cost_close_changed = Signal(int)
    quit_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("dshwMenu")
        self.setStyleSheet(PANEL_STYLE)

        self._build()

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(2)

        # 1. 大小。
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(0, 19)
        self.scale_slider.setValue(scale_to_index(1.5))
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 20)
        self.scale_spin.setValue(10)
        self.scale_slider.valueChanged.connect(self._on_scale_slider)
        self.scale_spin.valueChanged.connect(self._on_scale_spin)
        root.addLayout(self._row("大小", self.scale_slider, self.scale_spin))

        # 2. 音效。
        self.sound_combo = QComboBox()
        self.sound_combo.addItem("小黄鸭", "duck")
        self.sound_combo.addItem("音效1", "fx1")
        self.sound_combo.currentIndexChanged.connect(self._on_sound_set)
        root.addLayout(self._row("音效", self.sound_combo))

        # 3. 音量。
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(90)
        self.volume_label = QLabel("90%")
        self.volume_label.setFixedWidth(40)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume_slider.valueChanged.connect(self._on_volume)
        root.addLayout(self._row("音量", self.volume_slider, self.volume_label))

        # 4. 用量。
        self.usage_combo = QComboBox()
        self.usage_combo.addItem("小鲸鱼记账 (推荐)", "ledger")
        self.usage_combo.addItem("实时·令牌", "token")
        self.usage_combo.currentIndexChanged.connect(self._on_usage_mode)
        root.addLayout(self._row("用量", self.usage_combo))

        # 5. 峰谷。
        self.peak_combo = QComboBox()
        self.peak_combo.addItem("默认", "default")
        self.peak_combo.addItem("梁文峰谷", "liangwen")
        self.peak_combo.addItem("!?强强?!", "qiangqiang")
        self.peak_combo.currentIndexChanged.connect(self._on_peak_mode)
        root.addLayout(self._row("峰谷", self.peak_combo))

        # 6. 气泡。
        self.bubble_check = QCheckBox("气泡")
        self.bubble_check.setChecked(True)
        self.bubble_check.toggled.connect(self.bubble_on_changed)
        root.addLayout(self._row(self.bubble_check))

        # 7. 每轮消耗提示 + 自动关闭。
        self.turn_cost_check = QCheckBox("每轮消耗提示")
        self.turn_cost_check.setChecked(True)
        self.turn_cost_check.toggled.connect(self.turn_cost_on_changed)
        self.turn_cost_close_spin = QSpinBox()
        self.turn_cost_close_spin.setRange(0, 3600)
        self.turn_cost_close_spin.setSuffix(" 秒")
        self.turn_cost_close_spin.setValue(5)
        self.turn_cost_close_spin.valueChanged.connect(self.turn_cost_close_changed)
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(self.turn_cost_check)
        row.addStretch(1)
        row.addWidget(QLabel("自动关闭"))
        row.addWidget(self.turn_cost_close_spin)
        root.addLayout(row)

        # 退出按钮（桌面端扩展：提供关闭桌宠的入口）。
        self.quit_button = QPushButton("退出桌宠")
        self.quit_button.clicked.connect(self.quit_requested)
        root.addWidget(self.quit_button)

    def _row(self, label: str, *controls) -> QHBoxLayout:
        """构造一行（标签 + 控件们）。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(QLabel(label))
        row.addStretch(1)
        for control in controls:
            row.addWidget(control)
        return row

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
        vol = value / 100.0
        self.volume_label.setText(f"{value}%")
        self.volume_changed.emit(vol)

    def _on_usage_mode(self) -> None:
        self.usage_mode_changed.emit(self.usage_combo.currentData())

    def _on_peak_mode(self) -> None:
        self.peak_mode_changed.emit(self.peak_combo.currentData())

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
        self.turn_cost_check.blockSignals(True)
        self.turn_cost_close_spin.blockSignals(True)

        scale = float(settings.get("scale", 1.5))
        index = scale_to_index(scale)
        self.scale_slider.setValue(index)
        self.scale_spin.setValue(index + 1)

        sound_set = settings.get("soundSet", "duck")
        self._select_combo(self.sound_combo, sound_set if sound_set in ("duck", "fx1") else "duck")

        vol = float(settings.get("vol", 0.9))
        vol = max(0.0, min(1.0, vol))
        self.volume_slider.setValue(int(round(vol * 100)))
        self.volume_label.setText(f"{int(round(vol * 100))}%")

        usage_mode = settings.get("usageMode", "ledger")
        self._select_combo(self.usage_combo, usage_mode if usage_mode in ("ledger", "token") else "ledger")

        peak_mode = settings.get("peakMode", "default")
        self._select_combo(
            self.peak_combo,
            peak_mode if peak_mode in ("default", "liangwen", "qiangqiang") else "default",
        )

        self.bubble_check.setChecked(settings.get("bubbleOn", True))
        self.turn_cost_check.setChecked(settings.get("turnCostOn", True))
        close_ms = int(settings.get("turnCostCloseMs", 5000) or 0)
        self.turn_cost_close_spin.setValue(max(0, close_ms // 1000))

        self.scale_slider.blockSignals(False)
        self.scale_spin.blockSignals(False)
        self.sound_combo.blockSignals(False)
        self.volume_slider.blockSignals(False)
        self.usage_combo.blockSignals(False)
        self.peak_combo.blockSignals(False)
        self.bubble_check.blockSignals(False)
        self.turn_cost_check.blockSignals(False)
        self.turn_cost_close_spin.blockSignals(False)

    @staticmethod
    def _select_combo(combo: QComboBox, data: str) -> None:
        """按 data 选中下拉项。"""
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)
