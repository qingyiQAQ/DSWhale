"""每轮对话消耗监控模块。

对应原项目「每轮对话消耗统计」功能，但桌面端无法直接监听 DSH 会话事件流，因此采用
用户确认的策略：

1. **优先**：轮询 DSH 本地服务 ``{dshServer}/dsh-whale/last-turn.json``（原插件暴露的
   接口），拿到新 ``seq`` 即代表一轮对话结算完成，100% 与原网页版一致。
2. **失败降级**：当 DSH 本地服务不可达（未运行 / 未装插件）时，自动切换为
   「余额差值」模式——每次观测到余额下降，把下降额度当作上一轮消耗弹出。

两种模式的金额统一通过 ``on_turn_cost(amount)`` 回调抛给 UI 层。
"""

from __future__ import annotations

import json
import logging

from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

LAST_TURN_PATH = "/dsh-whale/last-turn.json"
POLL_TIMEOUT_SEC = 3.0


class TurnCostMonitor:
    """每轮消耗监控器（含 DSH 轮询与余额差值降级）。"""

    def __init__(self, server_url: str, on_turn_cost: Callable[[float], None]):
        self.server_url = (server_url or "http://127.0.0.1:3080").rstrip("/")
        self.on_turn_cost = on_turn_cost

        self._last_seq = 0
        self._aligned = False
        # 是否处于余额差值降级模式。
        self._fallback = False
        self._fallback_logged = False
        self._last_balance: Optional[float] = None

    # ------------------------------------------------------------------ #
    # DSH 轮询
    # ------------------------------------------------------------------ #
    def poll_last_turn(self) -> None:
        """轮询一次 DSH last-turn 接口（由 UI 定时器每秒调用，已在后台线程）。"""
        url = self.server_url + LAST_TURN_PATH
        try:
            data = _http_get_json(url, POLL_TIMEOUT_SEC)
        except (HTTPError, URLError, OSError, ValueError) as exc:
            self._enter_fallback(exc)
            return

        # 接口可达 -> 恢复正常模式。
        if self._fallback:
            self._exit_fallback()

        if not isinstance(data, dict) or not data.get("ok"):
            return
        seq = data.get("seq")
        if not isinstance(seq, (int, float)):
            return

        if not self._aligned:
            # 首次拿到数据只对齐 seq，不弹历史轮次。
            self._last_seq = int(seq)
            self._aligned = True
            logger.info("[turn_cost] 已对齐 DSH 每轮消耗 seq=%d", self._last_seq)
            return

        if seq > self._last_seq:
            self._last_seq = int(seq)
            amount = data.get("amount")
            if amount is not None:
                try:
                    self.on_turn_cost(float(amount))
                except (TypeError, ValueError):
                    pass

    # ------------------------------------------------------------------ #
    # 余额差值降级
    # ------------------------------------------------------------------ #
    def on_balance_update(self, new_balance: Optional[float]) -> None:
        """在余额刷新后调用；降级模式下用余额下降差额估算上一轮消耗。"""
        if self._fallback and new_balance is not None:
            if self._last_balance is not None and new_balance < self._last_balance:
                delta = self._last_balance - new_balance
                logger.info("[turn_cost] 降级模式：余额下降 ¥%.4f 视为上一轮消耗", delta)
                self.on_turn_cost(delta)
        self._last_balance = new_balance

    # ------------------------------------------------------------------ #
    # 模式切换
    # ------------------------------------------------------------------ #
    def _enter_fallback(self, exc: Exception) -> None:
        if not self._fallback:
            self._fallback = True
            self._fallback_logged = True
            logger.warning(
                "[turn_cost] DSH 本地服务不可达（%s），每轮消耗降级为余额差值模式", str(exc)[:120]
            )

    def _exit_fallback(self) -> None:
        self._fallback = False
        self._fallback_logged = False
        logger.info("[turn_cost] DSH 本地服务恢复，每轮消耗回到精确模式")

    @property
    def is_fallback(self) -> bool:
        """是否处于余额差值降级模式。"""
        return self._fallback


def _http_get_json(url: str, timeout: float) -> Any:
    """简单的 GET + JSON 解析（仅用于本地 DSH 服务，无需自定义头）。"""
    request = Request(url)
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body.decode("utf-8"))
