"""配置与凭据模块。

负责三件事：
1. 读取 / 持久化桌宠设置（优先自有目录 ``~/.dshw-pet``，读取不到再降级 ``~/.dsh``
   以继承原插件的旧数据，格式兼容）。
2. 解析 DeepSeek 凭据（``DEEPSEEK_API_KEY`` / ``DEEPSEEK_PLATFORM_TOKEN``），
   优先级：环境变量 -> 本地 config.json -> DSH 凭据文件。
3. 提供账本文件路径。

存储路径（优先 ``~/.dshw-pet``，读取不到再降级 ``~/.dsh``）：
- 设置文件：``~/.dshw-pet/settings.json``（降级 ``~/.dsh/.dshw-size.json``）
- 账本文件：``~/.dshw-pet/usage.json``（降级迁移 ``~/.dsh/.dshw-usage.json``）
- 凭据文件：``~/.dshw-pet/config.json``（降级 ``~/.dsh/.credentials.yaml``）
"""

from __future__ import annotations

import json
import logging
import os
import re

from typing import Any, Optional

logger = logging.getLogger(__name__)

# 默认配置目录（优先，桌宠自有）。
PET_HOME = os.path.join(os.path.expanduser("~"), ".dshw-pet")
# DSH 主目录（降级读取用，兼容原网页挂件的旧数据）。
DSH_HOME = os.path.join(os.path.expanduser("~"), ".dsh")

# 设置文件候选路径（按优先级取第一个可读的；写入时取第一个可写的）。
SETTINGS_CANDIDATES = [
    os.path.join(PET_HOME, "settings.json"),
    os.path.join(DSH_HOME, ".dshw-size.json"),
]
# 账本文件候选路径。
LEDGER_CANDIDATES = [
    os.path.join(PET_HOME, "usage.json"),
    os.path.join(DSH_HOME, ".dshw-usage.json"),
]
# 凭据文件候选路径。
CREDENTIALS_CANDIDATES = [
    os.path.join(PET_HOME, "config.json"),
    os.path.join(DSH_HOME, ".credentials.yaml"),
]

# 默认设置（与原插件默认值一致）。
DEFAULT_SETTINGS: dict[str, Any] = {
    "scale": 1.5,
    "sound": True,
    "vol": 0.9,
    "soundSet": "duck",
    "usageMode": "ledger",
    "peakMode": "default",
    "bubbleOn": True,
    "idleTalkOn": True,
    "turnCostOn": True,
    "turnCostCloseMs": 5000,
    "scrollGapOn": False,
    "scrollGapPx": 17,
    "autostartOn": False,
    # 桌面端扩展字段：
    "pos": None,          # 位置锚点记忆 {hAnchor, hDist, vAnchor, vDist}
    "dshServer": "http://127.0.0.1:3080",  # 每轮消耗轮询的 DSH 本地服务地址
}

# 环境变量名。
ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_PLATFORM_TOKEN = "DEEPSEEK_PLATFORM_TOKEN"


def _first_readable(paths: list[str]) -> Optional[str]:
    """返回第一个存在且可读的路径，否则 None。"""
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def _first_writable(paths: list[str]) -> str:
    """返回第一个可写路径（目录不存在则尝试创建），兜底返回最后一个。"""
    for p in paths:
        parent = os.path.dirname(p)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                continue
        if p:
            return p
    return paths[-1]


