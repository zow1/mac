#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FloatTrans · 悬浮翻译  macOS 版 v1.0.0

Windows 版的 macOS 移植：界面和功能保持一致，平台层换成 macOS 原生能力：
  * 取词：screencapture 截屏 + Vision 文字识别（本地离线）
  * 热键：Carbon RegisterEventHotKey（不需要辅助功能授权）
  * 发音：afplay / say
  * 自启动：LaunchAgent

翻译核心（有道 / Google / MyMemory + 语言与引擎设置）在 trans_core.py 里，
与 Windows 版共用同一份代码。

    python3 float_trans_mac.py          # 启动
    python3 float_trans_mac.py --check  # 环境自检，出问题先跑这个
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import tkinter as tk
from tkinter import font as tkfont

import trans_core as core
from trans_core import (LANGS, LANG_NAME, LANG_SHORT, DST_LANGS, ENGINES,
                        ENGINE_NAME, Result, Translator)

try:
    import mac_ocr as so
except Exception:                       # 取词不可用时，翻译本身照常工作
    so = None

try:
    from mac_hotkey import HotkeyListener
except Exception:
    HotkeyListener = None

# --------------------------------------------------------------------------- 常量

APP_NAME = "FloatTrans"
APP_TITLE = "悬浮翻译"
VERSION = "1.1.0"

CFG_DIR = os.path.join(os.path.expanduser("~"),
                       "Library", "Application Support", APP_NAME)
CFG_PATH = os.path.join(CFG_DIR, "config.json")
LOG_PATH = os.path.join(CFG_DIR, "app.log")
LOCK_PATH = os.path.join(CFG_DIR, "app.lock")

# 配色
BG = "#12141b"
BG_SOFT = "#191c26"
BG_INPUT = "#1e2230"
BORDER = "#2b3042"
ACCENT = "#5b9dff"
ACCENT_HI = "#8fc0ff"
TEXT = "#e9edf6"
MUTED = "#8b93a8"
DIM = "#5f6779"
ERROR = "#ff7b72"

FAMILY = "PingFang SC"
FAMILY_MONO = "Menlo"

DEFAULT_W = 450
MIN_H = 132
MAX_H = 660

POS_RE = re.compile(r"^([a-z]+\.(?:\s*[&/,]\s*[a-z]+\.)*)\s+(.+)$")

DEFAULTS = {
    "x": None,
    "y": None,
    "alpha": 0.96,
    "font_size": 13,
    "clipboard": True,
    "tts": False,
    "autostart": False,
    "collapsed": False,
    "hover": False,
    "hover_mod": "alt",
    "src_lang": "auto",
    "dst_lang": "zh",
    "engine": "auto",
}

_LOCK_HANDLE = None

# --------------------------------------------------------------------------- 小工具


def ensure_dir():
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
    except Exception:
        pass


def log(msg: str):
    try:
        ensure_dir()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


core.set_logger(log)


def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict):
    try:
        ensure_dir()
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_PATH)
    except Exception as e:
        log("save cfg failed: %r" % (e,))


def trim_def(text: str, limit: int = 52) -> str:
    """释义太长时在分号/逗号处截断，避免最后一行只剩一个字。"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in ("；", ";", "，", ","):
        i = head.rfind(sep)
        if i >= limit // 2:
            return head[:i + 1] + "…"
    return head + "…"


def mac_activate():
    """把本 App 提到最前，否则无边框窗口收不到键盘。"""
    try:
        from AppKit import NSApp
        NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        pass


def mac_float_everywhere(root):
    """让窗口浮在所有 Space / 全屏 App 之上（看视频全屏时也能看见）。"""
    try:
        from AppKit import (NSApp,
                            NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorFullScreenAuxiliary,
                            NSWindowCollectionBehaviorStationary)
        behavior = (NSWindowCollectionBehaviorCanJoinAllSpaces
                    | NSWindowCollectionBehaviorFullScreenAuxiliary
                    | NSWindowCollectionBehaviorStationary)
        for w in NSApp.windows():
            try:
                w.setCollectionBehavior_(behavior)
            except Exception:
                pass
    except Exception as e:
        log("collectionBehavior fail %r" % (e,))


# --------------------------------------------------------------------------- 发音


def play_url(url: str, fallback_text: str = ""):
    """下载有道发音 mp3 用 afplay 播放；没有 url 就退回系统 say。"""
    def run():
        path = os.path.join(tempfile.gettempdir(), "floattrans_tts.mp3")
        try:
            if url:
                req = urllib.request.Request(
                    url, headers={"User-Agent": core.UA})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = r.read()
                if len(data) > 800:
                    with open(path, "wb") as f:
                        f.write(data)
                    subprocess.run(["afplay", path], timeout=25,
                                   capture_output=True)
                    return
        except Exception as e:
            log("tts fail %r" % (e,))
        if fallback_text:
            try:
                subprocess.run(["say", fallback_text[:200]], timeout=25,
                               capture_output=True)
            except Exception as e:
                log("say fail %r" % (e,))

    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------- 自启动


def _launch_agent_path() -> str:
    return os.path.expanduser("~/Library/LaunchAgents/com.floattrans.agent.plist")


def set_autostart(on: bool) -> bool:
    """开机自启：写一个 LaunchAgent plist。只在用 python 直接跑时可靠。"""
    path = _launch_agent_path()
    try:
        if on:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            script = os.path.abspath(__file__)
            plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.floattrans.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
    <string>%s</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
""" % (sys.executable, script)
            with open(path, "w", encoding="utf-8") as f:
                f.write(plist)
            subprocess.run(["launchctl", "load", "-w", path],
                           capture_output=True, timeout=10)
        else:
            if os.path.exists(path):
                subprocess.run(["launchctl", "unload", "-w", path],
                               capture_output=True, timeout=10)
                os.remove(path)
        return True
    except Exception as e:
        log("autostart fail %r" % (e,))
        return False


# --------------------------------------------------------------------------- 单实例


