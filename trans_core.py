#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FloatTrans 翻译核心 —— 与操作系统无关，Windows / macOS 版共用。

这里只放「跟界面和系统无关」的东西：语言与引擎表、语言代码转换、语言粗判、
HTTP 请求，以及 Translator（有道 / Google / MyMemory + 自动降级）。
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# --------------------------------------------------------------------------- 语言 / 引擎

# 界面显示名 -> 内部代码（内部代码尽量用通用写法，各引擎再各自转换）
LANGS = [
    ("自动检测", "auto"),
    ("中文", "zh"),
    ("繁体中文", "zh-TW"),
    ("英语", "en"),
    ("日语", "ja"),
    ("韩语", "ko"),
    ("法语", "fr"),
    ("德语", "de"),
    ("俄语", "ru"),
    ("西班牙语", "es"),
    ("葡萄牙语", "pt"),
    ("意大利语", "it"),
]
LANG_NAME = {code: name for name, code in LANGS}
LANG_SHORT = dict(LANG_NAME, auto="自动")
# 目标语言不能是「自动检测」，其余与源语言一致
DST_LANGS = [(n, c) for n, c in LANGS if c != "auto"]

ENGINES = [
    ("自动", "auto"),          # 有道 -> Google -> MyMemory 依次降级
    ("有道", "youdao"),
    ("Google", "google"),
    ("MyMemory", "mymemory"),
]
ENGINE_NAME = {code: name for name, code in ENGINES}

# 各家引擎对中文代码的叫法不一样
_YOUDAO_CODE = {"zh": "zh-CHS", "zh-TW": "zh-CHT"}
_GOOGLE_CODE = {"zh": "zh-CN", "zh-TW": "zh-TW"}
_MYMEMORY_CODE = {"zh": "zh-CN", "zh-TW": "zh-TW"}

# --------------------------------------------------------------------------- 正则

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
HANGUL_RE = re.compile(r"[\u1100-\u11ff\uac00-\ud7af]")
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-]*$")
TAG_RE = re.compile(r"<[^>]+>")


def lang_code(lang: str, engine: str) -> str:
    """把内部语言代码转成指定引擎认识的语言代码。"""
    table = {"youdao": _YOUDAO_CODE, "google": _GOOGLE_CODE,
             "mymemory": _MYMEMORY_CODE}.get(engine, {})
    return table.get(lang, lang)


def detect_lang(text: str) -> str:
    """粗略判断源语言，只用于「自动检测」和 MyMemory 这种必须给源语言的引擎。"""
    if KANA_RE.search(text):
        return "ja"
    if HANGUL_RE.search(text):
        return "ko"
    if CJK_RE.search(text):
        return "zh"
    return "en"


# --------------------------------------------------------------------------- 网络


def http_get(url: str, timeout: float = 7.0, data: bytes | None = None,
             referer: str | None = None) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


# --------------------------------------------------------------------------- 翻译


class TranslateError(Exception):
    pass


class Result:
    __slots__ = ("src", "text", "phonetic", "defs", "engine", "elapsed",
                 "tts_url", "src_lang", "dst_lang")

    def __init__(self, src: str):
        self.src = src
        self.text = ""
        self.phonetic = ""
        self.defs: list[str] = []
        self.engine = ""
        self.elapsed = 0.0
        self.tts_url = ""
        self.src_lang = "auto"
        self.dst_lang = "zh"


