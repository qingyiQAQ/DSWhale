"""余额拉取模块。

移植自原项目 lib/index.js 的 ``fetchBalance`` / ``getBalance`` 逻辑：

- 调 DeepSeek 官方接口 ``GET https://api.deepseek.com/user/balance``。
- 多币种智能选币：优先 CNY 且余额 > 0，其次任意非零项，再退回 CNY 项，最后取第一项
  （接口返回的币种数组顺序不固定，不可直接取 [0]）。
- 重试：网络错误 / 超时 / 5xx 重试 1 次（间隔 500ms）；4xx 不重试。
- 瞬时失败回退：网络 / 超时 / 5xx 且有缓存时返回最近成功值并标记 stale，不闪错误。
- 25 秒内存缓存 + 进行中请求去重（避免手动刷新与定时刷新并发重复请求）。

``get_balance`` 是同步阻塞调用，应由 UI 层放在后台线程中执行（本桌宠用单一后台
工作线程，故并发场景少，去重仅做保守保护）。
"""

from __future__ import annotations

import logging
import threading
import time

from datetime import datetime, timezone
from typing import Any, Optional

from .http_util import fetch_json, HttpError, NetworkError, ParseError

logger = logging.getLogger(__name__)

BALANCE_URL = "https://api.deepseek.com/user/balance"
BALANCE_TTL_SEC = 25
FETCH_TIMEOUT_SEC = 20
RETRY_DELAY_SEC = 0.5


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串（对齐 JS 的 toISOString 行为）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pick_balance_info(infos: Any) -> Optional[dict]:
    """从 balance_infos 数组中选择展示项（见模块 docstring 的选币规则）。"""
    if not isinstance(infos, list) or not infos:
        return None

    def num(item) -> float:
        if isinstance(item, dict) and "total_balance" in item:
            try:
                return float(item["total_balance"])
            except (TypeError, ValueError):
                return float("nan")
        return float("nan")

    for rule in (
        lambda x: isinstance(x, dict) and x.get("currency") == "CNY" and num(x) > 0,
        lambda x: num(x) > 0,
        lambda x: isinstance(x, dict) and x.get("currency") == "CNY",
    ):
        for item in infos:
            if isinstance(item, dict) and rule(item):
                return item
    return infos[0] if isinstance(infos[0], dict) else None


class BalanceClient:
    """余额客户端：带缓存、去重与瞬时回退的余额服务。"""

    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key
        self._cache: Optional[dict[str, Any]] = None   # {at, payload}
        self._inflight = False
        self._lock = threading.Lock()

    def fetch_balance(self) -> dict[str, Any]:
        """直接请求余额接口（含重试），返回原始 payload（不含 todayUsage）。"""
        if not self.api_key:
            return {"ok": False, "code": "NO_KEY", "error": "未配置 DEEPSEEK_API_KEY"}

        last_err: Optional[Exception] = None
        last_was_4xx = False

        for attempt in range(2):
            try:
                data = fetch_json(
                    BALANCE_URL,
                    {"Authorization": "Bearer " + self.api_key},
                    FETCH_TIMEOUT_SEC,
                )
            except HttpError as exc:
                last_err = exc
                last_was_4xx = 400 <= exc.status < 500
                if last_was_4xx:
                    break  # 4xx 不重试
            except ParseError:
                return {"ok": False, "code": "PARSE", "error": "余额接口返回不是合法 JSON"}
            except NetworkError as exc:
                last_err = exc
            else:
                info = pick_balance_info(data.get("balance_infos"))
                if not info or "total_balance" not in info:
                    return {"ok": False, "code": "SHAPE", "error": "余额接口返回结构异常"}
                return {
                    "ok": True,
                    "totalBalance": float(info["total_balance"]),
                    "currency": str(info.get("currency") or "CNY"),
                    "updatedAt": _now_iso(),
                }

            # 第一次失败后短暂等待再重试。
            if attempt == 0:
                time.sleep(RETRY_DELAY_SEC)

        transient = not last_was_4xx
        message = ("余额接口请求失败: " + str(last_err)[:200]) if last_err else "余额接口请求失败"
        return {"ok": False, "code": "HTTP", "transient": transient, "error": message}

    def get_balance(self) -> dict[str, Any]:
        """获取余额 payload，带 25 秒缓存 + 进行中请求去重 + 瞬时回退。"""
        now = time.time()
        with self._lock:
            if self._cache and now - self._cache["at"] < BALANCE_TTL_SEC:
                return self._cache["payload"]
            if self._inflight:
                # 已有请求进行中：有缓存回缓存，无缓存给一个软错误避免重复请求。
                return self._cache["payload"] if self._cache else {
                    "ok": False, "code": "BUSY", "error": "余额请求进行中", "transient": True,
                }
            self._inflight = True

        try:
            payload = self.fetch_balance()
        finally:
            with self._lock:
                self._inflight = False

        if payload.get("ok"):
            with self._lock:
                self._cache = {"at": now, "payload": payload}
            return payload

        # 瞬时网络抖动且已有缓存 -> 沿用旧余额并标记 stale。
        with self._lock:
            cached = self._cache
        if payload.get("transient") and cached:
            logger.info("[balance] 瞬时失败，沿用最近余额: %s", payload.get("error"))
            return {**cached["payload"], "stale": True, "error": payload.get("error")}

        if not payload.get("transient"):
            logger.error("[balance] %s %s", payload.get("code"), payload.get("error"))
        return payload
