#!/bin/bash
# FloatTrans 悬浮翻译 · macOS 启动脚本
# 双击运行；第一次如果提示「无法打开」，右键 → 打开。
cd "$(dirname "$0")" || exit 1

say_alert() {
    osascript -e "display alert \"$1\" message \"$2\"" >/dev/null 2>&1
}

PY=$(command -v python3)
if [ -z "$PY" ]; then
    say_alert "没找到 python3" "请先安装 Python 3（https://www.python.org/downloads/macos/），安装时保持默认选项。"
    open "https://www.python.org/downloads/macos/" 2>/dev/null
    exit 1
fi

if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    say_alert "这个 python3 没有 tkinter" "请安装 python.org 的 Python 3.12（自带 tkinter），装完再双击本脚本。当前用的是：$PY"
    exit 1
fi

# 取词依赖（只影响截图取词 / 悬停取词，缺了主程序照样能翻译）
if ! "$PY" -c "import Vision" >/dev/null 2>&1; then
    osascript -e 'display notification "正在安装取词依赖 pyobjc，第一次会慢一两分钟…" with title "悬浮翻译"' >/dev/null 2>&1
    "$PY" -m pip install --user --quiet pyobjc-framework-Vision pyobjc-framework-Quartz
fi

# 根证书：缺了会让所有翻译都报 SSL CERTIFICATE_VERIFY_FAILED
if ! "$PY" -c "import certifi" >/dev/null 2>&1; then
    "$PY" -m pip install --user --quiet certifi >/dev/null 2>&1 || true
fi

exec "$PY" float_trans_mac.py