class Translator:
    """源语言可自动 / 手动，目标语言手动，引擎可自动降级或指定；单词额外查词典。"""

    def __init__(self):
        self.google_ok = None  # 探测结果：None 未探测
        self._probe_lock = threading.Lock()

    # ---------------------------------------------------------------- 探测
    def probe_google(self):
        with self._probe_lock:
            if self.google_ok is not None:
                return
            try:
                self._google("hello", "auto", "zh")
                self.google_ok = True
            except Exception:
                self.google_ok = False

    # ---------------------------------------------------------------- 主入口
    def translate(self, text: str, src: str = "auto", dst: str = "zh",
                  engine: str = "auto") -> Result:
        text = " ".join(text.split())
        if not text:
            raise TranslateError("没有可翻译的内容")
        if len(text) > 2000:
            text = text[:2000]

        src = src if src in LANG_NAME else "auto"
        dst = dst if dst in LANG_NAME and dst != "auto" else "zh"
        engine = engine if engine in ENGINE_NAME else "auto"

        res = Result(text)
        src_used = detect_lang(text) if src == "auto" else src
        res.src_lang = src_used
        res.dst_lang = dst

        # 源和目标撞了就没必要翻，直接把原文摆出来
        if src_used == dst:
            res.text = text
            res.engine = "原文"
            return res

        # 1) 单个英文单词 -> 查词典（音标 / 释义 / 真人发音），仅当目标是中文
        if dst == "zh" and src_used == "en" and WORD_RE.match(text):
            try:
                info = self._youdao_dict(text)
                if info:
                    res.phonetic, res.defs = info
                    res.tts_url = ("https://dict.youdao.com/dictvoice?type=2&audio="
                                   + urllib.parse.quote(text))
            except Exception:
                pass

        # 2) 整句翻译（自动降级 / 指定引擎）
        last_err = None
        for name, fn in self._chain(engine, src_used, dst):
            try:
                out, tts = fn(text, src, dst)
                if out:
                    res.text = out
                    res.engine = name
                    res.tts_url = res.tts_url or tts
                    return res
            except Exception as e:
                last_err = e

        if res.defs:  # 词典里拿到了释义也算成功
            res.text = "\n".join(res.defs[:2])
            res.engine = "有道词典"
            return res

        if engine != "auto":
            raise TranslateError("%s 翻译失败：%s（可在设置里换个引擎）"
                                 % (ENGINE_NAME[engine], str(last_err)[:60]))
        raise TranslateError("网络翻译失败，请检查网络后重试（%s）"
                             % (str(last_err)[:60] if last_err else "无可用引擎"))

    # ---------------------------------------------------------------- 引擎调度
    @staticmethod
    def youdao_supports(src: str, dst: str) -> bool:
        """有道这个免费接口只做「简体中文 <-> 其他语言」。

        实测：英->日、英->德、德->法、日->韩 全部返回 errorCode=102（不支持的语言
        类型）；繁中（zh-CHT）两个方向也是 102。所以只有一端是简体中文时才有戏。
        """
        if "zh-TW" in (src, dst):
            return False
        return "zh" in (src, dst)

    def _chain(self, engine: str, src: str, dst: str):
        """返回要尝试的引擎列表：自动模式按顺序降级，手动模式只用指定的那个。"""
        pool = {
            "youdao": ("有道", self._youdao_ai),
            "google": ("Google", self._google),
            "mymemory": ("MyMemory", self._mymemory),
        }
        ok = self.youdao_supports(src, dst)
        if engine in pool:
            if engine == "youdao" and not ok:
                raise TranslateError(
                    "有道只支持中文与其他语言互译，不支持 %s→%s，请换成 Google 或 MyMemory"
                    % (LANG_NAME.get(src, src), LANG_NAME.get(dst, dst)))
            return [pool[engine]]
        chain = [pool["youdao"]] if ok else []
        if self.google_ok:
            chain.append(pool["google"])
        chain.append(pool["mymemory"])
        return chain

    # ---------------------------------------------------------------- 有道
    def _youdao_ai(self, text: str, src: str, dst: str):
        params = {"q": text,
                  "from": "Auto" if src == "auto" else lang_code(src, "youdao"),
                  "to": lang_code(dst, "youdao")}
        body = urllib.parse.urlencode(params).encode()
        url = "https://aidemo.youdao.com/trans"
        raw = http_get(url, timeout=8, data=body,
                       referer="https://ai.youdao.com/")
        data = json.loads(raw)
        if str(data.get("errorCode")) != "0":
            raise TranslateError("youdao error %s" % data.get("errorCode"))
        parts = data.get("translation") or []
        out = "".join(parts).strip()
        if not out:
            raise TranslateError("empty")
        tts = data.get("tSpeakUrl") or data.get("speakUrl") or ""
        return out, tts

    def _youdao_dict(self, word: str):
        url = ("https://dict.youdao.com/jsonapi?q="
               + urllib.parse.quote(word))
        data = json.loads(http_get(url, timeout=6))
        ec = (data.get("ec") or {}).get("word") or []
        if not ec:
            return None
        w = ec[0]
        phon = []
        if w.get("ukphone"):
            phon.append("英 /%s/" % w["ukphone"])
        if w.get("usphone"):
            phon.append("美 /%s/" % w["usphone"])
        defs = []
        for trs in w.get("trs", []):
            for tr in trs.get("tr", []):
                for line in tr.get("l", {}).get("i", []):
                    line = TAG_RE.sub("", line).strip()
                    if line:
                        defs.append(line)
        if not phon and not defs:
            return None
        return "   ".join(phon), defs

    # ---------------------------------------------------------------- Google
    def _google(self, text: str, src: str, dst: str):
        sl = "auto" if src == "auto" else lang_code(src, "google")
        tl = lang_code(dst, "google")
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=%s&tl=%s&dt=t&q=%s"
               % (sl, tl, urllib.parse.quote(text)))
        data = json.loads(http_get(url, timeout=5))
        out = "".join(seg[0] for seg in data[0] if seg and seg[0])
        if not out.strip():
            raise TranslateError("empty")
        return out.strip(), ""

    # ---------------------------------------------------------------- MyMemory
    def _mymemory(self, text: str, src: str, dst: str):
        # MyMemory 不支持 auto，得自己判一下源语言
        s = detect_lang(text) if src == "auto" else src
        if s == dst:
            raise TranslateError("same lang")
        pair = "%s|%s" % (lang_code(s, "mymemory"), lang_code(dst, "mymemory"))
        url = ("https://api.mymemory.translated.net/get?q=%s&langpair=%s"
               % (urllib.parse.quote(text), urllib.parse.quote(pair)))
        data = json.loads(http_get(url, timeout=8))
        out = (data.get("responseData") or {}).get("translatedText") or ""
        out = TAG_RE.sub("", out).strip()
        if not out or "QUERY LENGTH LIMIT" in out.upper():
            raise TranslateError("mymemory empty")
        return out, ""
