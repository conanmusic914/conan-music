# -*- coding: utf-8 -*-
# Conan听歌系统 | 终极完美版 + 酷狗云端下载（已合并 musicDownload / musicdl）
# 修复：播放卡加载转圈、全屏、酷狗点击、新建歌单及封面上传
#
# 酷狗下载版使用说明：
# 1. 服务器需安装下载引擎：pip install musicdl
# 2. 管理员登录后进入「🌐 酷狗下载」，搜索歌手/专辑，点“⬇️ 下载”或“整张专辑”即可下载入库
# 3. 下载文件按「音乐根目录/歌手/专辑/歌名-歌手.ext」归档，酷狗有歌词时自动写入同名 .lrc
# 4. 下载记录保存在 kugou_downloads.json，已入库歌曲会显示“✅ 已入库”并可直接播放
# 5. 默认音源“酷狗优先、酷我兜底”，可在 KUGOU_DL_SOURCES 中调整
# 6. 下载时自动保存专辑封面 cover.jpg/Folder.jpg，并写入音频 ID3/FLAC/MP4 标签
# 7. 伴奏/DJ/铃声/现场等黑名单曲目默认不勾选；可在酷狗下载页开启“自动跳过黑名单”
# 8. 管理员密码、端口、token 有效期放在 config.json；管理员操作写入 admin_action.log
# 9. 管理员可在酷狗下载页使用“库扫描”检查丢音频/缺歌词/缺封面，并一键补歌词、补封面
import http.server
import socketserver
import os
import sys
import socket
import json
import base64
import zlib
import hmac
import html as _html
import time
import hashlib
import secrets
import shutil
import re
import difflib
import threading
import queue
import requests
import cgi
import zipfile
import csv
import traceback
import subprocess
from urllib.parse import quote, unquote, urlparse, parse_qs, urljoin
from concurrent.futures import ThreadPoolExecutor

try:
    requests.packages.urllib3.disable_warnings()
except Exception:
    pass

# pythonw 启动时 sys.stdout / sys.stderr 可能是 None；HTTP 请求日志写 stderr 会直接抛异常，
# 浏览器看到的就是 Empty reply from server。这里先把输出接到文件，保证隐藏窗口启动也能正常服务。
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# 目录规范（像安装软件那样，全部英文、分类存放）
DATA_DIR = os.path.join(APP_DIR, "appdata")      # 所有 *.json 运行数据
LOG_DIR = os.path.join(APP_DIR, "logs")          # 所有日志
CERT_DIR = os.path.join(APP_DIR, "certs")        # HTTPS 证书
MEDIA_DIR = os.path.join(APP_DIR, "media")       # 头像 / 封面 / 字体
CACHE_DIR = os.path.join(APP_DIR, "cache")       # 各类缓存
ARCHIVE_DIR = os.path.join(APP_DIR, "archive")   # 归档 / 备份
FONT_DIR = os.path.join(MEDIA_DIR, "fonts")
BACKUP_DIR = os.path.join(ARCHIVE_DIR, "backups")
for _d in (DATA_DIR, LOG_DIR, CERT_DIR, MEDIA_DIR, CACHE_DIR, ARCHIVE_DIR, FONT_DIR, BACKUP_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass
try:
    if sys.stdout is None:
        sys.stdout = open(os.path.join(LOG_DIR, "server_stdout.log"), "a", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.path.join(LOG_DIR, "server_stderr.log"), "a", encoding="utf-8")
except Exception:
    pass

# ====================== 可选依赖导入 ======================
try:
    from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, APIC, TRCK, TDRC, TCON, USLT
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.flac import FLAC, Picture
    from mutagen.wave import WAVE
    from mutagen import File as MutagenFile
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("警告: 未安装 mutagen，无法提取和显示内嵌封面。建议运行 pip install mutagen")

# musicdl 下载引擎（从 musicDownload 合并而来，用于酷狗云端歌曲下载）
try:
    from musicdl import musicdl
    MUSICDL_AVAILABLE = True
    print("✅ 酷狗下载引擎 musicdl 已加载")
except Exception as e:
    musicdl = None
    MUSICDL_AVAILABLE = False
    print(f"警告: musicdl 加载失败（{e}），酷狗云端下载功能不可用。请在服务器上运行 pip install musicdl")

# ====================== 基础配置 ======================
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
DEFAULT_SYSTEM_CONFIG = {
    "port": 5000,
    "admin_username": "Conan",
    "admin_password": "20140829czy",
    "token_ttl_days": 7,
    "sync_admin_password": True,
    "download_concurrency": 3,
    "download_max_active": 20,
    "download_timeout": 45,
    "download_speed_limit_kb": 0,
    "disk_warn_gb": 20,
    "disk_stop_gb": 5,
    "log_rotate_mb": 10,
    "blacklist_keywords": "伴奏,伴奏版,dj,铃声,demo,纯音乐,现场,live,inst,试听",
    "whitelist_keywords": "",
    "lyric_blacklist_keywords": "本歌词由,更多精彩歌词,歌词下载,QQ音乐,酷狗音乐,酷我音乐,网易云音乐",
    "album_scan_days": 30,
    "clean_album_names": False,
    "archive_template": "{歌手}/{专辑}/{歌名}-{歌手}",
    "cover_size": 480,
    "cover_retry": 2,
    "default_cover": True,
    "login_max_fail": 5,
    "login_lock_minutes": 10,
    "admin_ips": [],
    "admin_note": "",
    "guest_mode": "play",
    "task_backup": True,
    "share_token_ttl_hours": 24,
    "download_quality": "auto",
    "broadcast_text": "📢 本次更新：通知看完自动清红点、提现已关闭、首页广播、扫码听歌分享、刷新按钮已生效；新增网页全屏/歌词全屏分开、逐字歌词节奏与星星跳跃；全站启用“最美的风景”字体；修复《不要慌太阳下山有月光》下成 3:58 错误版本（下载时长校验已收紧）；歌单支持重命名和删除，删除前自动备份 playlists.json。",
    "broadcast_updated": "",
}

def _load_system_config():
    cfg = dict(DEFAULT_SYSTEM_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception as e:
            print(f"读取 config.json 失败，使用默认配置: {e}")
    else:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SYSTEM_CONFIG, f, ensure_ascii=False, indent=2)
            print("已生成 config.json，可修改管理员密码和 token 有效期")
        except Exception as e:
            print(f"创建 config.json 失败: {e}")
    return cfg

SYSTEM_CONFIG = _load_system_config()
ADMIN_USERNAME = str(SYSTEM_CONFIG.get("admin_username") or "Conan").strip() or "Conan"
try:
    TOKEN_TTL_SECONDS = max(3600, int(float(SYSTEM_CONFIG.get("token_ttl_days", 7)) * 86400))
except Exception:
    TOKEN_TTL_SECONDS = 7 * 86400
try:
    PORT = int(os.environ.get("CONAN_PORT") or SYSTEM_CONFIG.get("port", 5000))
except Exception:
    PORT = 5000
ADMIN_LOG_FILE = os.path.join(LOG_DIR, "admin_action.log")
SYSTEM_LOG_FILE = os.path.join(LOG_DIR, "server_download.log")
LOGIN_LOCK = threading.RLock()
LOGIN_FAILS = {}
SETTINGS_LOCK = threading.RLock()


def _config_int(key, default, low=None, high=None):
    try:
        value = int(float(SYSTEM_CONFIG.get(key, default)))
    except Exception:
        value = int(default)
    if low is not None: value = max(low, value)
    if high is not None: value = min(high, value)
    return value


def save_system_config(patch):
    """只保存白名单字段，原子写回 config.json。"""
    allowed = set(DEFAULT_SYSTEM_CONFIG.keys())
    with SETTINGS_LOCK:
        for k, v in (patch or {}).items():
            if k in allowed:
                SYSTEM_CONFIG[k] = v
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(SYSTEM_CONFIG, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
    return SYSTEM_CONFIG


def _share_secret_bytes():
    base = "conan-share-v1|" + str(SYSTEM_CONFIG.get("admin_password", "")) + "|" + ADMIN_USERNAME
    return hashlib.sha256(base.encode("utf-8")).digest()


def _make_share_token(rel, ttl_hours=None, note="", cover=""):
    rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if ttl_hours is None:
        try:
            ttl_hours = max(0, min(8760, int(float(SYSTEM_CONFIG.get("share_token_ttl_hours", 24)))))
        except Exception:
            ttl_hours = 24
    ttl_hours = int(ttl_hours or 0)
    if ttl_hours > 0:
        expires = int(time.time()) + ttl_hours * 3600
    else:
        expires = int(time.time()) + 10 * 365 * 24 * 3600
    payload = base64.urlsafe_b64encode(json.dumps(
        {"r": rel, "e": expires, "t": ttl_hours, "n": str(note or "")[:120], "c": str(cover or "")[:600]},
        ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_share_secret_bytes(), payload.encode("ascii"), hashlib.sha256).hexdigest()[:20]
    return payload + "." + sig


def _parse_share_token_full(token):
    text = str(token or "").strip()
    if "." not in text:
        return None
    payload, sig = text.rsplit(".", 1)
    expect = hmac.new(_share_secret_bytes(), payload.encode("ascii"), hashlib.sha256).hexdigest()[:20]
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(raw.decode("utf-8"))
        if int(data.get("e", 0)) < int(time.time()):
            return None
        rel = str(data.get("r", "")).replace("\\", "/").strip().lstrip("/")
        if not rel:
            return None
        return {
            "rel": rel, "exp": int(data.get("e", 0)), "ttl": int(data.get("t", 0) or 0),
            "note": str(data.get("n", "") or "")[:120], "cover": str(data.get("c", "") or "")[:600],
        }
    except Exception:
        return None


def _parse_share_token(token):
    data = _parse_share_token_full(token)
    return data["rel"] if data else None


def _share_qr_png(url, size=240):
    try:
        import qrcode
        from io import BytesIO
        box = max(2, min(20, int(size) // 41))
        qr = qrcode.QRCode(box_size=box, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image()
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""


def _share_qr_data_uri(url):
    try:
        png = _share_qr_png(url, 220)
        if not png:
            return ""
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    except Exception:
        return ""


def _config_list(key, default=""):
    value = SYSTEM_CONFIG.get(key, default)
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in re.split(r"[,，、\n]+", str(value or "")) if x.strip()]


def _log_rotate_check():
    try:
        max_mb = _config_int("log_rotate_mb", 10, 1, 1024)
        if os.path.exists(ADMIN_LOG_FILE) and os.path.getsize(ADMIN_LOG_FILE) > max_mb * 1024 * 1024:
            backup = ADMIN_LOG_FILE + "." + time.strftime("%Y%m%d_%H%M%S")
            os.replace(ADMIN_LOG_FILE, backup)
    except Exception:
        pass

# 音乐根目录：优先环境变量 CONAN_MUSIC_ROOT（Flask / PythonAnywhere 部署时用），其次命令行参数，最后本机默认路径
MUSIC_ROOT = (os.environ.get("CONAN_MUSIC_ROOT") or "").strip().strip('"')
if not MUSIC_ROOT:
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        MUSIC_ROOT = sys.argv[1]
    else:
        MUSIC_ROOT = r"T:\插件（python代码所运用的插件）\conan 支持系统\MP3歌曲库"

MUSIC_EXT = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac"}
FONT_LIB_DIR = os.path.join(os.path.dirname(MUSIC_ROOT), "字体库")  # 歌词可换字体：用户自备字体的目录
FONT_LIB_EXT = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
FONT_MIME = {".ttf": "font/ttf", ".otf": "font/otf", ".ttc": "font/collection",
             ".woff": "font/woff", ".woff2": "font/woff2"}
USER_DB_FILE = os.path.join(DATA_DIR, "users.json")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedbacks.json")
WITHDRAW_FILE = os.path.join(DATA_DIR, "withdrawals.json")
MESSAGE_FILE = os.path.join(DATA_DIR, "messages.json")
VIP_PAY_FILE = os.path.join(DATA_DIR, "vip_pays.json")
POINT_LOG_FILE = os.path.join(DATA_DIR, "point_logs.json")
COPYRIGHT_FILE = os.path.join(DATA_DIR, "copyrights.json")
KUGOU_AUTH_FILE = os.path.join(DATA_DIR, "kugou_auth.json")
KUGOU_DOWNLOAD_FILE = os.path.join(DATA_DIR, "kugou_downloads.json")
PLAYLISTS_FILE = os.path.join(DATA_DIR, "playlists.json")
AVATARS_DIR = os.path.join(MEDIA_DIR, "avatars")
COVERS_DIR = os.path.join(MEDIA_DIR, "covers")

os.makedirs(AVATARS_DIR, exist_ok=True)
os.makedirs(COVERS_DIR, exist_ok=True)

# 酷狗下载引擎配置
KUGOU_DL_SOURCES = ["KugouMusicClient", "KuwoMusicClient"]  # 酷狗优先，酷我兜底
KUGOU_DL_SEARCH_SIZE = 10
KUGOU_TEMP_DIR = os.path.join(CACHE_DIR, "temp")

# 积分规则
POINTS_PER_YUAN = 200
MIN_WITHDRAW_POINTS = 200
VIP_POINTS_PER_DAY = 100
DOWNLOAD_VIP_POINTS_PER_DAY = 200 # 下载会员 200积分/天
SINGLE_BUY_POINTS = 1000          # 单曲买断 1000积分
VIP_TRANSFER_PHONE = "13417120123"
TRIAL_SECONDS = 90
TRAIL_RATIO = 0.35

# 全局加密密钥
COM_KEY = b"ConanMusicSecureKey2024!@#"

# 限流
IP_RATE_WINDOW = 1.0
IP_RATE_MAX = 25
IP_HITS = {}

AUDIO_HITS = {}
AUDIO_RATE_WINDOW = 60.0
AUDIO_RATE_MAX = 150

SINGLE_SONG_HITS = {}
SINGLE_SONG_WINDOW = 60.0
SINGLE_SONG_MAX = 10

ONLINE_USERS = {}
DEDUPE_CACHE = {}
FILE_META_CACHE = {}
FILE_META_TTL = 300.0
FILE_META_MAX = 5000

# 音频解码校验缓存：同一文件（路径+大小+修改时间）不重复跑 ffmpeg
AUDIO_DECODE_CACHE = {}
AUDIO_DECODE_TTL = 3600.0
AUDIO_DECODE_MAX = 5000
AUDIO_DECODE_LOCK = threading.Lock()

# 双源搜索短缓存：同一关键词短时间内不重复请求酷狗/酷我
SEARCH_CACHE = {}
SEARCH_CACHE_TTL = 600.0
SEARCH_CACHE_MAX = 500
SEARCH_CACHE_LOCK = threading.Lock()

# ====================== 核心辅助函数 ======================
def safe_path(base_path, rel_path):
    if not rel_path: return os.path.normpath(base_path)
    base_abs = os.path.abspath(base_path)
    rel_path = rel_path.replace("/", os.sep).replace("\\", os.sep).lstrip(os.sep)
    target_abs = os.path.abspath(os.path.join(base_abs, rel_path))
    if not target_abs.startswith(base_abs + os.sep) and target_abs != base_abs:
        return None
    return target_abs

def _kugou_find_audio(rel):
    """按下载记录里的相对路径找音频；兼容扩展名变化、旧文件名和手工改名。"""
    rel = str(rel or "").replace("\\", "/").strip().strip("/")
    if not rel: return "", ""
    full = safe_path(MUSIC_ROOT, rel)
    if full and os.path.isfile(full) and os.path.splitext(full)[1].lower() in MUSIC_EXT:
        return rel, full
    base_rel = os.path.splitext(rel)[0]
    for ext in (".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac"):
        cand_rel = base_rel + ext
        cand = safe_path(MUSIC_ROOT, cand_rel)
        if cand and os.path.isfile(cand): return cand_rel, cand
    # 同目录下按文件名前缀/主名匹配，处理酷狗临时文件名或扩展名不同
    folder_rel = os.path.dirname(rel)
    folder = safe_path(MUSIC_ROOT, folder_rel) if folder_rel else MUSIC_ROOT
    if folder and os.path.isdir(folder):
        stem = os.path.splitext(os.path.basename(rel))[0]
        try:
            for fn in os.listdir(folder):
                if os.path.splitext(fn)[1].lower() not in MUSIC_EXT: continue
                if os.path.splitext(fn)[0] == stem:
                    found_rel = (folder_rel + "/" + fn).strip("/") if folder_rel else fn
                    return found_rel, os.path.join(folder, fn)
        except Exception:
            pass
    return "", ""

def _cache_file_meta(full_path):
    now = time.time()
    cached = FILE_META_CACHE.get(full_path)
    if cached and (now - cached["cache_time"]) < FILE_META_TTL:
        try:
            st = os.stat(full_path)
            if st.st_mtime == cached["mtime"]: return cached
        except Exception: return None
    try:
        st = os.stat(full_path)
        meta = {"size": st.st_size, "mtime": st.st_mtime, "cache_time": now}
        FILE_META_CACHE[full_path] = meta
        if len(FILE_META_CACHE) > FILE_META_MAX:
            for k in list(FILE_META_CACHE.keys())[:1000]: FILE_META_CACHE.pop(k, None)
        return meta
    except Exception: return None

def _check_dedupe(token, action, wait_sec=3):
    if not token: return True
    now = time.time()
    key = f"{token}_{action}"
    if key in DEDUPE_CACHE and now - DEDUPE_CACHE[key] < wait_sec: return False
    DEDUPE_CACHE[key] = now
    if len(DEDUPE_CACHE) > 5000:
        now_t = time.time()
        for k in list(DEDUPE_CACHE.keys()):
            if now_t - DEDUPE_CACHE[k] > 60: del DEDUPE_CACHE[k]
    return True

def hash_password(password, salt=None):
    if not salt: salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return pwd_hash, salt

def extract_cover(full_path):
    if not MUTAGEN_AVAILABLE or not os.path.isfile(full_path): return None, None
    ext = os.path.splitext(full_path)[1].lower()
    try:
        if ext == ".mp3":
            tags = ID3(full_path)
            for tag in tags.values():
                if tag.FrameID == 'APIC': return tag.data, tag.mime
        elif ext in (".m4a", ".mp4", ".aac"):
            tags = MP4(full_path)
            covr_list = tags.tags.get('covr')
            if covr_list: return bytes(covr_list[0]), "image/jpeg"
        elif ext == ".flac":
            tags = FLAC(full_path)
            if tags.pictures: return tags.pictures[0].data, tags.pictures[0].mime
    except Exception: pass
    return None, None

def get_audio_info(full_path):
    if not MUTAGEN_AVAILABLE or not os.path.isfile(full_path): return {}
    try:
        audio = MutagenFile(full_path)
        if not audio: return {}
        bitrate = getattr(audio.info, 'bitrate', 0) // 1000 if hasattr(audio.info, 'bitrate') else 0
        duration = getattr(audio.info, 'length', 0)
        return {"bitrate": bitrate, "duration": int(duration)}
    except Exception: 
        return {}

def _check_single_song_limit(ip, rel):
    now = time.time()
    key = f"{ip}_{rel}"
    arr = SINGLE_SONG_HITS.get(key, [])
    arr = [t for t in arr if now - t < SINGLE_SONG_WINDOW]
    if len(arr) >= SINGLE_SONG_MAX: return False
    arr.append(now)
    SINGLE_SONG_HITS[key] = arr
    return True

def _encrypt_stream(data, key=COM_KEY):
    out = bytearray(len(data))
    for i in range(len(data)):
        out[i] = data[i] ^ key[i % len(key)]
    return out

def _decrypt_stream(data, key=COM_KEY):
    return _encrypt_stream(data, key)

# ====================== 歌单 / 版权 ======================
def load_playlists():
    if not os.path.exists(PLAYLISTS_FILE): return []
    try:
        with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def save_playlists(data):
    tmp = PLAYLISTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PLAYLISTS_FILE)


def backup_playlists():
    try:
        if os.path.exists(PLAYLISTS_FILE):
            shutil.copy2(PLAYLISTS_FILE, PLAYLISTS_FILE + ".bak")
        return True
    except Exception as e:
        print(f"备份歌单失败: {e}")
        return False

def load_kugou_auth():
    if not os.path.exists(KUGOU_AUTH_FILE): return {}
    try:
        with open(KUGOU_AUTH_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_kugou_auth(data):
    _atomic_write_json(KUGOU_AUTH_FILE, data)

def load_copyrights():
    if not os.path.exists(COPYRIGHT_FILE): return {}
    try:
        with open(COPYRIGHT_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_copyrights(data):
    with open(COPYRIGHT_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ====================== 黑名单 / 封面 / 管理员日志 ======================
KUGOU_BLACKLIST_KEYWORDS = _config_list(
    "blacklist_keywords",
    "伴奏,伴奏版,dj,铃声,demo,纯音乐,现场,live,inst,试听",
)
KUGOU_WHITELIST_KEYWORDS = _config_list("whitelist_keywords", "")
ADMIN_LOG_LOCK = threading.Lock()

def _refresh_blacklist_config():
    global KUGOU_BLACKLIST_KEYWORDS, KUGOU_WHITELIST_KEYWORDS
    KUGOU_BLACKLIST_KEYWORDS = _config_list(
        "blacklist_keywords",
        "伴奏,伴奏版,dj,铃声,demo,纯音乐,现场,live,inst,试听",
    )
    KUGOU_WHITELIST_KEYWORDS = _config_list("whitelist_keywords", "")

def _kugou_blacklist_reason(song_name, album_name=""):
    text = f"{song_name or ''} {album_name or ''}".lower()
    # 白名单优先：例如某些现场版是用户明确想保留的
    for kw in KUGOU_WHITELIST_KEYWORDS:
        if kw.lower() in text:
            return ""
    for kw in KUGOU_BLACKLIST_KEYWORDS:
        if kw.lower() in text:
            return kw
    return ""

def _normalize_kugou_cover_url(url):
    url = str(url or "").strip()
    if not url: return ""
    if url.startswith("//"): url = "https:" + url
    size = _config_int("cover_size", 480, 240, 1080)
    return url.replace("{size}", str(size))

def log_admin_action(username, action, detail="", ip=""):
    """记录管理员关键操作，方便排查是谁误操作；日志过大自动分割。"""
    try:
        line = " | ".join([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            str(username or "-"),
            str(action or "-"),
            str(detail or "").replace("\n", " ")[:500],
            str(ip or "-"),
        ])
        with ADMIN_LOG_LOCK:
            _log_rotate_check()
            with open(ADMIN_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        print(f"写管理员日志失败: {e}")


def log_system(message):
    """下载/标签/歌词等运行日志，便于排查单曲失败；自动限制大小。"""
    try:
        with ADMIN_LOG_LOCK:
            try:
                if os.path.exists(SYSTEM_LOG_FILE) and os.path.getsize(SYSTEM_LOG_FILE) > _config_int("log_rotate_mb", 10, 1, 1024) * 1024 * 1024:
                    os.replace(SYSTEM_LOG_FILE, SYSTEM_LOG_FILE + "." + time.strftime("%Y%m%d_%H%M%S"))
            except Exception:
                pass
            with open(SYSTEM_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n")
    except Exception:
        pass


# ====================== 蓝牙连接/断开提醒（手机端检测并上报） ======================
BT_LAST_EVENT = {"text": "", "ts": 0}
BT_MONITOR_LOCK = threading.Lock()


def _bt_report(action, name):
    """手机网页通过 Web Bluetooth 检测到耳机连接/断开后上报；广播给所有页面弹提示。"""
    name = str(name or "").strip()[:60]
    if not name:
        name = "蓝牙设备"
    stamp = time.strftime("%H:%M:%S")
    if action == "connect":
        text = f"【手机蓝牙】已连接：{name}"
        toast_text = f"🔵 {text}"
    else:
        text = f"【手机蓝牙】已断开：{name}"
        toast_text = f"🔌 {text}"
    log_system(f"{text}（{stamp}）")
    try:
        add_message(ADMIN_USERNAME, f"{text}（{stamp}）", "system")
    except Exception:
        pass
    with BT_MONITOR_LOCK:
        BT_LAST_EVENT["text"] = toast_text
        BT_LAST_EVENT["ts"] = int(time.time() * 1000)


def search_kugou_grouped(keyword, page=1):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.kugou.com/"
    }
    page = max(1, int(page or 1))
    url = f"https://songsearch.kugou.com/song_search_v2?keyword={quote(keyword)}&page={page}&pagesize=100&platform=WebFilter&userid=0&clientver=2000&iscorrection=1"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        data = res.json()
        lists = data.get("data", {}).get("lists", [])

        kugou_auth = load_kugou_auth()
        downloads = load_kugou_downloads()
        albums = {}
        for item in lists:
            song_name = re.sub(r'<[^>]+>', '', item.get("SongName", "")).strip()
            singer = re.sub(r'<[^>]+>', '', item.get("SingerName", "")).strip()
            album_name = re.sub(r'<[^>]+>', '', item.get("AlbumName", "")).strip()
            if not album_name: album_name = "未知专辑"
            album_id = str(item.get("AlbumID") or "").strip()
            # 优先用酷狗 AlbumID 分组，避免不同歌手的同名专辑被合并
            group_key = album_id or f"{album_name}||{singer}"
            file_hash = str(item.get("FileHash") or "").strip()
            cover = _normalize_kugou_cover_url(item.get("Image") or item.get("AlbumImage") or "")
            blacklisted = _kugou_blacklist_reason(song_name, album_name)

            auth_key = f"{song_name}-{singer}"
            is_auth = kugou_auth.get(auth_key, False)

            local_rel = ""
            entry = downloads.get(auth_key)
            if entry and entry.get("rel"):
                found_rel, full = _kugou_find_audio(entry["rel"])
                if full and os.path.isfile(full): local_rel = found_rel

            if group_key not in albums:
                albums[group_key] = {
                    "album": album_name, "album_id": album_id, "singer": singer,
                    "cover": cover, "songs": []
                }
            if cover and not albums[group_key].get("cover"):
                albums[group_key]["cover"] = cover

            albums[group_key]["songs"].append({
                "name": song_name, "singer": singer, "album": album_name, "album_id": album_id,
                "auth_key": auth_key, "is_auth": is_auth,
                "hash": file_hash, "local_rel": local_rel, "cover": cover, "blacklisted": blacklisted,
                "duration": int(item.get("Duration") or 0),
                "filesize": int(item.get("FileSize") or 0)
            })
        return list(albums.values())
    except Exception as e:
        print(f"酷狗搜索失败: {e}")
        return []


def _kugou_album_tracks(album_name, singer="", album_id=""):
    """按专辑名/歌手/AlbumID 找回完整曲目，修复搜索接口只返回部分歌曲的问题。"""
    album_name = str(album_name or "").strip()
    singer = str(singer or "").strip()
    album_id = str(album_id or "").strip()
    if not album_name and not album_id: return []
    cache_key = album_id or f"{album_name}|{singer}"
    with KUGOU_ALBUM_CACHE_LOCK:
        cached = KUGOU_ALBUM_CACHE.get(cache_key)
        if cached and time.time() - cached.get("time", 0) < KUGOU_ALBUM_CACHE_TTL:
            return cached.get("tracks", [])
    keywords = []
    if album_name and singer: keywords.append(f"{album_name} {singer}")
    if album_name: keywords.append(album_name)
    if singer: keywords.append(singer)
    found = {}
    for kw in keywords:
        for pg in (1, 2, 3):
            groups = search_kugou_grouped(kw, pg)
            if not groups:
                break
            for group in groups:
                gid = str(group.get("album_id") or "").strip()
                g_album = str(group.get("album") or "").strip()
                g_singer = str(group.get("singer") or "").strip()
                if album_id and gid:
                    if gid != album_id: continue
                else:
                    if _norm_text(g_album) != _norm_text(album_name): continue
                    if singer and _norm_text(singer) not in _norm_text(g_singer) and _norm_text(g_singer) not in _norm_text(singer):
                        continue
                for song in group.get("songs", []) or []:
                    key = song.get("auth_key") or song.get("hash") or song.get("name")
                    if not key or key in found: continue
                    found[key] = song
    tracks = list(found.values())
    with KUGOU_ALBUM_CACHE_LOCK:
        if len(KUGOU_ALBUM_CACHE) > 300: KUGOU_ALBUM_CACHE.clear()
        KUGOU_ALBUM_CACHE[cache_key] = {"time": time.time(), "tracks": tracks}
    return tracks


KUGOU_COVER_LOOKUP_CACHE = {}
KUGOU_COVER_LOOKUP_LOCK = threading.RLock()

def _kugou_find_cover_url(song, singer, album=""):
    """给单首下载兜底找专辑封面：优先精确歌名+歌手，再按歌名搜。"""
    key = (str(song or ""), str(singer or ""), str(album or ""))
    with KUGOU_COVER_LOOKUP_LOCK:
        if key in KUGOU_COVER_LOOKUP_CACHE:
            return KUGOU_COVER_LOOKUP_CACHE[key]
    result = ""
    for keyword in (f"{song} {singer}".strip(), str(song or "").strip()):
        if not keyword: continue
        for group in search_kugou_grouped(keyword):
            group_album = str(group.get("album", "") or "")
            for s in group.get("songs", []) or []:
                if _norm_text(s.get("name", "")) != _norm_text(song): continue
                if singer and _norm_text(s.get("singer", "")) != _norm_text(singer): continue
                if album and group_album and _norm_text(group_album) != _norm_text(album):
                    continue
                result = s.get("cover", "") or group.get("cover", "") or ""
                if result: break
            if result: break
        if result: break
    with KUGOU_COVER_LOOKUP_LOCK:
        if len(KUGOU_COVER_LOOKUP_CACHE) > 200: KUGOU_COVER_LOOKUP_CACHE.clear()
        KUGOU_COVER_LOOKUP_CACHE[key] = result
    return result


# ====================== 酷狗云端下载引擎（合并 musicDownload / musicdl） ======================
KUGOU_TASKS = {}
KUGOU_TASK_LOCK = threading.RLock()
KUGOU_PERSIST_LOCK = threading.RLock()
KUGOU_DATA_LOCK = threading.RLock()
KUGOU_FILE_LOCK = threading.RLock()
KUGOU_REFRESH_LOCK = threading.Lock()
KUGOU_SCHEDULER_STARTED = False
LYRIC_UPGRADE_LOCK = threading.RLock()
LYRIC_UPGRADE_STATE = {
    "running": False, "stop": False, "total": 0, "done": 0,
    "ok": 0, "failed": 0, "current": "", "message": ""
}
KUGOU_SCHEDULER_LOCK = threading.Lock()
KUGOU_ACTIVE_THREADS = {}
KUGOU_MAX_TASKS = 100
KUGOU_MAX_CONCURRENT = _config_int("download_concurrency", 3, 1, 8)
KUGOU_MAX_ACTIVE_TASKS = _config_int("download_max_active", 20, 1, 200)
KUGOU_TASK_TIMEOUT = _config_int("download_timeout", 45, 10, 600)
KUGOU_TASK_BACKUP_FILE = os.path.join(DATA_DIR, "download_task_bak.json")
KUGOU_ALBUM_CACHE = {}
KUGOU_ALBUM_CACHE_LOCK = threading.RLock()
KUGOU_ALBUM_CACHE_TTL = 1800
KUGOU_LYRIC_CACHE = {}
KUGOU_TASK_FILE = os.path.join(DATA_DIR, "download_task.json")
KUGOU_CREATE_HITS = {}
KUGOU_CREATE_WINDOW = 600.0
KUGOU_CREATE_MAX = 30

KUGOU_LYRIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.kugou.com/",
}


def _atomic_write_json(path, data):
    """先写临时文件再替换，避免服务器崩溃/断电把 json 写坏"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"保存 {path} 失败: {e}")
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception:
            pass
        return False


def load_kugou_downloads():
    if not os.path.exists(KUGOU_DOWNLOAD_FILE): return {}
    try:
        with open(KUGOU_DOWNLOAD_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


def save_kugou_downloads(data):
    _atomic_write_json(KUGOU_DOWNLOAD_FILE, data)


_WIN_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def _safe_segment(name, fallback="未知"):
    """Windows 文件名安全过滤：\\ / : * ? " < > | 控制字符、保留名、结尾点空格"""
    s = str(name or "")
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip(" .").rstrip(" .")
    if not s: return fallback
    if s.upper() in _WIN_RESERVED_NAMES or s.upper().split(".")[0] in _WIN_RESERVED_NAMES:
        s = "_" + s
    s = s[:80].rstrip(" .")
    return s or fallback


def _get_song_val(obj, key, default=""):
    if isinstance(obj, dict): return obj.get(key, default)
    return getattr(obj, key, default) if hasattr(obj, key) else default


def _norm_text(s):
    return re.sub(r"[\s\-_·、,，&＆/]+", "", str(s or "")).lower()


# ====================== 下载音质档（标准/高品/无损/Hi-Res，和酷狗一致） ======================
KUGOU_QUALITY_OPTIONS = ("auto", "standard", "high", "lossless", "hires")


def _candidate_quality_key(info):
    """按扩展名和估算码率给候选音源分档：1=标准 2=高品 3=无损 4=Hi-Res"""
    ext = str(_get_song_val(info, "ext", "") or "").lower()
    if ext in ("flac", "ape", "wav", "alac"):
        return 3
    if ext in ("dsf", "dff"):
        return 4
    size = 0
    try:
        size = int(float(_get_song_val(info, "file_size_bytes", 0) or 0))
    except Exception:
        size = 0
    dur = 0
    try:
        dur = float(_get_song_val(info, "duration_s", 0) or 0)
    except Exception:
        dur = 0
    kbps = (size * 8 / dur / 1000) if (dur > 0 and size > 0) else 0
    if kbps >= 850:
        return 4
    if kbps >= 300:
        return 3
    if kbps >= 160:
        return 2
    return 1


def _apply_quality_preference(candidates, quality):
    """把符合音质档的候选排到最前；无命中时保留原顺序兜底，绝不因此少下歌。"""
    quality = str(quality or "auto")
    if quality not in KUGOU_QUALITY_OPTIONS or quality == "auto" or not candidates:
        return candidates
    def key(c):
        try:
            return _candidate_quality_key(c)
        except Exception:
            return 1
    if quality == "standard":
        wanted = [c for c in candidates if key(c) == 1]
    elif quality == "high":
        wanted = [c for c in candidates if key(c) in (2, 3)]
    elif quality == "lossless":
        wanted = [c for c in candidates if key(c) in (3, 4)]
    else:
        wanted = [c for c in candidates if key(c) == 4]
    if not wanted:
        return candidates
    seen = set()
    merged = []
    for c in wanted + candidates:
        if id(c) in seen:
            continue
        seen.add(id(c))
        merged.append(c)
    return merged


# ====================== 酷狗 MV（搜索 + 带签名播放直链，和酷狗一致） ======================
KUGOU_MV_SECRET = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
KUGOU_MV_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")
MV_SEARCH_CACHE = {}
MV_PLAY_CACHE = {}


def _mv_cache_get(cache, key, ttl_seconds):
    entry = cache.get(key)
    if entry and time.time() - entry[0] < ttl_seconds:
        return entry[1]
    return None


def _mv_cache_put(cache, key, value):
    cache[key] = (time.time(), value)
    if len(cache) > 200:
        try:
            cache.pop(next(iter(cache)))
        except Exception:
            pass


def search_kugou_mv(keyword):
    kw = str(keyword or "").strip()
    if not kw:
        return []
    cached = _mv_cache_get(MV_SEARCH_CACHE, kw, 600)
    if cached is not None:
        return cached
    headers = {"User-Agent": KUGOU_MV_UA, "Referer": "https://m.kugou.com/"}
    url = ("https://mobiles.kugou.com/api/v3/search/mv?keyword=" + quote(kw)
           + "&pagesize=20&page=1&plat=2&version=9108")
    result = []
    try:
        res = requests.get(url, headers=headers, timeout=8)
        data = res.json()
        for it in (data.get("data") or {}).get("info", []) or []:
            if not isinstance(it, dict):
                continue
            img = str(it.get("imgurl") or "")
            if img and "{size}" in img:
                img = img.replace("{size}", "400")
            result.append({
                "name": str(it.get("filename") or "").strip(),
                "singer": str(it.get("singername") or "").strip(),
                "hash": str(it.get("hash") or "").strip(),
                "duration": int(it.get("duration") or 0),
                "cover": img,
                "publish": str(it.get("publishdate") or "").strip(),
                "heat": int(it.get("historyheat") or 0),
            })
        result = [x for x in result if x["hash"] and x["name"]]
    except Exception as e:
        log_system(f"酷狗MV搜索失败 | {kw} | {e}")
        result = []
    _mv_cache_put(MV_SEARCH_CACHE, kw, result)
    return result


def kugou_mv_play_urls(mv_hash):
    """按酷狗 getInterFacePublic 签名算法请求 MV 播放接口，返回 {name,singer,cover,duration,urls{rq,sq,le}}"""
    key = str(mv_hash or "").strip()
    if not key:
        return None
    cached = _mv_cache_get(MV_PLAY_CACHE, key, 1800)
    if cached is not None:
        return cached
    now_ms = int(time.time() * 1000)
    params = {
        "appid": "1005", "srcappid": "2919", "clientver": "20000",
        "clienttime": str(now_ms), "mid": str(now_ms), "uuid": str(now_ms), "dfid": "-",
    }
    params.update({"cmd": "100", "hash": key, "ext": "mp4", "ismp3": "1", "ssl": "1"})
    parts = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    params["signature"] = hashlib.md5((KUGOU_MV_SECRET + parts + KUGOU_MV_SECRET).encode("utf-8")).hexdigest().upper()
    headers = {"User-Agent": KUGOU_MV_UA, "Referer": "https://m.kugou.com/"}
    result = None
    try:
        res = requests.get("https://m3ws.kugou.com/api/v1/mv/infov2", params=params, headers=headers, timeout=8)
        data = res.json()
        mv = data.get("mvdata") or {}
        urls = {}
        for tier in ("rq", "sq", "le"):
            seg = mv.get(tier) or {}
            link = str(seg.get("downurl") or "")
            if not link and isinstance(seg.get("backupdownurl"), list) and seg.get("backupdownurl"):
                link = str(seg["backupdownurl"][0] or "")
            if link:
                link = link.replace("http://", "https://")
            urls[tier] = link
        result = {
            "name": str(data.get("songname") or "").strip(),
            "singer": str(data.get("singer") or "").strip(),
            "cover": str(data.get("mvicon") or "").replace("http://", "https://"),
            "duration": int(data.get("duration") or 0),
            "urls": urls,
        }
        if not any(urls.values()):
            result = None
    except Exception as e:
        log_system(f"酷狗MV播放接口失败 | {key} | {e}")
        result = None
    _mv_cache_put(MV_PLAY_CACHE, key, result)
    return result


# ===================== 在线听歌（流式播放，不落盘） =====================
ONLINE_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"),
    "Referer": "http://m.kugou.com/",
}
ONLINE_SEARCH_CACHE = {}
ONLINE_URL_CACHE = {}
ONLINE_DUR_CACHE = {}
ONLINE_ALT_CACHE = {}
ONLINE_SEARCH_TOTAL = {}
ONLINE_RECOMMEND_CACHE = {}
ONLINE_CLIENT = None
ONLINE_CLIENT_LOCK = threading.Lock()
ONLINE_STREAM_HOSTS = ("kugou.com", "kgcdn.com", "kglinker.com", "kugou.net",
                       "kuwo.cn", "kuwo.com", "bd.kuwo.cn")
ONLINE_RANK_MAP = {
    "top": (8888, "TOP500"),
    "new": (74534, "新歌榜"),
    "guochao": (85897, "国潮音乐榜"),
    "shanggan": (51340, "伤感榜"),
    "minyao": (51341, "民谣榜"),
    "dj": (24971, "DJ热歌榜"),
}


def _online_item(name, singer, song_hash, duration, album="", cover="", album_id="", extra=None):
    return {
        "name": str(name or "").strip(),
        "singer": str(singer or "").strip(),
        "album": str(album or "").strip(),
        "hash": str(song_hash or "").strip(),
        "duration": int(duration or 0),
        "cover": str(cover or "").replace("http://", "https://"),
        "album_id": str(album_id or "").strip(),
        "extra": extra or {},
    }


def _kugou_search_v2_online(kw, limit=20, page=1):
    """mobilecdn 搜不到时的第二搜索源（songsearch v2），同样给出可播条目"""
    kw = str(kw or "").strip()
    if not kw: return []
    out, seen = [], set()
    try:
        d = requests.get("https://songsearch.kugou.com/song_search_v2",
                         params={"keyword": kw, "page": max(1, int(page or 1)),
                                 "pagesize": max(5, min(50, limit)),
                                 "platform": "Web", "format": "json"},
                         headers=ONLINE_UA, timeout=(3, 10)).json()
        lists = (d.get("data") or {}).get("lists") or [] if isinstance(d, dict) else []
        for it in lists:
            if not isinstance(it, dict): continue
            name = str(it.get("SongName") or "").strip()
            fhash = str(it.get("FileHash") or "").strip().lower()
            if not name or not fhash or fhash in seen: continue
            seen.add(fhash)
            out.append(_online_item(
                name, str(it.get("SingerName") or "").strip(), fhash,
                it.get("Duration") or 0,
                album=str(it.get("AlbumName") or ""),
                cover="", album_id=it.get("AlbumID") or "",
                extra={"filesize": int(it.get("FileSize") or 0), "quality": "128"},
            ))
    except Exception as e:
        log_system(f"第二搜索源失败 | {kw} | {e}")
    return out


def kugou_online_search(kw, limit=20, page=1):
    """酷狗在线搜歌（不落盘，直接给前端播）；page 支持无限翻页拿全部"""
    kw = str(kw or "").strip()
    if not kw:
        return []
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    key = f"{kw}|{limit}|{page}"
    cached = _mv_cache_get(ONLINE_SEARCH_CACHE, key, 300)
    if cached is not None:
        return cached
    out = []
    try:
        r = requests.get("http://mobilecdn.kugou.com/api/v3/search/song",
                         params={"keyword": kw, "page": page, "pagesize": max(5, min(50, limit))},
                         headers=ONLINE_UA, timeout=(3, 8))
        raw = r.json() or {}
        d = raw.get("data") or {}
        info = d.get("info") or []
        try:
            ONLINE_SEARCH_TOTAL[key] = int(d.get("total") or 0)
        except Exception:
            pass
        for it in info:
            if not isinstance(it, dict):
                continue
            name = str(it.get("songname") or "").strip()
            fhash = str(it.get("hash") or "").strip()
            if not name or not fhash:
                continue
            cover = ""
            tp = it.get("trans_param")
            if isinstance(tp, dict):
                cover = str(tp.get("union_cover") or "")
                if cover:
                    cover = cover.replace("{size}", "300")
            out.append(_online_item(
                name, str(it.get("singername") or "").strip(), fhash,
                it.get("duration") or 0,
                album=str(it.get("album_name") or ""),
                cover=cover, album_id=it.get("album_id") or "",
                extra={"filesize": int(it.get("filesize") or 0),
                       "quality": "SQ" if it.get("sqhash") else ("320" if it.get("320hash") else "128")},
            ))
    except Exception as e:
        log_system(f"在线搜索失败 | {kw} | {e}")
    if not out and page == 1:
        out = _kugou_search_v2_online(kw, limit)
    if out:
        _mv_cache_put(ONLINE_SEARCH_CACHE, key, out)
    return out


def kugou_online_recommend(rank_key="top", limit=30, page=1):
    """酷狗榜单推荐（默认 TOP500），支持翻页拿完整榜单"""
    rankid, rankname = ONLINE_RANK_MAP.get(str(rank_key or "top"), ONLINE_RANK_MAP["top"])
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    key = f"{rankid}|{limit}|{page}"
    total_key = f"rank|{rankid}|{limit}|{page}"
    cached = _mv_cache_get(ONLINE_RECOMMEND_CACHE, key, 600)
    if cached is not None:
        if isinstance(cached, tuple) and len(cached) == 2 and isinstance(cached[0], list):
            ONLINE_SEARCH_TOTAL[total_key] = int(cached[1] or 0)
            return cached[0]
        if isinstance(cached, list):
            return cached
    out = []
    try:
        r = requests.get("http://mobilecdn.kugou.com/api/v3/rank/song",
                         params={"rankid": rankid, "page": page, "pagesize": max(5, min(50, limit)),
                                 "plat": 0, "version": 8000},
                         headers=ONLINE_UA, timeout=(3, 10))
        raw = r.json() or {}
        d = raw.get("data") or {}
        info = d.get("info") or []
        try:
            ONLINE_SEARCH_TOTAL[f"rank|{rankid}|{limit}|{page}"] = int(d.get("total") or 0)
        except Exception:
            pass
        seen = set()
        for it in info:
            if not isinstance(it, dict):
                continue
            fhash = str(it.get("hash") or "").strip()
            fn = str(it.get("filename") or "").strip()
            name = str(it.get("songname") or "").strip()
            singer = str(it.get("singername") or "").strip()
            if not name and " - " in fn:
                name = fn.split(" - ", 1)[1].strip()
            if not singer and " - " in fn:
                singer = fn.split(" - ", 1)[0].strip()
            if not name or not fhash:
                continue
            uniq = (name + "|" + singer).lower()
            if uniq in seen:
                continue
            seen.add(uniq)
            cover = ""
            tp = it.get("trans_param")
            if isinstance(tp, dict):
                cover = str(tp.get("union_cover") or "")
                if cover:
                    cover = cover.replace("{size}", "300")
            out.append(_online_item(
                name, singer, fhash, it.get("duration") or 0,
                album=str(it.get("album_name") or ""), cover=cover,
                album_id=it.get("album_id") or "",
                extra={"rankname": rankname,
                       "quality": "SQ" if it.get("sqhash") else ("320" if it.get("320hash") else "128")},
            ))
    except Exception as e:
        log_system(f"在线推荐失败 | {rankname} | {e}")
    try:
        tot = int(ONLINE_SEARCH_TOTAL.get(total_key, 0) or 0)
    except Exception:
        tot = 0
    _mv_cache_put(ONLINE_RECOMMEND_CACHE, key, (out, tot))
    return out


def kugou_online_hot(limit=30):
    """经典热歌推荐：用一组华语热门歌手并发搜索，混合成首页推荐（缓存 30 分钟）"""
    key = f"hot|{limit}"
    cached = _mv_cache_get(ONLINE_RECOMMEND_CACHE, key, 1800)
    if cached is not None:
        return cached
    artists = ["周杰伦", "林俊杰", "陈奕迅", "邓紫棋", "李荣浩", "薛之谦",
               "张杰", "刘若英", "蔡依林", "孙燕姿", "五月天", "梁静茹",
               "周深", "毛不易", "王菲", "张碧晨"]

    def grab(artist):
        try:
            return kugou_online_search(artist, 3) or []
        except Exception:
            return []

    pool = []
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for songs in ex.map(grab, artists):
                for s in songs:
                    pool.append(s)
    except Exception as e:
        log_system(f"推荐热歌失败 | {e}")
    seen, out = set(), []
    for s in pool:
        k = (str(s.get("name", "") + "|" + s.get("singer", "")).lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= limit:
            break
    _mv_cache_put(ONLINE_RECOMMEND_CACHE, key, out)
    return out


def _online_stream_allowed(url):
    """流代理白名单：只允许酷狗/酷我 CDN，防止被当作任意代理"""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in ONLINE_STREAM_HOSTS)


_MP3_BR_V1L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_MP3_BR_V2L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
_MP3_SR_V1 = [44100, 48000, 32000, 0]
_MP3_SR_V2 = [22050, 24000, 16000, 0]


def _probe_audio_duration(url, headers=None, timeout=(3, 8)):
    """只读音频头 256KB，估算/读出时长（秒）。用于识别“错音频”（放出来完全不是这首歌）"""
    try:
        h = dict(headers or {})
        h["Range"] = "bytes=0-262143"
        r = requests.get(url, headers=h, timeout=timeout)
        data = bytes(r.content or b"")
        total = 0
        m = re.search(r"/(\d+)\s*$", str(r.headers.get("Content-Range") or ""))
        if m:
            total = int(m.group(1))
        if not total:
            total = int(r.headers.get("Content-Length") or 0) or len(data)
        # FLAC：STREAMINFO 里有精确总采样数
        idx = data.find(b"fLaC")
        if idx >= 0 and idx + 26 <= len(data):
            packed = int.from_bytes(data[idx + 18:idx + 26], "big")
            sr = (packed >> 44) & 0xFFFFF
            samples = packed & 0xFFFFFFFFF
            if sr and 0 < samples:
                sec = samples / float(sr)
                if 5 <= sec <= 20000:
                    return sec
        # MP3：找帧头算平均码率，再用总字节估算
        pos = data.find(b"\xff")
        tries = 0
        while pos != -1 and tries < 4000:
            tries += 1
            if pos + 4 < len(data):
                b1, b2 = data[pos + 1], data[pos + 2]
                if (b1 & 0xE0) == 0xE0:
                    ver = (b1 >> 3) & 0x3      # 3=MPEG1 2=MPEG2 0=MPEG2.5
                    layer = (b1 >> 1) & 0x3    # 1=Layer III
                    br_idx = (b2 >> 4) & 0xF
                    sr_idx = (b2 >> 2) & 0x3
                    if layer == 1 and br_idx and br_idx != 15 and sr_idx != 3:
                        br = _MP3_BR_V1L3[br_idx] if ver == 3 else _MP3_BR_V2L3[br_idx]
                        sr = (_MP3_SR_V1 if ver == 3 else _MP3_SR_V2)[sr_idx]
                        if br and sr and total:
                            sec = total * 8 / (br * 1000.0)
                            if 5 <= sec <= 20000:
                                return sec
            pos = data.find(b"\xff", pos + 1)
    except Exception:
        pass
    return 0.0


def _online_audio_len_ok(url, expect_sec, tol=0.25):
    """时长对不上就判定为错源；探不到时长时放行（不影响正常歌曲）"""
    if not url or not expect_sec:
        return True
    got = _probe_audio_duration(url, {"User-Agent": ONLINE_UA["User-Agent"],
                                      "Referer": "http://m.kugou.com/"})
    if not got:
        return True
    diff = abs(float(got) - float(expect_sec))
    if diff <= max(10.0, float(expect_sec) * tol):
        return True
    log_system(f"在线音源时长不符已丢弃 | 期望 {expect_sec}s 实际 {got:.0f}s | {str(url)[:90]}")
    return False


def _online_audio_size_ok(url, expect_size, tol=0.15):
    """文件大小对不上（通常是错版或试听片段）就判定为错源；拿不到大小则放行"""
    try:
        expect = float(expect_size or 0)
        if not url or expect <= 0:
            return True
        r = requests.head(url, headers={"User-Agent": ONLINE_UA["User-Agent"],
                                        "Referer": "http://m.kugou.com/"},
                          allow_redirects=True, timeout=(3, 8))
        cl = r.headers.get("Content-Length") or ""
        r.close()
        if not cl:
            return True
        got = float(cl)
        if got <= 0:
            return True
        if abs(got - expect) <= max(256 * 1024, expect * tol):
            return True
        log_system(f"在线音源大小不符已丢弃 | 期望 {int(expect)}B 实际 {int(got)}B | {str(url)[:90]}")
        return False
    except Exception:
        return True


def _kugou_hash_duration(name, singer, file_hash):
    """按 hash 反查酷狗官方时长/文件大小（songsearch v2），用于拦截错版音源"""
    fh = str(file_hash or "").strip().upper()
    if not fh:
        return 0, 0
    ck = f"dur|{fh}"
    hit = _mv_cache_get(ONLINE_DUR_CACHE, ck, 86400)
    if hit:
        return hit
    kw = f"{str(name or '').strip()} {str(singer or '').strip()}".strip() or fh
    try:
        r = requests.get("https://songsearch.kugou.com/song_search_v2",
                         params={"keyword": kw, "page": 1, "pagesize": 30,
                                 "platform": "WebFilter", "userid": 0, "clientver": 2000,
                                 "iscorrection": 1},
                         headers={"User-Agent": ONLINE_UA["User-Agent"],
                                  "Referer": "https://www.kugou.com/"},
                         timeout=(3, 8))
        lists = ((r.json() or {}).get("data") or {}).get("lists") or []
        for it in lists:
            if str(it.get("FileHash") or "").strip().upper() == fh:
                dur = int(it.get("Duration") or 0)
                size = int(it.get("FileSize") or 0)
                if dur:
                    out = (dur, size)
                    _mv_cache_put(ONLINE_DUR_CACHE, ck, out)
                    return out
    except Exception as e:
        log_system(f"反查官方时长失败 | {fh} | {e}")
    return 0, 0


def _online_tracker_url(file_hash):
    """酷狗官方 tracker：免费歌曲通常直接给直链（最快路径）"""
    if not file_hash:
        return "", ""
    for br in ("sq", "hq", "320", "128"):
        try:
            key = hashlib.md5((str(file_hash).lower() + "kgcloudv2").encode("utf-8")).hexdigest()
            params = {"key": key, "hash": file_hash, "br": br, "appid": "1005",
                      "pid": "2", "cmd": "25", "behavior": "play"}
            r = requests.get("http://trackercdn.kugou.com/i/v2/", params=params,
                             headers=ONLINE_UA, timeout=(3, 6)).json()
            urls = r.get("url") or []
            if urls:
                return str(urls[0]), br
        except Exception:
            continue
    return "", ""


def _online_client():
    """musicdl 解析客户端（单例，避免每首歌都重新初始化）"""
    global ONLINE_CLIENT
    if ONLINE_CLIENT is not None:
        return ONLINE_CLIENT
    with ONLINE_CLIENT_LOCK:
        if ONLINE_CLIENT is not None:
            return ONLINE_CLIENT
        tmp = os.path.join(KUGOU_TEMP_DIR, "_online_jit")
        os.makedirs(tmp, exist_ok=True)
        cfg = {src: {"search_size_per_source": 5, "work_dir": tmp, "disable_print": True}
               for src in KUGOU_DL_SOURCES}
        ONLINE_CLIENT = musicdl.MusicClient(
            music_sources=KUGOU_DL_SOURCES,
            init_music_clients_cfg=cfg,
            requests_overrides={src: {"timeout": 12} for src in KUGOU_DL_SOURCES},
        )
    return ONLINE_CLIENT


def _online_resolve_via_musicdl(name, singer, duration=0):
    """付费/受限歌曲兜底：用 musicdl 的多源解析器拿直链"""
    if not MUSICDL_AVAILABLE or not name:
        return ""
    try:
        client = _online_client()
        results = client.search(keyword=f"{name} {singer}".strip())
    except Exception as e:
        log_system(f"在线音源解析失败 | {name} - {singer} | {e}")
        return ""
    cands = []
    for src in KUGOU_DL_SOURCES:
        for info in (results or {}).get(src, []) or []:
            if str(_get_song_val(info, "download_url", "") or ""):
                cands.append(info)
    if not cands:
        return ""
    cands = [c for c in cands if _candidate_title_ok(c, name)] or cands
    pool = cands
    if duration:
        nearby = []
        for c in cands:
            try:
                cs = float(_get_song_val(c, "duration_s", 0) or 0)
            except Exception:
                cs = 0
            if cs and abs(cs - float(duration)) <= 12:
                nearby.append(c)
        pool = nearby or cands
    pool.sort(key=lambda c: 0 if not _candidate_version_bad(c) else 1)
    best = pool[0]
    return str(_get_song_val(best, "download_url", "") or "")


def _online_resolve_via_90svip(file_hash):
    """最快路径：90svip 解析器（实测约 2 秒拿到酷狗 CDN 直链，常为 flac）"""
    if not file_hash:
        return ""
    base = "https://music.90svip.cn/"
    try:
        r = requests.post(base,
                          data={"input": file_hash, "filter": "id", "type": "kugou", "page": "1"},
                          headers={"User-Agent": ONLINE_UA["User-Agent"], "Referer": base,
                                   "X-Requested-With": "XMLHttpRequest"}, timeout=(4, 10))
        lst = (r.json() or {}).get("data") or []
        if not lst:
            return ""
        url = str(lst[0].get("url") or "").strip()
        if not url:
            return ""
        if not url.startswith("http"):
            url = urljoin(base, url)
        try:
            resp = requests.get(url,
                                headers={"User-Agent": ONLINE_UA["User-Agent"], "Referer": base},
                                allow_redirects=True, stream=True, timeout=(4, 12))
            final = resp.url or ""
            ctype = (resp.headers.get("Content-Type") or "").lower()
            resp.close()
            if final.startswith("http") and ("audio" in ctype or "mpeg" in ctype or "flac" in ctype
                                            or "octet" in ctype):
                return final
            if final.startswith("http") and final != url:
                return final
        except Exception:
            pass
        return url if url.startswith("http") else ""
    except Exception as e:
        log_system(f"90svip 解析失败 | {file_hash} | {e}")
        return ""


def _online_musicdl_candidates(name, singer, duration=0):
    """musicdl 多源（酷我 / 酷狗）直链候选，按“同名 + 时长接近 + 非 DJ 版”排序"""
    if not MUSICDL_AVAILABLE or not name: return []
    ck = f"mdl|{name}|{singer}|{int(duration or 0)}"
    hit = _mv_cache_get(ONLINE_ALT_CACHE, ck, 600)
    if hit: return hit
    try:
        client = _online_client()
        results = client.search(keyword=f"{name} {singer}".strip())
    except Exception as e:
        log_system(f"多源解析失败 | {name} - {singer} | {e}")
        return []
    ts = _norm_song_title(name)
    ta = _norm_song_title(singer)
    ranked = []
    for src in KUGOU_DL_SOURCES:
        for info in (results or {}).get(src, []) or []:
            url = str(_get_song_val(info, "download_url", "") or "").strip()
            if not url: continue
            sname = _norm_song_title(_get_song_val(info, "song_name", ""))
            ssinger = _norm_song_title(_get_song_val(info, "singer", "")
                                       or _get_song_val(info, "singers", ""))
            if sname and ts and _name_sim(sname, ts) < 0.75: continue
            if ta and ssinger and _name_sim(ssinger, ta) < 0.4: continue
            try:
                cd = float(_get_song_val(info, "duration_s", 0) or 0)
            except Exception:
                cd = 0.0
            if duration and cd and abs(cd - float(duration)) > 60: continue
            dd = abs(cd - float(duration)) if (duration and cd) else 999.0
            bad = 1 if _candidate_version_bad(info) else 0
            ranked.append((dd, bad, url))
    ranked.sort(key=lambda x: (x[0], x[1]))
    out = [u for _, _, u in ranked]
    _mv_cache_put(ONLINE_ALT_CACHE, ck, out)
    return out


def online_play_payload(file_hash, name, singer, duration=0):
    """在线播放：官方 tracker → 90svip → musicdl 多源兜底；每级都校验时长，错音频直接丢弃"""
    try:
        duration = int(float(duration or 0))
    except Exception:
        duration = 0
    expect_size = 0
    if duration <= 0:
        # 前端没带时长时按 hash 反查官方时长，避免校验失效放过错版音源
        duration, expect_size = _kugou_hash_duration(name, singer, file_hash)
    cache_key = f"{file_hash}|{name}|{singer}|{int(duration or 0)}"
    cached = _mv_cache_get(ONLINE_URL_CACHE, cache_key, 1800)
    if cached is not None:
        return cached

    def pick(url, quality):
        if not url: return None
        if not _online_audio_len_ok(url, duration): return None
        if expect_size and not _online_audio_size_ok(url, expect_size): return None
        payload = {"url": url, "quality": quality}
        _mv_cache_put(ONLINE_URL_CACHE, cache_key, payload)
        return payload

    url, quality = _online_tracker_url(file_hash)
    got = pick(url, quality)
    if got: return got

    got = pick(_online_resolve_via_90svip(file_hash), "flac")
    if got: return got

    for u in _online_musicdl_candidates(name, singer, duration)[:6]:
        got = pick(u, "auto")
        if got: return got

    got = pick(_online_resolve_via_musicdl(name, singer, duration), "auto")
    if got: return got

    # 三级都没拿到合格音源：不缓存空结果，避免把“暂时失败”锁死 30 分钟
    return None


def online_lyric_text(file_hash, name, singer, duration=0):
    """在线歌词：hash → 关键词 → 歌曲搜索，三级兜底（含 KRC 逐字）；同名不同歌手不会串词"""
    session = requests.Session()
    session.headers.update(ONLINE_UA)
    text = _kugou_lrc_by_hash(session, file_hash, name, singer, duration)
    if not text:
        text = _kugou_lrc_by_keyword(session, name, singer, duration)
    if not text:
        text = _kugou_lrc_by_songsearch(session, name, singer, duration)
    return text


# ---------- 歌词译文 / 音译 ----------
LYRIC_TRANS_DIR = os.path.join(CACHE_DIR, "lyric_trans")
LYRIC_TRANS_JOBS = {}
LYRIC_TRANS_LOCK = threading.Lock()
LYRIC_TRANS_CACHE = {}

_KO_CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_KO_JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_KO_JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]

_JP_KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo", "ら": "ra", "り": "ri",
    "る": "ru", "れ": "re", "ろ": "ro", "わ": "wa", "を": "o", "ん": "n",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "っ": "", "ー": "-", "ゔ": "vu",
}
_JP_COMBO = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo", "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho", "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo", "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo", "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo", "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo", "てぃ": "ti", "でぃ": "di", "ふぁ": "fa",
    "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
}


def _detect_lyric_lang(texts):
    sample = " ".join(texts)[:2000]
    hangul = sum(1 for ch in sample if "\uac00" <= ch <= "\ud7a3")
    kana = sum(1 for ch in sample if ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff"))
    cyr = sum(1 for ch in sample if "\u0400" <= ch <= "\u04ff")
    han = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in sample if ch.isalpha() and ch.isascii())
    if hangul >= max(3, kana + han // 2):
        return "ko"
    if kana >= 3:
        return "ja"
    if cyr >= 3:
        return "ru"
    if latin >= 3 and latin >= han:
        return "en"
    if han:
        return "zh"
    return ""


def _romanize_hangul(text):
    out = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            idx = code - 0xAC00
            out.append(_KO_CHO[idx // 588] + _KO_JUNG[(idx % 588) // 28] + _KO_JONG[idx % 28])
        else:
            out.append(ch)
    return "".join(out)


def _romanize_kana(text):
    # 片假名统一转平假名后按表转换，汉字保留
    converted = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            converted.append(chr(code - 0x60))
        else:
            converted.append(ch)
    text = "".join(converted)
    out = []
    i = 0
    while i < len(text):
        two = text[i:i + 2]
        if two in _JP_COMBO:
            out.append(_JP_COMBO[two])
            i += 2
            continue
        ch = text[i]
        if ch in _JP_KANA:
            out.append(_JP_KANA[ch])
        elif ch == "っ" and i + 1 < len(text):
            nxt = _JP_KANA.get(text[i + 1], "")
            out.append(nxt[:1] if nxt else "")
            i += 1
            continue
        else:
            out.append(ch)
        i += 1
    res = "".join(out)
    return res


def _lyric_phonetic_map(texts, lang):
    if lang == "ko":
        return {t: _romanize_hangul(t) for t in texts}
    if lang == "ja":
        return {t: _romanize_kana(t) for t in texts}
    return {}


def _extract_lyric_lines(krc_text):
    """从 KRC/LRC 文本里抽出每句纯文本（与前端 parseLrc 的 c 字段对齐）"""
    out = []
    for raw in (krc_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        body = re.sub(r"<[^>]*>", "", line)
        body = re.sub(r"\[[^\]]*\]", "", body).strip()
        if not body:
            continue
        if body.lower().startswith(("by", "ar:", "ti:", "al:")):
            continue
        if len(body) > 120:
            continue
        out.append(body)
    uniq = []
    for t in out:
        if t not in uniq:
            uniq.append(t)
    return uniq


def _lyric_trans_cache_path(key):
    return os.path.join(LYRIC_TRANS_DIR, key + ".json")


def _lyric_trans_load(key):
    if key in LYRIC_TRANS_CACHE:
        return LYRIC_TRANS_CACHE[key]
    path = _lyric_trans_cache_path(key)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            LYRIC_TRANS_CACHE[key] = data
            return data
    except Exception:
        pass
    return None


def _my_memory_translate(text, langpair, retries=2):
    for _ in range(max(1, retries)):
        try:
            r = requests.get("https://api.mymemory.translated.net/get",
                             params={"q": text, "langpair": langpair},
                             headers=ONLINE_UA, timeout=(5, 15))
            data = r.json() or {}
            translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            if translated and "MYMEMORY WARNING" not in translated.upper():
                return translated
        except Exception:
            time.sleep(0.6)
    return ""


def _run_lyric_trans_job(key, texts, lang):
    langpair = {"ko": "ko|zh-CN", "ja": "ja|zh-CN", "en": "en|zh-CN", "ru": "ru|zh-CN"}.get(lang, "")
    result = {}
    if langpair:
        try:
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs = {ex.submit(_my_memory_translate, t, langpair): t for t in texts}
                for fut in futs:
                    src = futs[fut]
                    try:
                        tr = fut.result()
                    except Exception:
                        tr = ""
                    if tr:
                        result[src] = tr
        except Exception as e:
            log_system(f"歌词翻译失败 | {key} | {e}")
    payload = {"trans": result}
    try:
        os.makedirs(LYRIC_TRANS_DIR, exist_ok=True)
        with open(_lyric_trans_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass
    LYRIC_TRANS_CACHE[key] = payload
    with LYRIC_TRANS_LOCK:
        job = LYRIC_TRANS_JOBS.get(key) or {}
        job["status"] = "done"
        job["trans"] = result
        LYRIC_TRANS_JOBS[key] = job


def _ensure_lyric_trans_job(key, texts, lang):
    with LYRIC_TRANS_LOCK:
        job = LYRIC_TRANS_JOBS.get(key)
        if job:
            return job
        LYRIC_TRANS_JOBS[key] = {"status": "run", "trans": {}}
    threading.Thread(target=_run_lyric_trans_job, args=(key, texts, lang), daemon=True).start()
    return LYRIC_TRANS_JOBS[key]


def kugou_search_by_kind(kind, kw, limit=20, page=1):
    """分类搜索：综合/单曲/歌词=歌曲，专辑/歌手/歌单/MV 各自卡片"""
    kind = str(kind or "song").strip().lower()
    kw = str(kw or "").strip()
    if not kw:
        return []
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    if kind in ("song", "single", "all", "lyric"):
        return kugou_online_search(kw, limit, page)
    cache_key = f"{kind}|{kw}|{limit}|{page}"
    cached = _mv_cache_get(ONLINE_SEARCH_CACHE, cache_key, 300)
    if cached is not None:
        return cached
    out = []
    try:
        r = requests.get(f"http://mobilecdn.kugou.com/api/v3/search/{kind}",
                         params={"keyword": kw, "page": page, "pagesize": max(5, min(50, limit))},
                         headers=ONLINE_UA, timeout=(3, 10))
        data = (r.json() or {}).get("data")
        info = data.get("info") if isinstance(data, dict) else data
        try:
            if isinstance(data, dict):
                ONLINE_SEARCH_TOTAL[cache_key] = int(data.get("total") or 0)
        except Exception:
            pass
        if isinstance(info, dict):
            info = info.get("info") or []
        for it in (info or []):
            if not isinstance(it, dict):
                continue
            cover = ""
            if kind == "album":
                cover = str(it.get("imgurl") or "")
                out.append({"kind": "album", "id": str(it.get("albumid") or ""),
                            "title": str(it.get("albumname") or "").strip(),
                            "sub": str(it.get("singername") or "").strip(),
                            "extra": "%s 首 · %s" % (it.get("songcount") or 0,
                                                    str(it.get("publishtime") or "")[:10]),
                            "cover": cover.replace("{size}", "300").replace("http://", "https://")})
            elif kind == "singer":
                out.append({"kind": "singer", "id": str(it.get("singerid") or ""),
                            "title": str(it.get("singername") or "").strip(),
                            "sub": "歌手", "extra": "",
                            "cover": "/api/artist_icon?name=" + quote(str(it.get("singername") or ""))})
            elif kind == "special":
                cover = str(it.get("imgurl") or "")
                tp = it.get("trans_param")
                if not cover and isinstance(tp, dict):
                    try:
                        cover = (tp.get("skin") or {}).get("background_info", {}) \
                            .get("image_info", {}).get("image_url", "") or ""
                    except Exception:
                        cover = ""
                out.append({"kind": "special", "id": str(it.get("specialid") or ""),
                            "title": str(it.get("specialname") or "").strip(),
                            "sub": str(it.get("nickname") or "").strip(),
                            "extra": "%s 首 · %s 播放" % (it.get("songcount") or 0,
                                                       it.get("playcount") or 0),
                            "cover": cover.replace("{size}", "300").replace("http://", "https://")})
            elif kind == "mv":
                cover = str(it.get("imgurl") or "")
                out.append({"kind": "mv", "id": str(it.get("hash") or ""),
                            "title": str(it.get("filename") or "").strip(),
                            "sub": str(it.get("singername") or "").strip(),
                            "extra": _fmttime(it.get("duration") or 0),
                            "cover": cover.replace("{size}", "300").replace("http://", "https://")})
    except Exception as e:
        log_system(f"分类搜索失败 | {kind} | {kw} | {e}")
    _mv_cache_put(ONLINE_SEARCH_CACHE, cache_key, out)
    return out


def _fmttime(sec):
    try:
        sec = int(sec or 0)
    except Exception:
        sec = 0
    return "%02d:%02d" % (sec // 60, sec % 60)


SongInfoUtils_seconds = _fmttime


def online_album_songs(album_id="", album_name="", singer=""):
    """专辑曲目 → 统一成在线歌曲结构（可直接点播）"""
    songs = _kugou_album_tracks(album_name, singer, album_id) or []
    out = []
    for s in songs:
        name = str(s.get("name") or s.get("song") or "").strip()
        fhash = str(s.get("hash") or "").strip()
        if not name or not fhash:
            continue
        out.append(_online_item(name, str(s.get("singer") or singer or "").strip(), fhash,
                                s.get("duration") or 0,
                                album=str(s.get("album") or album_name or ""),
                                cover=str(s.get("cover") or ""),
                                album_id=str(s.get("album_id") or album_id or "")))
    return out


# ---------- 歌手分类（自建精选歌手库 + 酷狗头像/粉丝数） ----------
_SINGER_SEED = """
周杰伦|huayu|male
薛之谦|huayu|male
林俊杰|huayu|male
许嵩|huayu|male
陈奕迅|huayu|male
王力宏|huayu|male
张杰|huayu|male
张学友|huayu|male
刘德华|huayu|male
毛不易|huayu|male
李荣浩|huayu|male
华晨宇|huayu|male
汪苏泷|huayu|male
陶喆|huayu|male
周华健|huayu|male
李宗盛|huayu|male
张信哲|huayu|male
萧敬腾|huayu|male
庾澄庆|huayu|male
张宇|huayu|male
罗大佑|huayu|male
王菲|huayu|female
邓紫棋|huayu|female
孙燕姿|huayu|female
蔡依林|huayu|female
张韶涵|huayu|female
梁静茹|huayu|female
田馥甄|huayu|female
那英|huayu|female
张惠妹|huayu|female
莫文蔚|huayu|female
杨丞琳|huayu|female
刘若英|huayu|female
徐佳莹|huayu|female
张碧晨|huayu|female
李宇春|huayu|female
周笔畅|huayu|female
谭维维|huayu|female
张靓颖|huayu|female
郁可唯|huayu|female
黄龄|huayu|female
陈奕迅|yuanyu|male
张学友|yuanyu|male
张国荣|yuanyu|male
李克勤|yuanyu|male
张敬轩|yuanyu|male
刘德华|yuanyu|male
许冠杰|yuanyu|male
侧田|yuanyu|male
林忆莲|yuanyu|female
杨千嬅|yuanyu|female
容祖儿|yuanyu|female
谢安琪|yuanyu|female
郑秀文|yuanyu|female
梅艳芳|yuanyu|female
陈慧娴|yuanyu|female
卫兰|yuanyu|female
关淑怡|yuanyu|female
Beyond|yuanyu|group
草蜢|yuanyu|group
Twins|yuanyu|group
江蕙|minnan|female
伍佰|minnan|male
黄乙玲|minnan|female
詹雅雯|minnan|female
张清芳|minnan|female
萧煌奇|minnan|male
谢金燕|minnan|female
陈盈洁|minnan|female
林强|minnan|male
TAEYANG|korea|male
PSY|korea|male
Zico|korea|male
金泰妍|korea|female
IU|korea|female
BIGBANG|korea|group
EXO|korea|group
BLACKPINK|korea|group
BTS|korea|group
少女时代|korea|group
2NE1|korea|group
Super Junior|korea|group
SHINee|korea|group
TWICE|korea|group
NCT|korea|group
aespa|korea|group
NewJeans|korea|group
米津玄師|japan|male
宇多田光|japan|female
仓木麻衣|japan|female
滨崎步|japan|female
Aimer|japan|female
中岛美嘉|japan|female
玉置浩二|japan|male
松任谷由实|japan|female
山口百惠|japan|female
Ado|japan|female
YOASOBI|japan|group
ONE OK ROCK|japan|group
X JAPAN|japan|group
SPITZ|japan|group
King Gnu|japan|group
Official髭男dism|japan|group
L'Arc~en~Ciel|japan|group
Taylor Swift|western|female
Adele|western|female
Billie Eilish|western|female
Beyoncé|western|female
Rihanna|western|female
Ariana Grande|western|female
Lady Gaga|western|female
Dua Lipa|western|female
Alicia Keys|western|female
Ed Sheeran|western|male
Bruno Mars|western|male
Eminem|western|male
Michael Jackson|western|male
Justin Bieber|western|male
Post Malone|western|male
Coldplay|western|group
Imagine Dragons|western|group
Maroon 5|western|group
Queen|western|group
The Beatles|western|group
凤凰传奇|huayu|group
五月天|huayu|group
SHE|huayu|group
TFBOYS|huayu|group
筷子兄弟|huayu|group
动力火车|huayu|group
羽泉|huayu|group
水木年华|huayu|group
苏打绿|huayu|group
F.I.R.飞儿乐团|huayu|group
逃跑计划|huayu|group
旅行团|huayu|group
南征北战|huayu|group
好妹妹|huayu|group
房东的猫|huayu|group
"""
SINGER_LANGS = [("all", "全部"), ("huayu", "华语"), ("yuanyu", "粤语"), ("minnan", "闽南语"),
                ("korea", "韩国"), ("japan", "日本"), ("western", "欧美"), ("other", "其他")]
SINGER_SEXES = [("all", "全部"), ("male", "男"), ("female", "女"), ("group", "组合")]
_SINGER_INITIALS = {
    "周": "Z", "薛": "X", "林": "L", "许": "X", "陈": "C", "王": "W", "张": "Z", "刘": "L",
    "毛": "M", "李": "L", "华": "H", "汪": "W", "陶": "T", "庾": "Y", "萧": "X", "罗": "L",
    "邓": "D", "孙": "S", "蔡": "C", "梁": "L", "田": "T", "那": "N", "莫": "M", "杨": "Y",
    "徐": "X", "谭": "T", "郁": "Y", "黄": "H", "何": "H", "容": "R", "谢": "X", "郑": "Z",
    "梅": "M", "卫": "W", "关": "G", "侧": "C", "江": "J", "伍": "W", "詹": "Z", "金": "J",
    "少": "S", "米": "M", "滨": "B", "仓": "C", "中": "Z", "玉": "Y", "松": "S", "山": "S",
    "五": "W", "筷": "K", "动": "D", "羽": "Y", "水": "S", "苏": "S", "飞": "F", "逃": "T",
    "旅": "L", "南": "N", "好": "H", "房": "F", "凤": "F", "草": "C", "黄": "H", "田": "T",
    "艾": "A", "安": "A", "白": "B", "包": "B", "毕": "B", "曹": "C", "常": "C", "程": "C",
    "崔": "C", "戴": "D", "丁": "D", "董": "D", "杜": "D", "段": "D", "范": "F", "方": "F",
    "冯": "F", "付": "F", "高": "G", "葛": "G", "古": "G", "顾": "G", "郭": "G", "韩": "H",
    "杭": "H", "郝": "H", "洪": "H", "侯": "H", "胡": "H", "花": "H", "武": "W", "夏": "X",
    "向": "X", "辛": "X", "熊": "X", "曾": "Z", "赵": "Z", "朱": "Z", "钟": "Z", "左": "Z",
    "曲": "Q", "齐": "Q", "秦": "Q", "邱": "Q", "任": "R", "沈": "S", "施": "S", "石": "S",
    "宋": "S", "隋": "S", "唐": "T", "万": "W", "魏": "W", "温": "W", "巫": "W", "吴": "W",
    "席": "X", "邢": "X", "严": "Y", "阎": "Y", "颜": "Y", "阳": "Y", "姚": "Y", "叶": "Y",
    "尹": "Y", "应": "Y", "游": "Y", "于": "Y", "余": "Y", "孟": "M", "苗": "M", "闵": "M",
}
SINGER_POOL_FILE = os.path.join(DATA_DIR, "singer_pool.json")
SINGER_POOL = {"ts": 0, "data": {}}
SINGER_POOL_LOCK = threading.Lock()


def _singer_initial(name):
    s = str(name or "").strip()
    if not s:
        return "#"
    ch = s[0]
    if ch.isascii() and ch.isalpha():
        return ch.upper()
    return _SINGER_INITIALS.get(ch, "#")


def _load_singer_pool():
    global SINGER_POOL
    try:
        if os.path.isfile(SINGER_POOL_FILE):
            with open(SINGER_POOL_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f) or {}
            if isinstance(loaded, dict) and loaded.get("data"):
                SINGER_POOL = loaded
    except Exception:
        pass


def kugou_singer_pool(force=False):
    """拉取酷狗热门歌手池（头像 + 粉丝数），7 天缓存到本地 json"""
    global SINGER_POOL
    with SINGER_POOL_LOCK:
        if not force and SINGER_POOL.get("data") and (time.time() - float(SINGER_POOL.get("ts") or 0)) < 7 * 86400:
            return SINGER_POOL["data"]
        data = dict(SINGER_POOL.get("data") or {})
        try:
            for page in range(1, 7):
                r = requests.get("http://mobilecdn.kugou.com/api/v3/singer/list",
                                 params={"page": page, "pagesize": 50},
                                 headers=ONLINE_UA, timeout=(4, 12))
                info = ((r.json() or {}).get("data") or {}).get("info") or []
                for it in info:
                    if not isinstance(it, dict):
                        continue
                    name = str(it.get("singername") or "").strip()
                    if not name:
                        continue
                    cover = str(it.get("imgurl") or "")
                    cover = cover.replace("{size}", "240").replace("http://", "https://")
                    data[name] = {
                        "singerid": str(it.get("singerid") or ""),
                        "fans": int(it.get("fanscount") or 0),
                        "cover": cover,
                        "settled": bool(it.get("is_settled")),
                    }
        except Exception as e:
            log_system(f"歌手池拉取失败 | {e}")
        if data:
            SINGER_POOL = {"ts": time.time(), "data": data}
            try:
                with open(SINGER_POOL_FILE, "w", encoding="utf-8") as f:
                    json.dump(SINGER_POOL, f, ensure_ascii=False)
            except Exception:
                pass
        return data


try:
    _load_singer_pool()
except Exception:
    pass


def _singer_book():
    out = []
    for line in _SINGER_SEED.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name, lang, sex = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if lang == "other":
            sex = sex or "group"
        out.append({"name": name, "lang": lang, "sex": sex})
    return out


def singer_list(lang="all", sex="all", letter="", kw="", limit=200):
    """歌手分类列表：内置名单 + 酷狗池的头像/粉丝数，支持关键词/首字母筛选"""
    lang = (lang or "all").strip()
    sex = (sex or "all").strip()
    letter = (letter or "").strip().upper()
    kw = (kw or "").strip()
    pool = kugou_singer_pool()

    if kw:
        found = kugou_search_by_kind("singer", kw, min(40, max(10, limit)))
        base = [{"name": (x.get("title") or "").strip(), "lang": "all", "sex": "all"} for x in found]
    else:
        base = _singer_book()

    out = []
    seen = set()
    for s in base:
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        if not kw:
            if lang != "all" and s.get("lang") != lang:
                continue
            if sex != "all" and s.get("sex") != sex:
                continue
        ini = _singer_initial(name)
        if letter and ini != letter:
            continue
        seen.add(key)
        info = pool.get(name) or {}
        cover = info.get("cover") or ""
        if not cover:
            cover = "/api/artist_icon?name=" + quote(name)
        out.append({
            "name": name,
            "initial": ini,
            "lang": s.get("lang") or "all",
            "sex": s.get("sex") or "all",
            "cover": cover,
            "fans": int(info.get("fans") or 0),
            "settled": bool(info.get("settled")),
            "singerid": info.get("singerid") or "",
        })
    if not kw:
        out.sort(key=lambda x: (-int(x["fans"] or 0), x["initial"], x["name"]))
    return out[:max(1, int(limit))]


def recognize_audio(wav_bytes):
    """听歌识曲：Shazam 音频指纹（给 5~10 秒尽量干净的片段最好）"""
    try:
        from shazamio import Shazam
    except Exception:
        return {"ok": False, "msg": "识曲引擎未安装（缺少 shazamio 库）"}
    import asyncio
    import tempfile
    path = ""
    try:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(wav_bytes)

        async def _run():
            return await Shazam().recognize(path)

        out = asyncio.run(_run())
    except Exception as e:
        return {"ok": False, "msg": "识别失败：" + str(e)[:90]}
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass
    track = (out or {}).get("track") or {}
    title = str(track.get("title") or "").strip()
    if not title:
        return {"ok": False, "msg": "没识别出来，靠近声源、放 8 秒左右的副歌再试一次"}
    images = track.get("images") or {}
    genres = track.get("genres") or {}
    return {
        "ok": True,
        "title": title,
        "artist": str(track.get("subtitle") or "").strip(),
        "cover": str(images.get("coverart") or images.get("coverarthq") or ""),
        "genre": str(genres.get("primary") or ""),
    }


def _can_download(user):
    """下载权限：管理员 或 下载会员（听歌VIP仅在线听全曲，不下载到本地）"""
    if not isinstance(user, dict):
        return False
    return bool(user.get("role") == "admin" or is_download_vip(user))


def _kugou_rank_candidates(search_results, song_name, singer, album):
    """把 musicdl 的搜索结果按“歌名/歌手/专辑/音源优先级”排序，取最像的候选"""
    candidates = []
    for src in KUGOU_DL_SOURCES:
        for info in (search_results or {}).get(src, []) or []:
            candidates.append(info)

    target_name = _norm_text(song_name)
    target_singer = _norm_text(singer)
    target_album = _norm_text(album)

    if target_name:
        name_matched = []
        for info in candidates:
            n = _norm_text(_get_song_val(info, "song_name", ""))
            if n and (n == target_name or target_name in n or n in target_name):
                name_matched.append(info)
        candidates = name_matched
    if not candidates: return []

    def score(info):
        n = _norm_text(_get_song_val(info, "song_name", ""))
        s = _get_song_val(info, "singers", "")
        if isinstance(s, (list, tuple)): s = "&".join(str(x) for x in s)
        s = _norm_text(s)
        a = _norm_text(_get_song_val(info, "album", ""))
        src = str(_get_song_val(info, "source", ""))
        sc = 0
        if target_name and n == target_name: sc += 100
        elif target_name and (target_name in n or n in target_name): sc += 55
        if target_singer and s == target_singer: sc += 70
        elif target_singer and (target_singer in s or s in target_singer): sc += 35
        if target_album and a and (target_album == a): sc += 20
        elif target_album and a and (target_album in a or a in target_album): sc += 10
        for bad_word in ("伴奏", "纯音乐", "dj版", "铃声", "片段"):
            if bad_word in n and bad_word not in target_name: sc -= 40
        if src == "KugouMusicClient": sc += 25
        elif src == "KuwoMusicClient": sc += 12
        return sc

    candidates.sort(key=score, reverse=True)
    return candidates


def _clean_album_name(name):
    text = str(name or "").strip()
    if not SYSTEM_CONFIG.get("clean_album_names", False):
        return text
    # 只清理常见版本后缀，保留专辑主体，避免同名版本互相覆盖
    text = re.sub(r"\s*[\(（\[【]\s*(?:deluxe|豪华版|特别版|remix|重制版|典藏版)[^\)）\]】]*[\)）\]】]\s*$", "", text, flags=re.I)
    return text.strip() or str(name or "").strip()


def _kugou_target_dir(singer, album, album_artist="", year=""):
    # 专辑目录统一用专辑主歌手，避免合唱歌曲被拆到“周杰伦、某某”目录
    singer_seg = _safe_segment(album_artist or singer, "未知歌手")
    album_seg = _safe_segment(_clean_album_name(album), "")
    template = str(SYSTEM_CONFIG.get("archive_template") or "{歌手}/{专辑}").strip()
    # 只让模板控制目录层级；文件名仍由 _kugou_move_download 统一生成，避免路径穿越
    if "{歌名}" in template or "{song}" in template:
        template = "{歌手}/{专辑}"
    path = template.replace("{歌手}", singer_seg).replace("{artist}", singer_seg)
    path = path.replace("{专辑}", album_seg).replace("{album}", album_seg)
    year_seg = _safe_segment(str(year or ""), "") if year else ""
    path = path.replace("{年份}", year_seg).replace("{year}", year_seg)
    # 未实现的占位符不落成目录名，避免出现“{年份}”这种文件夹
    path = re.sub(r"\{[^{}]+\}", "", path)
    if not album or album_seg in ("未知", "未知专辑"):
        # 没有专辑信息时不强行造一个“未知专辑”目录
        path = re.sub(r"[\\/]*\{?专辑\}?", "", path).strip("\\/ ")
        path = singer_seg
    path = re.sub(r"[\\/]+", lambda m: os.sep, path).strip(os.sep)
    if not path or path in (".", ""): path = os.path.join(singer_seg, album_seg)
    target = os.path.join(MUSIC_ROOT, path)
    if not safe_path(MUSIC_ROOT, os.path.relpath(target, MUSIC_ROOT)): return None
    os.makedirs(target, exist_ok=True)
    return target


def _kugou_move_download(song_obj, target_dir, song_name, singer):
    save_path = str(_get_song_val(song_obj, "save_path", "") or "")
    if not save_path or not os.path.isfile(save_path): return None
    ext = os.path.splitext(save_path)[1].lstrip(".") or str(_get_song_val(song_obj, "ext", "mp3") or "mp3")
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:6].lower() or "mp3"
    base_name = _safe_segment(f"{song_name}-{singer}", "未知歌曲")
    new_audio = os.path.join(target_dir, f"{base_name}.{ext}")
    tmp_audio = new_audio + ".part"
    try:
        if os.path.exists(tmp_audio): os.remove(tmp_audio)
        shutil.move(save_path, tmp_audio)
        os.replace(tmp_audio, new_audio)
    except Exception as e:
        try:
            if os.path.exists(tmp_audio): os.remove(tmp_audio)
        except Exception: pass
        print(f"移动酷狗音频失败: {e}")
        return None
    old_lrc = os.path.splitext(save_path)[0] + ".lrc"
    if os.path.exists(old_lrc):
        new_lrc = os.path.join(target_dir, f"{base_name}.lrc")
        tmp_lrc = new_lrc + ".part"
        try:
            if os.path.exists(tmp_lrc): os.remove(tmp_lrc)
            shutil.move(old_lrc, tmp_lrc)
            os.replace(tmp_lrc, new_lrc)
        except Exception: pass
    return new_audio


# ====================== 封面 / 音频标签 ======================
KUGOU_COVER_CACHE = {}
KUGOU_COVER_LOCK = threading.RLock()

def _image_mime(data):
    if not data: return ""
    if data[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    return ""

def _kugou_cover_bytes(url):
    """下载酷狗封面，带内存缓存，失败不影响歌曲下载。"""
    normalized = _normalize_kugou_cover_url(url)
    if not normalized: return None, ""
    with KUGOU_COVER_LOCK:
        cached = KUGOU_COVER_CACHE.get(normalized)
    if cached: return cached
    try:
        retries = _config_int("cover_retry", 2, 0, 5) + 1
        for attempt in range(retries):
            try:
                res = requests.get(normalized, headers=KUGOU_LYRIC_HEADERS, timeout=10, verify=False)
                if res.status_code != 200:
                    time.sleep(0.4 * (attempt + 1)); continue
                data = res.content or b""
                if not data or len(data) > 8 * 1024 * 1024: return None, ""
                mime = _image_mime(data)
                if not mime: return None, ""
                with KUGOU_COVER_LOCK:
                    if len(KUGOU_COVER_CACHE) > 200: KUGOU_COVER_CACHE.clear()
                    KUGOU_COVER_CACHE[normalized] = (data, mime)
                return data, mime
            except Exception:
                if attempt + 1 < retries: time.sleep(0.5 * (attempt + 1))
        return None, ""
    except Exception as e:
        print(f"下载专辑封面失败: {e}")
        return None, ""

def _find_folder_cover(audio_path):
    folder = os.path.dirname(audio_path)
    for name in ("cover.jpg", "Folder.jpg", "cover.png", "Folder.png", "folder.jpg", "folder.png"):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate): return candidate
    return ""

def _read_cover_file(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        mime = _image_mime(data)
        return (data, mime) if mime else (None, "")
    except Exception:
        return None, ""

def _folder_cover_data(folder):
    """专辑文件夹封面：优先 cover.jpg/Folder.jpg；没有就用第一首音频的内嵌封面。"""
    if not folder or not os.path.isdir(folder): return None, ""
    cover = _find_folder_cover(os.path.join(folder, "_cover_lookup_.mp3"))
    if cover:
        data, mime = _read_cover_file(cover)
        if data: return data, mime
    try:
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() not in MUSIC_EXT: continue
            data, mime = extract_cover(os.path.join(folder, name))
            if data: return data, mime or _image_mime(data)
    except Exception:
        pass
    return None, ""

def _audio_cover_data(audio_path):
    """优先读音频内嵌封面，没有就读同目录 cover.jpg / Folder.jpg。"""
    data, mime = extract_cover(audio_path)
    if data: return data, mime or _image_mime(data)
    cover = _find_folder_cover(audio_path)
    if cover: return _read_cover_file(cover)
    return None, ""


def _artist_svg_icon(name):
    """歌手没有封面时生成酷狗风格的圆形首字渐变图标（SVG）。"""
    first = (name or "♪")[:1]
    first = (first.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))
    pairs = (("#ff758c", "#ff7eb3"), ("#86a8e7", "#7f7fd5"), ("#38f9d7", "#43e97b"),
             ("#fee140", "#fa709a"), ("#330867", "#30cfd0"), ("#fed6e3", "#a8edea"),
             ("#b490ca", "#5ee7df"), ("#fa71cd", "#c471f5"), ("#f9d423", "#f83600"),
             ("#005bea", "#00c6fb"), ("#12c2e9", "#c471ed"), ("#ff9966", "#ff5e62"))
    h = sum(ord(ch) for ch in (name or ""))
    c1, c2 = pairs[h % len(pairs)]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
           '<defs><linearGradient id="ag" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
           '<circle cx="60" cy="60" r="60" fill="url(#ag)"/>'
           f'<text x="60" y="64" font-size="54" text-anchor="middle" dominant-baseline="middle" '
           'fill="#ffffff" font-family="Microsoft YaHei, PingFang SC, sans-serif">'
           f'{first}</text></svg>')
    return svg.encode("utf-8"), "image/svg+xml"


ARTIST_ICON_DIR = os.path.join(CACHE_DIR, "artist_icons")
ARTIST_ICON_UA = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
    "Referer": "http://m.kugou.com/",
}


def _artist_icon_cache_path(name, ext):
    return os.path.join(ARTIST_ICON_DIR, hashlib.md5((name or "").encode("utf-8")).hexdigest() + ext)


def _artist_icon_cache_read(name):
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        p = _artist_icon_cache_path(name, ext)
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    data = f.read()
                if data:
                    return data, _image_mime(data) or ("image/png" if ext == ".png" else "image/jpeg")
            except Exception:
                pass
    return None, ""


def _artist_icon_cache_write(name, data, mime):
    try:
        os.makedirs(ARTIST_ICON_DIR, exist_ok=True)
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
               "image/gif": ".gif"}.get((mime or "").split(";")[0].strip().lower(), ".jpg")
        path = _artist_icon_cache_path(name, ext)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _kugou_singer_img(name):
    """从酷狗抓歌手真人头像：搜歌手 → 歌手详情取 imgurl → 下载（失败返回 None）。"""
    if not name:
        return None, ""
    # 多歌手目录（如「蔡依林、周杰伦」）只取第一位歌手去搜
    names = [name]
    for sep in ("、", "，", "/", "／", "&", "·"):
        if sep in name:
            first = name.split(sep)[0].strip()
            if first and first not in names:
                names.append(first)
            break
    try:
        sid = None
        for q in names:
            r = requests.get("http://mobilecdn.kugou.com/api/v3/search/singer",
                             params={"keyword": q, "page": 1, "pagesize": 10},
                             headers=ARTIST_ICON_UA, timeout=(3, 6))
            singers = (r.json() or {}).get("data") or []
            if isinstance(singers, dict):
                singers = singers.get("lists") or singers.get("info") or []
            # 先精确匹配（忽略大小写），再宽松包含匹配
            for s in singers:
                if (s.get("singername") or "").casefold() == q.casefold():
                    sid = s.get("singerid"); break
            if not sid:
                for s in singers:
                    nm = (s.get("singername") or "").casefold()
                    qq = q.casefold()
                    if nm and (qq in nm or nm in qq):
                        sid = s.get("singerid"); break
            if sid:
                break
        if not sid:
            return None, ""
        r2 = requests.get("http://mobilecdn.kugou.com/api/v3/singer/info",
                          params={"singerid": sid}, headers=ARTIST_ICON_UA, timeout=(3, 6))
        imgurl = ((r2.json() or {}).get("data") or {}).get("imgurl") or ""
        if not imgurl:
            return None, ""
        imgurl = imgurl.replace("{size}", "240")
        r3 = requests.get(imgurl, headers=ARTIST_ICON_UA, timeout=(3, 8))
        data = r3.content
        if not data or len(data) < 1024:
            return None, ""
        mime = (r3.headers.get("Content-Type") or "").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = _image_mime(data) or "image/jpeg"
        return data, mime
    except Exception:
        return None, ""


def _artist_icon_data(rel):
    """歌手图标：优先酷狗歌手真人头像（本地缓存），抓不到退回首字渐变 SVG。"""
    name = os.path.basename((rel or "").replace("\\", "/").rstrip("/"))
    data, mime = _artist_icon_cache_read(name)
    if data:
        return data, mime
    data, mime = _kugou_singer_img(name)
    if data:
        _artist_icon_cache_write(name, data, mime)
        return data, mime
    return _artist_svg_icon(name)


def _save_folder_cover(target_dir, data, mime):
    if not data or not target_dir or not os.path.isdir(target_dir): return False
    ok = False
    # cover.jpg 给网页/其他播放器，Folder.jpg 给 Windows 资源管理器显示文件夹缩略图
    for filename in ("cover.jpg", "Folder.jpg"):
        path = os.path.join(target_dir, filename)
        tmp = path + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            ok = True
        except Exception as e:
            print(f"保存专辑封面失败({filename}): {e}")
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except Exception: pass
    return ok

def _write_audio_tags(audio_path, song, singer, album, cover_data=None, cover_mime="",
                      track="", year="", genre="", lyrics="", album_artist=""):
    """把歌名/歌手/专辑/序号/年份/流派/歌词和封面写入音频标签；失败只打印，不影响音频入库。"""
    if not MUTAGEN_AVAILABLE or not audio_path or not os.path.isfile(audio_path): return False
    ext = os.path.splitext(audio_path)[1].lower()
    def add_id3(tags):
        tags.add(TIT2(encoding=3, text=str(song or "")))
        tags.add(TPE1(encoding=3, text=str(singer or "")))
        if album_artist: tags.add(TPE2(encoding=3, text=str(album_artist)))
        tags.add(TALB(encoding=3, text=str(album or "")))
        if track: tags.add(TRCK(encoding=3, text=str(track)))
        if year: tags.add(TDRC(encoding=3, text=str(year)))
        if genre: tags.add(TCON(encoding=3, text=str(genre)))
        if lyrics: tags.add(USLT(encoding=3, lang="chi", desc="", text=str(lyrics)))
        if cover_data:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=cover_mime or _image_mime(cover_data), type=3, desc="Cover", data=cover_data))
    try:
        if ext == ".mp3":
            try: tags = ID3(audio_path)
            except Exception: tags = ID3()
            add_id3(tags)
            tags.save(audio_path)
            return True
        if ext == ".flac":
            audio = FLAC(audio_path)
            audio["title"] = [str(song or "")]
            audio["artist"] = [str(singer or "")]
            audio["album"] = [str(album or "")]
            if album_artist: audio["albumartist"] = [str(album_artist)]
            if track: audio["tracknumber"] = [str(track)]
            if year: audio["date"] = [str(year)]
            if genre: audio["genre"] = [str(genre)]
            if lyrics: audio["lyrics"] = [str(lyrics)]
            if cover_data:
                audio.clear_pictures()
                pic = Picture()
                pic.type = 3
                pic.mime = cover_mime or _image_mime(cover_data)
                pic.desc = "Cover"
                pic.data = cover_data
                audio.add_picture(pic)
            audio.save()
            return True
        if ext in (".m4a", ".mp4", ".aac"):
            audio = MP4(audio_path)
            audio["\xa9nam"] = [str(song or "")]
            audio["\xa9ART"] = [str(singer or "")]
            audio["\xa9alb"] = [str(album or "")]
            if album_artist: audio["aART"] = [str(album_artist)]
            if track:
                try: audio["trkn"] = [(int(str(track).split("/")[0] or 0), 0)]
                except Exception: pass
            if year: audio["\xa9day"] = [str(year)]
            if genre: audio["\xa9gen"] = [str(genre)]
            if lyrics: audio["\xa9lyr"] = [str(lyrics)]
            if cover_data:
                fmt = MP4Cover.FORMAT_PNG if (cover_mime == "image/png" or _image_mime(cover_data) == "image/png") else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(cover_data, imageformat=fmt)]
            audio.save()
            return True
        if ext == ".wav":
            audio = WAVE(audio_path)
            if audio.tags is None:
                audio.add_tags()
            add_id3(audio.tags)
            audio.save()
            return True
    except Exception as e:
        print(f"写入音频标签失败({os.path.basename(audio_path)}): {e}")
        log_system(f"标签写入失败 | {audio_path} | {e}")
    return False

def _kugou_apply_cover_and_tags(audio_path, target_dir, song, singer, album, cover_url="",
                               track="", year="", genre="", lyrics="", album_artist=""):
    data, mime = _kugou_cover_bytes(cover_url) if cover_url else (None, "")
    if not data:
        # 音源本身可能带内嵌封面，先复用；没有就保持无封面
        data, mime = _audio_cover_data(audio_path)
    if data:
        _save_folder_cover(target_dir, data, mime)
    _write_audio_tags(audio_path, song, singer, album, data, mime,
                      track=track, year=year, genre=genre, lyrics=lyrics,
                      album_artist=album_artist)
    return bool(data)


_LRC_STD_RE = re.compile(r"\[\d{1,3}:\d{2}(?:\.\d+)?\]")
_LRC_KRC_RE = re.compile(r"\[\d+,\d+(?:,\d+)?\]")
_LYRIC_AD_PATTERNS = (
    re.compile(r"本歌词由[^，。；\n]{0,40}(?:提供|制作)", re.I),
    re.compile(r"更多(?:精彩)?歌词[^\n]{0,40}", re.I),
    re.compile(r"(?:QQ音乐|酷狗音乐|酷我音乐|网易云音乐)(?:独家)?(?:\s|$)", re.I),
    re.compile(r"(?:未经许可|版权归)[^\n]{0,40}", re.I),
    re.compile(r"歌词(?:下载|分享|制作)[^\n]{0,40}", re.I),
    re.compile(r"(?:www\.|https?://)[^\s]+", re.I),
)

def _clean_lyric_content(text):
    s = str(text or "")
    for pat in _LYRIC_AD_PATTERNS:
        s = pat.sub("", s)
    for kw in _config_list("lyric_blacklist_keywords", ""):
        if kw and kw.lower() in s.lower():
            s = re.sub(re.escape(kw), "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    low = s.lower().strip(" .-—|")
    if low in ("", "歌词", "暂无歌词", "获取失败", "qq音乐", "酷狗音乐", "酷我音乐", "网易云音乐"):
        return ""
    return s

def _clean_lyric_text(text):
    """清理歌词里的水印/广告/网址，保留时间标签和有效歌词行。"""
    raw = str(text or "").lstrip("\ufeff")
    if not raw: return ""
    out = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped: continue
        m = re.match(r"^((?:\[\d{1,3}:\d{2}(?:\.\d+)?\])+)(.*)$", stripped)
        if m:
            content = _clean_lyric_content(m.group(2))
            if content:
                out.append(m.group(1) + content)
            continue
        # 元信息行保留，但纯广告行丢掉
        if re.match(r"^\[(id|ar|ti|al|by|offset|hash|sign|total|qq|ver)[:：]", stripped, re.I):
            out.append(stripped)
            continue
        if _LRC_KRC_RE.search(stripped):
            # 先交给 KRC 转换，转换后再清理正文
            out.append(stripped)
            continue
        if _clean_lyric_content(stripped):
            out.append(stripped)
    # 去掉完全重复的行，避免某些音源把同一句歌词重复写多次
    deduped = []
    seen = set()
    for line in out:
        if line in seen: continue
        seen.add(line)
        deduped.append(line)
    return "\n".join(deduped)


def _read_text_auto(path):
    """按 UTF-8/GBK/GB18030/Big5/UTF-16 依次尝试，解决老歌词乱码。"""
    if not path or not os.path.isfile(path): return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    return ""


def _lyric_text_valid(text):
    """判断歌词是否可用：既要防止把“获取失败”写成 lrc，也要接受 KRC 逐字行"""
    s = str(text or "").lstrip("\ufeff").strip()
    if not s: return False
    if re.fullmatch(r"(获取失败|暂无歌词|歌词获取失败)[。.!！]?", s, re.I):
        return False
    return bool(_LRC_STD_RE.search(s) or _LRC_KRC_RE.search(s))


def _krc_like_to_lrc(text):
    """把酷狗 KRC 逐字行转成标准 LRC；已经是标准 LRC 的原样返回"""
    raw = str(text or "").lstrip("\ufeff")
    if not _LRC_KRC_RE.search(raw): return _clean_lyric_text(raw)
    out = []
    for line in raw.splitlines():
        m = re.match(r"^\[(\d+),(\d+)(?:,\d+)?\](.*)$", line.strip())
        if m:
            content = _clean_lyric_content(re.sub(r"<[^>]*>", "", m.group(3)))
            if content:
                sec = int(m.group(1)) / 1000.0
                out.append(f"[{int(sec // 60):02d}:{sec % 60:05.2f}]{content}")
            continue
        # 保留标准时间行和 LRC 元信息
        if _LRC_STD_RE.search(line) or re.match(r"^\[(id|ar|ti|al|by|offset|hash|sign|total|qq|ver)[:：]", line.strip(), re.I):
            out.append(line)
    return _clean_lyric_text("\n".join(out)) if out else _clean_lyric_text(raw)


def _kugou_lyric_get_json(session, url, params=None, timeout=8):
    try:
        return session.get(url, params=params, timeout=timeout).json()
    except requests.exceptions.SSLError:
        try:
            return session.get(url, params=params, timeout=timeout, verify=False).json()
        except Exception:
            return {}
    except Exception:
        return {}


def _decode_kugou_krc(raw_bytes):
    """解密酷狗 KRC：krc1 头 + 16 字节循环 XOR + zlib。"""
    try:
        raw = bytes(raw_bytes or b"")
        if not raw.startswith(b"krc1"):
            return ""
        key = bytes([0x40, 0x47, 0x61, 0x77, 0x5e, 0x32, 0x74, 0x47, 0x51, 0x36, 0x31, 0x2d, 0xce, 0xd2, 0x6e, 0x69])
        enc = raw[4:]
        dec = bytes(b ^ key[i % 16] for i, b in enumerate(enc))
        out = None
        for start in (0, 4):
            try:
                out = zlib.decompress(dec[start:])
                break
            except Exception:
                continue
        if out is None:
            return ""
        text = out.decode("utf-8", errors="replace").lstrip("\ufeff")
        return text if _LRC_KRC_RE.search(text) else ""
    except Exception:
        return ""


_LYRIC_VERSION_TAG = "（(加速版|降速版|DJ版|dj版|伴奏|纯音乐|Live|live|现场版|翻唱|女声版|男声版|片段|完整版|重制版)）?"


def _norm_song_title(s):
    """歌名归一化：去标签/括号版本标记/标点空格，用于判断是不是同一首歌"""
    t = re.sub(r"<[^>]*>", "", str(s or ""))
    t = re.sub(r"（[^）]*）|\([^)]*\)|【[^】]*】|\[[^\]]*\]|〔[^〕]*〕", " ", t)
    t = re.sub(_LYRIC_VERSION_TAG, " ", t)
    t = t.lower()
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)
    return t


def _name_sim(a, b):
    if not a or not b: return 0.0
    if a == b: return 1.0
    if a in b or b in a: return 0.88
    return difflib.SequenceMatcher(None, a, b).ratio()


def _lyric_cand_score(c, song, singer, duration):
    """歌词候选匹配度：歌名必须像，歌手尽量对，时长差太多直接丢（0 = 不是这首歌）"""
    if not song and not singer: return 2.0
    cs = _norm_song_title(c.get("song") or c.get("SongName") or "")
    ca = _norm_song_title(c.get("singer") or c.get("SingerName") or "")
    ts = _norm_song_title(song)
    ta = _norm_song_title(singer)
    ss = _name_sim(cs, ts)
    if ts and cs and ss < 0.75:
        return 0.0
    sa = 1.0
    if ta and ca:
        sa = _name_sim(ca, ta)
        if sa < 0.5:
            return 0.0
    try:
        cd = float(c.get("duration") or 0)
    except Exception:
        cd = 0.0
    if cd > 10000: cd = cd / 1000.0
    dd = 0.0
    if duration and cd:
        dd = abs(cd - float(duration))
        if dd > 90:
            return 0.0
    return ss * 10 + sa * 4 + (1.0 if not dd else max(0.0, 1.0 - dd / 90.0))


def _kugou_lrc_from_candidates(session, candidates, song="", singer="", duration=0):
    ranked = []
    for c in (candidates or [])[:12]:
        if not isinstance(c, dict): continue
        score = _lyric_cand_score(c, song, singer, duration)
        if score <= 0: continue
        ranked.append((score, c))
    ranked.sort(key=lambda x: -x[0])
    for _, c in ranked[:8]:
        lrc_id = c.get("id", "")
        accesskey = c.get("accesskey", "")
        if not lrc_id or not accesskey: continue
        for fmt in ("krc", "lrc"):
            data = _kugou_lyric_get_json(
                session, "https://lyrics.kugou.com/download",
                params={"ver": 1, "client": "pc", "id": lrc_id, "accesskey": accesskey, "fmt": fmt},
            )
            content = data.get("content", "") if isinstance(data, dict) else ""
            if not content: continue
            try:
                raw = base64.b64decode(content)
            except Exception:
                continue
            if fmt == "krc":
                text = _decode_kugou_krc(raw)
                if text and _lyric_text_valid(text):
                    return _clean_lyric_text(text)
            else:
                text = raw.decode("utf-8", errors="replace")
                text = _clean_lyric_text(text)
                if _lyric_text_valid(text):
                    return _krc_like_to_lrc(text)
    return ""


def _kugou_lrc_by_hash(session, file_hash, song="", singer="", duration=0):
    if not file_hash: return ""
    data = _kugou_lyric_get_json(
        session, "https://lyrics.kugou.com/search",
        params={"ver": 1, "man": "yes", "client": "pc", "hash": str(file_hash)},
    )
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    return _kugou_lrc_from_candidates(session, candidates, song, singer, duration)


def _kugou_lrc_by_keyword(session, song, singer, duration=0):
    seen = set()
    for kw in (f"{song} {singer}".strip(), str(song or "").strip()):
        if not kw or kw in seen: continue
        seen.add(kw)
        data = _kugou_lyric_get_json(
            session, "https://lyrics.kugou.com/search",
            params={"ver": 1, "man": "yes", "client": "pc", "keyword": kw},
        )
        if not isinstance(data, dict): continue
        for key in ("candidates", "ugccandidates", "ai_candidates"):
            text = _kugou_lrc_from_candidates(session, data.get(key, []), song, singer, duration)
            if text: return text
    return ""


def _kugou_lrc_by_songsearch(session, song, singer, duration=0):
    """按“歌名+歌手”搜酷狗歌曲，再逐首查歌词；只接受同名同歌手的版本"""
    if not song: return ""
    data = _kugou_lyric_get_json(
        session, "https://songsearch.kugou.com/song_search_v2",
        params={"keyword": f"{song} {singer}".strip(), "page": 1, "pagesize": 10, "platform": "Web", "format": "json"},
        timeout=10,
    )
    lists = data.get("data", {}).get("lists", []) if isinstance(data, dict) else []
    target_song = _norm_song_title(re.sub(r"<[^>]*>", "", str(song)))
    target_singer = _norm_song_title(re.sub(r"<[^>]*>", "", str(singer)))
    candidates = []
    for item in (lists or [])[:10]:
        if not isinstance(item, dict): continue
        item_song = _norm_song_title(re.sub(r"<[^>]*>", "", str(item.get("SongName", ""))))
        item_singer = _norm_song_title(re.sub(r"<[^>]*>", "", str(item.get("SingerName", ""))))
        if item_song != target_song: continue
        # 同名不同歌手（同名的另一首歌）坚决不借歌词，否则整段歌词会串
        if target_singer and item_singer != target_singer: continue
        file_hash = str(item.get("FileHash", "") or "").strip()
        if not file_hash: continue
        try: item_dur = int(item.get("Duration", 0) or 0)
        except Exception: item_dur = 0
        if duration and item_dur and abs(item_dur - duration) > 60: continue
        candidates.append((abs(item_dur - duration) if duration and item_dur else 0, file_hash))
    candidates.sort(key=lambda x: x[0])
    for _, file_hash in candidates[:6]:
        text = _kugou_lrc_by_hash(session, file_hash, song, singer, duration)
        if text: return text
    return ""


def _lrclib_fetch(song="", singer="", duration=0):
    """酷狗无歌词时的第二歌词源（LRCLIB），只接受同名同歌手 + 带时间轴的同步歌词。"""
    try:
        params = {"track_name": str(song or "").strip(), "artist_name": str(singer or "").strip()}
        if duration: params["duration"] = int(duration)
        res = requests.get("https://lrclib.net/api/search", params=params,
                           headers={"User-Agent": "ConanMusicServer/1.0"}, timeout=10, verify=False)
        if res.status_code != 200: return ""
        items = res.json() if isinstance(res.json(), list) else []
        ts = _norm_song_title(song)
        ta = _norm_song_title(singer)
        for item in items[:10]:
            if ts and _name_sim(_norm_song_title(item.get("trackName")), ts) < 0.75: continue
            if ta and _name_sim(_norm_song_title(item.get("artistName")), ta) < 0.5: continue
            text = str(item.get("syncedLyrics") or "").strip()
            if text and _LRC_STD_RE.search(text):
                return _clean_lyric_text(text)
    except Exception:
        pass
    return ""


def _kugou_lyric_alt_source(song, singer, duration=0):
    """当前版本没有官方歌词时，找一个同名、确实带官方歌词的其他版本（只做推荐，不自动替换）"""
    if not song: return None
    try:
        session = requests.Session()
        session.headers.update(ONLINE_UA)
        data = _kugou_lyric_get_json(
            session, "https://songsearch.kugou.com/song_search_v2",
            params={"keyword": f"{song} {singer}".strip(), "page": 1, "pagesize": 10,
                    "platform": "Web", "format": "json"},
            timeout=10,
        )
        lists = (data.get("data") or {}).get("lists") or [] if isinstance(data, dict) else []
        ts = _norm_song_title(song)
        ta = _norm_song_title(singer)
        for item in (lists or [])[:10]:
            if not isinstance(item, dict): continue
            isong = _norm_song_title(re.sub(r"<[^>]*>", "", str(item.get("SongName", ""))))
            isinger = _norm_song_title(re.sub(r"<[^>]*>", "", str(item.get("SingerName", ""))))
            if isong != ts or not isinger or isinger == ta: continue
            h = str(item.get("FileHash") or "").strip()
            if not h: continue
            cand = _kugou_lyric_get_json(
                session, "https://lyrics.kugou.com/search",
                params={"ver": 1, "man": "yes", "client": "pc", "hash": h},
                timeout=10,
            )
            cands = (cand or {}).get("candidates") or [] if isinstance(cand, dict) else []
            hit = any(_lyric_cand_score(c, item.get("SongName"), item.get("SingerName"), 0) > 0
                      for c in cands if isinstance(c, dict))
            if not hit: continue
            try: dur = int(item.get("Duration") or 0)
            except Exception: dur = 0
            return {
                "name": re.sub(r"<[^>]*>", "", str(item.get("SongName") or song)).strip(),
                "singer": re.sub(r"<[^>]*>", "", str(item.get("SingerName") or "")).strip(),
                "hash": h,
                "duration": dur,
            }
    except Exception:
        pass
    return None


def fetch_kugou_lyrics(file_hash, song="", singer="", duration=0, allow_search=True):
    """按 hash 取歌词；无歌词时可按歌名/歌手兜底搜索同名版本"""
    cache_key = f"{file_hash}|{song}|{singer}|{int(duration or 0)}"
    cached = KUGOU_LYRIC_CACHE.get(cache_key)
    if cached:
        return cached
    session = requests.Session()
    session.headers.update(KUGOU_LYRIC_HEADERS)
    try:
        text = _kugou_lrc_by_hash(session, file_hash, song, singer, duration)
        if text:
            text = _clean_lyric_text(text)
            KUGOU_LYRIC_CACHE[cache_key] = text
            return text
        if not allow_search: return ""
        text = _kugou_lrc_by_keyword(session, song, singer, duration)
        if text:
            text = _clean_lyric_text(text)
            KUGOU_LYRIC_CACHE[cache_key] = text
            return text
        text = _clean_lyric_text(_kugou_lrc_by_songsearch(session, song, singer, duration))
        if text:
            KUGOU_LYRIC_CACHE[cache_key] = text
            return text
        # 第二歌词源兜底，解决酷狗部分老歌没有歌词
        text = _lrclib_fetch(song, singer, duration)
        if text:
            KUGOU_LYRIC_CACHE[cache_key] = text
        return text
    except Exception as e:
        print(f"获取酷狗歌词失败: {e}")
        try:
            text = _lrclib_fetch(song, singer, duration)
            if text:
                KUGOU_LYRIC_CACHE[cache_key] = text
                return text
        except Exception:
            pass
        return ""


def _kugou_write_lyric(audio_path, file_hash, song="", singer="", allow_search=False, force=False):
    lrc_path = os.path.splitext(audio_path)[0] + ".lrc"
    if os.path.exists(lrc_path) and not force:
        old_text = _read_text_auto(lrc_path)
        if _lyric_text_valid(_clean_lyric_text(old_text)):
            return True
    duration = get_audio_info(audio_path).get("duration", 0)
    text = fetch_kugou_lyrics(file_hash, song, singer, duration, allow_search=allow_search)
    text = _clean_lyric_text(text)
    if not _lyric_text_valid(text):
        # 强制刷新失败时保留原歌词，避免把好歌词删掉
        if not force:
            try:
                if os.path.exists(lrc_path): os.remove(lrc_path)
            except Exception:
                pass
        return False
    # KRC 保留逐字时间轴，普通 LRC 才转成标准格式
    if not _LRC_KRC_RE.search(text):
        text = _krc_like_to_lrc(text)
    try:
        tmp_path = lrc_path + ".part"
        with open(tmp_path, "w", encoding="utf-8") as f: f.write(text)
        os.replace(tmp_path, lrc_path)
        return True
    except Exception as e:
        print(f"保存酷狗歌词失败: {e}")
        return False


def _lrc_shift_text(text, seconds):
    """把 LRC/KRC 时间轴整体偏移 seconds 秒；用于网页端歌词校准。"""
    try:
        delta = float(seconds)
    except Exception:
        delta = 0
    def repl(m):
        mm, ss = int(m.group(1)), float(m.group(2))
        total = max(0.0, mm * 60 + ss + delta)
        return f"[{int(total // 60):02d}:{total % 60:05.2f}]"
    out = []
    for line in str(text or "").splitlines():
        # 酷狗 KRC：行首 [开始毫秒,时长] 整体平移，逐字相对偏移保持不变
        km = re.match(r"^\[(\d+),(\d+)(?:,(\d+))?\](.*)$", line)
        if km:
            start_ms = max(0, int(km.group(1), 10) + int(round(delta * 1000)))
            mid = km.group(2) + (("," + km.group(3)) if km.group(3) is not None else "")
            out.append(f"[{start_ms},{mid}]" + km.group(4))
        else:
            out.append(re.sub(r"\[(\d{1,3}):(\d{2}(?:\.\d+)?)\]", repl, line))
    return "\n".join(out)


def _find_lrc_for_rel(rel):
    full = safe_path(MUSIC_ROOT, rel)
    if not full: return ""
    return os.path.splitext(full)[0] + ".lrc"


def _lrc_shift_line_text(text, line_no, seconds):
    """只平移第 line_no 句歌词（从 0 开始）的起始时间。"""
    try:
        delta_ms = int(round(float(seconds) * 1000))
    except Exception:
        delta_ms = 0
    out, idx = [], 0
    for line in str(text or "").splitlines():
        km = re.match(r"^\[(\d+),(\d+)(?:,(\d+))?\](.*)$", line)
        if km:
            if idx == line_no:
                start_ms = max(0, int(km.group(1), 10) + delta_ms)
                mid = km.group(2) + (("," + km.group(3)) if km.group(3) is not None else "")
                line = f"[{start_ms},{mid}]" + km.group(4)
            idx += 1
        else:
            tm = re.match(r"^((?:\[\d{1,3}:\d{2}(?:\.\d+)?\])+)(.*)$", line)
            if tm and tm.group(2).strip():
                if idx == line_no:
                    def repl(m):
                        total = max(0.0, int(m.group(1)) * 60 + float(m.group(2)) + delta_ms / 1000.0)
                        return f"[{int(total // 60):02d}:{total % 60:05.2f}]"
                    line = re.sub(r"\[(\d{1,3}):(\d{2}(?:\.\d+)?)\]", repl, line)
                idx += 1
        out.append(line)
    return "\n".join(out), idx


def lrclib_offset(rel, seconds):
    """把某首歌的 .lrc 时间轴整体加减 seconds 秒。"""
    lrc_path = _find_lrc_for_rel(rel)
    if not lrc_path or not os.path.isfile(lrc_path):
        return False, "这首歌没有本地歌词文件"
    text = _read_text_auto(lrc_path)
    if not text.strip():
        return False, "歌词文件为空"
    # 首次偏移前保存初始歌词，供“回到初始时间轴”恢复
    try:
        orig_path = lrc_path + ".orig"
        if not os.path.exists(orig_path):
            shutil.copy2(lrc_path, orig_path)
    except Exception as e:
        return False, f"备份初始歌词失败：{e}"
    shifted = _lrc_shift_text(text, seconds)
    try:
        tmp = lrc_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(shifted)
        os.replace(tmp, lrc_path)
        return True, f"时间轴已偏移 {float(seconds):+.1f} 秒"
    except Exception as e:
        return False, f"保存歌词失败：{e}"


def lrclib_offset_line(rel, line_no, seconds):
    """只微调某一句歌词的时间轴，line_no 从 1 开始。"""
    lrc_path = _find_lrc_for_rel(rel)
    if not lrc_path or not os.path.isfile(lrc_path):
        return False, "这首歌没有本地歌词文件"
    text = _read_text_auto(lrc_path)
    if not text.strip():
        return False, "歌词文件为空"
    try:
        line_idx = int(line_no) - 1
    except Exception:
        return False, "行号无效"
    if line_idx < 0:
        return False, "行号从 1 开始"
    # 首次微调前保存初始歌词
    try:
        orig_path = lrc_path + ".orig"
        if not os.path.exists(orig_path):
            shutil.copy2(lrc_path, orig_path)
    except Exception as e:
        return False, f"备份初始歌词失败：{e}"
    shifted, total = _lrc_shift_line_text(text, line_idx, seconds)
    if total <= line_idx:
        return False, f"第 {line_no} 句不存在，当前只有 {total} 句"
    try:
        tmp = lrc_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(shifted)
        os.replace(tmp, lrc_path)
        return True, f"第 {line_no} 句已微调 {float(seconds):+.1f} 秒"
    except Exception as e:
        return False, f"保存歌词失败：{e}"


def lrclib_reset(rel):
    """恢复到首次偏移前保存的初始歌词时间轴。"""
    lrc_path = _find_lrc_for_rel(rel)
    if not lrc_path:
        return False, "路径无效"
    orig_path = lrc_path + ".orig"
    if not os.path.exists(orig_path):
        return False, "没有找到初始时间轴备份；当前歌词没有偏移过"
    text = _read_text_auto(orig_path)
    if not text.strip():
        return False, "初始歌词备份为空"
    try:
        tmp = lrc_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, lrc_path)
        return True, "已恢复到初始时间轴"
    except Exception as e:
        return False, f"恢复歌词失败：{e}"


def lrclib_export(rel, raw=False):
    lrc_path = _find_lrc_for_rel(rel)
    if not lrc_path or not os.path.isfile(lrc_path):
        return None, "没有本地歌词"
    text = _clean_lyric_text(_read_text_auto(lrc_path))
    if not text.strip():
        return None, "歌词为空"
    # 导出给本地播放器使用时，KRC 转成标准 LRC；raw=True 保留原始逐字时间轴
    if not raw and _LRC_KRC_RE.search(text):
        text = _krc_like_to_lrc(text)
    return text, os.path.basename(lrc_path)


def lrclib_import(rel, text):
    """管理员导入外部歌词，覆盖原 lrc；自动转 UTF-8 并清理广告。"""
    lrc_path = _find_lrc_for_rel(rel)
    if not lrc_path:
        return False, "路径无效"
    cleaned = _clean_lyric_text(str(text or ""))
    if not _lyric_text_valid(cleaned):
        return False, "导入歌词内容无效"
    try:
        os.makedirs(os.path.dirname(lrc_path), exist_ok=True)
        tmp = lrc_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(cleaned)
        os.replace(tmp, lrc_path)
        return True, "歌词已导入并清理"
    except Exception as e:
        return False, f"保存失败：{e}"


def embed_lyric_to_audio(rel):
    """把本地 lrc 写入音频标签，脱离 lrc 文件也能显示歌词。"""
    full = safe_path(MUSIC_ROOT, rel)
    if not full or not os.path.isfile(full):
        return False, "音频文件不存在"
    lrc_path = _find_lrc_for_rel(rel)
    if not lrc_path or not os.path.isfile(lrc_path):
        return False, "没有可内嵌的歌词"
    lyrics = _clean_lyric_text(_read_text_auto(lrc_path))
    if not _lyric_text_valid(lyrics):
        return False, "歌词无效"
    title, artist, album = _audio_song_info(full)
    cover_data, cover_mime = _audio_cover_data(full)
    ok = _write_audio_tags(
        full, title or os.path.basename(full), artist or "",
        album or "", cover_data, cover_mime, lyrics=lyrics
    )
    return (True, "已内嵌歌词到音频标签") if ok else (False, "写入音频标签失败")


def lrclib_search(keyword, limit=50):
    """按歌词正文搜索；返回包含关键词的歌曲列表。"""
    needle = str(keyword or "").strip().lower()
    if not needle: return []
    results = []
    for song in scan_all_songs(MUSIC_ROOT):
        rel = song.get("fullRel", "")
        lrc_path = _find_lrc_for_rel(rel)
        if not lrc_path or not os.path.isfile(lrc_path): continue
        text = _read_text_auto(lrc_path)
        if needle not in text.lower(): continue
        snippet = ""
        for line in text.splitlines():
            if needle in line.lower():
                snippet = _clean_lyric_content(re.sub(r"\[[^\]]+\]", "", line))
                break
        results.append({"rel": rel, "name": song.get("name", ""), "snippet": snippet[:160]})
        if len(results) >= limit: break
    return results

    rel = str(rel or "").replace("\\", "/").strip("/")
    full = safe_path(MUSIC_ROOT, rel)
    if full and os.path.isfile(full): return rel, full
    base = os.path.basename(rel)
    if not base: return "", None
    for dirpath, _, files in os.walk(MUSIC_ROOT):
        if base in files:
            found = os.path.join(dirpath, base)
            return os.path.relpath(found, MUSIC_ROOT).replace("\\", "/"), found
    return "", None


def _tag_text(tags, keys):
    if not tags: return ""
    for key in keys:
        try:
            val = tags.get(key)
        except Exception:
            val = None
        if val is None: continue
        if isinstance(val, (list, tuple)):
            val = val[0] if val else ""
        if hasattr(val, "text"):
            val = val.text
            if isinstance(val, (list, tuple)): val = val[0] if val else ""
        text = str(val or "").strip()
        if text: return text
    return ""


def _audio_song_info(audio_path):
    """优先从音频标签取歌名/歌手/专辑，取不到再从文件名和目录推断。"""
    title = singer = album = ""
    if MUTAGEN_AVAILABLE and os.path.isfile(audio_path):
        try:
            audio = MutagenFile(audio_path)
            tags = getattr(audio, "tags", None)
            title = _tag_text(tags, ("title", "TIT2", "\xa9nam"))
            singer = _tag_text(tags, ("artist", "TPE1", "\xa9ART"))
            album = _tag_text(tags, ("album", "TALB", "\xa9alb"))
        except Exception:
            pass
    if not title:
        stem = os.path.splitext(os.path.basename(audio_path))[0]
        artist_folder = os.path.basename(os.path.dirname(os.path.dirname(audio_path)))
        if "-" in stem:
            left, right = stem.rsplit("-", 1)
            left, right = left.strip(), right.strip()
            # 兼容“歌手-歌名”老文件命名；新下载统一是“歌名-歌手”
            if artist_folder and _norm_text(left) == _norm_text(artist_folder):
                title, singer = right, (singer or left)
            else:
                title, singer = left, (singer or right)
        else:
            title = stem.strip()
    folder = os.path.basename(os.path.dirname(audio_path))
    if not album and folder and folder not in ("未知专辑", "未知歌手"):
        album = folder
    if not singer:
        singer = os.path.basename(os.path.dirname(os.path.dirname(audio_path))) or ""
    return title.strip(), singer.strip(), album.strip()


def _filename_issue(name):
    """检测 Windows 下容易出问题的文件名（已存在的文件一般不会有非法字符，主要用于提示）"""
    s = str(name or "")
    if not s: return "文件名为空"
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', s): return "包含 Windows 非法字符"
    if s != s.strip(): return "文件名首尾有空格"
    if re.search(r"\s{2,}", s): return "文件名有连续空格"
    if len(s) > 180: return "文件名过长"
    if s.endswith("."): return "文件名以点结尾"
    return ""


def repair_kugou_lyric(rel, force=False):
    """按已下载记录给某一首歌补歌词，返回 (是否成功, 说明)"""
    original_rel = str(rel or "").replace("\\", "/").strip("/")
    if not original_rel: return False, "缺少歌曲路径"
    rel, audio_path = _kugou_find_audio(original_rel)
    if not audio_path: return False, "音频文件不存在"
    record = {}
    for item in load_kugou_downloads().values():
        if not isinstance(item, dict): continue
        if item.get("rel") == original_rel or item.get("rel") == rel:
            record = item
            break
    song = str(record.get("name", "")).strip()
    singer = str(record.get("singer", "")).strip()
    album = str(record.get("album", "")).strip()
    file_hash = str(record.get("hash", "")).strip()
    if not song:
        song, singer, album = _audio_song_info(audio_path)
    if not song: return False, "无法识别歌名，不能搜索歌词"
    lrc_path = os.path.splitext(audio_path)[0] + ".lrc"
    if os.path.exists(lrc_path) and not force:
        text = _read_text_auto(lrc_path)
        if _lyric_text_valid(_clean_lyric_text(text)): return True, "已有歌词"
    # 强制重新搜索前，保留当前歌词作为“初始时间轴”备份
    if force and os.path.exists(lrc_path):
        try:
            orig_path = lrc_path + ".orig"
            if not os.path.exists(orig_path):
                shutil.copy2(lrc_path, orig_path)
        except Exception:
            pass
    if _kugou_write_lyric(audio_path, file_hash, song, singer, allow_search=True, force=force):
        return True, ("已重新获取酷狗逐字歌词" if force else "已补齐")
    return False, "暂无可匹配歌词"


def _lyric_upgrade_candidates(limit=100):
    """收集仍为普通 LRC、可升级成酷狗 KRC 逐字歌词的已入库歌曲。"""
    rels = []
    seen = set()
    for item in load_kugou_downloads().values():
        if not isinstance(item, dict): continue
        rel = str(item.get("rel", "") or "").replace("\\", "/").strip("/")
        file_hash = str(item.get("hash", "") or "").strip()
        if not rel or not file_hash or rel in seen: continue
        seen.add(rel)
        _, audio_path = _kugou_find_audio(rel)
        if not audio_path: continue
        lrc_path = os.path.splitext(audio_path)[0] + ".lrc"
        if not os.path.isfile(lrc_path): continue
        if _LRC_KRC_RE.search(_read_text_auto(lrc_path)): continue
        rels.append(rel)
        if len(rels) >= max(1, min(200, int(limit or 100))): break
    return rels


def _lyric_upgrade_worker(rels):
    total = len(rels)
    with LYRIC_UPGRADE_LOCK:
        LYRIC_UPGRADE_STATE.update({"running": True, "stop": False, "total": total,
                                    "done": 0, "ok": 0, "failed": 0,
                                    "current": "", "message": "正在升级逐字歌词"})
    for rel in rels:
        with LYRIC_UPGRADE_LOCK:
            if LYRIC_UPGRADE_STATE.get("stop"):
                LYRIC_UPGRADE_STATE["message"] = "已停止"
                break
            LYRIC_UPGRADE_STATE["current"] = rel
        ok, _msg = repair_kugou_lyric(rel, force=True)
        with LYRIC_UPGRADE_LOCK:
            LYRIC_UPGRADE_STATE["done"] += 1
            if ok: LYRIC_UPGRADE_STATE["ok"] += 1
            else: LYRIC_UPGRADE_STATE["failed"] += 1
        time.sleep(0.12)
    with LYRIC_UPGRADE_LOCK:
        LYRIC_UPGRADE_STATE["running"] = False
        LYRIC_UPGRADE_STATE["current"] = ""
        if not LYRIC_UPGRADE_STATE.get("stop"):
            LYRIC_UPGRADE_STATE["message"] = "升级完成"


def start_lyric_upgrade(limit=100):
    with LYRIC_UPGRADE_LOCK:
        if LYRIC_UPGRADE_STATE.get("running"):
            return False, "已有逐字歌词升级任务在运行", dict(LYRIC_UPGRADE_STATE)
        rels = _lyric_upgrade_candidates(limit)
        if not rels:
            return False, "没有需要升级的普通歌词", dict(LYRIC_UPGRADE_STATE)
        LYRIC_UPGRADE_STATE.update({"running": True, "stop": False, "total": len(rels),
                                    "done": 0, "ok": 0, "failed": 0,
                                    "current": "", "message": "正在升级逐字歌词"})
    t = threading.Thread(target=_lyric_upgrade_worker, args=(rels,), daemon=True)
    t.start()
    return True, f"已开始升级 {len(rels)} 首歌词", dict(LYRIC_UPGRADE_STATE)


def _repair_library_cover(rel):
    """只补封面/写标签，不重新下载音频。"""
    rel, audio_path = _kugou_find_audio(rel)
    if not audio_path: return False, "音频文件不存在"
    embedded, mime = extract_cover(audio_path)
    if embedded:
        if not _find_folder_cover(audio_path):
            _save_folder_cover(os.path.dirname(audio_path), embedded, mime)
            return True, "已从内嵌封面生成文件夹封面"
        return True, "已有内嵌封面"
    folder_cover = _find_folder_cover(audio_path)
    if folder_cover:
        data, mime = _read_cover_file(folder_cover)
        if data:
            song, singer, album = _audio_song_info(audio_path)
            _write_audio_tags(audio_path, song, singer, album, data, mime)
            return True, "已把文件夹封面写入音频标签"
    song, singer, album = _audio_song_info(audio_path)
    record_cover = ""
    for record in load_kugou_downloads().values():
        if not isinstance(record, dict): continue
        if record.get("rel") == rel or os.path.basename(str(record.get("rel", ""))) == os.path.basename(rel):
            record_cover = _normalize_kugou_cover_url(record.get("cover", ""))
            if record_cover: break
    cover_url = record_cover or _kugou_find_cover_url(song, singer, album)
    if not cover_url: return False, "酷狗没有找到封面"
    data, mime = _kugou_cover_bytes(cover_url)
    if not data: return False, "封面下载失败"
    _save_folder_cover(os.path.dirname(audio_path), data, mime)
    _write_audio_tags(audio_path, song, singer, album, data, mime)
    return True, "已下载封面并写入标签"


def library_scan():
    """扫描音频/歌词/封面/文件名/损坏文件，不触发封面网络下载。"""
    refresh_all_songs_cache()
    songs = scan_all_songs(MUSIC_ROOT)
    total = len(songs)
    missing_lyric = []
    missing_cover = []
    cover_embed_pending = []
    folder_cover_pending = []
    filename_issues = []
    audio_missing = []
    corrupt_audio = []
    total_bytes = 0
    for record in load_kugou_downloads().values():
        if not isinstance(record, dict): continue
        rel = str(record.get("rel", "") or "").replace("\\", "/").strip("/")
        if not rel: continue
        found_rel, found_path = _kugou_find_audio(rel)
        if not found_path:
            audio_missing.append({
                "rel": rel, "name": str(record.get("name", "") or os.path.basename(rel)),
                "singer": str(record.get("singer", "") or ""),
                "recorded": True,
            })
    for s in songs:
        rel = s.get("fullRel", "")
        full = safe_path(MUSIC_ROOT, rel)
        if not full: continue
        try: total_bytes += os.path.getsize(full)
        except Exception: pass
        # 损坏检测：文件过小或 mutagen 完全无法识别时标记；不误删
        try:
            if os.path.getsize(full) < 1024:
                corrupt_audio.append({"rel": rel, "name": s.get("name", ""), "reason": "文件过小/空文件"})
            elif MUTAGEN_AVAILABLE and MutagenFile(full) is None:
                corrupt_audio.append({"rel": rel, "name": s.get("name", ""), "reason": "音频头损坏或格式无法识别"})
        except Exception as e:
            corrupt_audio.append({"rel": rel, "name": s.get("name", ""), "reason": str(e)[:80]})
        base = os.path.splitext(full)[0]
        lrc_path = base + ".lrc"
        lrc_exists = os.path.exists(lrc_path)
        lrc_raw = _read_text_auto(lrc_path) if lrc_exists else ""
        lrc_clean = _clean_lyric_text(lrc_raw) if lrc_raw else ""
        lrc_ok = bool(lrc_clean.strip()) and _lyric_text_valid(lrc_clean)
        lrc_state = "ok" if lrc_ok else ("missing" if not lrc_exists else ("empty" if not lrc_raw.strip() else "invalid"))
        embedded = bool(extract_cover(full)[0])
        folder_cover = bool(_find_folder_cover(full))
        cover_ok = bool(embedded or folder_cover)
        issue = _filename_issue(os.path.basename(full))
        item = {
            "rel": rel, "name": s.get("name", ""),
            "lrc": bool(lrc_ok), "lrc_state": lrc_state, "cover": cover_ok,
            "embedded_cover": embedded, "folder_cover": folder_cover,
            "filename_issue": issue,
        }
        if not lrc_ok: missing_lyric.append(item)
        if not cover_ok: missing_cover.append(item)
        elif folder_cover and not embedded: cover_embed_pending.append(item)
        elif embedded and not folder_cover: folder_cover_pending.append(item)
        if issue: filename_issues.append(item)
    return {
        "total": total,
        "audio_missing": len(audio_missing),
        "lyric_missing": len(missing_lyric),
        "cover_missing": len(missing_cover),
        "cover_embed_pending": len(cover_embed_pending),
        "folder_cover_pending": len(folder_cover_pending),
        "filename_issues": len(filename_issues),
        "corrupt_audio": len(corrupt_audio),
        "total_size": total_bytes,
        "audio_items": audio_missing[:60],
        "lyric_items": missing_lyric[:120],
        "cover_items": (missing_cover + cover_embed_pending + folder_cover_pending)[:120],
        "filename_items": filename_issues[:60],
        "corrupt_items": corrupt_audio[:60],
    }


def library_repair(kind="both"):
    """批量补歌词/封面；kind 可为 lyric、cover、both。"""
    scan = library_scan()
    results = {"total": scan["total"], "lyric_fixed": 0, "lyric_failed": 0, "cover_fixed": 0, "cover_failed": 0, "details": []}
    if kind in ("lyric", "both"):
        for item in scan.get("lyric_items", []):
            ok, msg = repair_kugou_lyric(item["rel"])
            if ok: results["lyric_fixed"] += 1
            else:
                results["lyric_failed"] += 1
                if len(results["details"]) < 12:
                    results["details"].append({"rel": item["rel"], "kind": "lyric", "msg": msg})
    if kind in ("cover", "both"):
        for item in scan.get("cover_items", []):
            ok, msg = _repair_library_cover(item["rel"])
            if ok: results["cover_fixed"] += 1
            else:
                results["cover_failed"] += 1
                if len(results["details"]) < 12:
                    results["details"].append({"rel": item["rel"], "kind": "cover", "msg": msg})
    refresh_all_songs_cache()
    return results


def library_stats():
    scan = library_scan()
    songs = scan_all_songs(MUSIC_ROOT)
    artists = set()
    albums = set()
    for s in songs:
        rel = s.get("fullRel", "")
        parts = rel.split("/")
        if len(parts) >= 2: artists.add(parts[0])
        if len(parts) >= 3: albums.add(parts[0] + "/" + parts[1])
    return {
        "total": scan.get("total", 0),
        "artists": len(artists),
        "albums": len(albums),
        "size_mb": round(scan.get("total_size", 0) / 1024 / 1024, 1),
        "lyric_missing": scan.get("lyric_missing", 0),
        "cover_missing": scan.get("cover_missing", 0),
        "corrupt_audio": scan.get("corrupt_audio", 0),
        "audio_missing": scan.get("audio_missing", 0),
    }


def library_export_csv():
    scan = library_scan()
    from io import StringIO
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["相对路径", "文件名", "歌词状态", "是否缺歌词", "是否有封面", "文件名问题", "损坏状态"])
    for s in scan_all_songs(MUSIC_ROOT):
        rel = s.get("fullRel", "")
        full = safe_path(MUSIC_ROOT, rel) or ""
        base = os.path.splitext(full)[0] if full else ""
        lrc_path = base + ".lrc" if base else ""
        lrc_exists = bool(lrc_path and os.path.exists(lrc_path))
        lrc_state = "ok" if lrc_exists and _lyric_text_valid(_clean_lyric_text(_read_text_auto(lrc_path))) else ("missing" if not lrc_exists else "invalid")
        cover = bool(full and (_find_folder_cover(full) or extract_cover(full)[0]))
        issue = _filename_issue(os.path.basename(full)) if full else ""
        corrupt = ""
        try:
            if full and os.path.getsize(full) < 1024: corrupt = "文件过小/空文件"
            elif full and MUTAGEN_AVAILABLE and MutagenFile(full) is None: corrupt = "音频损坏"
        except Exception as e: corrupt = str(e)[:60]
        writer.writerow([rel, s.get("name", ""), lrc_state, "是" if lrc_state != "ok" else "否",
                         "是" if cover else "否", issue, corrupt])
    return out.getvalue()


def library_empty_dirs(delete=False):
    """列出/删除音乐库里的空文件夹（只删除真正没有任何文件的目录）。"""
    empty = []
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(MUSIC_ROOT): continue
        try:
            if not os.listdir(dirpath):
                empty.append(os.path.relpath(dirpath, MUSIC_ROOT).replace("\\", "/"))
                if delete:
                    os.rmdir(dirpath)
        except Exception:
            pass
    return empty


def library_duplicate_files(limit=120):
    """按文件大小预筛 + MD5 精确比对，找出改名后的重复歌曲。"""
    by_size = {}
    for s in scan_all_songs(MUSIC_ROOT):
        rel = s.get("fullRel", "")
        full = safe_path(MUSIC_ROOT, rel)
        if not full or not os.path.isfile(full): continue
        try: size = os.path.getsize(full)
        except Exception: continue
        if size < 4096: continue
        by_size.setdefault(size, []).append((rel, full))
    groups = []
    for size, files in by_size.items():
        if len(files) < 2: continue
        hashes = {}
        for rel, full in files:
            try:
                h = hashlib.md5()
                with open(full, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                hashes.setdefault(h.hexdigest(), []).append(rel)
            except Exception:
                pass
        for digest, rels in hashes.items():
            if len(rels) > 1:
                groups.append({"md5": digest, "size": size, "files": rels})
                if len(groups) >= limit: return groups
    return groups


def library_cache_clear():
    KUGOU_ALBUM_CACHE.clear()
    KUGOU_LYRIC_CACHE.clear()
    KUGOU_COVER_CACHE.clear()
    FILE_META_CACHE.clear()
    ALBUM_AUDIT_CACHE.clear()
    return True

ALBUM_AUDIT_LOCK = threading.RLock()
ALBUM_AUDIT_TTL = 900


ALBUM_AUDIT_CACHE = {}
ALBUM_AUDIT_LOCK = threading.RLock()
ALBUM_AUDIT_TTL = 900

def _library_album_groups():
    """按本地“歌手/专辑”目录汇总歌曲，跨同专辑不同歌手目录合并统计。"""
    groups = {}
    for dirpath, _dirnames, filenames in os.walk(MUSIC_ROOT):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in (MUSIC_EXT | {".ape", ".wma"}): continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, MUSIC_ROOT).replace("\\", "/")
            parts = rel.split("/")
            if len(parts) < 3: continue
            artist_dir, album_dir = parts[0], parts[1]
            title, _singer, _tag_album = _audio_song_info(full)
            # 旧文件标签可能是 Track04/Track07 这类占位名，优先用文件名纠正
            base_name = os.path.splitext(fn)[0]
            if (not title or re.match(r"^track\s*\d+$", _norm_text(title))
                    or _norm_text(title) in ("未知歌曲", "unknown", "track")):
                title = base_name.rsplit("-", 1)[0].strip() if "-" in base_name else base_name
            if not title:
                title = base_name
            key = _norm_text(album_dir)
            if not key: continue
            rec = groups.setdefault(key, {
                "album": album_dir, "artists": {}, "local_titles": {},
                "files": [], "latest_mtime": 0
            })
            try: rec["latest_mtime"] = max(rec["latest_mtime"], os.path.getmtime(full))
            except Exception: pass
            rec["artists"][artist_dir] = rec["artists"].get(artist_dir, 0) + 1
            tk = _norm_text(title)
            if tk and tk not in rec["local_titles"]:
                rec["local_titles"][tk] = {"name": title, "rel": rel}
            rec["files"].append(rel)
    return groups


def library_album_scan(force=False, recent_days=0, skip_invalid=False):
    """扫描每个本地专辑，向酷狗核对完整曲目并列出缺歌。只读，不下载。
    recent_days>0 时只检查最近 N 天内有变动的专辑；skip_invalid 时跳过已确认无音源的专辑。
    """
    with ALBUM_AUDIT_LOCK:
        cached = ALBUM_AUDIT_CACHE.get("data")
        if not force and cached and time.time() - cached.get("time", 0) < ALBUM_AUDIT_TTL:
            return cached["data"]
    groups = _library_album_groups()
    albums = []
    skipped_recent = 0
    skipped_invalid = 0
    cutoff = time.time() - max(0, int(recent_days or 0)) * 86400
    for key, rec in groups.items():
        if recent_days and rec.get("latest_mtime", 0) < cutoff:
            skipped_recent += 1
            continue
        artists = rec.get("artists", {})
        primary = max(artists, key=artists.get) if artists else ""
        tracks = _kugou_album_tracks(rec["album"], primary)
        if not tracks:
            if skip_invalid:
                skipped_invalid += 1
                continue
            albums.append({
                "album": rec["album"], "artist": primary, "path": f"{primary}/{rec['album']}",
                "local": len(rec["local_titles"]), "expected": 0, "missing": 0,
                "missing_tracks": [], "status": "unmatched",
                "latest_mtime": rec.get("latest_mtime", 0),
            })
            continue
        expected = {}
        for t in tracks:
            name = str(t.get("name") or "").strip()
            if not name: continue
            expected[_norm_text(name)] = t
        missing_tracks = [
            t for k, t in expected.items() if k not in rec["local_titles"]
        ]
        albums.append({
            "album": rec["album"], "artist": primary, "path": f"{primary}/{rec['album']}",
            "local": len(rec["local_titles"]), "expected": len(expected),
            "missing": len(missing_tracks), "missing_tracks": missing_tracks,
            "status": "complete" if not missing_tracks else "partial",
            "latest_mtime": rec.get("latest_mtime", 0),
        })
    albums.sort(key=lambda x: (-x["missing"], x["album"]))
    partial = [a for a in albums if a["status"] == "partial"]
    data = {
        "albums": albums,
        "album_total": len(albums),
        "album_partial": len(partial),
        "album_unmatched": sum(1 for a in albums if a["status"] == "unmatched"),
        "tracks_missing": sum(a["missing"] for a in partial),
        "skipped_recent": skipped_recent,
        "skipped_invalid": skipped_invalid,
        "checked": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with ALBUM_AUDIT_LOCK:
        ALBUM_AUDIT_CACHE["data"] = {"time": time.time(), "data": data}
    return data


def library_album_repair(album_names=None, skip_blacklist=True, recent_days=0):
    """把选定专辑的缺失曲目加入下载队列；不重下已存在歌曲。"""
    audit = library_album_scan(force=True, recent_days=recent_days, skip_invalid=True)
    wanted = {_norm_text(x) for x in (album_names or []) if str(x).strip()}
    results = {"albums": 0, "tracks_queued": 0, "tasks": [], "skipped": []}
    with KUGOU_TASK_LOCK:
        active_keys = set()
        for t in KUGOU_TASKS.values():
            if t.get("status") in ("queued", "running", "pausing"):
                for it in t.get("items", []):
                    if it.get("auth_key"): active_keys.add(it["auth_key"])
    for album in audit.get("albums", []):
        if album.get("status") != "partial": continue
        if wanted and _norm_text(album.get("album")) not in wanted: continue
        items = []
        for idx, t in enumerate(album.get("missing_tracks", []), start=1):
            name = str(t.get("name") or "").strip()
            singer = str(t.get("singer") or "").strip()
            auth_key = str(t.get("auth_key") or f"{name}-{singer}").strip()
            if not name or auth_key in active_keys: continue
            if skip_blacklist and t.get("blacklisted"): continue
            items.append({
                "song": name, "singer": singer, "album": album.get("album", ""),
                "album_id": str(t.get("album_id") or "").strip(),
                "album_artist": album.get("artist", ""),
                "hash": str(t.get("hash") or "").strip(),
                "cover": _normalize_kugou_cover_url(t.get("cover") or ""),
                "blacklisted": t.get("blacklisted") or "",
                "track": str(t.get("track") or idx),
                "year": str(t.get("year") or ""),
                "genre": str(t.get("genre") or ""),
                "auth_key": auth_key,
            })
            active_keys.add(auth_key)
        if not items:
            continue
        task, reused = create_kugou_download_task(items)
        if not task:
            results["skipped"].append({"album": album.get("album"), "reason": "未完成任务已达上限"})
            continue
        results["albums"] += 1
        results["tracks_queued"] += len(task.get("items", []))
        results["tasks"].append({
            "task_id": task.get("id"), "album": album.get("album"),
            "total": len(task.get("items", [])), "reused": reused,
        })
    return results


def _duration_text_to_seconds(value):
    """把 musicdl 的 00:03:40 / 03:40 / 220 统一换算成秒。"""
    text = str(value or "").strip()
    if not text:
        return 0.0
    m = re.match(r"^\s*(\d{1,3}):(\d{1,2})(?::(\d{1,2}))?", text)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if c is None:
            return float(int(a) * 60 + int(b))
        return float(int(a) * 3600 + int(b) * 60 + int(c))
    try:
        return float(text)
    except Exception:
        return 0.0


def _audio_file_duration(path):
    """读取真实音频时长；读不出来返回 0，绝不拿候选元数据冒充文件时长。"""
    try:
        audio = MutagenFile(path)
        if audio is not None and getattr(audio, "info", None):
            return float(audio.info.length or 0)
    except Exception:
        pass
    return 0.0


def _audio_decode_ok(path):
    """真正解码一遍，拦截“时长正确但音轨损坏”的 FLAC/MP3。
    同一文件（大小/修改时间未变）复用上次结果，避免重复扫描整库时反复吃 CPU。"""
    path = str(path or "")
    if not path or not os.path.isfile(path):
        return False, "文件不存在"
    try:
        st = os.stat(path)
        cache_key = (os.path.abspath(path), int(st.st_size), int(st.st_mtime))
    except Exception:
        cache_key = None
    if cache_key:
        with AUDIO_DECODE_LOCK:
            cached = AUDIO_DECODE_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < AUDIO_DECODE_TTL:
            return cached[1], cached[2]
    ffmpeg = ""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg") or ""
    if not ffmpeg:
        # 没有 ffmpeg 时不做解码检查，避免把正常下载全部误杀
        return True, ""
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=90, text=True, encoding="utf-8", errors="ignore",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        err = (proc.stderr or "").strip()
        low = err.lower()
        bad_markers = (
            "invalid sync code", "invalid frame header", "decode_frame() failed",
            "header missing", "error while decoding", "invalid data found",
            "failed to decode", "corrupt",
        )
        if any(x in low for x in bad_markers):
            result = (False, "音频解码失败（文件损坏）")
        else:
            result = (True, "")
    except subprocess.TimeoutExpired:
        result = (False, "音频解码超时（文件损坏或过大）")
    except Exception as e:
        # 检查器自身异常不阻断下载，交给时长校验兜底
        return True, f"解码检查异常：{e}"
    if cache_key:
        with AUDIO_DECODE_LOCK:
            if len(AUDIO_DECODE_CACHE) > AUDIO_DECODE_MAX:
                for k in list(AUDIO_DECODE_CACHE.keys())[:1000]:
                    AUDIO_DECODE_CACHE.pop(k, None)
            AUDIO_DECODE_CACHE[cache_key] = (time.time(), result[0], result[1])
    return result


def _check_downloaded_audio(downloaded_path, candidate):
    """下载后校验真实时长和实际解码完整性；Kugou 曾把 3:40 的歌返回成 2:31 的错误音频。"""
    path = str(downloaded_path or "")
    if not path or not os.path.isfile(path):
        return False, "下载文件不存在"
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
    if size < 50000:
        return False, f"音频文件过小（{size} 字节）"
    actual = _audio_file_duration(path)
    expected = _duration_text_to_seconds(_get_song_val(candidate, "duration", ""))
    if actual <= 0:
        return False, "音频无法读取或格式异常"
    if actual < 10:
        return False, f"音频过短（{actual:.1f} 秒）"
    if expected > 0:
        # 严格按音源标注时长校验：之前上限放到 1.5 倍，导致 3:14 的歌下成 3:58 的错误版本也能入库
        tolerance = max(8.0, expected * 0.15)
        if actual < expected - tolerance or actual > expected + tolerance:
            return False, f"音源时长不符（文件 {actual:.0f} 秒，标注 {expected:.0f} 秒）"
    decode_ok, decode_reason = _audio_decode_ok(path)
    if not decode_ok:
        return False, decode_reason
    return True, ""


def _candidate_title_ok(candidate, song_name):
    """歌名必须严格一致，搜索兜底只搜歌名时也不能把别的歌当成任务曲目。"""
    n = _norm_text(_get_song_val(candidate, "song_name", ""))
    target_n = _norm_text(song_name)
    return bool(n and target_n and n == target_n)


VERSION_BAD_KWS = (
    "dj", "remix", "混音", "电音", "现场", "live", "伴奏", "串烧", "慢摇",
    "舞曲", "车载", "纯音乐", "改版", "翻唱", "cover", "抖音", "instrumental",
    "剪辑", "片段", "试听", "铃声", "remaster", "mashup", "夜店", "无伴奏",
)


def _candidate_version_bad(candidate):
    """候选是否为 DJ/混音/现场/伴奏/翻唱等非原版；命中返回关键词，原版返回空串。"""
    if not isinstance(candidate, dict):
        return ""
    text = " ".join(
        str(_get_song_val(candidate, k, "") or "")
        for k in ("song_name", "singers", "filename", "file_name", "album_name", "album")
    )
    low = text.lower()
    for kw in VERSION_BAD_KWS:
        k = kw.lower()
        if k in ("dj", "live", "cover"):
            if re.search(r"\b" + re.escape(k) + r"\b", low):
                return kw
        elif k in low:
            return kw
    return ""


def _filter_duration_outliers(candidates):
    """同一歌名的候选里剔除时长明显偏离主流时长的异常版本。

    场景：源站元数据把 DJ/片段版标成和原版一样的歌名（如《成全》3:08 DJ 版），
    单看元数据无法识破，但和同歌名多数候选（4:3x）对比即可发现异常。
    规则：至少 3 个候选带有效时长才启用；时长落在中位数 ±15% 之外的一律剔除；
    无时长信息的候选保留（信息不足不误杀）。"""
    if not candidates or len(candidates) < 3:
        return candidates
    durs = []
    for c in candidates:
        secs = _duration_text_to_seconds(_get_song_val(c, "duration", "") or "")
        durs.append(secs if (secs and secs > 30) else 0.0)
    valid = sorted(d for d in durs if d > 0)
    if len(valid) < 3:
        return candidates
    median = valid[len(valid) // 2]
    if median <= 0:
        return candidates
    lo, hi = median * 0.85, median * 1.15
    kept = [c for c, d in zip(candidates, durs) if (d <= 0 or lo <= d <= hi)]
    return kept if kept else candidates


def _candidate_is_exact(candidate, song_name, singer, album=""):
    """候选是否和任务里的歌名/歌手真正对得上；用于决定值不值得修复音源。"""
    n = _norm_text(_get_song_val(candidate, "song_name", ""))
    target_n = _norm_text(song_name)
    s = _get_song_val(candidate, "singers", "")
    if isinstance(s, (list, tuple)):
        s = "&".join(str(x) for x in s)
    s = _norm_text(s)
    target_s = _norm_text(singer)
    if not n or not target_n or n != target_n:
        return False
    if not target_s:
        return True
    # 完整合唱名单可能和源站写法不同，只要包含核心歌手即可
    return bool(s and (s == target_s or target_s in s or s in target_s))


def _repair_downloaded_audio(path):
    """酷狗/酷我有些 FLAC 能下载、时长也对，但帧已损坏。
    先下载，再用 ffmpeg 忽略错误重编码，修好后正常入库；修不好才丢弃。"""
    path = str(path or "")
    if not path or not os.path.isfile(path):
        return False, "文件不存在"
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".flac", ".mp3"):
        return False, "格式不支持修复"
    ffmpeg = ""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg") or ""
    if not ffmpeg:
        return False, "没有 ffmpeg，无法修复"
    tmp = path + ".repairtmp" + ext
    try:
        if ext == ".flac":
            cmd = [ffmpeg, "-y", "-v", "error", "-err_detect", "ignore_err",
                   "-i", path, "-map", "0:a", "-c:a", "flac",
                   "-compression_level", "4", "-map_metadata", "0", tmp]
        else:
            cmd = [ffmpeg, "-y", "-v", "error", "-err_detect", "ignore_err",
                   "-i", path, "-map", "0:a", "-c:a", "libmp3lame",
                   "-b:a", "320k", "-map_metadata", "0", tmp]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              timeout=240, text=True, encoding="utf-8", errors="ignore",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 50000:
            return False, "重编码失败：" + (proc.stderr or "")[:120]
        ok, reason = _audio_decode_ok(tmp)
        if not ok:
            return False, "修复后仍无法解码：" + reason
        os.replace(tmp, path)
        return True, ""
    except Exception as e:
        return False, f"修复异常：{e}"
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _kugou_search_cached(client, keyword):
    """双源搜索短缓存：同一关键词 10 分钟内复用，减少重复请求和超时。"""
    key = str(keyword or "").strip()
    if not key:
        return {}
    now = time.time()
    with SEARCH_CACHE_LOCK:
        item = SEARCH_CACHE.get(key)
        if item and now - item[0] < SEARCH_CACHE_TTL:
            return item[1]
    result = client.search(keyword=key)
    with SEARCH_CACHE_LOCK:
        if len(SEARCH_CACHE) > SEARCH_CACHE_MAX:
            for k in list(SEARCH_CACHE.keys())[:100]:
                SEARCH_CACHE.pop(k, None)
        SEARCH_CACHE[key] = (now, result)
    return result


def _kugou_search_variants(client, song_name, singer, album):
    """两个源站一起搜；长合唱名单先搜完整，搜不到再退到首位歌手/只搜歌名。
    酷狗/酷我只要有一个返回真候选，就交给后面的候选排序。"""
    queries = []
    full = f"{song_name} {singer}".strip()
    if full:
        queries.append(full)
    first_singer = re.split(r"[、,&，/]|feat\.?", str(singer or ""), maxsplit=1)[0].strip()
    if first_singer and first_singer != singer:
        queries.append(f"{song_name} {first_singer}".strip())
    queries.append(str(song_name or "").strip())
    seen_queries = []
    fallback_candidates = []
    for kw in queries:
        if not kw or kw in seen_queries:
            continue
        seen_queries.append(kw)
        try:
            result = _kugou_search_cached(client, kw)
        except Exception:
            continue
        cands = _kugou_rank_candidates(result, song_name, singer, album)
        if cands:
            if any(_candidate_is_exact(c, song_name, singer, album) for c in cands):
                return cands, kw
            if not fallback_candidates:
                fallback_candidates = cands
    return fallback_candidates, (seen_queries[0] if seen_queries else "")


def _download_kugou_song(client, item):
    """搜索并下载单曲，返回 (是否成功, 服务器相对路径, 说明, 入库记录)"""
    song_name, singer, album = item["song"], item["singer"], item["album"]
    album_artist = str(item.get("album_artist") or "").strip()
    try:
        candidates, used_keyword = _kugou_search_variants(client, song_name, singer, album)
    except Exception as e:
        return False, "", f"搜索失败：{e}", None
    if not candidates:
        return False, "", "酷狗、酷我均无音源，已跳过", None
    # 音质档偏好：把符合档位的候选排到最前，找不到时仍按原顺序尝试（不因此少下歌）
    candidates = _apply_quality_preference(candidates, item.get("quality"))
    # 时长一致性过滤：同名候选里剔除时长明显异常的 DJ/片段版本（元数据自洽骗过逐曲校验的场合）
    _title_ok = [c for c in candidates if _candidate_title_ok(c, song_name)]
    if _title_ok:
        _kept = _filter_duration_outliers(_title_ok)
        _dropped = [c for c in _title_ok if c not in _kept]
        if _dropped and _kept:
            for c in _dropped:
                log_system(
                    f"候选时长过滤 | {song_name} - {singer} | "
                    f"时长 {_get_song_val(c, 'duration', '')} 偏离主流版本，已跳过"
                )
            candidates = _kept

    last_error = ""
    for cand in candidates[:6]:
        if not _candidate_title_ok(cand, song_name):
            last_error = "候选歌名不匹配"
            continue
        # 过滤 DJ/混音/现场/伴奏/翻唱等非原版（用户主动搜 DJ 等关键词时不拦截）
        bad_kw = _candidate_version_bad(cand)
        if bad_kw and not _candidate_version_bad({"song_name": song_name, "singers": singer, "album": album}):
            last_error = f"候选为{bad_kw}版本，已跳过"
            log_system(f"候选版本过滤 | {song_name} - {singer} | {bad_kw}版跳过")
            continue
        try:
            downloaded = client.download(song_infos=[cand])
        except Exception as e:
            last_error = str(e)
            continue
        if not downloaded:
            last_error = "音源下载失败"
            continue
        candidate_obj = downloaded[0]
        candidate_path = str(_get_song_val(candidate_obj, "save_path", "") or "")
        if not candidate_path and isinstance(candidate_obj, (str, bytes, os.PathLike)):
            candidate_path = str(candidate_obj)
        valid, reason = _check_downloaded_audio(candidate_path, cand)
        if not valid:
            last_error = reason
            log_system(f"音源校验失败 | {song_name} - {singer} | {reason}")
            # 时长不符说明候选本身就是另一首/错误版本，重编码救不回来，直接换下一个候选
            if "时长不符" in reason:
                valid = False
            # 真候选但帧损坏：不直接丢弃，先尝试原格式修复；修好就入库
            elif _candidate_is_exact(cand, song_name, singer, album):
                repaired, repair_msg = _repair_downloaded_audio(candidate_path)
                if repaired:
                    valid, reason = _check_downloaded_audio(candidate_path, cand)
                    if valid:
                        log_system(f"音源已修复入库 | {song_name} - {singer}")
                    else:
                        last_error = f"修复后校验失败：{reason}"
                else:
                    last_error = repair_msg
            if not valid:
                try:
                    if candidate_path and os.path.isfile(candidate_path):
                        os.remove(candidate_path)
                except Exception:
                    pass
                continue
        with KUGOU_FILE_LOCK:
            target_dir = _kugou_target_dir(singer, album, album_artist, str(item.get("year", "") or ""))
            if not target_dir:
                last_error = "目标目录无效"
                continue
            audio_path = _kugou_move_download(downloaded[0], target_dir, song_name, singer)
        if not audio_path:
            last_error = "音频文件移动失败"
            continue
        rel = os.path.relpath(audio_path, MUSIC_ROOT).replace("\\", "/")
        cover_url = item.get("cover", "") or _kugou_find_cover_url(song_name, singer, album)
        # 歌词先落盘，再把歌词/曲目序号/年份写入标签
        has_lyric = _kugou_write_lyric(
            audio_path, item.get("hash", ""), song_name, singer,
            allow_search=True,
        )
        lyrics = ""
        try:
            lrc_path = os.path.splitext(audio_path)[0] + ".lrc"
            if os.path.isfile(lrc_path):
                lyrics = _clean_lyric_text(_read_text_auto(lrc_path))
        except Exception:
            pass
        track = str(item.get("track") or "").strip()
        year = str(item.get("year") or _get_song_val(cand, "publish_date", "") or "")[:4]
        genre = str(item.get("genre") or _get_song_val(cand, "genre", "") or "").strip()
        has_cover = _kugou_apply_cover_and_tags(
            audio_path, target_dir, song_name, singer, album, cover_url,
            track=track, year=year, genre=genre, lyrics=lyrics,
            album_artist=album_artist,
        )
        record = {
            "rel": rel, "name": song_name, "singer": singer, "album": album,
            "album_artist": album_artist, "track": track, "year": year, "genre": genre,
            "hash": str(item.get("hash", "") or ""), "has_lyric": bool(has_lyric),
            "has_cover": bool(has_cover), "cover": cover_url,
            "quality": str(item.get("quality") or "auto"),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return True, rel, "", record
    log_system(f"下载失败 | {song_name} - {singer} | {last_error or '下载失败'}")
    if (last_error.startswith("音源校验失败") or last_error.startswith("修复") or
            last_error.startswith("修复后校验失败") or last_error.startswith("候选歌名不匹配")):
        return False, "", "酷狗、酷我均无音源（候选音源损坏或不匹配），已跳过", None
    return False, "", last_error or "下载失败", None


FAIL_TYPE_LABELS = {
    "no_source": "音源不可用/无版权",
    "timeout": "下载超时",
    "lyric": "歌词失败",
    "disk": "磁盘空间不足",
    "permission": "权限错误",
    "network": "网络错误",
    "engine": "下载引擎错误",
    "other": "其他错误",
}


def classify_failure(message):
    text = str(message or "").lower()
    if any(k in text for k in ("无版权", "音源", "找不到", "没有可下载", "下架", "not found", "无结果")):
        return "no_source"
    if any(k in text for k in ("超时", "timeout", "timed out")):
        return "timeout"
    if any(k in text for k in ("歌词", "lyric")):
        return "lyric"
    if any(k in text for k in ("磁盘", "no space", "空间不足", "disk full")):
        return "disk"
    if any(k in text for k in ("权限", "permission", "denied", "拒绝访问")):
        return "permission"
    if any(k in text for k in ("网络", "connection", "ssl", "proxy", "连接")):
        return "network"
    if any(k in text for k in ("musicdl", "引擎", "engine")):
        return "engine"
    return "other"


def _disk_usage_ok():
    try:
        free_gb = shutil.disk_usage(MUSIC_ROOT).free / (1024 ** 3)
    except Exception:
        return True, 0.0
    stop_gb = float(SYSTEM_CONFIG.get("disk_stop_gb", 5) or 5)
    return free_gb > stop_gb, free_gb


def _public_kugou_task(task, items_limit=None):
    with KUGOU_TASK_LOCK:
        if not task: return None
        status = task.get("status", "queued")
        all_items = [{
            "song": it.get("song", ""), "singer": it.get("singer", ""), "album": it.get("album", ""),
            "auth_key": it.get("auth_key", ""), "status": it.get("status", "waiting"),
            "rel": it.get("rel", ""), "msg": it.get("msg", ""),
            "cover": it.get("cover", ""), "blacklisted": it.get("blacklisted", ""),
            "fail_type": it.get("fail_type", ""),
            "fail_label": FAIL_TYPE_LABELS.get(it.get("fail_type", ""), ""),
        } for it in task.get("items", [])]
        items = all_items[:items_limit] if items_limit else all_items
        resumable = status in ("paused", "error", "cancelled") and any(
            it["status"] in ("waiting", "failed", "cancelled", "running") for it in all_items
        )
        total_n = len(all_items)
        done_n = sum(1 for it in all_items if it["status"] == "done")
        failed_n = sum(1 for it in all_items if it["status"] == "failed")
        skipped_n = sum(1 for it in all_items if it["status"] == "skipped")
        status_labels = {
            "queued": "排队等待", "running": "下载中", "pausing": "暂停中",
            "paused": "暂停等待", "done": "已完成", "error": "失败",
            "cancelled": "已取消", "cancelling": "取消中",
        }
        eta_seconds = None
        if status == "running" and done_n > 0:
            elapsed = max(0.1, float(task.get("updated", time.time()) - task.get("created", time.time())))
            remain = max(0, total_n - done_n - failed_n - skipped_n)
            eta_seconds = int(min(86400, (elapsed / done_n) * remain)) if remain else 0
        elif status == "queued":
            eta_seconds = None
        return {
            "id": task["id"], "status": status, "status_label": status_labels.get(status, status),
            "progress": round(task.get("progress", 0), 1),
            "message": task.get("message", ""), "done_count": task.get("done_count", 0),
            "failed_count": task.get("failed_count", 0),
            "skipped_count": task.get("skipped_count", 0), "total": total_n,
            "created": task.get("created", 0), "updated": task.get("updated", 0),
            "priority": task.get("priority", 0), "eta_seconds": eta_seconds,
            "items": items,
            "pauseable": status in ("queued", "running", "pausing"),
            "resumable": bool(resumable),
            "cancellable": status in ("queued", "running", "pausing", "paused"),
            "retryable": failed_n > 0 or status in ("error", "cancelled"),
            "engine": MUSICDL_AVAILABLE,
        }


def _serialize_kugou_task(task):
    return {
        "id": task["id"], "status": task.get("status", "queued"), "progress": task.get("progress", 0),
        "message": task.get("message", ""), "done_count": task.get("done_count", 0),
        "failed_count": task.get("failed_count", 0),
        "skipped_count": task.get("skipped_count", 0), "created": task.get("created", 0),
        "updated": task.get("updated", 0), "priority": task.get("priority", 0),
        "pause_requested": bool(task.get("pause_requested")),
        "cancel_requested": bool(task.get("cancel_requested")),
        "items": [{
            "song": it.get("song", ""), "singer": it.get("singer", ""), "album": it.get("album", ""),
            "album_id": it.get("album_id", ""),
            "album_artist": it.get("album_artist", ""),
            "hash": it.get("hash", ""), "auth_key": it.get("auth_key", ""),
            "status": it.get("status", "waiting"), "rel": it.get("rel", ""), "msg": it.get("msg", ""),
            "cover": it.get("cover", ""), "blacklisted": it.get("blacklisted", ""),
            "track": it.get("track", ""), "year": it.get("year", ""), "genre": it.get("genre", ""),
            "fail_type": it.get("fail_type", ""),
        } for it in task.get("items", [])],
    }


def _persist_kugou_tasks():
    """把任务队列原子写入 download_task.json，服务器重启后可恢复"""
    with KUGOU_PERSIST_LOCK:
        with KUGOU_TASK_LOCK:
            tasks = sorted(KUGOU_TASKS.values(), key=lambda x: x.get("created", 0))
            if len(tasks) > KUGOU_MAX_TASKS:
                overflow = len(tasks) - KUGOU_MAX_TASKS
                removable = [t for t in tasks if t.get("status") in ("done", "error", "cancelled")]
                for t in removable[:overflow]:
                    KUGOU_TASKS.pop(t["id"], None)
                tasks = sorted(KUGOU_TASKS.values(), key=lambda x: x.get("created", 0))
            data = {"version": 3, "saved": time.time(), "tasks": [_serialize_kugou_task(t) for t in tasks]}
        ok = _atomic_write_json(KUGOU_TASK_FILE, data)
        if ok and SYSTEM_CONFIG.get("task_backup", True):
            try:
                shutil.copyfile(KUGOU_TASK_FILE, KUGOU_TASK_BACKUP_FILE)
            except Exception as e:
                print(f"备份下载任务失败: {e}")
        return ok


def _recompute_task_counts(task):
    items = task.get("items", [])
    task["done_count"] = sum(1 for it in items if it.get("status") == "done")
    task["failed_count"] = sum(1 for it in items if it.get("status") == "failed")
    task["skipped_count"] = sum(1 for it in items if it.get("status") == "skipped")
    total = len(items) or 1
    processed = sum(1 for it in items if it.get("status") in ("done", "failed", "skipped", "cancelled"))
    task["progress"] = 100.0 if task.get("status") in ("done", "error", "cancelled") else (processed / total) * 100.0
    task["updated"] = time.time()


def _set_task_failed(task, message, item_message="下载失败"):
    with KUGOU_TASK_LOCK:
        task["status"] = "error"
        task["message"] = message
        task["progress"] = 100
        for it in task.get("items", []):
            if it.get("status") in ("waiting", "running"):
                it["status"] = "failed"
                it["msg"] = item_message
                it["fail_type"] = classify_failure(item_message)
        _recompute_task_counts(task)
    _persist_kugou_tasks()


def _find_active_task_for(auth_key):
    for t in KUGOU_TASKS.values():
        if t.get("status") in ("queued", "running", "pausing"):
            for it in t.get("items", []):
                if it.get("auth_key") == auth_key: return t
    return None


def create_kugou_download_task(items, priority=0):
    """items: [{song, singer, album, hash}]，返回 (任务, 是否复用)；任务过多时返回 (None, False)"""
    with KUGOU_TASK_LOCK:
        if len(items) == 1:
            existing = _find_active_task_for(items[0]["auth_key"])
            if existing: return existing, True
        active_count = sum(1 for t in KUGOU_TASKS.values() if t.get("status") in ("queued", "running", "pausing"))
        if active_count >= KUGOU_MAX_ACTIVE_TASKS:
            return None, False
        tid = f"kg{int(time.time() * 1000)}{secrets.token_hex(2)}"
        task = {
            "id": tid, "status": "queued", "progress": 0, "message": "已加入下载队列",
            "done_count": 0, "failed_count": 0, "created": time.time(), "updated": time.time(),
            "priority": int(priority or 0), "pause_requested": False, "cancel_requested": False,
            "items": [{
                "song": it["song"], "singer": it["singer"], "album": it["album"],
                "album_id": it.get("album_id", ""),
                "album_artist": it.get("album_artist", ""),
                "hash": it.get("hash", ""), "cover": it.get("cover", ""),
                "blacklisted": it.get("blacklisted", ""), "auth_key": it["auth_key"],
                "track": it.get("track", ""), "year": it.get("year", ""), "genre": it.get("genre", ""),
                "status": "waiting", "rel": "", "msg": "",
            } for it in items],
        }
        KUGOU_TASKS[tid] = task
    _persist_kugou_tasks()
    ensure_kugou_scheduler()
    return task, False


def ensure_kugou_scheduler():
    global KUGOU_SCHEDULER_STARTED
    with KUGOU_SCHEDULER_LOCK:
        if KUGOU_SCHEDULER_STARTED: return
        KUGOU_SCHEDULER_STARTED = True
    threading.Thread(target=_kugou_scheduler_loop, name="KugouDownloadScheduler", daemon=True).start()


def set_kugou_task_priority(task_id, priority=1):
    with KUGOU_TASK_LOCK:
        task = KUGOU_TASKS.get(task_id)
        if not task: return False
        task["priority"] = int(priority or 0)
        if task.get("status") == "queued":
            task["message"] = "⭐ 已优先，排队等待中"
        task["updated"] = time.time()
    _persist_kugou_tasks()
    return True


def batch_kugou_task_action(task_ids, action):
    results = []
    for tid in task_ids:
        with KUGOU_TASK_LOCK:
            task = KUGOU_TASKS.get(tid)
        if not task:
            results.append({"id": tid, "ok": False, "msg": "任务不存在"})
            continue
        try:
            if action == "pause": _pause_kugou_task(task)
            elif action == "resume": _resume_kugou_task(task)
            elif action == "cancel": _cancel_kugou_task(task)
            elif action == "priority": set_kugou_task_priority(tid, 1)
            else:
                results.append({"id": tid, "ok": False, "msg": "未知操作"}); continue
            results.append({"id": tid, "ok": True})
        except Exception as e:
            results.append({"id": tid, "ok": False, "msg": str(e)})
    return results


def retry_kugou_task(task_id, mode="failed"):
    """把失败/取消/未完成的曲目重新建一个任务，不重下已成功的歌。"""
    with KUGOU_TASK_LOCK:
        task = KUGOU_TASKS.get(task_id)
        if not task: return None, "任务不存在"
        items = list(task.get("items", []))
        if mode == "item":
            selected = [it for it in items if it.get("_retry_selected")]
        else:
            selected = [it for it in items if it.get("status") in ("failed", "waiting", "cancelled")]
    selected = [it for it in selected if it.get("song") and it.get("auth_key")]
    for it in selected:
        it.pop("_retry_selected", None)
    if not selected: return None, "没有需要重试的曲目"
    new_items = [{
        "song": it.get("song", ""), "singer": it.get("singer", ""),
        "album": it.get("album", ""), "album_id": it.get("album_id", ""),
        "album_artist": it.get("album_artist", ""), "hash": it.get("hash", ""),
        "cover": it.get("cover", ""), "blacklisted": it.get("blacklisted", ""),
        "auth_key": it.get("auth_key", ""), "track": it.get("track", 0),
        "year": it.get("year", ""), "genre": it.get("genre", ""),
    } for it in selected]
    new_task, reused = create_kugou_download_task(new_items, priority=1)
    if not new_task:
        return None, "未完成任务已达上限，请先处理队列中的任务"
    return new_task, ("已在队列中" if reused else "已重新加入队列")


def _kugou_scheduler_loop():
    """最多同时跑 KUGOU_MAX_CONCURRENT 个任务，其余按创建时间排队"""
    while True:
        try:
            if MUSICDL_AVAILABLE:
                candidates = []
                with KUGOU_TASK_LOCK:
                    for tid, th in list(KUGOU_ACTIVE_THREADS.items()):
                        if not th.is_alive(): KUGOU_ACTIVE_THREADS.pop(tid, None)
                    slots = KUGOU_MAX_CONCURRENT - len(KUGOU_ACTIVE_THREADS)
                    if slots > 0:
                        queued = sorted(
                            [t for t in KUGOU_TASKS.values()
                             if t.get("status") == "queued" and t["id"] not in KUGOU_ACTIVE_THREADS],
                            key=lambda x: (-int(x.get("priority", 0) or 0), x.get("created", 0))
                        )
                        candidates = [t["id"] for t in queued[:slots]]
                for tid in candidates:
                    _start_kugou_task_thread(tid)
        except Exception as e:
            print(f"酷狗下载调度器异常: {e}")
        time.sleep(1.0)


def _start_kugou_task_thread(tid):
    with KUGOU_TASK_LOCK:
        task = KUGOU_TASKS.get(tid)
        if not task or task.get("status") != "queued": return False
        th = KUGOU_ACTIVE_THREADS.get(tid)
        if th and th.is_alive(): return False
        task["status"] = "running"
        task["message"] = "开始下载..."
        task["updated"] = time.time()
        thread = threading.Thread(target=_kugou_task_worker, args=(tid,), name=f"KugouTask-{tid}", daemon=True)
        KUGOU_ACTIVE_THREADS[tid] = thread
    _persist_kugou_tasks()
    thread.start()
    return True


def _kugou_task_worker(tid):
    task = KUGOU_TASKS.get(tid)
    try:
        if task: _run_kugou_task(task)
    except Exception as e:
        if task: _set_task_failed(task, f"下载线程异常：{e}", str(e))
    finally:
        with KUGOU_TASK_LOCK:
            if task:
                status = task.get("status")
                if status == "pausing":
                    task["status"] = "paused"
                    task["message"] = "⏸ 已暂停"
                    task["pause_requested"] = False
                    _recompute_task_counts(task)
                elif status == "cancelling":
                    task["status"] = "cancelled"
                    task["message"] = "⏹ 已取消"
                    task["cancel_requested"] = False
                    for it in task.get("items", []):
                        if it.get("status") in ("waiting", "running"):
                            it["status"] = "cancelled"
                            it["msg"] = "已取消"
                    _recompute_task_counts(task)
                elif status == "running":
                    task["status"] = "error"
                    task["message"] = "❌ 下载线程意外结束"
                    task["progress"] = 100
            KUGOU_ACTIVE_THREADS.pop(tid, None)
        _persist_kugou_tasks()


def _run_kugou_task(task):
    with KUGOU_TASK_LOCK:
        if task.get("status") in ("cancelling", "cancelled", "pausing", "paused", "done", "error"):
            return
        task["status"] = "running"
        task["updated"] = time.time()
        _recompute_task_counts(task)
    _persist_kugou_tasks()

    if not MUSICDL_AVAILABLE:
        _set_task_failed(task, "服务器未安装 musicdl，请运行 pip install musicdl 后重启服务", "musicdl 未安装")
        return
    disk_ok, free_gb = _disk_usage_ok()
    if not disk_ok:
        _set_task_failed(task, f"磁盘剩余空间不足（{free_gb:.1f} GB），已停止下载", "磁盘空间不足")
        return

    task_temp = os.path.join(KUGOU_TEMP_DIR, _safe_segment(task["id"], "task"))
    try:
        if os.path.isdir(task_temp):
            shutil.rmtree(task_temp, ignore_errors=True)
        os.makedirs(task_temp, exist_ok=True)
        cfg = {
            src: {
                "search_size_per_source": KUGOU_DL_SEARCH_SIZE,
                "work_dir": task_temp,
                "disable_print": True,
            }
            for src in KUGOU_DL_SOURCES
        }
        client = musicdl.MusicClient(
            music_sources=KUGOU_DL_SOURCES,
            init_music_clients_cfg=cfg,
            requests_overrides={src: {"timeout": KUGOU_TASK_TIMEOUT} for src in KUGOU_DL_SOURCES},
        )
    except Exception as e:
        _set_task_failed(task, f"下载引擎初始化失败：{e}", "musicdl 初始化失败")
        shutil.rmtree(task_temp, ignore_errors=True)
        return

    total = len(task.get("items", []))
    try:
        for idx, item in enumerate(task.get("items", [])):
            with KUGOU_TASK_LOCK:
                if task.get("cancel_requested") or task.get("status") == "cancelling":
                    for it in task.get("items", []):
                        if it.get("status") in ("waiting", "running"):
                            it["status"] = "cancelled"
                            it["msg"] = "已取消"
                    task["status"] = "cancelling"
                    task["message"] = "正在取消..."
                    task["pause_requested"] = False
                    break
                if task.get("pause_requested") or task.get("status") == "pausing":
                    task["status"] = "pausing"
                    task["message"] = "正在暂停，当前歌曲完成后暂停..."
                    task["cancel_requested"] = False
                    break
                if item.get("status") in ("done", "failed", "skipped", "cancelled"):
                    continue
                item["status"] = "running"
                item["msg"] = ""
                task["message"] = f"正在下载 {idx + 1}/{total}：{item['song']} - {item['singer']}"
                task["progress"] = (idx / max(1, total)) * 100
                task["updated"] = time.time()
            _persist_kugou_tasks()

            try:
                ok, rel, msg, record = _download_kugou_song(client, item)
            except Exception as e:
                ok, rel, msg, record = False, "", f"异常：{e}", None
                log_system(f"下载异常 | {item.get('song')} - {item.get('singer')} | {traceback.format_exc()}")

            with KUGOU_TASK_LOCK:
                if ok:
                    item["status"] = "done"
                    item["rel"] = rel
                    item["msg"] = ""
                    task["done_count"] = sum(1 for it in task["items"] if it.get("status") == "done")
                else:
                    if msg.startswith("酷狗、酷我均无音源"):
                        item["status"] = "skipped"
                        item["msg"] = "酷狗、酷我均无音源，已跳过"
                        item["fail_type"] = "no_source"
                        task["skipped_count"] = sum(1 for it in task["items"] if it.get("status") == "skipped")
                    else:
                        item["status"] = "failed"
                        item["msg"] = msg
                        item["fail_type"] = classify_failure(msg)
                        task["failed_count"] = sum(1 for it in task["items"] if it.get("status") == "failed")
                processed = sum(1 for it in task["items"] if it.get("status") in ("done", "failed", "skipped", "cancelled"))
                task["progress"] = (processed / max(1, total)) * 100
                task["updated"] = time.time()
            if ok and record:
                with KUGOU_DATA_LOCK:
                    downloads = load_kugou_downloads()
                    downloads[item["auth_key"]] = record
                    save_kugou_downloads(downloads)
                    kgs = load_kugou_auth()
                    kgs[item["auth_key"]] = True
                    save_kugou_auth(kgs)
            _persist_kugou_tasks()
    finally:
        shutil.rmtree(task_temp, ignore_errors=True)

    with KUGOU_TASK_LOCK:
        status = task.get("status")
        if status in ("cancelling", "cancelled"):
            task["status"] = "cancelled"
            task["cancel_requested"] = False
            for it in task["items"]:
                if it.get("status") in ("waiting", "running"):
                    it["status"] = "cancelled"
                    it["msg"] = "已取消"
            _recompute_task_counts(task)
            task["progress"] = 100
            task["message"] = f"⏹ 已取消：成功 {task['done_count']} / {total} 首"
        elif status in ("pausing", "paused") and any(
                it.get("status") in ("waiting", "running") for it in task["items"]):
            task["status"] = "paused"
            task["pause_requested"] = False
            _recompute_task_counts(task)
            task["message"] = f"⏸ 已暂停：成功 {task['done_count']} / {total} 首" + (
                f"，失败 {task['failed_count']} 首" if task["failed_count"] else "")
        else:
            # 全部歌曲都已处理（包括“暂停请求”恰好发生在最后一首之后）
            _recompute_task_counts(task)
            task["progress"] = 100
            if task["failed_count"] == 0:
                task["status"] = "done"
                if task["done_count"] == 0 and task.get("skipped_count", 0):
                    task["message"] = f"⏭ 已跳过 {task['skipped_count']} 首：酷狗、酷我均无音源"
                else:
                    task["message"] = f"✅ 已入库 {task['done_count']} 首歌曲" + (
                        f"，跳过 {task['skipped_count']} 首无音源" if task.get("skipped_count", 0) else "")
            elif task["done_count"] == 0:
                task["status"] = "error"
                first_error = next((it["msg"] for it in task["items"] if it.get("status") == "failed" and it.get("msg")), "")
                task["message"] = f"❌ 全部下载失败（{task['failed_count']} 首）" + (f"：{first_error}" if first_error else "") + (
                    f"，跳过 {task['skipped_count']} 首无音源" if task.get("skipped_count", 0) else "")
            else:
                task["status"] = "done"
                task["message"] = f"✅ 已入库 {task['done_count']} 首，失败 {task['failed_count']} 首" + (
                    f"，跳过 {task['skipped_count']} 首无音源" if task.get("skipped_count", 0) else "")
    task["updated"] = time.time()
    _persist_kugou_tasks()
    try:
        with KUGOU_REFRESH_LOCK:
            refresh_all_songs_cache()
    except Exception:
        pass
    _persist_kugou_tasks()


def _pause_kugou_task(task):
    with KUGOU_TASK_LOCK:
        status = task.get("status")
        if status == "queued":
            task["status"] = "paused"
            task["message"] = "⏸ 已暂停（尚未开始）"
        elif status in ("running", "pausing"):
            task["status"] = "pausing"
            task["pause_requested"] = True
            task["message"] = "正在暂停，当前歌曲完成后暂停..."
        else:
            return False
        task["updated"] = time.time()
        _recompute_task_counts(task)
    _persist_kugou_tasks()
    return True


def _resume_kugou_task(task):
    with KUGOU_TASK_LOCK:
        if task.get("status") == "done": return False
        for it in task.get("items", []):
            if it.get("status") in ("failed", "cancelled", "running"):
                it["status"] = "waiting"
                it["msg"] = ""
        if not any(it.get("status") == "waiting" for it in task.get("items", [])):
            return False
        task["status"] = "queued"
        task["pause_requested"] = False
        task["cancel_requested"] = False
        task["message"] = "已恢复，等待下载..."
        task["updated"] = time.time()
        _recompute_task_counts(task)
    _persist_kugou_tasks()
    ensure_kugou_scheduler()
    return True


def _cancel_kugou_task(task):
    with KUGOU_TASK_LOCK:
        status = task.get("status")
        if status in ("done", "error", "cancelled"): return False
        task["cancel_requested"] = True
        if status in ("queued", "paused"):
            task["status"] = "cancelled"
            task["cancel_requested"] = False
            for it in task.get("items", []):
                if it.get("status") in ("waiting", "running"):
                    it["status"] = "cancelled"
                    it["msg"] = "已取消"
            task["message"] = "⏹ 已取消"
            task["progress"] = 100
        elif status in ("running", "pausing"):
            task["status"] = "cancelling"
            task["message"] = "正在取消，当前歌曲完成后停止..."
        else:
            return False
        task["updated"] = time.time()
        _recompute_task_counts(task)
    _persist_kugou_tasks()
    return True


def _kugou_create_rate_ok(ip):
    now = time.time()
    with KUGOU_TASK_LOCK:
        arr = [t for t in KUGOU_CREATE_HITS.get(ip, []) if now - t < KUGOU_CREATE_WINDOW]
        if len(arr) >= KUGOU_CREATE_MAX:
            KUGOU_CREATE_HITS[ip] = arr
            return False
        arr.append(now)
        KUGOU_CREATE_HITS[ip] = arr
        if len(KUGOU_CREATE_HITS) > 500:
            for k, v in list(KUGOU_CREATE_HITS.items()):
                if not v or now - v[-1] > KUGOU_CREATE_WINDOW * 3:
                    KUGOU_CREATE_HITS.pop(k, None)
    return True


def restore_kugou_tasks():
    """服务器启动时读取 download_task.json，恢复未完成任务"""
    raw = None
    # Windows 下进程可能在 os.replace 瞬间被结束，正式文件暂时缺失但 .tmp 是完整的
    for task_file in (KUGOU_TASK_FILE, KUGOU_TASK_FILE + ".tmp"):
        if not os.path.exists(task_file): continue
        try:
            with open(task_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            break
        except Exception as e:
            print(f"读取下载任务记录失败({task_file}): {e}")
    if raw is None: return 0
    raw_tasks = raw.get("tasks", []) if isinstance(raw, dict) else raw
    if not isinstance(raw_tasks, list): return 0
    restored = 0
    with KUGOU_TASK_LOCK:
        for r in raw_tasks[-KUGOU_MAX_TASKS:]:
            if not isinstance(r, dict): continue
            tid = str(r.get("id", "")).strip()
            if not tid or tid in KUGOU_TASKS: continue
            items = []
            for it in r.get("items", []) or []:
                if not isinstance(it, dict): continue
                song = str(it.get("song", "")).strip()
                if not song: continue
                singer = str(it.get("singer", "")).strip()
                status = str(it.get("status", "waiting"))
                if status not in ("waiting", "running", "done", "failed", "skipped", "cancelled"): status = "waiting"
                if status == "running": status = "waiting"
                items.append({
                    "song": song, "singer": singer, "album": str(it.get("album", "")).strip(),
                    "album_id": str(it.get("album_id", "")).strip(),
                    "album_artist": str(it.get("album_artist", "")).strip(),
                    "hash": str(it.get("hash", "")).strip(), "cover": _normalize_kugou_cover_url(it.get("cover", "")),
                    "blacklisted": str(it.get("blacklisted", "") or ""), "auth_key": str(it.get("auth_key", "")).strip() or f"{song}-{singer}",
                    "track": str(it.get("track", "") or ""), "year": str(it.get("year", "") or ""),
                    "genre": str(it.get("genre", "") or ""),
                    "status": status, "rel": str(it.get("rel", "")), "msg": str(it.get("msg", "")),
                    "fail_type": str(it.get("fail_type", "") or (classify_failure(it.get("msg", "")) if status == "failed" else "")),
                })
            if not items: continue
            status = str(r.get("status", "queued"))
            message = str(r.get("message", ""))
            if status == "running":
                status = "queued"; message = "服务重启，已恢复任务，等待继续下载..."
            elif status == "pausing":
                status = "paused"; message = "服务重启，任务已暂停"
            elif status == "cancelling":
                status = "cancelled"; message = "服务重启，任务已取消"
                for it in items:
                    if it["status"] in ("waiting", "running"):
                        it["status"] = "cancelled"; it["msg"] = "已取消"
            elif status not in ("queued", "paused", "done", "error", "cancelled"):
                status = "queued"
            task = {
                "id": tid, "status": status, "progress": float(r.get("progress", 0) or 0),
                "message": message or "已恢复下载任务", "done_count": 0, "failed_count": 0,
                "created": float(r.get("created", time.time()) or time.time()), "updated": time.time(),
                "priority": int(r.get("priority", 0) or 0),
                "pause_requested": False, "cancel_requested": False, "items": items,
            }
            _recompute_task_counts(task)
            KUGOU_TASKS[tid] = task
            restored += 1
    _persist_kugou_tasks()
    return restored


def _normalize_kugou_items(raw):
    """把前端提交的单曲/多曲请求整理成下载任务条目"""
    if isinstance(raw, dict): raw = [raw]
    if not isinstance(raw, list): return []
    items = []
    for s in raw[:300]:
        if not isinstance(s, dict): continue
        name = str(s.get("song") or s.get("name") or "").strip()
        singer = str(s.get("singer") or "").strip()
        if not name: continue
        album = str(s.get("album") or s.get("album_name") or "").strip()
        album_id = str(s.get("album_id") or s.get("albumid") or "").strip()
        album_artist = str(s.get("album_artist") or "").strip()
        quality = str(s.get("quality") or SYSTEM_CONFIG.get("download_quality", "auto") or "auto")
        if quality not in KUGOU_QUALITY_OPTIONS:
            quality = "auto"
        items.append({
            "song": name, "singer": singer, "album": album, "album_id": album_id,
            "album_artist": album_artist,
            "hash": str(s.get("hash") or "").strip(),
            "cover": _normalize_kugou_cover_url(s.get("cover") or ""),
            "blacklisted": _kugou_blacklist_reason(name, album),
            "track": str(s.get("track") or s.get("track_no") or s.get("tracknumber") or "").strip(),
            "year": str(s.get("year") or s.get("publish_date") or s.get("publish_time") or "").strip()[:4],
            "genre": str(s.get("genre") or s.get("style") or "").strip(),
            "auth_key": f"{name}-{singer}",
            "quality": quality,
        })
    return items


# ====================== 用户系统 ======================
TOKEN_FILE = os.path.join(DATA_DIR, "tokens.json")

def load_token_map():
    """兼容旧版“token -> 用户名”格式，新格式带过期时间。"""
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            result = {}
            migrated = False
            for token, value in (raw or {}).items():
                if isinstance(value, dict):
                    username = str(value.get("username", "")).strip()
                    expires = float(value.get("expires", 0) or 0)
                    if username:
                        result[token] = {"username": username, "expires": expires or (now + TOKEN_TTL_SECONDS)}
                else:
                    username = str(value or "").strip()
                    if username:
                        result[token] = {"username": username, "expires": now + TOKEN_TTL_SECONDS}
                        migrated = True
            if migrated:
                try:
                    with open(TOKEN_FILE, "w", encoding="utf-8") as f: json.dump(result, f, ensure_ascii=False)
                except Exception: pass
            return result
    except Exception: pass
    return {}

def save_token_map():
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f: json.dump(TOKEN_MAP, f, ensure_ascii=False)
    except Exception: pass

TOKEN_MAP = load_token_map()

def _token_username(token):
    entry = TOKEN_MAP.get(token)
    if isinstance(entry, dict): return str(entry.get("username", "") or "")
    return str(entry or "")

def _token_expired(token):
    entry = TOKEN_MAP.get(token)
    if not isinstance(entry, dict): return False
    expires = float(entry.get("expires", 0) or 0)
    return bool(expires and time.time() > expires)

def _init_admin():
    pwd_hash, pwd_salt = hash_password(str(SYSTEM_CONFIG.get("admin_password") or "20140829czy"))
    return {"username": ADMIN_USERNAME, "pwd_hash": pwd_hash, "pwd_salt": pwd_salt, "role": "admin", "enable": True, "points": 0, "vip_expire": 0, "download_expire": 0, "vip_history": [], "favs": [], "purchased": [], "avatar": ""}

DEFAULT_ADMIN = _init_admin()

def load_users():
    if not os.path.exists(USER_DB_FILE):
        save_users([DEFAULT_ADMIN]); return [DEFAULT_ADMIN]
    try:
        with open(USER_DB_FILE, "r", encoding="utf-8") as f: users = json.load(f)
        need_save = False
        for u in users:
            if "points" not in u: u["points"] = 0; need_save = True
            if "vip_expire" not in u: u["vip_expire"] = 0; need_save = True
            if "download_expire" not in u: u["download_expire"] = 0; need_save = True
            if "vip_history" not in u: u["vip_history"] = []; need_save = True
            if "favs" not in u: u["favs"] = []; need_save = True
            if "purchased" not in u: u["purchased"] = []; need_save = True
            if "avatar" not in u: u["avatar"] = ""; need_save = True
        admin_user = next((u for u in users if u.get("username") == ADMIN_USERNAME), None)
        if not admin_user:
            users.append(DEFAULT_ADMIN); need_save = True
        elif SYSTEM_CONFIG.get("sync_admin_password", True):
            cfg_pwd = str(SYSTEM_CONFIG.get("admin_password") or "20140829czy")
            pwd_hash, _ = hash_password(cfg_pwd, admin_user.get("pwd_salt", ""))
            if admin_user.get("pwd_hash") != pwd_hash:
                pwd_hash, pwd_salt = hash_password(cfg_pwd)
                admin_user["pwd_hash"] = pwd_hash
                admin_user["pwd_salt"] = pwd_salt
                admin_user["role"] = "admin"
                need_save = True
        if need_save: save_users(users)
        return users
    except Exception: return [DEFAULT_ADMIN]

def save_users(users):
    with open(USER_DB_FILE, "w", encoding="utf-8") as f: json.dump(users, f, ensure_ascii=False, indent=2)

def get_user_by_name(username):
    for u in load_users():
        if u["username"] == username: return u
    return None

def update_user(username, **kw):
    users = load_users()
    for u in users:
        if u["username"] == username: u.update(kw); break
    save_users(users)

def add_points(username, delta, reason=""):
    u = get_user_by_name(username)
    if not u: return
    update_user(username, points=u.get("points", 0) + delta)
    logs = load_point_logs()
    logs.append({"id": int(time.time()*1000), "username": username, "delta": delta, "reason": reason or "调整", "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    if len(logs) > 5000: logs = logs[-5000:]
    save_point_logs(logs)

def is_vip(user): return (user or {}).get("vip_expire", 0) > time.time()
def is_download_vip(user): return (user or {}).get("download_expire", 0) > time.time()
def vip_left_seconds(user): return max(0, int((user or {}).get("vip_expire", 0) - time.time()))
def dl_left_seconds(user): return max(0, int((user or {}).get("download_expire", 0) - time.time()))

def grant_vip(username, days, type="vip"):
    u = get_user_by_name(username)
    if not u: return
    now = time.time()
    if type == "vip":
        base = max(now, u.get("vip_expire", 0))
        update_user(username, vip_expire=base + days * 86400)
    elif type == "download":
        base = max(now, u.get("download_expire", 0))
        update_user(username, download_expire=base + days * 86400)

def create_user(username, password):
    if get_user_by_name(username): return False, "用户名已存在"
    pwd_hash, pwd_salt = hash_password(password)
    new_user = {"username": username, "pwd_hash": pwd_hash, "pwd_salt": pwd_salt, "role": "user", "enable": True, "points": 0, "vip_expire": 0, "download_expire": 0, "vip_history": [], "favs": [], "purchased": [], "avatar": ""}
    users = load_users(); users.append(new_user); save_users(users)
    return True, "注册成功"

def check_user_pwd(username, password):
    u = get_user_by_name(username)
    if not u: return False, "用户不存在"
    if not u["enable"]: return False, "账号已被禁用"
    if "pwd_salt" in u and u["pwd_salt"]:
        pwd_hash, _ = hash_password(password, u["pwd_salt"])
        if pwd_hash == u["pwd_hash"]: return True, u["role"]
    else:
        if u["pwd_hash"] == hashlib.md5(password.encode("utf-8")).hexdigest():
            new_hash, new_salt = hash_password(password)
            update_user(username, pwd_hash=new_hash, pwd_salt=new_salt)
            return True, u["role"]
    return False, "密码错误"


def _login_lock_state(username, ip):
    key = f"{username}|{ip}"
    with LOGIN_LOCK:
        rec = LOGIN_FAILS.get(key) or {}
        remain = float(rec.get("until", 0) or 0) - time.time()
        if remain > 0:
            return True, int(remain) + 1
        return False, 0


def _record_login_fail(username, ip):
    key = f"{username}|{ip}"
    max_fail = _config_int("login_max_fail", 5, 1, 50)
    lock_min = _config_int("login_lock_minutes", 10, 1, 1440)
    with LOGIN_LOCK:
        rec = LOGIN_FAILS.setdefault(key, {"fails": 0, "until": 0})
        rec["fails"] = int(rec.get("fails", 0)) + 1
        if rec["fails"] >= max_fail:
            rec["until"] = time.time() + lock_min * 60
        return rec


def _clear_login_fail(username, ip):
    with LOGIN_LOCK:
        LOGIN_FAILS.pop(f"{username}|{ip}", None)


def _admin_ip_allowed(ip):
    allowed = _config_list("admin_ips", "")
    if not allowed: return True
    return str(ip or "") in allowed

def user_from_token(token):
    if not token or token not in TOKEN_MAP: return None
    if _token_expired(token):
        TOKEN_MAP.pop(token, None); save_token_map()
        return None
    username = _token_username(token)
    if not username:
        TOKEN_MAP.pop(token, None); save_token_map()
        return None
    u = get_user_by_name(username)
    if not u or not u["enable"]: return None
    return u

# ====================== 数据读写 ======================
def load_feedbacks():
    if not os.path.exists(FEEDBACK_FILE): return []
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f: data = json.load(f)
    except Exception: return []
    now = time.time(); cutoff = now - 7 * 86400; changed = False; kept = []
    for f in data:
        if f.get("status") == "approved" and f.get("id", 0) / 1000 < cutoff: changed = True; continue
        kept.append(f)
    if changed: save_feedbacks(kept)
    return kept

def save_feedbacks(data):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def load_withdraws():
    if not os.path.exists(WITHDRAW_FILE): return []
    try:
        with open(WITHDRAW_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def save_withdraws(data):
    with open(WITHDRAW_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def load_messages():
    if not os.path.exists(MESSAGE_FILE): return []
    try:
        with open(MESSAGE_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def save_messages(data):
    with open(MESSAGE_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def add_message(to_user, content, from_user):
    msgs = load_messages()
    msgs.append({"id": int(time.time()*1000), "to": to_user, "from": from_user, "content": content, "read": False, "replies": [], "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_messages(msgs)

def load_vip_pays():
    if not os.path.exists(VIP_PAY_FILE): return []
    try:
        with open(VIP_PAY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def save_vip_pays(data):
    with open(VIP_PAY_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def load_point_logs():
    if not os.path.exists(POINT_LOG_FILE): return []
    try:
        with open(POINT_LOG_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def save_point_logs(data):
    with open(POINT_LOG_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ====================== 目录 ======================
ALL_SONG_CACHE = None
CACHE_EXPIRE = 30
LAST_SCAN_TIME = 0
SONG_INDEX_CACHE = None
SONG_INDEX_TIME = 0

def read_dir_content(base_path, rel_path, page=1, page_size=100):
    full_dir = safe_path(base_path, rel_path)
    if not full_dir or not os.path.isdir(full_dir): return [], [], 0
    
    copyrights = load_copyrights()
    folders, songs = [], []
    try:
        for entry in os.scandir(full_dir):
            if entry.is_dir(): folders.append(entry.name)
            elif entry.is_file():
                name, ext = os.path.splitext(entry.name)
                if ext.lower() in MUSIC_EXT:
                    rel = f"{rel_path}/{entry.name}".lstrip("/") if rel_path else entry.name
                    cp = copyrights.get(rel, "")
                    info = get_audio_info(entry.path)
                    songs.append({"name": name, "file": entry.name, "copyright": cp, "bitrate": info.get("bitrate",0), "ext": ext.upper().replace(".","")})
    except Exception as e:
        print(f"读取目录失败: {rel_path}, 错误: {e}"); return [], [], 0
        
    folders.sort(); songs.sort(key=lambda x: x["name"].lower())
    total = len(songs)
    start = (page - 1) * page_size; end = start + page_size
    return folders, songs[start:end], total

def scan_all_songs(base_path):
    global ALL_SONG_CACHE, LAST_SCAN_TIME
    now = time.time()
    if ALL_SONG_CACHE is not None and (now - LAST_SCAN_TIME) < CACHE_EXPIRE: return ALL_SONG_CACHE
    copyrights = load_copyrights()
    result = []
    try:
        for dirpath, _, files in os.walk(base_path):
            for f in files:
                name, ext = os.path.splitext(f)
                if ext.lower() in MUSIC_EXT:
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, base_path).replace("\\", "/")
                    cp = copyrights.get(rel, "")
                    info = get_audio_info(full)
                    result.append({"name": name, "fullRel": rel, "copyright": cp, "bitrate": info.get("bitrate",0), "ext": ext.upper().replace(".","")})
    except Exception as e: print(f"扫描歌曲失败: {e}")
    result.sort(key=lambda x: x["name"].lower())
    ALL_SONG_CACHE = result; LAST_SCAN_TIME = now
    return result

def scan_song_index(base_path):
    """轻量全库索引：只读目录和扩展名，不解析音频头，专供搜索使用。"""
    global SONG_INDEX_CACHE, SONG_INDEX_TIME
    now = time.time()
    if SONG_INDEX_CACHE is not None and (now - SONG_INDEX_TIME) < CACHE_EXPIRE:
        return SONG_INDEX_CACHE
    copyrights = load_copyrights()
    result = []
    try:
        for dirpath, _, files in os.walk(base_path):
            for f in files:
                name, ext = os.path.splitext(f)
                if ext.lower() in MUSIC_EXT:
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, base_path).replace("\\", "/")
                    result.append({
                        "name": name, "fullRel": rel, "copyright": copyrights.get(rel, ""),
                        "bitrate": 0, "ext": ext.upper().replace(".", ""),
                    })
    except Exception as e:
        print(f"扫描歌曲索引失败: {e}")
    result.sort(key=lambda x: x["name"].lower())
    SONG_INDEX_CACHE = result
    SONG_INDEX_TIME = now
    return result


def refresh_all_songs_cache():
    global ALL_SONG_CACHE, LAST_SCAN_TIME, SONG_INDEX_CACHE, SONG_INDEX_TIME
    ALL_SONG_CACHE = None; LAST_SCAN_TIME = 0
    SONG_INDEX_CACHE = None; SONG_INDEX_TIME = 0
    return scan_song_index(MUSIC_ROOT)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        if ip and not ip.startswith("127."): return ip
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127."): return ip
    except Exception:
        pass
    return "127.0.0.1"

local_ip = get_local_ip()

_TUNNEL_CACHE = {"url": None, "ts": 0.0}

def _current_tunnel_url():
    """从本机 cpolar 客户端(127.0.0.1:4040)读取当前隧道公网地址；失败返回 None。"""
    global _TUNNEL_CACHE
    now = time.time()
    if now - _TUNNEL_CACHE.get("ts", 0.0) < 30:
        return _TUNNEL_CACHE.get("url")
    url = None
    try:
        r = requests.get("http://127.0.0.1:4040/", timeout=2.5)
        hits = re.findall(r'https?://[a-zA-Z0-9]+\.r\d+\.cpolar\.top', r.text or "")
        if hits:
            prefer = [h for h in hits if h.startswith("https://")]
            url = (prefer or hits)[0]
    except Exception:
        url = None
    _TUNNEL_CACHE = {"url": url, "ts": now}
    return url


class MyHandler(http.server.SimpleHTTPRequestHandler):
    def _safe_write(self, func):
        try: func()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError): pass

    def _client_ip(self):
        try: return self.client_address[0]
        except Exception: return "?"

    def _request_token(self, body):
        """支持请求体 token 或 X-Conan-Token 请求头，避免只靠可复制的 URL 触发操作"""
        tok = body.get("token", "") if isinstance(body, dict) else ""
        if not tok: tok = self.headers.get("X-Conan-Token", "")
        return str(tok or "").strip()

    def _admin_user(self, body):
        u = user_from_token(self._request_token(body))
        return u if u and u.get("role") == "admin" else None

    def _rate_ok(self):
        ip = self._client_ip(); now = time.time()
        arr = IP_HITS.get(ip, []); arr = [t for t in arr if now - t < IP_RATE_WINDOW]
        arr.append(now); IP_HITS[ip] = arr
        return len(arr) <= IP_RATE_MAX

    def _is_media_get(self):
        """播放/封面/歌词/头像属于连续静态读取，不能和页面接口共用 25 次/秒的限流；
        否则一次加载几十张专辑封面后，紧接着的 /file/ 音频请求会 429，
        前端 player.onerror 就误报“播放失败，文件可能损坏”。"""
        p = self.path or ""
        return (
            p.startswith("/file/") or p.startswith("/lyric/") or
            p.startswith("/avatars/") or p.startswith("/api/cover") or
            p.startswith("/api/folder_cover") or p.startswith("/api/artist_icon") or
            p.startswith("/api/font_file") or p.startswith("/api/online/stream")
        )

    def _audio_rate_ok(self, ip):
        now = time.time(); arr = AUDIO_HITS.get(ip, [])
        arr = [t for t in arr if now - t < AUDIO_RATE_WINDOW]
        if len(arr) > AUDIO_RATE_MAX: return False
        arr.append(now); AUDIO_HITS[ip] = arr; return True

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._safe_write(lambda: self._raw_json(body))

    def _raw_json(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Conan-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def read_post_body(self):
        try: length = int(self.headers.get("content-length", 0))
        except (TypeError, ValueError): length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try: return json.loads(raw.decode("utf-8"))
        except Exception: return {}

    def _send_html(self, html):
        self._safe_write(lambda: self._raw_html(html))

    def _raw_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html;charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers(); self.wfile.write(html.encode("utf-8"))

    def do_HEAD(self):
        if not self._is_media_get() and not self._rate_ok(): self.send_response(429); self.end_headers(); return
        if self.path.startswith("/file/"):
            parsed = urlparse(self.path)
            rel = unquote(parsed.path[len("/file/"):])
            full_path = safe_path(MUSIC_ROOT, rel)
            if not full_path or not os.path.isfile(full_path): self.send_response(404); self.end_headers(); return
            meta = _cache_file_meta(full_path)
            if not meta: self.send_response(404); self.end_headers(); return
            ext = os.path.splitext(full_path)[1].lower()
            mime = {".mp3":"audio/mpeg",".flac":"audio/flac",".m4a":"audio/mp4",".wav":"audio/wav",".ogg":"audio/ogg",".aac":"audio/aac"}.get(ext, "audio/mpeg")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(meta["size"]))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return
        self.send_response(405); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Conan-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._is_media_get() and not self._rate_ok(): self.send_response(429); self.end_headers(); return
        if self.path == "/login": self._send_html(LOGIN_HTML)
        elif self.path == "/register": self._send_html(REGISTER_HTML)
        elif self.path == "/admin": self._send_html(ADMIN_HTML)
        elif self.path == "/": self._send_html(INDEX_HTML)
        elif self.path.startswith("/api/list"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            rel_path = (qs.get("path") or [""])[0]
            page = int((qs.get("page") or ["1"])[0])
            page_size = int((qs.get("page_size") or ["100"])[0])
            folders, songs, total = read_dir_content(MUSIC_ROOT, rel_path, page, page_size)
            self.send_json({"folders": folders, "songs": songs, "total": total, "page": page, "page_size": page_size})
        elif self.path.startswith("/api/all"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            kw = (qs.get("kw") or [""])[0].lower().strip()
            all_songs = scan_song_index(MUSIC_ROOT)
            terms = [t for t in re.split(r"\s+", kw) if t]
            if terms:
                matched = []
                for s in all_songs:
                    hay = (s.get("name", "") + " " + s.get("fullRel", "")).lower()
                    if all(t in hay for t in terms):
                        matched.append(s)
            else:
                matched = all_songs
            
            online_results = []
            if kw and not matched:
                online_albums = search_kugou_grouped(qs.get("kw")[0])
                for album in online_albums:
                    for song in album["songs"]:
                        online_results.append({
                            "name": song["name"], "singer": song["singer"], "album": album["album"],
                            "auth_key": song["auth_key"], "is_auth": song["is_auth"],
                            "hash": song.get("hash", ""), "local_rel": song.get("local_rel", ""),
                            "cover": song.get("cover", ""), "blacklisted": song.get("blacklisted", ""),
                            "duration": int(song.get("duration") or 0),
                            "filesize": int(song.get("filesize") or 0)
                        })
            self.send_json({"data": matched, "total": len(matched), "online": online_results, "engine": MUSICDL_AVAILABLE})
        elif self.path.startswith("/api/kugou/tree"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            kw = (qs.get("kw") or [""])[0].strip()
            if not kw: self.send_json({"ok": False, "data": [], "engine": MUSICDL_AVAILABLE}); return
            albums = search_kugou_grouped(kw)
            self.send_json({"ok": True, "data": albums, "engine": MUSICDL_AVAILABLE})
        elif self.path.startswith("/api/kugou/mv/search"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            kw = (qs.get("kw") or [""])[0].strip()
            if not kw:
                self.send_json({"ok": True, "data": []}); return
            self.send_json({"ok": True, "data": search_kugou_mv(kw)})
        elif self.path.startswith("/api/kugou/mv/play"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            mv_hash = (qs.get("hash") or [""])[0].strip()
            info = kugou_mv_play_urls(mv_hash) if mv_hash else None
            if not info:
                self.send_json({"ok": False, "msg": "MV 播放源获取失败，可换一个搜索结果试试"}); return
            self.send_json({"ok": True, "data": info})
        elif self.path.startswith("/api/singer/list"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            lang = (qs.get("lang") or ["all"])[0].strip()
            sex = (qs.get("sex") or ["all"])[0].strip()
            letter = (qs.get("letter") or [""])[0].strip()
            kw = (qs.get("kw") or [""])[0].strip()
            try:
                limit = int((qs.get("limit") or ["200"])[0] or 200)
            except Exception:
                limit = 200
            data = singer_list(lang, sex, letter, kw, limit)
            self.send_json({"ok": True, "data": data,
                            "langs": [{"k": k, "n": n} for k, n in SINGER_LANGS],
                            "sexes": [{"k": k, "n": n} for k, n in SINGER_SEXES]})
        elif self.path.startswith("/api/online/search/kind"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            kind = (qs.get("kind") or ["song"])[0].strip().lower()
            kw = (qs.get("kw") or [""])[0].strip()
            try:
                limit = int((qs.get("limit") or ["20"])[0] or 20)
            except Exception:
                limit = 20
            try:
                page = int((qs.get("page") or ["1"])[0] or 1)
            except Exception:
                page = 1
            items = kugou_search_by_kind(kind, kw, limit, page)
            self.send_json({"ok": True, "kind": kind, "data": items,
                            "page": page, "page_size": max(5, min(50, limit)),
                            "total": ONLINE_SEARCH_TOTAL.get(f"{kind}|{kw}|{limit}|{page}", 0)})
        elif self.path.startswith("/api/online/album"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            album_id = (qs.get("id") or [""])[0].strip()
            album_name = (qs.get("name") or [""])[0].strip()
            singer = (qs.get("singer") or [""])[0].strip()
            self.send_json({"ok": True, "data": online_album_songs(album_id, album_name, singer),
                            "album": album_name})
        elif self.path.startswith("/api/online/search"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            kw = (qs.get("kw") or [""])[0].strip()
            limit = int((qs.get("limit") or ["20"])[0] or 20)
            try:
                page = int((qs.get("page") or ["1"])[0] or 1)
            except Exception:
                page = 1
            self.send_json({"ok": True, "data": kugou_online_search(kw, limit, page),
                            "page": page, "page_size": max(5, min(50, limit)),
                            "total": ONLINE_SEARCH_TOTAL.get(f"{kw}|{limit}|{page}", 0)})
        elif self.path.startswith("/api/online/hot"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            try:
                limit = int((qs.get("limit") or ["30"])[0] or 30)
            except Exception:
                limit = 30
            self.send_json({"ok": True, "data": kugou_online_hot(limit)})
        elif self.path.startswith("/api/online/rank"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            key = (qs.get("key") or ["top"])[0].strip()
            limit = int((qs.get("limit") or ["30"])[0] or 30)
            try:
                rank_page = int((qs.get("page") or ["1"])[0] or 1)
            except Exception:
                rank_page = 1
            self.send_json({"ok": True, "data": kugou_online_recommend(key, limit, rank_page),
                            "page": rank_page, "page_size": max(5, min(50, limit)),
                            "total": ONLINE_SEARCH_TOTAL.get(
                                f"rank|{ONLINE_RANK_MAP.get(key, ONLINE_RANK_MAP['top'])[0]}|{limit}|{rank_page}", 0)})
        elif self.path.startswith("/api/online/play"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            file_hash = (qs.get("hash") or [""])[0].strip()
            name = (qs.get("name") or [""])[0].strip()
            singer = (qs.get("singer") or [""])[0].strip()
            try:
                duration = int(float((qs.get("duration") or ["0"])[0] or 0))
            except Exception:
                duration = 0
            info = online_play_payload(file_hash, name, singer, duration)
            if not info:
                self.send_json({"ok": False, "msg": "这个版本暂时拿不到正确音源（错歌已被拦下），换个版本试试"}); return
            self.send_json({"ok": True, "url": info.get("url", ""), "quality": info.get("quality", "")})
        elif self.path.startswith("/api/online/lyric/trans"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            tk = (qs.get("key") or [""])[0].strip()
            cached = _lyric_trans_load(tk) if tk else None
            if cached and cached.get("trans"):
                self.send_json({"ok": True, "trans": cached.get("trans") or {}, "pending": False}); return
            job = LYRIC_TRANS_JOBS.get(tk) or {}
            if job.get("status") == "done":
                self.send_json({"ok": True, "trans": job.get("trans") or {}, "pending": False}); return
            self.send_json({"ok": True, "trans": {}, "pending": True})
        elif self.path.startswith("/api/online/lyric"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            file_hash = (qs.get("hash") or [""])[0].strip()
            name = (qs.get("name") or [""])[0].strip()
            singer = (qs.get("singer") or [""])[0].strip()
            try:
                duration = int(float((qs.get("duration") or ["0"])[0] or 0))
            except Exception:
                duration = 0
            text = online_lyric_text(file_hash, name, singer, duration)
            payload = {"ok": bool(text), "content": text or ""}
            if not text:
                payload["alt"] = _kugou_lyric_alt_source(name, singer, duration)
            lines = _extract_lyric_lines(text) if text else []
            lang = _detect_lyric_lang(lines) if lines else ""
            payload["lang"] = lang
            payload["phonetic"] = _lyric_phonetic_map(lines, lang) if lang else {}
            if lines and lang and lang != "zh":
                tk = hashlib.md5(("|".join(lines) + "|" + lang).encode("utf-8")).hexdigest()[:20]
                payload["trans_key"] = tk
                cached = _lyric_trans_load(tk)
                if cached and cached.get("trans"):
                    payload["trans"] = cached.get("trans") or {}
                    payload["trans_pending"] = False
                else:
                    _ensure_lyric_trans_job(tk, lines, lang)
                    payload["trans"] = {}
                    payload["trans_pending"] = True
            self.send_json(payload)
        elif self.path.startswith("/api/online/stream"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            target = unquote((qs.get("u") or [""])[0])
            if not _online_stream_allowed(target):
                self.send_response(403); self.end_headers(); return
            headers = {"User-Agent": ONLINE_UA["User-Agent"], "Referer": "http://m.kugou.com/"}
            rng = self.headers.get("Range")
            if rng:
                headers["Range"] = rng
            try:
                resp = requests.get(target, headers=headers, stream=True, timeout=(5, 30))
            except Exception:
                self.send_response(502); self.end_headers(); return
            try:
                self.send_response(resp.status_code)
                ctype = resp.headers.get("Content-Type") or ""
                if (not ctype) or ("octet-stream" in ctype.lower()):
                    low = target.lower()
                    if ".flac" in low:
                        ctype = "audio/flac"
                    elif ".m4a" in low or ".mp4" in low:
                        ctype = "audio/mp4"
                    elif ".aac" in low:
                        ctype = "audio/aac"
                    else:
                        ctype = "audio/mpeg"
                self.send_header("Content-Type", ctype)
                for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    v = resp.headers.get(h)
                    if v:
                        self.send_header(h, v)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                for chunk in resp.iter_content(64 * 1024):
                    if chunk:
                        self.wfile.write(chunk)
            except Exception:
                pass
            finally:
                resp.close()
        elif self.path.startswith("/share_qr/"):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            qr_token = unquote(self.path[len("/share_qr/"):].split("?", 1)[0]).strip()
            data = _parse_share_token_full(qr_token)
            try:
                size = int((qs.get("size") or ["240"])[0])
            except Exception:
                size = 240
            size = max(96, min(640, size))
            full = safe_path(MUSIC_ROOT, data["rel"]) if data else None
            if not full or not os.path.isfile(full):
                self.send_response(404); self.end_headers(); return
            png = _share_qr_png(f"http://{local_ip}:{PORT}/share/{qr_token}", size)
            if not png:
                self.send_response(500); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self._safe_write(lambda: self.wfile.write(png))
        elif self.path == "/api/refresh":
            refresh_all_songs_cache()
            self.send_json({"ok": True})
        elif self.path.startswith("/fonts/"):
            font_name = os.path.basename(unquote(self.path[len("/fonts/"):]))
            font_path = os.path.join(FONT_DIR, font_name)
            if not font_name.lower().endswith((".ttf", ".otf")) or not os.path.isfile(font_path):
                self.send_response(404); self.end_headers(); return
            try:
                with open(font_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "font/ttf" if font_name.lower().endswith(".ttf") else "font/otf")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=31536000")
                self.end_headers()
                self._safe_write(lambda: self.wfile.write(data))
            except Exception:
                self.send_response(500); self.end_headers()
        elif self.path == "/ca.crt":
            # 手机安装用根证书：https://IP:5001/ca.crt 下载后安装为“CA 证书”
            ca_file = os.path.join(CERT_DIR, "ca.pem")
            if not os.path.isfile(ca_file):
                self.send_response(404); self.end_headers(); return
            try:
                with open(ca_file, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                self.send_header("Content-Disposition", 'attachment; filename="conan-ca.crt"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self._safe_write(lambda: self.wfile.write(data))
            except Exception:
                self.send_response(500); self.end_headers()
        elif self.path == "/api/broadcast":
            self.send_json({
                "ok": True,
                "text": str(SYSTEM_CONFIG.get("broadcast_text", "") or ""),
                "updated": str(SYSTEM_CONFIG.get("broadcast_updated", "") or ""),
            })
        elif self.path == "/api/tunnel":
            u = _current_tunnel_url()
            if u:
                self.send_json({"ok": True, "url": u})
            else:
                self.send_json({"ok": False, "msg": "未检测到 cpolar 隧道"})
        elif self.path == "/api/bluetooth/event":
            with BT_MONITOR_LOCK:
                ev = dict(BT_LAST_EVENT or {"text": "", "ts": 0})
            self.send_json({"ok": True, "ts": ev.get("ts", 0), "event": ev.get("text", "")})
        elif self.path.startswith("/share/"):
            share_token = unquote(self.path[len("/share/"):]).strip()
            share_data = _parse_share_token_full(share_token)
            share_rel = share_data["rel"] if share_data else None
            share_full = safe_path(MUSIC_ROOT, share_rel) if share_rel else None
            if not share_full or not os.path.isfile(share_full):
                self._send_html("<h2>分享链接已失效</h2><p>请让分享者重新生成二维码。</p>")
            else:
                self._send_html(_share_page_html(share_rel, share_token, share_data))
        elif self.path.startswith("/api/cover"):
            parsed = urlparse(self.path)
            rel = (parse_qs(parsed.query).get("path") or [""])[0]
            full_path = safe_path(MUSIC_ROOT, rel)
            if not full_path or not os.path.isfile(full_path): self.send_response(404); self.end_headers(); return
            data, mime = _audio_cover_data(full_path)
            if not data: self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", mime or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers(); self.wfile.write(data)
        elif self.path.startswith("/api/folder_cover"):
            parsed = urlparse(self.path)
            rel = (parse_qs(parsed.query).get("path") or [""])[0]
            full_dir = safe_path(MUSIC_ROOT, rel)
            if not full_dir or not os.path.isdir(full_dir): self.send_response(404); self.end_headers(); return
            data, mime = _folder_cover_data(full_dir)
            if not data: self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", mime or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers(); self.wfile.write(data)
        elif self.path.startswith("/api/artist_icon"):
            parsed = urlparse(self.path)
            rel = (parse_qs(parsed.query).get("name") or [""])[0]
            full_dir = safe_path(MUSIC_ROOT, rel)
            if not full_dir or not os.path.isdir(full_dir): self.send_response(404); self.end_headers(); return
            data, mime = _artist_icon_data(rel)
            if not data:
                data, mime = _artist_svg_icon(os.path.basename(full_dir))
            self.send_response(200)
            self.send_header("Content-Type", mime or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers(); self.wfile.write(data)
        elif self.path.startswith("/api/fonts"):
            fonts = []
            if os.path.isdir(FONT_LIB_DIR):
                try:
                    fonts = sorted(fn for fn in os.listdir(FONT_LIB_DIR)
                                   if os.path.splitext(fn)[1].lower() in FONT_LIB_EXT
                                   and os.path.isfile(os.path.join(FONT_LIB_DIR, fn)))
                except Exception:
                    fonts = []
            self.send_json({"ok": True, "fonts": fonts, "dir": FONT_LIB_DIR})
        elif self.path.startswith("/api/font_file"):
            parsed = urlparse(self.path)
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            full_font = safe_path(FONT_LIB_DIR, name)
            if (not full_font or not os.path.isfile(full_font)
                    or os.path.splitext(full_font)[1].lower() not in FONT_LIB_EXT):
                self.send_response(404); self.end_headers(); return
            try:
                with open(full_font, "rb") as f:
                    data = f.read()
            except Exception:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", FONT_MIME.get(os.path.splitext(full_font)[1].lower(), "font/ttf"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers(); self.wfile.write(data)
        elif self.path.startswith("/avatars/"):
            safe_av = safe_path(AVATARS_DIR, unquote(self.path[len("/avatars/"):]))
            if not safe_av or not os.path.isfile(safe_av): self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            with open(safe_av, "rb") as f: self.wfile.write(f.read())
        elif self.path.startswith("/covers/"):
            safe_cv = safe_path(COVERS_DIR, unquote(self.path[len("/covers/"):]))
            if not safe_cv or not os.path.isfile(safe_cv): self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            with open(safe_cv, "rb") as f: self.wfile.write(f.read())
        elif self.path.startswith("/lyric/"):
            self._serve_lyric()
        elif self.path.startswith("/file/"):
            self._serve_file()
        elif self.path.startswith("/api/playlist.m3u"):
            self._serve_m3u()
        elif self.path.startswith("/api/download"):
            self._serve_download()
        else:
            self.send_response(404); self.end_headers()

    def _serve_lyric(self):
        rel = unquote(self.path[len("/lyric/"):])
        base, _ = os.path.splitext(rel)
        lrc_full = safe_path(MUSIC_ROOT, base + ".lrc")
        if not lrc_full or not os.path.isfile(lrc_full): self.send_json({"hasLrc": False}); return
        text = _read_text_auto(lrc_full)
        text = _clean_lyric_text(text)
        if not _lyric_text_valid(text):
            self.send_json({"hasLrc": False}); return
        # KRC 保留逐字时间轴；普通 LRC 直接返回，浏览器只负责渲染
        content = text if _LRC_KRC_RE.search(text) else _krc_like_to_lrc(text)
        self.send_json({"hasLrc": True, "content": content})

    def _serve_m3u(self):
        """导出 M3U/M3U8 播放列表，链接里带登录 token，可直接导入本地播放器"""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        u = user_from_token(token)
        if not u:
            self.send_response(401); self.send_header("Content-Type", "text/plain;charset=utf-8"); self.end_headers()
            self._safe_write(lambda: self.wfile.write("请先登录后再导出播放列表".encode("utf-8")))
            return

        fmt = (qs.get("fmt") or ["m3u"])[0].lower()
        ext = "m3u8" if fmt == "m3u8" else "m3u"
        songs = []
        name = "Conan歌单"
        plid_raw = (qs.get("plid") or [""])[0]
        if plid_raw:
            try: plid = int(plid_raw)
            except Exception: plid = 0
            pls = load_playlists()
            pl = next((p for p in pls if p.get("id") == plid), None)
            if not pl:
                self.send_response(404); self.end_headers(); return
            if pl.get("owner") != u["username"] and not pl.get("is_public") and u.get("role") != "admin":
                self.send_response(403); self.end_headers(); return
            name = pl.get("name") or name
            for s in pl.get("songs", []):
                rel = str(s.get("fullRel", ""))
                title = str(s.get("name", "") or os.path.splitext(os.path.basename(rel))[0])
                songs.append((rel, title))
        else:
            rel_path = (qs.get("path") or [""])[0].strip("/")
            all_songs = scan_all_songs(MUSIC_ROOT)
            prefix = (rel_path + "/") if rel_path else ""
            for s in all_songs:
                if prefix and not s.get("fullRel", "").startswith(prefix): continue
                songs.append((s.get("fullRel", ""), s.get("name", "")))
            if rel_path:
                name = os.path.basename(rel_path) or name

        relative_paths = (qs.get("relative") or ["0"])[0].lower() in ("1", "true", "yes")
        body = self._build_m3u_text(songs, name, token, relative_paths)

        fname = _safe_segment(name, "playlist") + "." + ext
        ascii_name = re.sub(r"[^\x20-\x7E]", "_", fname) or ("playlist." + ext)
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-mpegurl;charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(fname)}')
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._safe_write(lambda: self.wfile.write(body.encode("utf-8")))

    def _build_m3u_text(self, songs, name, token, relative_paths):
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"{get_local_ip()}:{PORT}").split(",")[0].strip()
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
        base = f"{proto}://{host}"
        unique = []
        seen = set()
        for rel, title in songs:
            if not rel or rel in seen: continue
            full = safe_path(MUSIC_ROOT, rel)
            if not full or not os.path.isfile(full): continue
            seen.add(rel)
            unique.append((rel, title or os.path.splitext(os.path.basename(rel))[0]))
            if len(unique) >= 5000: break
        lines = ["#EXTM3U", f"# Conan听歌系统 - {name} - 共 {len(unique)} 首"]
        for rel, title in unique:
            safe_title = re.sub(r"[\r\n]+", " ", title).strip()
            if relative_paths:
                # 适合把整个音乐库拷贝到本地播放器后使用，不绑定服务器地址
                url = rel.replace("/", "\\") if os.name == "nt" else rel
            else:
                url = base + "/file/" + quote(rel, safe="/") + "?token=" + quote(token, safe="")
            # 部分播放器支持 #EXTIMG，能让歌单显示专辑封面
            cover_rel = os.path.join(os.path.dirname(rel), "cover.jpg").replace("\\", "/")
            if safe_path(MUSIC_ROOT, cover_rel) and os.path.isfile(safe_path(MUSIC_ROOT, cover_rel)):
                if relative_paths:
                    lines.append("#EXTIMG:" + cover_rel.replace("/", "\\" if os.name == "nt" else "/"))
                else:
                    lines.append("#EXTIMG:" + base + "/api/folder_cover?path=" + quote(cover_rel, safe="") + "&token=" + quote(token, safe=""))
            lines.append(f"#EXTINF:-1,{safe_title}")
            lines.append(url)
        return "\n".join(lines) + "\n"

    def _serve_file(self):
        ip = self._client_ip()
        if not self._audio_rate_ok(ip): self.send_response(429); self.end_headers(); return

        parsed = urlparse(self.path)
        rel = unquote(parsed.path[len("/file/"):])
        qs = parse_qs(parsed.query)
        u = user_from_token((qs.get("token") or [""])[0])
        share_token = (qs.get("st") or [""])[0]
        share_rel = _parse_share_token(share_token) if share_token else None
        share_ok = bool(share_rel and share_rel == rel)
        if share_ok:
            u = u or {"username": "share", "role": "guest"}
        vip = is_vip(u) or bool(u and u.get("role") == "admin") or share_ok

        # 游客模式：play=允许试听，view=只能浏览，closed=未登录不能播放
        if not u and not share_ok and str(SYSTEM_CONFIG.get("guest_mode", "play")) in ("view", "closed"):
            self.send_response(403); self.end_headers(); return
        
        share_flag = (qs.get("share") or [""])[0]
        if share_flag == "1": vip = False
        if share_ok: vip = True

        if not vip and not share_ok and not _check_single_song_limit(ip, rel):
            self.send_response(403); self.end_headers(); return

        full_path = safe_path(MUSIC_ROOT, rel)
        if not full_path or not os.path.isfile(full_path): self.send_response(404); self.end_headers(); return
        meta = _cache_file_meta(full_path)
        if not meta or meta["size"] <= 0: self.send_response(404); self.end_headers(); return
        file_size = meta["size"]

        ext = os.path.splitext(full_path)[1].lower()
        mime = {".mp3":"audio/mpeg",".flac":"audio/flac",".m4a":"audio/mp4",".wav":"audio/wav",".ogg":"audio/ogg",".aac":"audio/aac"}.get(ext, "audio/mpeg")

        trial_limit = file_size if vip else int(file_size * TRAIL_RATIO)
        cache_hdr = "public, max-age=3600" if vip else "no-store, no-cache, must-revalidate"

        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                s_str, e_str = range_header.replace("bytes=","").split("-")
                start = int(s_str) if s_str else 0
                if start >= trial_limit:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers(); return
                end = int(e_str) if e_str else trial_limit - 1
                end = min(end, trial_limit - 1, file_size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", cache_hdr)
                self.end_headers()
                with open(full_path, "rb") as f:
                    f.seek(start); self._safe_write(lambda: self.wfile.write(f.read(length)))
                return
            except Exception: pass

        send_len = min(trial_limit, file_size)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(send_len))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache_hdr)
        self.end_headers()
        with open(full_path, "rb") as f:
            self._safe_write(lambda: self.wfile.write(f.read(send_len)))

    def _serve_download(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        u = user_from_token((qs.get("token") or [""])[0])
        rel = (qs.get("path") or [""])[0]
        
        if not u: self.send_response(401); self.end_headers(); return
        full_path = safe_path(MUSIC_ROOT, rel)
        if not full_path or not os.path.isfile(full_path): self.send_response(404); self.end_headers(); return

        is_bought = rel in u.get("purchased", [])
        has_dl_auth = is_download_vip(u) or u.get("role")=="admin" or is_bought

        fname = os.path.basename(full_path)
        name_no_ext = os.path.splitext(fname)[0]

        if has_dl_auth:
            # 返回原版音频
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{quote(fname)}"')
            self.send_header("Content-Length", str(os.path.getsize(full_path)))
            self.end_headers()
            with open(full_path, "rb") as f:
                self._safe_write(lambda: self.wfile.write(f.read()))
        else:
            # 返回加密的 .coman 文件 (全文件流式异或加密)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{quote(name_no_ext)}.coman"')
            
            with open(full_path, "rb") as f:
                raw_data = f.read()
            
            enc_data = _encrypt_stream(raw_data)
            
            self.send_header("Content-Length", str(len(enc_data)))
            self.end_headers()
            self._safe_write(lambda: self.wfile.write(enc_data))

    def do_POST(self):
        global KUGOU_MAX_CONCURRENT, KUGOU_MAX_ACTIVE_TASKS, KUGOU_TASK_TIMEOUT
        if not self._rate_ok():
            self.send_json({"ok": False, "msg": "请求过于频繁"}); return
        
        if self.path in ["/api/admin/songs/upload", "/api/upload_avatar", "/api/upload_image"]:
            ctype, pdict = cgi.parse_header(self.headers.get('content-type'))
            if ctype == 'multipart/form-data':
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={'REQUEST_METHOD': 'POST'})
                token = form.getvalue("token", "")
                u = user_from_token(token)
                if not u: self.send_json({"ok": False, "msg": "请先登录"}); return
                
                if self.path == "/api/upload_avatar":
                    if "file" not in form: self.send_json({"ok": False, "msg": "无文件"}); return
                    fileitem = form["file"]
                    if not fileitem.filename: self.send_json({"ok": False, "msg": "文件名错误"}); return
                    fn = f"{u['username']}_{int(time.time())}.jpg"
                    save_to = os.path.join(AVATARS_DIR, fn)
                    with open(save_to, 'wb') as f: shutil.copyfileobj(fileitem.file, f)
                    update_user(u["username"], avatar="/avatars/"+fn)
                    self.send_json({"ok": True, "msg": "头像已更新"}); return
                    
                elif self.path == "/api/upload_image":
                    if "file" not in form: self.send_json({"ok": False, "msg": "无文件"}); return
                    fileitem = form["file"]
                    if not fileitem.filename: self.send_json({"ok": False, "msg": "文件名错误"}); return
                    fn = f"cover_{int(time.time())}_{secrets.token_hex(4)}.jpg"
                    save_to = os.path.join(COVERS_DIR, fn)
                    with open(save_to, 'wb') as f: shutil.copyfileobj(fileitem.file, f)
                    self.send_json({"ok": True, "url": "/covers/"+fn}); return
                    
                elif self.path == "/api/admin/songs/upload":
                    if u.get("role") != "admin": self.send_json({"ok": False, "msg": "无权限"}); return
                    target_dir = form.getvalue("target_dir", "")
                    safe_dir = safe_path(MUSIC_ROOT, target_dir)
                    if not safe_dir or not os.path.isdir(safe_dir): self.send_json({"ok": False, "msg": "目录无效"}); return
                    if "file" not in form: self.send_json({"ok": False, "msg": "请选择文件"}); return
                    fileitem = form["file"]
                    fn = os.path.basename(fileitem.filename)
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in MUSIC_EXT: self.send_json({"ok": False, "msg": "格式不支持"}); return
                    save_to = os.path.join(safe_dir, fn)
                    with open(save_to, 'wb') as f: shutil.copyfileobj(fileitem.file, f)
                    refresh_all_songs_cache()
                    self.send_json({"ok": True, "msg": "上传成功"}); return
        else:
            body = self.read_post_body()

        if self.path == "/api/recognize":
            audio = str(body.get("audio") or "")
            if audio.startswith("data:") and "," in audio:
                audio = audio.split(",", 1)[1]
            try:
                raw = base64.b64decode(audio, validate=False)
            except Exception:
                self.send_json({"ok": False, "msg": "音频数据无效"}); return
            if not raw:
                self.send_json({"ok": False, "msg": "没收到录音"}); return
            if len(raw) > 12 * 1024 * 1024:
                self.send_json({"ok": False, "msg": "录音太长，请控制在 10 秒左右"}); return
            if raw[:4] != b"RIFF":
                self.send_json({"ok": False, "msg": "只接受 WAV 录音（浏览器会自动转码）"}); return
            self.send_json(recognize_audio(raw)); return

        if self.path == "/api/heartbeat":
            token = body.get("token", ""); u = user_from_token(token)
            if u:
                song = body.get("song", "")
                if song: ONLINE_USERS[u["username"]] = {"time": time.time(), "song": song, "ip": self._client_ip()}
                else: ONLINE_USERS.pop(u["username"], None)
            self.send_json({"ok": True}); return

        if self.path == "/api/playlists/list":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            pls = load_playlists()
            res = [p for p in pls if p["owner"] == u["username"] or p.get("is_public") or u.get("role")=="admin"]
            self.send_json({"ok": True, "data": res}); return

        if self.path == "/api/playlists/create":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            name = body.get("name","").strip()
            cover = body.get("cover", "").strip()
            if not name: self.send_json({"ok": False, "msg": "名称不能为空"}); return
            is_public = bool(body.get("is_public", False))
            if u.get("role") != "admin": is_public = False
            pls = load_playlists()
            pid = int(time.time()*1000)
            pls.append({"id": pid, "name": name, "cover": cover, "owner": u["username"], "is_public": is_public, "songs": []})
            save_playlists(pls)
            self.send_json({"ok": True, "msg": "创建成功", "pid": pid}); return

        if self.path == "/api/playlists/add_song":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            pid = int(body.get("pid", 0))
            raw_songs = body.get("songs")
            if isinstance(raw_songs, list): songs = raw_songs
            else: songs = [body.get("song", {})]
            songs = [s for s in songs if isinstance(s, dict) and s.get("fullRel")]
            if not songs: self.send_json({"ok": False, "msg": "无效歌曲"}); return
            pls = load_playlists()
            added = 0
            for p in pls:
                if p["id"] == pid:
                    if p["owner"] != u["username"] and u.get("role")!="admin": self.send_json({"ok": False, "msg": "无权修改"}); return
                    for song in songs:
                        if not any(x["fullRel"] == song["fullRel"] for x in p["songs"]):
                            p["songs"].append(song); added += 1
                    save_playlists(pls)
                    self.send_json({"ok": True, "added": added, "msg": f"已添加 {added} 首"}); return
            self.send_json({"ok": False, "msg": "歌单不存在"}); return

        if self.path == "/api/playlists/remove_song":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            pid = int(body.get("pid", 0)); rel = body.get("fullRel", "")
            pls = load_playlists()
            for p in pls:
                if p["id"] == pid:
                    if p["owner"] != u["username"] and u.get("role")!="admin": self.send_json({"ok": False, "msg": "无权修改"}); return
                    p["songs"] = [x for x in p["songs"] if x["fullRel"] != rel]
                    save_playlists(pls)
                    self.send_json({"ok": True, "msg": "已移除"}); return
            self.send_json({"ok": False, "msg": "歌单不存在"}); return

        if self.path == "/api/playlists/update":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            pid = int(body.get("pid", 0))
            new_name = str(body.get("name", "") or "").strip()
            wants_public = ("is_public" in body)
            if not new_name and not wants_public:
                self.send_json({"ok": False, "msg": "没有要修改的内容"}); return
            pls = load_playlists()
            for p in pls:
                if p["id"] == pid:
                    if p["owner"] != u["username"] and u.get("role") != "admin":
                        self.send_json({"ok": False, "msg": "无权修改"}); return
                    if new_name: p["name"] = new_name[:40]
                    if wants_public: p["is_public"] = bool(body.get("is_public"))
                    save_playlists(pls)
                    action = "重命名歌单" if new_name else ("公开歌单" if p.get("is_public") else "设为私有歌单")
                    log_admin_action(u["username"], action, p.get("name", ""), self._client_ip())
                    self.send_json({"ok": True, "msg": "已保存", "is_public": bool(p.get("is_public"))}); return
            self.send_json({"ok": False, "msg": "歌单不存在"}); return

        if self.path == "/api/playlists/delete":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            pid = int(body.get("pid", 0))
            pls = load_playlists()
            target = None
            for p in pls:
                if p["id"] == pid:
                    target = p; break
            if not target: self.send_json({"ok": False, "msg": "歌单不存在"}); return
            if target.get("owner") != u["username"] and u.get("role") != "admin":
                self.send_json({"ok": False, "msg": "无权删除"}); return
            if not backup_playlists():
                self.send_json({"ok": False, "msg": "备份歌单失败，已取消删除"}); return
            pls = [p for p in pls if p["id"] != pid]
            save_playlists(pls)
            log_admin_action(u["username"], "删除歌单", target.get("name", ""), self._client_ip())
            self.send_json({"ok": True, "msg": "歌单已删除", "pid": pid}); return

        if self.path == "/api/song/buy":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            rel = body.get("path", "")
            if u.get("points",0) < SINGLE_BUY_POINTS: self.send_json({"ok": False, "msg": f"积分不足，需要{SINGLE_BUY_POINTS}积分"}); return
            purchased = u.get("purchased", [])
            if rel not in purchased:
                add_points(u["username"], -SINGLE_BUY_POINTS, f"单曲买断: {rel}")
                purchased.append(rel)
                update_user(u["username"], purchased=purchased)
            self.send_json({"ok": True, "msg": "买断成功！可原版下载"}); return

        if self.path == "/api/admin/online":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            now = time.time(); res = []
            for uname, info in list(ONLINE_USERS.items()):
                if now - info["time"] < 300:
                    usr = get_user_by_name(uname)
                    res.append({"username": uname, "song": info["song"], "is_vip": is_vip(usr)})
            self.send_json({"ok": True, "data": res}); return

        if self.path == "/api/admin/copyright/set":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            rel = body.get("path",""); cps = load_copyrights()
            if body.get("copyright","").strip(): cps[rel] = body.get("copyright","").strip()
            else: cps.pop(rel, None)
            save_copyrights(cps); refresh_all_songs_cache()
            self.send_json({"ok": True, "msg": "已更新"}); return

        if self.path == "/api/admin/kugou/auth":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            auth_key = body.get("auth_key","").strip(); kgs = load_kugou_auth()
            if body.get("is_auth", False): kgs[auth_key] = True
            else: kgs.pop(auth_key, None)
            save_kugou_auth(kgs); self.send_json({"ok": True, "msg": "已更新"}); return

        if self.path == "/api/kugou/album/prevalidate":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            album_name = str(body.get("album") or body.get("album_name") or "").strip()
            singer = str(body.get("singer") or "").strip()
            album_id = str(body.get("album_id") or "").strip()
            if not album_name and not album_id:
                self.send_json({"ok": False, "msg": "缺少专辑名或 AlbumID"}); return
            tracks = _kugou_album_tracks(album_name, singer, album_id)
            if not tracks:
                self.send_json({"ok": False, "msg": "酷狗未返回该专辑曲目，可能专辑不存在或音源已下架", "count": 0})
                return
            self.send_json({"ok": True, "count": len(tracks),
                            "album": album_name or (tracks[0].get("album") if tracks else ""),
                            "tracks": [{"name": t.get("name"), "singer": t.get("singer")} for t in tracks[:100]],
                            "msg": f"专辑预校验通过，共 {len(tracks)} 首"})
            return

        if self.path == "/api/kugou/download/start":
            u = user_from_token(self._request_token(body))
            if not _can_download(u):
                self.send_json({"ok": False, "msg": "仅站长或会员可下载到本地（普通用户请直接在线听）"}); return
            if not _kugou_create_rate_ok(self._client_ip()):
                self.send_json({"ok": False, "msg": "下载任务提交过于频繁，请稍后再试"}); return
            if not MUSICDL_AVAILABLE:
                self.send_json({"ok": False, "msg": "服务器未安装 musicdl，请运行 pip install musicdl 后重启服务"}); return
            raw = body.get("songs")
            if raw is None:
                raw = body if (body.get("song") or body.get("name")) else []
            items = _normalize_kugou_items(raw)
            if not items:
                self.send_json({"ok": False, "msg": "没有可下载的歌曲"}); return
            # 整张专辑：后端按 AlbumID/专辑名重新拉完整曲目，避免酷狗搜索只返回部分歌曲
            task_quality = str(body.get("quality") or SYSTEM_CONFIG.get("download_quality", "auto") or "auto")
            if task_quality not in KUGOU_QUALITY_OPTIONS:
                task_quality = "auto"
            if body.get("expand_album"):
                album_name = str(body.get("album") or body.get("album_name") or "").strip()
                album_id = str(body.get("album_id") or body.get("albumid") or "").strip()
                album_singer = str(body.get("singer") or "").strip()
                if not album_name and items:
                    album_name = str(items[0].get("album") or "").strip()
                    album_id = album_id or str(items[0].get("album_id") or "").strip()
                    album_singer = album_singer or str(items[0].get("singer") or "").strip()
                extras = _kugou_album_tracks(album_name, album_singer, album_id)
                seen = {it.get("auth_key") for it in items}
                downloads = load_kugou_downloads()
                for s in extras:
                    auth_key = str(s.get("auth_key") or "").strip()
                    if not auth_key or auth_key in seen: continue
                    if s.get("local_rel"): continue
                    entry = downloads.get(auth_key)
                    if entry and entry.get("rel"):
                        _found_rel, full = _kugou_find_audio(entry["rel"])
                        if full and os.path.isfile(full): continue
                    if body.get("skip_blacklist") and s.get("blacklisted"): continue
                    seen.add(auth_key)
                    items.append({
                        "song": str(s.get("name") or "").strip(),
                        "singer": str(s.get("singer") or "").strip(),
                        "album": str(s.get("album") or album_name).strip(),
                        "album_id": str(s.get("album_id") or album_id).strip(),
                        "album_artist": album_singer,
                        "hash": str(s.get("hash") or "").strip(),
                        "cover": _normalize_kugou_cover_url(s.get("cover") or ""),
                        "blacklisted": s.get("blacklisted") or "",
                        "auth_key": auth_key,
                        "quality": task_quality,
                    })
                if not items:
                    self.send_json({"ok": False, "msg": "该专辑曲目均已入库"}); return
            # 跳过已有文件开关：默认开启，避免批量补缺重复下载；也可临时关闭强制重下
            if bool(body.get("skip_existing", True)):
                downloads = load_kugou_downloads()
                existing_keys = set()
                for it in items:
                    key = str(it.get("auth_key") or "")
                    entry = downloads.get(key)
                    if not entry and key:
                        entry = downloads.get(f"{it.get('song','')}-{it.get('singer','')}")
                    if not entry or not entry.get("rel"): continue
                    _found_rel, full = _kugou_find_audio(entry["rel"])
                    if full and os.path.isfile(full): existing_keys.add(key)
                if existing_keys:
                    items = [it for it in items if it.get("auth_key") not in existing_keys]
                if not items:
                    self.send_json({"ok": False, "msg": "所选歌曲本地均已存在，已跳过"}); return
            task, reused = create_kugou_download_task(items, priority=int(body.get("priority", 0) or 0))
            if not task:
                self.send_json({"ok": False, "msg": f"未完成任务已达上限（{KUGOU_MAX_ACTIVE_TASKS} 个），请先处理已有任务"}); return
            log_admin_action(u["username"], "创建下载任务" if not reused else "复用下载任务", f"{len(items)} 首，任务 {task['id']}：" + "、".join(f"{it['song']}-{it['singer']}" for it in items[:3]), self._client_ip())
            self.send_json({
                "ok": True, "task_id": task["id"], "total": len(task["items"]), "reused": reused,
                "msg": "该歌曲已在下载队列中" if reused else "已加入下载队列"
            })
            return

        if self.path == "/api/kugou/lyric/repair":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以补歌词"}); return
            rel = str(body.get("rel", "") or "").strip()
            if rel:
                rels = [rel]
            else:
                rels = []
                seen = set()
                for rec in load_kugou_downloads().values():
                    if not isinstance(rec, dict): continue
                    r = str(rec.get("rel", "") or "").replace("\\", "/").strip("/")
                    if r and r not in seen:
                        seen.add(r); rels.append(r)
            if not rels:
                self.send_json({"ok": False, "msg": "没有可补歌词的下载记录"}); return
            fixed = 0
            details = []
            for r in rels[:500]:
                ok, msg = repair_kugou_lyric(r)
                if ok: fixed += 1
                else:
                    details.append({"rel": r, "msg": msg})
            failed = len(rels) - fixed
            log_admin_action(u["username"], "批量补歌词", f"检查 {len(rels)} 首，成功/已有 {fixed} 首，失败 {failed} 首", self._client_ip())
            self.send_json({
                "ok": True,
                "msg": f"检查 {len(rels)} 首，已有/补齐 {fixed} 首，未找到 {failed} 首",
                "total": len(rels), "fixed": fixed, "failed": failed,
                "details": details[:10],
            })
            return

        if self.path in ("/api/admin/lyric/shift", "/api/admin/lyric/shift_line", "/api/admin/lyric/reset",
                          "/api/admin/lyric/refresh", "/api/admin/lyric/export",
                          "/api/admin/lyric/import", "/api/admin/lyric/embed",
                          "/api/admin/lyric/search", "/api/admin/lyric/upgrade_all",
                          "/api/admin/lyric/upgrade_status", "/api/admin/lyric/upgrade_stop"):
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以操作歌词"}); return
            rel = str(body.get("rel", "") or "").strip()
            if self.path.endswith("/upgrade_all"):
                try: limit = int(body.get("limit", 100) or 100)
                except Exception: limit = 100
                ok, msg, state = start_lyric_upgrade(limit)
                log_admin_action(u["username"], "批量升级逐字歌词", msg, self._client_ip())
                self.send_json({"ok": ok, "msg": msg, "state": state}); return
            if self.path.endswith("/upgrade_status"):
                with LYRIC_UPGRADE_LOCK: state = dict(LYRIC_UPGRADE_STATE)
                self.send_json({"ok": True, "state": state}); return
            if self.path.endswith("/upgrade_stop"):
                with LYRIC_UPGRADE_LOCK:
                    LYRIC_UPGRADE_STATE["stop"] = True
                    if not LYRIC_UPGRADE_STATE.get("running"):
                        LYRIC_UPGRADE_STATE["message"] = "当前没有运行中的升级任务"
                self.send_json({"ok": True, "msg": "已请求停止升级"}); return
            if self.path.endswith("/refresh"):
                ok, msg = repair_kugou_lyric(rel, force=True)
                log_admin_action(u["username"], "重新获取酷狗歌词", f"{rel}: {msg}", self._client_ip())
                self.send_json({"ok": ok, "msg": msg}); return
            if self.path.endswith("/reset"):
                ok, msg = lrclib_reset(rel)
                log_admin_action(u["username"], "恢复歌词初始时间轴", rel, self._client_ip())
                self.send_json({"ok": ok, "msg": msg}); return
            if self.path.endswith("/shift_line"):
                ok, msg = lrclib_offset_line(rel, body.get("line", 0), float(body.get("seconds", 0) or 0))
                log_admin_action(u["username"], "单句歌词微调", f"{rel}: {msg}", self._client_ip())
                self.send_json({"ok": ok, "msg": msg}); return
            if self.path.endswith("/shift"):
                ok, msg = lrclib_offset(rel, float(body.get("seconds", 0) or 0))
                self.send_json({"ok": ok, "msg": msg}); return
            if self.path.endswith("/export"):
                raw = bool(body.get("raw"))
                text, msg = lrclib_export(rel, raw=raw)
                ext = ".krc" if raw else ".lrc"
                self.send_json({"ok": bool(text), "text": text or "", "msg": msg,
                                "filename": (os.path.splitext(os.path.basename(rel))[0] + ext)}); return
            if self.path.endswith("/import"):
                ok, msg = lrclib_import(rel, body.get("text", ""))
                log_admin_action(u["username"], "导入歌词", f"{rel}", self._client_ip())
                self.send_json({"ok": ok, "msg": msg}); return
            if self.path.endswith("/embed"):
                ok, msg = embed_lyric_to_audio(rel)
                log_admin_action(u["username"], "内嵌歌词", f"{rel}: {msg}", self._client_ip())
                self.send_json({"ok": ok, "msg": msg}); return
            data = lrclib_search(body.get("keyword", ""), int(body.get("limit", 50) or 50))
            self.send_json({"ok": True, "data": data, "count": len(data)}); return

            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            tid = str(body.get("task_id", ""))
            with KUGOU_TASK_LOCK:
                task = KUGOU_TASKS.get(tid)
                pub = _public_kugou_task(task)
            if not pub:
                self.send_json({"ok": False, "msg": "任务不存在或已过期"}); return
            self.send_json({"ok": True, "task": pub})
            return

        if self.path == "/api/kugou/download/list":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            with KUGOU_TASK_LOCK:
                tasks = sorted(KUGOU_TASKS.values(), key=lambda x: x.get("created", 0), reverse=True)
                data = [_public_kugou_task(t, 12) for t in tasks[:50]]
                active = sum(1 for t in KUGOU_TASKS.values() if t.get("status") in ("queued", "running", "pausing"))
            self.send_json({
                "ok": True, "data": data, "active": active,
                "max_concurrent": KUGOU_MAX_CONCURRENT, "max_active": KUGOU_MAX_ACTIVE_TASKS,
                "engine": MUSICDL_AVAILABLE,
            })
            return

        if self.path in ("/api/kugou/download/pause", "/api/kugou/download/resume", "/api/kugou/download/cancel"):
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            tid = str(body.get("task_id", ""))
            with KUGOU_TASK_LOCK:
                task = KUGOU_TASKS.get(tid)
            if not task:
                self.send_json({"ok": False, "msg": "任务不存在或已过期"}); return
            if self.path.endswith("/pause"):
                ok = _pause_kugou_task(task); msg = "已暂停" if ok else "当前状态不能暂停"
            elif self.path.endswith("/resume"):
                ok = _resume_kugou_task(task); msg = "已恢复下载" if ok else "当前状态不能继续"
            else:
                ok = _cancel_kugou_task(task); msg = "已取消" if ok else "当前状态不能取消"
            log_admin_action(u["username"], "任务操作", f"{self.path.rsplit('/',1)[-1]} {tid}: {msg}", self._client_ip())
            self.send_json({"ok": bool(ok), "msg": msg})
            return

        if self.path in ("/api/kugou/download/priority", "/api/kugou/download/batch", "/api/kugou/download/retry"):
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            if self.path.endswith("/priority"):
                tid = str(body.get("task_id", ""))
                ok = set_kugou_task_priority(tid, int(body.get("priority", 1) or 1))
                log_admin_action(u["username"], "任务插队", f"{tid}", self._client_ip())
                self.send_json({"ok": ok, "msg": "已调到队首优先下载" if ok else "任务不存在"})
                return
            if self.path.endswith("/batch"):
                ids = body.get("task_ids") or []
                if not isinstance(ids, list): ids = []
                action = str(body.get("action", "") or "").lower()
                data = batch_kugou_task_action([str(x) for x in ids], action)
                log_admin_action(u["username"], "批量任务操作", f"{action}: {len(ids)} 个任务", self._client_ip())
                self.send_json({"ok": True, "data": data, "msg": f"已处理 {sum(1 for x in data if x.get('ok'))} 个任务"})
                return
            tid = str(body.get("task_id", ""))
            new_task, msg = retry_kugou_task(tid)
            if not new_task:
                self.send_json({"ok": False, "msg": msg}); return
            log_admin_action(u["username"], "重试下载", f"{tid} -> {new_task['id']}，{msg}", self._client_ip())
            self.send_json({"ok": True, "task_id": new_task["id"], "msg": msg})
            return

        if self.path in ("/api/admin/settings/get", "/api/admin/settings/save"):
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以修改系统设置"}); return
            if self.path.endswith("/get"):
                safe = {k: SYSTEM_CONFIG.get(k, v) for k, v in DEFAULT_SYSTEM_CONFIG.items()}
                safe.pop("admin_password", None)
                self.send_json({
                    "ok": True, "data": safe,
                    "runtime": {
                        "max_concurrent": KUGOU_MAX_CONCURRENT,
                        "max_active": KUGOU_MAX_ACTIVE_TASKS,
                        "timeout": KUGOU_TASK_TIMEOUT,
                        "free_gb": round(_disk_usage_ok()[1], 1),
                        "music_root": MUSIC_ROOT,
                    }
                })
                return
            patch = body.get("settings") or body
            if not isinstance(patch, dict):
                self.send_json({"ok": False, "msg": "设置格式错误"}); return
            # 数值字段做范围保护，避免把服务器配死
            for key, low, high in (
                ("download_concurrency", 1, 8), ("download_max_active", 1, 200),
                ("download_timeout", 10, 600), ("download_speed_limit_kb", 0, 1000000),
                ("disk_warn_gb", 0, 1000), ("disk_stop_gb", 0, 1000),
                ("log_rotate_mb", 1, 1024), ("cover_size", 240, 1080),
                ("cover_retry", 0, 5), ("login_max_fail", 1, 50),
                ("login_lock_minutes", 1, 1440), ("album_scan_days", 0, 3650),
            ):
                if key in patch:
                    try: patch[key] = max(low, min(high, int(float(patch[key]))))
                    except Exception: patch.pop(key, None)
            if "download_quality" in patch and str(patch["download_quality"]) not in KUGOU_QUALITY_OPTIONS:
                patch["download_quality"] = "auto"
            if "broadcast_text" in patch:
                patch["broadcast_updated"] = time.strftime("%Y-%m-%d %H:%M")
            save_system_config(patch)
            _refresh_blacklist_config()
            KUGOU_MAX_CONCURRENT = _config_int("download_concurrency", 3, 1, 8)
            KUGOU_MAX_ACTIVE_TASKS = _config_int("download_max_active", 20, 1, 200)
            KUGOU_TASK_TIMEOUT = _config_int("download_timeout", 45, 10, 600)
            log_admin_action(u["username"], "修改系统设置", json.dumps({k: v for k, v in patch.items() if k in DEFAULT_SYSTEM_CONFIG}, ensure_ascii=False)[:400], self._client_ip())
            self.send_json({"ok": True, "msg": "设置已保存并即时生效", "data": {k: SYSTEM_CONFIG.get(k) for k in DEFAULT_SYSTEM_CONFIG if k != "admin_password"}})
            return

        if self.path == "/api/admin/logs":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            count = max(20, min(500, int(body.get("lines", 120) or 120)))
            keyword = str(body.get("filter", "") or "").strip().lower()
            lines = []
            try:
                with open(ADMIN_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if keyword and keyword not in line.lower(): continue
                        lines.append(line.rstrip("\n"))
            except Exception:
                pass
            self.send_json({"ok": True, "data": lines[-count:], "total": len(lines)})
            return

        if self.path == "/api/admin/backup":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            try:
                name = f"conan_backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
                path = os.path.join(BACKUP_DIR, name)
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                    for fn in (CONFIG_FILE, KUGOU_TASK_FILE, KUGOU_TASK_BACKUP_FILE,
                               KUGOU_AUTH_FILE, KUGOU_DOWNLOAD_FILE, PLAYLISTS_FILE):
                        if os.path.exists(fn): z.write(fn, arcname=os.path.basename(fn))
                log_admin_action(u["username"], "备份系统数据", name, self._client_ip())
                self.send_json({"ok": True, "msg": f"已备份到 {path}", "path": path})
            except Exception as e:
                self.send_json({"ok": False, "msg": f"备份失败：{e}"})
            return

        if self.path == "/api/admin/library/scan":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以扫描音乐库"}); return
            data = library_scan()
            log_admin_action(u["username"], "扫描音乐库", f"{data['total']} 首，丢音频 {data['audio_missing']}，缺歌词 {data['lyric_missing']}，缺封面 {data['cover_missing']}", self._client_ip())
            self.send_json({"ok": True, "data": data})
            return

        if self.path == "/api/admin/library/repair":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以批量补歌词/封面"}); return
            kind = str(body.get("kind", "both") or "both").lower()
            if kind not in ("lyric", "cover", "both"): kind = "both"
            data = library_repair(kind)
            log_admin_action(u["username"], "批量修复音乐库", f"{kind}: 歌词成功 {data['lyric_fixed']} 失败 {data['lyric_failed']}，封面成功 {data['cover_fixed']} 失败 {data['cover_failed']}", self._client_ip())
            self.send_json({
                "ok": True, "data": data,
                "msg": f"补歌词：成功 {data['lyric_fixed']} / 失败 {data['lyric_failed']}；补封面：成功 {data['cover_fixed']} / 失败 {data['cover_failed']}"
            })
            return

        if self.path == "/api/admin/library/albums/scan":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以检查专辑完整度"}); return
            data = library_album_scan(
                force=bool(body.get("force", True)),
                recent_days=int(body.get("recent_days", SYSTEM_CONFIG.get("album_scan_days", 30)) or 0),
                skip_invalid=bool(body.get("skip_invalid", True)),
            )
            log_admin_action(u["username"], "专辑完整度扫描", f"{data['album_total']} 张专辑，缺歌 {data['album_partial']} 张，共缺 {data['tracks_missing']} 首", self._client_ip())
            self.send_json({"ok": True, "data": data})
            return

        if self.path == "/api/admin/library/albums/repair":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "只有管理员可以批量补全专辑"}); return
            names = body.get("albums") or []
            if not isinstance(names, list): names = []
            data = library_album_repair(
                names,
                skip_blacklist=bool(body.get("skip_blacklist", True)),
                recent_days=int(body.get("recent_days", 0) or 0),
            )
            log_admin_action(u["username"], "批量补全专辑", f"新增 {data['albums']} 张，排队 {data['tracks_queued']} 首", self._client_ip())
            self.send_json({
                "ok": True, "data": data,
                "msg": f"已创建 {data['albums']} 个补全任务，共 {data['tracks_queued']} 首；跳过 {len(data['skipped'])} 张"
            })
            return

        if self.path == "/api/admin/library/tools":
            u = self._admin_user(body)
            if not u:
                self.send_json({"ok": False, "msg": "无权限"}); return
            action = str(body.get("action", "") or "").lower()
            if action == "stats":
                data = library_stats()
                self.send_json({"ok": True, "data": data}); return
            if action == "csv":
                text = library_export_csv()
                log_admin_action(u["username"], "导出音乐库清单", "CSV", self._client_ip())
                self.send_json({"ok": True, "csv": text, "filename": f"music_library_{time.strftime('%Y%m%d')}.csv"}); return
            if action == "empty":
                delete = bool(body.get("delete", False))
                dirs = library_empty_dirs(delete=delete)
                log_admin_action(u["username"], "清理空目录" if delete else "扫描空目录", f"{len(dirs)} 个", self._client_ip())
                self.send_json({"ok": True, "data": dirs, "deleted": delete,
                                "msg": f"{'已删除' if delete else '发现'} {len(dirs)} 个空目录"}); return
            if action == "duplicates":
                groups = library_duplicate_files(int(body.get("limit", 120) or 120))
                self.send_json({"ok": True, "data": groups,
                                "msg": f"发现 {len(groups)} 组重复文件（MD5）"}); return
            if action == "cache":
                library_cache_clear()
                self.send_json({"ok": True, "msg": "缓存已清理"}); return
            self.send_json({"ok": False, "msg": "未知工具操作"}); return

            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            self.send_json({"ok": True, "data": u.get("favs", [])}); return

        if self.path == "/api/favs/sync":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            favs = body.get("favs", []); clean, seen = [], set()
            for f in favs:
                if not isinstance(f, dict): continue
                fr = f.get("fullRel","")
                if fr and fr not in seen: seen.add(fr); clean.append({"fullRel": fr, "name": f.get("name","")})
            update_user(u["username"], favs=clean)
            self.send_json({"ok": True, "data": clean}); return

        if self.path == "/api/bluetooth/report":
            u = user_from_token(self._request_token(body))
            if not u:
                self.send_json({"ok": False, "msg": "请先登录"}); return
            action = str(body.get("action", "") or "").strip().lower()
            if action not in ("connect", "disconnect"):
                self.send_json({"ok": False, "msg": "无效的状态"}); return
            _bt_report(action, str(body.get("name", "") or ""))
            self.send_json({"ok": True}); return

        if self.path == "/api/song/delete":
            admin = self._admin_user(body)
            if not admin:
                self.send_json({"ok": False, "msg": "仅管理员可删除歌曲"}); return
            rel = str(body.get("rel", "") or "").replace("\\", "/").strip().lstrip("/")
            full_path = safe_path(MUSIC_ROOT, rel) if rel else None
            if not full_path or not os.path.isfile(full_path):
                self.send_json({"ok": False, "msg": "歌曲不存在"}); return
            if os.path.splitext(full_path)[1].lower() not in MUSIC_EXT:
                self.send_json({"ok": False, "msg": "只能删除音频文件"}); return
            # 移入回收站：保留原目录结构，重名自动加时间戳，误删可手动恢复
            recycle_root = os.path.join(MUSIC_ROOT, "_回收站")
            rel_dir = os.path.dirname(rel)
            recycle_dir = os.path.join(recycle_root, rel_dir) if rel_dir else recycle_root
            try:
                os.makedirs(recycle_dir, exist_ok=True)
            except Exception:
                pass
            target = os.path.join(recycle_dir, os.path.basename(full_path))
            base, ext = os.path.splitext(target)
            n = 1
            while os.path.exists(target):
                target = f"{base}_{time.strftime('%H%M%S')}_{n}{ext}"; n += 1
            try:
                os.replace(full_path, target)
            except Exception as e:
                self.send_json({"ok": False, "msg": f"删除失败：{e}"}); return
            for side_ext in (".lrc", ".krc"):
                side = os.path.splitext(full_path)[0] + side_ext
                if os.path.isfile(side):
                    try:
                        side_t = os.path.splitext(target)[0] + side_ext
                        n2 = 1
                        while os.path.exists(side_t):
                            side_t = f"{os.path.splitext(target)[0]}_{n2}{side_ext}"; n2 += 1
                        os.replace(side, side_t)
                    except Exception:
                        pass
            # 从所有歌单移除该曲
            try:
                pls = load_playlists()
                changed = False
                for pl in pls:
                    songs = pl.get("songs")
                    if not isinstance(songs, list): continue
                    keep = []
                    for s in songs:
                        srel = str(s.get("fullRel", "") or s.get("rel", "") or "").replace("\\", "/").strip().lstrip("/")
                        if srel == rel: continue
                        keep.append(s)
                    if len(keep) != len(songs):
                        pl["songs"] = keep; changed = True
                if changed:
                    backup_playlists(); save_playlists(pls)
            except Exception:
                pass
            log_admin_action(admin["username"], "删除本地歌曲", f"{rel} → _回收站", self._client_ip())
            log_system(f"管理员删除本地歌曲 | {rel} → 回收站")
            self.send_json({"ok": True, "msg": "已移入回收站", "recycled": True}); return

        if self.path == "/api/share/create":
            u = user_from_token(body.get("token", ""))
            if not u: self.send_json({"ok": False, "msg": "请先登录"}); return
            rel = str(body.get("rel", "") or "").replace("\\", "/").strip().lstrip("/")
            full_path = safe_path(MUSIC_ROOT, rel) if rel else None
            if not full_path or not os.path.isfile(full_path):
                self.send_json({"ok": False, "msg": "歌曲不存在"}); return
            try:
                ttl_hours = int(float(body.get("ttl_hours", 0) or 0))
            except Exception:
                ttl_hours = 0
            if ttl_hours not in (0, 1, 24, 168):
                ttl_hours = 0
            note = str(body.get("note", "") or "").strip()[:120]
            cover = str(body.get("cover", "") or "").strip()[:600]
            if cover and not (cover.startswith("/") or cover.startswith("http://") or cover.startswith("https://")):
                cover = ""
            token = _make_share_token(rel, ttl_hours=ttl_hours, note=note, cover=cover)
            url = f"http://{local_ip}:{PORT}/share/{token}"
            self.send_json({"ok": True, "url": url, "qr": _share_qr_data_uri(url), "ttl_hours": ttl_hours, "note": note, "cover": cover}); return

        if self.path == "/api/m3u/selected":
            u = user_from_token(body.get("token", ""))
            if not u:
                self.send_json({"ok": False, "msg": "请先登录"}); return
            rels = body.get("rels")
            if not isinstance(rels, list) or not rels:
                self.send_json({"ok": False, "msg": "请先勾选要导出的歌曲"}); return
            songs = []
            for one in rels[:5000]:
                rel = str(one or "").replace("\\", "/").strip().lstrip("/")
                if not rel: continue
                songs.append((rel, os.path.splitext(os.path.basename(rel))[0]))
            if not songs:
                self.send_json({"ok": False, "msg": "请先勾选要导出的歌曲"}); return
            relative = bool(body.get("relative"))
            text = self._build_m3u_text(songs, "精选歌单", str(body.get("token", "")), relative)
            fname = f"精选歌单_{len(songs)}首.m3u"
            ascii_name = re.sub(r"[^\x20-\x7E]", "_", fname)
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-mpegurl;charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(fname)}')
            self.send_header("Content-Length", str(len(text.encode("utf-8"))))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._safe_write(lambda: self.wfile.write(text.encode("utf-8")))
            return

        if self.path == "/api/register":
            ok, msg = create_user(body.get("username",""), body.get("pwd",""))
            self.send_json({"ok": ok, "msg": msg}); return

        if self.path == "/api/login":
            username = str(body.get("username", "") or "").strip()
            curr_ip = self._client_ip()
            locked, remain = _login_lock_state(username, curr_ip)
            if locked:
                self.send_json({"ok": False, "msg": f"密码错误次数过多，请在 {remain} 秒后再试"}); return
            ok, res = check_user_pwd(username, body.get("pwd",""))
            now_ts = time.time()
            if ok:
                if res == "admin" and not _admin_ip_allowed(curr_ip):
                    self.send_json({"ok": False, "msg": f"当前 IP（{curr_ip}）不在管理员白名单内"}); return
                _clear_login_fail(username, curr_ip)
                token = hashlib.md5((username+str(time.time())+str(os.getpid())+secrets.token_hex(4)).encode()).hexdigest()
                expires_at = now_ts + TOKEN_TTL_SECONDS
                TOKEN_MAP[token] = {"username": username, "created": now_ts, "expires": expires_at}
                save_token_map()
                u = get_user_by_name(username)
                last_ip = u.get("last_ip")
                if last_ip and last_ip != curr_ip:
                    add_message(u["username"], f"【系统提示】您的账号于 {time.strftime('%Y-%m-%d %H:%M:%S')} 在异地（IP: {curr_ip}）登录。", "system")
                update_user(u["username"], last_ip=curr_ip)
                if u.get("role") == "admin":
                    log_admin_action(u["username"], "管理员登录", f"登录成功；UA={self.headers.get('User-Agent','')[:120]}", curr_ip)
                self.send_json({"ok": True, "token": token, "expires": expires_at, "user": self._user_public(u), "server_time": now_ts})
            else:
                rec = _record_login_fail(username, curr_ip)
                if rec.get("until", 0) > now_ts:
                    lock_min = _config_int("login_lock_minutes", 10, 1, 1440)
                    self.send_json({"ok": False, "msg": f"密码错误，账号已锁定 {lock_min} 分钟"}); return
                self.send_json({"ok": False, "msg": res})
            return

        if self.path == "/api/logout":
            tok = body.get("token","")
            if tok in TOKEN_MAP:
                uname = _token_username(tok); ONLINE_USERS.pop(uname, None)
                del TOKEN_MAP[tok]; save_token_map()
            self.send_json({"ok": True}); return

        if self.path == "/api/checktoken":
            u = user_from_token(body.get("token",""))
            now_ts = time.time()
            if u: self.send_json({"ok": True, "user": self._user_public(u), "server_time": now_ts})
            else: self.send_json({"ok": False, "msg": "登录已过期"})
            return

        if self.path == "/api/vip/buy_points":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            points = int(body.get("points", 0))
            vtype = body.get("type", "vip")
            
            req_pts = VIP_POINTS_PER_DAY if vtype=="vip" else DOWNLOAD_VIP_POINTS_PER_DAY
            if points < req_pts: self.send_json({"ok": False, "msg": f"最少兑换{req_pts}积分"}); return
            if u.get("points", 0) < points: self.send_json({"ok": False, "msg": "积分不足"}); return
            
            days = points // req_pts
            add_points(u["username"], -points, f"兑换{days}天{'听歌' if vtype=='vip' else '下载'}会员")
            grant_vip(u["username"], days, vtype)
            self.send_json({"ok": True, "msg": f"成功兑换{days}天"}); return

        if self.path == "/api/vip/apply_pay":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            amount = int(body.get("amount", 0)); note = (body.get("note","") or "").strip()
            if amount < 1 or not note: self.send_json({"ok": False, "msg": "请填写完整"}); return
            pays = load_vip_pays()
            pays.append({"id": int(time.time()*1000), "username": u["username"], "phone": VIP_TRANSFER_PHONE, "amount": amount, "days": amount, "note": note, "status": "pending", "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            save_vip_pays(pays)
            self.send_json({"ok": True, "msg": "已提交申请"}); return

        if self.path == "/api/vip/mylist":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            self.send_json({"ok": True, "data": u.get("vip_history", [])}); return

        if self.path == "/api/admin/vip/paylist":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            pays = load_vip_pays(); pays.reverse()
            self.send_json({"ok": True, "data": pays}); return

        if self.path == "/api/admin/vip/confirm":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            pid = body.get("id"); pays = load_vip_pays()
            for p in pays:
                if p["id"] == pid:
                    if p["status"] == "pending": p["status"] = "done"; grant_vip(p["username"], p["days"])
                    break
            save_vip_pays(pays); self.send_json({"ok": True}); return

        if self.path == "/api/admin/vip/grant":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            username = body.get("username","").strip(); days = int(body.get("days", 0)); vtype = body.get("type", "vip")
            if not get_user_by_name(username) or days <= 0: self.send_json({"ok": False}); return
            grant_vip(username, days, vtype); self.send_json({"ok": True}); return

        if self.path == "/api/feedback/submit":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            song_name = body.get("songName","").strip()
            if not song_name: self.send_json({"ok": False}); return
            fbs = load_feedbacks()
            fbs.append({"id": int(time.time()*1000), "username": u["username"], "songName": song_name, "note": body.get("note","").strip(), "status": "pending", "points": 0, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            save_feedbacks(fbs); self.send_json({"ok": True, "msg": "已提交"}); return

        if self.path == "/api/admin/feedback":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            fbs = load_feedbacks(); fbs.reverse()
            self.send_json({"ok": True, "data": fbs}); return

        if self.path == "/api/admin/feedback/batch_grant":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            ids = body.get("ids", []); action = body.get("action"); points = int(body.get("points", 0))
            fbs = load_feedbacks(); changed = False
            for f in fbs:
                if f["id"] in ids and f["status"] == "pending":
                    if action == "approve":
                        f["status"] = "approved"; f["points"] = points
                        add_points(f["username"], points, f"反馈奖励")
                    elif action == "reject": f["status"] = "rejected"
                    changed = True
            if changed: save_feedbacks(fbs)
            self.send_json({"ok": True}); return

        if self.path in ("/api/withdraw/apply", "/api/admin/withdraw", "/api/admin/withdraw/done"):
            self.send_json({"ok": False, "msg": "提现功能已关闭"}); return

        if self.path == "/api/message/mylist":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            msgs = [m for m in load_messages() if m["to"] == u["username"]]
            msgs.reverse(); self.send_json({"ok": True, "data": msgs}); return

        if self.path == "/api/message/read":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            ids = body.get("ids")
            mid = body.get("id")
            wanted = set(ids) if isinstance(ids, list) else ({mid} if mid else set())
            if not wanted:
                self.send_json({"ok": True, "count": 0}); return
            msgs = load_messages(); changed = 0
            for m in msgs:
                if m.get("id") in wanted and m["to"] == u["username"] and not m.get("read"):
                    m["read"] = True; changed += 1
            if changed: save_messages(msgs)
            self.send_json({"ok": True, "count": changed}); return

        if self.path == "/api/admin/userlist":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            self.send_json({"ok": True, "data": load_users()}); return

        if self.path == "/api/admin/toggle":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            users = load_users()
            for x in users:
                if x["username"] == body.get("username"): x["enable"] = body.get("enable", True)
            save_users(users); self.send_json({"ok": True}); return

        if self.path == "/api/pointlog/mylist":
            u = user_from_token(body.get("token",""))
            if not u: self.send_json({"ok": False}); return
            logs = [l for l in load_point_logs() if l["username"] == u["username"]]
            logs.reverse(); self.send_json({"ok": True, "data": logs[:200]}); return

        if self.path == "/api/admin/pointlog/all":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            logs = load_point_logs(); logs.reverse()
            self.send_json({"ok": True, "data": logs[:500]}); return

        if self.path == "/api/admin/songs/list":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            folders, songs, total = read_dir_content(MUSIC_ROOT, body.get("path", ""), 1, 1000)
            self.send_json({"ok": True, "folders": folders, "songs": songs, "path": body.get("path", "")}); return

        if self.path == "/api/admin/songs/delete":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            full_path = safe_path(MUSIC_ROOT, body.get("path", ""))
            if not full_path or not os.path.isfile(full_path): self.send_json({"ok": False}); return
            try:
                os.remove(full_path); refresh_all_songs_cache(); self.send_json({"ok": True})
            except Exception: self.send_json({"ok": False})
            return

        if self.path == "/api/admin/songs/move":
            u = user_from_token(body.get("token",""))
            if not u or u.get("role") != "admin": self.send_json({"ok": False}); return
            src = safe_path(MUSIC_ROOT, body.get("src", "")); dst_dir = safe_path(MUSIC_ROOT, body.get("dst", ""))
            if not src or not dst_dir: self.send_json({"ok": False}); return
            try:
                shutil.move(src, os.path.join(dst_dir, os.path.basename(src))); refresh_all_songs_cache(); self.send_json({"ok": True})
            except Exception: self.send_json({"ok": False})
            return

        if self.path == "/api/serverinfo":
            u = user_from_token(self._request_token(body))
            if not u: self.send_json({"ok": False, "msg": "请先登录"}); return
            lan_ip = get_local_ip()
            ok_disk, free_gb = _disk_usage_ok()
            self.send_json({
                "ok": True, "lan_ip": lan_ip, "port": PORT,
                "url": f"http://{lan_ip}:{PORT}",
                "current": self.headers.get("Host", ""),
                "free_gb": round(free_gb, 1), "disk_ok": ok_disk,
                "disk_warn_gb": SYSTEM_CONFIG.get("disk_warn_gb", 20),
                "disk_stop_gb": SYSTEM_CONFIG.get("disk_stop_gb", 5),
            })
            return

        self.send_json({"ok": False, "msg": "未知接口"})

    def _user_public(self, u):
        return {"username": u["username"], "role": u["role"], "points": u.get("points", 0), 
                "vip": is_vip(u), "vip_expire": u.get("vip_expire", 0), "vip_left": vip_left_seconds(u), 
                "dl_vip": is_download_vip(u), "dl_expire": u.get("download_expire", 0), "dl_left": dl_left_seconds(u),
                "avatar": u.get("avatar", ""), "purchased": u.get("purchased", [])}


# ====================== 登录/注册页 ======================
LOGIN_HTML = r'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Conan听歌系统 - 登录</title><style>@font-face{font-family:'ZuiMeiDeFengJing';src:url('/fonts/最美的风景.ttf') format('truetype');font-display:swap;}*{box-sizing:border-box;margin:0;padding:0;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}body{background:#1a1a2e;color:#fff;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif;padding:40px 20px}.login-box{max-width:420px;margin:0 auto;background:#27293d;padding:30px;border-radius:16px}h2{text-align:center;margin-bottom:24px}input{width:100%;padding:12px 14px;margin:10px 0;border:none;border-radius:10px;background:#1a1a2e;color:#fff;font-size:16px}button{width:100%;padding:13px;margin-top:10px;font-size:16px;cursor:pointer;font-family:inherit;font-weight:600;letter-spacing:.02em;border:1px solid rgba(255,255,255,.15)!important;border-radius:12px!important;background-image:linear-gradient(180deg,rgba(255,255,255,.2),rgba(255,255,255,.05) 42%,rgba(0,0,0,.18))!important;box-shadow:0 5px 18px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.24)!important;transition:transform .18s ease,filter .18s ease,box-shadow .18s ease!important}button:hover{transform:translateY(-1px);filter:brightness(1.09);box-shadow:0 8px 22px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.28)!important}button:active{transform:scale(.985);filter:brightness(.96)}.btn-login{background:#e94560;color:#fff}.btn-register{background:#2d3047;color:#dfe3ee}.msg{text-align:center;margin:12px 0;color:#ff7777;min-height:20px}</style></head><body><div class="login-box"><h2>🎵 Conan听歌系统</h2><div id="msg" class="msg"></div><input id="username" placeholder="用户名" autocomplete="username"><input id="pwd" type="password" placeholder="密码" autocomplete="current-password"><button class="btn-login" id="loginBtn">账号密码登录</button><button class="btn-register" id="regBtn">前往注册</button></div><script>document.getElementById("regBtn").onclick=function(){location.href="/register";};document.getElementById("loginBtn").onclick=async function(){var u=document.getElementById("username").value.trim();var p=document.getElementById("pwd").value;if(!u||!p){document.getElementById("msg").innerText="请输入用户名和密码";return;}this.innerText="登录中...";try{var res=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u,pwd:p})});var ret=await res.json();if(ret.ok){localStorage.setItem("conan_token",ret.token);location.href="/";}else{document.getElementById("msg").innerText=ret.msg||"登录失败";this.innerText="账号密码登录";}}catch(e){document.getElementById("msg").innerText="网络错误";this.innerText="账号密码登录";}};document.getElementById("pwd").addEventListener("keydown",function(e){if(e.key==="Enter")document.getElementById("loginBtn").click();});</script></body></html>'''

REGISTER_HTML = r'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>注册</title><style>@font-face{font-family:'ZuiMeiDeFengJing';src:url('/fonts/最美的风景.ttf') format('truetype');font-display:swap;}*{box-sizing:border-box;margin:0;padding:0;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}body{background:#1a1a2e;color:#fff;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif;padding:40px 20px}.box{max-width:420px;margin:0 auto;background:#27293d;padding:30px;border-radius:16px}h2{text-align:center;margin-bottom:24px}input{width:100%;padding:12px 14px;margin:10px 0;border:none;border-radius:10px;background:#1a1a2e;color:#fff;font-size:16px}button{width:100%;padding:13px;margin-top:10px;font-size:16px;cursor:pointer;font-family:inherit;font-weight:600;letter-spacing:.02em;border:1px solid rgba(255,255,255,.15)!important;border-radius:12px!important;background-color:#e94560;color:#fff;background-image:linear-gradient(180deg,rgba(255,255,255,.2),rgba(255,255,255,.05) 42%,rgba(0,0,0,.18))!important;box-shadow:0 5px 18px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.24)!important;transition:transform .18s ease,filter .18s ease,box-shadow .18s ease!important}button:hover{transform:translateY(-1px);filter:brightness(1.09);box-shadow:0 8px 22px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.28)!important}button:active{transform:scale(.985);filter:brightness(.96)}.msg{text-align:center;margin:12px 0;color:#ff7777;min-height:20px}.back{margin-top:14px;text-align:center;cursor:pointer;color:#8cf}</style></head><body><div class="box"><h2>注册账号</h2><div id="msg" class="msg"></div><input id="username" placeholder="用户名"><input id="pwd" type="password" placeholder="密码"><input id="pwd2" type="password" placeholder="确认密码"><button id="regBtn">注册</button><div class="back" onclick="location.href='/login'">返回登录页</div></div><script>document.getElementById("regBtn").onclick=async function(){var u=document.getElementById("username").value.trim();var p=document.getElementById("pwd").value;var p2=document.getElementById("pwd2").value;if(!u||!p){document.getElementById("msg").innerText="请填写完整";return;}if(p!==p2){document.getElementById("msg").innerText="两次密码不一致";return;}this.innerText="注册中...";try{var res=await fetch("/api/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u,pwd:p})});var ret=await res.json();document.getElementById("msg").innerText=ret.msg;if(ret.ok)setTimeout(function(){location.href="/login";},1200);else this.innerText="注册";}catch(e){document.getElementById("msg").innerText="网络错误";this.innerText="注册";}};</script></body></html>'''

# ====================== 管理员后台 ======================
ADMIN_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>管理员后台</title>
<style>
@font-face{font-family:'ZuiMeiDeFengJing';src:url('/fonts/最美的风景.ttf') format('truetype');font-display:swap;}
*{box-sizing:border-box;margin:0;padding:0;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}
body{background:#1a1a2e;color:#fff;padding:16px;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}
h2{text-align:center;margin:10px 0 16px}
.tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.tabs button{flex:1;padding:10px;background:#27293d;color:#fff;border:none;border-radius:10px;cursor:pointer;font-size:14px;min-width:80px}
.tabs button.active{background:#e94560}
.panel{display:none}
.panel.active{display:block}
table{width:100%;border-collapse:collapse;background:#27293d;border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:10px 8px;text-align:center;border-bottom:1px solid #1a1a2e}
.card{background:#27293d;border-radius:10px;padding:14px;margin:8px 0}
.card .row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}
.badge{padding:3px 8px;border-radius:6px;font-size:12px}
.badge.pending{background:#f0a030;color:#000}
.badge.approved,.badge.done{background:#07c160;color:#fff}
.badge.rejected{background:#e94560;color:#fff}
.badge.vip{background:#8e44ad;color:#fff}
.badge.dl{background:#f39c12;color:#fff}
button.act{padding:6px 10px;border:none;border-radius:6px;cursor:pointer;margin:2px;font-size:12px}
.act-ok{background:#07c160;color:#fff}
.act-no{background:#e94560;color:#fff}
.act-blue{background:#0f3460;color:#fff}
.act-purple{background:#8e44ad;color:#fff}
input.small{width:70px;padding:4px 6px;border:none;border-radius:6px;background:#1a1a2e;color:#fff;text-align:center}
select,textarea,input[type=text],input[type=number]{width:100%;padding:10px;border:none;border-radius:10px;background:#1a1a2e;color:#fff;margin-bottom:8px;font-size:14px}
.back{margin:16px 0;text-align:center}
.back button{padding:10px 20px;background:#383b58;color:#fff;border:none;border-radius:10px;cursor:pointer}
.reply-box{margin-top:8px;border-top:1px solid #1a1a2e;padding-top:8px;font-size:12px;color:#aaa}

/* 管理后台高级按钮 */
button{
  -webkit-appearance:none;appearance:none;font-family:inherit;font-weight:600;letter-spacing:.01em;
  border:1px solid rgba(255,255,255,.13)!important;border-radius:12px!important;
  background-image:linear-gradient(180deg,rgba(255,255,255,.17),rgba(255,255,255,.04) 42%,rgba(0,0,0,.18))!important;
  box-shadow:0 4px 14px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.18)!important;
  transition:transform .18s ease,filter .18s ease,box-shadow .18s ease!important;
}
button:hover{transform:translateY(-1px);filter:brightness(1.09);box-shadow:0 8px 20px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.24)!important}
button:active{transform:scale(.985);filter:brightness(.96)}
button:disabled{opacity:.5;filter:grayscale(.2);cursor:not-allowed;transform:none!important}
button.act{padding:7px 12px!important;border-radius:9px!important;font-size:12px!important;margin:3px}
.tabs{background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(0,0,0,.1));border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:6px;gap:6px;box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 8px 24px rgba(0,0,0,.18)}
.tabs button{
  background-color:transparent!important;background-image:none!important;border:1px solid transparent!important;
  box-shadow:none!important;border-radius:11px!important;color:#9aa0b5!important;padding:10px!important;
}
.tabs button:hover{color:#fff!important;background-color:rgba(255,255,255,.06)!important;transform:none;filter:none}
.tabs button.active{background-color:#e94560!important;background-image:linear-gradient(135deg,rgba(255,255,255,.24),transparent 48%,rgba(0,0,0,.16))!important;color:#fff!important;border-color:rgba(255,255,255,.2)!important;box-shadow:0 5px 16px rgba(0,0,0,.24),inset 0 1px 0 rgba(255,255,255,.28)!important}
.back button{padding:11px 22px!important;border-radius:999px!important;background-color:#2e3148!important;color:#fff}
</style></head><body>
<h2>👑 管理后台</h2>
<div class="tabs">
  <button class="active" onclick="showPanel('users',this)">👤 用户</button>
  <button onclick="showPanel('vip',this)">👑 会员</button>
  <button onclick="showPanel('feedback',this)">📝 反馈</button>
  <button onclick="showPanel('pointlog',this)">🧾 积分</button>
  <button onclick="showPanel('songs',this)">🎵 歌曲</button>
</div>

<div id="panel-users" class="panel active"></div>
<div id="panel-vip" class="panel"></div>
<div id="panel-feedback" class="panel"></div>
<div id="panel-pointlog" class="panel"></div>
<div id="panel-songs" class="panel"></div>

<div class="back"><button onclick="location.href='/'">返回播放器</button></div>
<script>
var token=localStorage.getItem("conan_token");
function showPanel(name,btn){
  document.querySelectorAll(".panel").forEach(p=>p.classList.remove("active"));
  document.getElementById("panel-"+name).classList.add("active");
  document.querySelectorAll(".tabs button").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  if(name==="songs" && !document.getElementById("song-current-path")) loadSongs("");
}
async function api(path,data){
  var res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Conan-Token":token||""},body:JSON.stringify(Object.assign({token:token},data||{}))});
  return res.json();
}
function fmtVip(user){
  let s = "";
  if(user.vip_expire>Date.now()/1000) s += '<span class="badge vip">听歌VIP</span> ';
  if(user.download_expire>Date.now()/1000) s += '<span class="badge dl">下载VIP</span>';
  return s || '<span style="color:#666">-</span>';
}

async function loadUsers(){
  var r=await api("/api/admin/userlist");
  if(!r.ok){location.href="/login";return;}
  var h="<table><tr><th>头像</th><th>用户名</th><th>积分</th><th>特权</th><th>操作</th></tr>";
  r.data.forEach(function(u){
    var av = u.avatar ? '<img src="'+u.avatar+'" style="width:24px;height:24px;border-radius:50%;object-fit:cover">' : '👤';
    h+="<tr><td>"+av+"</td><td>"+u.username+"</td><td>"+(u.points||0)+"</td><td>"+fmtVip(u)+"</td><td>";
    if(u.role!=="admin") h+='<button class="act act-no" onclick="toggleUser(\''+u.username+'\','+(!u.enable)+')">'+(u.enable?"禁用":"启用")+"</button>";
    h+="</td></tr>";
  });
  h+="</table>";
  document.getElementById("panel-users").innerHTML=h;
}
async function toggleUser(name,en){await api("/api/admin/toggle",{username:name,enable:en});loadUsers();}

async function loadPointLog(){
  var r=await api("/api/admin/pointlog/all");
  if(!r.ok)return;
  var h="<table><tr><th>时间</th><th>用户</th><th>变动</th><th>原因</th></tr>";
  r.data.forEach(function(l){
    var sign=l.delta>=0?"+":"-"; var color=l.delta>=0?"#0f0":"#f66";
    h+='<tr><td style="font-size:12px;color:#aaa">'+l.time+'</td><td>'+l.username+'</td><td style="color:'+color+'">'+sign+Math.abs(l.delta)+'</td><td style="font-size:12px">'+(l.reason||"")+'</td></tr>';
  });
  h+="</table>";
  document.getElementById("panel-pointlog").innerHTML=h;
}

async function loadVip(){
  var r=await api("/api/admin/userlist");
  if(!r.ok)return;
  var opts='<option value="">-- 选择用户 --</option>';
  r.data.forEach(function(u){ if(u.role!=="admin") opts+='<option value="'+u.username+'">'+u.username+'</option>'; });
  var box='<div class="card"><h3>开通特权</h3><select id="vipGrantUser">'+opts+'</select>'
    +'<select id="vipGrantType"><option value="vip">听歌会员</option><option value="download">下载会员</option></select>'
    +'<input type="number" id="vipGrantDays" placeholder="天数" min="1">'
    +'<button class="act act-purple" onclick="grantVip()">开通</button><div id="vipGrantMsg" style="margin-top:6px;color:#0f0;font-size:13px"></div></div>';
  var pr=await api("/api/admin/vip/paylist");
  box+='<h3 style="margin:14px 0 8px">💳 转账核实</h3>';
  pr.data.forEach(function(p){
    var badge=p.status==="pending"?'<span class="badge pending">待核实</span>':'<span class="badge done">已开通</span>';
    box+='<div class="card"><div class="row"><div><b>'+p.username+'</b> · '+(p.amount)+'元<br>凭证：'+(p.note||"")+'</div><div>'+badge;
    if(p.status==="pending") box+='<div style="margin-top:6px"><button class="act act-ok" onclick="confirmVip('+p.id+')">开通</button></div>';
    box+='</div></div></div>';
  });
  document.getElementById("panel-vip").innerHTML=box;
}
async function grantVip(){
  var u=document.getElementById("vipGrantUser").value; var d=parseInt(document.getElementById("vipGrantDays").value)||0;
  var t=document.getElementById("vipGrantType").value;
  if(!u||d<=0) return;
  var r=await api("/api/admin/vip/grant",{username:u,days:d,type:t});
  document.getElementById("vipGrantMsg").innerText=r.ok?"已开通":"失败";
}
async function confirmVip(id){await api("/api/admin/vip/confirm",{id:id});loadVip();}

let allFeedbacks = [];
async function loadFeedback(){
  var r=await api("/api/admin/feedback"); if(!r.ok)return;
  allFeedbacks = r.data; renderFeedbackList();
}
function renderFeedbackList(){
  var filter=document.getElementById("fbFilter")?document.getElementById("fbFilter").value:"all";
  var h='<div style="margin-bottom:10px;display:flex;gap:8px;flex-wrap:wrap"><select id="fbFilter" style="width:auto" onchange="renderFeedbackList()"><option value="all">全部</option><option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">已驳回</option></select><button class="act act-ok" onclick="batchFeedback(true)">批通过</button><button class="act act-no" onclick="batchFeedback(false)">批驳回</button></div>';
  var list=allFeedbacks.filter(f=>filter==="all"||f.status===filter);
  list.forEach(function(f){
    var badge='<span class="badge '+f.status+'">'+(f.status==="pending"?"待审":"已处理")+'</span>';
    h+='<div class="card"><div class="row"><div style="display:flex;gap:8px"><input type="checkbox" class="fb-check" value="'+f.id+'"><div><b>'+f.songName+'</b> ('+f.username+')</div></div><div>'+badge+'</div></div>';
    if(f.status==="pending") h+='<div style="margin-top:10px"><input class="small" id="pts_'+f.id+'" value="10">分 <button class="act act-ok" onclick="grantFb('+f.id+',true)">通过</button> <button class="act act-no" onclick="grantFb('+f.id+',false)">驳回</button></div>';
    h+="</div>";
  });
  document.getElementById("panel-feedback").innerHTML=h;
}
async function grantFb(id,approve){
  var pts=approve?parseInt(document.getElementById("pts_"+id).value)||0:0;
  await api("/api/admin/feedback/batch_grant",{ids:[id],action:approve?"approve":"reject",points:pts});
  loadFeedback();
}
async function batchFeedback(approve){
  var checks=document.querySelectorAll(".fb-check:checked"); if(!checks.length) return alert("请勾选");
  var ids=Array.from(checks).map(c=>parseInt(c.value)); var pts=0;
  if(approve){ var p=prompt("积分:", "10"); if(p===null)return; pts=parseInt(p)||0; }
  await api("/api/admin/feedback/batch_grant",{ids:ids,action:approve?"approve":"reject",points:pts}); loadFeedback();
}

async function loadWithdraw(){
  var r=await api("/api/admin/withdraw"); if(!r.ok)return;
  var h="";
  r.data.forEach(function(w){
    h+='<div class="card"><div class="row"><div><b>'+w.username+'</b> · '+w.points+'积分 (¥'+w.amount+')<br>手机:'+w.phone+'</div><div><span class="badge '+w.status+'">'+(w.status==="pending"?"待打款":"已打款")+'</span>';
    if(w.status==="pending") h+='<div style="margin-top:6px"><button class="act act-ok" onclick="doneWd('+w.id+')">打款</button></div>';
    h+='</div></div></div>';
  });
  document.getElementById("panel-withdraw").innerHTML=h;
}
async function doneWd(id){await api("/api/admin/withdraw/done",{id:id});loadWithdraw();}

let currentSongPath = "";
async function loadSongs(path){
  currentSongPath = path || "";
  var r=await api("/api/admin/songs/list",{path:currentSongPath}); if(!r.ok) return;
  var h = '<div class="card"><div class="row"><div>当前：' + (currentSongPath || "根目录") + '</div><div><button class="act act-blue" onclick="uploadSongFile()">📤 上传</button> <button class="act act-no" onclick="loadSongs(\'\')">回根</button></div></div>';
  h += '<div style="margin:10px 0">';
  r.folders.forEach(f => { var np = currentSongPath ? currentSongPath + "/" + f : f; h += '<button class="act act-ok" style="margin:4px" onclick="loadSongs(\''+np.replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">📁 '+f+'</button>'; });
  h += '</div><table><tr><th>文件名</th><th>操作</th></tr>';
  r.songs.forEach(s => {
    var rel = currentSongPath ? currentSongPath + "/" + s.file : s.file;
    h += '<tr><td style="text-align:left">🎵 ' + s.name + '</td><td><button class="act act-no" onclick="deleteSong(\''+rel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">删</button> <button class="act act-blue" onclick="moveSong(\''+rel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">移</button> <button class="act act-purple" onclick="setCopyright(\''+rel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\',\''+(s.copyright||'').replace(/'/g,"\\'")+'\')">版权</button></td></tr>';
  });
  h += '</table>';
  document.getElementById("panel-songs").innerHTML = h;
}
async function deleteSong(rel) { if(!confirm("确定删除？")) return; var r = await api("/api/admin/songs/delete", {path: rel}); if(r.ok) { alert("已删除"); loadSongs(currentSongPath); } }
async function moveSong(srcRel) { var dst = prompt("目标目录:"); if(!dst) return; var r = await api("/api/admin/songs/move", {src: srcRel, dst: dst}); if(r.ok) { alert("已移动"); loadSongs(currentSongPath); } }
async function setCopyright(rel, oldVal) { var cp = prompt("版权方:", oldVal); if(cp === null) return; var r = await api("/api/admin/copyright/set", {path: rel, copyright: cp}); if(r.ok) { loadSongs(currentSongPath); } }
function uploadSongFile() {
  var input = document.createElement('input'); input.type = 'file'; input.accept = 'audio/*';
  input.onchange = async function(e) {
    var file = e.target.files[0]; if(!file) return;
    var formData = new FormData(); formData.append("token", token); formData.append("target_dir", currentSongPath); formData.append("file", file);
    var xhr = new XMLHttpRequest(); xhr.open("POST", "/api/admin/songs/upload", true);
    xhr.onload = function() { if(xhr.status===200) { loadSongs(currentSongPath); } };
    xhr.send(formData);
  };
  input.click();
}
loadUsers();loadVip();loadFeedback();loadPointLog();
</script></body></html>'''

# ====================== 首页播放器 ======================
INDEX_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="theme-color" content="#1a1a2e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Conan听歌系统 · 最美的风景</title>
<style>
@font-face{font-family:'ZuiMeiDeFengJing';src:url('/fonts/最美的风景.ttf') format('truetype');font-display:swap;}
:root { --bg:#1a1a2e; --panel:#27293d; --text:#eee; --text-mute:#889; --accent:#e94560; --accent-glow:rgba(233,69,96,.28); --input-bg:#1a1a2e; --lyric-size:16px; --full-lyric-size:36px; }
[data-theme="light"] { --bg:#f4f4f9; --panel:#ffffff; --text:#333; --text-mute:#666; --accent:#e94560; --accent-glow:rgba(233,69,96,.22); --input-bg:#eef0f4; }
[data-theme="blue"] { --bg:#0a192f; --panel:#112240; --text:#ccd6f6; --text-mute:#8892b0; --accent:#64ffda; --accent-glow:rgba(100,255,218,.22); --input-bg:#0a192f; }
[data-theme="pink"] { --bg:#fff0f5; --panel:#ffe4e1; --text:#db7093; --text-mute:#c71585; --accent:#ff69b4; --accent-glow:rgba(255,105,180,.22); --input-bg:#fff5f8; }

*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}
input,select,textarea,button{font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}
body{background:var(--bg);color:var(--text);font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif;padding:14px 14px 266px;min-height:100vh;transition:background 0.3s;}
h1{text-align:center;margin-bottom:14px;font-size:26px;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi",serif;letter-spacing:.08em;}
.topbar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.topbar button{flex:1;padding:10px 0;background:var(--panel);color:var(--text);border:none;border-radius:10px;font-size:13px;cursor:pointer;min-width:80px;transition:0.3s;}
.topbar button.active{background:var(--accent);color:#fff;}
#searchBox{width:100%;padding:12px 14px;background:var(--panel);border:none;border-radius:10px;color:var(--text);font-size:15px;margin-bottom:12px;outline:none;}
.path-bar{padding:10px 12px;background:var(--panel);border-radius:8px;margin-bottom:10px;font-size:14px;color:var(--text-mute)}
.item{display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--panel);border-radius:10px;margin:8px 0;cursor:pointer;font-size:16px;transition:0.3s;}
.item .txt{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .heart{font-size:20px;color:var(--text-mute);flex-shrink:0;width:34px;text-align:center;cursor:pointer}
.item .heart.liked{color:var(--accent)}
.item .share,.item .dl,.item .addpl{font-size:18px;color:var(--text-mute);flex-shrink:0;width:34px;text-align:center;cursor:pointer;transition:color 0.2s;}
.item .share:hover,.item .dl:hover,.item .addpl:hover{color:var(--accent);}
.item .del-song{font-size:18px;color:var(--text-mute);flex-shrink:0;width:34px;text-align:center;cursor:pointer;transition:color 0.2s;opacity:.75}
.item .del-song:hover{color:#e94560;opacity:1}
.item.playing{background:var(--input-bg);border-left:3px solid var(--accent)}
.item.disabled{opacity:0.6;cursor:not-allowed}
.cover-img{width:44px;height:44px;border-radius:6px;object-fit:cover;flex-shrink:0;background:var(--input-bg)}
.sel-chk{width:18px;height:18px;flex-shrink:0;accent-color:var(--accent);cursor:pointer;margin:0 6px}
/* 列表独立滚动：列表再长也不会把歌词区挤出屏幕 */
#listBox{max-height:max(240px,calc(100vh - 560px));min-height:240px;overflow-y:auto;overflow-x:hidden;padding-right:6px;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.18) transparent;border-radius:10px}
#listBox::-webkit-scrollbar{width:8px}
#listBox::-webkit-scrollbar-thumb{background:rgba(255,255,255,.18);border-radius:8px}
#listBox::-webkit-scrollbar-track{background:transparent}
.control-wrap{position:fixed;left:0;bottom:0;width:100%;background:var(--panel);padding:12px 16px 18px;border-top:1px solid var(--input-bg);z-index:999;transition:0.3s;}
.control-bar{display:flex;gap:8px;justify-content:center;margin-top:10px;flex-wrap:wrap}
.control-btn{flex:1;min-width:80px;padding:12px 6px;background:var(--input-bg);color:var(--text);border:none;border-radius:10px;font-size:14px;cursor:pointer}
.mode-btn{flex:1;padding:12px 6px;background:#0f3460;color:#fff;border:none;border-radius:10px;font-size:13px;cursor:pointer}
#broadcastBar.bc-collapsed #broadcastText{max-height:3.4em;overflow:hidden;-webkit-mask-image:linear-gradient(to bottom,#000 58%,transparent);mask-image:linear-gradient(to bottom,#000 58%,transparent)}
.empty{text-align:center;color:var(--text-mute);padding:40px 0;font-size:14px}

.drag-handle { cursor: move; width: 40px; height: 6px; background: var(--text-mute); border-radius: 3px; margin: 0 auto 8px auto; opacity: 0.5; }
#lyricBox{background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(0,0,0,.08)),var(--input-bg);border-radius:14px;margin:12px 0;padding:16px;max-height:200px;overflow:hidden;position:relative;mask-image:linear-gradient(to bottom,transparent 0,#000 15%,#000 85%,transparent 100%);-webkit-mask-image:linear-gradient(to bottom,transparent 0,#000 15%,#000 85%,transparent 100%)}
#lyricContent{padding:100px 0 140px;transition:transform .38s cubic-bezier(.2,.8,.3,1);will-change:transform;position:relative}
.lyric-line{margin:12px 0;font-size:var(--lyric-size);text-align:center;color:var(--text-mute);transition:opacity .3s ease,color .3s ease,transform .3s ease;cursor:pointer;position:relative;transform-origin:center;max-width:100%;line-height:1.7;overflow-wrap:anywhere;word-break:break-word;user-select:none;-webkit-user-select:none}
.lyric-line .lword{display:inline-block;white-space:pre-wrap;opacity:.55;position:relative;transition:color .16s ease,opacity .16s ease,text-shadow .16s ease}
.lyric-line.active .lword{opacity:.78}
.lyric-line .lword.sung{color:#f4f6ff;opacity:.96}
.lyric-line .lword.hit{color:#ffd166;opacity:1;text-shadow:0 0 10px rgba(255,209,102,.95),0 0 22px rgba(233,69,96,.7)}
.lyric-line.past{color:#dfe5f5;opacity:.82}
#fullLyricContent .lyric-line.past{color:#ccd2ea;opacity:.88}
.lyric-line .lword.hit::after{content:"✦";position:absolute;left:50%;top:-.8em;color:#ffe082;font-size:calc(.72em * var(--star-scale,1));line-height:1;pointer-events:none;animation:lyricStar var(--star-dur,.6s) ease-out 1 forwards}
@keyframes lyricStar{0%{opacity:0;transform:translate(-50%,6px) scale(.55) rotate(-8deg)}30%{opacity:1;transform:translate(-50%,-2px) scale(1.08) rotate(5deg)}68%{opacity:.78;transform:translate(-50%,-9px) scale(1.16) rotate(13deg)}100%{opacity:0;transform:translate(-50%,-18px) scale(1.28) rotate(20deg)}}
/* 译文 / 音译 小字行（外语歌词自动出现） */
.lyric-sub{display:block;font-size:.62em;line-height:1.45;margin-top:3px;font-weight:400;letter-spacing:.01em;color:var(--lyric-trans-color,#9fb0d0);opacity:.92;transition:color .3s,opacity .3s}
.lyric-sub.lyric-phon{font-size:.56em;color:var(--lyric-phon-color,#7d8aa8);opacity:.85;font-style:italic}
#fullLyricContent .lyric-sub{color:rgba(255,255,255,.66)}
#fullLyricContent .lyric-sub.lyric-phon{color:rgba(255,255,255,.46)}
.lyric-line.active .lyric-sub{color:#ffe6a6}
.lyric-line.active .lyric-sub.lyric-phon{color:rgba(255,230,166,.72)}
.artist-icon{border-radius:50%!important;object-fit:cover;box-shadow:0 0 0 2px rgba(255,255,255,.12)}
/* 歌词字体/颜色自定义（在歌词工具里修改，默认值与原配色一致） */
#lyricContent,#fullLyricContent{font-family:var(--lyric-font,'ZuiMeiDeFengJing','PingFang SC','Microsoft YaHei',sans-serif)}
.lyric-line{color:var(--lyric-future-color,var(--text-mute))}
#fullLyricContent .lyric-line{color:var(--lyric-future-color,#444)}
.lyric-line .lword.sung{color:var(--lyric-sung-color,#f4f6ff)}
.lyric-line.past{color:var(--lyric-sung-color,#dfe5f5)}
#fullLyricContent .lyric-line.past{color:var(--lyric-sung-color,#ccd2ea)}
.lyric-line .lword.hit{color:var(--lyric-hit-color,#ffd166);text-shadow:0 0 10px var(--lyric-hit-color,rgba(255,209,102,.95)),0 0 22px rgba(233,69,96,.7)}
.lyric-line.active{color:var(--lyric-hit-color,var(--accent))}
#fullLyricContent .lyric-line.active{color:var(--lyric-hit-color,#fff)}
#lyricToolModal input[type=color]{width:38px;height:28px;padding:0;margin:0;border:1px solid rgba(255,255,255,.15);border-radius:8px;background:transparent;cursor:pointer}
.lyric-line.active{color:var(--accent);font-weight:700;text-shadow:0 0 14px rgba(233,69,96,.28)}
#lyricBox.lyric-focus .lyric-line:not(.active){opacity:0.18}
#lyricBox.lyric-focus .lyric-line.active{text-shadow:0 0 18px rgba(233,69,96,0.55)}
#lyricBox .lyric-empty{color:var(--text-mute);text-align:center;padding:30px 0;font-size:14px}
#fullScreenWrap{display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:#000;z-index:999999;overflow-y:auto}
#fullBg{position:fixed;inset:0;background-size:cover;background-position:center;filter:blur(36px) brightness(.28) saturate(.9);transform:scale(1.12);opacity:.9;pointer-events:none;z-index:0;transition:opacity .35s,filter .35s}
#fullScreenWrap.bg-pure #fullBg{opacity:0}
#fullScreenWrap.bg-none #fullBg{opacity:0}
#fullScreenWrap::after{content:"";position:fixed;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.28),rgba(0,0,0,.18) 35%,rgba(0,0,0,.55));pointer-events:none;z-index:1}
#fullScreenWrap .close-full{position:fixed;top:16px;right:16px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:20px;padding:10px 18px;cursor:pointer;z-index:1000000}
#fullBgModeBtn{position:fixed;top:16px;right:116px;background:rgba(255,255,255,.12);color:#fff;border:none;border-radius:20px;padding:10px 14px;cursor:pointer;z-index:1000000;font-size:12px}
#fullLyricInner{position:relative;z-index:2;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 30px}
#fullLyricContent{width:100%;text-align:center;padding:34vh 0 46vh;user-select:none;-webkit-user-select:none}
#fullLyricContent .lyric-line{margin:20px 0;font-size:var(--full-lyric-size);color:#444;transition:opacity .32s ease,color .32s ease,transform .32s ease;cursor:pointer;transform-origin:center;max-width:min(1100px,94vw);line-height:1.65;overflow-wrap:anywhere;word-break:break-word;user-select:none}
#fullLyricContent .lyric-line.active{color:#fff;font-weight:bold;transform:scale(1.05);text-shadow:0 0 24px rgba(255,255,255,.18)}

.user-bar{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--panel);border-radius:10px;margin-bottom:10px;flex-wrap:wrap;gap:6px;}
.user-bar .right{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.user-bar button{padding:6px 10px;border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:12px}
.avatar-sm { width: 32px; height: 32px; border-radius: 50%; object-fit: cover; background: #333; cursor: pointer; vertical-align: middle; margin-right: 8px;}
.points-tag{color:#ffd700;font-weight:bold}
.vip-tag{background:#8e44ad;border-radius:8px;padding:2px 8px;font-size:11px;color:#fff}
.dl-tag{background:#f39c12;border-radius:8px;padding:2px 8px;font-size:11px;color:#fff}
.cp-tag{background:#07c160;color:#fff;font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px;white-space:nowrap;}

.modal{display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:radial-gradient(ellipse at 50% 0%,rgba(233,69,96,.14),transparent 45%),rgba(4,6,13,.78);backdrop-filter:blur(16px) saturate(1.2);-webkit-backdrop-filter:blur(16px) saturate(1.2);z-index:999998;align-items:center;justify-content:center;padding:20px;animation:modalFade .22s ease}
.modal .box{position:relative;background:linear-gradient(145deg,rgba(36,39,59,.98),rgba(12,14,26,.99));border:1px solid rgba(255,255,255,.13);border-radius:24px;padding:26px 26px 22px;max-width:460px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:0 30px 90px rgba(0,0,0,.68),0 0 0 1px rgba(233,69,96,.12),inset 0 1px 0 rgba(255,255,255,.16);animation:modalPop .28s cubic-bezier(.2,.8,.3,1)}
.modal .box::before{content:"";position:absolute;left:18%;right:18%;top:-1px;height:1px;background:linear-gradient(90deg,transparent,rgba(233,69,96,.85),rgba(255,209,102,.6),transparent);pointer-events:none}
.modal h3{margin-bottom:16px;text-align:center;font-size:22px;letter-spacing:.04em;text-shadow:0 2px 10px rgba(0,0,0,.35)}
.modal-kicker{text-align:center;color:var(--accent);font-size:10px;letter-spacing:.3em;font-weight:700;margin-bottom:5px;opacity:.9}
.modal .modal-x{position:absolute;top:12px;right:12px;width:32px;height:32px;padding:0;border-radius:50%!important;background:rgba(255,255,255,.07)!important;color:rgba(255,255,255,.75);font-size:15px;cursor:pointer;z-index:2}
.modal .modal-x:hover{background:rgba(233,69,96,.25)!important;color:#fff}
.modal input,.modal textarea,.modal select{width:100%;padding:12px;margin:6px 0;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(0,0,0,.28);color:var(--text);font-size:15px;transition:border-color .18s ease,box-shadow .18s ease,background .18s ease}
.modal input:focus,.modal textarea:focus,.modal select:focus{outline:none;border-color:rgba(233,69,96,.7);box-shadow:0 0 0 3px rgba(233,69,96,.13);background:rgba(0,0,0,.38)}
.modal .row{display:flex;gap:8px;margin-top:10px}
.modal .row button{flex:1;padding:12px;border:none;border-radius:10px;cursor:pointer;font-size:15px;color:#fff}
.modal-footer{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.modal-footer button{flex:1;min-width:130px;padding:12px 14px;border:none;border-radius:12px;cursor:pointer;color:#fff;font-size:14px}
.lyric-tool-song{color:#aeb3c8;font-size:13px;text-align:center;margin:-4px auto 16px;padding:8px 12px;border-radius:10px;background:rgba(0,0,0,.24);max-width:90%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lyric-tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}
.lyric-tool-grid button{padding:12px 10px;border:none;border-radius:12px;cursor:pointer;color:#fff;font-size:14px}
.lyric-tool-tip{color:#8f96ad;font-size:12px;line-height:1.6;margin:12px 2px 10px;padding-left:10px;border-left:2px solid rgba(233,69,96,.55)}
.lyric-upgrade-status{margin-top:12px;padding:10px 12px;border-radius:12px;background:rgba(0,0,0,.24);border:1px solid rgba(255,255,255,.07)}
.lyric-upgrade-head{display:flex;align-items:center;justify-content:space-between;gap:10px;color:#b9c0d4;font-size:12px;margin-bottom:8px}
.lyric-upgrade-track{height:7px;background:rgba(0,0,0,.4);border-radius:999px;overflow:hidden}
#lyricUpgradeFill{height:100%;width:0%;background:linear-gradient(90deg,var(--accent),#ffd166);border-radius:999px;transition:width .25s ease}
.confirm-text{color:#aeb3c8;font-size:14px;line-height:1.75;text-align:center;margin:2px 4px 6px}
.lyric-import-box{min-height:170px;resize:vertical;line-height:1.65}
@keyframes modalFade{from{opacity:0}to{opacity:1}}
@keyframes modalPop{from{opacity:0;transform:translateY(16px) scale(.96)}to{opacity:1;transform:translateY(0) scale(1)}}
@media (max-width:640px){.modal{padding:12px}.modal .box{padding:22px 18px 18px;border-radius:20px}.lyric-tool-grid{grid-template-columns:repeat(2,1fr)}.lyric-tool-grid button{font-size:13px;padding:11px 6px}.modal-footer button{min-width:110px}}
.hint{background:rgba(255,215,0,0.1);border:1px solid #ffd700;border-radius:10px;padding:12px;margin:10px 0;font-size:13px;color:#ffd700;}
.record-item{background:var(--input-bg);border-radius:8px;padding:10px;margin:6px 0;font-size:13px}

#progressWrap{position:relative;margin:0 4px}
#progressBar{position:relative;width:100%;height:8px;background:var(--input-bg);border-radius:4px;cursor:pointer;touch-action:none;}
#bufferFill{position:absolute;left:0;top:0;height:100%;background:rgba(255,255,255,0.1);border-radius:4px;width:0%;transition:width .2s}
#progressFill{position:absolute;left:0;top:0;height:100%;background:var(--accent);border-radius:4px;width:0%;}
#progressThumb{position:absolute;top:50%;left:0%;transform:translate(-50%,-50%);width:14px;height:14px;background:#fff;border-radius:50%;box-shadow:0 0 4px rgba(0,0,0,.6)}
#progressTooltip{position:absolute;top:-32px;left:0;background:rgba(0,0,0,0.88);padding:3px 8px;border-radius:5px;font-size:12px;color:#fff;display:none;transform:translateX(-50%);white-space:nowrap;z-index:3;}

#loadingOverlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.45);z-index:9999999;align-items:center;justify-content:center;flex-direction:column;gap:14px}
.spin-box svg{width:80px;height:80px;}

@media (max-width: 640px){
  body{padding-bottom:230px}
  .control-btn{padding:16px 8px;font-size:15px}
  .item{padding:12px 14px;font-size:17px}
  .item .heart,.item .share,.item .dl,.item .addpl{font-size:24px;width:40px}
}
.dl-btn{padding:6px 10px;border:none;border-radius:8px;background:#07c160;color:#fff;cursor:pointer;font-size:12px;flex-shrink:0;margin-left:8px}
.dl-btn:hover{filter:brightness(1.08)}
.dl-btn:disabled{background:#555;color:#bbb;cursor:not-allowed}
.dl-bar{background:rgba(7,193,96,0.12);border:1px solid #07c160;border-radius:10px;padding:10px 12px;margin:10px 0;font-size:13px;color:var(--text)}
.dl-bar .track{height:6px;background:var(--input-bg);border-radius:3px;margin-top:8px;overflow:hidden}
.dl-bar .fill{height:100%;width:0;background:#07c160;transition:width .3s}
.engine-warn{background:rgba(233,69,96,0.12);border:1px solid #e94560;color:#e94560;border-radius:10px;padding:10px 12px;margin:8px 0;font-size:13px;line-height:1.6}
.dl-mini{font-size:11px;color:#888;margin-left:6px}

/* 播放器增强：音量 / 队列 / M3U / 手机端 / 下载任务 */
#progressBar{height:10px}
#progressBar:active #progressThumb{transform:translate(-50%,-50%) scale(1.25)}
.volume-wrap{display:flex;align-items:center;gap:6px;flex:0 0 150px;min-width:120px;padding:0 4px}
#volumeSlider{width:100%;accent-color:var(--accent);cursor:pointer}
#muteBtn{background:var(--input-bg);color:var(--text);border:none;border-radius:8px;padding:8px 10px;cursor:pointer;font-size:16px;flex-shrink:0}
.queue-list{max-height:55vh;overflow-y:auto}
.queue-item{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;cursor:pointer;font-size:13px;margin:4px 0;background:var(--input-bg)}
.queue-item.playing{border-left:3px solid var(--accent);background:rgba(233,69,96,0.12)}
.queue-item .idx{color:var(--text-mute);width:26px;text-align:right;flex-shrink:0}
.queue-item .qname{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#fullSongTitle{position:fixed;top:18px;left:20px;color:#fff;font-size:16px;max-width:70vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;z-index:1000000;text-shadow:0 2px 6px rgba(0,0,0,.6)}
#m3uBtn{padding:10px 14px;background:#0f3460;color:#fff;border:none;border-radius:10px;cursor:pointer;white-space:nowrap}
.task-card{background:var(--input-bg);border-radius:10px;padding:10px;margin:8px 0;font-size:12px}
.task-head{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.task-status{font-size:11px;padding:2px 8px;border-radius:6px;background:#555;color:#fff;white-space:nowrap}
.task-status.running,.task-status.queued{background:#3498db}
.task-status.done{background:#07c160}
.task-status.error{background:#e94560}
.task-status.paused{background:#f39c12}
.task-status.cancelled{background:#777}
.task-track{height:6px;background:#111;border-radius:3px;margin:8px 0;overflow:hidden}
.task-fill{height:100%;background:#07c160;transition:width .3s}
.task-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.task-actions button{padding:5px 9px;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:11px}
.task-items{margin-top:6px;color:var(--text-mute);line-height:1.6;max-height:120px;overflow-y:auto}
.task-items .ok{color:#07c160}
.task-items .bad{color:#e94560}

@media (max-width: 640px){
  body{padding:10px 10px calc(330px + env(safe-area-inset-bottom))}
  .control-wrap{left:0!important;width:100%!important;min-width:0!important;bottom:0!important;padding:10px 10px calc(14px + env(safe-area-inset-bottom))}
  .drag-handle{display:none}
  .topbar{flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;gap:6px;padding-bottom:4px;scrollbar-width:none;-webkit-overflow-scrolling:touch}
  .topbar::-webkit-scrollbar{display:none}
  .topbar button{flex:0 0 auto;min-width:86px;font-size:12px;padding:9px 8px}
  .control-bar{gap:6px}
  .control-btn{min-width:0;font-size:13px;padding:12px 4px}
  .mode-btn{font-size:12px}
  /* 手机端：上一首/播放/下一首一行，模式/列表/歌词工具收成第二行小胶囊 */
  .volume-wrap{display:none!important}
  .control-bar{display:grid!important;grid-template-columns:repeat(3,1fr);gap:6px}
  .control-btn{min-width:0!important;flex:0 0 auto!important;padding:13px 4px;font-size:14px}
  .mode-btn,#queueBtn,#lyricToolBtn{min-width:0!important;flex:0 0 auto!important;padding:9px 4px!important;font-size:12px!important;border-radius:10px;opacity:.92}
  #searchTabs{flex-wrap:nowrap!important;overflow-x:auto;overflow-y:hidden;gap:5px;scrollbar-width:none;-webkit-overflow-scrolling:touch}
  #searchTabs::-webkit-scrollbar{display:none}
  #searchTabs button{flex:0 0 auto;white-space:nowrap}
  .volume-wrap{flex:1 0 100%;order:5;margin-top:6px;min-width:0}
  #lyricBox{max-height:150px}
  #listBox{max-height:max(180px,calc(100vh - 560px));min-height:180px}
  #fullSongTitle{top:12px;left:12px;font-size:14px;max-width:68vw}
  .item{padding:11px 10px}
  .item .heart,.item .share,.item .dl,.item .addpl{font-size:22px;width:34px}
  .dl-btn{font-size:11px;padding:5px 7px;margin-left:4px}
}

/* 主区域：默认上下排（窄屏/手机），宽屏改左右并排 */
#mainArea{display:flex;flex-direction:column;gap:8px}
#mainListCol{display:flex;flex-direction:column;min-width:0}
#lyricCol{display:flex;flex-direction:column;gap:6px;min-width:0}

/* 桌面端：整页锁定成一屏，左侧列表内部滚动，右侧歌词常驻可见 */
@media (min-width: 1100px) and (min-height: 780px){
  body{height:100vh;overflow:hidden;display:flex;flex-direction:column;padding:12px 14px 205px}
  body > *:not(#mainArea){flex:0 0 auto}
  #mainArea{flex:1 1 auto;min-height:0;flex-direction:row;gap:12px}
  #mainListCol{flex:1 1 auto;min-height:0}
  #listBox{flex:1 1 auto;min-height:0;max-height:none}
  #lyricCol{flex:0 0 clamp(300px,30%,440px);min-height:0}
  #lyricBox{flex:1 1 auto;max-height:none;display:flex;flex-direction:column;justify-content:center}
  h1{font-size:20px;margin:2px 0 0}
  .subtitle{font-size:14px;margin:-2px 0 8px}
  #broadcastBar{margin-bottom:8px;padding:9px 14px}
  .topbar{margin-bottom:8px;gap:6px}
  .topbar button{padding:7px 0;font-size:12px;min-width:72px}
  #searchBox{padding:9px 12px;margin-bottom:8px}
  #searchTabs{margin:-2px 0 6px}
  .path-bar{padding:7px 10px;margin-bottom:6px}
}

/* ==================== 高级按钮系统（重做） ==================== */
button{
  -webkit-appearance:none;appearance:none;
  font-family:inherit;line-height:1.35;text-shadow:0 1px 1px rgba(0,0,0,.22);
}
button:not(.close-full){
  border:1px solid rgba(255,255,255,.13)!important;
  border-radius:12px!important;
  background-image:linear-gradient(180deg,rgba(255,255,255,.17) 0%,rgba(255,255,255,.045) 40%,rgba(255,255,255,0) 55%,rgba(0,0,0,.18) 100%)!important;
  box-shadow:0 4px 14px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.18),inset 0 -1px 0 rgba(0,0,0,.18)!important;
  font-weight:600;letter-spacing:.01em;
  transition:transform .18s cubic-bezier(.2,.8,.3,1),filter .18s ease,box-shadow .18s ease,border-color .18s ease!important;
}
button:not(.close-full):hover:not(:disabled){
  transform:translateY(-1px);
  filter:brightness(1.09) saturate(1.03);
  box-shadow:0 8px 22px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.26),0 0 0 1px rgba(255,255,255,.06)!important;
}
button:not(.close-full):active:not(:disabled){
  transform:translateY(0) scale(.985);
  filter:brightness(.96);
  box-shadow:0 2px 7px rgba(0,0,0,.24),inset 0 2px 5px rgba(0,0,0,.2)!important;
}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button:disabled{opacity:.5;filter:grayscale(.25);cursor:not-allowed;transform:none!important}

/* 顶部工具栏：一体式分段控制 */
.topbar{
  position:sticky;top:0;z-index:80;
  background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(0,0,0,.1)),var(--panel);
  border:1px solid rgba(255,255,255,.08);
  border-radius:16px;padding:6px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 10px 28px rgba(0,0,0,.18);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
}
.topbar button{
  background-color:transparent!important;background-image:none!important;
  border:1px solid transparent!important;box-shadow:none!important;
  color:var(--text-mute)!important;font-size:12.5px!important;font-weight:600;
  border-radius:11px!important;padding:10px 7px!important;
}
.topbar button:hover:not(.active){
  background-color:rgba(255,255,255,.06)!important;
  border-color:rgba(255,255,255,.1)!important;
  color:var(--text)!important;transform:none;filter:none;
  box-shadow:none!important;
}
.topbar button.active{
  background-color:var(--accent)!important;
  background-image:linear-gradient(135deg,rgba(255,255,255,.24),rgba(255,255,255,.04) 45%,rgba(0,0,0,.16))!important;
  border-color:rgba(255,255,255,.2)!important;
  color:#fff!important;
  box-shadow:0 5px 18px var(--accent-glow),inset 0 1px 0 rgba(255,255,255,.28)!important;
}

/* 用户栏：低调玻璃胶囊，按功能保留一点色相 */
.user-bar{
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(0,0,0,.08)),var(--panel);
  border:1px solid rgba(255,255,255,.07);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 8px 22px rgba(0,0,0,.14);
}
.user-bar button{
  border-radius:999px!important;padding:7px 12px!important;
  background-color:rgba(255,255,255,.055)!important;
  background-image:linear-gradient(180deg,rgba(255,255,255,.12),rgba(0,0,0,.12))!important;
  border:1px solid rgba(255,255,255,.09)!important;
  color:var(--text)!important;font-size:12px!important;
  box-shadow:0 2px 8px rgba(0,0,0,.16),inset 0 1px 0 rgba(255,255,255,.12)!important;
}
.user-bar button:hover{color:#fff!important;background-color:rgba(255,255,255,.1)!important;border-color:rgba(255,255,255,.2)!important}
#onlineBtn{border-color:rgba(52,152,219,.4)!important;color:#9edcff!important}
#msgBtn{border-color:rgba(155,89,182,.42)!important;color:#dabcff!important}
#pointsBtn{border-color:rgba(240,160,48,.42)!important;color:#ffd28f!important}
#feedbackBtn{border-color:rgba(120,144,156,.4)!important;color:#cfd8dc!important}
#withdrawBtn{border-color:rgba(7,193,96,.38)!important;color:#96efc0!important}
#vipBtn{border-color:rgba(255,200,87,.42)!important;color:#ffe3a1!important}
#adminBtn{border-color:rgba(233,69,96,.45)!important;color:#ffb5c1!important}
#settingsBtn{border-color:rgba(255,255,255,.16)!important;color:#dfe3ee!important}
#mobileBtn{border-color:rgba(52,152,219,.32)!important;color:#b9d9ff!important}
#logoutBtn{border-color:rgba(255,255,255,.08)!important;color:#9aa0b5!important}

/* 播放器控制区：深色玻璃 + 唯一主按钮 */
.control-wrap{
  background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(0,0,0,.1)),var(--panel);
  border-top:1px solid rgba(255,255,255,.08);
  box-shadow:0 -10px 30px rgba(0,0,0,.25);backdrop-filter:blur(18px);
}
.control-bar{gap:10px}
.control-btn,.mode-btn{
  background-color:rgba(255,255,255,.055)!important;
  background-image:linear-gradient(180deg,rgba(255,255,255,.1),rgba(0,0,0,.1))!important;
  color:var(--text)!important;
  border:1px solid rgba(255,255,255,.09)!important;
  box-shadow:0 3px 10px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.1)!important;
  border-radius:12px!important;
}
[data-theme="light"] .control-btn,[data-theme="light"] .mode-btn{
  background-color:rgba(0,0,0,.035)!important;
  background-image:linear-gradient(180deg,rgba(255,255,255,.55),rgba(0,0,0,.025))!important;
  border-color:rgba(0,0,0,.1)!important;color:#333!important;
}
[data-theme="light"] .user-bar button{
  background-color:rgba(0,0,0,.035)!important;
  background-image:linear-gradient(180deg,rgba(255,255,255,.6),rgba(0,0,0,.03))!important;
  border-color:rgba(0,0,0,.1)!important;color:#333!important;
}
[data-theme="light"] button{text-shadow:none}
.control-btn:hover,.mode-btn:hover{
  background-color:rgba(255,255,255,.09)!important;
  border-color:rgba(255,255,255,.2)!important;
}
#playBtn{
  background-color:var(--accent)!important;
  background-image:linear-gradient(135deg,rgba(255,255,255,.24),transparent 44%,rgba(0,0,0,.16))!important;
  border-color:rgba(255,255,255,.24)!important;
  color:#fff!important;font-weight:700;
  box-shadow:0 7px 24px var(--accent-glow),inset 0 1px 0 rgba(255,255,255,.3)!important;
}
#muteBtn{
  background-color:rgba(255,255,255,.05)!important;
  border:1px solid rgba(255,255,255,.1)!important;
  border-radius:12px!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.1)!important;
}

/* 任务操作按钮：小尺寸也保持精致 */
.task-actions button{
  border-radius:9px!important;
  padding:6px 10px!important;
  font-size:11.5px!important;font-weight:600;
  letter-spacing:.02em;
  box-shadow:0 2px 8px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.16)!important;
}

/* 下载 / 播放小胶囊 */
.dl-btn{border-radius:999px!important;padding:6px 12px!important;font-weight:700;letter-spacing:.02em}
.dl-btn:hover{box-shadow:0 4px 14px rgba(0,0,0,.24),inset 0 1px 0 rgba(255,255,255,.24)!important}
#m3uBtn{border-radius:12px!important;border:1px solid rgba(255,255,255,.12)!important;box-shadow:0 3px 10px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.12)!important}
#backBtn{border-radius:12px!important;box-shadow:0 4px 14px rgba(0,0,0,.2),inset 0 1px 0 rgba(255,255,255,.22)!important}

/* 全屏退出：磨砂胶囊 */
.close-full{
  background:rgba(255,255,255,.1)!important;
  border:1px solid rgba(255,255,255,.2)!important;
  border-radius:999px!important;
  box-shadow:0 4px 18px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.18)!important;
  backdrop-filter:blur(14px);
  font-weight:600;transition:.2s;
}
.close-full:hover{background:rgba(255,255,255,.18)!important;transform:translateY(-1px)}

/* 弹窗主操作 */
.modal .row button{border-radius:12px!important;font-weight:600;letter-spacing:.02em}

/* ==================== 底部「正在播放」歌曲栏（酷狗式） ==================== */
#npBar{display:flex;align-items:center;gap:10px;padding:2px 2px 9px}
#npCoverWrap{width:46px;height:46px;border-radius:10px;overflow:hidden;background:var(--input-bg);position:relative;flex-shrink:0;display:flex;align-items:center;justify-content:center}
#npCover{width:100%;height:100%;object-fit:cover;display:none}
#npCoverPh{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:20px;color:var(--text-mute);pointer-events:none}
#npMeta{flex:1;min-width:0}
#npTitle{font-size:14px;line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#npSinger{font-size:11px;color:var(--text-mute);line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
#npActions{display:flex;gap:6px;flex-shrink:0}
#npActions button{padding:8px 11px;background:var(--input-bg);color:var(--text);border:none;border-radius:9px;font-size:13px;cursor:pointer;line-height:1}
#npActions button:active{background:var(--accent);color:#fff}

/* ==================== 移动端（手机 / 平板）底部导航 ==================== */
#mobileNav,#moreSheet,#moreMask{display:none}
html.is-mobile #mobileNav{
  display:flex;position:fixed;left:0;right:0;bottom:0;height:auto;min-height:58px;z-index:1001;
  background:linear-gradient(180deg,rgba(255,255,255,.045),transparent),var(--panel);
  border-top:1px solid var(--input-bg);align-items:stretch;
  /* 底部安全区：优先用系统 env，否则用 JS 算出的 --nav-safe，最后兜底 14px */
  padding:0 0 calc(14px + var(--nav-safe, env(safe-area-inset-bottom, 0px)));
}
html.is-mobile #mobileNav .nav-btn{
  flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
  background:transparent;border:none;color:var(--text-mute);cursor:pointer;padding:6px 2px;min-width:0;
}
html.is-mobile #mobileNav .nav-btn em{font-style:normal;font-size:19px;line-height:1}
html.is-mobile #mobileNav .nav-btn span{font-size:11px;letter-spacing:.04em;white-space:nowrap}
html.is-mobile #mobileNav .nav-btn.active{color:var(--accent)}
html.is-mobile #moreMask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1000}
html.is-mobile #moreMask.show{display:block}
html.is-mobile #moreSheet{
  display:none;position:fixed;left:0;right:0;bottom:calc(72px + var(--nav-safe, env(safe-area-inset-bottom, 0px)));z-index:1002;
  background:var(--panel);border-top-left-radius:16px;border-top-right-radius:16px;
  padding:14px 12px 16px;box-shadow:0 -8px 30px rgba(0,0,0,.45);max-height:66vh;overflow-y:auto;
}
html.is-mobile #moreSheet.show{display:block}
#moreSheet .ms-title{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--text-mute);margin:0 4px 10px}
#moreGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
#moreGrid button{padding:13px 6px;border:none;border-radius:12px;background:var(--input-bg);color:var(--text);font-size:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#moreGrid button:active{background:var(--accent);color:#fff}
html.is-mobile .topbar{display:none}
html.is-mobile .user-bar .right{display:none}
html.is-mobile body{padding-bottom:calc(418px + var(--nav-safe, env(safe-area-inset-bottom, 0px)))}
html.is-mobile .control-wrap{bottom:calc(72px + var(--nav-safe, env(safe-area-inset-bottom, 0px)))!important}
html.is-mobile #npCoverWrap{width:42px;height:42px;border-radius:9px}
html.is-mobile #npTitle{font-size:13px}
html.is-mobile #npActions button{padding:7px 9px;font-size:12px}
html.is-mobile h1{font-size:19px;margin:0}
html.is-mobile .subtitle{font-size:13px;margin:-2px 0 6px}
</style></head><body>
<script>(function(){var ua=navigator.userAgent||"";var m=/Android|iPhone|iPad|iPod|Mobile|Tablet|HarmonyOS|Phone|Kindle|Silk|PlayBook|Opera Mini/i.test(ua)||(navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1);if(m)document.documentElement.classList.add("is-mobile");window.IS_MOBILE=!!m;})();
(function(){
  function calcNavSafe(){
    try{
      var d=document.createElement("div");
      d.style.cssText="position:fixed;left:0;bottom:0;width:1px;height:env(safe-area-inset-bottom,0px);visibility:hidden;pointer-events:none;";
      document.body.appendChild(d);
      var env=Math.round(d.getBoundingClientRect().height||0);
      d.remove();
      var safe=env;
      if(!safe){
        var ih=window.innerHeight||0,sh=(window.screen&&(window.screen.availHeight||window.screen.height))||0;
        // 沉浸式/全屏容器判据：网页可视高度几乎顶满可用屏幕高度（只差状态栏+标题栏），说明底部伸到了系统导航条下面，补 48px；普通浏览器可视区矮很多，只补 14px
        safe=(sh&&ih>=sh-40)?48:14;
      }
      document.documentElement.style.setProperty("--nav-safe",safe+"px");
    }catch(e){document.documentElement.style.setProperty("--nav-safe","14px");}
  }
  if(window.IS_MOBILE){
    if(document.body) calcNavSafe(); else document.addEventListener("DOMContentLoaded",calcNavSafe);
    window.addEventListener("resize",calcNavSafe);
    window.addEventListener("orientationchange",function(){setTimeout(calcNavSafe,300);});
    if(window.visualViewport) window.visualViewport.addEventListener("resize",calcNavSafe);
  }
})();
</script>
<div id="loadingOverlay">
  <div class="spin-box"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><g><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="0s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(45 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-0.15s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(90 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-0.3s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(135 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-0.45s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(180 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-0.6s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(225 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-0.75s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(270 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-0.9s"/></rect><rect x="46" y="10" width="8" height="22" rx="3" fill="#fff" transform="rotate(315 50 50)"><animate attributeName="opacity" values="1;0.1" dur="1.2s" repeatCount="indefinite" begin="-1.05s"/></rect></g></svg></div>
</div>

<div class="user-bar">
  <span id="userInfo" style="display:flex;align-items:center;">
    <img id="avatarImg" class="avatar-sm" src="" style="display:none" onclick="document.getElementById('avatarInput').click()">
    <span id="defaultAvatar" class="avatar-sm" style="background:#444;text-align:center;line-height:32px;font-size:16px;" onclick="document.getElementById('avatarInput').click()">👤</span>
    <span id="usernameText">未登录</span>
    <span id="vipBadge" style="margin-left:6px;"></span>
  </span>
  <input type="file" id="avatarInput" accept="image/*" style="display:none" onchange="uploadAvatar(this)">
  
  <div class="right">
    <button id="onlineBtn" style="background:#3498db">🎧 在线</button>
    <button id="msgBtn" style="background:#8e44ad">💬<span id="msgCount" style="background:#e94560;border-radius:8px;padding:1px 6px;font-size:11px;display:none">0</span></button>
    <button id="pointsBtn" style="background:#f0a030">💰 <span id="pointsText" class="points-tag">0</span></button>
    <button id="feedbackBtn" style="background:#0f3460">📝 反馈</button>
    <button id="vipBtn" style="background:#8e44ad">👑 会员</button>
    <button id="adminBtn" style="display:none;background:#e94560">后台</button>
    <button id="settingsBtn" style="display:none;background:#455a64">⚙ 设置</button>
    <button id="mobileBtn" style="background:#0f3460">📱 手机</button>
    <button id="logoutBtn" style="background:#555">退出</button>
  </div>
</div>

<h1>🎵 Conan听歌系统</h1>
<div class="subtitle" style="text-align:center;font-family:'ZuiMeiDeFengJing','STKaiti','KaiTi','华文楷体',serif;font-size:15px;color:#c9a7b2;letter-spacing:.22em;margin:-6px 0 8px">最美的风景</div>
<div id="broadcastBar" class="bc-collapsed" style="display:none;max-width:1100px;margin:0 auto 12px;padding:12px 16px;border-radius:14px;background:linear-gradient(135deg,rgba(233,69,96,.16),rgba(52,152,219,.12));border:1px solid rgba(233,69,96,.32);font-size:13px;line-height:1.7;position:relative">
  <div style="display:flex;gap:8px;align-items:flex-start"><span style="font-size:18px;flex-shrink:0">📢</span><div style="flex:1;min-width:0"><div id="broadcastText" style="white-space:pre-wrap"></div><div id="broadcastUpdated" style="color:#889;font-size:11px;margin-top:4px"></div></div><button id="bcToggle" onclick="toggleBroadcast()" style="background:transparent;border:none;color:#ffd166;font-size:12px;padding:0 4px;cursor:pointer;white-space:nowrap">展开</button><button onclick="closeBroadcast()" style="background:transparent;border:none;color:#889;font-size:18px;padding:0 4px;cursor:pointer">×</button></div>
</div>
<div class="topbar">
  <button id="btnRecommend" class="active">🏠 推荐</button>
  <button id="btnBrowse">📂 浏览</button>
  <button id="btnPlaylists">📋 歌单</button>
  <button id="btnKugou">🌐 酷狗下载</button>
  <button id="btnMv" title="搜索并在线播放酷狗 MV">🎬 MV</button>
  <button id="btnBt" title="检测本机（手机）蓝牙耳机连接状态" onclick="btAuthorize()">🎧 蓝牙</button>
  <button id="btnSinger" title="歌手分类：按语种 / 性别筛选，看头像粉丝数">🎤 歌手</button>
  <button id="btnRecognize" title="听歌识曲：放一段音乐，自动识别是什么歌">🔎 识曲</button>
  <button id="lyricStyleBtn">🎨 歌词</button>
  <button id="btnSfx" title="酷狗式音效（会员 / 站长专属）">🎚 音效</button>
  <button id="speedBtn">1x 倍速</button>
  <button id="themeBtn">🌗 主题</button>
  <button id="refreshBtn" title="刷新歌曲库、通知和广播">🔄 刷新</button>
  <button id="fullBtn" title="只把网页窗口全屏，不改变歌词布局">⛶ 网页全屏</button>
  <button id="lyricFullBtn" title="只把歌词全屏显示，不强制网页全屏">🎤 歌词全屏</button>
</div>

<div class="modal" id="sfxModal">
  <div class="box" style="max-width:820px">
    <h3>🎚 音效（会员 / 站长专属）</h3>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:12px;color:#889;gap:10px">
      <span>选一个音效，正在播放的歌立刻生效；效果由浏览器实时处理，不落盘</span>
      <span id="sfxNow" style="white-space:nowrap;color:#ffd166">当前：原声</span>
    </div>
    <div id="sfxList" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;max-height:52vh;overflow-y:auto;padding:2px"></div>
    <div class="row" style="margin-top:12px">
      <button onclick="applySoundFx('')" style="background:#2d3047">原声（关闭音效）</button>
      <button onclick="closeModal('sfxModal')" style="background:#37415c">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="recModal">
  <div class="box" style="max-width:540px">
    <h3>🔎 听歌识曲</h3>
    <div style="font-size:12px;color:#889;line-height:1.75;margin-bottom:12px">
      把手机 / 音箱里正在放的歌靠近麦克风，<b style="color:#ffd166">先放 8 秒左右</b>，再点下面按钮开始录。
      手机端要用 <b>https</b> 打开页面（<span style="color:#ffd166">https://192.168.1.4:5001</span>）浏览器才给麦克风权限；电脑本机用 127.0.0.1 也可以。
    </div>
    <div id="recState" style="text-align:center;font-size:13px;color:#ffd166;min-height:22px;margin-bottom:10px"></div>
    <div id="recResult"></div>
    <div class="row" style="margin-top:12px">
      <button id="recStartBtn" onclick="recognizeByMic()" style="background:#e94560;flex:1">🎤 开始识别（8 秒）</button>
      <button onclick="closeModal('recModal')" style="background:#37415c">关闭</button>
    </div>
  </div>
</div>

<div style="display:flex;gap:8px;margin-bottom:12px;">
  <input id="searchBox" type="text" placeholder="🔍 搜索在线歌曲（酷狗），回车即搜…" style="flex:1;margin:0;padding:12px;border-radius:10px;border:none;background:var(--panel);color:var(--text);">
  <button onclick="importComan()" style="padding:0 15px;background:#8e44ad;color:#fff;border:none;border-radius:10px;cursor:pointer;">导入</button>
</div>
<div id="searchTabs" style="display:flex;gap:6px;flex-wrap:wrap;margin:-4px 0 10px 0;align-items:center;">
  <button data-k="song" onclick="setSearchKind('song',this)">综合</button>
  <button data-k="single" onclick="setSearchKind('single',this)">单曲</button>
  <button data-k="mv" onclick="setSearchKind('mv',this)">视频</button>
  <button data-k="mv2" onclick="setSearchKind('mv2',this)">MV</button>
  <button data-k="lyric" onclick="setSearchKind('lyric',this)">歌词</button>
  <button data-k="special" onclick="setSearchKind('special',this)">歌单</button>
  <button data-k="album" onclick="setSearchKind('album',this)">专辑</button>
  <button data-k="singer" onclick="setSearchKind('singer',this)">歌手</button>
</div>

<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
  <button id="backBtn" style="padding:10px 14px;background:var(--accent);color:#fff;border:none;border-radius:10px;display:none">← 返回</button>
  <div class="path-bar" id="pathText" style="flex:1;margin-bottom:0">📂 根目录</div>
  <button id="multiSelBtn" onclick="toggleMultiSel()">✅ 多选</button>
  <button id="exportSelBtn" onclick="exportSelectedM3u()" style="display:none;background:#07c160;color:#fff;border:none;border-radius:10px;padding:10px 14px">导出选中</button>
  <button id="m3uBtn" onclick="exportM3u()">📤 导出M3U</button>
  <button id="deletePlBtn" style="display:none;background:#c0392b;color:#fff;border:none;border-radius:10px;padding:10px 14px" title="删除当前歌单">🗑️ 删除歌单</button>
</div>
<div id="mainArea">
  <div id="mainListCol">
    <div id="listBox"></div>
    <div id="footerNote" style="text-align:center;color:#697089;font-size:11px;margin:10px 0 4px;letter-spacing:.06em">Conan Music · 最美的风景 · v2026.11.19 在线听歌版</div>
  </div>
  <div id="lyricCol">
    <div id="lyricHead" style="display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;color:var(--text-mute);letter-spacing:.1em">
      <span>🎤 歌词</span>
      <span style="display:flex;gap:6px">
        <button onclick="adjustFont(2)" style="padding:4px 9px;background:var(--panel);color:var(--text);border:none;border-radius:6px;cursor:pointer;font-size:12px">A+</button>
        <button onclick="adjustFont(-2)" style="padding:4px 9px;background:var(--panel);color:var(--text);border:none;border-radius:6px;cursor:pointer;font-size:12px">A-</button>
      </span>
    </div>
    <div id="lyricBox" style="margin:0;"><div id="lyricContent" style="transition:transform .38s cubic-bezier(.2,.8,.3,1)"><div class="lyric-empty">🎵 点一首歌开始播放，歌词会一直显示在这里</div></div></div>
  </div>
</div>

<audio id="player" preload="metadata"></audio>

<!-- 移动端（手机 / 平板）底部导航 -->
<div id="moreMask" onclick="closeMoreSheet()"></div>
<div id="moreSheet">
  <div class="ms-title"><span>更多功能</span><span style="cursor:pointer;padding:4px 8px" onclick="closeMoreSheet()">✕ 关闭</span></div>
  <div id="moreGrid"></div>
</div>
<nav id="mobileNav">
  <button class="nav-btn active" data-nav="recommend" onclick="navGo('recommend')"><em>🏠</em><span>推荐</span></button>
  <button class="nav-btn" data-nav="browse" onclick="navGo('browse')"><em>📂</em><span>浏览</span></button>
  <button class="nav-btn" data-nav="playlists" onclick="navGo('playlists')"><em>📋</em><span>歌单</span></button>
  <button class="nav-btn" data-nav="search" onclick="navGo('search')"><em>🔍</em><span>搜索</span></button>
  <button class="nav-btn" data-nav="lyric" onclick="navGo('lyric')"><em>🎤</em><span>歌词</span></button>
  <button class="nav-btn" data-nav="more" onclick="navGo('more')"><em>☰</em><span>更多</span></button>
</nav>

<div class="control-wrap" id="controlWrap">
  <div class="drag-handle" id="dragHandle"></div>
  <div id="npBar">
    <div id="npCoverWrap">
      <img id="npCover" alt="cover">
      <span id="npCoverPh">🎵</span>
    </div>
    <div id="npMeta">
      <div id="npTitle">未在播放</div>
      <div id="npSinger">点一首歌开始播放</div>
    </div>
    <div id="npActions">
      <button id="npLyricBtn" title="歌词全屏">🎤</button>
      <button id="npQueueBtn" title="播放列表">📋</button>
    </div>
  </div>
  <div id="progressWrap">
    <div id="progressBar">
      <div id="bufferFill"></div>
      <div id="progressFill"></div>
      <div id="progressThumb"></div>
    </div>
    <div id="progressTooltip">00:00</div>
    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-mute);margin-top:8px">
      <span id="currentTime">00:00</span>
      <span id="audioInfo" style="color:var(--accent)"></span>
      <span id="totalTime">00:00</span>
    </div>
  </div>
  <div class="control-bar">
    <button class="control-btn" id="prevBtn">⏮ 上一首</button>
    <button class="control-btn" id="playBtn">▶ 播放</button>
    <button class="control-btn" id="nextBtn">下一首 ⏭</button>
    <button class="mode-btn" id="modeBtn">🔁 列表循环</button>
    <button class="control-btn" id="queueBtn" style="flex:0 0 92px;min-width:92px">📋 列表</button>
    <button class="control-btn" id="lyricToolBtn" style="flex:0 0 110px;min-width:110px">📝 歌词工具</button>
    <div class="volume-wrap">
      <button id="muteBtn" title="静音">🔊</button>
      <input id="volumeSlider" type="range" min="0" max="1" step="0.01" value="1" title="音量">
    </div>
  </div>
</div>

<div id="fullScreenWrap">
  <div id="fullBg"></div>
  <button class="close-full" onclick="exitFullScreen()">✕ 退出</button>
  <button id="fullBgModeBtn" onclick="cycleFullBgMode()">封面模糊</button>
  <div id="fullSongTitle"></div>
  <div id="fullLyricInner"><div id="fullLyricContent"></div></div>
</div>

<!-- 弹窗组件 -->
<div class="modal" id="onlineModal"><div class="box"><h3>🎧 正在听歌</h3><div id="onlineList"></div><div class="row"><button style="background:#383b58" onclick="document.getElementById('onlineModal').style.display='none'">关闭</button></div></div></div>
<div class="modal" id="msgModal"><div class="box"><h3>💬 留言</h3><div id="msgList"></div><div class="row"><button style="background:#383b58" onclick="document.getElementById('msgModal').style.display='none'">关闭</button></div></div></div>
<div class="modal" id="addToPlModal"><div class="box"><h3>添加到歌单</h3><div id="plOptions"></div><div class="row"><button style="background:#383b58" onclick="document.getElementById('addToPlModal').style.display='none'">取消</button></div></div></div>

<div class="modal" id="vipModal">
  <div class="box">
    <h3>👑 特权说明</h3>
    <div id="vipStatus" style="text-align:center;margin-bottom:8px;"></div>
    <div class="hint">💳 听歌会员 = 100积分/天 (可试听全曲，下载加密.coman)<br>💳 下载会员 = 200积分/天 (可下载原版.mp3)<br>单曲买断 = 1000积分</div>
    <input id="vipPayAmount" type="number" placeholder="转账金额(元)">
    <input id="vipPayNote" placeholder="转账单号/凭证">
    <div class="row"><button style="background:#07c160" onclick="applyVipPay()">提交转账申请</button></div>
    <div style="text-align:center;margin:12px 0">—— 或 积分兑换 ——</div>
    <select id="buyVipType"><option value="vip">兑换听歌会员</option><option value="download">兑换下载会员</option></select>
    <input id="vipPointsInput" type="number" placeholder="积分">
    <div class="row"><button style="background:#f0a030" onclick="buyVipPoints()">积分兑换</button></div>
    <div id="vipMsg" style="text-align:center;margin-top:10px;font-size:14px"></div>
    <div class="row"><button style="background:#383b58" onclick="document.getElementById('vipModal').style.display='none'">关闭</button></div>
  </div>
</div>

<div class="modal" id="createPlModal">
  <div class="box">
    <h3>新建歌单</h3>
    <input id="newPlName" placeholder="歌单名称" style="margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
        <img id="newPlCoverPreview" src="" style="width:50px;height:50px;border-radius:8px;object-fit:cover;display:none;background:#333;">
        <button onclick="document.getElementById('newPlCoverFile').click()" style="padding:8px 12px;background:var(--input-bg);color:var(--text);border:none;border-radius:8px;cursor:pointer;">选择封面</button>
        <input type="file" id="newPlCoverFile" accept="image/*" style="display:none" onchange="previewPlCover(this)">
    </div>
    <div class="row">
      <button style="background:#383b58" onclick="document.getElementById('createPlModal').style.display='none'">取消</button>
      <button style="background:var(--accent)" id="btnCreatePl" onclick="submitCreatePlaylist()">创建</button>
    </div>
  </div>
</div>

<div class="modal" id="renamePlModal">
  <div class="box">
    <h3>✏️ 重命名歌单</h3>
    <input type="hidden" id="renamePlId">
    <input id="renamePlName" placeholder="新的歌单名称" maxlength="40" style="margin-bottom:10px;">
    <div class="row">
      <button style="background:#383b58" onclick="document.getElementById('renamePlModal').style.display='none'">取消</button>
      <button style="background:var(--accent)" onclick="submitRenamePlaylist()">保存</button>
    </div>
  </div>
</div>

<div class="modal" id="confirmModal">
  <div class="box" style="max-width:430px;text-align:center">
    <div class="modal-kicker">PLEASE CONFIRM</div>
    <h3 id="confirmTitle">确认操作</h3>
    <div id="confirmText" class="confirm-text"></div>
    <div class="modal-footer">
      <button id="confirmCancel" style="background:#383b58">取消</button>
      <button id="confirmOk" style="background:var(--accent)">确认</button>
    </div>
  </div>
</div>

<div class="modal" id="feedbackModal"><div class="box"><h3>反馈</h3><input id="fbSong" placeholder="歌名"><textarea id="fbNote" placeholder="备注"></textarea><div class="row"><button onclick="submitFeedback()">提交</button></div></div></div>
<div class="modal" id="pointLogModal"><div class="box"><h3>积分明细</h3><div id="pointLogList"></div></div></div>

<div class="modal" id="queueModal">
  <div class="box">
    <h3>📋 播放列表</h3>
    <div id="queueList" class="queue-list"></div>
    <div class="row"><button style="background:#07c160" onclick="saveQueueAsPlaylist()">存为歌单</button><button style="background:#8e44ad" onclick="showPlayHistory()">最近播放</button><button style="background:#383b58" onclick="document.getElementById('queueModal').style.display='none'">关闭</button></div>
  </div>
</div>

<div class="modal" id="lyricToolModal">
  <div class="box" style="max-width:680px">
    <div class="modal-kicker">LYRIC STUDIO</div>
    <h3>📝 歌词工具</h3>
    <div id="lyricToolSong" class="lyric-tool-song"></div>
    <div class="lyric-tool-grid">
      <button style="background:#3498db" onclick="lyricShift(-0.5)">⏪ 提前 0.5 秒</button>
      <button style="background:#3498db" onclick="lyricShift(0.5)">⏩ 延后 0.5 秒</button>
      <button style="background:#e67e22" onclick="lyricReset()">↩️ 回到初始时间轴</button>
      <button style="background:#07c160" onclick="lyricExport()">📤 导出 LRC</button>
      <button style="background:#8e44ad" onclick="lyricEmbed()">💾 内嵌到音频</button>
      <button style="background:var(--accent)" onclick="lyricRefresh()">🔄 重新搜索酷狗逐字歌词</button>
      <button id="lyricUpgradeBtn" style="background:#0f766e" onclick="startLyricUpgrade()">🚀 分批升级全库逐字歌词</button>
      <button style="background:#455a64" onclick="lyricPreview()">👁 预览逐字时间轴</button>
      <button style="background:#5d4037" onclick="lyricExport(true)">📤 导出原始 KRC</button>
    </div>
    <div class="lyric-tool-tip" id="lyricToolTip">时间轴调整会先备份原始歌词；点“回到初始时间轴”可一键还原。</div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:10px 0;padding:10px;border-radius:10px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.06)">
      <span style="color:#9aa0b8;font-size:12px">单句微调</span>
      <span style="color:#9aa0b8;font-size:12px">第</span>
      <input id="lyricLineNo" type="number" min="1" value="1" style="width:62px;padding:7px;border-radius:8px;border:1px solid rgba(255,255,255,.1);background:var(--input-bg);color:var(--text);text-align:center">
      <span style="color:#9aa0b8;font-size:12px">句</span>
      <button style="background:#3498db;padding:7px 10px;font-size:12px" onclick="lyricShiftLine(-0.1)">提前 0.1s</button>
      <button style="background:#3498db;padding:7px 10px;font-size:12px" onclick="lyricShiftLine(0.1)">延后 0.1s</button>
      <span style="color:#66718a;font-size:11px">先用“预览逐字时间轴”查看句号</span>
    </div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:10px 0;padding:10px;border-radius:10px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.06)">
      <span style="color:#9aa0b8;font-size:12px;white-space:nowrap">🎨 歌词样式</span>
      <select id="lyricFontSelect" title="歌词字体（字体库目录：T:\插件（python代码所运用的插件）\conan 支持系统\字体库）" style="flex:1;min-width:170px;padding:8px;margin:0;border-radius:8px;border:1px solid rgba(255,255,255,.1);background:var(--input-bg);color:var(--text);font-size:12px"></select>
      <button style="background:#455a64;padding:7px 10px;font-size:12px" onclick="loadLyricFonts(true)">↻ 刷新字体</button>
      <span style="color:#9aa0b8;font-size:12px">已唱</span><input id="lyricColorPast" type="color" title="已唱字/已唱行颜色">
      <span style="color:#9aa0b8;font-size:12px">当前</span><input id="lyricColorCur" type="color" title="当前字/当前行颜色">
      <span style="color:#9aa0b8;font-size:12px">未唱</span><input id="lyricColorFuture" type="color" title="未唱歌词颜色">
      <button style="background:#5d4037;padding:7px 10px;font-size:12px" onclick="resetLyricColors()">恢复默认</button>
      <span style="color:#66718a;font-size:11px;width:100%">字体文件放入「T:\插件（python代码所运用的插件）\conan 支持系统\字体库」后点“刷新字体”；颜色即时生效并自动记忆。</span>
    </div>
    <div id="lyricUpgradeStatus" class="lyric-upgrade-status" style="display:none">
      <div class="lyric-upgrade-head"><span id="lyricUpgradeText">准备升级...</span><button onclick="stopLyricUpgrade()" style="background:#c0392b;padding:4px 9px;font-size:11px">停止</button></div>
      <div class="lyric-upgrade-track"><div id="lyricUpgradeFill"></div></div>
    </div>
    <textarea id="lyricImportText" rows="10" class="lyric-import-box" placeholder="粘贴外部 LRC 歌词后可覆盖导入（自动清理广告与水印）"></textarea>
    <div class="modal-footer">
      <button style="background:#e67e22" onclick="lyricImport()">📥 导入并覆盖当前歌词</button>
      <button class="modal-close-btn" onclick="closeModal('lyricToolModal')">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="coverPreviewModal">
  <div class="box" style="max-width:520px;text-align:center">
    <div class="modal-kicker">ALBUM ART</div>
    <h3>🖼 封面预览</h3>
    <img id="coverPreviewImg" style="width:100%;max-height:62vh;object-fit:contain;border-radius:16px;background:#111;margin-top:6px">
    <div id="coverPreviewName" style="color:#889;font-size:12px;margin-top:10px"></div>
    <div class="modal-footer"><button style="background:#383b58" onclick="closeModal('coverPreviewModal')">关闭</button></div>
  </div>
</div>

<div class="modal" id="lyricPreviewModal">
  <div class="box" style="max-width:620px">
    <div class="modal-kicker">LYRIC PREVIEW</div>
    <h3>👁 逐字时间轴预览</h3>
    <div id="lyricPreviewSong" class="lyric-tool-song"></div>
    <div id="lyricPreviewBody" style="max-height:58vh;overflow:auto;background:rgba(0,0,0,.2);border-radius:12px;padding:12px;font-size:13px;line-height:1.9"></div>
    <div class="modal-footer"><button style="background:#383b58" onclick="closeModal('lyricPreviewModal')">关闭</button></div>
  </div>
</div>

<div class="modal" id="mobileModal">
  <div class="box">
    <h3>📱 手机访问</h3>
    <div class="hint">让手机和电脑连接同一个 Wi-Fi，然后在手机浏览器打开下面地址。电脑防火墙需放行本服务端口。</div>
    <input id="mobileUrl" readonly>
    <div class="row">
      <button style="background:#0f3460" onclick="copyMobileUrl()">复制地址</button>
      <button style="background:#383b58" onclick="document.getElementById('mobileModal').style.display='none'">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="shareModal">
  <div class="box" style="max-width:460px;text-align:center">
    <h3>📤 扫码听歌</h3>
    <div id="shareSongTitle" style="color:#aaa;font-size:13px;margin-bottom:12px"></div>
    <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;justify-content:center">
      <img id="shareQr" src="" style="width:200px;height:200px;border-radius:14px;background:#fff;padding:8px;display:none">
      <div style="text-align:left;flex:1;min-width:190px">
        <label style="font-size:12px;color:#889">分享文案（显示在扫码页）<br>
          <input id="shareNote" placeholder="想对听歌的人说..." maxlength="120" style="margin-top:4px;width:100%">
        </label>
        <label style="font-size:12px;color:#889;margin-top:8px;display:block">链接有效期
          <select id="shareTtl" style="margin-top:4px;width:100%">
            <option value="1">1 小时</option>
            <option value="24">1 天</option>
            <option value="168">7 天</option>
            <option value="0" selected>永久</option>
          </select>
        </label>
        <label style="font-size:12px;color:#889;margin-top:8px;display:block">自定义封面（图片地址，留空自动用歌曲封面）
          <input id="shareCover" placeholder="https://..." style="margin-top:4px;width:100%">
        </label>
      </div>
    </div>
    <input id="shareUrl" readonly style="margin-top:12px;font-size:12px">
    <div class="row" style="justify-content:center">
      <button style="background:#0f3460" onclick="copyShareUrl()">复制链接</button>
      <button style="background:#383b58" onclick="document.getElementById('shareModal').style.display='none'">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="mvModal">
  <div class="box" style="max-width:780px">
    <h3>🎬 酷狗 MV（搜索 + 在线播放）</h3>
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <input id="mvSearchInput" placeholder="搜索 MV：歌名 / 歌手" style="flex:1;min-width:0;margin:0">
      <button style="background:#e94560;white-space:nowrap" onclick="searchMv()">搜索</button>
    </div>
    <div id="mvList" style="max-height:32vh;overflow-y:auto"></div>
    <video id="mvPlayer" controls playsinline style="width:100%;border-radius:12px;background:#000;display:none;margin-top:10px;max-height:46vh"></video>
    <div id="mvQualityRow" style="display:none;margin-top:8px;font-size:13px;color:#889">
      画质：
      <select id="mvQuality" style="margin:0 6px 0 4px">
        <option value="rq">超清</option>
        <option value="sq">高清</option>
        <option value="le">流畅</option>
      </select>
    </div>
    <div class="row">
      <button style="background:#383b58" onclick="closeMv()">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="kugouTaskModal">
  <div class="box" style="max-width:560px">
    <h3>⬇️ 酷狗下载任务</h3>
    <div id="kugouTaskList" style="max-height:60vh;overflow-y:auto">加载中...</div>
    <div class="row">
      <button style="background:#3498db" onclick="loadKugouTasks()">刷新</button>
      <button style="background:#383b58" onclick="closeKugouTasks()">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="settingsModal">
  <div class="box" style="max-width:760px">
    <h3>⚙ 系统设置（保存后即时生效）</h3>
    <div id="settingsBody" style="max-height:66vh;overflow-y:auto;font-size:13px;line-height:1.9">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <label>下载并发数（1~8）<br><input id="setConcurrency" type="number" min="1" max="8" value="3"></label>
        <label>未完成任务上限<br><input id="setMaxActive" type="number" min="1" max="200" value="20"></label>
        <label>单首超时（秒）<br><input id="setTimeout" type="number" min="10" max="600" value="45"></label>
        <label>单任务限速 KB/s（0=不限）<br><input id="setSpeedLimit" type="number" min="0" value="0"></label>
        <label>磁盘预警 GB<br><input id="setDiskWarn" type="number" min="0" value="20"></label>
        <label>禁止下载阈值 GB<br><input id="setDiskStop" type="number" min="0" value="5"></label>
        <label>日志分割大小 MB<br><input id="setLogMb" type="number" min="1" value="10"></label>
        <label>封面尺寸 px<br><input id="setCoverSize" type="number" min="240" max="1080" value="480"></label>
        <label>封面重试次数<br><input id="setCoverRetry" type="number" min="0" max="5" value="2"></label>
        <label>登录失败锁定次数<br><input id="setLoginMax" type="number" min="1" value="5"></label>
        <label>锁定时长（分钟）<br><input id="setLockMin" type="number" min="1" value="10"></label>
        <label>专辑补缺默认扫描天数<br><input id="setScanDays" type="number" min="0" value="30"></label>
        <label>默认下载音质<br><select id="setQuality">
          <option value="auto">智能（有高音质优先）</option>
          <option value="standard">标准 128k</option>
          <option value="high">高品 320k</option>
          <option value="lossless">无损 FLAC</option>
          <option value="hires">Hi-Res</option>
        </select></label>
      </div>
      <label>黑名单关键词（逗号分隔）<br><textarea id="setBlacklist" rows="2" style="width:100%"></textarea></label>
      <label>白名单关键词（命中就不算黑名单）<br><textarea id="setWhitelist" rows="2" style="width:100%"></textarea></label>
      <label>歌词过滤关键词（逗号分隔，自动剔除）<br><textarea id="setLyricBlacklist" rows="2" style="width:100%"></textarea></label>
      <label>归档模板<br><input id="setArchive" style="width:100%" placeholder="{歌手}/{专辑}/{歌名}-{歌手}"></label>
      <label>管理员备注<br><input id="setAdminNote" style="width:100%"></label>
      <label>广播 / 更新公告（显示在首页顶部）<br><textarea id="setBroadcast" rows="3" style="width:100%" placeholder="写这次更新了什么..."></textarea></label>
      <label>允许登录后台的 IP（逗号分隔，留空不限制）<br><input id="setAdminIps" style="width:100%"></label>
      <label>游客模式<br><select id="setGuestMode"><option value="play">可浏览和播放</option><option value="view">只能浏览</option><option value="closed">关闭游客访问</option></select></label>
      <div id="settingsRuntime" style="color:#888;margin-top:8px"></div>
    </div>
    <div class="row">
      <button style="background:#07c160" onclick="saveSettings()">保存设置</button>
      <button style="background:#3498db" onclick="showAdminLogs()">查看日志</button>
      <button style="background:#8e44ad" onclick="backupSystemFiles()">备份数据</button>
      <button style="background:#383b58" onclick="document.getElementById('settingsModal').style.display='none'">关闭</button>
    </div>
  </div>
</div>

<div class="modal" id="logModal">
  <div class="box" style="max-width:780px">
    <h3>📜 管理员操作日志</h3>
    <div style="display:flex;gap:8px;margin-bottom:8px"><input id="logFilter" placeholder="过滤：失败 / 登录 / 专辑..." style="flex:1"><button style="background:#3498db" onclick="loadAdminLogs()">查询</button></div>
    <div id="logBody" style="max-height:58vh;overflow:auto;font-family:monospace;font-size:12px;white-space:pre-wrap;background:rgba(0,0,0,.18);padding:10px;border-radius:8px">加载中...</div>
    <div class="row"><button style="background:#383b58" onclick="document.getElementById('logModal').style.display='none'">关闭</button></div>
  </div>
</div>

<div class="modal" id="libraryScanModal">
  <div class="box" style="max-width:720px">
    <h3>🧹 音乐库体检 / 修复</h3>
    <div id="libraryScanBody" style="max-height:60vh;overflow-y:auto;font-size:14px;line-height:1.7">点击“开始扫描”检查歌词、封面和文件名。</div>
    <div style="display:flex;gap:8px;align-items:center;margin:8px 0;font-size:13px">
      <span>专辑扫描范围</span>
      <input id="albumScanDays" type="number" min="0" value="30" placeholder="天数，0=全部" style="width:100px;margin:0">
      <span>天内</span>
      <label><input id="albumSkipInvalid" type="checkbox" checked> 跳过音源失效专辑</label>
      <button style="background:#3498db;padding:8px 12px" onclick="scanAlbumAudit()">按范围扫描</button>
    </div>
    <div class="row">
      <button style="background:#8e44ad" onclick="scanLibrary()">开始扫描</button>
      <button style="background:#3498db" onclick="scanAlbumAudit()">专辑补缺</button>
      <button style="background:#07c160" onclick="repairLibrary('lyric')">补歌词</button>
      <button style="background:#0f3460" onclick="repairLibrary('cover')">补封面</button>
      <button style="background:#607d8b" onclick="libraryTool('stats')">统计</button>
      <button style="background:#795548" onclick="libraryTool('csv')">导出CSV</button>
      <button style="background:#9e9d24" onclick="libraryTool('empty')">清空目录</button>
      <button style="background:#455a64" onclick="libraryTool('duplicates')">查重复</button>
      <button style="background:#383b58" onclick="document.getElementById('libraryScanModal').style.display='none'">关闭</button>
    </div>
  </div>
</div>

<script>
const player=document.getElementById("player");
let savedVolume = parseFloat(localStorage.getItem("conan_volume"));
if(isNaN(savedVolume)) savedVolume = 0.8;
player.volume = Math.max(0, Math.min(1, savedVolume));
let isMuted = localStorage.getItem("conan_muted") === "1";
player.muted = isMuted;
// 播放时尽量保持屏幕不熄；不支持 WakeLock 的浏览器会自动忽略
let wakeLock=null;
async function requestWakeLock(){
    if(!("wakeLock" in navigator)) return;
    try{ wakeLock=await navigator.wakeLock.request("screen"); }catch(e){}
}
player.addEventListener("play",requestWakeLock);
player.addEventListener("pause",function(){ try{ if(wakeLock) wakeLock.release(); wakeLock=null; }catch(e){} });
// ============ 强制关闭加载 ============
function forceHideLoading(){
    loadingCount = 0;
    document.getElementById("loadingOverlay").style.display = "none";
}

// ============ 主题和字号 ============
let theme = localStorage.getItem("theme") || "dark";
document.documentElement.setAttribute('data-theme', theme);
document.getElementById("themeBtn").onclick = () => {
    const themes = ["dark", "light", "blue", "pink"];
    let idx = themes.indexOf(theme);
    theme = themes[(idx + 1) % themes.length];
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem("theme", theme);
};
let lyricSize = parseInt(localStorage.getItem("lyricSize") || "16");
function applyFont() {
    document.documentElement.style.setProperty('--lyric-size', lyricSize + 'px');
    document.documentElement.style.setProperty('--full-lyric-size', (lyricSize + 20) + 'px');
}
applyFont();
function adjustFont(d) { lyricSize = Math.max(12, Math.min(30, lyricSize + d)); applyFont(); localStorage.setItem("lyricSize", lyricSize); }

// ============ 迷你播放器拖拽 ============
const controlWrap = document.getElementById("controlWrap");
const dragHandle = document.getElementById("dragHandle");
let isDragging = false, startX, startY, startLeft, startBottom;

dragHandle.addEventListener("mousedown", (e) => {
    if(window.innerWidth <= 640) return;
    isDragging = true;
    startX = e.clientX; startY = e.clientY;
    const rect = controlWrap.getBoundingClientRect();
    startLeft = rect.left;
    startBottom = window.innerHeight - rect.bottom;
    controlWrap.style.transition = "none";
});
document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    let dx = e.clientX - startX;
    let dy = startY - e.clientY;
    controlWrap.style.left = (startLeft + dx) + "px";
    controlWrap.style.bottom = (startBottom + dy) + "px";
    controlWrap.style.width = "auto"; 
    controlWrap.style.minWidth = "300px";
});
document.addEventListener("mouseup", () => { isDragging = false; controlWrap.style.transition = ""; });

// ============ 快捷键（统一在播放器初始化后绑定，避免重复触发） ============
// ============ 全屏：网页全屏 / 歌词全屏 分开 ============
function toggleWebFullScreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(function(err){ showToast("网页全屏失败：" + err.message); });
    } else {
        if (document.exitFullscreen) document.exitFullscreen();
    }
}
document.getElementById("fullBtn").onclick = toggleWebFullScreen;
function toggleLyricFullScreen(){
    const wrap=document.getElementById("fullScreenWrap");
    if(wrap.style.display === "block"){
        exitFullScreen();
        return;
    }
    wrap.style.display = "block";
    isFullScreen = true;
    updateFullTitle();
    lrcIdx = -1;
    updateLyric();
}
document.getElementById("lyricFullBtn").onclick = toggleLyricFullScreen;
let fullBgMode=localStorage.getItem("conan_full_bg_mode")||"blur";
function updateFullBg(){
    const wrap=document.getElementById("fullScreenWrap");
    const bg=document.getElementById("fullBg");
    const btn=document.getElementById("fullBgModeBtn");
    if(!wrap||!bg) return;
    wrap.classList.toggle("bg-pure",fullBgMode==="pure");
    wrap.classList.toggle("bg-none",fullBgMode==="none");
    if(btn) btn.innerText=fullBgMode==="blur"?"封面模糊":(fullBgMode==="pure"?"纯色":"无背景");
    const s=playlist[curIndex];
    const rel=s && s.fullRel ? String(s.fullRel).replace(/\\/g,"/") : "";
    const dir=rel.split("/").slice(0,-1).join("/");
    bg.style.backgroundImage=(fullBgMode!=="none" && dir) ? 'url("/api/folder_cover?path='+encodeURIComponent(dir)+'")' : "none";
}
function cycleFullBgMode(){
    fullBgMode = fullBgMode==="blur" ? "pure" : (fullBgMode==="pure" ? "none" : "blur");
    localStorage.setItem("conan_full_bg_mode",fullBgMode);
    updateFullBg();
}
document.getElementById("fullBgModeBtn").onclick=cycleFullBgMode;
function exitFullScreen(){
    document.getElementById("fullScreenWrap").style.display="none";
    isFullScreen = false;
}
function updateFullTitle(){
    const s = playlist[curIndex];
    document.getElementById("fullSongTitle").innerText = s ? ("♪ " + s.name) : "";
    updateFullBg();
    try{ updateNowPlayingBar(); }catch(e){}
}

/* ===== 底部「正在播放」歌曲栏 ===== */
function syncBottomPad(){
    if(!document.documentElement.classList.contains("is-mobile")) return;
    const cw=document.querySelector(".control-wrap");
    const nav=document.getElementById("mobileNav");
    const cwH=cw?cw.offsetHeight:0;
    const navH=nav?nav.offsetHeight:0;
    document.body.style.paddingBottom=(cwH+navH+16)+"px";
}
function updateNowPlayingBar(){
    const t=document.getElementById("npTitle");
    if(!t) return;
    let name="", singer="", cover="";
    const src=String((typeof player!=="undefined" && player && (player.currentSrc||player.getAttribute("src")))||"");
    if(src.indexOf("/api/online/stream")>=0 && currentOnlineSong){
        name=currentOnlineSong.name||"";
        singer=currentOnlineSong.singer||"";
        cover=currentOnlineSong.cover||"";
    }else{
        const s=playlist[curIndex];
        if(s){
            name=s.name||"";
            singer=s.singer||s.artist||s.author||"";
            cover=s.fullRel?("/api/cover?path="+encodeURIComponent(s.fullRel)):(s.cover||s.coverUrl||"");
        }
    }
    t.innerText = name || "未在播放";
    const sg=document.getElementById("npSinger");
    if(sg) sg.innerText = name ? (singer || "　") : "点一首歌开始播放";
    const img=document.getElementById("npCover");
    const ph=document.getElementById("npCoverPh");
    if(img){
        if(cover){
            img.onerror=function(){ this.style.display="none"; if(ph) ph.style.display="flex"; };
            if(img.getAttribute("src")!==cover){ img.src=cover; }
            img.style.display="block";
            if(ph) ph.style.display="none";
        }else{
            img.style.display="none";
            if(ph) ph.style.display="flex";
        }
    }
    try{ syncBottomPad(); }catch(e){}
}

/* 歌曲栏按钮 + 事件 */
(function(){
    var lb=document.getElementById("npLyricBtn");
    if(lb) lb.onclick=function(){ var b=document.getElementById("lyricFullBtn"); if(b) b.click(); };
    var qb=document.getElementById("npQueueBtn");
    if(qb) qb.onclick=function(){ var b=document.getElementById("queueBtn"); if(b) b.click(); };
    var p=document.getElementById("player");
    if(p){ p.addEventListener("play", function(){ try{ updateNowPlayingBar(); }catch(e){} });
           p.addEventListener("loadedmetadata", function(){ try{ updateNowPlayingBar(); }catch(e){} }); }
    window.addEventListener("resize", function(){ try{ syncBottomPad(); }catch(e){} });
    window.addEventListener("load", function(){ try{ updateNowPlayingBar(); syncBottomPad(); }catch(e){} });
})();

// ============ 核心变量 ============
let loginUser=null, token="", viewMode="browse", kugouKw="", curPlaylistId=0, playlistNames={}, playlistMeta={}, currentOnlineSong=null, onlineLyricSong=null, onlineLrcRaw=null;
const TRIAL=90;
let currentFolders=[], currentRelPath="", playlist=[], curIndex=-1, playMode=localStorage.getItem("conan_play_mode") || "list";
let multiSel=false, selRels=[];
let loadingCount=0;
let isFullScreen = false;

function showLoading(){loadingCount++;document.getElementById("loadingOverlay").style.display="flex";}
function hideLoading(){loadingCount=Math.max(0,loadingCount-1);if(loadingCount===0)document.getElementById("loadingOverlay").style.display="none";}

async function api(path,data,silent){
  if(!silent) showLoading();
  try{ const res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Conan-Token":token||""},body:JSON.stringify(Object.assign({token:token},data||{}))}); return await res.json(); }
  finally{ if(!silent) hideLoading(); }
}
async function apiGet(url){
  showLoading();
  try{ const res=await fetch(url); return await res.json(); }
  finally{ hideLoading(); }
}
function escapeHtml(s){ return String(s==null?"":s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

// ============ 用户逻辑 ============
async function loadMessages(){
  try{
    const r=await api("/api/message/mylist",null,true);
    const list=(r && r.data) || [];
    const unread=list.filter(function(m){ return !m.read; }).length;
    const badge=document.getElementById("msgCount");
    if(badge){
      badge.innerText=String(unread);
      badge.style.display=unread?"inline":"none";
    }
  }catch(e){}
}
async function loadBroadcast(){
  try{
    const res=await fetch("/api/broadcast");
    const r=await res.json();
    const bar=document.getElementById("broadcastBar");
    const txt=document.getElementById("broadcastText");
    const upd=document.getElementById("broadcastUpdated");
    if(!bar || !txt) return;
    if(r && r.text){
      txt.textContent=r.text;
      upd.textContent=r.updated?("更新于 "+r.updated):"";
      bar.style.display="block";
      clearTimeout(bar._autoHide);
      bar._autoHide=setTimeout(function(){ if(bar.style.display==="block") bar.style.display="none"; },15000);
    }else{
      bar.style.display="none";
    }
  }catch(e){}
}
function closeBroadcast(){
  const bar=document.getElementById("broadcastBar");
  if(bar){ clearTimeout(bar._autoHide); bar.style.display="none"; }
}
function toggleBroadcast(){
  const bar=document.getElementById("broadcastBar"); if(!bar) return;
  bar.classList.toggle("bc-collapsed");
  const btn=document.getElementById("bcToggle");
  if(btn) btn.innerText = bar.classList.contains("bc-collapsed") ? "展开" : "收起";
}

/* ===== 移动端底部导航 ===== */
function markNav(key){
  document.querySelectorAll("#mobileNav .nav-btn").forEach(function(b){
    b.classList.toggle("active", b.getAttribute("data-nav")===key);
  });
}
function navGo(key){
  if(key==="more"){ openMoreSheet(); return; }
  if(key==="search"){
    try{ window.scrollTo(0,0); }catch(e){}
    const sb=document.getElementById("searchBox");
    if(sb){ sb.focus(); }
    markNav("search");
    return;
  }
  if(key==="lyric"){
    const b=document.getElementById("lyricFullBtn");
    if(b){ b.click(); }
    markNav("lyric");
    return;
  }
  const map={recommend:"btnRecommend", browse:"btnBrowse", playlists:"btnPlaylists"};
  const b=document.getElementById(map[key]||"");
  if(b){ b.click(); }
  markNav(key);
}
function openMoreSheet(){
  const grid=document.getElementById("moreGrid"); if(!grid) return;
  grid.innerHTML="";
  document.querySelectorAll(".topbar button, .user-bar .right button, #modeBtn, #queueBtn, #lyricToolBtn").forEach(function(b){
    if(b.style.display==="none") return;
    if(b.closest && b.closest(".control-bar") && getComputedStyle(b).display==="none") return;
    const nb=document.createElement("button");
    nb.textContent=((b.textContent||"").replace(/\s+/g," ").trim()) || (b.getAttribute("title")||"功能");
    nb.addEventListener("click", function(){ closeMoreSheet(); try{ b.click(); }catch(e){} });
    grid.appendChild(nb);
  });
  const s=document.getElementById("moreSheet"); if(s) s.classList.add("show");
  const m=document.getElementById("moreMask"); if(m) m.classList.add("show");
  markNav("more");
}
function closeMoreSheet(){
  const s=document.getElementById("moreSheet"); if(s) s.classList.remove("show");
  const m=document.getElementById("moreMask"); if(m) m.classList.remove("show");
  markNav("");
}
async function checkLogin(){
  token=localStorage.getItem("conan_token");
  if(!token){ location.href="/login"; return; }
  const ret=await api("/api/checktoken",null,true);
  if(!ret.ok){ localStorage.removeItem("conan_token"); location.href="/login"; return; }
  loginUser=ret.user;
  applyUserToUI();
  startHeartbeat();
  setTimeout(loadMessages,1000);
  loadBroadcast();
  
  await renderRecommend("hot");
  await restoreLastPlayback();
}
function applyUserToUI(){
  if(!loginUser) return;
  document.getElementById("usernameText").innerText=loginUser.username;
  if(loginUser.avatar){
      document.getElementById("avatarImg").src=loginUser.avatar;
      document.getElementById("avatarImg").style.display="inline-block";
      document.getElementById("defaultAvatar").style.display="none";
  }
  const vp=document.getElementById("vipBadge");
  let tags = "";
  if(loginUser.vip) tags += '<span class="vip-tag">听歌VIP</span> ';
  if(loginUser.dl_vip) tags += '<span class="dl-tag">下载VIP</span>';
  if(loginUser.role==="admin") tags += '<span class="vip-tag" style="background:#e94560">站长</span>';
  vp.innerHTML = tags;
  
  document.getElementById("pointsText").innerText=loginUser.points||0;
  if(loginUser.role==="admin"){ document.getElementById("adminBtn").style.display="block"; document.getElementById("settingsBtn").style.display="block"; }
}
document.getElementById("logoutBtn").onclick=async()=>{ try{await api("/api/logout");}catch(e){} localStorage.removeItem("conan_token"); location.href="/login"; };
document.getElementById("adminBtn").onclick=()=>location.href="/admin";
document.getElementById("settingsBtn").onclick=openSettings;

async function uploadAvatar(input) {
    if(!input.files.length) return;
    var formData = new FormData();
    formData.append("token", token);
    formData.append("file", input.files[0]);
    var xhr = new XMLHttpRequest(); xhr.open("POST", "/api/upload_avatar", true);
    xhr.onload = async function() {
        var r = JSON.parse(xhr.responseText);
        if(r.ok) { const ret=await api("/api/checktoken"); loginUser=ret.user; applyUserToUI(); alert("头像更新成功"); }
        else alert(r.msg);
    };
    xhr.send(formData);
}

function importComan() {
    var input = document.createElement('input'); input.type = 'file'; input.accept = '.coman';
    input.onchange = function(e) {
        var file = e.target.files[0]; if(!file) return;
        var reader = new FileReader();
        reader.onload = function(ev) {
            var arrayBuffer = ev.target.result;
            var uint8 = new Uint8Array(arrayBuffer);
            var key = new TextEncoder().encode("ConanMusicSecureKey2024!@#");
            for (var i = 0; i < uint8.length; i++) {
                uint8[i] ^= key[i % key.length];
            }
            var blob = new Blob([uint8], {type: 'audio/mp3'});
            var url = URL.createObjectURL(blob);
            
            playlist = [{name: file.name.replace('.coman', ''), fullRel: url, isLocal: true}];
            viewMode="local";
            curIndex=0;
            renderList();
            playSong(0);
        };
        reader.readAsArrayBuffer(file);
    };
    input.click();
}

// ============ 歌单逻辑 ============
document.getElementById("btnPlaylists").onclick=async()=>{
    viewMode="playlists";
    updateM3uBtn();
    document.querySelectorAll(".topbar button").forEach(b=>b.classList.remove("active"));
    document.getElementById("btnPlaylists").classList.add("active");
    document.getElementById("pathText").innerText="📋 我的歌单";
    document.getElementById("backBtn").style.display="none";
    renderPlaylists();
};

async function renderPlaylists() {
    showLoading();
    const r = await api("/api/playlists/list");
    hideLoading();
    playlistNames = {};
    playlistMeta = {};
    
    let html = `<input id="plSearchBox" placeholder="🔍 搜索歌单名称..." oninput="filterPlaylistCards(this.value)" style="width:100%;padding:11px 13px;border-radius:10px;border:1px solid rgba(255,255,255,.08);background:var(--input-bg);color:var(--text);margin-bottom:10px">`;
    html += `<div class="item" onclick="showCreatePlaylistModal()" style="border:1px dashed var(--accent); justify-content:center;">
        <span style="color:var(--accent);font-size:16px;font-weight:bold;">➕ 新建歌单</span>
    </div>`;
    
    if(!r.data.length) html += '<div class="empty">暂无歌单</div>';
    const isAdmin = loginUser && loginUser.role === "admin";
    r.data.forEach(pl => {
        playlistNames[pl.id] = pl.name;
        playlistMeta[pl.id] = pl;
        let tag = pl.is_public ? '<span class="cp-tag" style="background:#e94560">公开</span>' : '';
        let ownerTag = (isAdmin && (!loginUser || pl.owner !== loginUser.username)) ? `<span style="font-size:11px;color:#889;margin-left:6px">@${escapeHtml(pl.owner || "")}</span>` : '';
        let coverSrc = pl.cover || "";
        if(!coverSrc && pl.songs && pl.songs.length){
            const first = String(pl.songs[0].fullRel || "").replace(/\\/g,"/");
            const dir = first.split("/").slice(0,-1).join("/");
            if(dir) coverSrc = "/api/folder_cover?path=" + encodeURIComponent(dir);
        }
        let coverImg = coverSrc ? `<img src="${escapeHtml(coverSrc)}" loading="lazy" style="width:44px;height:44px;border-radius:6px;object-fit:cover;" onerror="this.style.display='none';this.nextElementSibling.style.display='block'"><span style="display:none;font-size:20px;width:44px;text-align:center;">📋</span>` : `<span style="font-size:20px;width:44px;text-align:center;">📋</span>`;
        let canEdit = isAdmin || (loginUser && pl.owner === loginUser.username);
        let editBtns = canEdit ? `<span title="重命名" style="cursor:pointer;padding:4px" onclick="event.stopPropagation();showRenamePlaylist(${pl.id})">✏️</span><span title="${pl.is_public?'设为私有':'公开歌单'}" style="cursor:pointer;padding:4px" onclick="event.stopPropagation();togglePlaylistPublic(${pl.id})">${pl.is_public?'🔒':'🌐'}</span><span title="删除歌单" style="cursor:pointer;padding:4px" onclick="event.stopPropagation();deletePlaylist(${pl.id})">🗑️</span>` : '';
        html += `<div class="item pl-card" data-name="${escapeHtml(String(pl.name||"").toLowerCase())}" data-owner="${escapeHtml(String(pl.owner||"").toLowerCase())}" onclick="openPlaylist(${pl.id})">
            ${coverImg}
            <span class="txt" style="margin-left:10px;">${escapeHtml(pl.name)} ${tag}${ownerTag}</span>
            <span style="color:#888;white-space:nowrap">${pl.songs.length}首</span>
            ${editBtns}
        </div>`;
    });
    document.getElementById("listBox").innerHTML = html;
    document.getElementById("pathText").innerText = "📋 我的歌单（" + r.data.length + " 个）";
    const delBtn = document.getElementById("deletePlBtn");
    if(delBtn) delBtn.style.display = "none";
}

function filterPlaylistCards(kw){
    const q=String(kw||"").trim().toLowerCase();
    document.querySelectorAll(".pl-card").forEach(function(el){
        const hay=(el.dataset.name||"")+" "+(el.dataset.owner||"");
        el.style.display=(!q || hay.indexOf(q)>=0) ? "" : "none";
    });
}
function showRenamePlaylist(pid){
    const oldName = playlistNames[pid] || "";
    document.getElementById("renamePlId").value = pid;
    document.getElementById("renamePlName").value = oldName;
    document.getElementById("renamePlModal").style.display = "flex";
    setTimeout(function(){ document.getElementById("renamePlName").focus(); }, 50);
}
async function submitRenamePlaylist(){
    const pid = parseInt(document.getElementById("renamePlId").value, 10);
    const name = document.getElementById("renamePlName").value.trim();
    if(!pid || !name){ showToast("请输入歌单名称"); return; }
    const r = await api("/api/playlists/update", {pid: pid, name: name}, true);
    if(!r.ok){ showToast(r.msg || "重命名失败"); return; }
    document.getElementById("renamePlModal").style.display = "none";
    playlistNames[pid] = name;
    showToast("歌单已重命名");
    renderPlaylists();
}
async function deletePlaylist(pid){
    const name = playlistNames[pid] || "这个歌单";
    if(!await showConfirm("删除歌单", "确定删除歌单「" + name + "」？删除前会自动备份，但删除后不能恢复歌单内容。", "删除")) return;
    const r = await api("/api/playlists/delete", {pid: pid}, true);
    if(!r.ok){ showToast(r.msg || "删除失败"); return; }
    if(curPlaylistId === pid){
        curPlaylistId = 0;
        playlist = [];
        viewMode = "playlists";
    }
    showToast("歌单已删除，已自动备份");
    document.getElementById("btnPlaylists").click();
}
async function togglePlaylistPublic(pid){
    const pl=playlistMeta[pid];
    if(!pl) return;
    const next=!pl.is_public;
    let r;
    try{ r=await api("/api/playlists/update",{pid:pid,is_public:next},true); }
    catch(e){ showToast("网络错误"); return; }
    showToast(r.ok?(next?"🌐 歌单已公开":"🔒 歌单已设为私有"):(r.msg||"操作失败"));
    if(r.ok) renderPlaylists();
}
function deleteCurrentPlaylist(){
    if(curPlaylistId) deletePlaylist(curPlaylistId);
}
document.getElementById("deletePlBtn").onclick = deleteCurrentPlaylist;

function showCreatePlaylistModal() {
    document.getElementById("newPlName").value = "";
    document.getElementById("newPlCoverPreview").src = "";
    document.getElementById("newPlCoverPreview").style.display = "none";
    document.getElementById("createPlModal").style.display = "flex";
}
function previewPlCover(input) {
    if(input.files && input.files[0]) {
        var reader = new FileReader();
        reader.onload = function(e) {
            var img = document.getElementById("newPlCoverPreview");
            img.src = e.target.result;
            img.style.display = "block";
        };
        reader.readAsDataURL(input.files[0]);
    }
}
async function submitCreatePlaylist() {
    let name = document.getElementById("newPlName").value.trim();
    if(!name) return alert("请输入歌单名称");
    let fileInput = document.getElementById("newPlCoverFile");
    let coverUrl = "";
    if(fileInput.files.length > 0) {
        let btn = document.getElementById("btnCreatePl");
        btn.innerText = "上传中..."; btn.disabled = true;
        var formData = new FormData(); formData.append("token", token); formData.append("file", fileInput.files[0]);
        try {
            let res = await fetch("/api/upload_image", {method:"POST", body: formData});
            let data = await res.json();
            if(data.ok) coverUrl = data.url;
        } catch(e) {}
        btn.innerText = "创建"; btn.disabled = false;
    }
    let r = await api("/api/playlists/create", {name: name, cover: coverUrl});
    if(r.ok) {
        document.getElementById("createPlModal").style.display = "none";
        renderPlaylists();
    } else alert(r.msg);
}

async function openPlaylist(pid, name) {
    curPlaylistId = pid;
    if(!name) name = playlistNames[pid] || ("歌单 " + pid);
    document.getElementById("pathText").innerText = "📋 " + name;
    document.getElementById("backBtn").style.display = "block";
    document.getElementById("backBtn").onclick = () => { document.getElementById("btnPlaylists").click(); };
    showLoading();
    const r = await api("/api/playlists/list");
    hideLoading();
    let pl = r.data.find(p => p.id === pid);
    if(!pl) return;
    playlist = pl.songs;
    viewMode = "playlist_detail";
    const canEdit = loginUser && (loginUser.role === "admin" || pl.owner === loginUser.username);
    const delBtn = document.getElementById("deletePlBtn");
    if(delBtn) delBtn.style.display = canEdit ? "block" : "none";
    updateM3uBtn();
    renderList();
}

document.getElementById("btnBrowse").onclick=()=>{
    viewMode="browse";
    document.querySelectorAll(".topbar button").forEach(b=>b.classList.remove("active"));
    document.getElementById("btnBrowse").classList.add("active");
    document.getElementById("backBtn").style.display="none";
    const delBtn=document.getElementById("deletePlBtn"); if(delBtn) delBtn.style.display="none";
    loadDir("");
};
document.getElementById("backBtn").onclick=()=>{ document.getElementById("btnBrowse").click(); };

function showAddToPlaylist(fullRel, name) {
    api("/api/playlists/list").then(r => {
        let html = "";
        r.data.forEach(pl => {
            html += `<div class="item" onclick="addSongToPl(${pl.id}, '${fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}', '${name.replace(/'/g,"\\'")}')">${escapeHtml(pl.name)}</div>`;
        });
        document.getElementById("plOptions").innerHTML = html || "暂无歌单";
        document.getElementById("addToPlModal").style.display = "flex";
    });
}
async function addSongToPl(pid, fullRel, name) {
    await api("/api/playlists/add_song", {pid: pid, song: {fullRel: fullRel, name: name}});
    document.getElementById("addToPlModal").style.display = "none";
    alert("已添加");
}

async function deleteLocalSong(fullRel, name) {
    const token = localStorage.getItem("token") || "";
    if(!await showConfirm("删除本地歌曲", "确定删除《"+name+"》吗？\n本地文件会移入“_回收站”文件夹（可恢复），并自动从所有歌单移除。", "删除")) return;
    try {
        const res = await fetch("/api/song/delete", {method:"POST", headers:{"Content-Type":"application/json","X-Conan-Token":token}, body:JSON.stringify({token:token, rel:fullRel})});
        const r = await res.json();
        showToast(r.msg || (r.ok ? "已移入回收站" : "删除失败"));
        if(!r.ok) return;
    } catch(e) {
        showToast("删除失败");
        return;
    }
    try {
        if(viewMode === "browse") await loadDir(currentRelPath || "");
        else if(viewMode === "playlist_detail" && curPlaylistId) await openPlaylist(curPlaylistId);
        else if(viewMode === "search") await renderSearch();
        else renderList();
    } catch(e) {}
}

// ============ 歌曲列表渲染 ============
function renderList(){
  if(viewMode==="recommend") return renderRecommend();
  if(viewMode==="kugou") return renderKugou();
  if(viewMode==="search") return renderSearch();
  
  let html="";
  if(viewMode==="browse") {
      const depth=currentRelPath===""?0:currentRelPath.split("/").length;
      if(depth===0){document.getElementById("pathText").innerText="👤 选择歌手";}
      else if(depth===1){document.getElementById("pathText").innerText="💿 "+currentRelPath;}
      else{document.getElementById("pathText").innerText="🎵 "+currentRelPath;}
      
      currentFolders.forEach(fd=>{
        const np=currentRelPath?currentRelPath+"/"+fd:fd;
        // 歌手层（depth 0）用酷狗风格圆形歌手图标：文件夹封面→内嵌封面→首字渐变 SVG 兜底
        let folderCover;
        if(depth===0){
          folderCover = `<img class="cover-img artist-icon" loading="lazy" src="/api/artist_icon?name=${encodeURIComponent(fd)}" onerror="this.style.display='none';this.nextElementSibling.style.display='block'"><span style="display:none;font-size:20px;width:44px;text-align:center;flex-shrink:0">🎤</span>`;
        }else{
          folderCover = `<img class="cover-img" loading="lazy" src="/api/folder_cover?path=${encodeURIComponent(np)}" onerror="this.style.display='none';this.nextElementSibling.style.display='block'"><span style="display:none;font-size:20px;width:44px;text-align:center;flex-shrink:0">📁</span>`;
        }
        html+='<div class="item" onclick="loadDir(\''+np.replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+folderCover+'<span class="txt">'+escapeHtml(fd)+'</span></div>';
      });
  }
  
  playlist.forEach((s,idx)=>{
    const fc = (loginUser && loginUser.favs && loginUser.favs.some(x=>x.fullRel===s.fullRel)) ? "liked" : "";
    const pc=(idx===curIndex)?"playing":"";
    const cpTag = s.copyright ? `<span class="cp-tag">© ${escapeHtml(s.copyright)}</span>` : '';
    const infoTag = s.ext ? `<span style="font-size:11px;color:#888;margin-left:6px">${s.ext} ${s.bitrate}kbps</span>` : '';
    
    let delBtn = "";
    if(viewMode === "playlist_detail") {
        delBtn = `<span class="dl" style="color:#f66" onclick="event.stopPropagation();removeFromPl('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}')">✖</span>`;
    }
    
    let shareAction = `<span class="share" style="cursor:pointer;color:#7ec8e3;padding:0 4px" onclick="event.stopPropagation();shareSong('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${escapeHtml(s.name)}')">📤</span>`;
    let selBox = multiSel ? `<input type="checkbox" class="sel-chk" ${selRels.indexOf(s.fullRel)>=0?'checked':''} onclick="event.stopPropagation();toggleSel('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}')">` : '';
    let actions = s.isLocal ? shareAction : "";
    if (!s.isLocal) {
        actions += `<span class="addpl" onclick="event.stopPropagation();showAddToPlaylist('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${s.name.replace(/'/g,"\\'")}')">➕</span>`
                + `<span class="dl" onclick="event.stopPropagation();downloadSong('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${escapeHtml(s.name)}')">⬇️</span>`
                + `<span class="heart ${fc}" onclick="event.stopPropagation();toggleFav('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${s.name.replace(/'/g,"\\'")}')">♥</span>`;
    }
    if (loginUser && loginUser.role === "admin" && !s.isLocal) {
        actions += `<span class="del-song" title="删除本地歌曲（移入回收站）" onclick="event.stopPropagation();deleteLocalSong('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${s.name.replace(/'/g,"\\'")}')">🗑️</span>`;
    }
    
    html+='<div class="item '+pc+'" onclick="playSong('+idx+')">'
        + selBox
        + '<img class="cover-img" src="/api/cover?path='+encodeURIComponent(s.fullRel)+'" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'">'
        + '<span style="display:none;font-size:20px;width:44px;text-align:center;">🎵</span>'
        + '<div class="txt" style="display:flex;flex-direction:column;">'
        +   `<span>${escapeHtml(s.name)}${cpTag}</span>`
        +   `<span style="font-size:11px;color:#888">${infoTag}</span>`
        + '</div>'
        + delBtn + actions
        + '</div>';
  });
  
  if(!currentFolders.length && !playlist.length) html='<div class="empty">空空如也</div>';
  document.getElementById("listBox").innerHTML=html;
  updateM3uBtn();
  if(document.getElementById("queueModal").style.display==="flex") renderQueue();
}

async function loadDir(rp){
  currentRelPath=rp; viewMode="browse";
  const data=await apiGet("/api/list?path="+encodeURIComponent(rp)+"&page=1&page_size=100");
  currentFolders=data.folders || [];
  playlist=(data.songs || []).map(s=>({name:s.name,fullRel:rp?rp+"/"+s.file:s.file,copyright:s.copyright,bitrate:s.bitrate,ext:s.ext}));
  document.getElementById("backBtn").style.display = rp ? "block" : "none";
  renderList();
}

async function removeFromPl(rel) {
    if(!curPlaylistId) return;
    await api("/api/playlists/remove_song", {pid: curPlaylistId, fullRel: rel});
    openPlaylist(curPlaylistId, document.getElementById("pathText").innerText.replace("📋 ",""));
}

// ============ 下载逻辑 ============
async function downloadSong(fullRel, name) {
    if(!loginUser) return;
    let isBought = loginUser.purchased && loginUser.purchased.includes(fullRel);
    let hasDL = loginUser.dl_vip || loginUser.role === "admin" || isBought;
    
    if(!hasDL) {
        let conf = confirm("您当前没有下载原版权限。\n是否花费 1000 积分买断该曲，或去开通下载会员？\n(如果取消，将为您下载加密的 .coman 文件)");
        if(conf) {
            if(loginUser.points < 1000) return alert("积分不足1000！请先去兑换。");
            showLoading();
            let res = await api("/api/song/buy", {path: fullRel});
            hideLoading();
            if(!res.ok) return alert(res.msg);
            alert("买断成功！开始下载原版...");
            let r = await api("/api/checktoken"); loginUser = r.user; applyUserToUI();
        }
    }
    
    let url = `/api/download?token=${token}&path=${encodeURIComponent(fullRel)}`;
    window.open(url, "_blank");
}

// ============ M3U 歌单导出 / 手机访问 ============
function updateM3uBtn(){
    const b=document.getElementById("m3uBtn");
    const inList=(viewMode==="browse"||viewMode==="playlist_detail");
    if(b) b.style.display=inList?"inline-block":"none";
    const ms=document.getElementById("multiSelBtn");
    if(ms) ms.style.display=inList?"inline-block":"none";
    const eb=document.getElementById("exportSelBtn");
    if(eb){
        eb.style.display=(multiSel&&inList)?"inline-block":"none";
        if(eb.style.display!=="none") eb.innerText="导出选中("+selRels.length+")";
    }
}
function toggleMultiSel(){
    if(!loginUser) return alert("请先登录");
    multiSel=!multiSel;
    if(!multiSel) selRels=[];
    const btn=document.getElementById("multiSelBtn");
    if(btn){
        btn.innerText=multiSel?"☑ 退出多选":"✅ 多选";
        btn.style.background=multiSel?"var(--accent)":"";
    }
    renderList();
}
function toggleSel(rel){
    if(!rel) return;
    const i=selRels.indexOf(rel);
    if(i>=0) selRels.splice(i,1); else selRels.push(rel);
    const eb=document.getElementById("exportSelBtn");
    if(eb) eb.innerText="导出选中("+selRels.length+")";
}
async function exportSelectedM3u(){
    if(!loginUser) return showToast("请先登录");
    if(!selRels.length) return showToast("请先勾选要导出的歌曲");
    let res;
    try{
        res=await fetch("/api/m3u/selected",{method:"POST",headers:{"Content-Type":"application/json","X-Conan-Token":token||""},body:JSON.stringify({token:token,rels:selRels})});
    }catch(e){ return showToast("导出失败：网络错误"); }
    if(!res.ok){
        let msg="导出失败";
        try{ msg=(await res.text())||msg; }catch(e){}
        return showToast(msg);
    }
    const blob=await res.blob();
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob);
    a.download="精选歌单_"+selRels.length+"首.m3u";
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); },600);
    showToast("已导出 "+selRels.length+" 首歌曲的 M3U 歌单");
}
function exportM3u(){
    if(!loginUser) return alert("请先登录");
    let url="/api/playlist.m3u?token="+encodeURIComponent(token)+"&fmt=m3u";
    if(viewMode==="playlist_detail" && curPlaylistId){
        url += "&plid="+curPlaylistId;
    } else if(viewMode==="browse"){
        url += "&path="+encodeURIComponent(currentRelPath||"");
    } else {
        return alert("请在“浏览”或“歌单详情”页面导出 M3U");
    }
    if(!confirm("M3U 文件内含登录凭证，导入本地播放器后请勿公开分享。继续导出？")) return;
    let relative = confirm("是否导出相对路径版本？（适合把音乐库拷到本地/别的电脑播放；取消则导出带服务器地址的版本）");
    if(relative) url += "&relative=1";
    window.open(url,"_blank");
}
document.getElementById("mobileBtn").onclick=async function(){
    const r=await api("/api/serverinfo",null,true);
    const url=(r && r.ok && r.url) ? r.url : location.origin;
    document.getElementById("mobileUrl").value=url;
    document.getElementById("mobileModal").style.display="flex";
};
function copyMobileUrl(){
    const input=document.getElementById("mobileUrl");
    input.select();
    if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(input.value).then(function(){ alert("地址已复制"); }).catch(function(){ alert("复制失败，请手动复制"); });
    } else {
        try{ document.execCommand("copy"); alert("地址已复制"); }catch(e){ alert("请手动复制"); }
    }
}
let shareRelCur="";
async function shareSong(rel, name){
    shareRelCur=rel||"";
    document.getElementById("shareSongTitle").textContent=name||"";
    const noteEl=document.getElementById("shareNote");
    const coverEl=document.getElementById("shareCover");
    const ttlEl=document.getElementById("shareTtl");
    if(noteEl) noteEl.value="";
    if(coverEl) coverEl.value="/api/cover?path="+encodeURIComponent(rel||"");
    if(ttlEl) ttlEl.value="0";
    document.getElementById("shareModal").style.display="flex";
    await regenShare();
}
async function regenShare(){
    if(!shareRelCur) return;
    const ttl=Number(document.getElementById("shareTtl").value||"0");
    const note=document.getElementById("shareNote").value.trim();
    const cover=document.getElementById("shareCover").value.trim();
    let r;
    try{
        r=await api("/api/share/create",{rel:shareRelCur,ttl_hours:ttl,note:note,cover:cover},true);
    }catch(e){ showToast("网络错误，分享生成失败"); return; }
    if(!r.ok){ showToast(r.msg||"生成分享失败"); return; }
    document.getElementById("shareUrl").value=r.url||"";
    const qr=document.getElementById("shareQr");
    if(r.qr){ qr.src=r.qr; qr.style.display="inline-block"; }
    else { qr.removeAttribute("src"); qr.style.display="none"; }
}
document.getElementById("shareTtl").onchange=regenShare;
document.getElementById("shareNote").onchange=regenShare;
document.getElementById("shareCover").onchange=regenShare;
function copyShareUrl(){
    const input=document.getElementById("shareUrl");
    input.select();
    if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(input.value).then(function(){ showToast("链接已复制"); }).catch(function(){ showToast("复制失败，请手动复制"); });
    } else {
        try{ document.execCommand("copy"); showToast("链接已复制"); }catch(e){ showToast("请手动复制"); }
    }
}

// ============ 酷狗式音效（Web Audio 实时处理，会员 / 站长专属） ============
const SOUND_FX=[
    {id:"3d",      name:"3D 丽音",   desc:"智能环绕立体声",         icon:"🔊"},
    {id:"diamond", name:"黑钻蝰蛇",  desc:"全频修复 · 低音澎湃",    icon:"💎"},
    {id:"live",    name:"Livehouse", desc:"置身音乐现场",           icon:"🎤"},
    {id:"hifi",    name:"HiFi 现场", desc:"高保真临场音质",         icon:"🎧"},
    {id:"surround",name:"3D 环绕",   desc:"三维动态声像环绕",       icon:"🌀"},
    {id:"quiet",   name:"静噪强音",  desc:"提升旋律清晰度",         icon:"🔇"},
    {id:"vocal",   name:"悦耳人声",  desc:"人声通透贴耳",           icon:"👂"},
    {id:"vast",    name:"浩瀚声场",  desc:"宽阔空间音乐会感",       icon:"🌌"},
    {id:"theater", name:"环绕剧院",  desc:"四面八方的包围感",       icon:"🏛"},
    {id:"stage",   name:"悠扬舞台",  desc:"众歌环绕如临演出现场",   icon:"🎭"},
];
const SFX_PRESET={
    "":        {band:[0,0,0,0,0],  wet:0,   lfo:0,   th:-100, ra:1},
    "3d":      {band:[-1,-2,1,2,3],wet:.14, lfo:.22, th:-24,  ra:2},
    "diamond": {band:[7,-3,1,2,3], wet:0,   lfo:0,   th:-18,  ra:4.5},
    "live":    {band:[2,0,0,1,1],  wet:.36, lfo:0,   th:-28,  ra:2},
    "hifi":    {band:[1,0,2,4,4],  wet:.06, lfo:0,   th:-40,  ra:1.5},
    "surround":{band:[0,-1,1,2,2], wet:.20, lfo:.45, th:-30,  ra:1.5},
    "quiet":   {band:[1,1,3,5,5],  wet:0,   lfo:0,   th:-20,  ra:3},
    "vocal":   {band:[-4,4,3,1,1], wet:.05, lfo:0,   th:-34,  ra:1.5},
    "vast":    {band:[2,-1,1,1,2], wet:.50, lfo:0,   th:-30,  ra:1.5},
    "theater": {band:[2,-2,0,1,2], wet:.62, lfo:0,   th:-30,  ra:1.5},
    "stage":   {band:[3,-1,0,1,1], wet:.38, lfo:.1,  th:-28,  ra:1.8},
};
let sfxId=(localStorage.getItem("conan_sound_fx")||"");
let sfxCtx=null, sfxChain=null, sfxBroken=false;

function sfxAllowed(){ return !!(loginUser && (loginUser.role==="admin" || loginUser.vip)); }
function sfxName(id){ const f=SOUND_FX.find(function(x){return x.id===id;}); return f?f.name:"原声"; }

function sfxBuildImpulse(seconds,decay){
    const rate=sfxCtx.sampleRate, len=Math.max(1,Math.floor(rate*seconds));
    const buf=sfxCtx.createBuffer(2,len,rate);
    for(let ch=0;ch<2;ch++){
        const data=buf.getChannelData(ch);
        for(let i=0;i<len;i++){
            const t=i/len;
            data[i]=(Math.random()*2-1)*Math.pow(1-t,decay)*0.5;
        }
    }
    return buf;
}

function sfxInitOnce(){
    if(sfxCtx) return sfxCtx;
    if(sfxBroken) return null;
    const AC=window.AudioContext||window.webkitAudioContext;
    if(!AC){ sfxBroken=true; return null; }
    try{
        sfxCtx=new AC();
        const src=sfxCtx.createMediaElementSource(player);
        const b0=sfxCtx.createBiquadFilter(); b0.type="lowshelf";  b0.frequency.value=90;
        const b1=sfxCtx.createBiquadFilter(); b1.type="peaking";   b1.frequency.value=320;  b1.Q.value=.8;
        const b2=sfxCtx.createBiquadFilter(); b2.type="peaking";   b2.frequency.value=1200; b2.Q.value=.9;
        const b3=sfxCtx.createBiquadFilter(); b3.type="peaking";   b3.frequency.value=3500; b3.Q.value=.9;
        const b4=sfxCtx.createBiquadFilter(); b4.type="highshelf"; b4.frequency.value=9000;
        const pan=sfxCtx.createStereoPanner?sfxCtx.createStereoPanner():null;
        const comp=sfxCtx.createDynamicsCompressor();
        const conv=sfxCtx.createConvolver();
        conv.buffer=sfxBuildImpulse(1.05,2.6);
        const wet=sfxCtx.createGain(); wet.gain.value=0;
        const master=sfxCtx.createGain(); master.gain.value=1;
        src.connect(b0); b0.connect(b1); b1.connect(b2); b2.connect(b3); b3.connect(b4);
        let tail=b4;
        if(pan){ b4.connect(pan); tail=pan; }
        tail.connect(comp); comp.connect(master);
        tail.connect(conv); conv.connect(wet); wet.connect(master);
        master.connect(sfxCtx.destination);
        const osc=sfxCtx.createOscillator(); osc.frequency.value=.17;
        const oscGain=sfxCtx.createGain(); oscGain.gain.value=0;
        osc.connect(oscGain); if(pan) oscGain.connect(pan.pan);
        try{ osc.start(); }catch(e){}
        sfxChain={b0,b1,b2,b3,b4,pan,comp,wet,oscGain,osc,conv,master};
        if(sfxCtx.state==="suspended"){ try{ sfxCtx.resume(); }catch(e){} }
        return sfxCtx;
    }catch(e){
        sfxBroken=true;
        console.warn("音效初始化失败（浏览器可能不支持）",e);
        return null;
    }
}

function applySoundFx(id,silent){
    if(!sfxAllowed()){
        if(!silent) showToast("音效是会员 / 站长专属，开通听歌VIP就能用");
        return;
    }
    const preset=SFX_PRESET[id];
    if(!preset) return;
    if(!sfxCtx) sfxInitOnce();
    if(!sfxCtx){
        if(!silent) showToast("这个浏览器不支持实时音效，用 Edge / Chrome 试试");
        return;
    }
    const ch=sfxChain, t=sfxCtx.currentTime;
    const bands=[ch.b0,ch.b1,ch.b2,ch.b3,ch.b4];
    bands.forEach(function(f,i){ try{ f.gain.setTargetAtTime(preset.band[i],t,.12); }catch(e){} });
    try{
        ch.comp.threshold.setTargetAtTime(preset.th,t,.2);
        ch.comp.ratio.setTargetAtTime(preset.ra,t,.2);
        ch.comp.knee.setTargetAtTime(18,t,.2);
        ch.comp.attack.setTargetAtTime(.006,t,.2);
        ch.comp.release.setTargetAtTime(.32,t,.2);
        ch.wet.gain.setTargetAtTime(preset.wet,t,.18);
        ch.oscGain.gain.setTargetAtTime(preset.lfo,t,.2);
    }catch(e){}
    sfxId=id||"";
    localStorage.setItem("conan_sound_fx",sfxId);
    const nowEl=document.getElementById("sfxNow");
    if(nowEl) nowEl.innerText="当前："+sfxName(sfxId);
    if(!silent) showToast(sfxId?("已切换音效："+sfxName(sfxId)):"已切回原声");
    sfxPaint();
}

function sfxPaint(){
    const box=document.getElementById("sfxList");
    if(!box) return;
    box.innerHTML=SOUND_FX.map(function(f){
        const on=(f.id===sfxId);
        return '<div onclick="applySoundFx(\''+f.id+'\')" style="cursor:pointer;padding:12px 12px 10px;border-radius:14px;transition:all .2s;'
            +'background:'+(on?'linear-gradient(135deg,rgba(233,69,96,.30),rgba(255,209,102,.16))':'rgba(255,255,255,.055)')+';'
            +'border:1px solid '+(on?'rgba(255,209,102,.72)':'rgba(255,255,255,.10)')+';'
            +(on?'box-shadow:0 8px 22px rgba(233,69,96,.28);':'')+'">'
            +'<div style="font-size:24px;line-height:1;margin-bottom:8px;font-family:\'Segoe UI Emoji\',\'Apple Color Emoji\',\'Noto Color Emoji\',sans-serif">'+f.icon+'</div>'
            +'<div style="font-weight:600;font-size:14px;color:'+(on?'#ffd166':'#e8ecf7')+'">'+escapeHtml(f.name)
            +(on?' <span style="font-size:10px;opacity:.9">· 使用中</span>':'')+'</div>'
            +'<div style="font-size:11px;color:#889;margin-top:4px;line-height:1.45">'+escapeHtml(f.desc)+'</div>'
            +'</div>';
    }).join("");
    const nowEl=document.getElementById("sfxNow");
    if(nowEl) nowEl.innerText="当前："+sfxName(sfxId);
}

document.getElementById("btnSfx").onclick=function(){
    if(!sfxAllowed()){
        showToast("音效是会员 / 站长专属：开通听歌VIP，或用站长账号登录");
        return;
    }
    sfxPaint();
    document.getElementById("sfxModal").style.display="flex";
    if(sfxId) applySoundFx(sfxId,true);
};

// ============ 歌手分类（语种 / 性别筛选 + 头像粉丝数 + 字母索引） ============
let singerFilter={lang:"all",sex:"all",letter:""};
let singerCache=[];

document.getElementById("btnSinger").onclick=function(){ renderSingerPage(); };

async function renderSingerPage(){
    viewMode="singers";
    document.querySelectorAll(".topbar button").forEach(function(b){b.classList.remove("active");});
    const self=document.getElementById("btnSinger"); if(self) self.classList.add("active");
    document.getElementById("backBtn").style.display="none";
    const delBtn=document.getElementById("deletePlBtn"); if(delBtn) delBtn.style.display="none";
    document.getElementById("pathText").innerText="🎤 歌手分类";
    await loadSingers();
}

async function loadSingers(){
    const box=document.getElementById("listBox");
    if(!box) return;
    box.innerHTML='<div class="empty">歌手加载中…</div>';
    let r=null;
    try{
        r=await apiGet("/api/singer/list?lang="+encodeURIComponent(singerFilter.lang)
            +"&sex="+encodeURIComponent(singerFilter.sex)
            +"&letter="+encodeURIComponent(singerFilter.letter)+"&limit=200");
    }catch(e){ r=null; }
    const data=(r&&r.data)||[];
    const langs=(r&&r.langs)||[];
    const sexes=(r&&r.sexes)||[];
    singerCache=data;
    const chips=function(items,cur,field){
        return items.map(function(it){
            const on=(it.k===cur);
            return '<button onclick="setSingerFilter(\''+field+'\',\''+it.k+'\')" style="padding:6px 14px;border-radius:999px;border:none;cursor:pointer;font-size:12px;transition:all .2s;'
                +(on?'background:var(--accent,#e94560);color:#fff;font-weight:600;box-shadow:0 4px 12px rgba(233,69,96,.35)':'background:rgba(255,255,255,.07);color:#9aa3bb')+'">'
                +escapeHtml(it.n)+'</button>';
        }).join("");
    };
    const head='<div style="margin-bottom:12px">'
        +'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'+chips(langs,singerFilter.lang,"lang")+'</div>'
        +'<div style="display:flex;gap:6px;flex-wrap:wrap">'+chips(sexes,singerFilter.sex,"sex")+'</div>'
        +'</div>';
    const letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ#".split("");
    const idx='<div style="flex:0 0 26px;position:sticky;top:10px;align-self:flex-start;display:flex;flex-direction:column;align-items:center;gap:1px;font-size:10px;color:#5f6a86;padding-top:4px">'
        +letters.map(function(l){
            const on=(l===singerFilter.letter);
            return '<span onclick="setSingerFilter(\'letter\',\''+(on?"":l)+'\')" title="首字母 '+l+'" style="cursor:pointer;padding:1px 4px;border-radius:5px;line-height:1.15;'
                +(on?'background:rgba(233,69,96,.45);color:#fff':'')+'">'+l+'</span>';
        }).join("")+'</div>';
    if(!data.length){
        box.innerHTML='<div style="display:flex;gap:10px;align-items:flex-start"><div style="flex:1;min-width:0">'+head
            +'<div class="empty">这个筛选下暂时没有歌手，换个条件试试</div></div>'+idx+'</div>';
        return;
    }
    document.getElementById("pathText").innerText="🎤 歌手分类 · 共 "+data.length+" 位"+(singerFilter.letter?" · "+singerFilter.letter:"");
    const rows=data.map(function(s,i){
        const fans=s.fans?(s.fans>=10000?(s.fans/10000).toFixed(1)+"万":String(s.fans)):"";
        return '<div class="item" onclick="openSinger('+i+')" style="cursor:pointer">'
            +'<img class="cover-img artist-icon" loading="lazy" referrerpolicy="no-referrer" src="'+escapeHtml(s.cover)+'" onerror="this.style.visibility=\'hidden\'">'
            +'<span class="txt" style="display:flex;flex-direction:column;min-width:0">'
            +  '<span style="display:flex;align-items:center;gap:6px">'
            +    '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(s.name)+'</span>'
            +    (s.settled?'<span style="font-size:10px;color:#7ec8ff;border:1px solid rgba(126,200,255,.45);border-radius:4px;padding:0 4px;flex-shrink:0">已入驻</span>':'')
            +  '</span>'
            +  '<span style="font-size:11px;color:#889">'+(fans?"粉丝数："+fans:"热门歌手")+'</span>'
            +'</span>'
            +'<span class="dl" title="听这位歌手的歌">🎤</span>'
            +'</div>';
    }).join("");
    box.innerHTML='<div style="display:flex;gap:10px;align-items:flex-start">'
        +'<div style="flex:1;min-width:0">'+head+rows+'</div>'+idx+'</div>';
}

function setSingerFilter(field,val){
    singerFilter[field]=val||"";
    loadSingers();
}

function openSinger(i){
    const s=singerCache[i];
    if(!s) return;
    const box=document.getElementById("searchBox");
    if(box) box.value=s.name;
    searchKind="single"; paintSearchTabs();
    searchOnlineSongs(s.name);
}

// ============ 听歌识曲（麦克风录音 → WAV → Shazam 指纹） ============
function recSetState(txt){ const el=document.getElementById("recState"); if(el) el.innerText=txt||""; }
function escapeJsStr(s){ return String(s||"").replace(/\\/g,"\\\\").replace(/'/g,"\\'").replace(/"/g,'\\"').replace(/\r?\n/g," "); }

function recMergePcm(chunks, targetRate, srcRate){
    let total=0;
    chunks.forEach(function(c){ total+=c.length; });
    const src=new Float32Array(total);
    let off=0;
    chunks.forEach(function(c){ src.set(c,off); off+=c.length; });
    if(!srcRate || Math.abs(srcRate-targetRate)<50) return src;
    const ratio=srcRate/targetRate;
    const outLen=Math.floor(total/ratio);
    const out=new Float32Array(outLen);
    for(let i=0;i<outLen;i++){
        const pos=i*ratio, i0=Math.floor(pos), i1=Math.min(total-1,i0+1), frac=pos-i0;
        out[i]=src[i0]*(1-frac)+src[i1]*frac;
    }
    return out;
}

function recEncodeWav(samples, sampleRate){
    const len=samples.length;
    const buf=new ArrayBuffer(44+len*2);
    const view=new DataView(buf);
    const wstr=function(off,s){ for(let i=0;i<s.length;i++) view.setUint8(off+i,s.charCodeAt(i)); };
    wstr(0,"RIFF"); view.setUint32(4,36+len*2,true); wstr(8,"WAVE"); wstr(12,"fmt ");
    view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
    view.setUint32(24,sampleRate,true); view.setUint32(28,sampleRate*2,true);
    view.setUint16(32,2,true); view.setUint16(34,16,true); wstr(36,"data");
    view.setUint32(40,len*2,true);
    let off=44;
    for(let i=0;i<len;i++){
        let s=Math.max(-1,Math.min(1,samples[i]));
        view.setInt16(off, s<0? s*0x8000 : s*0x7FFF, true); off+=2;
    }
    return buf;
}

function recBufToBase64(buf){
    const bytes=new Uint8Array(buf);
    let binary="";
    const chunk=0x8000;
    for(let i=0;i<bytes.length;i+=chunk){
        binary += String.fromCharCode.apply(null, bytes.subarray(i,i+chunk));
    }
    return btoa(binary);
}

document.getElementById("btnRecognize").onclick=function(){
    const res=document.getElementById("recResult"); if(res) res.innerHTML="";
    recSetState("");
    const btn=document.getElementById("recStartBtn"); if(btn){ btn.disabled=false; btn.innerText="🎤 开始识别（8 秒）"; }
    document.getElementById("recModal").style.display="flex";
};

async function recognizeByMic(){
    if(!window.isSecureContext){
        recSetState("当前地址不是安全连接，麦克风用不了");
        showToast("手机端请用 https://192.168.1.4:5001 打开页面，浏览器才允许录音");
        return;
    }
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
        showToast("这个浏览器不支持麦克风录音，换 Edge / Chrome"); return;
    }
    const btn=document.getElementById("recStartBtn");
    const resultBox=document.getElementById("recResult");
    if(resultBox) resultBox.innerHTML="";
    if(btn){ btn.disabled=true; btn.innerText="识别中…"; }
    let ctx=null, stream=null, proc=null, srcNode=null;
    try{
        stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false,channelCount:1}});
        ctx=new (window.AudioContext||window.webkitAudioContext)();
        srcNode=ctx.createMediaStreamSource(stream);
        proc=ctx.createScriptProcessor(4096,1,1);
        const chunks=[];
        proc.onaudioprocess=function(e){ chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
        srcNode.connect(proc); proc.connect(ctx.destination);
        for(let t=8;t>0;t--){
            recSetState("🎤 正在录音，请让音乐持续播放… "+t+" 秒");
            await new Promise(function(r){ setTimeout(r, t===8?900:1000); });
        }
        recSetState("处理录音…");
        try{ proc.disconnect(); }catch(e){}
        try{ srcNode.disconnect(); }catch(e){}
        try{ stream.getTracks().forEach(function(t){ t.stop(); }); }catch(e){}
        const merged=recMergePcm(chunks,16000,ctx.sampleRate);
        try{ ctx.close(); }catch(e){}
        const wav=recEncodeWav(merged,16000);
        recSetState("上传识别中…（约 3 秒）");
        const r=await api("/api/recognize", {audio:"data:audio/wav;base64,"+recBufToBase64(wav)}, true);
        if(!r || !r.ok){
            recSetState("");
            if(resultBox) resultBox.innerHTML='<div class="empty">'+escapeHtml((r&&r.msg)||"没识别出来，再试一次")+'</div>';
            return;
        }
        recSetState("识别成功 ✅");
        if(resultBox){
            resultBox.innerHTML='<div style="display:flex;gap:12px;align-items:center;padding:12px;border-radius:14px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1)">'
                +(r.cover?'<img src="'+escapeHtml(r.cover)+'" style="width:66px;height:66px;border-radius:10px;object-fit:cover;flex-shrink:0" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'">':'')
                +'<div style="min-width:0">'
                +'<div style="font-weight:700;font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(r.title||"")+'</div>'
                +'<div style="color:#889;font-size:12px;margin-top:3px">'+escapeHtml(r.artist||"")+(r.genre?" · "+escapeHtml(r.genre):"")+'</div>'
                +'<button onclick="playRecognized(\''+escapeJsStr((r.title||"")+" "+(r.artist||""))+'\')" style="margin-top:9px;background:#e94560;border:none;border-radius:8px;color:#fff;padding:7px 13px;cursor:pointer">▶ 马上听这首歌</button>'
                +'</div></div>';
        }
    }catch(e){
        recSetState("");
        const msg=(e && e.name==="NotAllowedError") ? "麦克风权限被拒绝：请在浏览器弹窗里点“允许”" : ("录音失败："+((e&&e.message)||e));
        if(resultBox) resultBox.innerHTML='<div class="empty">'+escapeHtml(msg)+'</div>';
    }finally{
        try{ if(proc) proc.disconnect(); }catch(e){}
        try{ if(srcNode) srcNode.disconnect(); }catch(e){}
        try{ if(stream) stream.getTracks().forEach(function(t){ t.stop(); }); }catch(e){}
        try{ if(ctx) ctx.close(); }catch(e){}
        if(btn){ btn.disabled=false; btn.innerText="🎤 重新识别（8 秒）"; }
    }
}

function playRecognized(q){
    closeModal("recModal");
    const box=document.getElementById("searchBox");
    if(box) box.value=q;
    searchKind="single"; paintSearchTabs();
    searchOnlineSongs(q);
}

// ============ 酷狗 MV（搜索 + 在线播放，和酷狗一样） ============
let mvCurrent=[];
let mvPlayUrls=null;
document.getElementById("btnMv").onclick=function(){
    document.getElementById("mvModal").style.display="flex";
    const inp=document.getElementById("mvSearchInput");
    if(inp && !inp.value){ inp.focus(); }
};
document.getElementById("mvSearchInput").addEventListener("keydown",function(e){
    if(e.key==="Enter") searchMv();
});
function closeMv(){
    const v=document.getElementById("mvPlayer");
    if(v){ try{v.pause();}catch(e){} v.removeAttribute("src"); v.style.display="none"; }
    mvPlayUrls=null;
    document.getElementById("mvQualityRow").style.display="none";
    document.getElementById("mvModal").style.display="none";
}
async function searchMv(){
    const kw=document.getElementById("mvSearchInput").value.trim();
    if(!kw) return showToast("请输入 MV 关键词");
    const list=document.getElementById("mvList");
    list.innerHTML='<div class="empty">MV 搜索中...</div>';
    let r;
    try{ r=await apiGet("/api/kugou/mv/search?kw="+encodeURIComponent(kw)); }catch(e){ r=null; }
    const data=(r&&r.ok)?(r.data||[]):[];
    mvCurrent=data;
    if(!data.length){ list.innerHTML='<div class="empty">没找到相关 MV，换个关键词试试</div>'; return; }
    list.innerHTML=data.map(function(m,i){
        const dur=Number(m.duration||0);
        const durText=Math.floor(dur/60)+":"+String(dur%60).padStart(2,"0");
        return '<div class="item" onclick="playMv('+i+')">'
          +'<img class="cover-img" src="'+escapeHtml(m.cover||"")+'" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'">'
          +'<span style="display:none;font-size:20px;width:44px;text-align:center;flex-shrink:0">🎬</span>'
          +'<div class="txt"><span>'+escapeHtml(m.name)+'</span><span style="font-size:11px;color:#888">'+escapeHtml(m.singer)+' · '+durText+(m.heat?(' · 热度 '+m.heat):'')+'</span></div>'
          +'</div>';
    }).join("");
}
async function playMv(i){
    const m=mvCurrent[i];
    if(!m) return;
    const list=document.getElementById("mvList");
    list.innerHTML='<div class="empty">正在解析 MV 播放地址...</div>';
    let r;
    try{ r=await apiGet("/api/kugou/mv/play?hash="+encodeURIComponent(m.hash)); }catch(e){ r=null; }
    const info=(r&&r.ok)?r.data:null;
    if(!info || !info.urls || !Object.keys(info.urls).length){
        showToast("MV 播放源解析失败，换一个结果试试");
        list.innerHTML=mvCurrent.map(function(mm,ii){
            return '<div class="item" onclick="playMv('+ii+')">'+escapeHtml(mm.name)+' - '+escapeHtml(mm.singer)+'</div>';
        }).join("");
        return;
    }
    mvPlayUrls=info.urls;
    applyMvQuality();
    document.getElementById("mvQualityRow").style.display="block";
    const v=document.getElementById("mvPlayer");
    v.style.display="block";
    try{ player.pause(); }catch(e){}
}
function applyMvQuality(){
    if(!mvPlayUrls) return;
    const sel=document.getElementById("mvQuality").value;
    let url=mvPlayUrls[sel]||mvPlayUrls.rq||mvPlayUrls.sq||mvPlayUrls.le||"";
    if(!url){ showToast("该画质暂无直链"); return; }
    const v=document.getElementById("mvPlayer");
    v.src=url;
    v.load();
    v.play().catch(function(){});
}
document.getElementById("mvQuality").onchange=applyMvQuality;

// ============ 酷狗云端下载（合并 musicDownload / musicdl） ============
let kugouAlbums = [];
let searchOnline = [];
let kugouStatusHtml = "";
let kugouSkipBlack = localStorage.getItem("conan_skip_black") !== "0";
let kugouSkipExisting = localStorage.getItem("conan_skip_existing") !== "0";

document.getElementById("btnKugou").onclick = () => {
    viewMode = "kugou";
    document.querySelectorAll(".topbar button").forEach(b=>b.classList.remove("active"));
    document.getElementById("btnKugou").classList.add("active");
    document.getElementById("backBtn").style.display = "none";
    const delBtn=document.getElementById("deletePlBtn"); if(delBtn) delBtn.style.display="none";
    renderKugou();
};

async function renderKugou(){
  updateM3uBtn();
  document.getElementById("pathText").innerHTML = '🌐 酷狗云端音乐库（管理员可下载入库）';
  let html = '<div style="margin-bottom:15px; display:flex; gap:8px; flex-wrap:wrap; align-items:center;">'
           + '<input id="kugouSearchInput" type="text" placeholder="输入歌手 / 专辑 / 歌曲名" style="flex:1;min-width:180px;padding:12px;border-radius:10px;border:none;background:var(--panel);color:var(--text);" value="'+escapeHtml(kugouKw)+'">'
           + '<button onclick="doKugouSearch()" style="padding:12px 20px;background:#07c160;color:#fff;border:none;border-radius:10px;cursor:pointer;font-weight:bold;">搜索</button>'
           + '<select id="kugouQuality" title="下载音质档（和酷狗一致）" style="padding:11px 8px;border-radius:10px;border:none;background:var(--panel);color:var(--text);cursor:pointer;">'
           + '<option value="auto">音质：智能</option><option value="standard">标准 128k</option><option value="high">高品 320k</option><option value="lossless">无损 FLAC</option><option value="hires">Hi-Res</option>'
           + '</select>'
           + '<label style="font-size:13px;color:var(--text-mute);display:flex;align-items:center;gap:4px;white-space:nowrap;"><input id="kugouSkipBlack" type="checkbox" '+(kugouSkipBlack?'checked':'')+' onchange="toggleKugouSkipBlack(this.checked)"> 自动跳过黑名单</label>'
           + '<label style="font-size:13px;color:var(--text-mute);display:flex;align-items:center;gap:4px;white-space:nowrap;"><input id="kugouSkipExisting" type="checkbox" '+(kugouSkipExisting?'checked':'')+' onchange="toggleKugouSkipExisting(this.checked)"> 跳过已有文件</label>'
           + ((loginUser && loginUser.role==="admin") ? '<button onclick="showKugouTasks()" style="padding:12px 16px;background:#3498db;color:#fff;border:none;border-radius:10px;cursor:pointer;">📋 任务</button>' : '')
           + ((loginUser && loginUser.role==="admin") ? '<button onclick="scanLibrary()" style="padding:12px 16px;background:#8e44ad;color:#fff;border:none;border-radius:10px;cursor:pointer;">🧹 库扫描</button>' : '')
           + ((loginUser && loginUser.role==="admin") ? '<button onclick="scanAlbumAudit()" style="padding:12px 16px;background:#2980b9;color:#fff;border:none;border-radius:10px;cursor:pointer;">📚 专辑补缺</button>' : '')
           + '</div>';
  html += '<div id="kugouDlStatus">'+kugouStatusHtml+'</div>';

  if(!kugouKw) {
    html += '<div class="empty">请输入歌手 / 专辑 / 歌曲名，查看酷狗云端的全部专辑和歌曲</div>';
    document.getElementById("listBox").innerHTML = html;
    bindKugouQuality();
    return;
  }

  html += '<div id="kugouResultsBox" style="color:#888;text-align:center;padding:20px">加载中...</div>';
  document.getElementById("listBox").innerHTML = html;
  bindKugouQuality();

  const res = await apiGet("/api/kugou/tree?kw=" + encodeURIComponent(kugouKw));
  let box = document.getElementById("kugouResultsBox");
  if(!box) return;

  if(!res.ok || !res.data || !res.data.length) {
     box.innerHTML = '<div style="text-align:center;color:#888;padding:20px">未找到相关歌手或专辑</div>';
     return;
  }

  kugouAlbums = res.data;
  let isAdmin = loginUser && loginUser.role === "admin";
  let rHtml = "";
  if (res.engine === false) {
     rHtml += '<div class="engine-warn">⚠️ 服务器未安装 <b>musicdl</b>，暂时无法下载入库。请在服务器上运行 <b>pip install musicdl</b> 后重启本程序。</div>';
  }

  res.data.forEach((album, i) => {
     let pending = album.songs.filter(s=>!s.local_rel).length;
     let albumBtn = "";
     if (isAdmin && pending > 0) {
        albumBtn = '<button class="dl-btn" onclick="event.stopPropagation(); startKugouAlbumDownload('+i+', this)">⬇️ 整张专辑('+pending+')</button>';
     }
     let coverUrl = album.cover || "";
     let albumCover = coverUrl
        ? '<img class="cover-img" loading="lazy" referrerpolicy="no-referrer" src="'+escapeHtml(coverUrl)+'" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'"><span style="display:none">💿</span>'
        : '<span>💿</span>';
     let blackCount = album.songs.filter(s=>s.blacklisted).length;
     rHtml += '<div class="item" onclick="toggleKugouAlbum('+i+')" style="cursor:pointer;">'
          + albumCover
          + '<span class="txt">《' + escapeHtml(album.album) + '》 - ' + escapeHtml(album.singer) + '</span>'
          + (blackCount ? '<span style="color:#f39c12;font-size:12px;">🚫黑名单 '+blackCount+'</span>' : '')
          + '<span style="color:#888;font-size:12px;">' + album.songs.length + ' 首歌</span>'
          + albumBtn
          + '</div>';

     rHtml += '<div id="kugou-songs-'+i+'" style="display:none; padding-left:20px; margin-bottom:10px;">';
     album.songs.forEach((song, j) => {
        let localTag = song.local_rel ? '<span class="cp-tag" style="background:#07c160">✅ 已入库</span>' : '';
        let blackTag = song.blacklisted ? '<span class="cp-tag" style="background:#f39c12" title="黑名单关键词">🚫 '+escapeHtml(song.blacklisted)+'</span>' : '';
        let tagHtml = localTag || ((song.is_auth ? '<span class="cp-tag" style="background:#8e44ad">已授权</span>' : '<span style="color:#e94560;font-size:12px;">暂无版权</span>') + blackTag);
        let checked = (!song.local_rel && !song.blacklisted) ? ' checked' : '';
        let chkHtml = song.local_rel ? '' : '<input class="kugou-song-chk" type="checkbox" id="kugouChk-'+i+'-'+j+'" '+checked+' onclick="event.stopPropagation()" title="勾选后才会批量下载">';

        let actionBtn = "";
        if (song.local_rel) {
            actionBtn = '<button class="dl-btn" style="background:#3498db" onclick="event.stopPropagation(); playKugouLocal(kugouAlbums['+i+'].songs['+j+'].local_rel, kugouAlbums['+i+'].songs['+j+'].name)">▶ 播放</button>';
        } else if (isAdmin) {
            actionBtn = '<button class="dl-btn" onclick="event.stopPropagation(); startKugouDownload(kugouAlbums['+i+'].songs['+j+'], this)">⬇️ 下载</button>';
        }
        let authBtn = "";
        if (isAdmin) {
            let btnText = song.is_auth ? "取消授权" : "授权";
            let btnBg = song.is_auth ? "#e94560" : "#8e44ad";
            authBtn = '<button class="dl-btn" style="background:'+btnBg+'" onclick="event.stopPropagation(); toggleKugouAuthByIndex('+i+','+j+')">'+btnText+'</button>';
        }

        rHtml += '<div class="item" onclick="kugouRowClick('+i+','+j+')" style="background:rgba(128,128,128,0.1); margin:4px 0; padding:8px 14px; font-size:14px;">'
             + chkHtml
             + '<span>☁️</span>'
             + '<span class="txt">' + escapeHtml(song.name) + ' <span style="color:#889;font-size:12px;">- ' + escapeHtml(song.singer) + '</span></span>'
             + tagHtml
             + authBtn
             + actionBtn
             + '</div>';
     });
     rHtml += '</div>';
  });

  box.innerHTML = rHtml;
}

function doKugouSearch(){
  kugouKw = document.getElementById("kugouSearchInput").value.trim();
  kugouStatusHtml = "";
  renderKugou();
}
function toggleKugouSkipBlack(checked){
  kugouSkipBlack = !!checked;
  localStorage.setItem("conan_skip_black", kugouSkipBlack ? "1" : "0");
}
function toggleKugouSkipExisting(checked){
  kugouSkipExisting = !!checked;
  localStorage.setItem("conan_skip_existing", kugouSkipExisting ? "1" : "0");
}
function toggleKugouAlbum(i){
  let box = document.getElementById("kugou-songs-"+i);
  if(!box) return;
  if(box.style.display === "none") box.style.display = "block";
  else box.style.display = "none";
}
function kugouRowClick(i, j){
  const song = kugouAlbums[i] && kugouAlbums[i].songs[j];
  if(!song) return;
  if(song.local_rel) playKugouLocal(song.local_rel, song.name);
  else alert(song.is_auth ? "云端音乐，管理员可点击“⬇️ 下载”入库" : "云端音乐暂无版权，管理员可尝试下载入库");
}
function playKugouLocal(rel, name){
  if(!rel) return;
  playlist = [{name:name, fullRel:rel, copyright:"", bitrate:0, ext:""}];
  curIndex = 0;
  playSong(0);
}
function toggleKugouAuthByIndex(i, j, ev){
  if(ev) ev.stopPropagation();
  const s = kugouAlbums[i] && kugouAlbums[i].songs[j];
  if(s) toggleKugouAuth(s.auth_key, s.is_auth);
}
function toggleKugouAuthBySearch(i, ev){
  if(ev) ev.stopPropagation();
  const s = searchOnline[i];
  if(s) toggleKugouAuth(s.auth_key, s.is_auth);
}
async function toggleKugouAuth(authKey, currentState){
  if(!loginUser || loginUser.role !== "admin") return;
  const targetState = !currentState;
  const r = await api("/api/admin/kugou/auth", {auth_key: authKey, is_auth: targetState});
  if(r.ok) {
    if (viewMode === "kugou") renderKugou();
    else if (viewMode === "search") renderSearch();
  } else alert(r.msg);
}

function getDlQuality(){
  const v=localStorage.getItem("conan_dl_quality");
  return (["auto","standard","high","lossless","hires"].indexOf(v)>=0)?v:"auto";
}
function bindKugouQuality(){
  const q=document.getElementById("kugouQuality");
  if(!q) return;
  q.value=getDlQuality();
  q.onchange=function(){ localStorage.setItem("conan_dl_quality",q.value); showToast("下载音质已切换为："+q.options[q.selectedIndex].text); };
}
function kugouSetStatus(html){
  kugouStatusHtml = html;
  const bar = document.getElementById("kugouDlStatus");
  if(bar) bar.innerHTML = html;
}
async function startKugouDownload(song, btn){
  if(!song) return;
  if(song.local_rel){ playKugouLocal(song.local_rel, song.name); return; }
  if(btn){ btn.disabled = true; btn.innerText = "排队中..."; }
  const songQ = Object.assign({}, song, {quality: getDlQuality()});
  const r = await api("/api/kugou/download/start", {songs:[songQ], skip_existing:!!kugouSkipExisting, quality:getDlQuality()});
  if(!r.ok){
    if(btn && btn.isConnected){ btn.disabled = false; btn.innerText = "⬇️ 下载"; }
    return alert(r.msg || "提交下载失败");
  }
  pollKugouTask(r.task_id, btn, "single");
}
async function startKugouAlbumDownload(i, btn){
  const album = kugouAlbums[i];
  if(!album) return;
  const songs = [];
  album.songs.forEach((s, j) => {
    if (s.local_rel) return;
    const cb = document.getElementById("kugouChk-"+i+"-"+j);
    if (cb && !cb.checked) return;
    if (kugouSkipBlack && s.blacklisted) return;
    songs.push(Object.assign({}, s, {
      album: album.album || s.album || "",
      album_id: album.album_id || s.album_id || "",
      album_artist: album.singer || s.album_artist || "",
      singer: s.singer || album.singer || "",
      cover: album.cover || s.cover || "",
      quality: getDlQuality()
    }));
  });
  if(!songs.length) return alert("没有勾选可下载歌曲；黑名单曲目默认不勾选，如需下载请取消“自动跳过黑名单”并手动勾选");
  if(btn){ btn.disabled = true; btn.innerText = "提交中..."; }
  const r = await api("/api/kugou/download/start", {
    songs: songs,
    expand_album: true,
    album: album.album || "",
    album_id: album.album_id || "",
    singer: album.singer || "",
    skip_blacklist: !!kugouSkipBlack,
    skip_existing: !!kugouSkipExisting,
    quality: getDlQuality()
  });
  if(!r.ok){
    if(btn && btn.isConnected){ btn.disabled = false; btn.innerText = "⬇️ 整张专辑"; }
    return alert(r.msg || "提交下载失败");
  }
  pollKugouTask(r.task_id, null, "album");
}
async function pollKugouTask(taskId, btn, mode){
  for(let tick = 0; tick < 6000; tick++){
    let r;
    try { r = await api("/api/kugou/download/status", {task_id: taskId}, true); }
    catch(e) { r = {ok:false, msg:"网络错误"}; }
    if(!r.ok || !r.task){
      if(btn && btn.isConnected){ btn.disabled = false; btn.innerText = "⬇️ 下载"; }
      kugouSetStatus('<div class="dl-bar">任务状态获取失败：'+escapeHtml((r && r.msg) || "任务已过期")+'</div>');
      return;
    }
    const t = r.task;
    const pct = Math.max(0, Math.min(100, Math.round(t.progress || 0)));
    kugouSetStatus('<div class="dl-bar"><div>'+escapeHtml(t.message || "处理中...")+'</div>'
      + '<div class="track"><div class="fill" style="width:'+pct+'%"></div></div>'
      + '<div style="margin-top:6px;color:#888">进度 '+pct+'% · 成功 '+(t.done_count||0)+' / '+(t.total||0)+' · 失败 '+(t.failed_count||0)+'</div></div>');
    if(btn && btn.isConnected){
      if(t.status === "done"){ btn.innerText = "✅ 已入库"; btn.disabled = true; }
      else if(t.status === "error"){ btn.innerText = "❌ 失败"; btn.disabled = false; }
      else if(t.status === "paused"){ btn.innerText = "⏸ 已暂停"; btn.disabled = false; }
      else if(t.status === "cancelled"){ btn.innerText = "⏹ 已取消"; btn.disabled = false; }
      else { btn.innerText = "下载中 " + pct + "%"; }
    }
    if(["done","error","paused","cancelled"].includes(t.status)){
      if(t.status==="done") showToast("✅ 已入库：" + (t.message || "下载任务完成"));
      else if(t.status==="error") showToast("❌ 下载失败：" + (t.message || "请查看任务详情"));
      if(document.getElementById("kugouTaskModal").style.display === "flex") loadKugouTasks();
      setTimeout(function(){
        if(viewMode === "kugou") renderKugou();
        else if(viewMode === "search") renderSearch();
      }, 600);
      return;
    }
    await new Promise(res => setTimeout(res, 1200));
  }
  if(btn && btn.isConnected){ btn.disabled = false; btn.innerText = "⬇️ 下载"; }
  kugouSetStatus('<div class="dl-bar">下载任务等待超时，请稍后重试</div>');
}

// ============ 酷狗下载任务管理（暂停 / 继续 / 取消） ============
let kugouTaskTimer=null;
let selectedKugouTaskIds=new Set();
function fmtTaskEta(sec){
    if(sec===null || sec===undefined) return "排队中，等待并发空位";
    if(sec<=0) return "即将完成";
    const m=Math.floor(sec/60), s=Math.floor(sec%60);
    return "预计剩余 "+(m>0?m+"分":"")+s+"秒";
}
function toggleTaskSelection(id, checked){
    if(checked) selectedKugouTaskIds.add(id); else selectedKugouTaskIds.delete(id);
}
async function selectedTaskAction(action){
    const ids=Array.from(selectedKugouTaskIds);
    if(!ids.length) return alert("请先勾选任务");
    if(action==="cancel" && !confirm("确定批量取消选中的 "+ids.length+" 个任务？")) return;
    let r;
    try{ r=await api("/api/kugou/download/batch",{task_ids:ids,action:action},true); }
    catch(e){ return alert("网络错误"); }
    if(!r.ok) alert(r.msg||"操作失败");
    else { selectedKugouTaskIds.clear(); }
    loadKugouTasks();
}
async function showKugouTasks(){
    document.getElementById("kugouTaskModal").style.display="flex";
    loadKugouTasks();
    if(kugouTaskTimer) clearInterval(kugouTaskTimer);
    kugouTaskTimer=setInterval(loadKugouTasks,3000);
}
async function openSettings(){
    if(!loginUser || loginUser.role!=="admin") return alert("只有管理员可以打开设置");
    const modal=document.getElementById("settingsModal");
    modal.style.display="flex";
    const body=document.getElementById("settingsBody");
    body.style.opacity="0.6";
    let r;
    try{ r=await api("/api/admin/settings/get",{},true); }
    catch(e){ body.style.opacity="1"; return alert("读取设置失败：网络错误"); }
    if(!r.ok){ body.style.opacity="1"; return alert(r.msg||"读取设置失败"); }
    const d=r.data||{};
    const vals={
      setConcurrency:d.download_concurrency, setMaxActive:d.download_max_active,
      setTimeout:d.download_timeout, setSpeedLimit:d.download_speed_limit_kb,
      setDiskWarn:d.disk_warn_gb, setDiskStop:d.disk_stop_gb,
      setLogMb:d.log_rotate_mb, setCoverSize:d.cover_size, setCoverRetry:d.cover_retry,
      setLoginMax:d.login_max_fail, setLockMin:d.login_lock_minutes,
      setScanDays:d.album_scan_days, setBlacklist:d.blacklist_keywords,
      setWhitelist:d.whitelist_keywords, setArchive:d.archive_template,
      setLyricBlacklist:d.lyric_blacklist_keywords,
      setAdminNote:d.admin_note, setAdminIps:(Array.isArray(d.admin_ips)?d.admin_ips.join(","):d.admin_ips),
      setBroadcast:d.broadcast_text,
      setGuestMode:d.guest_mode,
      setQuality:d.download_quality||"auto"
    };
    for(const [id,v] of Object.entries(vals)){
        const el=document.getElementById(id); if(!el) continue;
        if(id==="setAdminIps" && Array.isArray(v)) el.value=v.join(",");
        else el.value=(v===undefined||v===null)?"":v;
    }
    const rt=r.runtime||{};
    document.getElementById("settingsRuntime").textContent=
      "运行中并发 "+rt.max_concurrent+" / 上限 "+rt.max_active+" / 磁盘剩余 "+(rt.free_gb||"?")+" GB / 音乐库："+(rt.music_root||"");
    body.style.opacity="1";
}
async function saveSettings(){
    const payload={
      download_concurrency:Number(document.getElementById("setConcurrency").value),
      download_max_active:Number(document.getElementById("setMaxActive").value),
      download_timeout:Number(document.getElementById("setTimeout").value),
      download_speed_limit_kb:Number(document.getElementById("setSpeedLimit").value),
      disk_warn_gb:Number(document.getElementById("setDiskWarn").value),
      disk_stop_gb:Number(document.getElementById("setDiskStop").value),
      log_rotate_mb:Number(document.getElementById("setLogMb").value),
      cover_size:Number(document.getElementById("setCoverSize").value),
      cover_retry:Number(document.getElementById("setCoverRetry").value),
      login_max_fail:Number(document.getElementById("setLoginMax").value),
      login_lock_minutes:Number(document.getElementById("setLockMin").value),
      album_scan_days:Number(document.getElementById("setScanDays").value),
      blacklist_keywords:document.getElementById("setBlacklist").value.trim(),
      whitelist_keywords:document.getElementById("setWhitelist").value.trim(),
      lyric_blacklist_keywords:document.getElementById("setLyricBlacklist").value.trim(),
      archive_template:document.getElementById("setArchive").value.trim(),
      admin_note:document.getElementById("setAdminNote").value.trim(),
      broadcast_text:document.getElementById("setBroadcast").value.trim(),
      admin_ips:document.getElementById("setAdminIps").value.split(/[,，、\n]+/).map(x=>x.trim()).filter(Boolean),
      guest_mode:document.getElementById("setGuestMode").value,
      download_quality:document.getElementById("setQuality").value
    };
    let r;
    try{ r=await api("/api/admin/settings/save",{settings:payload},true); }
    catch(e){ return alert("保存失败：网络错误"); }
    if(!r.ok) return alert(r.msg||"保存失败");
    alert(r.msg||"设置已保存");
    await openSettings();
    await loadBroadcast();
    if(viewMode==="kugou") loadKugouTasks();
}
async function showAdminLogs(){
    document.getElementById("logModal").style.display="flex";
    await loadAdminLogs();
}
async function loadAdminLogs(){
    const body=document.getElementById("logBody");
    body.textContent="加载中...";
    let r;
    try{ r=await api("/api/admin/logs",{lines:200,filter:document.getElementById("logFilter").value},true); }
    catch(e){ body.textContent="读取失败：网络错误"; return; }
    if(!r.ok){ body.textContent=(r.msg||"读取失败"); return; }
    body.textContent=(r.data||[]).join("\n") || "暂无日志";
    body.scrollTop=body.scrollHeight;
}
async function backupSystemFiles(){
    if(!confirm("立即备份 config.json 和下载任务数据？（不备份音频）")) return;
    let r;
    try{ r=await api("/api/admin/backup",{},true); }
    catch(e){ return alert("备份失败：网络错误"); }
    alert(r.ok?(r.msg||"备份完成"):(r.msg||"备份失败"));
}
function closeKugouTasks(){
    document.getElementById("kugouTaskModal").style.display="none";
    if(kugouTaskTimer){ clearInterval(kugouTaskTimer); kugouTaskTimer=null; }
}
async function loadKugouTasks(){
    const box=document.getElementById("kugouTaskList");
    if(!box) return;
    let r;
    try{ r=await api("/api/kugou/download/list",null,true); }
    catch(e){ box.innerHTML='<div class="empty">读取任务失败</div>'; return; }
    if(!r.ok){ box.innerHTML='<div class="empty">'+escapeHtml(r.msg||"无权限")+'</div>'; return; }
    let topHtml = '<div class="task-actions" style="margin-bottom:8px;flex-wrap:wrap">'
      + '<button style="background:#0f3460" onclick="repairKugouLyrics()">🎵 检查/补歌词</button>'
      + '<button style="background:#e67e22" onclick="selectedTaskAction(\'priority\')">⭐ 选中优先</button>'
      + '<button style="background:#f39c12" onclick="selectedTaskAction(\'pause\')">⏸ 批量暂停</button>'
      + '<button style="background:#07c160" onclick="selectedTaskAction(\'resume\')">▶ 批量继续</button>'
      + '<button style="background:#e94560" onclick="selectedTaskAction(\'cancel\')">⏹ 批量取消</button>'
      + '</div>';
    if(r.engine===false){
        topHtml += '<div class="engine-warn">⚠️ 服务器未安装 musicdl，任务不会开始下载。请运行 <b>pip install musicdl</b> 后重启服务。</div>';
    }
    const labels={queued:"排队等待",running:"下载中",pausing:"暂停中",paused:"暂停等待",done:"已完成",error:"失败",cancelled:"已取消",cancelling:"取消中"};
    if(!r.data || !r.data.length){ box.innerHTML=topHtml+'<div class="empty">暂无下载任务</div>'; return; }
    let html=topHtml+'<div style="color:#888;font-size:12px;margin-bottom:8px">运行中 '+ (r.active||0) +' / 并发上限 '+ (r.max_concurrent||3) +' · 勾选任务后可批量操作</div>';
    r.data.forEach(function(t){
        const pct=Math.max(0,Math.min(100,Math.round(t.progress||0)));
        let btns="";
        if(t.pauseable) btns+='<button style="background:#f39c12" onclick="kugouTaskAction(\''+t.id+'\',\'pause\')">⏸ 暂停</button>';
        if(t.resumable) btns+='<button style="background:#07c160" onclick="kugouTaskAction(\''+t.id+'\',\'resume\')">▶ 继续</button>';
        if(t.status==="queued") btns+='<button style="background:#e67e22" onclick="kugouTaskAction(\''+t.id+'\',\'priority\')">⭐ 优先</button>';
        if(t.retryable) btns+='<button style="background:#9b59b6" onclick="kugouTaskAction(\''+t.id+'\',\'retry\')">🔁 重试失败</button>';
        if(t.cancellable) btns+='<button style="background:#e94560" onclick="kugouTaskAction(\''+t.id+'\',\'cancel\')">⏹ 取消</button>';
        const eta = t.status==="running" ? fmtTaskEta(t.eta_seconds) : (t.status==="queued" ? fmtTaskEta(null) : "");
        html+='<div class="task-card" style="border-left:3px solid '+(t.priority>0?"#e67e22":(t.status==="running"?"#3498db":(t.status==="paused"?"#f39c12":(t.status==="error"?"#e94560":"#555"))))+'">'
           + '<div class="task-head"><div style="display:flex;align-items:flex-start;gap:8px"><input type="checkbox" '+(selectedKugouTaskIds.has(t.id)?"checked":"")+' onchange="toggleTaskSelection(\''+t.id+'\',this.checked)" style="margin-top:4px">'+(t.priority>0?'<span style="color:#e67e22">⭐</span> ':'')+'<div><b>'+escapeHtml(t.message||"下载任务")+'</b><div style="color:#888;margin-top:3px">共 '+(t.total||0)+' 首 · 成功 '+(t.done_count||0)+' · 失败 '+(t.failed_count||0)+(t.skipped_count?' · 跳过 '+t.skipped_count:'')+(eta?' · '+escapeHtml(eta):'')+'</div></div></div>'
           + '<span class="task-status '+escapeHtml(t.status)+'" style="'+(t.status==="queued"?"color:#e67e22":(t.status==="paused"?"color:#f39c12":(t.status==="error"?"color:#e94560":"")))+'">'+escapeHtml(t.status_label||labels[t.status]||t.status)+'</span></div>'
           + '<div class="task-track"><div class="task-fill" style="width:'+pct+'%"></div></div>'
           + '<div style="color:#888">进度 '+pct+'%</div>'
           + (btns?'<div class="task-actions">'+btns+'</div>':'')
           + '<div class="task-items">';
        (t.items||[]).slice(0,12).forEach(function(it){
            const icon=it.status==="done"?'✅':(it.status==="failed"?'❌':(it.status==="skipped"?'⏭':(it.status==="running"?'⬇️':(it.status==="cancelled"?'⏹':'⏳'))));
            const fail=it.status==="failed"&&it.fail_label?' · ['+escapeHtml(it.fail_label)+']':'';
            html+='<div class="'+(it.status==="done"?'ok':(it.status==="failed"?'bad':''))+'">'+icon+' '+escapeHtml(it.song||"")+' - '+escapeHtml(it.singer||"")+fail+(it.msg?' · '+escapeHtml(it.msg):'')+'</div>';
        });
        if((t.items||[]).length>12) html+='<div>… 还有 '+((t.items||[]).length-12)+' 首</div>';
        html+='</div></div>';
    });
    box.innerHTML=html;
}
async function kugouTaskAction(taskId, action){
    if(action==="cancel" && !confirm("确定取消这个下载任务？")) return;
    let path="/api/kugou/download/"+action;
    let payload={task_id:taskId};
    if(action==="priority") payload.priority=1;
    let r;
    try{ r=await api(path,payload,true); }
    catch(e){ return alert("网络错误"); }
    if(!r.ok) alert(r.msg||"操作失败");
    loadKugouTasks();
    if(viewMode==="kugou") setTimeout(renderKugou,300);
}
// ============ 音乐库扫描 / 批量补歌词、补封面 ============
function libraryItemHtml(it, kind){
    let badge = it.filename_issue ? '<span style="color:#f39c12;font-size:12px">⚠ '+escapeHtml(it.filename_issue)+'</span>' : '';
    let extra = "";
    if(kind === "cover") {
        if(!it.embedded_cover && it.folder_cover) extra = '<span style="color:#888;font-size:12px">有文件夹封面，待写入标签</span>';
        else if(it.embedded_cover && !it.folder_cover) extra = '<span style="color:#888;font-size:12px">有内嵌封面，可生成文件夹封面</span>';
        else extra = '<span style="color:#e94560;font-size:12px">缺封面</span>';
    }
    if(kind === "lyric") {
        const st = it.lrc_state || "missing";
        const label = st === "empty" ? "歌词是空文件" : (st === "invalid" ? "歌词无有效时间轴/文本" : "缺歌词");
        extra = '<span style="color:#e94560;font-size:12px">'+label+'</span>';
    }
    if(kind === "audio") extra = '<span style="color:#e94560;font-size:12px">下载记录存在，但音频文件找不到</span>';
    return '<div style="padding:6px 0;border-bottom:1px solid rgba(128,128,128,.15)">'
        + '<div style="color:var(--text)">'+escapeHtml(it.name || it.rel)+' '+badge+'</div>'
        + '<div style="color:#888;font-size:12px">'+escapeHtml(it.rel)+' '+extra+'</div>'
        + '</div>';
}
function renderLibraryScan(data){
    const body = document.getElementById("libraryScanBody");
    if(!body) return;
    let html = '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px">'
        + '<div>🎵 音频：<b>'+data.total+'</b> 首</div>'
        + '<div>📥 记录丢失音频：<b style="color:#e94560">'+(data.audio_missing||0)+'</b></div>'
        + '<div>📝 缺歌词：<b style="color:#e94560">'+data.lyric_missing+'</b></div>'
        + '<div>🖼 缺封面：<b style="color:#f39c12">'+data.cover_missing+'</b></div>'
        + '<div>🏷 待写标签封面：<b>'+data.cover_embed_pending+'</b></div>'
        + '<div>🖼 待生成文件夹封面：<b>'+(data.folder_cover_pending||0)+'</b></div>'
        + '<div style="grid-column:1/-1">⚠ 文件名异常：<b>'+data.filename_issues+'</b></div>'
        + '</div>';
    if(data.audio_items && data.audio_items.length){
        html += '<div style="font-weight:bold;margin:10px 0 4px">下载记录里找不到音频</div>';
        data.audio_items.slice(0,10).forEach(it=>{ html += libraryItemHtml(it, "audio"); });
    }
    if(data.lyric_items && data.lyric_items.length){
        html += '<div style="font-weight:bold;margin:10px 0 4px">缺歌词清单</div>';
        data.lyric_items.slice(0,20).forEach(it=>{ html += libraryItemHtml(it, "lyric"); });
        if(data.lyric_items.length>20) html += '<div style="color:#888;font-size:12px">仅显示前 20 条，修复时按全部清单处理</div>';
    } else {
        html += '<div style="color:#07c160;margin:8px 0">✅ 歌词都正常</div>';
    }
    if(data.cover_items && data.cover_items.length){
        html += '<div style="font-weight:bold;margin:14px 0 4px">缺封面 / 待写标签清单</div>';
        data.cover_items.slice(0,20).forEach(it=>{ html += libraryItemHtml(it, "cover"); });
        if(data.cover_items.length>20) html += '<div style="color:#888;font-size:12px">仅显示前 20 条</div>';
    } else {
        html += '<div style="color:#07c160;margin:8px 0">✅ 封面都正常</div>';
    }
    if(data.filename_items && data.filename_items.length){
        html += '<div style="font-weight:bold;margin:14px 0 4px">文件名异常</div>';
        data.filename_items.slice(0,10).forEach(it=>{ html += libraryItemHtml(it, "file"); });
    }
    body.innerHTML = html;
}
async function scanLibrary(){
    if(!loginUser || loginUser.role !== "admin") return alert("只有管理员可以扫描音乐库");
    const modal = document.getElementById("libraryScanModal");
    const body = document.getElementById("libraryScanBody");
    if(modal) modal.style.display = "flex";
    if(body) body.innerHTML = '<div style="color:#888;padding:20px;text-align:center">正在扫描音乐库，请稍候...</div>';
    let r;
    try { r = await api("/api/admin/library/scan", {}, true); }
    catch(e) { if(body) body.innerHTML = '<div class="empty">扫描失败：网络错误</div>'; return; }
    if(!r.ok){ if(body) body.innerHTML = '<div class="empty">'+escapeHtml(r.msg||"扫描失败")+'</div>'; return; }
    renderLibraryScan(r.data || {});
}
async function libraryTool(action){
    if(!loginUser || loginUser.role !== "admin") return alert("只有管理员可以使用库工具");
    const modal=document.getElementById("libraryScanModal");
    const body=document.getElementById("libraryScanBody");
    if(modal) modal.style.display="flex";
    if(body) body.innerHTML='<div style="color:#888;padding:20px;text-align:center">处理中...</div>';
    let payload={action:action};
    if(action==="empty") payload.delete=false;
    let r;
    try{ r=await api("/api/admin/library/tools",payload,true); }
    catch(e){ if(body) body.innerHTML='<div class="empty">操作失败：网络错误</div>'; return; }
    if(!r.ok){ if(body) body.innerHTML='<div class="empty">'+escapeHtml(r.msg||"操作失败")+'</div>'; return; }
    if(action==="csv"){
        if(!r.csv) return alert("没有可导出的数据");
        const blob=new Blob([r.csv],{type:"text/csv;charset=utf-8"});
        const a=document.createElement("a");
        a.href=URL.createObjectURL(blob); a.download=r.filename||"music_library.csv"; a.click();
        setTimeout(()=>URL.revokeObjectURL(a.href),2000);
        if(body) body.innerHTML='<div style="color:#07c160;padding:16px">CSV 清单已开始下载</div>';
        return;
    }
    if(action==="stats"){
        const d=r.data||{};
        if(body) body.innerHTML='<div style="line-height:2">🎵 总歌曲：<b>'+d.total+'</b><br>💿 专辑：<b>'+d.albums+'</b><br>👤 歌手：<b>'+d.artists+'</b><br>💾 总容量：<b>'+d.size_mb+' MB</b><br>📝 缺歌词：<b style="color:#e94560">'+d.lyric_missing+'</b><br>🖼 缺封面：<b style="color:#e94560">'+d.cover_missing+'</b><br>💔 损坏音频：<b style="color:#e94560">'+d.corrupt_audio+'</b></div>';
        return;
    }
    if(action==="empty"){
        const dirs=r.data||[];
        if(!dirs.length){ if(body) body.innerHTML='<div style="color:#07c160;padding:16px">✅ 没有空目录</div>'; return; }
        if(confirm("发现 "+dirs.length+" 个空目录，是否删除？\n"+dirs.slice(0,10).join("\n"))){
            payload.delete=true;
            let rr;
            try{ rr=await api("/api/admin/library/tools",payload,true); }catch(e){ return alert("网络错误"); }
            alert(rr.msg||"已清理");
            if(body) body.innerHTML='<div style="color:#07c160;padding:16px">'+escapeHtml(rr.msg||"已清理")+'</div>';
        } else {
            if(body) body.innerHTML='<div style="line-height:1.8">'+dirs.slice(0,80).map(escapeHtml).join("<br>")+'</div>';
        }
        return;
    }
    if(action==="duplicates"){
        const groups=r.data||[];
        if(!groups.length){ if(body) body.innerHTML='<div style="color:#07c160;padding:16px">✅ 没有发现重复文件</div>'; return; }
        if(body) body.innerHTML=groups.map(g=>'<div style="border-bottom:1px solid #333;padding:8px 0"><div style="color:#f39c12">MD5 '+escapeHtml(g.md5)+' · '+(g.files||[]).length+' 个文件</div>'+(g.files||[]).map(x=>'<div style="color:#aaa">'+escapeHtml(x)+'</div>').join('')+'</div>').join('');
        return;
    }
    if(body) body.innerHTML='<div style="color:#07c160;padding:16px">'+escapeHtml(r.msg||"完成")+'</div>';
}

async function repairLibrary(kind){
    if(!loginUser || loginUser.role !== "admin") return alert("只有管理员可以批量修复");
    const label = kind === "lyric" ? "歌词" : "封面";
    if(!confirm("开始批量补"+label+"？只会抓取缺失项，不会重新下载音频，可能需要一点时间。")) return;
    const body = document.getElementById("libraryScanBody");
    if(body) body.innerHTML = '<div style="color:#888;padding:20px;text-align:center">正在批量补'+label+'...</div>';
    let r;
    try { r = await api("/api/admin/library/repair", {kind: kind}, true); }
    catch(e) { if(body) body.innerHTML = '<div class="empty">操作失败：网络错误</div>'; return; }
    alert(r.msg || (r.ok ? "完成" : "操作失败"));
    await scanLibrary();
}

let albumAuditData = null;
function renderAlbumAudit(data){
    albumAuditData = data || {};
    const body = document.getElementById("libraryScanBody");
    if(!body) return;
    const albums = (albumAuditData.albums || []).filter(a=>a.status === "partial");
    let html = '<div style="background:rgba(52,152,219,0.12);border-radius:10px;padding:12px;margin-bottom:12px">'
        + '<div style="font-weight:bold;margin-bottom:6px">📚 专辑完整度</div>'
        + '<div>专辑总数：<b>'+(albumAuditData.album_total||0)+'</b> · '
        + '缺歌专辑：<b style="color:#e94560">'+(albumAuditData.album_partial||0)+'</b> · '
        + '共缺：<b style="color:#e94560">'+(albumAuditData.tracks_missing||0)+'</b> 首</div>'
        + '<div style="color:#888;font-size:12px;margin-top:4px">按酷狗 AlbumID 核对完整曲目；补全只下载缺失歌曲，不重下已有歌曲。</div>'
        + '</div>';
    if(albumAuditData.album_unmatched){
        html += '<div style="color:#888;font-size:12px;margin-bottom:8px">有 '+albumAuditData.album_unmatched+' 张专辑在酷狗未匹配到，已跳过。</div>';
    }
    if(!albums.length){
        html += '<div style="color:#07c160;margin:12px 0">✅ 所有能匹配到的专辑都已完整</div>';
        body.innerHTML = html;
        return;
    }
    html += '<div style="margin:8px 0"><label><input id="albumAuditAll" type="checkbox" onchange="toggleAlbumAuditAll(this.checked)"> 全选缺歌专辑</label></div>';
    html += '<div style="margin:8px 0;display:flex;gap:8px;flex-wrap:wrap">'
         + '<button onclick="repairAlbums(false)" style="background:#07c160">补全选中专辑</button>'
         + '<button onclick="repairAlbums(true)" style="background:#e94560">补全全部缺歌专辑</button>'
         + '</div>';
    albums.forEach((a, i)=>{
        const names = (a.missing_tracks||[]).slice(0,8).map(t=>t.name).join('、');
        html += '<div style="border:1px solid rgba(128,128,128,0.2);border-radius:8px;padding:8px;margin:6px 0">'
             + '<label style="display:flex;align-items:center;gap:8px;cursor:pointer">'
             + '<input class="album-audit-chk" type="checkbox" id="albumAuditChk-'+i+'" data-index="'+i+'">'
             + '<span style="font-weight:bold">'+escapeHtml(a.album)+'</span>'
             + '<span style="color:#888;font-size:12px">'+escapeHtml(a.artist||'')+'</span>'
             + '<span style="color:#e94560;font-size:12px">本地 '+a.local+' / 应有 '+a.expected+'，缺 '+a.missing+'</span>'
             + '</label>'
             + '<div style="color:#888;font-size:12px;margin-left:24px;margin-top:4px">'+escapeHtml(names)+(a.missing>8?' …':'')+'</div>'
             + '</div>';
    });
    body.innerHTML = html;
}
function toggleAlbumAuditAll(checked){
    document.querySelectorAll(".album-audit-chk").forEach(cb=>{ cb.checked = !!checked; });
}
async function scanAlbumAudit(){
    if(!loginUser || loginUser.role !== "admin") return alert("只有管理员可以检查专辑完整度");
    const modal = document.getElementById("libraryScanModal");
    const body = document.getElementById("libraryScanBody");
    if(modal) modal.style.display = "flex";
    if(body) body.innerHTML = '<div style="color:#888;padding:20px;text-align:center">正在向酷狗核对专辑曲目，请稍候...</div>';
    const daysEl=document.getElementById("albumScanDays");
    const skipEl=document.getElementById("albumSkipInvalid");
    const days=daysEl?Number(daysEl.value||0):30;
    const skip=skipEl?!!skipEl.checked:true;
    let r;
    try { r = await api("/api/admin/library/albums/scan", {force:true,recent_days:days,skip_invalid:skip}, true); }
    catch(e) { if(body) body.innerHTML = '<div class="empty">检查失败：网络错误</div>'; return; }
    if(!r.ok){ if(body) body.innerHTML = '<div class="empty">'+escapeHtml(r.msg||"检查失败")+'</div>'; return; }
    renderAlbumAudit(r.data || {});
}
async function repairAlbums(all){
    if(!loginUser || loginUser.role !== "admin") return alert("只有管理员可以补全专辑");
    const albums = (albumAuditData && albumAuditData.albums) || [];
    let names = [];
    if(all){
        names = albums.filter(a=>a.status==="partial").map(a=>a.album);
    } else {
        document.querySelectorAll(".album-audit-chk").forEach(cb=>{
            if(cb.checked){
                const a = albums[Number(cb.getAttribute("data-index"))];
                if(a) names.push(a.album);
            }
        });
    }
    if(!names.length) return alert("请先勾选要补全的专辑");
    const total = albums.filter(a=>names.includes(a.album)).reduce((n,a)=>n+(a.missing||0),0);
    if(!confirm("将补全 "+names.length+" 张专辑，预计缺 "+total+" 首，只下载缺失歌曲，不重下已有歌曲。继续？")) return;
    const body = document.getElementById("libraryScanBody");
    if(body) body.innerHTML = '<div style="color:#888;padding:20px;text-align:center">正在创建补全任务...</div>';
    let r;
    const daysEl=document.getElementById("albumScanDays");
    const days=daysEl?Number(daysEl.value||0):0;
    try { r = await api("/api/admin/library/albums/repair", {albums:names, skip_blacklist:true, recent_days:days}, true); }
    catch(e) { if(body) body.innerHTML = '<div class="empty">操作失败：网络错误</div>'; return; }
    alert(r.msg || (r.ok ? "已加入下载队列" : "操作失败"));
    await scanAlbumAudit();
}

async function repairKugouLyrics(){
    if(!confirm("会检查已下载歌曲的歌词；缺少歌词的会按歌名/歌手从酷狗补抓，可能需要一点时间。继续？")) return;
    let r;
    try{ r=await api("/api/kugou/lyric/repair",{},true); }
    catch(e){ return alert("网络错误"); }
    alert(r.msg || (r.ok ? "检查完成" : "操作失败"));
    if(viewMode==="kugou") renderKugou();
}

// ============ 播放控制 ============
let trialBlocked=false, songLoading=false, loadTimeoutTimer=null;
function finishSongLoading(){
  songLoading=false;
  clearTimeout(loadTimeoutTimer);
  loadTimeoutTimer=null;
  forceHideLoading();
}
function startLoadTimeout(){
  clearTimeout(loadTimeoutTimer);
  loadTimeoutTimer=setTimeout(()=>{
    if(songLoading && player.readyState < 3){
      songLoading=false;
      forceHideLoading();
      document.getElementById("playBtn").innerText="▶";
      showToast("加载超时，请刷新后重试");
      return;
    }
    // 已经可以播放但事件没触发（部分浏览器/移动端），不要再误报超时
    finishSongLoading();
  }, 30000);
}

let pendingResumeTime=0;
function savePlaybackState(){
    try{
        const s=playlist[curIndex]||{};
        if(!s.fullRel) return;
        const state={fullRel:s.fullRel,name:s.name||"",path:currentRelPath||"",index:curIndex,
                     time:isFinite(player.currentTime)?player.currentTime:0,
                     rate:player.playbackRate||1,mode:playMode||"list",updated:Date.now()};
        localStorage.setItem("conan_last_play",JSON.stringify(state));
        let hist=JSON.parse(localStorage.getItem("conan_play_history")||"[]");
        hist=hist.filter(x=>x.fullRel!==s.fullRel);
        hist.unshift({fullRel:s.fullRel,name:s.name||"",path:currentRelPath||"",time:Date.now()});
        localStorage.setItem("conan_play_history",JSON.stringify(hist.slice(0,200)));
    }catch(e){}
}
async function restoreLastPlayback(){
    let st;
    try{ st=JSON.parse(localStorage.getItem("conan_last_play")||"null"); }catch(e){ st=null; }
    if(!st || !st.fullRel) return;
    try{
        if(st.path){ await loadDir(st.path); }
        const idx=playlist.findIndex(s=>s.fullRel===st.fullRel);
        if(idx<0) return;
        pendingResumeTime=Math.max(0,Number(st.time)||0);
        player.playbackRate=Math.max(0.5,Math.min(2,Number(st.rate)||1));
        await playSong(idx);
    }catch(e){}
}
async function playSong(idx){
  if(idx<0 || idx>=playlist.length) return;
  if (playlist[idx] && playlist[idx].online && playlist[idx].hash) {
      curIndex = idx;
      return playOnlineSong(playlist[idx], idx);
  }
  if (!playlist[idx].isLocal) {
      let r = await api("/api/checktoken", null, true);
      if(r.ok) { loginUser = r.user; applyUserToUI(); }
      if(!loginUser) return;
  }
  
  curIndex=idx;
  const s=playlist[idx];
  savePlaybackState();
  trialSeekWarned=false;
  document.title = "▶ " + (s.name||"Conan听歌系统") + " - Conan听歌系统";
  updateFullTitle();
  if(document.getElementById("queueModal").style.display === "flex") renderQueue();
  songLoading=true; showLoading(); startLoadTimeout();
  
  let url = s.isLocal ? s.fullRel : `/file/${encodeURIComponent(s.fullRel)}?token=${token}`;
  player.src = url;
  player.load(); // 确保加载
  player.play().catch(()=>{});
  
  document.getElementById("playBtn").innerText="⏸";
  
  if (!s.isLocal) {
      await loadLyric(s.fullRel);
      document.getElementById("audioInfo").innerText = `${s.ext||''} ${s.bitrate?('| '+s.bitrate+'kbps'):''}`;
  } else {
      lrcLines=[]; lrcIdx=-1;
      document.getElementById("lyricContent").innerHTML = '<div class="lyric-empty">本地导入文件暂无歌词</div>';
      document.getElementById("fullLyricContent").innerHTML = '<div class="lyric-empty" style="color:#666">本地导入文件暂无歌词</div>';
      document.getElementById("audioInfo").innerText = "本地导入 .coman";
  }
}

// 监听各种事件强制关闭遮罩，并清除“加载超时”计时器
player.addEventListener('canplay', finishSongLoading);
player.addEventListener('playing', finishSongLoading);
player.addEventListener('loadeddata', finishSongLoading);

// 非阻塞提示：播放失败/加载超时不再弹 alert 卡住整个页面
function showToast(msg){
  let box = document.getElementById("appToast");
  if(!box){
    box = document.createElement("div");
    box.id = "appToast";
    box.style.cssText = "position:fixed;left:50%;bottom:34px;transform:translateX(-50%);background:rgba(22,22,22,.94);color:#fff;padding:12px 18px;border-radius:12px;font-size:14px;line-height:1.5;z-index:99999;max-width:82vw;box-shadow:0 10px 28px rgba(0,0,0,.28);opacity:0;transition:opacity .2s;pointer-events:none;";
    document.body.appendChild(box);
  }
  box.textContent = msg;
  box.style.opacity = "1";
  clearTimeout(box._timer);
  box._timer = setTimeout(function(){ box.style.opacity = "0"; }, 4200);
}

function closeModal(id){
  const el=document.getElementById(id);
  if(el) el.style.display="none";
}
function ensureModalCloseButtons(){
  document.querySelectorAll(".modal .box").forEach(function(box){
    if(box.querySelector(".modal-x")) return;
    const btn=document.createElement("button");
    btn.type="button"; btn.className="modal-x"; btn.innerHTML="✕"; btn.title="关闭";
    btn.onclick=function(){ const m=box.closest(".modal"); if(m) m.style.display="none"; };
    box.appendChild(btn);
  });
}
ensureModalCloseButtons();

let confirmResolver = null;
function finishConfirm(value){
  const modal = document.getElementById("confirmModal");
  if(modal) modal.style.display = "none";
  if(confirmResolver){ const r=confirmResolver; confirmResolver=null; r(!!value); }
}
function showConfirm(title, text, okText){
  const modal = document.getElementById("confirmModal");
  if(!modal) return Promise.resolve(window.confirm(text || title));
  document.getElementById("confirmTitle").innerText = title || "确认操作";
  document.getElementById("confirmText").innerText = text || "";
  document.getElementById("confirmOk").innerText = okText || "确认";
  document.getElementById("confirmCancel").onclick = function(){ finishConfirm(false); };
  document.getElementById("confirmOk").onclick = function(){ finishConfirm(true); };
  const x = modal.querySelector(".modal-x");
  if(x) x.onclick = function(){ finishConfirm(false); };
  modal.style.display = "flex";
  return new Promise(function(resolve){ confirmResolver = resolve; });
}

player.onerror = () => {
  finishSongLoading();
  document.getElementById("playBtn").innerText="▶";
  const cur=playlist[curIndex]||{};
  if (!cur.isLocal && loginUser && !loginUser.vip && loginUser.role!=="admin") {
      fetch(`/file/${encodeURIComponent(cur.fullRel||"")}?token=${token}`).then(res=>{
          if(res.status===403) showToast("试听次数过多，请开通VIP");
          else showToast("播放失败或需VIP");
      });
  } else {
      const code = player.error ? player.error.code : 0;
      if (code === 2) showToast("网络错误或文件加载失败，请刷新后重试");
      else if (code === 3) showToast("音频解码失败，文件可能损坏");
      else if (code === 4) showToast("浏览器不支持该音频格式");
      else showToast("播放失败，请刷新后重试");
  }
};

// 进度条拖拽 / 点击定位
const progressBarEl=document.getElementById("progressBar");
let seeking=false, seekWasPlaying=false, trialSeekWarned=false;
function maxSeekTime(){
    if(!player.duration || !isFinite(player.duration)) return 0;
    const s=playlist[curIndex];
    if(s && s.isLocal) return player.duration;
    if(loginUser && (loginUser.vip || loginUser.role === "admin")) return player.duration;
    return Math.min(player.duration, TRIAL);
}
function updateProgressUI(){
    if(!player.duration || !isFinite(player.duration)) return;
    const pct=Math.max(0,Math.min(100,player.currentTime/player.duration*100));
    document.getElementById("progressFill").style.width=pct+"%";
    document.getElementById("progressThumb").style.left=pct+"%";
    document.getElementById("currentTime").innerText=formatTime(player.currentTime);
}
function seekToTime(sec, fromUser){
    if(!player.duration || !isFinite(player.duration)) return;
    let target=Math.max(0,Math.min(player.duration, Number(sec)||0));
    const limit=maxSeekTime();
    if(target > limit){
        target=Math.max(0, limit - 0.05);
        if(fromUser && !trialSeekWarned){
            trialSeekWarned=true;
            showToast("试听用户最多拖到 90 秒，开通听歌VIP可完整拖动进度。");
        }
    }
    player.currentTime=target;
    updateProgressUI();
    lrcIdx=-1; updateLyric();
}
function seekRatioFromEvent(e){
    const rect=progressBarEl.getBoundingClientRect();
    return Math.max(0,Math.min(1,(e.clientX-rect.left)/Math.max(1,rect.width)));
}
function showSeekTooltip(ratio, clientX){
    const tip=document.getElementById("progressTooltip");
    const rect=progressBarEl.getBoundingClientRect();
    tip.style.display="block";
    tip.style.left=((clientX-rect.left)/Math.max(1,rect.width)*100)+"%";
    tip.innerText=formatTime((player.duration||0)*ratio);
}
progressBarEl.addEventListener("pointerdown",function(e){
    if(!player.duration || !isFinite(player.duration)) return;
    seeking=true; seekWasPlaying=!player.paused;
    try{ progressBarEl.setPointerCapture(e.pointerId); }catch(_){}
    if(seekWasPlaying) player.pause();
    const ratio=seekRatioFromEvent(e);
    showSeekTooltip(ratio,e.clientX);
    seekToTime(ratio*player.duration,true);
});
progressBarEl.addEventListener("pointermove",function(e){
    if(!player.duration || !isFinite(player.duration)) return;
    const ratio=seekRatioFromEvent(e);
    showSeekTooltip(ratio,e.clientX);
    if(seeking) seekToTime(ratio*player.duration,false);
});
function endSeek(e){
    if(!seeking) return;
    seeking=false;
    try{ progressBarEl.releasePointerCapture(e.pointerId); }catch(_){}
    document.getElementById("progressTooltip").style.display="none";
    if(seekWasPlaying) player.play().catch(()=>{});
}
progressBarEl.addEventListener("pointerup",endSeek);
progressBarEl.addEventListener("pointercancel",endSeek);
progressBarEl.addEventListener("pointerleave",function(){
    if(!seeking) document.getElementById("progressTooltip").style.display="none";
});

// 进度条
player.ontimeupdate=()=>{
  updateProgressUI();
  if(player.buffered.length>0 && player.duration){
    const bufEnd=player.buffered.end(player.buffered.length-1);
    document.getElementById("bufferFill").style.width=(bufEnd/player.duration*100)+"%";
  }
  updateLyric();
  if(Math.floor(player.currentTime)%3===0) savePlaybackState();
  
  // 只要开始播放了，就强制关闭遮罩
  if (player.currentTime > 0 && loadingCount > 0) {
      forceHideLoading();
  }

  if(loginUser && playlist[curIndex] && !playlist[curIndex].isLocal && !loginUser.vip && loginUser.role!=="admin" && !trialBlocked && player.currentTime>=TRIAL){
      trialBlocked=true; player.pause(); showToast("试听 90 秒结束，开通听歌VIP即可畅听全曲");
  }
};
player.onloadedmetadata=()=>{ document.getElementById("totalTime").innerText=formatTime(player.duration); updateProgressUI();
    if(pendingResumeTime>0 && isFinite(player.duration)){
        const t=Math.min(pendingResumeTime,Math.max(0,player.duration-0.5));
        try{ player.currentTime=t; }catch(e){}
        pendingResumeTime=0;
    }
};
player.onseeked=updateProgressUI;

function formatTime(s){if(!isFinite(s)||s<0)return"00:00";const m=Math.floor(s/60),ss=Math.floor(s%60);return(m<10?"0":"")+m+":"+(ss<10?"0":"")+ss;}

// 音量记忆 / 静音
function updateVolumeUI(){
    const slider=document.getElementById("volumeSlider");
    const btn=document.getElementById("muteBtn");
    if(slider) slider.value = player.muted ? 0 : player.volume;
    if(btn) btn.innerText = (player.muted || player.volume === 0) ? "🔇" : (player.volume < 0.5 ? "🔉" : "🔊");
}
function setVolume(v){
    v=Math.max(0,Math.min(1,Number(v)||0));
    player.volume=v; player.muted=false; isMuted=false; savedVolume=v;
    localStorage.setItem("conan_volume", v.toFixed(2));
    localStorage.setItem("conan_muted", "0");
    updateVolumeUI();
}
document.getElementById("volumeSlider").oninput=function(){ setVolume(this.value); };
document.getElementById("muteBtn").onclick=function(){
    isMuted=!isMuted; player.muted=isMuted;
    localStorage.setItem("conan_muted", isMuted ? "1" : "0");
    if(!isMuted && player.volume === 0) setVolume(savedVolume || 0.8);
    else updateVolumeUI();
};
updateVolumeUI();

// 倍速 / 键盘控制 / 播放历史
const SPEEDS=[1,1.25,1.5,0.75,2];
let speedIdx=Math.max(0,SPEEDS.indexOf(parseFloat(localStorage.getItem("conan_speed")||"1")));
if(!isFinite(speedIdx) || speedIdx<0) speedIdx=0;
function applyPlaybackSpeed(){
    const sp=SPEEDS[speedIdx]||1;
    player.playbackRate=sp;
    const btn=document.getElementById("speedBtn");
    if(btn) btn.innerText=sp+"x 倍速";
    localStorage.setItem("conan_speed",sp);
}
document.getElementById("speedBtn").onclick=function(){ speedIdx=(speedIdx+1)%SPEEDS.length; applyPlaybackSpeed(); };
applyPlaybackSpeed();
document.addEventListener("keydown",function(e){
    const tag=(e.target && e.target.tagName || "").toLowerCase();
    if(tag==="input" || tag==="textarea" || tag==="select") return;
    if(e.code==="Space"){ e.preventDefault(); togglePlay(); }
    else if(e.code==="ArrowRight"){ if(player.duration) seekToTime(player.currentTime+5,true); }
    else if(e.code==="ArrowLeft"){ if(player.duration) seekToTime(player.currentTime-5,true); }
    else if(e.code==="ArrowUp"){ e.preventDefault(); setVolume(player.volume+0.1); }
    else if(e.code==="ArrowDown"){ e.preventDefault(); setVolume(player.volume-0.1); }
    else if(e.key==="m" || e.key==="M"){ document.getElementById("muteBtn").click(); }
    else if(e.key==="f" || e.key==="F"){ if(e.shiftKey) toggleWebFullScreen(); else toggleLyricFullScreen(); }
    else if(e.key==="Escape"){ if(isFullScreen) exitFullScreen(); if(document.fullscreenElement) document.exitFullscreen(); }
});
document.getElementById("lyricToolBtn").onclick=function(){
    const t=lyricToolTarget();
    if(!t){ showToast("请先播放一首歌曲（在线的也可以）"); return; }
    const s=t.song||{};
    const title=((s.name||s.fullRel||"当前歌曲")+(s.singer?(" - "+s.singer):""))+(t.mode==="online"?" · 在线":"");
    document.getElementById("lyricToolSong").innerText=title;
    const tip=document.getElementById("lyricToolTip");
    if(tip){
        tip.innerText = t.mode==="online"
            ? "在线歌曲：时间轴微调、导入覆盖只存在这台设备的浏览器里（不落盘）；「内嵌到音频」和「导出原始 KRC」需要本地文件。"
            : "时间轴调整会先备份原始歌词；点“回到初始时间轴”可一键还原。";
    }
    if(typeof loadLyricFonts==="function") loadLyricFonts(false);
    document.getElementById("lyricToolModal").style.display="flex";
};
function currentSongRel(){
    const s=playlist[curIndex] || playlist[0];
    return s && s.fullRel ? s.fullRel : "";
}
async function lyricShift(sec){
    if(lyricToolTarget() && lyricToolTarget().mode==="online"){ onlineLrcShift(sec); return; }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    const r=await api("/api/admin/lyric/shift",{rel:rel,seconds:sec},true);
    showToast(r.msg||(r.ok?"已调整":"操作失败"));
    if(r.ok && typeof loadLyric==="function") loadLyric(rel);
}
async function lyricShiftLine(sec){
    if(lyricToolTarget() && lyricToolTarget().mode==="online"){ onlineLrcShiftLine(sec); return; }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    const line=Number(document.getElementById("lyricLineNo").value||1);
    if(!line || line<1){ showToast("请填写正确句号"); return; }
    let r;
    try{ r=await api("/api/admin/lyric/shift_line",{rel:rel,line:line,seconds:sec},true); }
    catch(e){ showToast("网络错误"); return; }
    showToast(r.msg||(r.ok?"已微调":"操作失败"));
    if(r.ok && typeof loadLyric==="function") loadLyric(rel);
}
async function lyricReset(){
    if(lyricToolTarget() && lyricToolTarget().mode==="online"){ onlineLrcReset(); return; }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    if(!await showConfirm("恢复初始时间轴", "恢复这首歌的初始歌词时间轴？当前偏移会被撤销。", "恢复")) return;
    const r=await api("/api/admin/lyric/reset",{rel:rel},true);
    showToast(r.msg||(r.ok?"已恢复初始时间轴":"操作失败"));
    if(r.ok && typeof loadLyric==="function") loadLyric(rel);
}
async function lyricRefresh(){
    if(lyricToolTarget() && lyricToolTarget().mode==="online"){ await onlineLrcRefresh(); return; }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    if(!await showConfirm("重新搜索酷狗歌词", "从酷狗重新获取逐字歌词并覆盖当前歌词？", "重新搜索")) return;
    showToast("正在重新搜索酷狗逐字歌词...");
    const r=await api("/api/admin/lyric/refresh",{rel:rel},true);
    showToast(r.msg||(r.ok?"已重新获取酷狗逐字歌词":"操作失败"));
    if(r.ok && typeof loadLyric==="function") loadLyric(rel);
}
let lyricUpgradeTimer=null;
function renderLyricUpgradeState(st){
    const box=document.getElementById("lyricUpgradeStatus");
    if(!box || !st) return;
    box.style.display="block";
    const total=Number(st.total||0), done=Number(st.done||0), ok=Number(st.ok||0), failed=Number(st.failed||0);
    const pct=total ? Math.min(100, Math.round(done*100/total)) : 0;
    document.getElementById("lyricUpgradeFill").style.width=pct+"%";
    let text=(st.message||"正在升级")+" · "+done+"/"+total+" 首";
    if(ok) text+=" · 成功 "+ok;
    if(failed) text+=" · 失败 "+failed;
    if(st.current) text+="\n当前："+String(st.current).split("/").pop();
    document.getElementById("lyricUpgradeText").innerText=text;
    const btn=document.getElementById("lyricUpgradeBtn");
    if(btn){ btn.disabled=!!st.running; btn.style.opacity=st.running?"0.55":"1"; }
}
async function pollLyricUpgrade(){
    try{
        const r=await api("/api/admin/lyric/upgrade_status",{},true);
        if(!r || !r.ok) return;
        renderLyricUpgradeState(r.state);
        if(r.state && !r.state.running){
            clearInterval(lyricUpgradeTimer); lyricUpgradeTimer=null;
            showToast("逐字歌词批量升级"+(r.state.message==="已停止"?"已停止":"完成")+"：成功 "+Number(r.state.ok||0)+" 首，失败 "+Number(r.state.failed||0)+" 首");
            const rel=currentSongRel();
            if(rel && typeof loadLyric==="function") loadLyric(rel);
        }
    }catch(e){}
}
async function startLyricUpgrade(){
    if(!await showConfirm("批量升级逐字歌词", "从酷狗分批升级全库普通歌词为 KRC 逐字歌词？每批最多 100 首，可随时停止。", "开始升级")) return;
    let r;
    try{ r=await api("/api/admin/lyric/upgrade_all",{limit:100},true); }
    catch(e){ showToast("启动失败，请检查网络"); return; }
    renderLyricUpgradeState(r.state||{});
    showToast(r.msg||(r.ok?"已开始升级歌词":"操作失败"));
    if(r.ok){
        if(lyricUpgradeTimer) clearInterval(lyricUpgradeTimer);
        lyricUpgradeTimer=setInterval(pollLyricUpgrade,1500);
    }
}
async function stopLyricUpgrade(){
    if(!await showConfirm("停止升级歌词", "停止当前批量歌词升级任务？已经完成的歌曲会保留。", "停止")) return;
    const r=await api("/api/admin/lyric/upgrade_stop",{},true);
    showToast(r.msg||"已请求停止");
}
// ===== 在线歌曲的歌词工具（不落盘：偏移 / 覆盖只存在浏览器本地） =====
function isOnlinePlaying(){
    try{
        const src=String((player&&player.currentSrc)||(player&&player.src)||"");
        return src.indexOf("/api/online/stream")>=0;
    }catch(e){ return false; }
}
function lyricToolTarget(){
    if(isOnlinePlaying() && (onlineLyricSong||currentOnlineSong)) return {mode:"online", song:(onlineLyricSong||currentOnlineSong)};
    const rel=currentSongRel();
    if(rel) return {mode:"local", rel:rel, song:(playlist[curIndex]||playlist[0])};
    if(onlineLyricSong||currentOnlineSong) return {mode:"online", song:(onlineLyricSong||currentOnlineSong)};
    return null;
}
function onlineLrcEditKey(hash){ return "conan_onl_lrc_"+String(hash||"none"); }
function onlineLrcTxtKey(hash){ return "conan_onl_lyrtxt_"+String(hash||"none"); }
function loadOnlineLrcEdit(hash){
    try{ return JSON.parse(localStorage.getItem(onlineLrcEditKey(hash))||"{}")||{}; }catch(e){ return {}; }
}
function saveOnlineLrcEdit(hash,ed){
    try{ localStorage.setItem(onlineLrcEditKey(hash),JSON.stringify(ed)); }catch(e){}
}
function applyOnlineLrcEdit(ed){
    if(!onlineLrcRaw) return [];
    ed=ed||{};
    const g=Number(ed.global||0), per=(ed.lines||{});
    return onlineLrcRaw.map(function(line,i){
        const d=g+Number(per[i]||0);
        const words=(line.words||[]).map(function(w){ return {text:w.text,t:w.t+d,d:w.d}; });
        return {t:line.t+d,c:line.c,words:words};
    });
}
function renderOnlineLrcLines(){
    const box=document.getElementById("lyricContent"), full=document.getElementById("fullLyricContent");
    if(!box) return;
    if(!lrcLines.length){
        box.innerHTML='<div class="lyric-empty">🎵 这个版本暂无官方歌词</div>';
        if(full) full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 这个版本暂无歌词</div>';
        return;
    }
    const html=lyricHtml();
    box.innerHTML=html;
    if(full) full.innerHTML=html;
    lyricDom.main=Array.from(box.querySelectorAll(".lyric-line"));
    lyricDom.full=full?Array.from(full.querySelectorAll(".lyric-line")):[];
    lyricDom.mainWords=lyricDom.main.map(function(line){ return Array.from(line.querySelectorAll(".lword")); });
    lyricDom.fullWords=lyricDom.full.map(function(line){ return Array.from(line.querySelectorAll(".lword")); });
    lrcIdx=-1; lastHitLine=-1; updateLyric();
}
function onlineLrcReapply(){
    if(!onlineLyricSong) return;
    lrcLines=applyOnlineLrcEdit(loadOnlineLrcEdit(onlineLyricSong.hash));
    renderOnlineLrcLines();
}
function onlineLrcShift(sec){
    const s=onlineLyricSong || currentOnlineSong;
    if(!s){ showToast("当前没有在线歌词"); return; }
    if(!onlineLrcRaw){ showToast("这首歌还没有歌词，可以先粘贴导入"); return; }
    const ed=loadOnlineLrcEdit(s.hash);
    ed.global=Math.round((Number(ed.global||0)+Number(sec||0))*100)/100;
    saveOnlineLrcEdit(s.hash,ed);
    onlineLrcReapply();
    showToast("已整体"+(sec<0?"提前":"延后")+" "+Math.abs(sec)+" 秒（已在本机记住）");
}
function onlineLrcShiftLine(sec){
    const s=onlineLyricSong || currentOnlineSong;
    if(!s){ showToast("当前没有在线歌词"); return; }
    const line=Number(document.getElementById("lyricLineNo").value||1);
    if(!line || line<1 || line>lrcLines.length){ showToast("句号超出范围（共 "+lrcLines.length+" 句）"); return; }
    const ed=loadOnlineLrcEdit(s.hash);
    ed.lines=ed.lines||{};
    ed.lines[line-1]=Math.round((Number(ed.lines[line-1]||0)+Number(sec||0))*100)/100;
    saveOnlineLrcEdit(s.hash,ed);
    onlineLrcReapply();
    showToast("第 "+line+" 句已"+(sec<0?"提前":"延后")+" "+Math.abs(sec)+" 秒");
}
function onlineLrcReset(){
    const s=onlineLyricSong || currentOnlineSong;
    if(!s) return;
    try{ localStorage.removeItem(onlineLrcEditKey(s.hash)); }catch(e){}
    onlineLrcReapply();
    showToast("已恢复初始时间轴");
}
async function onlineLrcRefresh(){
    const s=onlineLyricSong || currentOnlineSong;
    if(!s){ showToast("当前没有在线歌词"); return; }
    showToast("正在重新从酷狗抓取逐字歌词...");
    try{ localStorage.removeItem(onlineLrcTxtKey(s.hash)); localStorage.removeItem(onlineLrcEditKey(s.hash)); }catch(e){}
    await loadOnlineLyric(s);
    showToast(lrcLines.length?("已重新抓取，" + lrcLines.length + " 行"):"这个版本仍没有官方歌词");
}
function onlineLrcImport(){
    const s=onlineLyricSong || currentOnlineSong;
    if(!s){ showToast("当前没有在线歌曲"); return; }
    const text=document.getElementById("lyricImportText").value;
    if(!text.trim()){ showToast("请先粘贴歌词"); return; }
    const raw=parseLrc(text);
    if(!raw.length){ showToast("没解析出歌词行"); return; }
    onlineLrcRaw=raw;
    try{ localStorage.removeItem(onlineLrcEditKey(s.hash)); localStorage.setItem(onlineLrcTxtKey(s.hash),text); }catch(e){}
    lrcLines=applyOnlineLrcEdit({});
    renderOnlineLrcLines();
    showToast("已导入 "+raw.length+" 行（本机记住，下次打开仍生效）");
}
function formatLrcTime(t){
    t=Math.max(0,Number(t||0));
    const m=Math.floor(t/60), s=(t-m*60);
    return (m<10?"0":"")+m+":"+(s<10?"0":"")+s.toFixed(2);
}
function onlineLrcExport(){
    if(!lrcLines.length){ showToast("当前没有歌词可导出"); return; }
    const text=lrcLines.map(function(l){ return "["+formatLrcTime(l.t)+"]"+String(l.c||""); }).join("\r\n");
    const blob=new Blob([text],{type:"text/plain;charset=utf-8"});
    const a=document.createElement("a"); a.href=URL.createObjectURL(blob);
    a.download=(String((onlineLyricSong&&onlineLyricSong.name)||"lyric")+".lrc").replace(/[\\/:*?"<>|]/g,"_");
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(a.href); },1500);
    showToast("已导出 SRT/LRC 时间轴歌词");
}
async function lyricExport(raw){
    if(lyricToolTarget() && lyricToolTarget().mode==="online"){
        if(raw){ showToast("在线歌曲没有原始 KRC，下载到本地后再导出"); return; }
        onlineLrcExport(); return;
    }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    let r;
    try{ r=await api("/api/admin/lyric/export",{rel:rel,raw:!!raw},true); }catch(e){ showToast("网络错误"); return; }
    if(!r.ok){ showToast(r.msg||"没有歌词"); return; }
    const blob=new Blob([r.text],{type:"text/plain;charset=utf-8"});
    const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=r.filename||(raw?"lyric.krc":"lyric.lrc"); a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href),1500);
    showToast(raw?"已导出原始 KRC 逐字歌词":"已导出标准 LRC");
}
function lyricPreview(){
    const t=lyricToolTarget();
    if(!t){ showToast("请先播放歌曲"); return; }
    if(!lrcLines.length){ showToast("当前没有可预览的歌词"); return; }
    const s=t.song||{};
    document.getElementById("lyricPreviewSong").innerText=
        ((s.name||s.fullRel||"当前歌曲")+(s.singer?(" - "+s.singer):""))+(t.mode==="online"?"（在线）":"");
    const hasWords=lrcLines.some(function(l){ return l.words && l.words.length; });
    let html='<div style="color:#889;font-size:12px;margin-bottom:8px">'+(hasWords?"KRC 逐字时间轴 · 每个字显示绝对秒数":"普通 LRC 整句时间轴")+'</div>';
    lrcLines.forEach(function(l,i){
        html+='<div style="padding:7px 6px;border-bottom:1px solid rgba(255,255,255,.05)">'
            +'<span style="display:inline-block;min-width:28px;color:#55617a;font-family:monospace">'+(i+1)+'</span>'
            +'<span style="color:var(--accent);font-family:monospace;margin-right:10px">'+formatTime(l.t)+'</span>'
            +'<span style="color:#d8dced">'+escapeHtml(l.c||"")+'</span>';
        if(l.words && l.words.length){
            html+='<div style="margin-top:4px;color:#889;font-size:11px;line-height:1.7">'
                + l.words.map(function(w){ return escapeHtml(w.text)+'<span style="color:#556;font-family:monospace">('+Number(w.t||0).toFixed(2)+')</span>'; }).join(" ")
                +'</div>';
        }
        html+='</div>';
    });
    document.getElementById("lyricPreviewBody").innerHTML=html;
    document.getElementById("lyricPreviewModal").style.display="flex";
}
async function lyricImport(){
    const t=lyricToolTarget();
    if(t && t.mode==="online"){ onlineLrcImport(); return; }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    const text=document.getElementById("lyricImportText").value;
    if(!text.trim()){ showToast("请先粘贴歌词"); return; }
    const r=await api("/api/admin/lyric/import",{rel:rel,text:text},true);
    showToast(r.msg||(r.ok?"导入成功":"导入失败"));
    if(r.ok && typeof loadLyric==="function") loadLyric(rel);
}
async function lyricEmbed(){
    if(lyricToolTarget() && lyricToolTarget().mode==="online"){ showToast("在线歌曲没有本地音频文件，无法内嵌；下载到本地后可用"); return; }
    const rel=currentSongRel(); if(!rel){ showToast("请先播放歌曲"); return; }
    const r=await api("/api/admin/lyric/embed",{rel:rel},true);
    showToast(r.msg||(r.ok?"已内嵌":"操作失败"));
}

function showQueue(){
    renderQueue();
    document.getElementById("queueModal").style.display="flex";
}
function showPlayHistory(){
    let hist=[]; try{hist=JSON.parse(localStorage.getItem("conan_play_history")||"[]");}catch(e){}
    const box=document.getElementById("queueList");
    if(!hist.length){ box.innerHTML='<div class="empty">暂无播放历史</div>'; return; }
    box.innerHTML=hist.map((h,i)=>'<div class="queue-item" onclick="playHistoryItem('+i+')"><span class="idx">'+(i+1)+'</span><span class="qname">'+escapeHtml(h.name||h.fullRel)+'</span></div>').join("");
}
async function playHistoryItem(i){
    let hist=[]; try{hist=JSON.parse(localStorage.getItem("conan_play_history")||"[]");}catch(e){}
    const h=hist[i]; if(!h) return;
    await loadDir(h.path||"");
    const idx=playlist.findIndex(s=>s.fullRel===h.fullRel);
    if(idx>=0) playSong(idx);
}
async function saveQueueAsPlaylist(){
    if(!playlist.length) return alert("播放列表为空");
    const name=prompt("歌单名称", "我的播放列表 "+new Date().toLocaleDateString());
    if(!name) return;
    let r;
    try{ r=await api("/api/playlists/create",{name:name,cover:""},true); }
    catch(e){ return alert("网络错误"); }
    if(!r.ok) return alert(r.msg||"创建失败");
    const songs=playlist.filter(s=>s.fullRel && !s.isLocal).map(s=>({fullRel:s.fullRel,name:s.name||""}));
    if(!songs.length) return alert("当前列表没有可保存的服务器歌曲");
    try{ r=await api("/api/playlists/add_song",{pid:r.pid,songs:songs},true); }
    catch(e){ return alert("网络错误"); }
    alert(r.ok?("已保存 "+songs.length+" 首到歌单「"+name+"」"):(r.msg||"保存失败"));
}
function renderQueue(){
    const box=document.getElementById("queueList");
    if(!box) return;
    if(!playlist.length){ box.innerHTML='<div class="empty">播放列表为空</div>'; return; }
    let html="";
    playlist.forEach(function(s,i){
        html += '<div class="queue-item '+(i===curIndex?'playing':'')+'" draggable="true" ondragstart="queueDragStart(event,'+i+')" ondragover="event.preventDefault()" ondrop="queueDrop(event,'+i+')" onclick="playSong('+i+')">'
              + '<span class="idx">'+(i+1)+'</span>'
              + '<span class="qname">'+escapeHtml(s.name||"")+'</span>'
              + (i===curIndex?'<span style="color:var(--accent)">▶</span>':'')
              + '</div>';
    });
    box.innerHTML=html;
}
let queueDragIndex=-1;
function queueDragStart(e,i){ queueDragIndex=i; try{e.dataTransfer.effectAllowed="move";}catch(_){} }
function queueDrop(e,i){
    e.preventDefault();
    if(queueDragIndex<0 || queueDragIndex===i) return;
    const item=playlist.splice(queueDragIndex,1)[0];
    playlist.splice(i,0,item);
    if(curIndex===queueDragIndex) curIndex=i;
    else if(queueDragIndex<curIndex && i>=curIndex) curIndex--;
    else if(queueDragIndex>curIndex && i<=curIndex) curIndex++;
    queueDragIndex=-1; renderQueue(); savePlaybackState();
}
document.getElementById("queueBtn").onclick=showQueue;

function togglePlay(){ if(!player.src){ if(playlist.length) playSong(0); return; } if(player.paused){ player.play(); document.getElementById("playBtn").innerText="⏸"; } else { player.pause(); document.getElementById("playBtn").innerText="▶"; } }
const MODE_LABELS={list:"🔁 列表循环",single:"🔂 单曲循环",shuffle:"🔀 随机播放"};
function updateModeUI(){
    if(!MODE_LABELS[playMode]) playMode="list";
    document.getElementById("modeBtn").innerText=MODE_LABELS[playMode];
    localStorage.setItem("conan_play_mode",playMode);
}
function cyclePlayMode(){
    playMode = playMode==="list" ? "single" : (playMode==="single" ? "shuffle" : "list");
    updateModeUI();
}
document.getElementById("modeBtn").onclick=cyclePlayMode;
updateModeUI();
function nextIndex(forceNext){
    if(!playlist.length) return -1;
    if(playMode==="shuffle" && playlist.length>1){
        let r=curIndex;
        while(r===curIndex) r=Math.floor(Math.random()*playlist.length);
        return r;
    }
    if(playMode==="single" && !forceNext) return Math.max(0,curIndex);
    return (curIndex+1)%playlist.length;
}
function playNext(forceNext){
    if(!playlist.length) return;
    const i=nextIndex(forceNext);
    if(i>=0) playSong(i);
}
function playPrev(){
    if(!playlist.length)return;
    let i;
    if(playMode==="shuffle" && playlist.length>1){
        i=curIndex; while(i===curIndex) i=Math.floor(Math.random()*playlist.length);
    } else {
        i=curIndex-1; if(i<0)i=playlist.length-1;
    }
    playSong(i);
}
document.getElementById("playBtn").onclick=togglePlay;
document.getElementById("nextBtn").onclick=()=>playNext(true);
document.getElementById("prevBtn").onclick=playPrev;
player.addEventListener("ended",function(){
    if(!playlist.length) return;
    if(playMode==="single" && curIndex>=0){
        player.currentTime=0; player.play().catch(()=>{});
    } else {
        playNext();
    }
});

// 歌词
let lrcLines=[], lrcIdx=-1, lyricDom={main:[],full:[],mainWords:[],fullWords:[]}, lyricReqSeq=0, lastHitLine=-1;
let lrcTransMap={}, lrcPhonMap={}, lrcTransKey="", lrcTransTimer=null, lrcLyricLang="";
function applyLyricSubs(){
    [lyricDom.main, lyricDom.full].forEach(function(list){
        (list||[]).forEach(function(el){
            if(!el || el.querySelector(".lyric-sub")) return;
            const i=Number(el.getAttribute("data-i"));
            const line=lrcLines[i];
            if(!line) return;
            const sub=lyricSubHtmlFor(line.c);
            if(sub) el.insertAdjacentHTML("beforeend", sub);
        });
    });
    try{ updateLyric(); }catch(e){}
}
function pollLyricTrans(key){
    if(!key) return;
    clearTimeout(lrcTransTimer);
    let n=0;
    const tick=async function(){
        if(!key || key!==lrcTransKey || n++>12) return;
        try{
            const res=await fetch("/api/online/lyric/trans?key="+encodeURIComponent(key));
            const d=await res.json();
            if(d && d.trans && Object.keys(d.trans).length){
                lrcTransMap=d.trans;
                applyLyricSubs();
                return;
            }
            if(d && !d.pending) return;
        }catch(e){}
        lrcTransTimer=setTimeout(tick,1500);
    };
    lrcTransTimer=setTimeout(tick,1500);
}
const LRC_META_RE=/^(词|曲|编曲|作词|作曲|制作|制作人|监制|混音|母带|录音|出品|发行|统筹|企划|吉他|贝斯|鼓|键盘|和声|弦乐|OP|SP|Lyrics|Composed|Arranged|Producer|Mixed|Mastered)[：:]/i;
function parseLrc(text){
    let lines=[]; let offset=0;
    text=String(text||"").replace(/^\uFEFF/, "");
    const om=text.match(/\[offset:([+-]?\d+)\]/i);
    if(om) offset=parseInt(om[1],10)/1000;
    text.split(/\r?\n/).forEach(function(line){
        const times=[]; let m; const re=/\[(\d+):(\d+(?:\.\d+)?)\]/g;
        while((m=re.exec(line))!==null) times.push(parseInt(m[1],10)*60+parseFloat(m[2]));
        const content=line.replace(/\[[^\]]*\]/g,"").replace(/<[^>]*>/g,"").trim();
        if(content && LRC_META_RE.test(content)) return;
        if(times.length && content){
            times.forEach(function(t){ lines.push({t:t+offset,c:content,words:null}); });
            return;
        }
        // 酷狗 KRC：一行形如 [开始毫秒,时长]<逐字偏移,时长,0>字...
        const km=line.trim().match(/^\[(\d+),(\d+)(?:,\d+)?\](.*)$/);
        if(km){
            const startMs=parseInt(km[1],10);
            const body=km[3]||"";
            const words=[];
            const tags=Array.from(body.matchAll(/<(\d+),(\d+),(\d+)>/g));
            tags.forEach(function(tag,i){
                const textStart=tag.index+tag[0].length;
                const textEnd=(i+1<tags.length)?tags[i+1].index:body.length;
                const text=body.slice(textStart,textEnd);
                if(!text) return;
                words.push({
                    text:text,
                    t:(startMs+parseInt(tag[1],10))/1000+offset,
                    d:Math.max(0.03,parseInt(tag[2],10)/1000)
                });
            });
            const krcContent=body.replace(/<[^>]*>/g,"").trim();
            if(krcContent && !LRC_META_RE.test(krcContent)){
                lines.push({t:startMs/1000+offset,c:krcContent,words:words.length?words:null});
            }
        }
    });
    lines.sort(function(a,b){return a.t-b.t;});
    // 普通 LRC 没有逐字时间：按整行时长均分到每个字，也能做出逐字点亮节奏
    lines.forEach(function(line,i){
        if(line.words && line.words.length) return;
        const chars=Array.from(String(line.c||""));
        const nextT=(i+1<lines.length)?lines[i+1].t:line.t+4;
        let dur=nextT-line.t;
        if(!isFinite(dur) || dur<=0) dur=4;
        // 普通 LRC 没有逐字时间，按字数估一个自然的演唱节奏（不跟着超长间奏拖慢）
        dur=Math.max(1.1,Math.min(dur,chars.length*0.34,8));
        const step=dur/Math.max(1,chars.length);
        line.words=chars.map(function(ch,j){ return {text:ch,t:line.t+j*step,d:step}; });
    });
    return lines;
}
async function loadLyric(fr){
    const seq=++lyricReqSeq;
    const box=document.getElementById("lyricContent");
    const full=document.getElementById("fullLyricContent");
    box.innerHTML=""; full.innerHTML=""; lrcLines=[]; lrcIdx=-1;
    box.style.transform="translateY(0)";
    try{
        const res=await fetch("/lyric/"+encodeURIComponent(fr)); const data=await res.json();
        if(seq!==lyricReqSeq) return;
        if(!data.hasLrc){
            box.innerHTML='<div class="lyric-empty">🎵 暂无歌词</div>';
            full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 暂无歌词</div>';
            return;
        }
        lrcLines=parseLrc(data.content);
        if(!lrcLines.length){
            box.innerHTML='<div class="lyric-empty">🎵 暂无歌词</div>';
            full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 暂无歌词</div>';
            return;
        }
        const html=lyricHtml();
        box.innerHTML=html; full.innerHTML=html;
        lrcIdx=-1; updateLyric();
    }catch(e){
        if(seq!==lyricReqSeq) return;
        box.innerHTML='<div class="lyric-empty">🎵 暂无歌词</div>';
        full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 暂无歌词</div>';
    }
}
function lyricSubHtmlFor(text){
    const phon=lrcPhonMap[text]||"";
    const trans=lrcTransMap[text]||"";
    if(!phon && !trans) return "";
    let s="";
    if(phon && phon!==text) s+='<span class="lyric-sub lyric-phon">'+escapeHtml(phon)+'</span>';
    if(trans && trans!==text) s+='<span class="lyric-sub lyric-trans">'+escapeHtml(trans)+'</span>';
    return s;
}
function lyricHtml(){
    return lrcLines.map(function(l,i){
        const words=(l.words && l.words.length) ? l.words.map(function(w){
            const wd=Math.max(.18,Math.min(1.6,Number(w.d)||.45));
            const starScale=Math.min(1.25,Math.max(.8,.78+wd*.55));
            return '<span class="lword" data-t="'+w.t+'" data-d="'+w.d+'" style="--star-dur:'+wd.toFixed(2)+'s;--star-scale:'+starScale.toFixed(2)+'">'+escapeHtml(w.text)+'</span>';
        }).join("") : escapeHtml(l.c);
        return '<div class="lyric-line" data-i="'+i+'" data-t="'+l.t+'">'+words+lyricSubHtmlFor(l.c)+'</div>';
    }).join("");
}
async function loadLyric(fr){
    const seq=++lyricReqSeq;
    const box=document.getElementById("lyricContent");
    const full=document.getElementById("fullLyricContent");
    box.innerHTML=""; full.innerHTML=""; lrcLines=[]; lrcIdx=-1;
    lrcTransMap={}; lrcPhonMap={}; lrcTransKey=""; lrcLyricLang=""; clearTimeout(lrcTransTimer);
    lyricDom={main:[],full:[],mainWords:[],fullWords:[]};
    lastHitLine=-1;
    box.style.transform="translateY(0)";
    try{
        const res=await fetch("/lyric/"+encodeURIComponent(fr)); const data=await res.json();
        if(seq!==lyricReqSeq) return;
        if(!data.hasLrc){
            box.innerHTML='<div class="lyric-empty">🎵 暂无歌词</div>';
            full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 暂无歌词</div>';
            return;
        }
        lrcLines=parseLrc(data.content);
        if(!lrcLines.length){
            box.innerHTML='<div class="lyric-empty">🎵 暂无歌词</div>';
            full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 暂无歌词</div>';
            return;
        }
        const html=lyricHtml();
        box.innerHTML=html; full.innerHTML=html;
        lyricDom.main=Array.from(box.querySelectorAll(".lyric-line"));
        lyricDom.full=Array.from(full.querySelectorAll(".lyric-line"));
        lyricDom.mainWords=lyricDom.main.map(function(line){ return Array.from(line.querySelectorAll(".lword")); });
        lyricDom.fullWords=lyricDom.full.map(function(line){ return Array.from(line.querySelectorAll(".lword")); });
        lrcIdx=-1; updateLyric();
    }catch(e){
        if(seq!==lyricReqSeq) return;
        box.innerHTML='<div class="lyric-empty">🎵 暂无歌词</div>';
        full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 暂无歌词</div>';
    }
}
function highlightLineWords(containerId, idx, t){
    if(idx<0) return;
    const words=(containerId==="lyricContent") ? lyricDom.mainWords[idx] : lyricDom.fullWords[idx];
    if(!words || !words.length) return;
    words.forEach(function(el){
        const st=parseFloat(el.dataset.t||"0"), d=parseFloat(el.dataset.d||"0.2");
        const isHit=(t>=st && t<st+Math.max(0.05,d));
        const isSung=(t>=st+Math.max(0.05,d));
        if(el.classList.contains("hit")!==isHit) el.classList.toggle("hit",isHit);
        if(el.classList.contains("sung")!==isSung) el.classList.toggle("sung",isSung);
    });
}
function updateLyric(){
    if(!lrcLines.length) return;
    let t=player.currentTime, idx=-1;
    for(let i=0;i<lrcLines.length;i++){ if(t>=lrcLines[i].t) idx=i; else break; }
    if(idx!==lrcIdx){
        lrcIdx=idx;
        // 换行时清掉上一行的星星，否则每句最后一个字都会残留一颗星
        if(lastHitLine>=0){
            [lyricDom.mainWords[lastHitLine], lyricDom.fullWords[lastHitLine]].forEach(function(words){
                if(words) words.forEach(function(el){ el.classList.remove("hit"); });
            });
        }
        lastHitLine=idx;
        const mainLines=lyricDom.main, fullLines=lyricDom.full;
        mainLines.forEach(function(el,i){
            el.classList.toggle("active",i===idx);
            el.classList.toggle("past",i<idx);
        });
        fullLines.forEach(function(el,i){
            el.classList.toggle("active",i===idx);
            el.classList.toggle("past",i<idx);
        });
        // 跳歌/快进后：已唱的行整行点亮、未唱的行恢复灰色，前面不再一大片灰
        for(let li=0;li<lrcLines.length;li++){
            [lyricDom.mainWords[li], lyricDom.fullWords[li]].forEach(function(words){
                if(!words || !words.length) return;
                if(li<idx){
                    words.forEach(function(el){ el.classList.add("sung"); el.classList.remove("hit"); });
                }else if(li>idx){
                    words.forEach(function(el){ el.classList.remove("sung"); el.classList.remove("hit"); });
                }
            });
        }
        if(idx>=0 && mainLines[idx]){
            const h=document.getElementById("lyricBox").clientHeight;
            // 歌词内容上下留白，保证第一句/最后一句也能停在中间，不再突然弹跳
            const target=Math.max(0, mainLines[idx].offsetTop - h/2 + mainLines[idx].offsetHeight/2);
            document.getElementById("lyricContent").style.transform="translateY(-"+target+"px)";
        }else{
            document.getElementById("lyricContent").style.transform="translateY(0)";
        }
        if(isFullScreen && idx>=0 && fullLines[idx]){
            const wrap=document.getElementById("fullScreenWrap");
            const rect=fullLines[idx].getBoundingClientRect();
            const wrapRect=wrap.getBoundingClientRect();
            const delta=rect.top - wrapRect.top - wrap.clientHeight/2 + rect.height/2;
            wrap.scrollTo({top:Math.max(0,wrap.scrollTop+delta),behavior:"smooth"});
        }
    }
    if(idx>=0){
        highlightLineWords("lyricContent",idx,t);
        if(isFullScreen) highlightLineWords("fullLyricContent",idx,t);
    }
}
function lyricTick(){
    if(lrcLines.length && player && !player.paused && !document.hidden) updateLyric();
    requestAnimationFrame(lyricTick);
}
requestAnimationFrame(lyricTick);
function bindLyricClick(containerId){
    const box=document.getElementById(containerId);
    if(!box) return;
    box.addEventListener("click",function(e){
        const el=e.target.closest(".lyric-line");
        if(el && el.dataset.t) seekToTime(parseFloat(el.dataset.t),true);
    });
}
bindLyricClick("lyricContent");
bindLyricClick("fullLyricContent");

// 点击歌曲封面放大预览；阻止同时触发行点击播放
document.addEventListener("click",function(e){
    const img=e.target && e.target.closest ? e.target.closest("img.cover-img") : null;
    if(!img || !img.getAttribute("src")) return;
    e.preventDefault(); e.stopPropagation();
    document.getElementById("coverPreviewImg").src=img.src;
    const item=img.closest(".item");
    const nameEl=item ? item.querySelector(".txt") : null;
    document.getElementById("coverPreviewName").innerText=nameEl ? nameEl.innerText.slice(0,80) : "";
    document.getElementById("coverPreviewModal").style.display="flex";
},true);

// ============ 杂项 ============
document.getElementById("vipBtn").onclick=()=>document.getElementById("vipModal").style.display="flex";
document.getElementById("refreshBtn").onclick=async()=>{
    const btn=document.getElementById("refreshBtn");
    const old=btn.innerText;
    btn.disabled=true; btn.innerText="⏳ 刷新中";
    showToast("正在刷新歌曲库...");
    try{ await fetch("/api/refresh"); }catch(e){}
    try{
        if(viewMode==="browse") await loadDir(currentRelPath||"");
        else if(viewMode==="kugou") await renderKugou();
        else if(viewMode==="search") await renderSearch();
        else if(viewMode==="recommend") await renderRecommend(onlineRankKey||"hot");
        else if(viewMode==="online"){
            const kw=(document.getElementById("searchBox").value||"").trim();
            if(kw) await searchOnlineSongs(kw); else await renderRecommend(onlineRankKey||"hot");
        }
    }catch(e){}
    await loadMessages();
    await loadBroadcast();
    btn.disabled=false; btn.innerText=old||"🔄 刷新";
    showToast("已刷新歌曲库、通知和广播");
};
document.getElementById("msgBtn").onclick=async()=>{
    const r=await api("/api/message/mylist"); const list=r.data||[];
    let html="";
    list.forEach(m=>{ html+=`<div class="record-item"><b>${m.from}</b> (${m.time})<br>${escapeHtml(m.content)}</div>`; });
    document.getElementById("msgList").innerHTML=html||"无留言";
    document.getElementById("msgModal").style.display="flex";
    const unread=list.filter(function(m){return !m.read;});
    if(unread.length){
        try{ await api("/api/message/read",{ids:unread.map(function(m){return m.id;})},true); }catch(e){}
        await loadMessages();
    }
};
document.getElementById("onlineBtn").onclick=async()=>{
    const r=await api("/api/admin/online"); 
    if(!r.ok) return alert("无权限");
    let html=""; r.data.forEach(u=>{ html+=`<div class="record-item"><b>${u.username}</b> 正在听: ${u.song}</div>`; });
    document.getElementById("onlineList").innerHTML=html||"当前无人听歌";
    document.getElementById("onlineModal").style.display="flex";
};

async function applyVipPay(){
    let amt=parseInt(document.getElementById("vipPayAmount").value)||0; let note=document.getElementById("vipPayNote").value;
    if(amt<1 || !note) return alert("请填写完整");
    let r=await api("/api/vip/apply_pay", {amount:amt, note:note});
    document.getElementById("vipMsg").innerText = r.msg;
}
async function buyVipPoints(){
    let pts=parseInt(document.getElementById("vipPointsInput").value)||0;
    let type=document.getElementById("buyVipType").value;
    let minPts = type==="vip" ? 100 : 200;
    if(pts<minPts) return alert("积分不足最低要求");
    let r=await api("/api/vip/buy_points", {points:pts, type:type});
    document.getElementById("vipMsg").innerText = r.msg;
    if(r.ok) { let rr=await api("/api/checktoken"); loginUser=rr.user; applyUserToUI(); }
}

function startHeartbeat() {
    setInterval(() => {
        let song = "";
        if (!player.paused && curIndex >= 0 && playlist[curIndex] && !playlist[curIndex].isLocal) song = playlist[curIndex].name;
        api("/api/heartbeat", { song: song }, true).catch(()=>{});
    }, 15000);
}

// 事件绑定修复
let searchInputTimer=null;
let searchKind="song";
const SEARCH_KIND_LABEL={song:"综合",single:"单曲",mv:"视频/MV",lyric:"歌词",special:"歌单",album:"专辑",singer:"歌手"};
function paintSearchTabs(){
    document.querySelectorAll("#searchTabs button").forEach(function(b){
        const on=b.getAttribute("data-k")===searchKind;
        b.style.cssText="padding:6px 14px;border:none;border-radius:999px;cursor:pointer;font-size:12px;transition:all .2s;"
            +(on?"background:var(--accent,#3ec8a8);color:#fff;font-weight:600;box-shadow:0 4px 14px rgba(62,200,168,.35);"
                :"background:rgba(255,255,255,.07);color:var(--text-mute,#9aa3bb);");
    });
}
function setSearchKind(kind, btn){
    const real=(kind==="mv2")?"mv":kind;
    searchKind=kind;
    paintSearchTabs();
    const kw=(document.getElementById("searchBox").value||"").trim();
    if(real==="mv"){
        document.getElementById("btnMv").click();
        const inp=document.getElementById("mvSearchInput");
        if(inp && kw){ inp.value=kw; searchMv(); }
        return;
    }
    if(!kw) return;
    if(real==="song"||real==="single"||real==="lyric") searchOnlineSongs(kw);
    else searchByKind(real, kw);
}
// ===== 在线列表「加载更多」：搜索 / 专辑 / 歌手 / 歌单 / MV 都不再只给 20 条 =====
let onlineListCtx=null;
function kindRowsHtml(items){
    return (items||[]).map(function(x,i){
        const act=x.kind==="mv"?"▶ 播放 MV":(x.kind==="singer"?"🎤 听他/她的歌":(x.kind==="album"?"💿 查看曲目":"📋 打开"));
        const icon=x.kind==="singer"?"👤":(x.kind==="album"?"💿":(x.kind==="special"?"📋":"🎬"));
        let coverHtml;
        if(x.cover){
            coverHtml='<img class="cover-img" loading="lazy" referrerpolicy="no-referrer" src="'+escapeHtml(x.cover)+'" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
                +'<span style="display:none;font-size:20px;width:44px;height:44px;align-items:center;justify-content:center;flex-shrink:0">'+icon+'</span>';
        }else{
            coverHtml='<span style="font-size:20px;width:44px;height:44px;display:flex;align-items:center;justify-content:center;flex-shrink:0">'+icon+'</span>';
        }
        return '<div class="item" onclick="openKindItem('+i+')" style="cursor:pointer">'
            + coverHtml
            + '<span class="txt" style="display:flex;flex-direction:column;min-width:0">'
            +   '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(x.title||"")+'</span>'
            +   '<span style="font-size:11px;color:#889;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml((x.sub||"")+((x.extra)?" · "+x.extra:""))+'</span>'
            + '</span>'
            + '<span class="dl" title="打开">'+act+'</span>'
            + '</div>';
    }).join("");
}
function onlineMoreHtml(){
    const c=onlineListCtx;
    if(!c) return "";
    const n=(c.type==="kind")?((window._kindItems||[]).length):(onlineSongs||[]).length;
    const tot=(c.total||0);
    if(c.loading) return '<div style="text-align:center;padding:18px;color:#889;font-size:12px">正在加载更多…</div>';
    if(!c.hasMore) return '<div style="text-align:center;padding:18px;color:#66718a;font-size:12px">已经到底了 · 共 '+n+' 条</div>';
    return '<div style="text-align:center;padding:18px">'
        + '<button id="onlineMoreBtn" onclick="loadMoreOnline()" style="padding:10px 24px;border:none;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer;font-size:13px">⬇ 加载更多</button>'
        + '<div style="color:#66718a;font-size:11px;margin-top:6px">已 '+n+' 条'+(tot?(' · 共 '+tot+' 条'):'')+' · 不会自动加载，想听更多就点上面的按钮</div></div>';
}
function onlineDedupeSongs(list){
    const seen={}; const out=[];
    (list||[]).forEach(function(s){
        const k=String(s.hash||"")+"|"+String(s.name||"")+"|"+String(s.singer||"");
        if(seen[k]) return; seen[k]=1; out.push(s);
    });
    return out;
}
function onlineDedupeKind(list){
    const seen={}; const out=[];
    (list||[]).forEach(function(x){
        const k=String(x.kind||"")+"|"+String(x.id||x.title||"")+"|"+String(x.sub||"");
        if(seen[k]) return; seen[k]=1; out.push(x);
    });
    return out;
}
async function loadMoreOnline(){
    const c=onlineListCtx;
    if(!c || c.loading || !c.hasMore) return;
    c.loading=true;
    if(c.type==="songs") renderSongsAll(c.kw);
    else if(c.type==="kind") renderKindAll(c.kind, c.kw);
    else if(c.type==="rank") renderRankAll();
    const np=c.page+1;
    let items=[], total=0;
    try{
        if(c.type==="songs"){
            const r=await apiGet("/api/online/search?kw="+encodeURIComponent(c.kw)+"&limit=50&page="+np);
            items=(r&&r.ok&&r.data)||[]; total=(r&&r.total)||0;
            if(items.length) onlineSongs=onlineDedupeSongs((onlineSongs||[]).concat(items));
        }else if(c.type==="rank"){
            const r=await apiGet("/api/online/rank?key="+encodeURIComponent(c.key)+"&limit=50&page="+np);
            items=(r&&r.ok&&r.data)||[]; total=(r&&r.total)||0;
            if(items.length) onlineSongs=onlineDedupeSongs((onlineSongs||[]).concat(items));
        }else{
            const r=await apiGet("/api/online/search/kind?kind="+encodeURIComponent(c.kind)+"&kw="+encodeURIComponent(c.kw)+"&limit=50&page="+np);
            items=(r&&r.data)||[]; total=(r&&r.total)||0;
            if(items.length) window._kindItems=onlineDedupeKind((window._kindItems||[]).concat(items));
        }
    }catch(e){ items=[]; }
    c.loading=false;
    if(!items.length){ c.hasMore=false; }
    else{
        c.page=np;
        if(total) c.hasMore = ((c.type==="kind")?(window._kindItems||[]).length:(onlineSongs||[]).length) < total;
        else c.hasMore = items.length >= 25;
    }
    if(c.type==="songs") renderSongsAll(c.kw);
    else if(c.type==="kind") renderKindAll(c.kind, c.kw);
    else if(c.type==="rank") renderRankAll();
}
function renderSongsAll(kw){
    const box=document.getElementById("listBox");
    onlineSongs=onlineSongs||[];
    document.getElementById("pathText").innerText="🔍 在线搜索「"+kw+"」："+onlineSongs.length+" 首";
    if(!onlineSongs.length){
        box.innerHTML='<div class="empty">没有找到「'+escapeHtml(kw)+'」，换个关键词试试</div>';
        return;
    }
    box.innerHTML=renderOnlineRows(onlineSongs)+onlineMoreHtml();
}
function renderKindAll(kind,kw){
    const box=document.getElementById("listBox");
    const items=window._kindItems||[];
    const label=SEARCH_KIND_LABEL[kind]||"搜索";
    document.getElementById("pathText").innerText="🔍 "+label+"「"+kw+"」："+items.length+" 个";
    if(!items.length){
        box.innerHTML='<div class="empty">没搜到相关的'+label+'，换个关键词试试</div>';
        return;
    }
    box.innerHTML=kindRowsHtml(items)+onlineMoreHtml();
}
/* 取消「滑到底自动加载」：列表无限增长会把歌词区推到屏幕外，改为手动点「加载更多」 */

async function searchByKind(kind, kw){
    kw=(kw||"").trim();
    if(!kw) return;
    viewMode="online";
    document.querySelectorAll(".topbar button").forEach(function(b){b.classList.remove("active");});
    document.getElementById("backBtn").style.display="none";
    const delBtn=document.getElementById("deletePlBtn"); if(delBtn) delBtn.style.display="none";
    const box=document.getElementById("listBox");
    const label=SEARCH_KIND_LABEL[kind]||"搜索";
    document.getElementById("pathText").innerText="🔍 "+label+"「"+kw+"」";
    box.innerHTML='<div class="empty">正在酷狗搜索…</div>';
    let r=null;
    try{ r=await apiGet("/api/online/search/kind?kind="+encodeURIComponent(kind)+"&kw="+encodeURIComponent(kw)+"&limit=50&page=1"); }catch(e){ r=null; }
    const items=(r&&r.data)||[];
    window._kindItems=items;
    onlineListCtx={type:"kind", kind:kind, kw:kw, page:1, total:((r&&r.total)||0), hasMore:((r&&r.total)||0)? items.length<(r.total) : items.length>=25, loading:false};
    if(!items.length){ box.innerHTML='<div class="empty">没搜到相关的'+label+'，换个关键词试试</div>'; return; }
    renderKindAll(kind, kw);
}
async function openKindItem(i){
    const x=(window._kindItems||[])[i];
    if(!x) return;
    if(x.kind==="singer"){
        document.getElementById("searchBox").value=x.title;
        searchKind="single"; paintSearchTabs();
        searchOnlineSongs(x.title);
        return;
    }
    if(x.kind==="special"){
        document.getElementById("searchBox").value=x.title;
        searchKind="single"; paintSearchTabs();
        searchOnlineSongs(x.title);
        return;
    }
    if(x.kind==="album"){
        const box=document.getElementById("listBox");
        document.getElementById("pathText").innerText="💿 "+x.title+" · "+(x.sub||"");
        box.innerHTML='<div class="empty">正在读取专辑曲目…</div>';
        let r=null;
        try{ r=await apiGet("/api/online/album?id="+encodeURIComponent(x.id)+"&name="+encodeURIComponent(x.title)+"&singer="+encodeURIComponent(x.sub||"")); }catch(e){ r=null; }
        const songs=(r&&r.data)||[];
        if(!songs.length){ box.innerHTML='<div class="empty">这张专辑没读到完整曲目，试试搜歌手名</div>'; return; }
        onlineSongs=songs;
        onlineListCtx=null;
        document.getElementById("pathText").innerText="💿 专辑《"+x.title+"》"+(x.sub?" · "+x.sub:"")+" ："+songs.length+" 首（点歌在线听）";
        box.innerHTML=renderOnlineRows(songs);
        return;
    }
    if(x.kind==="mv"){
        document.getElementById("btnMv").click();
        const inp=document.getElementById("mvSearchInput");
        if(inp){ inp.value=x.title+" "+(x.sub||""); searchMv(); }
        return;
    }
}
document.getElementById("searchBox").oninput = function() {
    const val = this.value.trim();
    clearTimeout(searchInputTimer);
    searchInputTimer=setTimeout(function(){
        if (val) {
            if(searchKind==="song"||searchKind==="single"||searchKind==="lyric") searchOnlineSongs(val);
            else if(searchKind==="mv"||searchKind==="mv2"){ const inp=document.getElementById("mvSearchInput"); if(inp){ inp.value=val; searchMv(); } }
            else searchByKind(searchKind, val);
        } else {
            renderRecommend(onlineRankKey || "hot");
        }
    },320);
};
document.getElementById("searchBox").onkeydown = function(e){
    if(e.key==="Enter"){
        clearTimeout(searchInputTimer);
        const v=this.value.trim();
        if(v){
            if(searchKind==="song"||searchKind==="single"||searchKind==="lyric") searchOnlineSongs(v);
            else if(searchKind==="mv"||searchKind==="mv2"){ const inp=document.getElementById("mvSearchInput"); if(inp){ inp.value=v; searchMv(); } }
            else searchByKind(searchKind, v);
        }
    }
};
try{ paintSearchTabs(); }catch(e){}
async function searchOnlineSongs(kw){
    kw=(kw||"").trim();
    if(!kw){ renderRecommend(onlineRankKey || "hot"); return; }
    viewMode="online";
    document.querySelectorAll(".topbar button").forEach(function(b){b.classList.remove("active");});
    document.getElementById("backBtn").style.display="none";
    const delBtn=document.getElementById("deletePlBtn"); if(delBtn) delBtn.style.display="none";
    document.getElementById("pathText").innerText="🔍 在线搜索「"+kw+"」";
    const box=document.getElementById("listBox");
    box.innerHTML='<div class="empty">正在酷狗搜索…</div>';
    let r=null;
    try{ r=await apiGet("/api/online/search?kw="+encodeURIComponent(kw)+"&limit=50&page=1"); }catch(e){ r=null; }
    onlineSongs=(r&&r.ok&&r.data)?r.data:[];
    onlineListCtx={type:"songs", kw:kw, page:1, total:((r&&r.total)||0), hasMore:((r&&r.total)||0)? onlineSongs.length<(r.total) : onlineSongs.length>=25, loading:false};
    if(!onlineSongs.length){
        document.getElementById("pathText").innerText="🔍 在线搜索「"+kw+"」：0 首";
        box.innerHTML='<div class="empty">没有找到「'+escapeHtml(kw)+'」，换个关键词试试</div>';
        return;
    }
    renderSongsAll(kw);
}

async function renderSearch() {
    viewMode="search";
    updateM3uBtn();
    document.querySelectorAll(".topbar button").forEach(b=>b.classList.remove("active"));
    const kw = document.getElementById("searchBox").value.trim().toLowerCase();
    if(!kw){ loadDir(currentRelPath); return; }
    document.getElementById("backBtn").style.display="none";
    
    const r = await apiGet("/api/all?kw=" + encodeURIComponent(kw));
    const matched = r.data || [];
    const online = r.online || [];
    
    document.getElementById("pathText").innerText="🔍 搜索「"+kw+"」："+matched.length+" 首";
    playlist = matched;
    let html='<div id="kugouDlStatus">'+kugouStatusHtml+'</div>';
    
    if(!matched.length && !online.length){
      html='<div class="empty">没有找到匹配的歌曲</div>';
      document.getElementById("listBox").innerHTML=html;
      return;
    }
    
    matched.forEach((s,idx)=>{
      const fc = (loginUser && loginUser.favs && loginUser.favs.some(x=>x.fullRel===s.fullRel)) ? "liked" : "";
      const pc=(idx===curIndex&&viewMode==="search")?"playing":"";
      const cpTag = s.copyright ? `<span class="cp-tag">© ${escapeHtml(s.copyright)}</span>` : '';
      const infoTag = s.ext ? `<span style="font-size:11px;color:#888;margin-left:6px">${s.ext}${s.bitrate ? " " + s.bitrate + "kbps" : ""}</span>` : '';
      const parts = String(s.fullRel || "").split("/");
      const relTag = parts.length > 1 ? (parts.length > 2 ? parts[0] + " · " + parts[1] : parts[0]) : "";
      
      html+='<div class="item '+pc+'" onclick="playSong('+idx+')">'
          + '<img class="cover-img" src="/api/cover?path='+encodeURIComponent(s.fullRel)+'" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'">'
          + '<span style="display:none;font-size:20px;width:44px;text-align:center;">🎵</span>'
          + '<div class="txt" style="display:flex;flex-direction:column;">'
          +   `<span>${escapeHtml(s.name)}${cpTag}</span>`
          +   `<span style="font-size:11px;color:#888">${escapeHtml(relTag)}${infoTag}</span>`
          + '</div>'
          + `<span class="addpl" onclick="event.stopPropagation();showAddToPlaylist('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${s.name.replace(/'/g,"\\'")}')">➕</span>`
          + `<span class="dl" onclick="event.stopPropagation();downloadSong('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${escapeHtml(s.name)}')">⬇️</span>`
          + `<span class="heart ${fc}" onclick="event.stopPropagation();toggleFav('${s.fullRel.replace(/\\/g,"\\\\").replace(/'/g,"\\'")}','${s.name.replace(/'/g,"\\'")}')">♥</span></div>`;
    });
    
    if (online.length > 0) {
      searchOnline = online;
      onlineSongs = online;
      html += '<div style="margin-top:24px;color:#888;font-size:13px;text-align:center">以下为酷狗云端搜索结果 · 点歌即在线播放，不下载到本地</div>';
      if (r.engine === false) {
        html += '<div class="engine-warn">⚠️ 服务器未安装 <b>musicdl</b>，部分音源解析可能失败。请在服务器上运行 <b>pip install musicdl</b> 后重启。</div>';
      }
      let isAdmin = loginUser && loginUser.role === "admin";
      let canDl = canDownloadOnline();
      online.forEach((s, idx) => {
        let localTag = s.local_rel ? '<span class="cp-tag" style="background:#07c160">✅ 已入库</span>' : '';
        let blackTag = s.blacklisted ? '<span class="cp-tag" style="background:#f39c12">🚫 '+escapeHtml(s.blacklisted)+'</span>' : '';
        let tagHtml = localTag || ((s.is_auth ? '<span class="cp-tag" style="background:#8e44ad">已授权</span>' : '<span style="color:#e94560;font-size:12px;">暂无版权</span>') + blackTag);
        let coverHtml = s.cover
            ? '<img class="cover-img" loading="lazy" referrerpolicy="no-referrer" src="'+escapeHtml(s.cover)+'" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'"><span style="display:none;font-size:20px;color:var(--text-mute);width:44px;text-align:center;flex-shrink:0">☁️</span>'
            : '<span style="font-size:20px;color:var(--text-mute);width:44px;text-align:center;flex-shrink:0">☁️</span>';
        let actionBtn = "";
        if (s.local_rel) {
            actionBtn = '<button class="dl-btn" style="background:#3498db" onclick="event.stopPropagation(); playKugouLocal(searchOnline['+idx+'].local_rel, searchOnline['+idx+'].name)">▶ 播放</button>';
        } else {
            actionBtn = '<button class="dl-btn" style="background:#3498db" onclick="event.stopPropagation(); playOnlineAt('+idx+')">▶ 在线播放</button>';
            if (canDl) {
                actionBtn += '<button class="dl-btn" onclick="event.stopPropagation(); downloadOnlineAt('+idx+')">⬇️ 下载</button>';
            }
        }
        let authBtn = "";
        if (isAdmin) {
            let btnText = s.is_auth ? "取消授权" : "授权";
            let btnBg = s.is_auth ? "#e94560" : "#8e44ad";
            authBtn = '<button class="dl-btn" style="background:'+btnBg+'" onclick="event.stopPropagation(); toggleKugouAuthBySearch('+idx+')">'+btnText+'</button>';
        }

        html += '<div class="item" style="background:rgba(128,128,128,0.1)">'
             + coverHtml
             + '<span class="txt">'+escapeHtml(s.name)+' <span style="color:#889;font-size:12px">- '+escapeHtml(s.singer)+'</span></span>'
             + tagHtml
             + authBtn
             + actionBtn
             + '</div>';
      });
    }
    document.getElementById("listBox").innerHTML=html;
}

// ============ 在线听歌（推荐首页 / 在线搜索 / 不落盘播放） ============
let onlineSongs=[], onlineRankKey="hot", onlineLoading=false;
const ONLINE_RANK_LABELS={hot:"经典热歌",top:"TOP500",new:"新歌榜",guochao:"国潮音乐榜",shanggan:"伤感榜",minyao:"民谣榜",dj:"DJ热歌榜"};
const ONLINE_RANK_TABS=[["hot","🌟 经典热歌"],["top","🔥 TOP500"],["new","🆕 新歌榜"],["guochao","🏮 国潮榜"],["shanggan","💧 伤感榜"],["minyao","🌾 民谣榜"],["dj","🎧 DJ热歌"]];

function canDownloadOnline(){
    if(!loginUser) return false;
    return !!(loginUser.role==="admin" || loginUser.dl_vip);
}
function onlineCoverHtml(s){
    const tip=s.singer?escapeHtml(s.singer):"未知歌手";
    if(s.cover){
        return '<img class="cover-img" loading="lazy" referrerpolicy="no-referrer" src="'+escapeHtml(s.cover)+'" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
             + '<span style="display:none;font-size:20px;width:44px;height:44px;align-items:center;justify-content:center;flex-shrink:0">🎵</span>';
    }
    return '<span style="font-size:20px;width:44px;height:44px;display:flex;align-items:center;justify-content:center;flex-shrink:0">🎵</span>';
}
function renderOnlineRows(list){
    const dl=canDownloadOnline();
    return list.map(function(s,i){
        const dur=s.duration?formatTime(s.duration):"";
        const q=(s.extra&&s.extra.quality)?'<span class="cp-tag" style="background:#2b6cf6">'+escapeHtml(s.extra.quality)+'</span>':"";
        return '<div class="item" onclick="playOnlineAt('+i+')">'
             + onlineCoverHtml(s)
             + '<span class="txt" style="display:flex;flex-direction:column;min-width:0">'
             +   '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(s.name)+'</span>'
             +   '<span style="font-size:11px;color:#889;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(s.singer||"")+(dur?" · "+dur:"")+(s.album?" · "+escapeHtml(s.album):"")+'</span>'
             + '</span>'
             + q
             + '<span class="dl" onclick="event.stopPropagation();playOnlineAt('+i+')" title="在线播放">▶</span>'
             + (dl?'<span class="dl" onclick="event.stopPropagation();downloadOnlineAt('+i+')" title="下载到本地">⬇️</span>':'')
             + '</div>';
    }).join("");
}
async function renderRecommend(rankKey){
    onlineRankKey=rankKey||onlineRankKey||"hot";
    viewMode="recommend";
    onlineListCtx=null;
    const box=document.getElementById("listBox");
    if(!box) return;
    document.querySelectorAll(".topbar button").forEach(function(b){b.classList.remove("active");});
    const rb=document.getElementById("btnRecommend"); if(rb) rb.classList.add("active");
    document.getElementById("backBtn").style.display="none";
    const delBtn=document.getElementById("deletePlBtn"); if(delBtn) delBtn.style.display="none";
    document.getElementById("pathText").innerText="🏠 推荐好歌 · 酷狗"+(ONLINE_RANK_LABELS[onlineRankKey]||"TOP500");
    box.innerHTML=rankTabsHtml()+'<div class="empty">正在加载推荐…</div>';
    if(onlineLoading) return;
    onlineLoading=true;
    let r=null;
    try{
        const url=(onlineRankKey==="hot")?"/api/online/hot?limit=50"
            :("/api/online/rank?key="+encodeURIComponent(onlineRankKey)+"&limit=50&page=1");
        r=await apiGet(url);
    }catch(e){ r=null; }
    onlineLoading=false;
    onlineSongs=(r&&r.ok&&r.data)?r.data:[];
    if(!onlineSongs.length){
        box.innerHTML=rankTabsHtml()+'<div class="empty">推荐加载失败或网络波动，点右上角刷新重试</div>';
        return;
    }
    onlineListCtx = (onlineRankKey==="hot") ? null
        : {type:"rank", key:onlineRankKey, page:1, total:((r&&r.total)||0), hasMore:((r&&r.total)||0)? onlineSongs.length<(r.total) : onlineSongs.length>=25, loading:false};
    renderRankAll();
}
function rankTabsHtml(){
    return '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">'+ONLINE_RANK_TABS.map(function(t){
        const on=t[0]===onlineRankKey;
        return '<button onclick="renderRecommend(\''+t[0]+'\')" style="padding:7px 12px;border:none;border-radius:8px;cursor:pointer;font-size:12px;'
             +(on?'background:var(--accent);color:#fff':'background:var(--panel);color:var(--text-mute)')+'">'+t[1]+'</button>';
    }).join("")+'</div>';
}
function renderRankAll(){
    const box=document.getElementById("listBox");
    if(!box) return;
    onlineSongs=onlineSongs||[];
    viewMode="recommend";
    document.getElementById("pathText").innerText="🏠 推荐好歌 · 酷狗"+(ONLINE_RANK_LABELS[onlineRankKey]||"TOP500")+"："+onlineSongs.length+" 首";
    box.innerHTML=rankTabsHtml()+renderOnlineRows(onlineSongs)+onlineMoreHtml();
}
function onlineEntry(s){
    return {name:s.name,singer:s.singer,isLocal:false,online:true,hash:s.hash,
            cover:s.cover,duration:s.duration,album:s.album};
}
async function playOnlineAt(i){
    const s=onlineSongs[i];
    if(!s) return;
    playlist=onlineSongs.map(onlineEntry);
    curIndex=i;
    await playOnlineSong(s,i);
}
async function playOnlineSong(s,idx){
    if(!s) return;
    trialBlocked=false; trialSeekWarned=false;
    const title=s.name+(s.singer?" - "+s.singer:"");
    currentOnlineSong=s;
    try{ player.pause(); }catch(e){}
    updateFullTitle();
    document.getElementById("audioInfo").innerText="正在解析在线音源…";
    const slowTip=setTimeout(function(){ document.getElementById("audioInfo").innerText="多源搜索中…（版权受限的歌会慢一些）"; },8000);
    showToast("正在加载《"+s.name+"》…");
    let r=null;
    try{
        r=await apiGet("/api/online/play?hash="+encodeURIComponent(s.hash||"")
            +"&name="+encodeURIComponent(s.name||"")
            +"&singer="+encodeURIComponent(s.singer||"")
            +"&duration="+encodeURIComponent(s.duration||0));
    }catch(e){ r=null; }
    clearTimeout(slowTip);
    if(!r||!r.ok){
        document.getElementById("audioInfo").innerText="";
        showToast((r&&r.msg)||"音源解析失败，换一首试试");
        return;
    }
    player.src="/api/online/stream?u="+encodeURIComponent(r.url);
    showToast("▶ 正在播放《"+s.name+"》");
    if(sfxId && sfxAllowed()){ try{ applySoundFx(sfxId,true); }catch(e){} }
    try{ player.load(); player.play(); }catch(e){}
    document.getElementById("playBtn").innerText="⏸";
    document.getElementById("audioInfo").innerText="在线 · "+(r.quality&&r.quality!=="auto"?(r.quality.toUpperCase()):"自动音质");
    document.title="▶ "+title+" - Conan听歌系统";
    updateFullTitle();
    loadOnlineLyric(s);
    try{ document.getElementById("controlWrap").scrollIntoView({behavior:"smooth",block:"end"}); }catch(e){}
}
async function downloadOnlineAt(i){
    const s=onlineSongs[i];
    if(!s) return;
    if(!canDownloadOnline()){
        showToast("仅站长或会员可下载到本地，普通用户直接在线听就好");
        return;
    }
    const songQ={song:s.name,name:s.name,singer:s.singer,album:s.album||"",hash:s.hash,cover:s.cover||"",
                 album_id:s.album_id||"",auth_key:(s.name+"-"+s.singer)};
    try{
        const r=await api("/api/kugou/download/start",{songs:[songQ],skip_existing:true,quality:"auto"},true);
        showToast(r.msg||(r.ok?"已加入下载队列":"下载任务提交失败"));
    }catch(e){ showToast("网络错误，下载任务提交失败"); }
}
async function loadOnlineLyric(s){
    const seq=++lyricReqSeq;
    const box=document.getElementById("lyricContent");
    const full=document.getElementById("fullLyricContent");
    if(!box) return;
    box.innerHTML='<div class="lyric-empty">🎵 歌词加载中…</div>';
    if(full) full.innerHTML='<div class="lyric-empty">🎵 歌词加载中…</div>';
    lrcLines=[]; lrcIdx=-1; lyricDom={main:[],full:[],mainWords:[],fullWords:[]}; lastHitLine=-1;
    lrcTransMap={}; lrcPhonMap={}; lrcTransKey=""; lrcLyricLang=""; clearTimeout(lrcTransTimer);
    box.style.transform="translateY(0)";
    let r=null;
    try{
        r=await apiGet("/api/online/lyric?hash="+encodeURIComponent(s.hash||"")
            +"&name="+encodeURIComponent(s.name||"")
            +"&singer="+encodeURIComponent(s.singer||"")
            +"&duration="+encodeURIComponent(s.duration||0)
            +"&_v="+Date.now());
    }catch(e){ r=null; }
    if(seq!==lyricReqSeq) return;
    lrcPhonMap=(r&&r.phonetic)||{};
    lrcTransMap=(r&&r.trans)||{};
    lrcLyricLang=(r&&r.lang)||"";
    lrcTransKey=(r&&r.trans_key)||"";
    const text=(r&&r.ok&&r.content)?r.content:"";
    onlineLyricSong=s;
    let overlay="";
    try{ overlay=localStorage.getItem(onlineLrcTxtKey(s.hash))||""; }catch(e){}
    const rawText=overlay||text;
    onlineLrcRaw=rawText?parseLrc(rawText):null;
    lrcLines=applyOnlineLrcEdit(loadOnlineLrcEdit(s.hash));
    if(!lrcLines.length){
        box.innerHTML='<div class="lyric-empty">🎵 这个版本暂无官方歌词（同名不同歌手的版本不会借歌词，避免串词）</div>';
        if(full) full.innerHTML='<div class="lyric-empty" style="color:#666">🎵 这个版本暂无歌词</div>';
        return;
    }
    const html=lyricHtml();
    box.innerHTML=html;
    if(full) full.innerHTML=html;
    lyricDom.main=Array.from(box.querySelectorAll(".lyric-line"));
    lyricDom.full=full?Array.from(full.querySelectorAll(".lyric-line")):[];
    lyricDom.mainWords=lyricDom.main.map(function(line){ return Array.from(line.querySelectorAll(".lword")); });
    lyricDom.fullWords=lyricDom.full.map(function(line){ return Array.from(line.querySelectorAll(".lword")); });
    lrcIdx=-1; updateLyric();
    if(r && r.trans_pending && lrcTransKey) pollLyricTrans(lrcTransKey);
}
// 启动默认进推荐页
document.getElementById("btnRecommend").onclick=function(){ renderRecommend(onlineRankKey||"hot"); };

// ============ 歌词字体/颜色自定义（字体库目录：T:\插件（python代码所运用的插件）\conan 支持系统\字体库） ============
async function loadLyricFonts(showTip){
  const sel=document.getElementById("lyricFontSelect");
  if(!sel) return;
  let fonts=[];
  try{
    const r=await apiGet("/api/fonts");
    if(r && r.fonts) fonts=r.fonts;
  }catch(e){}
  const saved=localStorage.getItem("conan_lyric_font")||"";
  let html='<option value="">默认字体（最美的风景）</option>';
  if(!fonts.length){
    html+='<option value="" disabled>字体库暂无字体，请放入字体库目录后刷新</option>';
  }
  fonts.forEach(function(f){ html+='<option value="'+escapeHtml(f)+'">'+escapeHtml(f)+'</option>'; });
  sel.innerHTML=html;
  sel.value=saved;
  if(showTip) showToast(fonts.length?("字体库共 "+fonts.length+" 个字体"):"字体库暂无字体文件");
}
async function applyLyricFont(name,persist,silent){
  const root=document.documentElement;
  if(!name){
    root.style.setProperty("--lyric-font","'ZuiMeiDeFengJing','PingFang SC','Microsoft YaHei',sans-serif");
    if(persist) localStorage.removeItem("conan_lyric_font");
    if(!silent) showToast("已恢复默认字体");
    return;
  }
  try{
    const family="ConanLyricFont";
    const ff=new FontFace(family,"url('/api/font_file?name="+encodeURIComponent(name)+"')");
    const loaded=await ff.load();
    document.fonts.add(loaded);
    root.style.setProperty("--lyric-font","'"+family+"','ZuiMeiDeFengJing',sans-serif");
    if(persist) localStorage.setItem("conan_lyric_font",name);
    if(!silent) showToast("歌词字体已切换："+name);
  }catch(e){
    if(!silent) showToast("字体加载失败："+name);
  }
}
function applyLyricColors(persist){
  const map=[["past","lyricColorPast","--lyric-sung-color"],["cur","lyricColorCur","--lyric-hit-color"],["future","lyricColorFuture","--lyric-future-color"]];
  const obj={};
  map.forEach(function(c){
    const el=document.getElementById(c[1]);
    if(el){ document.documentElement.style.setProperty(c[2],el.value); obj[c[0]]=el.value; }
  });
  if(persist) localStorage.setItem("conan_lyric_colors",JSON.stringify(obj));
}
function resetLyricColors(){
  ["--lyric-sung-color","--lyric-hit-color","--lyric-future-color"].forEach(function(v){
    document.documentElement.style.removeProperty(v);
  });
  localStorage.removeItem("conan_lyric_colors");
  initLyricStyleUI();
  showToast("歌词颜色已恢复默认");
}
function initLyricStyleUI(){
  let saved={};
  try{ saved=JSON.parse(localStorage.getItem("conan_lyric_colors")||"{}"); }catch(e){}
  const cfg=[["past","lyricColorPast","#dfe5f5"],["cur","lyricColorCur","#ffd166"],["future","lyricColorFuture","#9aa1b5"]];
  cfg.forEach(function(c){
    const el=document.getElementById(c[1]);
    if(el) el.value=saved[c[0]]||c[2];
  });
  if(saved.past||saved.cur||saved.future) applyLyricColors(false);
}
(function(){
  const fs=document.getElementById("lyricFontSelect");
  if(fs) fs.onchange=function(){ applyLyricFont(this.value,true); };
  ["lyricColorPast","lyricColorCur","lyricColorFuture"].forEach(function(id){
    const el=document.getElementById(id);
    if(el) el.oninput=function(){ applyLyricColors(true); };
  });
})();
// 页面加载时恢复上次设置
loadLyricFonts(false).then(function(){
  const saved=localStorage.getItem("conan_lyric_font");
  if(saved) applyLyricFont(saved,false,true);
});
initLyricStyleUI();

// 启动
checkLogin();

// ============ 手机蓝牙连接/断开提醒（Web Bluetooth，手机端授权后自动检测） ============
let btLastTs=0, btWatchState={}, btWatching=false;
function btSupported(){ return !!(navigator.bluetooth); }
async function btProbe(d){
  try{
    const gatt=await d.gatt.connect();
    if(gatt && gatt.connected) return true;
    try{ if(gatt) gatt.disconnect(); }catch(e){}
    return false;
  }catch(e){ return false; }
}
async function btScanAndReport(){
  const token=localStorage.getItem("token")||"";
  if(!token || !btSupported()) return;
  let devices=[];
  try{ devices=await navigator.bluetooth.getDevices(); }catch(e){ return; }
  for(const d of devices){
    const connected=await btProbe(d);
    const prev=btWatchState[d.id];
    if(prev!==undefined && prev!==connected){
      const name=(d.name||"").trim()||"蓝牙设备";
      showToast(connected?("手机蓝牙已连接："+name):("手机蓝牙已断开："+name));
      try{
        await fetch("/api/bluetooth/report",{method:"POST",headers:{"Content-Type":"application/json","X-Conan-Token":token},body:JSON.stringify({token:token,action:connected?"connect":"disconnect",name:name})});
      }catch(e){}
    }
    btWatchState[d.id]=connected;
  }
}
async function pollBluetooth(){
  try{
    const res=await fetch("/api/bluetooth/event");
    const r=await res.json();
    if(r && r.ok && r.ts && r.ts>btLastTs){
      btLastTs=r.ts;
      if(r.event) showToast(r.event);
    }
  }catch(e){}
  if(btWatching) btScanAndReport();
}
setInterval(pollBluetooth, 20000);
pollBluetooth();
function btAuthorize(){
  if(!btSupported()){
    showToast("此浏览器不支持蓝牙检测：请用 https://192.168.1.4:5001 打开（手机先装证书）");
    return;
  }
  const token=localStorage.getItem("token")||"";
  if(!token){ showToast("请先登录再检测蓝牙"); return; }
  navigator.bluetooth.requestDevice({acceptAllDevices:true}).then(function(){
    btWatching=true;
    showToast("已授权，正在检测手机蓝牙耳机…");
    btScanAndReport();
  }).catch(function(){
    showToast("未授权蓝牙（已取消）");
  });
}
</script></body></html>'''

def _share_page_html(rel, token, share_data=None):
    name = os.path.splitext(os.path.basename(str(rel).replace("\\", "/")))[0]
    folder = os.path.dirname(str(rel).replace("\\", "/"))
    artist = folder.split("/")[-2] if folder else ""
    safe_name = _html.escape(name or "分享歌曲")
    safe_artist = _html.escape(artist or "Conan听歌系统")
    audio_url = "/file/" + quote(rel, safe="") + "?st=" + quote(token, safe="")
    cover_url = "/api/cover?path=" + quote(rel, safe="")
    share_data = share_data or {}
    ttl_hours = int(share_data.get("ttl", 0) or 0)
    ttl_text = "永久有效" if ttl_hours <= 0 else f"{ttl_hours} 小时"
    custom_cover = str(share_data.get("cover", "") or "").strip()
    if custom_cover and (custom_cover.startswith("/") or custom_cover.startswith("http://") or custom_cover.startswith("https://")):
        cover_url = custom_cover
    safe_cover = _html.escape(cover_url, quote=True)
    note = str(share_data.get("note", "") or "").strip()
    if note:
        note_html = '<div class="note">' + _html.escape(note) + '</div>'
    else:
        note_html = ''
    safe_token = _html.escape(token, quote=True)
    return r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>__NAME__ · 最美的风景</title>
<style>
@font-face{font-family:'ZuiMeiDeFengJing';src:url('/fonts/最美的风景.ttf') format('truetype');font-display:swap;}
*{box-sizing:border-box;margin:0;padding:0;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
background:radial-gradient(circle at 20% 10%,rgba(233,69,96,.24),transparent 34%),radial-gradient(circle at 80% 90%,rgba(52,152,219,.2),transparent 38%),#12131f;color:#fff;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","Microsoft YaHei",system-ui,sans-serif}
.card{width:min(440px,100%);background:linear-gradient(180deg,rgba(255,255,255,.1),rgba(255,255,255,.03));border:1px solid rgba(255,255,255,.14);border-radius:26px;padding:24px;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.16)}
.cover{width:168px;height:168px;border-radius:20px;object-fit:cover;background:#25263a;box-shadow:0 12px 30px rgba(0,0,0,.35)}
h1{font-size:24px;margin:18px 0 6px;letter-spacing:.04em}
.artist{color:#aab0c4;font-size:14px;margin-bottom:18px}
audio{width:100%;height:48px;border-radius:14px;outline:none}
.note{margin-top:14px;padding:10px 14px;border-radius:12px;background:rgba(255,255,255,.07);color:#ffd9e2;font-size:14px;line-height:1.7;white-space:pre-wrap;word-break:break-word}
.qr-wrap{margin:16px auto 0;display:inline-block;background:#fff;padding:10px;border-radius:14px}
#shareQr{width:220px;height:220px;display:block;border-radius:6px}
.qr-sizes{margin-top:8px;display:flex;gap:8px;justify-content:center}
.qr-sizes button{padding:5px 12px;border:1px solid rgba(233,69,96,.5);background:transparent;color:#ffd9e2;border-radius:999px;cursor:pointer;font-size:12px}
.qr-sizes button.active{background:#e94560;color:#fff}
.tip{margin-top:18px;color:#8f95aa;font-size:12px;line-height:1.8}
.brand{margin-top:16px;font-family:'ZuiMeiDeFengJing',"STKaiti","KaiTi","华文楷体",serif;font-size:26px;color:#f3d7df;letter-spacing:.18em}
</style></head><body><div class="card">
<img class="cover" src="__COVER__" onerror="this.style.display='none'">
<h1>__NAME__</h1><div class="artist">__ARTIST__</div>
<audio controls playsinline preload="metadata" src="__AUDIO__"></audio>
__NOTE__
<div class="qr-wrap"><img id="shareQr" src="/share_qr/__TOKEN__?size=220" alt="二维码"></div>
<div class="qr-sizes">
<button onclick="setQrSize(160,this)">小</button>
<button class="active" onclick="setQrSize(220,this)">中</button>
<button onclick="setQrSize(340,this)">大</button>
</div>
<div class="tip">扫描上方二维码听歌 · 手机和电脑需在同一 Wi-Fi<br>分享有效期 __TTL__，链接过期后请重新生成</div>
<div class="brand">最美的风景</div>
</div>
<script>function setQrSize(n,btn){document.getElementById("shareQr").src="/share_qr/__TOKEN__?size="+n;document.querySelectorAll(".qr-sizes button").forEach(function(b){b.classList.remove("active");});btn.classList.add("active");}</script>
</body></html>'''.replace("__NAME__", safe_name).replace("__ARTIST__", safe_artist).replace("__COVER__", safe_cover).replace("__AUDIO__", audio_url).replace("__TTL__", ttl_text).replace("__NOTE__", note_html).replace("__TOKEN__", safe_token)


# ====================== 启动 ======================
class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

KUGOU_RESTORED_COUNT = 0
_BOOT_LOCK = threading.Lock()
_BOOT_DONE = False


def bootstrap(restore_tasks=True):
    """恢复酷狗下载任务并启动调度线程。
    独立运行（python 本文件.py）和 Flask/WSGI 部署都会调用它；重复调用安全，只真正执行一次。"""
    global KUGOU_RESTORED_COUNT, _BOOT_DONE
    with _BOOT_LOCK:
        if _BOOT_DONE:
            return KUGOU_RESTORED_COUNT
        try:
            if os.path.isdir(KUGOU_TEMP_DIR):
                shutil.rmtree(KUGOU_TEMP_DIR, ignore_errors=True)
        except Exception:
            pass
        try:
            KUGOU_RESTORED_COUNT = restore_kugou_tasks() if restore_tasks else 0
        except Exception as _e:
            print(f"警告: 恢复下载任务失败（{_e}）")
            KUGOU_RESTORED_COUNT = 0
        try:
            ensure_kugou_scheduler()
        except Exception as _e:
            print(f"警告: 启动下载调度失败（{_e}）")
        _BOOT_DONE = True
    return KUGOU_RESTORED_COUNT


def _graceful_shutdown(signum=None, frame=None):
    try:
        _persist_kugou_tasks()
    except Exception:
        pass
    print("\n收到退出信号，下载任务已保存，下次启动自动续传。", flush=True)
    try:
        sys.exit(0)
    except Exception:
        os._exit(0)


for _sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
    try:
        import signal as _signal
        _sig = getattr(_signal, _sig_name, None)
        if _sig is not None: _signal.signal(_sig, _graceful_shutdown)
    except Exception:
        pass


def _print_banner():
    print("=" * 60)
    print("  🎵 Conan听歌系统（终极完美版 + 酷狗云端下载）")
    print(f"  📁 音乐根目录: {MUSIC_ROOT}")
    print(f"  🌐 端口: {PORT}")
    print(f"  💻 电脑访问: http://127.0.0.1:{PORT}")
    print(f"  📱 手机同WiFi: http://{local_ip}:{PORT}")
    print(f"  🔐 管理员: {ADMIN_USERNAME}（密码见 config.json，token 有效期 {TOKEN_TTL_SECONDS // 86400} 天）")
    print(f"  💰 积分: 200积分=1元 | 100积分=1天会员")
    print(f"  🎧 免费用户试听 {TRIAL_SECONDS} 秒")
    print(f"  ⬇️ 酷狗下载: {'可用（musicdl）' if MUSICDL_AVAILABLE else '不可用（请在服务器运行 pip install musicdl）'}")
    print(f"  🧵 下载并发: 最多 {KUGOU_MAX_CONCURRENT} 个任务 | 已恢复任务: {KUGOU_RESTORED_COUNT} 个")
    print("=" * 60)


def run_standalone():
    """原来的 socketserver 独立运行方式（python 本文件.py / pythonw 隐藏启动）。
    Flask 版本请用 flask_app.py；两种方式共用同一套业务代码。"""
    bootstrap()
    _print_banner()
    with ThreadedTCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
        # HTTPS：手机端 Web Bluetooth 要求安全上下文，证书存在时额外监听 5001
        HTTPS_PORT = 5001
        HTTPS_CERT_FILE = os.path.join(CERT_DIR, "server_cert.pem")
        HTTPS_KEY_FILE = os.path.join(CERT_DIR, "server_key.pem")
        try:
            if os.path.isfile(HTTPS_CERT_FILE) and os.path.isfile(HTTPS_KEY_FILE):
                import ssl as _ssl
                _httpsd = ThreadedTCPServer(("0.0.0.0", HTTPS_PORT), MyHandler)
                _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
                _ctx.load_cert_chain(HTTPS_CERT_FILE, HTTPS_KEY_FILE)
                _httpsd.socket = _ctx.wrap_socket(_httpsd.socket, server_side=True)
                threading.Thread(target=_httpsd.serve_forever, daemon=True, name="https-5001").start()
                print(f"  🔒 HTTPS 已启用: https://{local_ip}:{HTTPS_PORT}")
                print(f"  🎧 手机蓝牙检测：手机先访问 https://{local_ip}:{HTTPS_PORT}/ca.crt 安装证书")
            else:
                print("  ⚠️ 未找到 HTTPS 证书（server_cert.pem/server_key.pem），手机蓝牙检测不可用")
        except Exception as _https_err:
            print(f"  ⚠️ HTTPS 启动失败: {_https_err}")
        print("🚀 服务已启动，按 Ctrl+C 停止")
        httpd.serve_forever()


if __name__ == "__main__":
    run_standalone()
