#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FloatTrans macOS 取词底层：screencapture 截屏 + Vision 文字识别。

Windows 版用的是 GDI BitBlt + Windows.Media.Ocr；macOS 这边换成系统原生能力：
  * 截屏：/usr/sbin/screencapture（需要「屏幕录制」权限，首次会弹窗）
  * 识别：Vision 框架的 VNRecognizeTextRequest（本地离线，中英都认得不错）
  * 光标 / 修饰键 / 前台进程：Quartz 事件 API（不需要任何授权）

依赖：pyobjc-framework-Vision、pyobjc-framework-Quartz
    pip3 install pyobjc-framework-Vision pyobjc-framework-Quartz
没装也能跑主程序，只是截图取词和悬停取词不可用。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

_VISION_ERR = ""

try:
    import Quartz
    import Vision
    from Foundation import NSURL
    HAVE_VISION = True
except Exception as e:          # pyobjc 没装
    Quartz = None
    Vision = None
    NSURL = None
    HAVE_VISION = False
    _VISION_ERR = ("缺少 pyobjc（pip3 install pyobjc-framework-Vision "
                   "pyobjc-framework-Quartz）：%r" % (e,))

SCREENCAPTURE = "/usr/sbin/screencapture"

# 汉字 / 假名 / 谚文之间多余的空格
_CJK_GAP_RE = re.compile(
    r"(?<=[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af])"
    r"[ \t]+"
    r"(?=[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


# --------------------------------------------------------------------------- 环境检查

def available() -> str:
    """返回空字符串表示可用，否则返回不可用的原因。"""
    if not HAVE_VISION:
        return _VISION_ERR
    if not os.path.exists(SCREENCAPTURE):
        return "找不到系统自带的 screencapture"
    return ""


def screen_record_ok() -> bool:
    """是否已获得「屏幕录制」权限（macOS 10.15+）。"""
    if not HAVE_VISION:
        return False
    fn = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if fn is None:              # 老系统没有这个概念，按有权限处理
        return True
    try:
        return bool(fn())
    except Exception:
        return True


def request_screen_record() -> bool:
    """弹一次系统的「屏幕录制」授权请求，返回当前是否有权限。"""
    if not HAVE_VISION:
        return False
    fn = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
    if fn is None:
        return True
    try:
        return bool(fn())
    except Exception:
        return False


# --------------------------------------------------------------------------- 屏幕 / 光标

def screen_size():
    """主屏尺寸（点）。"""
    if not HAVE_VISION:
        return 1440, 900
    try:
        d = Quartz.CGMainDisplayID()
        b = Quartz.CGDisplayBounds(d)
        return int(b.size.width), int(b.size.height)
    except Exception:
        return 1440, 900


def cursor_pos():
    """光标位置（全局点坐标，原点在左上角）。"""
    if not HAVE_VISION:
        return None
    try:
        ev = Quartz.CGEventCreate(None)
        if not ev:
            return None
        p = Quartz.CGEventGetLocation(ev)
        return int(p.x), int(p.y)
    except Exception:
        return None


def modifier_down(name: str = "alt") -> bool:
    """某个修饰键是否按着。不需要辅助功能授权。"""
    if not HAVE_VISION:
        return False
    masks = {
        "alt": getattr(Quartz, "kCGEventFlagMaskAlternate", 0x80000),
        "ctrl": getattr(Quartz, "kCGEventFlagMaskControl", 0x40000),
        "shift": getattr(Quartz, "kCGEventFlagMaskShift", 0x20000),
        "cmd": getattr(Quartz, "kCGEventFlagMaskCommand", 0x100000),
    }
    try:
        flags = Quartz.CGEventSourceFlagsState(
            Quartz.kCGEventSourceStateCombinedSessionState)
        return bool(flags & masks.get(name, masks["alt"]))
    except Exception:
        return False


def frontmost_pid() -> int:
    """最前面那个 App 的 pid，用来避免在自己的窗口上触发悬停取词。"""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app else -1
    except Exception:
        return -1


# --------------------------------------------------------------------------- 截屏

def image_size(path: str):
    """图片实际像素尺寸（Retina 屏会比点数大一倍）。"""
    if not HAVE_VISION:
        return 0, 0
    try:
        url = NSURL.fileURLWithPath_(path)
        src = Quartz.CGImageSourceCreateWithURL(url, None)
        if not src:
            return 0, 0
        img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
        if not img:
            return 0, 0
        return int(Quartz.CGImageGetWidth(img)), int(Quartz.CGImageGetHeight(img))
    except Exception:
        return 0, 0


def capture(x, y, w, h, path: str | None = None):
    """截一块屏幕（点坐标），返回 (路径, 图宽像素, 图高像素)。"""
    path = path or os.path.join(tempfile.gettempdir(), "floattrans_shot.png")
    x, y = int(x), int(y)
    w, h = max(1, int(w)), max(1, int(h))
    cmd = [SCREENCAPTURE, "-x", "-t", "png",
           "-R", "%d,%d,%d,%d" % (x, y, w, h), path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=8)
    iw, ih = image_size(path)
    return path, iw, ih


# --------------------------------------------------------------------------- 识别

def ocr_image(path: str, languages=("zh-Hans", "en-US"), fast: bool = False):
    """识别一张图，返回 (整段文本, 行列表)。

    行列表每项 {"text", "x", "y", "w", "h"}，坐标是图片像素、原点左上角，
    方便和光标位置对齐。
    """
    if not HAVE_VISION:
        raise RuntimeError(_VISION_ERR)

    url = NSURL.fileURLWithPath_(path)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    level = (Vision.VNRequestTextRecognitionLevelFast if fast
             else Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLevel_(level)
    req.setUsesLanguageCorrection_(not fast)
    try:
        req.setRecognitionLanguages_(list(languages))
    except Exception:
        pass

    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError("Vision 识别失败：%s" % (err,))

    W, H = image_size(path)
    if not W or not H:
        W = H = 1

    lines = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        text = str(cands[0].string() or "")
        if not text.strip():
            continue
        bb = obs.boundingBox()      # 归一化，原点在左下角
        w = float(bb.size.width) * W
        h = float(bb.size.height) * H
        x = float(bb.origin.x) * W
        y = (1.0 - float(bb.origin.y) - float(bb.size.height)) * H
        lines.append({"text": text, "x": x, "y": y, "w": w, "h": h})

    lines.sort(key=lambda L: (round(L["y"], 1), L["x"]))
    return "\n".join(L["text"] for L in lines), lines


def clean_text(text: str) -> str:
    """整理 OCR 结果：去空行、去掉汉字之间被拆出来的空格。"""
    out = []
    for raw in (text or "").splitlines():
        line = _CJK_GAP_RE.sub("", raw.strip())
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def ocr_region(x, y, w, h, fast: bool = False):
    """截一块再识别，返回 (文本, 行列表, 缩放比)。

    缩放比 = 图片像素宽 / 区域点宽（Retina 是 2）。调用方拿它把行坐标
    换算回屏幕点坐标。
    """
    path, iw, ih = capture(x, y, w, h)
    text, lines = ocr_image(path, fast=fast)
    scale = (iw / float(w)) if w else 1.0
    try:
        os.remove(path)
    except Exception:
        pass
    return clean_text(text), lines, scale
