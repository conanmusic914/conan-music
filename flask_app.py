# -*- coding: utf-8 -*-
"""
Conan 听歌系统 —— Flask / WSGI 入口

原来这套程序是用 http.server(ThreadingTCPServer) 自己写 HTTP 服务的。
本文件把同一套业务代码包装成标准 Flask 应用，可部署到 PythonAnywhere、
Gunicorn、uWSGI、Waitress 等任何 WSGI 服务器。

设计思路（不重写业务，只做适配）：
  1. 用 importlib 按路径加载 `music 云服务器_酷狗下载版.py`（文件名带空格，不能直接 import）；
  2. Flask 收到请求后，构造一个"假 handler"，把 self.path / headers / rfile / wfile / client_address 补齐；
  3. 原 handler 的 do_GET / do_POST / do_HEAD / do_OPTIONS 原样执行（路由、鉴权、Range 流、下载任务都不动）；
  4. 把 handler 写出的状态码、响应头、响应体转成 Flask Response 返回。

本地运行：  python flask_app.py      （端口用 CONAN_PORT 改，默认 5000）
WSGI 部署： 见同目录 wsgi.py 与 README_Flask部署.md
"""
import email.message
import io
import os
import sys
import traceback
import importlib.util

from flask import Flask, Response, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 主程序可能的文件名（本机带空格；上传网站后常被改成无空格）
CORE_NAMES = [
    "music 云服务器_酷狗下载版.py",
    "music云服务器_酷狗下载版.py",
    "music_server.py",
]


def _find_core():
    """自动定位主程序：先按已知文件名找，再扫目录里 music 开头的 .py"""
    search_dirs = [BASE_DIR]
    env_dir = (os.environ.get("CONAN_APP_DIR") or "").strip()
    if env_dir and os.path.isdir(env_dir) and env_dir not in search_dirs:
        search_dirs.append(env_dir)

    for d in search_dirs:
        for name in CORE_NAMES:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path

    for d in search_dirs:
        try:
            for name in sorted(os.listdir(d)):
                if name.startswith("music") and name.endswith(".py") \
                        and name not in ("flask_app.py", "wsgi.py", "music_server.py"):
                    return os.path.join(d, name)
        except Exception:
            pass
    return None


CORE_FILE = _find_core()


