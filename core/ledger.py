"""记账持久化模块（小鲸鱼记账模式）。

移植自原项目 lib/index.js 的 ``recordLedgerUsage`` 逻辑，负责把「观测到的余额下降」
累加为当日用量并持久化到 ``.dshw-usage.json``：

- 同一天内余额下降 -> 差值累加到 todayUsage；余额上升（充值）不扣减。
- 币种感知：观测币种与上次不同时只重置基准、不记差值（防止多币种账户切换污染账本）。
- 跨天：todayUsage 归档进 history（保留最近 30 天），当日归零。
"""

from __future__ import annotations

import json
import logging
import os

from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_HISTORY_DAYS = 30


def today_key() -> str:
    """返回本地日期字符串（YYYY-MM-DD）。"""
    now = datetime.now()
    return f"{now.year}-{now.month:02d}-{now.day:02d}"


def _read(path: str) -> dict[str, Any]:
    """读取账本，缺失或损坏时返回空账本结构。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        if isinstance(parsed, dict) and isinstance(parsed.get("date"), str):
            return parsed
    except (OSError, ValueError) as exc:
        logger.debug("[ledger] 账本读取失败，使用空账本: %s", exc)
    return {"date": today_key(), "lastBalance": None, "todayUsage": 0, "history": {}}


def _write(path: str, ledger: dict[str, Any]) -> bool:
    """把账本写回磁盘（原子性依赖一次 write，量级很小）。"""
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False)
        return True
    except OSError as exc:
        logger.warning("[ledger] 账本写入失败: %s", exc)
        return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def record_usage(current_balance: Optional[float], currency: str, path: str) -> dict[str, Any]:
    """观测一次余额并更新账本，返回更新后的账本。

    参数:
        current_balance: 本次观测到的余额（可能为 None，表示观测失败）。
        currency: 本次观测到的币种（如 CNY / USD）。
        path: 账本文件路径。
    """
    t = today_key()
    ledger = _read(path)
    cur = str(currency or "")
    last_currency = ledger.get("lastCurrency", "")
    currency_changed = (
        isinstance(last_currency, str) and last_currency != ""
        and cur != "" and last_currency != cur
    )

    if ledger.get("date") != t:
        # 跨天：归档昨日用量，当日归零。
        if ledger.get("date") and _is_number(ledger.get("todayUsage")):
            ledger.setdefault("history", {})[ledger["date"]] = ledger["todayUsage"]
        ledger["date"] = t
        ledger["lastBalance"] = current_balance
        ledger["lastCurrency"] = cur
        ledger["todayUsage"] = 0
    elif currency_changed:
        # 币种切换：只换基准，不把差值记成消费。
        ledger["lastBalance"] = current_balance
        ledger["lastCurrency"] = cur
    else:
        prev = ledger.get("lastBalance")
        prev = prev if _is_number(prev) else current_balance
        if _is_number(prev) and _is_number(current_balance) and current_balance < prev:
            prev_usage = ledger.get("todayUsage") if _is_number(ledger.get("todayUsage")) else 0
            ledger["todayUsage"] = prev_usage + (prev - current_balance)
        ledger["lastBalance"] = current_balance
        ledger["lastCurrency"] = cur

    # 历史只保留最近 30 天。
    history = ledger.setdefault("history", {})
    keys = sorted(history.keys())
    while len(keys) > MAX_HISTORY_DAYS:
        del history[keys.pop(0)]

    _write(path, ledger)
    return ledger
