#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screentrans.py — 喵译 · 截图翻译工具

全局热键 Ctrl+Shift+T（或托盘菜单）触发 → 鼠标拖选区域截图 →
Qwen3-VL 一步识别图中外文并翻译成中文 → 置顶悬浮窗显示结果。

用法:
    python screentrans.py            # 常驻模式（托盘 + 全局热键）
    python screentrans.py once       # 单次模式：立即截图翻译一次
    python screentrans.py -k alt+q   # 自定义全局热键（如 -k "alt+q"）

依赖: Pillow, requests, pystray, keyboard（tkinter 为 Python 标准库）
配置: 同目录 .env（VISION_BASE_URL / VISION_MODEL / VISION_API_KEY）
"""

import sys
import queue
import threading
import ctypes
import argparse

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageGrab, ImageDraw

import vision_client as vc

APP_NAME = "喵译 · Screenshot Translate"


# ---- 高 DPI 支持：让 tkinter 像素坐标 == 真实屏幕像素（必须在创建 Tk 之前） ----
def _enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()

CROSSHAIR_PREVIEW = True  # 选区时右上角实时显示尺寸


class RegionSelector:
    """全屏半透明遮罩：鼠标拖拽框选截图区域（微信截图风格）。

    整窗 alpha 半透明让屏幕「变暗」，桌面仍可透过来看清；
    拖选时画出高亮选区框，松开即完成。
    """

    def __init__(self, root, on_done):
        self.root = root
        self.on_done = on_done
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)

        self.sw = self.top.winfo_screenwidth()
        self.sh = self.top.winfo_screenheight()

        # 半透明变暗：能看见桌面透过来，不会误以为死机
        self.top.attributes("-alpha", 0.35)
        self.top.geometry(f"{self.sw}x{self.sh}+0+0")

        self.canvas = tk.Canvas(self.top, cursor="crosshair", bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # 状态
        self.x1 = self.y1 = self.x2 = self.y2 = 0
        self.drawing = False
        self.rect_id = None
        self.size_txt_id = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", self._on_escape)

        self.top.focus_force()

    # ---------- 事件 ----------
    def _on_press(self, event):
        self.drawing = True
        self.x1 = self.x2 = event.x
        self.y1 = self.y2 = event.y

    def _on_drag(self, event):
        if not self.drawing:
            return
        self.x2, self.y2 = event.x, event.y
        self._update_rect()

    def _on_release(self, event):
        if not self.drawing:
            return
        self.drawing = False
        x1, y1, x2, y2 = self._bbox()
        self.top.destroy()
        # 最小尺寸过滤，防误触
        if (x2 - x1) >= 5 and (y2 - y1) >= 5:
            self.on_done((x1, y1, x2, y2))
        else:
            self.on_done(None)

    def _on_escape(self, _event):
        self.top.destroy()
        self.on_done(None)

    # ---------- 绘制 ----------
    def _bbox(self):
        x1, y1 = min(self.x1, self.x2), min(self.y1, self.y2)
        x2, y2 = max(self.x1, self.x2), max(self.y1, self.y2)
        return x1, y1, x2, y2

    def _update_rect(self):
        x1, y1, x2, y2 = self._bbox()
        # 选区框：亮青色边框 + 深灰填充（在暗色遮罩上清晰可见）
        if self.rect_id:
            self.canvas.coords(self.rect_id, x1, y1, x2, y2)
        else:
            self.rect_id = self.canvas.create_rectangle(
                x1, y1, x2, y2, outline="#00e5ff", width=2, fill="#1f1f1f"
            )
        # 尺寸角标
        if self.size_txt_id:
            self.canvas.coords(self.size_txt_id, x2 + 8, y2 + 2)
            self.canvas.itemconfigure(self.size_txt_id, text=f"{x2 - x1} × {y2 - y1}")
        else:
            self.size_txt_id = self.canvas.create_text(
                x2 + 8, y2 + 2, anchor="nw", fill="#00e5ff",
                font=("Consolas", 12), text=f"{x2 - x1} × {y2 - y1}",
            )


class ResultWindow:
    """置顶结果窗：逐条显示「原文 → 译文」，支持复制。"""

    def __init__(self, root, bbox, result, on_again=None, on_close=None):
        self.on_close = on_close
        self.top = tk.Toplevel(root)
        self.top.title("喵译 · 翻译结果")
        self.top.attributes("-topmost", True)
        try:
            self.top.attributes("-alpha", 0.97)
        except Exception:
            pass

        # 初始位置贴近截图区域
        if bbox:
            x = min(max(bbox[0], 20), self.top.winfo_screenwidth() - 500)
            y = min(max(bbox[1], 20), self.top.winfo_screenheight() - 360)
            self.top.geometry(f"+{x}+{y}")

        tk.Label(self.top, text="🐱 翻译结果", font=("Microsoft YaHei", 13, "bold")).pack(pady=(10, 4))

        frame = tk.Frame(self.top)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        self.text = tk.Text(
            frame, wrap="word", width=54, height=15,
            font=("Microsoft YaHei", 10), relief="solid", bd=1,
        )
        scroll = tk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        # 标签配色
        self.text.tag_configure("orig", foreground="#222222")
        self.text.tag_configure("trans", foreground="#0a8a5a")
        self.text.tag_configure("summary", foreground="#888888")
        self.text.tag_configure("error", foreground="#cc3333")

        self._fill(result)

        btn_frame = tk.Frame(self.top)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text="复制全部", command=self.copy_all).pack(side="left", padx=4)
        if on_again:
            ttk.Button(btn_frame, text="再截一张", command=on_again).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="关闭", command=self.close).pack(side="left", padx=4)

        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.focus_force()

    def _fill(self, result):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if isinstance(result, dict):
            if result.get("error"):
                self.text.insert("end", "⚠️ " + result["error"] + "\n", ("error",))
                raw = result.get("raw")
                if raw:
                    self.text.insert("end", "\n原始回答:\n" + str(raw) + "\n", ("summary",))
            else:
                items = result.get("items") or []
                if items:
                    for i, it in enumerate(items, 1):
                        original = it.get("original", "")
                        translation = it.get("translation", "")
                        self.text.insert("end", f"{i}. {original}\n", ("orig",))
                        self.text.insert("end", f"   → {translation}\n", ("trans",))
                        self.text.insert("end", "\n")
                summary = result.get("summary")
                if summary:
                    self.text.insert("end", f"💡 {summary}\n", ("summary",))
                if not items and not summary:
                    self.text.insert("end", "（未检测到外文文字）\n", ("summary",))
        else:
            self.text.insert("end", str(result) + "\n")
        self.text.configure(state="disabled")

    def copy_all(self):
        content = self.text.get("1.0", "end-1c")
        self.top.clipboard_clear()
        self.top.clipboard_append(content)

    def close(self):
        self.top.destroy()
        if self.on_close:
            self.on_close()


def make_tray_icon():
    """用 Pillow 画一个橙色猫脸托盘图标。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    orange = (255, 140, 66, 255)
    # 身体圆
    d.ellipse([6, 12, 58, 64], fill=orange)
    # 猫耳朵（两个三角）
    d.polygon([(4, 18), (20, 0), (22, 20)], fill=orange)
    d.polygon([(60, 18), (44, 0), (42, 20)], fill=orange)
    # 眼睛
    d.ellipse([18, 30, 27, 39], fill=(45, 40, 30, 255))
    d.ellipse([37, 30, 46, 39], fill=(45, 40, 30, 255))
    # 嘴（w 形）
    d.line([(29, 46), (32, 50), (35, 46)], fill=(45, 40, 30, 255), width=2)
    return img


