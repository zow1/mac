#!/bin/bash
# FloatTrans 悬浮翻译 · 环境自检
# 双击运行，把窗口里的内容截图发回来即可定位问题。
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3 || echo /usr/bin/python3)
clear
echo
"$PY" float_trans_mac.py --check
echo
echo "—— 下面是屏幕录制权限的说明 ——"
echo "如果上面显示「屏幕录制权限: 未授权」："
echo "  系统设置 → 隐私与安全性 → 屏幕录制 → 勾选 Terminal（或本脚本的父程序）"
echo "  勾完要完全退出终端再重开，权限才生效。"
echo
read -n 1 -s -r -p "按任意键关闭窗口…"
echo
