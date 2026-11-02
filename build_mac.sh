#!/bin/bash
# 把 macOS 版打包成 FloatTrans.app，再封成 FloatTrans.dmg
#   用法：  ./build_mac.sh          # 生成 .app + .dmg
#          ./build_mac.sh app      # 只生成 .app
# 必须在 macOS 上跑：PyInstaller 不能跨平台编译，ad-hoc 签名也只有 macOS 能做。
set -e
cd "$(dirname "$0")"

PY=${PY:-python3}
VERSION="1.0.0"
MODE=${1:-dmg}

echo "[1/4] 安装依赖…"
"$PY" -m pip install --upgrade --quiet pyinstaller
"$PY" -m pip install --quiet pyobjc-framework-Vision pyobjc-framework-Quartz

echo "[2/4] 生成 .icns 图标…"
# 有 Pillow 就重新画一张 1024 的，没有就用仓库里现成的 icon_mac.png
if "$PY" -c "import PIL" >/dev/null 2>&1; then
    "$PY" make_icon_mac.py >/dev/null 2>&1 || true
fi
rm -rf build_icon.iconset app.icns
mkdir -p build_icon.iconset
SRC_ICON="icon_preview.png"
[ -f icon_mac.png ] && SRC_ICON="icon_mac.png"
echo "      素材：$SRC_ICON"
while read -r name size; do
    sips -z "$size" "$size" "$SRC_ICON" \
        --out "build_icon.iconset/$name" >/dev/null 2>&1
done <<'SET'
icon_16x16.png 16
icon_16x16@2x.png 32
icon_32x32.png 32
icon_32x32@2x.png 64
icon_128x128.png 128
icon_128x128@2x.png 256
icon_256x256.png 256
icon_256x256@2x.png 512
icon_512x512.png 512
icon_512x512@2x.png 1024
SET
iconutil -c icns build_icon.iconset -o app.icns

echo "[3/4] 打包 .app…"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name FloatTrans \
    --icon app.icns \
    --hidden-import Vision \
    --hidden-import Quartz \
    --hidden-import AppKit \
    float_trans_mac.py

# ad-hoc 签名：能减少「已损坏，无法打开」的提示（正式分发要开发者证书）
codesign --force --deep --sign - dist/FloatTrans.app 2>/dev/null || \
    echo "   （ad-hoc 签名跳过，不影响本机使用）"

if [ "$MODE" = "app" ]; then
    echo
    echo "完成：dist/FloatTrans.app"
    exit 0
fi

echo "[4/4] 封装 .dmg…"
DMG="dist/FloatTrans-${VERSION}.dmg"
STAGE="build_dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R dist/FloatTrans.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"     # 拖拽安装用的快捷方式
hdiutil create -volname "FloatTrans" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo
echo "完成："
echo "  dist/FloatTrans.app          —— 直接双击用"
echo "  $DMG   —— 分发用，双击挂载后把图标拖进 Applications"
echo
echo "首次打开：右键 → 打开（未签名，Gatekeeper 会拦一次）。"
echo "装好后要在 系统设置 → 隐私与安全性 → 屏幕录制 里勾选 FloatTrans。"
