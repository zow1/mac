#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FloatTrans macOS 全局热键：Carbon 的 RegisterEventHotKey。

为什么不用 pyobjc 的 NSEvent 全局监听：那需要「辅助功能 / 输入监控」授权，
用户得手动去系统设置里勾；Carbon 的 RegisterEventHotKey 不需要任何授权，
而且注册失败（被别的 App 占用）会直接返回非 0，方便自动换备用键。

用法：
    hk = HotkeyListener({"show": show_event, "shot": shot_event})
    hk.start()
    # hk.label -> "⌃⌥T"，hk.labels -> {"show": ..., "shot": ...}
    # 按下热键时对应的 threading.Event 被置位，主循环轮询即可
"""

from __future__ import annotations

import ctypes

# 修饰键掩码（Carbon Events.h）
MOD_CMD = 0x0100
MOD_SHIFT = 0x0200
MOD_ALT = 0x0800
MOD_CTRL = 0x1000

# 虚拟键码（Carbon Events.h / HIToolbox）
KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
}

kEventClassKeyboard = 0x6B657962      # 'keyb'
kEventHotKeyPressed = 5
kEventParamDirectObject = 0x2D2D2D2D  # '----'
typeEventHotKeyID = 0x686B6964        # 'hkid'

_MOD_NAMES = {
    "cmd": MOD_CMD, "command": MOD_CMD, "meta": MOD_CMD, "⌘": MOD_CMD,
    "alt": MOD_ALT, "option": MOD_ALT, "opt": MOD_ALT, "⌥": MOD_ALT,
    "ctrl": MOD_CTRL, "control": MOD_CTRL, "⌃": MOD_CTRL,
    "shift": MOD_SHIFT, "⇧": MOD_SHIFT,
}

_SYMBOLS = [(MOD_CTRL, "⌃"), (MOD_ALT, "⌥"), (MOD_SHIFT, "⇧"), (MOD_CMD, "⌘")]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_HandlerProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p)


def parse_combo(combo: str):
    """'ctrl+alt+t' -> (修饰键掩码, 虚拟键码)，解析不了返回 None。"""
    mods = 0
    key = None
    for part in (p.strip().lower() for p in combo.split("+")):
        if not part:
            continue
        if part in _MOD_NAMES:
            mods |= _MOD_NAMES[part]
        elif part in KEY_CODES:
            key = part
        else:
            return None
    if key is None or not mods:
        return None
    return mods, KEY_CODES[key]


def pretty_combo(combo: str) -> str:
    """'ctrl+alt+t' -> '⌃⌥T'，设置里显示用。"""
    parsed = parse_combo(combo)
    if not parsed:
        return combo
    mods, code = parsed
    key = next((k for k, v in KEY_CODES.items() if v == code), "?")
    out = "".join(sym for mask, sym in _SYMBOLS if mods & mask)
    return out + key.upper()


class HotkeyListener:
    """注册若干全局热键，按下时置位对应的 threading.Event。"""

    def __init__(self, events: dict, combos: dict | None = None,
                 signature: bytes = b"FTra"):
        self.events = events
        self.combos = combos or {
            "show": ["ctrl+alt+t", "cmd+alt+t", "ctrl+shift+t", "cmd+shift+t"],
            "shot": ["ctrl+alt+s", "cmd+alt+s", "ctrl+shift+s", "cmd+shift+s"],
            "hover": ["ctrl+alt+h", "cmd+alt+h", "ctrl+shift+h"],
        }
        self.signature = int.from_bytes(signature.ljust(4, b"\0")[:4], "big")
        self.labels: dict[str, str] = {}
        self.label = ""
        self.error = ""
        self._carbon = None
        self._handler = None
        self._refs = []
        self._ids: dict[int, str] = {}
        self._next_id = 1
        self._target = None

    # ---------------------------------------------------------------- 启动
    def start(self):
        try:
            self._carbon = ctypes.CDLL(
                "/System/Library/Frameworks/Carbon.framework/Carbon")
        except Exception as e:
            self.error = "加载 Carbon 失败：%r" % (e,)
            return
        c = self._carbon

        c.GetApplicationEventTarget.restype = ctypes.c_void_p
        c.GetApplicationEventTarget.argtypes = []
        self._target = c.GetApplicationEventTarget()
        if not self._target:
            self.error = "拿不到应用事件目标"
            return

        c.InstallEventHandler.restype = ctypes.c_int32
        c.InstallEventHandler.argtypes = [
            ctypes.c_void_p, _HandlerProc, ctypes.c_ulong,
            ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p)]

        self._handler = _HandlerProc(self._on_event)
        spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        href = ctypes.c_void_p()
        st = c.InstallEventHandler(self._target, self._handler, 1,
                                   ctypes.byref(spec), None,
                                   ctypes.byref(href))
        if st != 0:
            self.error = "安装热键处理器失败（OSStatus %d）" % st
            return

        c.RegisterEventHotKey.restype = ctypes.c_int32
        c.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]

        for action, cands in self.combos.items():
            if action not in self.events:
                continue
            for combo in cands:
                if self._register(action, combo):
                    self.labels[action] = pretty_combo(combo)
                    break
        self.label = self.labels.get("show", "")
        if not self.labels:
            self.error = self.error or "热键都被占用了"

    def _register(self, action: str, combo: str) -> bool:
        parsed = parse_combo(combo)
        if not parsed:
            return False
        mods, code = parsed
        hid = self._next_id
        hk = EventHotKeyID(self.signature, hid)
        ref = ctypes.c_void_p()
        st = self._carbon.RegisterEventHotKey(
            ctypes.c_uint32(code), ctypes.c_uint32(mods), hk,
            ctypes.c_void_p(self._target), ctypes.c_uint32(0),
            ctypes.byref(ref))
        if st != 0:
            return False
        self._ids[hid] = action
        self._refs.append(ref)
        self._next_id += 1
        return True

    # ---------------------------------------------------------------- 回调
    def _on_event(self, _call_ref, event, _user):
        try:
            hk = EventHotKeyID()
            actual_type = ctypes.c_uint32(0)
            actual_size = ctypes.c_uint32(0)
            self._carbon.GetEventParameter(
                ctypes.c_void_p(event),
                ctypes.c_uint32(kEventParamDirectObject),
                ctypes.c_uint32(typeEventHotKeyID),
                ctypes.byref(actual_type),
                ctypes.c_uint32(ctypes.sizeof(hk)),
                ctypes.byref(actual_size),
                ctypes.byref(hk))
            ev = self.events.get(self._ids.get(int(hk.id), ""))
            if ev is not None:
                ev.set()
        except Exception:
            pass
        return 0

    # ---------------------------------------------------------------- 收尾
    def stop(self):
        if not self._carbon:
            return
        try:
            self._carbon.UnregisterEventHotKey.restype = ctypes.c_int32
            self._carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
            for ref in self._refs:
                self._carbon.UnregisterEventHotKey(ref)
        except Exception:
            pass
        self._refs = []
