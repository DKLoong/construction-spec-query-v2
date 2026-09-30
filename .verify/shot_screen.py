# -*- coding: utf-8 -*-
"""全屏截图：PowerShell 调用，保存 1600x1000 PNG 到指定路径"""
import sys
import ctypes
from PIL import ImageGrab

path = sys.argv[1]
img = ImageGrab.grab()
img.save(path, "PNG")
print("SAVED", path, img.size)
