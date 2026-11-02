#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 macOS 用的高分图标 icon_mac.png（1024×1024）。

app.ico 里最大只有 256，直接放大会发虚，所以用 make_icon 里同一套绘制逻辑
在 1024 下重画一遍。只在打包 .app / .dmg 时用一次，Windows 上跑也没问题。
"""

import os

from PIL import Image

from make_icon import make

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 1024


def main():
    img = make(SIZE)
    out = os.path.join(HERE, "icon_mac.png")
    img.save(out)
    print("written:", out, img.size)


if __name__ == "__main__":
    main()