class ScreenshotApp:
    """常驻后台：全局热键 + 托盘 + 单线程 tkinter 事件循环。"""

    def __init__(self, hotkey="ctrl+shift+t", once=False):
        self.hotkey = hotkey
        self.once = once
        self.busy = False
        self.q = queue.Queue()
        self.tray_icon = None

        self.root = tk.Tk()
        self.root.withdraw()  # 隐藏主窗口，只作为事件循环容器
        self.root.title(APP_NAME)

        self.base_url, self.model, self.api_key = vc.load_config()
        if not self.api_key:
            print("错误: 未找到 API Key。请在 screentrans/.env 设置 VISION_API_KEY。")
            self.root.destroy()
            sys.exit(1)

        self._register_hotkey()
        self.root.after(120, self._poll_queue)

    # ---------- 触发 ----------
    def _register_hotkey(self):
        try:
            import keyboard
            keyboard.add_hotkey(self.hotkey, self._enqueue_capture)
            print(f"全局热键已注册: {self.hotkey}")
        except Exception as e:
            print(f"热键注册失败（可能需管理员权限）: {e}")
            print("仍可通过系统托盘触发截图翻译。")

    def _enqueue_capture(self):
        self.q.put("capture")

    def _enqueue_quit(self):
        self.q.put("quit")

    # ---------- 事件循环 ----------
    def _poll_queue(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item == "capture":
                    self.start_capture()
                elif item == "quit":
                    self._cleanup()
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _cleanup(self):
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

    # ---------- 截图翻译流程 ----------
    def start_capture(self):
        if self.busy:
            return
        self.busy = True
        RegionSelector(self.root, self._on_region)

    def _on_region(self, bbox):
        if bbox is None:
            self.busy = False
            if self.once:
                self._cleanup()
                self.root.destroy()
            return
        threading.Thread(target=self._translate_worker, args=(bbox,), daemon=True).start()

    def _translate_worker(self, bbox):
        try:
            img = ImageGrab.grab(bbox=bbox)
            result = vc.translate_image(self.base_url, self.model, self.api_key, img)
        except Exception as e:
            result = {"error": f"[处理异常] {e}"}
        self.root.after(0, lambda: self._show_result(bbox, result))

    def _show_result(self, bbox, result):
        self.busy = False
        if self.once:
            on_close = lambda: (self._cleanup(), self.root.destroy())
            on_again = None
        else:
            on_close = None
            on_again = self.start_capture
        ResultWindow(self.root, bbox, result, on_again=on_again, on_close=on_close)

    # ---------- 托盘 ----------
    def _run_tray(self):
        from pystray import Icon, Menu, MenuItem
        menu = Menu(
            MenuItem("📷 截图翻译", lambda: self._enqueue_capture(), default=True),
            MenuItem("退出", lambda: self._enqueue_quit()),
        )
        self.tray_icon = Icon(APP_NAME, make_tray_icon(), menu=menu)
        self.tray_icon.run()

    # ---------- 启动 ----------
    def run(self):
        threading.Thread(target=self._run_tray, daemon=True).start()
        if self.once:
            self.start_capture()
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="喵译 · 截图翻译工具")
    parser.add_argument("mode", nargs="?", default="daemon",
                        help="daemon=常驻（默认）/ once=单次截图翻译")
    parser.add_argument("-k", "--hotkey", default="ctrl+shift+t", help="全局热键，默认 ctrl+shift+t")
    args = parser.parse_args()

    app = ScreenshotApp(hotkey=args.hotkey, once=(args.mode == "once"))
    app.run()


if __name__ == "__main__":
    main()
