# -*- coding: utf-8 -*-
"""
WSGI 入口（PythonAnywhere / Gunicorn / uWSGI 通用）

本文件是"自适应"的：
  · 程序目录 = 本文件所在目录（也可用环境变量 CONAN_APP_DIR 指定）
  · 音乐目录 = 环境变量 CONAN_MUSIC_ROOT，默认是程序目录下的 music 文件夹
  · flask_app.py 会在同目录里自动找主程序（带空格 / 不带空格的文件名都认）

所以只要把整个 server 文件夹原样放到 /home/CZY201408/server，
基本不需要改任何路径。

PythonAnywhere 用法：
  Web 标签页 → 点 WSGI 配置文件的路径 → 全选清空 → 粘贴本文件全部内容 → Save → Reload。
  （Source code / Working directory 填 /home/CZY201408/server）

Gunicorn：  gunicorn -w 1 --threads 8 -b 0.0.0.0:5000 wsgi:application
uWSGI：     uwsgi --http :5000 --module wsgi:application --enable-threads
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 目录配置（一般不用改）----
APP_DIR = os.environ.get("CONAN_APP_DIR") or HERE
MUSIC_DIR = os.environ.get("CONAN_MUSIC_ROOT") or os.path.join(APP_DIR, "music")

os.environ["CONAN_APP_DIR"] = APP_DIR
os.environ["CONAN_MUSIC_ROOT"] = MUSIC_DIR

for _p in (APP_DIR, HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    os.makedirs(MUSIC_DIR, exist_ok=True)
except Exception:
    pass

from flask_app import app as application  # noqa: E402  Flask 实例即 WSGI application

# 兼容部分平台要求同名变量
app = application
