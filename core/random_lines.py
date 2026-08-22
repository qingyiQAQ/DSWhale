"""随机台词模块。

移植自原项目 widget.js 的 ``RANDOM_GROUPS`` 加权随机台词。点击气泡时按权重随机抽取
一组台词展示；再点一次关闭。

台词返回结构（供 UI 层渲染）：
    {"gif": True}                      -> 只显示 rua.gif 动图
    {"lines": [(text, style, color, wrap), ...]} -> 最多三行，style 取值 A/B/P/C
      - A：label 样式（66u / 600）
      - B：amount 样式（128u / 800）
      - P：period 样式（104u / 800）
      - C：hint 样式（56u / #9fb0d9）
"""

from __future__ import annotations

import random

from typing import Any, Optional

# 台词内容常量。
GROUP2_TEXTS = ["好模型... ↓", "好女孩...↓"]
GROUP3_TEXTS = [
    "不知道用户有什么用，先赶走吧~",
    "我...我...我也要挣钱吗？",
    "我去吃饭啦，测完叫我",
    "压力一只蓝色大肥鱼？！",
    "DeepSleep...",
    "坏了...用户彻底怒了！",
]
GROUP5_TEXTS = [
    "你目录里的dsh是什么...大烧货吗...?",
    "恭喜你实现token自由！token全跑了！",
    "真当我是便宜货啊...",
]
GIF_FALLBACK_TEXTS = ["gif 加载失败了...", "今天没有动图给你看~", "呜呜 动图不见了..."]

# 峰谷文案（按 peakMode 映射）。
PEAK_TEXT = {
    "default": ("空闲时段", "高峰时段"),
    "liangwen": ("梁文谷", "梁文峰"),
    "qiangqiang": ("!?谷谷?!", "!?峰峰?!"),
}
PEAK_COLOR = {"off": "#2fa24c", "peak": "#e0433f"}


def _pick_one(arr: list[str]) -> str:
    """从列表中随机取一个。"""
    return arr[random.randrange(len(arr))]


def _single_center(style: str, text: str, color: str = "", wrap: bool = False) -> dict[str, Any]:
    """构造单行居中台词（第一行占位，其他行为空）。"""
    return {"lines": [(text, style, color, wrap), None, None]}


def _group1(is_peak: bool, peak_mode: str, today_usage: Optional[float], currency: str) -> dict[str, Any]:
    """三行：当前时间段 / 峰谷 / 今日已用。"""
    off_text, peak_text = PEAK_TEXT.get(peak_mode, PEAK_TEXT["default"])
    period = peak_text if is_peak else off_text
    period_color = PEAK_COLOR["peak"] if is_peak else PEAK_COLOR["off"]
    usage_text = "今日已用 " + _fmt(today_usage, currency)
    return {
        "lines": [
            ("当前时间段为:", "A", "", False),
            (period, "P", period_color, False),
            (usage_text, "C", "", False),
        ]
    }


def _fmt(amount: Optional[float], currency: str) -> str:
    """金额格式化（与余额展示一致）。"""
    try:
        fixed = f"{float(amount):.2f}"
    except (TypeError, ValueError):
        fixed = "--"
    return f"¥ {fixed}" if currency == "CNY" else f"{fixed} {currency}"


def pick_random_lines(is_peak: bool, peak_mode: str, today_usage: Optional[float], currency: str) -> dict[str, Any]:
    """按权重随机返回一组台词。

    权重与内容与原项目 index.js 保持一致（45 / 7 / 7 / 10 / 3 / 1）。
    """
    groups = [
        (45, lambda: _group1(is_peak, peak_mode, today_usage, currency)),
        (7, lambda: _single_center("B", _pick_one(GROUP2_TEXTS))),
        (7, lambda: _single_center("A", _pick_one(GROUP3_TEXTS), wrap=True)),
        (10, lambda: {"gif": True}),
        (3, lambda: _single_center("A", _pick_one(GROUP5_TEXTS), wrap=True)),
        (1, lambda: _single_center("B", "哦鲸鲸... ")),
    ]

    total = sum(weight for weight, _ in groups)
    r = random.random() * total
    for weight, factory in groups:
        r -= weight
        if r < 0:
            return factory()
    return groups[-1][1]()


def gif_fallback_lines() -> dict[str, Any]:
    """gif 加载失败时的降级文字台词。"""
    return _single_center("A", _pick_one(GIF_FALLBACK_TEXTS), wrap=True)
