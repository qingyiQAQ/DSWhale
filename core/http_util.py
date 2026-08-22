"""网络请求工具：基于标准库 urllib 的 JSON GET 请求封装。

设计目标：不引入 requests 等第三方依赖，只使用标准库完成带自定义请求头、
超时控制与结构化异常分类的 HTTPS GET 请求，供余额 / 用量模块复用。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from typing import Optional


class HttpError(Exception):
    """HTTP 状态码错误（4xx / 5xx）。"""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class NetworkError(Exception):
    """网络层错误（连接失败、DNS、超时等）。"""


class ParseError(Exception):
    """响应体不是合法 JSON 或解码失败。"""


def fetch_json(url: str, headers: Optional[dict] = None, timeout: float = 20.0):
    """发送 GET 请求并解析 JSON 响应。

    参数:
        url: 目标地址。
        headers: 请求头字典（如 Authorization）。
        timeout: 超时秒数。

    返回:
        解析后的 Python 对象（dict / list / 标量）。

    异常:
        HttpError: 收到 4xx / 5xx 响应。
        NetworkError: 网络连接 / 超时错误。
        ParseError: 响应体不是合法 JSON。
    """
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise NetworkError(str(exc)) from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseError(str(exc)) from exc
