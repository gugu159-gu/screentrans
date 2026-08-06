# 🐱 喵译 · Screenshot Translate

> 截图即译 —— 按一个热键，框选屏幕任意区域，**一步识别图中外文并翻译成中文**。

看英文论文、外文软件、日剧生肉截图时不想手动打字？喵译帮你把「截图 → 识别 → 翻译」压缩成**一次拖拽**。

![python](https://img.shields.io/badge/Python-3.10%2B-blue) ![license](https://img.shields.io/badge/License-MIT-green)

## ✨ 效果

框选屏幕上的英文界面，结果窗直接给出逐条对照译文：

```
1. Welcome to the Future
   → 欢迎来到未来
2. Sign in to continue your journey
   → 登录以继续您的旅程
3. Get Started
   → 立即开始
   ...
💡 一个欢迎页面，提示用户登录以继续旅程……
```

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API（同目录 .env，可复制 vision 项目的 .env）
# VISION_BASE_URL=https://api.siliconflow.cn/v1/chat/completions
# VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct
# VISION_API_KEY=sk-xxxx

# 3. 常驻运行（托盘 + 全局热键 Ctrl+Shift+T）
python screentrans.py

# 或单次模式（立即截图翻译一次后退出）
python screentrans.py once
```

## 🎮 用法

| 操作 | 说明 |
|---|---|
| `Ctrl+Shift+T` | 全局热键，弹出选区遮罩 |
| 鼠标拖拽 | 框选要翻译的区域，松开即翻译 |
| `Esc` | 取消本次截图 |
| 托盘菜单 | 截图翻译 / 退出（热键失效时的备用入口） |
| 结果窗 | 逐条「原文 → 译文」，可**复制全部** / 再截一张 |

自定义热键：`python screentrans.py -k "alt+q"`

## 🔧 原理

1. **选区截图**：tkinter 全屏遮罩 + `-transparentcolor` 实现「选区透明、周围变暗」的微信式拖拽框选
2. **一步翻译**：截图直接发给视觉模型（Qwen3-VL），让模型**同时完成识别 + 翻译**，输出结构化 JSON
3. **结果悬浮窗**：置顶显示，原文译文逐条对照，支持一键复制

不需要本地 OCR，不需要第二个翻译 API —— 一次调用全搞定。

## 📦 依赖

- `Pillow` — 截图与托盘图标
- `requests` — 调用视觉模型 API
- `keyboard` — 全局热键
- `pystray` — 系统托盘常驻
- `tkinter` — Python 标准库（选区遮罩 + 结果窗）

## ⚠️ 说明

- 全局热键需要**普通用户权限**即可；若目标窗口以管理员运行则热键可能失效，此时用托盘菜单触发
- API Key 只存在本地 `.env`（已 gitignore，不会泄露）
- 支持切换任意 OpenAI 兼容视觉模型（改 `.env` 里的 `VISION_MODEL` 即可，如 `Qwen/Qwen3-VL-30B-A3B-Instruct`）

## 🧩 相关项目

- [vision](https://github.com/yishengzhizhe/vision) — 命令行识图工具（本项目的识图逻辑来源）
- [喵Code (MiaoCode)](https://github.com/yishengzhizhe/miaocode) — Claude Code 桌面客户端

## 📄 License

MIT
