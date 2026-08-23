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

import json
import logging
import os
import random

from typing import Any, Optional

logger = logging.getLogger(__name__)

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

# 空闲俏皮话配置：项目根目录下的 config/idle_remarks.json + 合法样式档。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDLE_REMARKS_CONFIG = os.path.join(_PROJECT_ROOT, "config", "idle_remarks.json")
_VALID_STYLES = {"A", "B", "P", "C"}

# 内置默认俏皮话（config/idle_remarks.json 缺失或损坏时回退）。
# 元组：(文本, 样式档, 是否换行, 权重)。
_DEFAULT_IDLE_REMARKS = [
    ("好模型... ↓", "B", False, 5),
    ("好女孩...↓", "B", False, 5),
    ("不知道用户有什么用，先赶走吧~", "A", True, 7),
    ("我...我...我也要挣钱吗？", "A", True, 7),
    ("我去吃饭啦，测完叫我", "A", True, 7),
    ("压力一只蓝色大肥鱼？！", "A", True, 7),
    ("DeepSleep...", "A", True, 7),
    ("坏了...用户彻底怒了！", "A", True, 7),
    ("你目录里的dsh是什么...大烧货吗...?", "A", True, 3),
    ("恭喜你实现token自由！token全跑了！", "A", True, 3),
    ("真当我是便宜货啊...", "A", True, 3),
    ("哦鲸鲸... ", "B", False, 1),
]


def _load_idle_remarks() -> list[tuple[str, str, bool, float]]:
    """从 config/idle_remarks.json 读取俏皮话池；缺失/损坏时回退内置默认。

    逐条校验：text 非空字符串、style 属于 A/B/P/C（否则默认 A）、wrap 布尔、
    weight 数值且 >0（<=0 视为禁用跳过）；至少解析出一条才采用文件内容。
    """
    try:
        with open(IDLE_REMARKS_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("remarks") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return list(_DEFAULT_IDLE_REMARKS)

        parsed: list[tuple[str, str, bool, float]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            style = item.get("style", "A")
            if style not in _VALID_STYLES:
                style = "A"
            wrap = bool(item.get("wrap", True))
            try:
                weight = float(item.get("weight", 1))
            except (TypeError, ValueError):
                weight = 1.0
            if weight <= 0:
                continue  # 权重 <=0 视为禁用
            parsed.append((text, style, wrap, weight))  # 保留原文（含尾随空格，如「哦鲸鲸... 」）
        return parsed or list(_DEFAULT_IDLE_REMARKS)
    except (OSError, ValueError) as exc:
        logger.warning("[random_lines] 俏皮话配置读取失败，回退内置默认: %s", exc)
        return list(_DEFAULT_IDLE_REMARKS)


IDLE_REMARKS = _load_idle_remarks()

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


def pick_idle_remark() -> dict[str, Any]:
    """按权重随机返回一句俏皮话（单行文字，结构兼容 bubble.set_random）。"""
    total = sum(weight for _, _, _, weight in IDLE_REMARKS)
    r = random.random() * total
    for text, style, wrap, weight in IDLE_REMARKS:
        r -= weight
        if r < 0:
            return _single_center(style, text, wrap=wrap)
    # 兜底（浮点误差等极端情况）：返回最后一条。
    text, style, wrap, _weight = IDLE_REMARKS[-1]
    return _single_center(style, text, wrap=wrap)
