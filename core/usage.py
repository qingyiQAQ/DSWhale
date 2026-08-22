"""今日已用（实时·令牌模式）模块。

移植自原项目 lib/index.js 的 ``fetchUsage`` / ``computeTodayUsage`` 逻辑：

- 读平台会话令牌 ``DEEPSEEK_PLATFORM_TOKEN``，请求
  ``https://platform.deepseek.com/api/v0/usage/by_api_key/amount``。
- 接口不返回金额，只返回 token 分桶，本模块按峰谷定价换算成金额。
- 响应结构：``data.biz_data.series[]``，每项 ``{model, buckets:[{time, usage:{...}}]}``。
"""

from __future__ import annotations

import logging
import time

from datetime import datetime, timedelta
from typing import Any, Optional

from .http_util import fetch_json, HttpError, NetworkError
from .pricing import price_for, is_peak_time

logger = logging.getLogger(__name__)

USAGE_BASE_URL = "https://platform.deepseek.com/api/v0/usage/by_api_key/amount"
FETCH_TIMEOUT_SEC = 15


def _local_midnight_epoch() -> int:
    """返回本地零点对应的 epoch 秒（用于 start 参数）。"""
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)
    return int(midnight.timestamp())


def fetch_usage(platform_token: str) -> dict[str, Any]:
    """请求平台用量接口，返回 {amount, tokens} 或 {error}。"""
    if not platform_token:
        return {"error": "no platform token"}

    token = platform_token.strip()
    # 兼容用户粘贴时带不带 Bearer 前缀。
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    start = _local_midnight_epoch()
    end = start + 86400
    # tz 用本地时区偏移秒数（平台接口约定）。
    tz = -time.timezone if time.daylight == 0 else -time.altzone
    url = f"{USAGE_BASE_URL}?start={start}&end={end}&tz={tz}"

    try:
        data = fetch_json(url, {"Authorization": "Bearer " + token}, FETCH_TIMEOUT_SEC)
    except (HttpError, NetworkError) as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 —— 解析等异常兜底
        return {"error": str(exc)}

    result = compute_today_usage(data)
    if result is not None:
        return result
    return {"error": "no usage"}


def compute_today_usage(data: Any) -> Optional[dict[str, float]]:
    """把平台用量接口返回的 token 分桶换算成金额。

    返回 ``{amount, tokens}``；无有效数据返回 None。
    """
    # 兼容两种响应包裹：data.biz_data.series[] 或 data.series[]。
    d = data
    if isinstance(d, dict) and isinstance(d.get("data"), dict):
        inner = d["data"]
        if isinstance(inner.get("biz_data"), dict) and isinstance(inner["biz_data"].get("series"), list):
            d = inner["biz_data"]
        elif isinstance(inner.get("series"), list):
            d = inner
    series = d.get("series") if isinstance(d, dict) else None
    if not series:
        return None

    cost = 0.0
    tokens = 0
    found = False

    for s in series:
        if not isinstance(s, dict):
            continue
        price = price_for(s.get("model"))
        for bucket in (s.get("buckets") or []):
            usage = bucket.get("usage") if isinstance(bucket, dict) else None
            if not isinstance(usage, dict):
                continue
            hit = _to_float(usage.get("PROMPT_CACHE_HIT_TOKEN"))
            miss = _to_float(usage.get("PROMPT_CACHE_MISS_TOKEN"))
            out = _to_float(usage.get("RESPONSE_TOKEN"))
            if hit + miss + out == 0:
                continue
            found = True
            tokens += hit + miss + out
            peak = 1 if is_peak_time(bucket.get("time")) else 0
            cost += (hit / 1e6) * price["hit"][peak]
            cost += (miss / 1e6) * price["miss"][peak]
            cost += (out / 1e6) * price["out"][peak]

    if not found:
        return None
    return {"amount": cost, "tokens": tokens}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