def _load_core():
    """加载原 socketserver 主程序（与 Flask 共享同一份业务代码）"""
    if not CORE_FILE:
        raise RuntimeError(
            "找不到核心文件。请在 %s 放主程序（music 云服务器_酷狗下载版.py），"
            "或设置环境变量 CONAN_APP_DIR 指向程序目录。" % BASE_DIR
        )
    if "conan_music_core" in sys.modules:
        return sys.modules["conan_music_core"]
    spec = importlib.util.spec_from_file_location("conan_music_core", CORE_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["conan_music_core"] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()

app = Flask(__name__, static_folder=None)   # 关掉 Flask 自带 /static，所有路径交给原 handler
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

METHODS = ["GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"]


class _BufferWriter:
    """替代 handler 的 self.wfile，把写出的字节收进内存"""

    def __init__(self):
        self.chunks = []

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if data:
            self.chunks.append(bytes(data))
        return len(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        pass

    def close(self):
        pass

    def getvalue(self):
        return b"".join(self.chunks)


def _email_headers(environ):
    """把 WSGI environ 还原成 email.message.Message。
    原来 BaseHTTPRequestHandler 的 self.headers 就是这种类型，cgi.FieldStorage 也只认它。"""
    msg = email.message.Message()
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            try:
                msg[name] = str(value)
            except Exception:
                pass
    if environ.get("CONTENT_TYPE"):
        msg["Content-Type"] = str(environ["CONTENT_TYPE"])
    if environ.get("CONTENT_LENGTH"):
        msg["Content-Length"] = str(environ["CONTENT_LENGTH"])
    return msg


class _FlaskHandler(core.MyHandler):
    """把 WSGI 请求伪装成 BaseHTTPRequestHandler 请求。
    不调用父类 __init__（它会去读 socket），只补齐原 handler 会用到的属性。"""

    def __init__(self, raw_path, headers, body, client_ip, method):
        self.path = raw_path
        self.headers = headers                      # werkzeug EnvironHeaders，支持 .get("Range")
        self.rfile = io.BytesIO(body or b"")
        self.wfile = _BufferWriter()
        self.client_address = (client_ip, 0)
        self.command = method
        self.request_version = "HTTP/1.1"
        self.requestline = "%s %s HTTP/1.1" % (method, raw_path)
        self.protocol_version = "HTTP/1.1"
        self.close_connection = True
        self.status = 200
        self.response_headers = []
        self._declared_cl = None
        self._headers_ended = False

    # ---------- 覆盖 BaseHTTPRequestHandler 里直接写 socket 的 4 个方法 ----------
    def send_response(self, code, message=None):
        self.status = int(code)
        self.response_headers.append(("Server", "Conan-Music/Flask"))

    def send_response_only(self, code, message=None):
        self.status = int(code)

    def send_header(self, keyword, value):
        name = str(keyword).lower()
        # Content-Length 交给 Werkzeug 按真实 body 计算；只有 body 为空（HEAD/204）时才用原值
        if name == "content-length":
            self._declared_cl = str(value)
            return
        if name in ("connection", "keep-alive", "transfer-encoding"):
            return
        self.response_headers.append((str(keyword), str(value)))

    def end_headers(self):
        self._headers_ended = True

    # 父类里的空实现，防止兜底逻辑误触发
    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        pass

    def handle_one_request(self):
        pass


def _client_ip():
    xff = request.headers.get("X-Forwarded-For") or ""
    if xff:
        return xff.split(",")[0].strip() or "127.0.0.1"
    return request.environ.get("REMOTE_ADDR") or "127.0.0.1"


def _raw_path():
    """取浏览器原始路径（带 query，未解码），与原来 self.path 一致"""
    environ = request.environ
    raw = environ.get("RAW_URI") or environ.get("REQUEST_URI") or ""
    if not raw:
        raw = request.path
        qs = request.query_string.decode("latin-1")
        if qs:
            raw += "?" + qs
    script = environ.get("SCRIPT_NAME") or ""
    if script and raw.startswith(script):
        raw = raw[len(script):]
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


@app.route("/", defaults={"subpath": ""}, methods=METHODS)
@app.route("/<path:subpath>", methods=METHODS)
def conan_dispatch(subpath):
    method = (request.method or "GET").upper()
    body = b""
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        body = request.get_data(cache=True) or b""

    handler = _FlaskHandler(_raw_path(), _email_headers(request.environ), body, _client_ip(), method)

    try:
        if method == "GET":
            handler.do_GET()
        elif method == "HEAD":
            handler.do_HEAD()
        elif method == "POST":
            handler.do_POST()
        elif method == "OPTIONS":
            handler.do_OPTIONS()
        else:
            handler.send_response(405)
            handler.send_header("Allow", "GET, HEAD, POST, OPTIONS")
            handler.end_headers()
    except Exception:
        detail = traceback.format_exc()
        try:
            core.log_system("Flask 请求异常: " + detail.replace("\n", " | ")[-1500:])
        except Exception:
            pass
        if sys.stderr:
            try:
                sys.stderr.write(detail + "\n")
            except Exception:
                pass
        part = handler.wfile.getvalue()
        if part:
            return Response(part, status=500, mimetype="text/plain")
        return Response("服务器内部错误，详见 logs/system.log\n", status=500, mimetype="text/plain")

    payload = handler.wfile.getvalue()
    resp = Response(payload, status=handler.status)
    for name, value in handler.response_headers:
        try:
            resp.headers[name] = value
        except Exception:
            pass
    resp.headers["Content-Length"] = str(len(payload)) if payload else (handler._declared_cl or "0")
    return resp


@app.after_request
def _cors(resp):
    if "Access-Control-Allow-Origin" not in resp.headers:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


_ALREADY_BOOTED = False


def _boot_once():
    """恢复下载任务并启动调度线程（与 socketserver 版共用）。CONAN_NO_SCHEDULER=1 可跳过。"""
    global _ALREADY_BOOTED
    if _ALREADY_BOOTED:
        return
    _ALREADY_BOOTED = True
    if os.environ.get("CONAN_NO_SCHEDULER") == "1":
        return
    try:
        core.bootstrap()
    except Exception as e:
        print("警告: 初始化失败（%s）" % e, flush=True)


_boot_once()


if __name__ == "__main__":
    port = int(os.environ.get("CONAN_PORT") or getattr(core, "PORT", 5000))
    print("Flask 版启动: http://127.0.0.1:%d" % port, flush=True)
    print("音乐根目录: %s" % getattr(core, "MUSIC_ROOT", "?"), flush=True)
    print("业务核心: %s" % CORE_FILE, flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
