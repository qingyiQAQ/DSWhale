# DeepSeek 小鲸鱼桌宠 (DSWhale)

一个运行在 Windows 桌面上的 **DeepSeek API 余额小鲸鱼桌宠**，功能与原网页挂件
[DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)
**完全一致**，但不再依赖网页 / DSH 面板，可独立置顶显示在桌面任意位置。

![鲸鱼](assets/DSniang1.png)

## 功能

- **透明置顶鲸鱼**：逐像素透明（PySide6），无白边；可拖拽，四边四分之一吸附，左吸附自动镜像。
- **按压 Q 弹 + 音效**：按住鲸鱼会压扁回弹，播放按压 / 松手音效（小黄鸭 / 音效1 两套，可调音量）。
- **气泡余额展示**：点击鲸鱼弹出气泡，显示 `DeepSeek 余额` + `今日已用`；余额变化时自动弹出并做数字滚动动画；5 秒自动收起。
- **随机台词 / gif**：再点一次气泡切随机台词（峰谷时段、俏皮话等），约 14% 概率显示 `rua.gif` 动图。
- **多币种选币**：与官网逻辑一致——优先 CNY>0，其次任意非零，再退回 CNY，最后取首项。
- **今日已用（两种模式）**：
  - `小鲸鱼记账`（默认）：余额下降差值记账，跨天归档 30 天，币种感知。
  - `实时·令牌`：读平台用量接口，按峰谷定价换算金额。
- **峰谷定价**：工作日 9–12 / 14–18 为高峰；2026-08-23 起周末全天谷价；`deepseek-v4-pro` 为 3 倍价。
- **每轮对话消耗**：轮询 DSH 本地服务精确获取；服务不可达时自动降级为「余额差值」估算。
- **汉堡菜单**：大小 / 音效 / 音量 / 用量模式 / 峰谷文案 / 气泡开关 / 每轮消耗提示 + 退出。

## 技术栈与依赖

- Python 3.13
- [PySide6](https://pypi.org/project/PySide6/) ≥ 6.5（逐像素透明 + SVG + 音频 + gif）
- 网络请求只用标准库 `urllib`，无第三方网络依赖。

```bash
pip install -r requirements.txt
```

## 快速开始

```bash
# 建议：先把 DeepSeek 凭据写入环境变量，或放到 DSH 凭据文件
set DEEPSEEK_API_KEY=sk-xxxxxxxx        # 或写入 ~/.dsh/.credentials.yaml
set DEEPSEEK_PLATFORM_TOKEN=xxxxxx      # 可选：实时·令牌模式需要

python main.py
```

### 凭据解析优先级

1. 环境变量 `DEEPSEEK_API_KEY` / `DEEPSEEK_PLATFORM_TOKEN`
2. `~/.dsh/.credentials.yaml`（原 DSH 凭据服务，轻量键值解析，无需 PyYAML）
3. `~/.dshw-pet/config.json`

未配置 `DEEPSEEK_API_KEY` 时，气泡会显示「未配置」提示，其余功能（拖拽 / 音效 / 菜单）不受影响。

## 数据文件兼容

桌宠**复用原网页挂件的数据文件**，你的历史设置与账本无缝继承：

| 用途 | 路径（优先） | 回退路径 |
|------|--------------|----------|
| 设置（大小 / 音量 / 模式…） | `~/.dsh/.dshw-size.json` | `~/.dshw-pet/settings.json` |
| 账本（今日已用 / 历史） | `~/.dsh/.dshw-usage.json` | `~/.dshw-pet/usage.json` |
| 凭据 | `~/.dsh/.credentials.yaml` | `~/.dshw-pet/config.json` |

## 操作说明

| 操作 | 效果 |
|------|------|
| 按住鲸鱼拖动 | 移动，松手四边四分之一吸附（拖到左半边镜像） |
| 单击鲸鱼 | 弹出气泡（余额 + 今日已用），同时手动刷新余额 |
| 再点气泡 | 切随机台词 / gif；再点一次关闭 |
| 悬停鲸鱼 | 右上角出现「三点」按钮 |
| 点三点按钮 | 弹出汉堡菜单 |
| 菜单「退出桌宠」 | 退出程序（保存位置与设置） |

## 项目结构

```
DSWhale/
├── main.py                 # 入口：日志 + QApplication + PetWindow + 异常兜底
├── requirements.txt
├── assets/                 # 鲸鱼图 / gif / 音效（原项目资源）
├── core/                   # 与 UI 无关的逻辑层（纯 Python + 标准库）
│   ├── http_util.py        #   urllib JSON GET 封装（异常分类）
│   ├── config.py           #   设置 / 凭据解析与持久化
│   ├── pricing.py          #   峰谷定价表与判定
│   ├── balance.py          #   余额拉取（缓存 / 去重 / 瞬时回退 / 选币）
│   ├── usage.py            #   实时·令牌用量换算
│   ├── ledger.py           #   小鲸鱼记账持久化
│   ├── turn_cost.py        #   每轮消耗（DSH 轮询 + 余额差值降级）
│   ├── random_lines.py     #   随机台词（加权抽取）
│   └── audio.py            #   按压 / 松手音效（QtMultimedia）
└── ui/                     # Qt 界面层
    ├── bubble.py           #   气泡渲染器（SVG + 文字 + gif + 命中检测）
    ├── menu.py             #   汉堡菜单面板
    └── pet_window.py       #   主窗口（拖拽 / 吸附 / 镜像 / Q弹 / 定时器 / 线程）
```

## 模块职责与线程模型

- `core/` 不依赖任何 Qt 类型，可独立单测；`ui/` 负责把 `core/` 的结果渲染到屏幕。
- 网络请求（余额、用量、每轮消耗）都在**后台线程**执行，结果通过 Qt 信号
  （队列连接）安全地送回 UI 线程，不阻塞界面。
- 余额每 60 秒自动刷新，带 25 秒缓存与进行中请求去重；每轮消耗每秒轮询一次。

## 日志与问题排查

- 控制台输出 `INFO` 级关键事件（启动 / 模式切换 / 余额变化 / 降级等）。
- 运行目录下 `dshw-pet.log` 记录 `DEBUG` 级完整日志（含网络异常细节）。
- 若鲸鱼不动 / 余额不更新，先看 `dshw-pet.log` 中 `[balance]` / `[turn_cost]` 前缀的条目。
