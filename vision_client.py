#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_client.py — 截图翻译工具复用的视觉模型客户端

从 vision.py（识图工具）抽取核心逻辑，改为可 import 的模块：
调用硅基流动 Qwen3-VL（或任意 OpenAI 兼容接口）识别图片中的外文并翻译成中文。

配置读取同目录 .env：
    VISION_BASE_URL=https://api.siliconflow.cn/v1/chat/completions
    VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct
    VISION_API_KEY=sk-xxxx
"""

import os
import sys
import json
import time
import base64
import io

import requests

# Windows 控制台默认 GBK 编码，强制 UTF-8 防止中文/emoji 崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_MODEL = "Qwen/Qwen3-VL-8B-Instruct"


def load_config(env_dir=None):
    """从 .env 读取配置，返回 (base_url, model, api_key)。

    env_dir 指定 .env 所在目录，默认本文件所在目录。
    """
    env_path = os.path.join(env_dir or os.path.dirname(os.path.abspath(__file__)), ".env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")

    base_url = env.get("VISION_BASE_URL", DEFAULT_BASE_URL)
    model = env.get("VISION_MODEL", DEFAULT_MODEL)
    api_key = env.get("VISION_API_KEY") or env.get("ZHIPU_API_KEY") or os.environ.get("ZHIPU_API_KEY", "")
    return base_url, model, api_key


def image_to_data_url(image, mime="image/png"):
    """PIL Image -> base64 data URL（统一转 RGB 防 RGBA 部分模型不支持）。"""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def chat_once(base_url, model, api_key, messages, retries=2, json_mode=False):
    """发送一整套消息到视觉模型，返回 (回答文本, usage)。

    网络错误会带退避重试（最多 retries 次）。json_mode=True 时请求带
    response_format 强制模型输出 JSON。
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(base_url, json=payload, headers=headers, timeout=120)
            break
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))  # 2s / 4s 退避
    else:
        return f"[网络错误] {last_err}", None

    if resp.status_code != 200:
        return f"[请求失败 HTTP {resp.status_code}] {resp.text[:300]}", None

    try:
        resp.encoding = "utf-8"  # 强制 UTF-8，避免 chardet 误判成 GBK
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage")
        return content, usage
    except Exception as e:
        return f"[返回格式异常] {e}", None


def try_parse_json(text):
    """把模型回答解析成 JSON；容忍 ```json 代码块和前后杂散文本。"""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


TRANSLATE_PROMPT = (
    "你是一个截图翻译助手。请识别这张图片中出现的所有外文文字"
    "（英文、日文、韩文等非中文文字），并逐条翻译成简体中文。\n"
    "只输出一个 JSON 对象，不要输出任何其他内容，格式如下：\n"
    '{\n'
    '  "items": [\n'
    '    {"original": "外文原文", "translation": "简体中文翻译"}\n'
    '  ],\n'
    '  "summary": "用一句中文概括图片内容"\n'
    '}\n'
    "要求：\n"
    "1. 原文与翻译一一对应，逐条列出；按钮、标题、整句都要翻译\n"
    "2. 若图片没有外文文字，items 返回空数组，summary 用中文说明图片内容\n"
)


def translate_image(base_url, model, api_key, image, prompt=TRANSLATE_PROMPT):
    """识别并翻译图片中的外文。image 为 PIL Image，返回解析后的 dict。

    优先用 response_format 强制 JSON；失败自动降级重试一次（仅靠 prompt 约束），
    仍失败则返回 {"error": ..., "raw": 原始回答}。
    """
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
        ],
    }]

    answer, _ = chat_once(base_url, model, api_key, messages, json_mode=True)
    parsed = try_parse_json(answer)
    if parsed is not None:
        return parsed
    # 降级：不带 response_format 再试一次
    answer, _ = chat_once(base_url, model, api_key, messages, json_mode=False)
    parsed = try_parse_json(answer)
    if parsed is not None:
        return parsed
    return {"error": "模型未返回合法 JSON", "raw": answer}
