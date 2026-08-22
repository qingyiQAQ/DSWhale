"""峰谷定价模块。

移植自原项目 lib/index.js 的定价表与峰谷时段判定逻辑，数值与规则保持一致：

- 高峰时段：工作日 9:00–12:00 与 14:00–18:00（北京时间）。
- 2026-08-23 起（北京时间）周末（周六 / 周日）全天按谷价；生效时刻之前的历史
  分桶仍按旧规则计价，所以周末判定带生效分界。
- deepseek-v4-pro 为 flash 的 3 倍价（官方 2026-08-17 生效）；vision-exp 与 flash 同价。
- 每百万 token 单价，单位：元（CNY）。DeepSeek 调价时只需修改本文件。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# 北京时间（UTC+8）。
BEIJING_TZ = timezone(timedelta(hours=8))

# 高峰时段区间（小时，左闭右开）。
PEAK_HOURS = [
    (9, 12),
    (14, 18),
]

# 基础价目（flash / chat / reasoner 共用）：[空闲时段价, 高峰时段价]（元 / 百万 token）。
BASE_PRICE = {
    "hit": [0.05, 0.1],   # 缓存命中输入
    "miss": [1.5, 3.0],   # 缓存未命中输入
    "out": [4.5, 9.0],    # 输出
}

# deepseek-v4-pro 为 flash 的 3 倍价。
PRO_PRICE = {
    "hit": [0.15, 0.3],
    "miss": [4.5, 9.0],
    "out": [13.5, 27.0],
}

# 模型名 -> 价目表（子串匹配，注意保持字典插入顺序：更长的 exp 键排在 flash 前）。
PRICING = {
    "deepseek-v4-flash-vision-exp": BASE_PRICE,
    "deepseek-v4-flash": BASE_PRICE,
    "deepseek-v4-pro": PRO_PRICE,
    "deepseek-chat": BASE_PRICE,
    "deepseek-reasoner": BASE_PRICE,
    "_default": BASE_PRICE,
}

# 周末谷价生效分界：北京时间 2026-08-23 00:00 对应的 epoch 秒。
WEEKEND_VALLEY_FROM_SEC = int(datetime(2026, 8, 23, tzinfo=BEIJING_TZ).timestamp())


def price_for(model: str) -> dict:
    """按模型名返回价目表（子串匹配，找不到时回退默认档）。"""
    m = str(model or "").lower()
    for key, price in PRICING.items():
        if key == "_default":
            continue
        if key in m:
            return price
    return PRICING["_default"]


def is_peak_time(time_sec) -> bool:
    """判定给定 epoch 秒是否为高峰时段（北京时间，含周末谷价分界）。"""
    if not isinstance(time_sec, (int, float)):
        try:
            time_sec = float(time_sec)
        except (TypeError, ValueError):
            return False
    if math.isnan(time_sec) or math.isinf(time_sec):
        return False

    n = int(time_sec)
    beijing = datetime.fromtimestamp(n, tz=BEIJING_TZ)

    # 生效分界之后，周末全天谷价（weekday()：周一=0 ... 周六=5 周日=6）。
    if n >= WEEKEND_VALLEY_FROM_SEC and beijing.weekday() >= 5:
        return False

    hour = beijing.hour
    return any(start <= hour < end for start, end in PEAK_HOURS)