class Config:
    """桌宠配置封装：缓存设置并提供类型安全的读写接口。"""

    def __init__(self):
        self._settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._settings_path = _first_writable(SETTINGS_CANDIDATES)
        self._load()

    # ------------------------------------------------------------------ #
    # 设置读写
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """从设置文件加载，缺失或损坏时沿用默认值。"""
        path = _first_readable(SETTINGS_CANDIDATES)
        if not path:
            logger.info("[config] 未找到设置文件，使用默认设置")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # 只认已知字段，忽略未知字段（保证与原插件文件互相兼容）。
                for key in DEFAULT_SETTINGS:
                    if key in data:
                        self._settings[key] = data[key]
                logger.info("[config] 已加载设置: %s", path)
        except (OSError, ValueError) as exc:
            logger.warning("[config] 设置文件读取失败，使用默认值: %s", exc)

    def save(self) -> None:
        """把当前设置写回磁盘（未知字段不写，保持格式干净）。"""
        try:
            body = json.dumps(self._settings, ensure_ascii=False, indent=2)
            with open(self._settings_path, "w", encoding="utf-8") as f:
                f.write(body)
            logger.debug("[config] 设置已保存: %s", self._settings_path)
        except OSError as exc:
            logger.warning("[config] 设置保存失败: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        """读取单个设置项。"""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """更新单个设置项并立即落盘。"""
        self._settings[key] = value
        self.save()

    def update(self, values: dict[str, Any]) -> None:
        """批量更新设置并落盘。"""
        self._settings.update(values)
        self.save()

    @property
    def settings(self) -> dict[str, Any]:
        """返回当前设置的浅拷贝，避免外部误改内部状态。"""
        return dict(self._settings)

    # ------------------------------------------------------------------ #
    # 凭据解析
    # ------------------------------------------------------------------ #
    def resolve_credential(self, name: str, env_name: str) -> Optional[str]:
        """按「环境变量 -> 本地 config.json -> DSH 凭据文件」顺序解析凭据。

        参数:
            name: 凭据键名（如 ``DEEPSEEK_API_KEY``）。
            env_name: 对应的环境变量名。
        """
        # 1. 环境变量优先。
        value = os.environ.get(env_name)
        if value:
            return value.strip()

        # 2. 本地 config.json（~/.dshw-pet，JSON，优先）。
        local = CREDENTIALS_CANDIDATES[0]
        if os.path.isfile(local):
            value = self._parse_json_credential(local, name)
            if value:
                return value

        # 3. DSH 凭据文件（.credentials.yaml 的简单键值解析，不依赖 PyYAML，降级）。
        cred_file = CREDENTIALS_CANDIDATES[1]
        if os.path.isfile(cred_file):
            value = self._parse_credential_file(cred_file, name)
            if value:
                return value

        logger.warning("[config] 未解析到凭据 %s", name)
        return None

    @staticmethod
    def _parse_credential_file(path: str, name: str) -> Optional[str]:
        """解析 DSH 凭据文件中的某个键（轻量 YAML 行解析）。

        支持 ``key: value`` 与 ``  key: value``（缩进）两种形式，值为字符串时
        剥离首尾引号与空白。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    match = re.match(rf"^\s*{re.escape(name)}\s*:\s*(.*?)\s*$", line)
                    if match:
                        value = match.group(1).strip()
                        # 去掉可选的首尾单 / 双引号。
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                            value = value[1:-1]
                        if value:
                            return value
        except OSError:
            pass
        return None

    @staticmethod
    def _parse_json_credential(path: str, name: str) -> Optional[str]:
        """解析本地 config.json 中的凭据。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get(name):
                return str(data[name]).strip()
        except (OSError, ValueError):
            pass
        return None


def ledger_path() -> str:
    """返回账本写入路径（~/.dshw-pet 优先）。

    若新路径尚无账本而旧 ~/.dsh 路径有历史账本，则先迁移一次（复制旧文件到新路径），
    之后只读写新路径，彻底脱离 ~/.dsh。
    """
    new_path = LEDGER_CANDIDATES[0]
    old_path = LEDGER_CANDIDATES[1]
    if not os.path.isfile(new_path) and os.path.isfile(old_path):
        try:
            parent = os.path.dirname(new_path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(old_path, "r", encoding="utf-8") as src, \
                 open(new_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            logger.info("[config] 已迁移旧账本 %s -> %s", old_path, new_path)
        except OSError as exc:
            logger.warning("[config] 旧账本迁移失败，改用新账本: %s", exc)
    return _first_writable(LEDGER_CANDIDATES)