def acquire_lock():
    """同一个用户只允许跑一个实例，重复启动直接退出。"""
    global _LOCK_HANDLE
    try:
        import fcntl
        ensure_dir()
        f = open(LOCK_PATH, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_HANDLE = f
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- 自绘滑块


class MiniSlider(tk.Canvas):
    """比 tk.Scale 好看很多的极简滑块。"""

    def __init__(self, master, lo, hi, value, on_change, height=24):
        super().__init__(master, height=height, bg=BG, bd=0,
                         highlightthickness=0, cursor="hand2")
        self.lo, self.hi = lo, hi
        self.value = value
        self.on_change = on_change
        self._drag = False
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 200
        h = self.winfo_height() or 24
        pad = 9
        y = h // 2
        self.create_line(pad, y, w - pad, y, fill="#2a3142", width=4,
                         capstyle="round")
        ratio = (self.value - self.lo) / float(max(1, self.hi - self.lo))
        x = pad + (w - 2 * pad) * ratio
        self.create_line(pad, y, x, y, fill=ACCENT, width=4, capstyle="round")
        self.create_oval(x - 6, y - 6, x + 6, y + 6, fill=ACCENT,
                         outline=BG, width=2)

    def _press(self, e):
        self._drag = True
        self._set_from_x(e.x)

    def _move(self, e):
        if self._drag:
            self._set_from_x(e.x)

    def _release(self, _e):
        self._drag = False

    def _set_from_x(self, x):
        w = self.winfo_width() or 200
        pad = 9
        ratio = min(1.0, max(0.0, (x - pad) / float(max(1, w - 2 * pad))))
        v = int(round(self.lo + (self.hi - self.lo) * ratio))
        if v != self.value:
            self.value = v
            self._draw()
            self.on_change(v)


# --------------------------------------------------------------------------- 占位输入框


class PlaceholderEntry(tk.Entry):
    def __init__(self, master, placeholder: str, **kw):
        super().__init__(master, **kw)
        self.placeholder = placeholder
        self._ph = False
        self.bind("<FocusIn>", self._on_in, add="+")
        self.bind("<FocusOut>", self._on_out, add="+")
        self._show()

    def _show(self):
        if not super().get():
            self._ph = True
            self.configure(fg=DIM)
            super().insert(0, self.placeholder)

    def _hide(self):
        if self._ph:
            self._ph = False
            super().delete(0, "end")
            self.configure(fg=TEXT)

    def _on_in(self, _e):
        self._hide()

    def _on_out(self, _e):
        self._show()

    def get_text(self) -> str:
        return "" if self._ph else super().get()

    def set_text(self, text: str):
        self._hide()
        super().delete(0, "end")
        super().insert(0, text)


# --------------------------------------------------------------------------- 框选截图


class RegionSelector:
    """整屏蒙一层截图，拖动框选要识别的区域。

    两个坑都在这儿踩过：
      1. 必须**先截屏再建窗口**。反过来的话截到的是自己的黑窗口，整屏全黑。
      2. macOS 的 Tk 对 stipple 支持不好，用灰点矩形压暗会变成纯黑。
         所以这里不做压暗，只描一圈边框 + 亮色选区来提示「在框选中」。
    松手回调 on_done(x, y, w, h)，坐标是屏幕点坐标。
    """

    def __init__(self, root, on_done, on_cancel):
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.done = False
        self.x0 = self.y0 = None
        self.photo = None

        sw, sh = so.screen_size()
        self.vw, self.vh = sw, sh

        # 先截屏，再建窗口
        self._shot = self._grab(sw, sh)

        win = tk.Toplevel(root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#0b0d13")
        win.geometry("%dx%d+0+0" % (sw, sh))

        cv = tk.Canvas(win, width=sw, height=sh, bg="#0b0d13", bd=0,
                       highlightthickness=0, cursor="crosshair")
        cv.pack(fill="both", expand=True)
        self.cv = cv

        self.photo = self._load_photo()
        if self.photo is not None:
            cv.create_image(0, 0, image=self.photo, anchor="nw")
        else:
            # 图实在加载不了（老 Tk 不认 PNG/GIF 时）：退成半透明黑层，
            # 至少还能看见底下的字，不至于整屏全黑没法框
            try:
                win.attributes("-alpha", 0.35)
            except Exception:
                pass

        # 四边描一圈，提示已经进入框选状态
        cv.create_rectangle(1, 1, sw - 2, sh - 2, outline=ACCENT, width=2)
        self.sel = cv.create_rectangle(0, 0, 0, 0, outline=ACCENT, width=2,
                                       state="hidden")

        self.size_lbl = tk.Label(win, text="", bg="#0b0d13", fg=ACCENT,
                                 font=(FAMILY, 11), padx=7, pady=2, bd=0)
        self.hint = tk.Label(win, text="拖动框选要翻译的区域　·　Esc 取消",
                             bg="#0b0d13", fg=TEXT, font=(FAMILY, 12),
                             padx=16, pady=6, bd=0)
        self.hint.place(x=sw // 2, y=24, anchor="n")

        cv.bind("<Button-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        win.bind("<Escape>", lambda e: self._cancel())

        win.update_idletasks()
        win.lift()
        mac_activate()
        try:
            win.focus_force()
        except Exception:
            pass

    # ---------------------------------------------------------------- 背景图
    def _grab(self, sw, sh):
        try:
            path, iw, ih = so.capture(0, 0, sw, sh)
            return path, iw, ih
        except Exception as e:
            log("overlay grab fail %r" % (e,))
            return None

    def _load_photo(self):
        """把截图变成 Tk 能显示的图。Tk 8.5 不认 PNG，所以再退一步用 GIF。"""
        if not self._shot:
            return None
        path, iw, ih = self._shot
        for fmt in ("png", "gif"):
            try:
                if fmt == "gif":
                    path, iw, ih = so.capture(0, 0, self.vw, self.vh, fmt="gif")
                img = tk.PhotoImage(file=path)
                # Retina 上截图是点数的 2 倍，缩一半才和屏幕坐标对齐
                if self.vw and iw >= self.vw * 2 - 2:
                    img = img.subsample(2)
                return img
            except Exception as e:
                log("overlay image(%s) fail %r" % (fmt, e))
        return None

    # ---------------------------------------------------------------- 事件
    def _press(self, e):
        self.x0, self.y0 = e.x, e.y
        self.hint.place_forget()
        self._drag(e)

    def _drag(self, e):
        if self.x0 is None:
            return
        x1 = max(0, min(e.x, self.vw))
        y1 = max(0, min(e.y, self.vh))
        l, t = min(self.x0, x1), min(self.y0, y1)
        r, b = max(self.x0, x1), max(self.y0, y1)
        w, h = r - l, b - t
        if w < 2 or h < 2:
            return

        self.cv.coords(self.sel, l, t, r, b)
        self.cv.itemconfigure(self.sel, state="normal")

        self.size_lbl.configure(text="%d × %d" % (w, h))
        ly = t - 30 if t > 34 else b + 8
        self.size_lbl.place(x=l, y=ly)

    def _release(self, e):
        if self.x0 is None:
            return
        self._drag(e)
        l = min(self.x0, e.x)
        t = min(self.y0, e.y)
        w = abs(e.x - self.x0)
        h = abs(e.y - self.y0)
        if w < 8 or h < 8:
            self._cancel()
            return
        self._finish(l, t, w, h)

    def _finish(self, x, y, w, h):
        if self.done:
            return
        self.done = True
        try:
            self.win.destroy()
        except Exception:
            pass
        # 等窗口真正消失再截屏，否则会把压暗层拍进去
        try:
            self.on_done(x, y, w, h)
        except Exception as e:
            log("region done fail %r" % (e,))

    def _cancel(self):
        if self.done:
            return
        self.done = True
        try:
            self.win.destroy()
        except Exception:
            pass
        try:
            self.on_cancel()
        except Exception:
            pass


# --------------------------------------------------------------------------- 悬停气泡


class HoverPopup:
    """鼠标旁边的小气泡：上面原文，下面译文。"""

    def __init__(self, app):
        self.app = app
        self.win = None
        self.job = None
        self.visible = False
        self.shown_at = 0.0
        self.last_trans = ""

    def show(self, cx, cy, src, trans, engine):
        self.hide()
        win = tk.Toplevel(self.app.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG)

        wrap = tk.Frame(win, bg=BG, highlightbackground=BORDER,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True)

        inner = tk.Frame(wrap, bg=BG)
        inner.pack(fill="both", expand=True, padx=12, pady=9)

        wl = 360
        if src:
            tk.Label(inner, text=src, bg=BG, fg=MUTED, font=(FAMILY, 10),
                     wraplength=wl, justify="left", anchor="w").pack(
                fill="x", anchor="w")
        tk.Label(inner, text=trans, bg=BG, fg=TEXT,
                 font=(FAMILY, int(self.app.cfg.get("font_size", 13))),
                 wraplength=wl, justify="left", anchor="w").pack(
            fill="x", anchor="w", pady=(4 if src else 0, 0))
        tk.Label(inner, text=engine, bg=BG, fg=DIM,
                 font=(FAMILY, 9), anchor="w").pack(fill="x", pady=(4, 0))

        win.update_idletasks()
        w = min(wl + 26, max(140, wrap.winfo_reqwidth()))
        h = wrap.winfo_reqheight()
        sw, sh = so.screen_size() if so else (1440, 900)
        x = cx + 20
        y = cy + 24
        if x + w > sw - 8:
            x = max(8, cx - w - 20)
        if y + h > sh - 8:
            y = max(8, cy - h - 16)
        win.geometry("%dx%d+%d+%d" % (w, h, int(x), int(y)))

        self.last_trans = trans
        for widget in (wrap, inner):
            widget.bind("<Button-1>", lambda e: self._copy())
        win.bind("<Button-1>", lambda e: self._copy())

        self.visible = True
        self.shown_at = time.time()
        self.job = self.app.root.after(9000, self.hide)

    def _copy(self):
        try:
            self.app._ignore_clip = self.last_trans
            self.app._last_clip = self.last_trans
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(self.last_trans)
            self.app._set_status("译文已复制")
        except Exception:
            pass
        self.hide()

    def hide(self):
        if self.job:
            try:
                self.app.root.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        self.visible = False
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None


# --------------------------------------------------------------------------- 主程序


class FloatTrans:
    def __init__(self):
        self.cfg = load_cfg()
        self.translator = Translator()
        self.q: queue.Queue = queue.Queue()
        self.req_id = 0
        self.history: list[str] = []
        self.hist_idx = 0
        self.last_src = ""
        self.last_result = ""
        self.last_tts = ""
        self._last_clip = ""
        self._ignore_clip = ""
        self._drag = None
        self._moved = False
        self._sizing = False
        self._size_job = None
        self._save_job = None
        self.settings_win = None
        self._lang_labels: list[tuple] = []

        self.show_event = threading.Event()
        self.shot_event = threading.Event()
        self.hover_event = threading.Event()
        self.hotkey = HotkeyListener({
            "show": self.show_event,
            "shot": self.shot_event,
            "hover": self.hover_event,
        }) if HotkeyListener else None

        # 取词相关
        self._selector = None
        self._hover_anchor = None
        self._hover_busy = False
        self._hover_pos = None
        self.hq: queue.Queue = queue.Queue()
        self.popup = HoverPopup(self)

        self.W = DEFAULT_W
        self._build_root()
        self.root.report_callback_exception = self._on_tk_error
        self._build_ui()

        self._apply_position()
        self._start_threads()

        # 启动时不立刻翻译剪贴板里已有的内容
        try:
            self._last_clip = (self.root.clipboard_get() or "").strip()
        except Exception:
            self._last_clip = ""

        self.root.after(40, self._drain_queue)
        self.root.after(700, self._watch_clipboard)
        self.root.after(300, lambda: mac_float_everywhere(self.root))
        self.root.after(60, self._autosize)
        self.root.after(400, self._hover_tick)

    def _on_tk_error(self, exc, val, tb):
        try:
            import traceback
            log("TK ERROR:\n" + "".join(
                traceback.format_exception(exc, val, tb)))
        except Exception:
            pass

    def _schedule_save(self):
        if self._save_job:
            return
        self._save_job = self.root.after(500, self._do_save)

    def _do_save(self):
        self._save_job = None
        save_cfg(self.cfg)

    def _flush_save(self):
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        save_cfg(self.cfg)

    # ------------------------------------------------------------------ 窗口
    def _build_root(self):
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(self.cfg["alpha"]))
        self.root.geometry("%dx%d" % (self.W, 190))
        self.root.bind("<Escape>", lambda e: self.collapse())
        self.root.bind("<Control-MouseWheel>", self._zoom)

    def _apply_position(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if x is None or y is None or not (0 <= x < sw - 80) \
                or not (0 <= y < sh - 80):
            x = sw - self.W - 70
            y = 100
        self.root.geometry("+%d+%d" % (int(x), int(y)))

    def _start_threads(self):
        if self.hotkey:
            self.hotkey.start()
        threading.Thread(target=self.translator.probe_google,
                         daemon=True).start()
        self.root.after(1200, self._show_hotkey_hint)

    def _show_hotkey_hint(self):
        label = self.hotkey.label if self.hotkey else ""
        if label:
            shot = self.hotkey.labels.get("shot")
            hint = label + " 呼出"
            if shot:
                hint += " · " + shot + " 截图"
            self.status_hint.configure(text=hint)
        else:
            self.status_hint.configure(text="热键未注册")

    # ------------------------------------------------------------------ 界面
    def _build_ui(self):
        self.container = tk.Frame(self.root, bg=BG,
                                  highlightbackground=BORDER,
                                  highlightthickness=1)
        self.container.pack(fill="both", expand=True)

        self.expanded = tk.Frame(self.container, bg=BG)
        self.expanded.pack(fill="both", expand=True)

        self._build_header()
        self._build_body()
        self._build_status()

        self.ball = tk.Canvas(self.container, bg=BG, highlightthickness=0,
                              bd=0, cursor="hand2")
        self._draw_ball()
        self.ball.bind("<Configure>", self._draw_ball)
        self.ball.bind("<Button-1>", self._ball_press)
        self.ball.bind("<B1-Motion>", self._ball_move)
        self.ball.bind("<ButtonRelease-1>", self._ball_release)

    def _draw_ball(self, _e=None):
        w = self.ball.winfo_width() or 54
        h = self.ball.winfo_height() or 54
        self.ball.delete("all")
        self.ball.create_oval(2, 2, w - 2, h - 2, fill="#1b2030",
                              outline=ACCENT, width=2)
        self.ball.create_text(w / 2, h / 2, text="译", fill=ACCENT,
                              font=(FAMILY, 16, "bold"))

    def _build_header(self):
        head = tk.Frame(self.expanded, bg=BG, height=32)
        head.pack(fill="x", padx=12, pady=(10, 2))
        head.pack_propagate(False)

        logo = tk.Label(head, text="译", bg=ACCENT, fg="#ffffff",
                        font=(FAMILY, 10, "bold"), padx=5, pady=1)
        logo.pack(side="left", padx=(0, 8))
        title = tk.Label(head, text=APP_TITLE, bg=BG, fg=MUTED,
                         font=(FAMILY, 10))
        title.pack(side="left")

        self.btn_shot = self._btn(head, "截图", self.start_region_shot)
        self.btn_shot.pack(side="left", padx=(12, 0))
        self.btn_hover = tk.Label(head, text="悬停", bg=BG, fg=MUTED,
                                  font=(FAMILY, 10), cursor="hand2", padx=4)
        self.btn_hover.pack(side="left", padx=(4, 0))
        self.btn_hover.bind("<Button-1>", lambda e: self.toggle_hover())
        self.btn_hover.bind("<Enter>",
                            lambda e: self.btn_hover.configure(fg=ACCENT_HI))
        self.btn_hover.bind("<Leave>", lambda e: self._refresh_hover_btn())
        self._refresh_hover_btn()

        btn_close = self._btn(head, "✕", self.quit, hover=ERROR)
        btn_close.pack(side="right")
        btn_min = self._btn(head, "─", self.collapse)
        btn_min.pack(side="right", padx=(0, 2))
        self.btn_pin = self._btn(head, "⚙", self.open_settings)
        self.btn_pin.pack(side="right", padx=(0, 2))

        for w in (head, logo, title):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.configure(cursor="fleur")

    def _build_body(self):
        body = tk.Frame(self.expanded, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 0))

        row = tk.Frame(body, bg=BG_INPUT)
        row.pack(fill="x")

        self.entry = PlaceholderEntry(
            row, "输入 / 粘贴 单词或句子…",
            bg=BG_INPUT, fg=TEXT, insertbackground=ACCENT,
            relief="flat", bd=0, font=(FAMILY, 12),
            selectbackground="#33507f", selectforeground="#ffffff")
        self.entry.pack(side="left", fill="x", expand=True, ipady=8,
                        padx=(10, 4))
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Up>", lambda e: self._history(-1))
        self.entry.bind("<Down>", lambda e: self._history(1))
        self.entry.bind("<Command-v>",
                        lambda e: self.root.after(60,
                                                  self._translate_from_entry))

        go = tk.Label(row, text="译", bg=ACCENT, fg="#ffffff",
                      font=(FAMILY, 12, "bold"), padx=14, cursor="hand2")
        go.pack(side="right", fill="y")
        go.bind("<Button-1>", lambda e: self._translate_from_entry())
        go.bind("<Enter>", lambda e: go.configure(bg=ACCENT_HI))
        go.bind("<Leave>", lambda e: go.configure(bg=ACCENT))

        # 语言 / 引擎 快捷切换条
        lbar = tk.Frame(body, bg=BG)
        lbar.pack(fill="x", pady=(7, 0))
        tk.Label(lbar, text="源", bg=BG, fg=DIM,
                 font=(FAMILY, 9)).pack(side="left")
        self._chip(lbar, self._menu_src, "src").pack(side="left", padx=(3, 0))
        tk.Label(lbar, text="→", bg=BG, fg=DIM,
                 font=(FAMILY, 9)).pack(side="left", padx=4)
        self._chip(lbar, self._menu_dst, "dst").pack(side="left")
        self._chip(lbar, self._menu_engine, "engine",
                   prefix="引擎 ").pack(side="right")

        self.lbl_src = tk.Label(body, text="", bg=BG, fg=DIM,
                                font=(FAMILY, 10), anchor="w", justify="left",
                                wraplength=self.W - 32)
        self.lbl_src.pack(fill="x", pady=(8, 0))
        self.lbl_src.pack_forget()

        size0 = int(self.cfg["font_size"])
        self.result_font = tkfont.Font(family=FAMILY, size=size0)
        self.fonts = {
            "trans": self.result_font,
            "phon": tkfont.Font(family=FAMILY, size=max(9, size0 - 4)),
            "pos": tkfont.Font(family=FAMILY, size=max(8, size0 - 4)),
            "def": tkfont.Font(family=FAMILY, size=max(9, size0 - 2)),
        }
        self.txt_holder = tk.Frame(body, bg=BG, width=self.W - 24, height=26)
        self.txt_holder.pack(fill="x", pady=(4, 0))
        self.txt_holder.pack_propagate(False)

        self.txt = tk.Text(self.txt_holder, height=1, width=1, wrap="word",
                           bg=BG, fg=TEXT, relief="flat", bd=0,
                           highlightthickness=0, padx=0, pady=0,
                           spacing1=1, spacing3=2, cursor="arrow",
                           font=self.result_font,
                           selectbackground="#33507f",
                           selectforeground="#ffffff")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("trans", foreground=TEXT,
                               font=self.fonts["trans"])
        self.txt.tag_configure("phon", foreground=MUTED,
                               font=self.fonts["phon"])
        self.txt.tag_configure("pos", foreground=ACCENT,
                               font=self.fonts["pos"])
        self.txt.tag_configure("def", foreground=MUTED,
                               font=self.fonts["def"])
        self.txt.tag_configure("err", foreground=ERROR,
                               font=self.fonts["def"])
        self.txt.bind("<Double-Button-1>", lambda e: self.copy_result())
        self.txt.bind("<MouseWheel>", self._on_wheel)
        self.txt.configure(state="disabled")
        self.blocks = [("", "trans")]

    def _build_status(self):
        bar = tk.Frame(self.expanded, bg=BG, height=24)
        bar.pack(fill="x", padx=12, pady=(6, 8))
        bar.pack_propagate(False)

        self.status_hint = tk.Label(bar, text="", bg=BG, fg=DIM,
                                    font=(FAMILY, 9), anchor="w")
        self.status_hint.pack(side="left")
        self.status_left = tk.Label(bar, text="就绪", bg=BG, fg=DIM,
                                    font=(FAMILY, 9), anchor="w")
        self.status_left.pack(side="left", padx=(8, 0))

        self.btn_copy = self._btn(bar, "复制", self.copy_result, size=9)
        self.btn_copy.pack(side="right")
        self.btn_speak = self._btn(bar, "发音", self.speak, size=9)
        self.btn_speak.pack(side="right", padx=(0, 6))
        self.btn_clip = self._btn(bar, "自动", self.toggle_clipboard, size=9)
        self.btn_clip.pack(side="right", padx=(0, 10))
        self._refresh_clip_btn()

    def _btn(self, parent, text, cmd, size=10, hover=ACCENT_HI, color=None):
        color = color or MUTED
        lbl = tk.Label(parent, text=text, bg=BG, fg=color,
                       font=(FAMILY, size), cursor="hand2", padx=4)
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=hover))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=color))
        return lbl

    # ------------------------------------------------------------------ 语言 / 引擎
    def _chip(self, parent, cmd, kind, prefix=""):
        lbl = tk.Label(parent, text="", bg=BG, fg=ACCENT,
                       font=(FAMILY, 9), cursor="hand2", padx=2)
        lbl.bind("<Button-1>", lambda e: cmd(lbl))
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=ACCENT_HI))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=ACCENT))
        self._lang_labels.append((kind, lbl, prefix))
        self._refresh_lang_bar()
        return lbl

    def _choice_row(self, parent, title, kind, cmd):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=16, pady=(9, 0))
        tk.Label(f, text=title, bg=BG, fg=MUTED,
                 font=(FAMILY, 10)).pack(side="left")
        lbl = tk.Label(f, text="", bg=BG, fg=ACCENT,
                       font=(FAMILY, 10), cursor="hand2", padx=2)
        lbl.pack(side="right")
        lbl.bind("<Button-1>", lambda e: cmd(lbl))
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=ACCENT_HI))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=ACCENT))
        self._lang_labels.append((kind, lbl, ""))
        self._refresh_lang_bar()
        return lbl

    def _refresh_lang_bar(self):
        src = self.cfg.get("src_lang") or "auto"
        dst = self.cfg.get("dst_lang") or "zh"
        eng = self.cfg.get("engine") or "auto"
        vals = {
            "src": LANG_SHORT.get(src, src),
            "dst": LANG_NAME.get(dst, dst),
            "engine": ENGINE_NAME.get(eng, eng),
        }
        alive = []
        for kind, lbl, prefix in getattr(self, "_lang_labels", []):
            try:
                if not lbl.winfo_exists():
                    continue
                lbl.configure(text=prefix + vals.get(kind, ""))
                alive.append((kind, lbl, prefix))
            except Exception:
                pass
        self._lang_labels = alive

    def _popup(self, anchor, options, current, on_pick):
        """暗色下拉菜单。macOS 上 Tk 菜单也是自绘的，样式能跟着走。"""
        m = tk.Menu(anchor, tearoff=0, bg=BG_SOFT, fg=TEXT,
                    activebackground=ACCENT, activeforeground="#ffffff",
                    bd=0, relief="flat", font=(FAMILY, 12))
        var = tk.StringVar(master=m, value=current)
        for name, code in options:
            m.add_radiobutton(label=name, value=code, variable=var,
                              command=lambda c=code: on_pick(c))
        try:
            m.tk_popup(anchor.winfo_rootx(),
                       anchor.winfo_rooty() + anchor.winfo_height() + 2)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _menu_src(self, anchor):
        self._popup(anchor, LANGS, self.cfg.get("src_lang") or "auto",
                    self._set_src)

    def _menu_dst(self, anchor):
        self._popup(anchor, DST_LANGS, self.cfg.get("dst_lang") or "zh",
                    self._set_dst)

    def _menu_engine(self, anchor):
        self._popup(anchor, ENGINES, self.cfg.get("engine") or "auto",
                    self._set_engine)

    def _after_lang_change(self, hint):
        self._schedule_save()
        self._refresh_lang_bar()
        self._set_status(hint)
        if self.last_src and self.last_result:
            self.translate(self.last_src)

    def _set_src(self, code):
        self.cfg["src_lang"] = code
        self._after_lang_change("源语言：%s" % LANG_SHORT.get(code, code))

    def _set_dst(self, code):
        self.cfg["dst_lang"] = code
        self._after_lang_change("目标语言：%s" % LANG_NAME.get(code, code))

    def _set_engine(self, code):
        self.cfg["engine"] = code
        self._after_lang_change("翻译引擎：%s" % ENGINE_NAME.get(code, code))

    # ------------------------------------------------------------------ 拖拽
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(),
                      e.y_root - self.root.winfo_y())
        self._moved = False

    def _drag_move(self, e):
        if not self._drag:
            return
        x = e.x_root - self._drag[0]
        y = e.y_root - self._drag[1]
        self.root.geometry("+%d+%d" % (x, y))
        self._moved = True

    def _drag_end(self, _e):
        if self._drag and self._moved:
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            self._schedule_save()
        self._drag = None

    def _ball_press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(),
                      e.y_root - self.root.winfo_y())
        self._moved = False

    def _ball_move(self, e):
        self._drag_move(e)

    def _ball_release(self, e):
        moved = self._moved
        self._drag_end(e)
        if not moved:
            self.expand()

    # ------------------------------------------------------------------ 收起 / 展开
    def collapse(self):
        if not self.expanded.winfo_ismapped():
            return
        self.cfg["x"] = self.root.winfo_x()
        self.cfg["y"] = self.root.winfo_y()
        self.cfg["collapsed"] = True
        self._schedule_save()
        self.expanded.pack_forget()
        self.ball.pack(fill="both", expand=True)
        x, y = self._clamp(self.root.winfo_x(), self.root.winfo_y(), 54, 54)
        self.root.geometry("54x54+%d+%d" % (x, y))

    def expand(self):
        self.cfg["collapsed"] = False
        self._schedule_save()
        self.ball.pack_forget()
        self.expanded.pack(fill="both", expand=True)
        x, y = self._clamp(self.root.winfo_x(), self.root.winfo_y(),
                           self.W, 190)
        self.root.geometry("%dx%d+%d+%d" % (self.W, 190, x, y))
        self.root.lift()
        mac_activate()
        self.root.focus_force()
        self.entry.focus_set()
        self.root.after(30, self._autosize)

    def _clamp(self, x, y, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(int(x), sw - w - 4))
        y = max(0, min(int(y), sh - h - 60))
        return x, y

    # ------------------------------------------------------------------ 翻译
    def _on_enter(self, _e=None):
        self._translate_from_entry()
        return "break"

    def _translate_from_entry(self):
        text = self.entry.get_text().strip()
        if text:
            self.translate(text)

    def _lang_args(self):
        return (self.cfg.get("src_lang") or "auto",
                self.cfg.get("dst_lang") or "zh",
                self.cfg.get("engine") or "auto")

    def translate(self, text: str):
        text = text.strip()
        if not text:
            return
        self.req_id += 1
        rid = self.req_id
        self.last_src = text
        self._set_status("翻译中…")
        if not self.history or self.history[0] != text:
            self.history.insert(0, text)
            del self.history[40:]
        self.hist_idx = 0
        threading.Thread(target=self._work, args=(rid, text),
                         daemon=True).start()

    def _work(self, rid, text):
        t0 = time.time()
        try:
            res = self.translator.translate(text, *self._lang_args())
            res.elapsed = time.time() - t0
            self.q.put(("ok", rid, res))
        except Exception as e:
            log("translate fail %r" % (e,))
            self.q.put(("err", rid, str(e)))

    def _drain_queue(self):
        try:
            while True:
                kind, rid, payload = self.q.get_nowait()
                if kind == "ocr":
                    self._after_ocr(payload)
                    continue
                if rid != self.req_id:
                    continue
                if kind == "ok":
                    self._render(payload)
                else:
                    self._render_error(payload)
        except queue.Empty:
            pass

        try:
            while True:
                kind, pos, payload = self.hq.get_nowait()
                self._hover_busy = False
                if kind == "ok" and pos:
                    src, res = payload
                    self._show_hover(pos[0], pos[1], src, res)
        except queue.Empty:
            pass

        if self.show_event.is_set():
            self.show_event.clear()
            self.show_window()
        if self.shot_event.is_set():
            self.shot_event.clear()
            self.start_region_shot()
        if self.hover_event.is_set():
            self.hover_event.clear()
            self.toggle_hover()
        self.root.after(40, self._drain_queue)

    def _render(self, res: Result):
        self.last_result = res.text
        self.last_tts = res.tts_url
        blocks = [(res.text, "trans")]
        if res.phonetic:
            blocks.append((res.phonetic, "phon"))
        for d in res.defs[:2]:
            d = trim_def(d)
            m = POS_RE.match(d)
            if m:
                blocks.append((m.group(1), "pos"))
                blocks.append((m.group(2), "def"))
            else:
                blocks.append((d, "def"))
        self.blocks = blocks

        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        for i, (text, tag) in enumerate(blocks):
            if i:
                self.txt.insert("end", "\n")
            self.txt.insert("end", text, tag)
        self.txt.configure(state="disabled")

        src = res.src if len(res.src) > 26 else ""
        self.lbl_src.configure(text=src)
        if src:
            self.lbl_src.pack(fill="x", pady=(8, 0), before=self.txt_holder)
        else:
            self.lbl_src.pack_forget()

        self._set_status("%s · %.2fs" % (res.engine, res.elapsed))
        self._autosize()

        if self.cfg.get("tts") and res.tts_url:
            play_url(res.tts_url, res.src)

    def _render_error(self, msg: str):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("end", msg, "err")
        self.txt.configure(state="disabled")
        self.blocks = [(msg, "err")]
        self.last_result = ""
        self.last_tts = ""
        self.lbl_src.pack_forget()
        self._set_status("翻译失败")
        self._autosize()

    def _autosize(self):
        if self._size_job:
            try:
                self.root.after_cancel(self._size_job)
            except Exception:
                pass
        self._size_job = self.root.after_idle(self._do_autosize)

    def _wrap_count(self, text: str, font, width: int) -> int:
        total = 0
        for para in text.split("\n"):
            if not para:
                total += 1
                continue
            cur = 0
            lines = 1
            for tok in re.findall(r"\S+\s*|\s+", para):
                w = font.measure(tok)
                if cur + w <= width:
                    cur += w
                    continue
                if cur > 0:
                    lines += 1
                    cur = 0
                if w <= width:
                    cur = w
                    continue
                for ch in tok:
                    cw = font.measure(ch)
                    if cur + cw > width and cur > 0:
                        lines += 1
                        cur = 0
                    cur += cw
            total += lines
        return max(1, total)

    def _do_autosize(self):
        self._size_job = None
        if self._sizing or not self.expanded.winfo_ismapped():
            return
        self._sizing = True
        try:
            width = max(80, self.W - 28)
            line_h = int(self.result_font.metrics("linespace")) + 3
            total_lines = 0
            for text, tag in getattr(self, "blocks", [("", "trans")]):
                font = self.fonts.get(tag, self.result_font)
                total_lines += self._wrap_count(text, font, width)
            self.txt_holder.configure(
                height=max(line_h, total_lines * line_h + 2))
            self.root.update_idletasks()
            h = max(MIN_H, min(self.expanded.winfo_reqheight(), MAX_H))
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry("%dx%d+%d+%d" % (self.W, h, x, y))
        except Exception as e:
            log("autosize fail %r" % (e,))
        finally:
            self._sizing = False

    def _set_status(self, text: str):
        self.status_left.configure(text=text)

    def _on_wheel(self, e):
        self.txt.yview_scroll(-1 * (e.delta // 120 or 1), "units")

    # ------------------------------------------------------------------ 其它动作
    def copy_result(self):
        if not self.last_result:
            return
        self._ignore_clip = self.last_result
        self._last_clip = self.last_result
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_result)
        self._set_status("已复制")
        self.root.after(1400, lambda: self._set_status("就绪"))

    def speak(self):
        if self.last_tts:
            play_url(self.last_tts, self.last_src)
        elif self.last_src:
            url = ("https://dict.youdao.com/dictvoice?type=2&audio="
                   + urllib.parse.quote(self.last_src[:120]))
            play_url(url, self.last_src)

    def toggle_clipboard(self):
        self.cfg["clipboard"] = not self.cfg["clipboard"]
        self._schedule_save()
        self._refresh_clip_btn()

    def _refresh_clip_btn(self):
        on = bool(self.cfg.get("clipboard"))
        color = ACCENT if on else DIM
        self.btn_clip.configure(fg=color)
        self.btn_clip.configure(text="自动" if on else "手动")
        self.btn_clip.bind("<Leave>",
                           lambda e: self.btn_clip.configure(fg=color))

    def _history(self, step: int):
        if not self.history:
            return "break"
        self.hist_idx = max(0, min(len(self.history) - 1,
                                   self.hist_idx + step))
        self.entry.set_text(self.history[self.hist_idx])
        return "break"

    def _zoom(self, e):
        size = int(self.cfg["font_size"]) + (1 if e.delta > 0 else -1)
        size = max(10, min(22, size))
        self.cfg["font_size"] = size
        self._schedule_save()
        self._apply_font_size()

    def _apply_font_size(self):
        size = int(self.cfg["font_size"])
        self.fonts["trans"].configure(size=size)
        self.fonts["phon"].configure(size=max(9, size - 4))
        self.fonts["pos"].configure(size=max(8, size - 4))
        self.fonts["def"].configure(size=max(9, size - 2))
        self._autosize()

    # ------------------------------------------------------------------ 剪贴板
    def _watch_clipboard(self):
        self.root.after(650, self._watch_clipboard)
        if not self.cfg.get("clipboard"):
            return
        if not self.expanded.winfo_ismapped():
            return
        try:
            txt = self.root.clipboard_get()
        except Exception:
            return
        if not isinstance(txt, str):
            return
        txt = txt.strip()
        if not txt or txt == self._last_clip or txt == self._ignore_clip:
            return
        self._last_clip = txt
        if len(txt) > 400 or txt.count("\n") > 6:
            return
        if txt == self.last_src:
            return
        self.entry.set_text(txt)
        self.translate(txt)

    # ------------------------------------------------------------------ 显示
    def show_window(self):
        if not self.expanded.winfo_ismapped():
            self.expand()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()
        mac_activate()
        self.root.focus_force()
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def quit(self):
        self._flush_save()
        try:
            if self.hotkey:
                self.hotkey.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ 设置
    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return

        win = tk.Toplevel(self.root)
        self.settings_win = win
        win.overrideredirect(True)
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.geometry("300x330")
        x = self.root.winfo_x() + self.W + 10
        y = self.root.winfo_y()
        sw = win.winfo_screenwidth()
        if x + 300 > sw:
            x = max(20, self.root.winfo_x() - 310)
        win.geometry("+%d+%d" % (x, y))

        wrap = tk.Frame(win, bg=BG, highlightbackground=BORDER,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True)

        head = tk.Frame(wrap, bg=BG, height=34)
        head.pack(fill="x", padx=14, pady=(10, 0))
        head.pack_propagate(False)
        tk.Label(head, text="设置", bg=BG, fg=TEXT,
                 font=(FAMILY, 11, "bold")).pack(side="left")
        close = self._btn(head, "✕", win.destroy, hover=ERROR)
        close.pack(side="right")
        head.bind("<Button-1>", lambda e: self._win_drag(win, e, "s"))
        head.bind("<B1-Motion>", lambda e: self._win_drag(win, e, "m"))

        def section(title, value_text=""):
            f = tk.Frame(wrap, bg=BG)
            f.pack(fill="x", padx=16, pady=(10, 0))
            tk.Label(f, text=title, bg=BG, fg=MUTED,
                     font=(FAMILY, 10)).pack(side="left")
            v = tk.Label(f, text=value_text, bg=BG, fg=ACCENT,
                         font=(FAMILY, 10))
            v.pack(side="right")
            return v

        # 透明度
        v_alpha = section("不透明度",
                          "%d%%" % int(float(self.cfg["alpha"]) * 100))
        MiniSlider(wrap, 40, 100, int(float(self.cfg["alpha"]) * 100),
                   lambda v: self._set_alpha(v, v_alpha)).pack(
            fill="x", padx=14, pady=(4, 2))

        # 字号
        v_font = section("字号", str(self.cfg["font_size"]))
        MiniSlider(wrap, 10, 22, int(self.cfg["font_size"]),
                   lambda v: self._set_font(v, v_font)).pack(
            fill="x", padx=14, pady=(4, 2))

        self._check(wrap, "复制文字后自动翻译", "clipboard")
        self._check(wrap, "查词自动发音", "tts")
        self._check(wrap, "Option 悬停取词", "hover",
                    lambda on: self._refresh_hover_btn())
        self._check(wrap, "开机自动启动", "autostart", self._toggle_autostart)

        sep = tk.Frame(wrap, bg=BORDER, height=1)
        sep.pack(fill="x", padx=16, pady=(11, 1))
        self._choice_row(wrap, "源语言", "src", self._menu_src)
        self._choice_row(wrap, "目标语言", "dst", self._menu_dst)
        self._choice_row(wrap, "翻译引擎", "engine", self._menu_engine)

        tip = tk.Frame(wrap, bg=BG)
        tip.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(tip, text="呼出热键", bg=BG, fg=MUTED,
                 font=(FAMILY, 10)).pack(side="left")
        tk.Label(tip, text=(self.hotkey.label if self.hotkey else "")
                 or "未注册", bg=BG, fg=ACCENT,
                 font=(FAMILY_MONO, 11)).pack(side="right")

        tip2 = tk.Frame(wrap, bg=BG)
        tip2.pack(fill="x", padx=16, pady=(5, 0))
        tk.Label(tip2, text="截图热键", bg=BG, fg=MUTED,
                 font=(FAMILY, 10)).pack(side="left")
        tk.Label(tip2, text=(self.hotkey.labels.get("shot")
                             if self.hotkey else "") or "未注册",
                 bg=BG, fg=ACCENT,
                 font=(FAMILY_MONO, 11)).pack(side="right")

        foot = tk.Frame(wrap, bg=BG)
        foot.pack(fill="x", padx=16, pady=(14, 0))
        reset = tk.Label(foot, text="重置位置", bg=BG_SOFT, fg=MUTED,
                         font=(FAMILY, 10), padx=10, pady=5, cursor="hand2")
        reset.pack(side="left")
        reset.bind("<Button-1>", lambda e: self._reset_pos())
        tk.Label(foot, text="v" + VERSION, bg=BG, fg=DIM,
                 font=(FAMILY, 9)).pack(side="right")

        win.update_idletasks()
        win.geometry("300x%d" % max(300, wrap.winfo_reqheight()))
        win.bind("<Escape>", lambda e: win.destroy())

    def _win_drag(self, win, e, mode):
        if mode == "s":
            win._d = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())
        elif getattr(win, "_d", None):
            win.geometry("+%d+%d" % (e.x_root - win._d[0],
                                     e.y_root - win._d[1]))

    def _check(self, parent, text, key, cmd=None):
        on = bool(self.cfg.get(key))
        row = tk.Frame(parent, bg=BG, cursor="hand2")
        row.pack(fill="x", padx=14, pady=(9, 0))
        box = tk.Label(row, text="☑" if on else "☐", bg=BG,
                       fg=ACCENT if on else DIM, font=(FAMILY, 13),
                       cursor="hand2")
        box.pack(side="left")
        lbl = tk.Label(row, text=text, bg=BG, fg=TEXT, font=(FAMILY, 10),
                       cursor="hand2")
        lbl.pack(side="left", padx=(8, 0))

        def toggle(_e=None):
            val = not bool(self.cfg.get(key))
            self.cfg[key] = val
            box.configure(text="☑" if val else "☐",
                          fg=ACCENT if val else DIM)
            self._schedule_save()
            if cmd:
                cmd(val)
            if key == "clipboard":
                self._refresh_clip_btn()

        for w in (row, box, lbl):
            w.bind("<Button-1>", toggle)
        return row

    def _toggle_autostart(self, on):
        if not set_autostart(on):
            self._set_status("设置开机启动失败")

    def _set_alpha(self, value, label):
        label.configure(text="%d%%" % value)
        self.cfg["alpha"] = value / 100.0
        self.root.attributes("-alpha", value / 100.0)
        self._schedule_save()

    def _set_font(self, value, label):
        label.configure(text=str(value))
        self.cfg["font_size"] = value
        self._schedule_save()
        self._apply_font_size()

    def _reset_pos(self):
        sw = self.root.winfo_screenwidth()
        self.cfg["x"] = sw - self.W - 70
        self.cfg["y"] = 100
        self._schedule_save()
        self.root.geometry("+%d+%d" % (self.cfg["x"], self.cfg["y"]))

    # ------------------------------------------------------------------ 截图取词
    def start_region_shot(self):
        if so is None:
            self._set_status("取词模块缺失")
            return
        why = so.available()
        if why:
            self._set_status("OCR 不可用：" + why[:60])
            return
        if not so.screen_record_ok():
            so.request_screen_record()
            self._set_status("请在 系统设置 → 隐私与安全性 → 屏幕录制 里勾选本程序")
            return
        if self._selector is not None:
            return
        self.hide_hover()
        try:
            self._selector = RegionSelector(
                self.root, self._on_region, self._on_region_cancel)
        except Exception as e:
            log("selector fail %r" % (e,))
            self._selector = None
            self._set_status("框选窗口打开失败")
            return
        self._set_status("拖动框选要翻译的区域…")

    def _on_region_cancel(self):
        self._selector = None
        self._set_status("已取消")

    def _on_region(self, x, y, w, h):
        self._selector = None
        self._set_status("识别中…")

        def work():
            try:
                time.sleep(0.18)        # 等压暗层彻底消失
                text, lines, _scale = so.ocr_region(x, y, w, h)
                self.q.put(("ocr", 0, (text, None, (w, h))))
            except Exception as e:
                log("ocr fail %r" % (e,))
                self.q.put(("ocr", 0, ("", str(e), (w, h))))

        threading.Thread(target=work, daemon=True).start()

    def _after_ocr(self, payload):
        text, err, size = payload
        w, h = size
        if err:
            self._set_status("识别失败：" + err[:50])
            return
        if not text:
            if h < 18 or w < 18:
                self._set_status("框太小了，把整行文字都框进去")
            else:
                self._set_status("没认出文字，框松一点再试")
            return
        flat = " ".join(text.split())
        show = flat if len(flat) <= 60 else flat[:60] + "…"
        self.entry.set_text(show)
        self.show_window()
        self.translate(text)
        self._set_status("截图识别 %d 字" % len(text))

    # ------------------------------------------------------------------ 悬停取词
    def toggle_hover(self):
        if so is None or so.available():
            self._set_status("取词不可用")
            return
        on = not bool(self.cfg.get("hover"))
        self.cfg["hover"] = on
        self._schedule_save()
        self._refresh_hover_btn()
        if on:
            self._set_status("悬停取词已开：按住 Option 停在文字上")
        else:
            self.hide_hover()
            self._set_status("悬停取词已关")

    def _refresh_hover_btn(self):
        if not hasattr(self, "btn_hover"):
            return
        self.btn_hover.configure(
            fg=ACCENT if self.cfg.get("hover") else MUTED)

    def _hover_tick(self):
        self.root.after(140, self._hover_tick)
        if so is None or not self.cfg.get("hover"):
            self._hover_anchor = None
            return
        if self._selector is not None or self._hover_busy:
            return
        pos = so.cursor_pos()
        if not pos:
            return
        x, y = pos
        down = so.modifier_down(self.cfg.get("hover_mod") or "alt")

        if self.popup.visible:
            if not down and time.time() - self.popup.shown_at > 0.4:
                self.hide_hover()
            elif self._hover_pos and (
                    abs(x - self._hover_pos[0]) > 90
                    or abs(y - self._hover_pos[1]) > 60):
                self.hide_hover()
            return

        if not down:
            self._hover_anchor = None
            return
        if so.frontmost_pid() == os.getpid():
            self._hover_anchor = None
            return
        if self._hover_anchor is None:
            self._hover_anchor = (x, y, time.time())
            return
        ax, ay, at = self._hover_anchor
        if abs(x - ax) > 6 or abs(y - ay) > 6:
            self._hover_anchor = (x, y, time.time())
            return
        if time.time() - at < 0.45:
            return
        if self._hover_pos and abs(x - self._hover_pos[0]) < 24 \
                and abs(y - self._hover_pos[1]) < 18:
            return
        self._hover_pos = (x, y)
        self._hover_anchor = None
        self._hover_busy = True
        threading.Thread(target=self._hover_worker, args=(x, y),
                         daemon=True).start()

    @staticmethod
    def _hover_pick_line(lines, rel, scale=1.0):
        """在候选行里挑出鼠标底下那一行。rel 是光标在截图里的像素 y。"""
        best = None
        best_score = None
        for L in lines:
            text = (L.get("text") or "").strip()
            if not text:
                continue
            lw = max(1.0, L["w"])
            lh = max(1.0, L["h"])
            d = abs((L["y"] + lh / 2.0) - rel)
            if (lw < 16 * scale or lh < 8 * scale) and d > 6 * scale:
                continue
            if len(text) < 2 and d > 4 * scale:
                continue
            score = d - min(lw, 500 * scale) * 0.02
            if best_score is None or score < best_score:
                best_score = score
                best = (text, L)
        return best if best else (None, None)

    def _hover_worker(self, cx, cy):
        try:
            vw, vh = so.screen_size()
            bh = 80
            pick = ""
            for band in (1000, vw):
                bw = min(band, vw)
                x = max(0, min(cx - bw // 2, vw - bw))
                y = max(0, min(cy - bh // 2, vh - bh))
                text, lines, scale = so.ocr_region(x, y, bw, bh, fast=True)
                if not text:
                    continue
                pick = text
                edge = False
                if lines:
                    cand, near = self._hover_pick_line(
                        lines, (cy - y) * scale, scale)
                    if cand:
                        pick = cand
                        edge = (near["x"] <= 2 * scale
                                or near["x"] + near["w"] >= bw * scale - 2)
                if not edge or bw >= vw:
                    break
            if not pick:
                self.hq.put(("none", None, None))
                return
            res = self.translator.translate(pick, *self._lang_args())
            self.hq.put(("ok", (cx, cy), (pick, res)))
        except Exception as e:
            log("hover fail %r" % (e,))
            self.hq.put(("none", None, None))

    def _show_hover(self, cx, cy, src, res):
        try:
            self.popup.show(cx, cy, src, res.text, res.engine)
        except Exception as e:
            log("popup fail %r" % (e,))

    def hide_hover(self):
        try:
            self.popup.hide()
        except Exception:
            pass


# --------------------------------------------------------------------------- 自检


def run_check():
    print("FloatTrans macOS 自检")
    print("-" * 46)
    print("Python      :", sys.version.split()[0], "(%s)" % sys.executable)
    print("系统        :", sys.platform)

    try:
        import tkinter
        print("tkinter     : 可用，Tk %s" % tkinter.TkVersion)
    except Exception as e:
        print("tkinter     : 不可用 -> %r" % (e,))
        print("              请装 python.org 的 Python 3，自带 tkinter")

    print("翻译核心    : 可用（%d 种语言 / %d 个引擎）"
          % (len(LANGS), len(ENGINES)))

    if so is None:
        print("取词模块    : 导入失败")
    else:
        why = so.available()
        print("取词模块    :", "可用" if not why else "不可用 -> " + why)
        if not why:
            ok = so.screen_record_ok()
            print("屏幕录制权限:", "已授权" if ok else "未授权（截图取词会被拦）")
            try:
                sw, sh = so.screen_size()
                print("主屏尺寸    : %d x %d 点" % (sw, sh))
            except Exception:
                pass

    if HotkeyListener is None:
        print("全局热键    : 导入失败")
    else:
        ev = {"show": threading.Event()}
        hk = HotkeyListener(ev)
        hk.start()
        print("全局热键    :", hk.label or "注册失败",
              ("(%s)" % hk.error) if hk.error else "")
        hk.stop()

    print("配置目录    :", CFG_DIR)
    print("日志        :", LOG_PATH)
    print("-" * 46)
    print("自检结束。把这段输出贴回来就能定位问题。")
    return 0


# --------------------------------------------------------------------------- 入口


def main():
    if "--check" in sys.argv:
        return run_check()

    def hook(exc_type, exc, tb):
        import traceback
        log("UNCAUGHT:\n" + "".join(
            traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook

    if not acquire_lock():
        print("FloatTrans 已经在运行了")
        return 0

    log("---- start v%s ----" % VERSION)
    app = FloatTrans()
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
