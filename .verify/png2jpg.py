# -*- coding: utf-8 -*-
"""把 cu 保存的 PNG 压缩为统一 1280 宽 JPEG（覆盖同名 jpg）"""
import sys
from PIL import Image

src = sys.argv[1]
dst = src.replace(".png", ".jpg")
img = Image.open(src).convert("RGB")
w, h = img.size
tw = 1280
th = round(h * tw / w)
img = img.resize((tw, th), Image.LANCZOS)
img.save(dst, "JPEG", quality=85)
print("SAVED", dst, img.size)
