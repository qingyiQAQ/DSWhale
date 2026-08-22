"""音效播放模块。

基于 QtMultimedia 的 QMediaPlayer 播放按压 / 松手音效（mp3）：

- 两套音效：小黄鸭（Ya1/Ya2）、音效1（D1/D2）。
- 音量 0–1，音量 0 时自动视为静音。
- 文件缺失 / 平台不支持时静默降级（与原项目一致，不报错中断）。
- 按压 / 松手时机：短按时松手音效在按压音效末尾前 100ms 起播（重叠）；时长未知时
  退化为按压结束后再播松手音效。

注意：QMediaPlayer 必须在 QApplication 创建之后实例化，故本类由 UI 层在运行时构建。
"""

from __future__ import annotations

import logging
import os

from typing import Optional

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

logger = logging.getLogger(__name__)

# 音效文件名映射：soundSet -> (按压, 松手)。
SOUND_SETS = {
    "duck": ("Ya1.mp3", "Ya2.mp3"),
    "fx1": ("D1.mp3", "D2.mp3"),
}


class SoundPlayer:
    """按压 / 松手音效播放器。"""

    def __init__(self, assets_dir: str, sound_set: str = "duck", volume: float = 0.9, enabled: bool = True):
        self.assets_dir = assets_dir
        self.enabled = enabled
        self._sound_set = sound_set
        self._volume = volume
        self._release_timer: Optional[QTimer] = None
        self._release_on_ended = False

        # 两个独立的播放器：按压与松手音效互不抢断。
        self._press = QMediaPlayer()
        self._release = QMediaPlayer()
        self._press_out = QAudioOutput()
        self._release_out = QAudioOutput()
        self._press.setAudioOutput(self._press_out)
        self._release.setAudioOutput(self._release_out)

        self._press_available = False
        self._release_available = False
        self._apply_sound_set()
        self.set_volume(volume)

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def set_sound_set(self, sound_set: str) -> None:
        """切换音效套（duck / fx1）。"""
        self._sound_set = sound_set if sound_set in SOUND_SETS else "duck"
        self._apply_sound_set()

    def set_volume(self, volume: float) -> None:
        """设置音量（0–1）。"""
        self._volume = max(0.0, min(1.0, float(volume)))
        try:
            self._press_out.setVolume(self._volume)
            self._release_out.setVolume(self._volume)
        except RuntimeError:
            pass

    def set_enabled(self, enabled: bool) -> None:
        """开关声音。"""
        self.enabled = enabled

    def _apply_sound_set(self) -> None:
        """按当前音效套加载 press / release 文件，缺失则标记不可用。"""
        press_name, release_name = SOUND_SETS[self._sound_set]
        self._press_available = self._load_source(self._press, press_name)
        self._release_available = self._load_source(self._release, release_name)
        if not (self._press_available or self._release_available):
            logger.warning("[audio] 音效文件缺失，静默降级为无声: %s", self.assets_dir)

    def _load_source(self, player: QMediaPlayer, filename: str) -> bool:
        """加载音效文件到播放器，成功返回 True。"""
        path = os.path.join(self.assets_dir, filename)
        if not os.path.isfile(path):
            return False
        try:
            player.setSource(QUrl.fromLocalFile(path))
            return True
        except Exception as exc:  # noqa: BLE001 —— 平台不支持时降级
            logger.warning("[audio] 加载音效失败 %s: %s", filename, exc)
            return False

    # ------------------------------------------------------------------ #
    # 播放
    # ------------------------------------------------------------------ #
    def play_press(self) -> None:
        """播放按压音效（从头开始）。"""
        if not self.enabled or not self._press_available:
            return
        try:
            self._press.stop()
            self._press.setPosition(0)
            self._press.play()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[audio] 按压音效播放失败: %s", exc)

    def play_release(self) -> None:
        """播放松手音效（尽量与按压音效末尾重叠 100ms）。"""
        if not self.enabled or not self._release_available:
            return
        # 取消上一次未触发的松手计时。
        if self._release_timer is not None:
            self._release_timer.stop()
            self._release_timer = None

        if self._press.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            duration = self._press.duration()
            if duration > 0:
                # 已知时长：在按压结束前 100ms 起播松手音效（短按重叠效果）。
                remain = duration - self._press.position()
                delay = max(0, remain - 100)
                self._release_timer = QTimer()
                self._release_timer.setSingleShot(True)
                self._release_timer.timeout.connect(self._do_release)
                self._release_timer.start(int(delay))
            else:
                # 时长未知：等按压结束再播松手音效。
                self._release_on_ended = True
                try:
                    self._press.mediaStatusChanged.connect(self._on_press_ended)
                except Exception:  # noqa: BLE001
                    self._do_release()
        else:
            self._do_release()

    def _on_press_ended(self, status) -> None:
        """按压音效结束时（时长未知回退路径）触发松手音效。"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._release_on_ended:
            self._release_on_ended = False
            self._do_release()

    def _do_release(self) -> None:
        """真正执行松手音效播放。"""
        if not self.enabled or not self._release_available:
            return
        try:
            self._release.setPosition(0)
            self._release.play()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[audio] 松手音效播放失败: %s", exc)
