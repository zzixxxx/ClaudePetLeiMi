"""Claude Code 桌宠 — 读取 hook 写入的状态文件, 播放对应 GIF.

状态映射:
  working  -> 02  晕转奋笔
  thinking -> 01  蚊香眼思考
  done     -> 03  举本子炫耀 (10 秒后转 idle)
  waiting  -> 04  停笔等你
  error    -> 05  诶?
  idle     -> 06  抱本子待机 (超 3 分钟转 standby=03)

左键拖动, 右键菜单退出. 位置记忆在 pet_config.json.
"""
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import urllib.request
from ctypes import wintypes

from PIL import (Image, ImageDraw, ImageFilter, ImageFont, ImageGrab,
                 ImageSequence, ImageTk)

try:
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

BASE = os.path.dirname(os.path.abspath(__file__))
GIF_DIR = os.path.join(BASE, "gifs")
CFG_FILE = os.path.join(BASE, "pet_config.json")
PID_FILE = os.path.join(BASE, "pet.pid")
STATE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "cc-pet-state.json")
SESS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "cc-pet-sessions.json")
SESS_REG_DIR = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
USAGE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "cc-pet-usage.json")
CRED_FILE = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")

# 会话状态 -> (Claude Code 风格动词, 颜色点)
SESSION_STATES = {
    "working": ("Doodling…", "#2760cf"),
    "thinking": ("Pondering…", "#7c5cd6"),
    "waiting": ("Waiting…", "#d29922"),
    "done": ("Done", "#3fb950"),
    "error": ("Error", "#e5484d"),
    "idle": ("Idle", "#9a9aa5"),
}

USAGE_API = "https://api.anthropic.com/api/oauth/usage"
USAGE_API_POLL_S = 180      # 接口限流较狠, 官方 UA + 180s 是安全间隔
USAGE_API_FRESH_S = 600     # API 数据 10 分钟内算新鲜, 否则退回 statusline 文件
CLAUDE_UA = "claude-code/2.1.220"

# 自动更新: 对比远端 version.txt, 有新版则下载 zip 覆盖后自动重启
VERSION_FILE = os.path.join(BASE, "version.txt")
UPDATE_VER_URL = ("https://raw.githubusercontent.com/zzixxxx/"
                  "ClaudePetLeiMi/main/version.txt")
UPDATE_ZIP_URL = ("https://github.com/zzixxxx/ClaudePetLeiMi/"
                  "archive/refs/heads/main.zip")
UPDATE_CHECK_S = 24 * 3600


def local_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0"

ASSET_DIR = os.path.join(BASE, "assets")  # Impact.ttf 备而不用 (已回退)
ICON_PNG = os.path.join(ASSET_DIR, "pet.png")  # 托盘/快捷方式图标
UI_FONT = "Microsoft YaHei UI"  # 面板字体, 与右键菜单 (msyh.ttc) 一致

PET_SIZE = 200                 # 显示尺寸(像素)
TRANS_COLOR = "#ff00fe"        # 透明键色
POLL_MS = 500                  # 状态文件轮询间隔
DONE_HOLD_S = 10               # done 展示时长, 之后转 idle
IDLE_TO_STANDBY_S = 180        # idle 超 3 分钟 -> standby(03)
STALE_S = 15 * 60              # 状态文件太久没更新视为会话已死 -> idle

STATE_GIF = {
    "working": "02",
    "thinking": "01",
    "done": "03",
    "waiting": "04",
    "error": "05",
    "idle": "06",
    "standby": "03",
}


APP_AUMID = "ClaudePetLeiMi"
APP_DISPLAY_NAME = "蕾米埃尔"


def register_app_identity():
    """让系统通知显示为"蕾米埃尔"而不是 python: 设置进程 AUMID 并注册显示名/图标."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_AUMID)
        import winreg
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\AppUserModelId" + "\\" + APP_AUMID)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ,
                          APP_DISPLAY_NAME)
        ico = os.path.join(ASSET_DIR, "pet.ico")
        if os.path.exists(ico):
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, ico)
        winreg.CloseKey(key)
    except Exception:
        pass


def replace_existing_instance():
    """顶替旧实例: 按 PID 文件找到上一个桌宠进程, 确认是 python 后结束它."""
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            old_pid = int(f.read().strip())
    except (OSError, ValueError):
        old_pid = None
    if old_pid and old_pid != os.getpid():
        PROCESS_TERMINATE = 0x0001
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(
            PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, old_pid
        )
        if h:
            buf = ctypes.create_unicode_buffer(512)
            size = ctypes.c_ulong(512)
            if (
                kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
                and "python" in buf.value.lower()
            ):
                kernel32.TerminateProcess(h, 0)
            kernel32.CloseHandle(h)
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def _parse_reset(v):
    """重置时间 -> epoch 秒. 兼容 epoch 数字 / ISO 字符串."""
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else v
    if isinstance(v, str):
        try:
            from datetime import datetime
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def extract_usage(data):
    """从 statusline JSON 提取 [(标签, 百分比0-100, 重置epoch), ...].

    官方 schema: rate_limits.{five_hour,seven_day}.{used_percentage, resets_at}
    仅订阅账号且会话首次 API 响应后才有, 每个窗口都可能缺.
    """
    rl = data.get("rate_limits") or {}
    out = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        item = rl.get(key)
        if isinstance(item, dict) and item.get("used_percentage") is not None:
            out.append((label, float(item["used_percentage"]),
                        _parse_reset(item.get("resets_at"))))
    return out


def fmt_remain(ts):
    if not ts:
        return ""
    remain = int(ts - time.time())
    if remain <= 0:
        return "now"
    d, r = divmod(remain, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def fmt_resets_in(ts):
    """参考面板措辞: 'Resets in 3 hr 40 min' / 'Resets in 6 days 10 hr'."""
    if not ts:
        return ""
    remain = int(ts - time.time())
    if remain <= 0:
        return "Resets soon"
    d, r = divmod(remain, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"Resets in {d} day{'s' if d > 1 else ''} {h} hr"
    if h:
        return f"Resets in {h} hr {m} min"
    return f"Resets in {m} min"


def fmt_resets_weekday(ts):
    """周限额措辞: 'Resets Tue 9:00 PM'."""
    if not ts:
        return ""
    from datetime import datetime
    dt = datetime.fromtimestamp(ts)
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"Resets {dt:%a} {hour12}:{dt:%M} {ampm}"


def fmt_ago(ts):
    """'<1 min ago' / 'x min ago' / 'x hr ago'."""
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 60:
        return "<1 min ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} hr ago"


def usage_color(pct):
    if pct >= 80:
        return "#f85149"
    if pct >= 50:
        return "#d29922"
    return "#3fb950"


def draw_badge(pct5, pct7):
    """单个托盘徽章: 上半 5h 用量, 下半 7d 用量, 各自按红黄绿分档底色."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 63, 30), fill=usage_color(pct5))
    d.rectangle((0, 33, 63, 63), fill=usage_color(pct7))
    try:
        font = ImageFont.truetype("arialbd.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    d.text((32, 15), str(min(round(pct5), 99)), font=font, fill="white", anchor="mm")
    d.text((32, 48), str(min(round(pct7), 99)), font=font, fill="white", anchor="mm")
    mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 63, 63), radius=12, fill=255)
    return Image.composite(img, Image.new("RGBA", (64, 64), (0, 0, 0, 0)), mask)


def fetch_usage_api():
    """直查 Anthropic OAuth 用量接口 (同 /usage 命令数据源).

    读本地 Claude Code 凭证; token 过期就放弃 (Claude Code 跑起来会自己刷新),
    调用方降级用 statusline 文件.
    """
    with open(CRED_FILE, encoding="utf-8") as f:
        cred = json.load(f)
    oauth = cred.get("claudeAiOauth") or cred
    token = oauth.get("accessToken")
    expires_ms = oauth.get("expiresAt") or 0
    if not token or expires_ms / 1000 < time.time() + 60:
        return None
    req = urllib.request.Request(USAGE_API, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": CLAUDE_UA,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    data["_subscription"] = oauth.get("subscriptionType") or ""
    out = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        item = data.get(key)
        if isinstance(item, dict) and item.get("utilization") is not None:
            out.append((label, float(item["utilization"]),
                        _parse_reset(item.get("resets_at"))))
    if not out:
        return None
    return out, data


def limit_rows(data):
    """从 API 完整响应提取详情面板行: [(名称, pct, reset_epoch, 时长秒, 分组), ...]."""
    rows = []
    for lim in data.get("limits") or []:
        if not isinstance(lim, dict) or lim.get("percent") is None:
            continue
        kind = lim.get("kind", "")
        group = lim.get("group") or ("session" if kind == "session" else "weekly")
        scope = lim.get("scope") or {}
        model = scope.get("model") if isinstance(scope, dict) else str(scope)
        if isinstance(model, dict):
            model = model.get("display_name") or model.get("id") or ""
        if kind == "session":
            name = "Current session"
        elif kind == "weekly_all":
            name = "All models"
        elif kind == "weekly_scoped":
            name = model or "Scoped models"
        else:
            name = f"{kind} {model}".strip()
        duration = 5 * 3600 if group == "session" else 7 * 86400
        # weekly_scoped (单模型周限) 的上限是总周限的 50%, 进度条总量按 0.5 折算
        cap = 0.5 if kind == "weekly_scoped" else 1.0
        rows.append((name, float(lim["percent"]),
                     _parse_reset(lim.get("resets_at")), duration, group, cap))
    return rows


def tray_area_rect():
    """返回 (任务栏rect, 托盘区rect), 找不到返回 None."""
    user32 = ctypes.windll.user32
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar:
        return None
    notify = user32.FindWindowExW(taskbar, 0, "TrayNotifyWnd", None)
    r_task, r_notify = wintypes.RECT(), wintypes.RECT()
    if not user32.GetWindowRect(taskbar, ctypes.byref(r_task)):
        return None
    if not (notify and user32.GetWindowRect(notify, ctypes.byref(r_notify))):
        r_notify = r_task
    return r_task, r_notify


def load_frames(path):
    im = Image.open(path)
    frames = []
    for frame in ImageSequence.Iterator(im):
        rgba = frame.convert("RGBA")
        rgba.thumbnail((PET_SIZE, PET_SIZE), Image.LANCZOS)
        # 半透明像素硬阈值二值化, 避免与透明键色混出品红描边
        mask = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
        bg = Image.new("RGBA", rgba.size, TRANS_COLOR)
        bg.paste(rgba, (0, 0), mask)
        duration = max(frame.info.get("duration", 70), 20)
        frames.append((ImageTk.PhotoImage(bg.convert("RGB")), duration))
    return frames


class Pet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANS_COLOR)
        self.root.configure(bg=TRANS_COLOR)

        self.gifs = {
            name: load_frames(os.path.join(GIF_DIR, f"{name}.gif"))
            for name in set(STATE_GIF.values())
        }

        self.label = tk.Label(self.root, bg=TRANS_COLOR, bd=0)
        self.label.pack()

        self.tray_icon = None
        self._api_windows = None
        self._api_full = None
        self._api_ts = 0.0
        self._alert_state = {}
        self.popup = None
        self.sess_popup = None
        self._popup_imgs = []
        self._sess_imgs = []
        self._tr_cache = {}
        # settings.json 的 model 若带 [1m], 记下其基础名用于 1M 窗口判定
        self._settings_1m_base = ""
        try:
            with open(os.path.join(os.path.expanduser("~"), ".claude",
                                   "settings.json"), encoding="utf-8") as f:
                model_cfg = json.load(f).get("model") or ""
            if "[1m]" in model_cfg:
                self._settings_1m_base = model_cfg.split("[")[0]
        except Exception:
            pass
        self._fetch_now = threading.Event()
        threading.Thread(target=self._api_loop, daemon=True).start()
        # 自动更新: 开发目录(.git)跳过, 避免覆盖工作区
        if not os.path.exists(os.path.join(BASE, ".git")):
            threading.Thread(target=self._update_loop, daemon=True).start()

        self.display_state = "idle"
        self.frame_idx = 0
        self.anim_job = None

        self._place_window()
        self._bind_events()
        self._start_tray()
        self._animate()
        self._poll_state()
        self._poll_usage()

    # ---------- 自动更新 ----------

    def _update_loop(self):
        time.sleep(60)
        while True:
            try:
                self._check_update()
            except Exception:
                pass
            time.sleep(UPDATE_CHECK_S)

    def _check_update(self, manual=False):
        """对比远端 version.txt; 有新版下载覆盖并重启. 返回是否触发了更新."""
        req = urllib.request.Request(UPDATE_VER_URL,
                                     headers={"User-Agent": "ClaudePetLeiMi"})
        with urllib.request.urlopen(req, timeout=15) as r:
            remote = r.read().decode("utf-8", "ignore").strip()
        local = local_version()
        if not remote or remote == local:
            if manual:
                self._notify(f"已是最新版本 v{local}")
            return False
        import shutil
        import subprocess
        import tempfile
        import zipfile
        zpath = os.path.join(tempfile.gettempdir(), "ClaudePetLeiMi_upd.zip")
        req = urllib.request.Request(UPDATE_ZIP_URL,
                                     headers={"User-Agent": "ClaudePetLeiMi"})
        with urllib.request.urlopen(req, timeout=120) as r, \
                open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        exdir = os.path.join(tempfile.gettempdir(), "ClaudePetLeiMi_upd")
        shutil.rmtree(exdir, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(exdir)
        shutil.copytree(os.path.join(exdir, "ClaudePetLeiMi-main"), BASE,
                        dirs_exist_ok=True)
        # 幂等重跑 hooks 合并 (新版本可能新增事件)
        try:
            subprocess.run([sys.executable,
                            os.path.join(BASE, "install_hooks.py")],
                           timeout=30)
        except Exception:
            pass
        self.root.after(0, lambda: self._finish_update(remote))
        return True

    def _notify(self, message, title="ClaudePetLeiMi"):
        """Windows Toast 通知. 归属名/图标来自注册的 AUMID (蕾米埃尔).

        pystray 的旧式气泡通知归属名永远显示 Python, 不吃 AUMID, 所以走 WinRT.
        """
        import base64
        import subprocess

        def esc(s):
            return (s.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace("'", "''"))

        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] "
            "| Out-Null\n"
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::"
            "GetTemplateContent([Windows.UI.Notifications."
            "ToastTemplateType]::ToastText02)\n"
            "$texts = $xml.GetElementsByTagName('text')\n"
            f"$texts.Item(0).AppendChild($xml.CreateTextNode('{esc(title)}'))"
            " | Out-Null\n"
            f"$texts.Item(1).AppendChild($xml.CreateTextNode('{esc(message)}'))"
            " | Out-Null\n"
            "[Windows.UI.Notifications.ToastNotificationManager]::"
            f"CreateToastNotifier('{APP_AUMID}').Show("
            "[Windows.UI.Notifications.ToastNotification]::new($xml))\n"
        )
        try:
            enc = base64.b64encode(ps.encode("utf-16-le")).decode()
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                ["powershell", "-NoProfile", "-EncodedCommand", enc],
                creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass

    def _open_claude_desktop(self, new_chat=False):
        """打开 Claude Desktop; new_chat=True 时新建对话 (托盘左键, 同 CD 托盘行为)."""
        import subprocess
        try:
            os.startfile("claude://new" if new_chat else "claude://")
            return
        except OSError:
            pass
        try:  # 协议缺失时用商店应用 AUMID 兜底 (发布者哈希跨机器一致)
            subprocess.Popen(
                ["explorer.exe",
                 r"shell:appsFolder\Claude_pzs8sxrjxfjjc!Claude"])
        except Exception:
            pass

    def _uninstall(self):
        """菜单"卸载": 挽留弹窗(蕾米图+共犯提示语)确认后执行 uninstall.ps1 并退出."""
        if not self._confirm_uninstall():
            return
        import subprocess
        import tempfile
        script = os.path.join(BASE, "uninstall.ps1")
        if os.path.exists(script):
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", script],
                cwd=tempfile.gettempdir(), creationflags=CREATE_NO_WINDOW)
        self._quit()

    def _confirm_uninstall(self):
        """卸载二次确认: 蕾米图 + "是否不再成为蕾米埃尔的共犯？"; 图缺失时退化为纯文字弹窗."""
        img_path = os.path.join(ASSET_DIR, "uninstall.png")
        if not os.path.exists(img_path):
            from tkinter import messagebox
            return messagebox.askyesno("卸载 ClaudePetLeiMi",
                                       "是否不再成为蕾米埃尔的共犯？",
                                       parent=self.root)

        result = {"ok": False}
        win = tk.Toplevel(self.root)
        win.title("卸载 ClaudePetLeiMi")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        try:
            win.iconbitmap(os.path.join(ASSET_DIR, "pet.ico"))
        except Exception:
            pass

        photo = ImageTk.PhotoImage(Image.open(img_path))
        win._photo = photo  # 持引用防 GC 白图
        tk.Label(win, image=photo).pack(padx=24, pady=(18, 8))
        tk.Label(win, text="是否不再成为蕾米埃尔的共犯？",
                 font=("Microsoft YaHei UI", 11)).pack(padx=28, pady=(0, 14))

        btns = tk.Frame(win)
        btns.pack(pady=(0, 16))

        def choose(ok):
            result["ok"] = ok
            win.destroy()

        tk.Button(btns, text="是", width=10,
                  command=lambda: choose(True)).pack(side="left", padx=8)
        no_btn = tk.Button(btns, text="否", width=10,
                           command=lambda: choose(False))
        no_btn.pack(side="left", padx=8)

        win.bind("<Escape>", lambda e: choose(False))
        win.protocol("WM_DELETE_WINDOW", lambda: choose(False))
        # 居中 + 模态; 默认焦点给"否"(挽留)
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{(win.winfo_screenwidth() - w) // 2}"
                     f"+{(win.winfo_screenheight() - h) // 2}")
        no_btn.focus_set()
        win.grab_set()
        win.wait_window()
        return result["ok"]

    def _manual_update(self):
        def run():
            try:
                self._check_update(manual=True)
            except Exception as e:
                self._notify(f"检查更新失败: {e}")
        threading.Thread(target=run, daemon=True).start()

    def _finish_update(self, ver):
        import subprocess
        self._notify(f"已更新到 v{ver}, 正在重启")
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        DETACHED = 0x00000008 | 0x00000200
        subprocess.Popen([pythonw, os.path.join(BASE, "claude_pet.pyw")],
                         creationflags=DETACHED, close_fds=True, cwd=BASE)
        # 新实例会按 pet.pid 顶替本进程

    def _api_loop(self):
        while True:
            try:
                result = fetch_usage_api()
                if result:
                    windows, full = result
                    self._api_windows, self._api_full = windows, full
                    self._api_ts = time.time()
                    self._check_alerts(windows)
            except Exception:
                pass
            self._fetch_now.wait(timeout=USAGE_API_POLL_S)
            self._fetch_now.clear()

    def _force_refresh(self):
        self._fetch_now.set()
        self.root.after(2500, self._refresh_popup)

    def _check_alerts(self, windows):
        """阈值提醒: 各限额按 cap 折算后首次越过 80%/95% 时弹系统通知, 窗口重置后重新计.

        与进度条变红同口径: 5h/7d(cap=1) 即原始 80%/95%; Fable 等单模型周限
        (weekly_scoped, cap=0.5) 折算后 80% = 原始 40% 就告警.
        """
        items = [(label, pct, reset, 1.0) for label, pct, reset in windows]
        if self._api_full:
            for name, pct, reset, _dur, group, cap in limit_rows(self._api_full):
                if group != "session" and cap < 1:
                    items.append((name, pct, reset, cap))
        for label, pct, reset, cap in items:
            eff = pct / cap
            prev_reset, prev_level = self._alert_state.get(label, (None, 0))
            if reset != prev_reset:
                prev_level = 0
            # 越过 80% 告警线后, 每 +5% 通知一次 (80/85/90/95/100)
            level = int(eff // 5) * 5 if eff >= 80 else 0
            if level > prev_level:
                self._notify(
                    f"用量告急，天才程序员即将陨落！\n"
                    f"{label} 已用 {round(pct)}%，"
                    f"{fmt_remain(reset)}后重置", "Claude 用量提醒")
            self._alert_state[label] = (reset, max(level, prev_level))

    # ---------- 窗口 ----------

    def _place_window(self):
        x, y = None, None
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            x, y = cfg.get("x"), cfg.get("y")
        except Exception:
            pass
        if x is None or y is None:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x, y = sw - PET_SIZE - 40, sh - PET_SIZE - 80
        self.root.geometry(f"+{x}+{y}")

    def _save_cfg(self):
        try:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                json.dump({"x": self.root.winfo_x(), "y": self.root.winfo_y()}, f)
        except OSError:
            pass

    def _quit(self):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def _bind_events(self):
        self.label.bind("<Button-1>", self._drag_start)
        self.label.bind("<B1-Motion>", self._drag_move)
        self.label.bind("<ButtonRelease-1>", lambda e: self._save_cfg())
        self.label.bind("<Double-Button-1>", lambda e: self._toggle_popup())

        self.ctx_menu = None
        self.label.bind("<Button-3>",
                        lambda e: self._show_menu(e.x_root, e.y_root))

    # ---------- 右键菜单 (TranslucentTB 风格浅色圆角 flyout) ----------

    @staticmethod
    def _system_dark():
        """跟随系统主题 (TranslucentTB 同款行为): SystemUsesLightTheme=0 为深色."""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            val, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return val == 0
        except Exception:
            return False

    def _close_menu(self):
        if self.ctx_menu:
            self.ctx_menu.destroy()
            self.ctx_menu = None

    def _show_menu(self, x, y, prefer_up=False):
        """仿亚克力 flyout: 截取背景 -> 高斯模糊+主题色调 -> PIL 画圆角卡片/文字.

        真 DWM 亚克力会把 GDI 内容当全透明 (tk 文字消失), 且圆角 region 无抗锯齿,
        所以整张菜单用 PIL 渲染: 圆角/描边/悬停态全部 AA, 四角外是真实背景截图.
        prefer_up: 菜单弹在点击点右上方 (托盘用).
        """
        self._close_menu()
        dark = self._system_dark()
        items = [
            ("Show App", lambda: self._open_claude_desktop(False),
             ""),
            ("用量详情", self._toggle_popup, ""),
            ("会话状态", self._toggle_sessions, ""),
            None,
            ("检查更新", self._manual_update, ""),
            ("卸载", self._uninstall, ""),
            ("退出", self._quit, ""),
        ]
        try:
            # Microsoft YaHei UI (msyh.ttc index 1), Win11 菜单同源字体
            font = ImageFont.truetype("msyh.ttc", 14, index=1)
            icon_font = ImageFont.truetype("segmdl2.ttf", 15)
        except OSError:
            font = icon_font = ImageFont.load_default()
        row_h, sep_h, pad_v, pad_x, radius = 36, 9, 6, 14, 8
        text_x = pad_x + 15 + 12  # 图标列之后
        w = 190
        h = pad_v * 2 + sum(sep_h if it is None else row_h for it in items)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(8, min(x, sw - w - 8))
        if prefer_up or y + h > sh - 48:
            y = y - h - 4
        y = max(8, y)

        try:
            shot = ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert("RGBA")
        except Exception:
            shot = Image.new("RGBA", (w, h), "#808080")
        glass = shot.filter(ImageFilter.GaussianBlur(14))
        tint = (32, 32, 32, 208) if dark else (243, 243, 243, 208)
        glass = Image.alpha_composite(glass, Image.new("RGBA", (w, h), tint))

        def rounded(fill=None, outline=None, width=1, inset=0, rad=radius):
            layer = Image.new("RGBA", (w * 4, h * 4), (0, 0, 0, 0))
            ImageDraw.Draw(layer).rounded_rectangle(
                (inset * 4, inset * 4, w * 4 - 1 - inset * 4,
                 h * 4 - 1 - inset * 4),
                radius=rad * 4, fill=fill, outline=outline, width=width * 4)
            return layer.resize((w, h), Image.LANCZOS)

        base = shot.copy()
        base.paste(glass, (0, 0),
                   rounded(fill=(255, 255, 255, 255)).getchannel("A"))
        border_c = (255, 255, 255, 55) if dark else (0, 0, 0, 45)
        base = Image.alpha_composite(base, rounded(outline=border_c))

        fg = (240, 240, 240, 255) if dark else (26, 26, 26, 255)
        # 分隔线: 浅灰色
        sep_c = (205, 205, 205, 180)
        hover_c = (255, 255, 255, 28) if dark else (0, 0, 0, 20)
        clickable = []  # (y0, y1, callback, hover序号)
        layout = []     # (kind, ...)
        cy, idx = pad_v, 0
        for it in items:
            if it is None:
                layout.append(("sep", cy + sep_h // 2))
                cy += sep_h
            else:
                layout.append(("item", cy, it[0], idx, it[2]))
                clickable.append((cy, cy + row_h, it[1], idx))
                cy += row_h
                idx += 1

        def render(hover_idx):
            img = base.copy()
            if hover_idx >= 0:
                for y0, y1, _cb, i in clickable:
                    if i == hover_idx:
                        hov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                        ImageDraw.Draw(hov).rounded_rectangle(
                            (5, y0 + 1, w - 6, y1 - 2), radius=5, fill=hover_c)
                        img = Image.alpha_composite(img, hov)
            d = ImageDraw.Draw(img)
            for entry in layout:
                if entry[0] == "sep":
                    d.line((4, entry[1], w - 4, entry[1]), fill=sep_c)
                else:
                    mid = entry[1] + row_h // 2
                    d.text((pad_x, mid), entry[4], font=icon_font, fill=fg,
                           anchor="lm")
                    d.text((text_x, mid), entry[2], font=font, fill=fg,
                           anchor="lm")
            return ImageTk.PhotoImage(img.convert("RGB"))

        photos = {-1: render(-1)}
        for _y0, _y1, _cb, i in clickable:
            photos[i] = render(i)
        self._menu_photos = photos

        m = tk.Toplevel(self.root)
        self.ctx_menu = m
        m.overrideredirect(True)
        m.attributes("-topmost", True)
        lbl = tk.Label(m, image=photos[-1], bd=0, highlightthickness=0)
        lbl.pack()

        def row_at(ey):
            for y0, y1, cb, i in clickable:
                if y0 <= ey < y1:
                    return cb, i
            return None, -1

        lbl.bind("<Motion>",
                 lambda e: lbl.configure(image=photos[row_at(e.y)[1]]))
        lbl.bind("<Leave>", lambda e: lbl.configure(image=photos[-1]))

        def on_click(e):
            cb, _ = row_at(e.y)
            self._close_menu()
            if cb:
                cb()
        lbl.bind("<Button-1>", on_click)

        m.geometry(f"{w}x{h}+{x}+{y + 10}")

        # 入场动效: 上滑 10px 约 110ms
        steps = 8

        def _anim(i=1):
            if not (self.ctx_menu is m and m.winfo_exists()):
                return
            t = i / steps
            try:
                m.geometry(f"+{x}+{y + int((1 - t) * 10)}")
            except tk.TclError:
                return
            if i < steps:
                m.after(14, lambda: _anim(i + 1))
            else:
                m.focus_force()
                m.bind("<FocusOut>", lambda e: self._close_menu())

        m.bind("<Escape>", lambda e: self._close_menu())
        _anim()

    # ---------- 托盘徽章 ----------

    def _start_tray(self):
        if not HAS_TRAY:
            return
        pet = self

        class FlyoutIcon(pystray.Icon):
            """接管托盘点击: 左键开用量面板, 右键弹自绘 flyout (不走原生菜单)."""

            def _on_notify(self, wparam, lparam):
                WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
                if lparam == WM_LBUTTONUP:
                    # 同 Claude Desktop 托盘行为: 左键新建对话
                    pet.root.after(0, lambda: pet._open_claude_desktop(True))
                elif lparam == WM_RBUTTONUP:
                    pt = wintypes.POINT()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                    pet.root.after(
                        0, lambda x=pt.x, y=pt.y:
                        pet._show_menu(x, y, prefer_up=True))

        try:
            icon_img = Image.open(ICON_PNG).convert("RGBA").resize(
                (64, 64), Image.LANCZOS)
        except Exception:
            icon_img = draw_badge(0, 0)  # 图标缺失时退回数字徽章
        self.tray_icon = FlyoutIcon(
            "ClaudePetLeiMi", icon_img, "ClaudePetLeiMi",
        )
        self.tray_icon.run_detached()

    # ---------- 用量详情面板 ----------

    POPUP_W = 300

    def _toggle_popup(self):
        if self.popup and self.popup.winfo_exists():
            self._close_popup()
        else:
            self._open_popup()

    def _close_popup(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def _open_popup(self):
        self._close_popup()
        self._close_sessions()
        self.popup = tk.Toplevel(self.root)
        self.popup.overrideredirect(True)
        self.popup.attributes("-topmost", True)
        self.popup.configure(bg="#ffffff", highlightthickness=0)
        self.popup.bind("<Escape>", lambda e: self._close_popup())
        self._refresh_popup()
        self._animate_in(self.popup)
        self._bind_outside_close(self.popup, self._close_popup)

    # Claude Desktop Usage 页配色
    P_BG = "#ffffff"
    P_FG = "#1a1a1a"
    P_DIM = "#757575"
    P_TRACK = "#d7e4f9"
    P_FILL = "#2760cf"
    P_OVER = "#e5484d"
    BAR_W = 130
    BAR_H = 8

    def _draw_bar(self, parent, frac, over_pace, store=None, width=None):
        """PIL 4x 超采样渲染圆角进度条, 抗锯齿, 样式与 Claude Desktop 一致."""
        bw = width or self.BAR_W
        s = 4
        w, h = bw * s, self.BAR_H * s
        img = Image.new("RGB", (w, h), self.P_BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2,
                            fill=self.P_TRACK)
        if frac > 0:
            fw = max(int(w * min(frac, 1)), h)
            d.rounded_rectangle((0, 0, fw - 1, h - 1), radius=h // 2,
                                fill=self.P_OVER if over_pace else self.P_FILL)
        small = img.resize((bw, self.BAR_H), Image.LANCZOS)
        if os.environ.get("PET_DEBUG_BAR"):
            small.save(os.path.join(BASE, f"debug_bar_{bw}.png"))
        photo = ImageTk.PhotoImage(small)
        (self._popup_imgs if store is None else store).append(photo)
        return tk.Label(parent, image=photo, bg=self.P_BG, bd=0)

    def _limit_row(self, body, name, sub, pct, cap=1.0):
        """在共享网格 body 里追加一条限额行 (共享列宽, 进度条纵向对齐).

        cap: 该限额上限占总量比例 (weekly_scoped=0.5), 条的填充按 cap 折算;
        折算后 >=80% 变红, 否则统一蓝色.
        """
        bg = self.P_BG
        r = self._grid_row
        frac = pct / 100 / cap
        tk.Label(body, text=name, bg=bg, fg=self.P_FG, anchor="w",
                 font=(UI_FONT, 10)).grid(row=r, column=0, sticky="w",
                                          pady=(8, 0))
        tk.Label(body, text=sub, bg=bg, fg=self.P_DIM, anchor="w",
                 font=(UI_FONT, 9)).grid(row=r + 1, column=0, sticky="w")
        self._draw_bar(body, frac, frac >= 0.8).grid(
            row=r, column=1, rowspan=2, padx=(12, 10), pady=(8, 0))
        tk.Label(body, text=f"{round(pct)}% used", bg=bg, fg=self.P_DIM,
                 font=(UI_FONT, 9)).grid(row=r, column=2, rowspan=2,
                                         sticky="e", pady=(8, 0))
        self._grid_row += 2

    def _refresh_popup(self):
        if not (self.popup and self.popup.winfo_exists()):
            return
        for w in self.popup.winfo_children():
            w.destroy()
        self.popup._border_size = None  # 边框控件已随子控件销毁, 需重建
        self._popup_imgs = []
        bg, fg, dim = self.P_BG, self.P_FG, self.P_DIM

        data = self._api_full
        rows = limit_rows(data) if data else []
        now = time.time()

        # 标题: Your usage limits  <订阅类型>
        head = tk.Frame(self.popup, bg=bg)
        head.pack(fill="x", padx=20, pady=(14, 2))
        tk.Label(head, text="Your usage limits", bg=bg, fg=fg,
                 font=(UI_FONT, 11, "bold")).pack(side="left")
        sub_type = (data or {}).get("_subscription") or ""
        if sub_type:
            tk.Label(head, text="  " + sub_type.capitalize(), bg=bg, fg=dim,
                     font=(UI_FONT, 10)).pack(side="left")
        close = tk.Label(head, text="✕", bg=bg, fg=dim, font=(UI_FONT, 10))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self._close_popup())

        if not rows:
            tk.Label(self.popup, text="No data yet (waiting for first fetch)",
                     bg=bg, fg=dim, font=(UI_FONT, 9)).pack(padx=20, pady=16)

        session_rows = [r for r in rows if r[4] == "session"]
        weekly_rows = [r for r in rows if r[4] != "session"]

        body = tk.Frame(self.popup, bg=bg)
        body.pack(fill="x", padx=20)
        body.grid_columnconfigure(0, minsize=134)
        body.grid_columnconfigure(1, weight=1)
        self._grid_row = 0

        for name, pct, reset, duration, _g, cap in session_rows:
            self._limit_row(body, name, fmt_resets_in(reset), pct, cap)

        if weekly_rows:
            tk.Label(body, text="Weekly limits", bg=bg, fg=fg,
                     font=(UI_FONT, 11, "bold"), anchor="w").grid(
                row=self._grid_row, column=0, columnspan=3, sticky="w",
                pady=(16, 0))
            self._grid_row += 1
            for name, pct, reset, duration, _g, cap in weekly_rows:
                self._limit_row(body, name, fmt_resets_weekday(reset), pct,
                                cap)

        extra = (data or {}).get("extra_usage") or {}
        if extra.get("is_enabled") and extra.get("used_credits") is not None:
            row = tk.Frame(self.popup, bg=bg)
            row.pack(fill="x", padx=20, pady=(14, 0))
            tk.Label(row, text="Usage credits", bg=bg, fg=fg,
                     font=(UI_FONT, 10)).pack(side="left")
            cur = extra.get("currency") or "USD"
            sym = "$" if cur == "USD" else cur + " "
            tk.Label(row, text=f"{sym}{extra['used_credits']:.2f} spent",
                     bg=bg, fg=dim, font=(UI_FONT, 9)).pack(side="right")

        foot = tk.Frame(self.popup, bg=bg)
        foot.pack(fill="x", padx=20, pady=(14, 12))
        tk.Label(foot, text=f"Last updated: {fmt_ago(self._api_ts)}", bg=bg,
                 fg=dim, font=(UI_FONT, 9)).pack(side="left")
        refresh = tk.Label(foot, text=" ⟳", bg=bg, fg=dim, font=(UI_FONT, 10))
        refresh.pack(side="left")
        refresh.bind("<Button-1>", lambda e: self._force_refresh())

        self._position_popup(self.popup)

    # ---------- 面板定位 / 圆角 ----------

    @staticmethod
    def _top_hwnd(win):
        GA_ROOT = 2
        return ctypes.windll.user32.GetAncestor(win.winfo_id(), GA_ROOT)

    def _round_corners(self, win, w, h, radius=10):
        def apply():
            try:
                if not win.winfo_exists():
                    return
                hwnd = self._top_hwnd(win)
                rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
                    0, 0, w + 1, h + 1, radius * 2, radius * 2)
                ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
            except Exception:
                pass
        apply()
        win.after(60, apply)  # 未映射时可能没生效, 映射后补一次


    # 用量/会话两个面板统一定宽. 必须 >= 内容需求(约390), 否则 grid 放不下
    # 会从右侧裁掉进度条 Label, 圆头被切成方角!
    PANEL_W = 396

    def _decorate_border(self, win, w, h, radius=10, color="#d9d9d9"):
        """自绘 AA 圆角描边: 四角圆弧图 + 四边 1px 直线 (代替 highlight 矩形边框,
        后者会被 SetWindowRgn 在角上切断)."""
        if getattr(win, "_border_size", None) == (w, h):
            return
        win._border_size = (w, h)
        for x in getattr(win, "_border_widgets", []):
            try:
                x.destroy()
            except Exception:
                pass
        widgets, photos = [], []
        s, r = 4, radius
        big = Image.new("RGB", (2 * r * s, 2 * r * s), self.P_BG)
        ImageDraw.Draw(big).arc(
            (s // 2, s // 2, 2 * r * s - s // 2, 2 * r * s - s // 2),
            0, 360, fill=color, width=s)
        big = big.resize((2 * r, 2 * r), Image.LANCZOS)
        quads = {"tl": ((0, 0, r, r), (0, 0)),
                 "tr": ((r, 0, 2 * r, r), (w - r, 0)),
                 "bl": ((0, r, r, 2 * r), (0, h - r)),
                 "br": ((r, r, 2 * r, 2 * r), (w - r, h - r))}
        for box, pos in quads.values():
            photo = ImageTk.PhotoImage(big.crop(box))
            photos.append(photo)
            lbl = tk.Label(win, image=photo, bd=0, bg=self.P_BG)
            lbl.place(x=pos[0], y=pos[1])
            widgets.append(lbl)
        for x, y, ww, hh in ((r, 0, w - 2 * r, 1), (r, h - 1, w - 2 * r, 1),
                             (0, r, 1, h - 2 * r), (w - 1, r, 1, h - 2 * r)):
            f = tk.Frame(win, bg=color, bd=0)
            f.place(x=x, y=y, width=ww, height=hh)
            widgets.append(f)
        win._border_widgets = widgets
        win._border_photos = photos

    def _add_tooltip(self, widget, text):
        state = {"tip": None}

        def show(e):
            if state["tip"]:
                return
            t = tk.Toplevel(widget)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            tk.Label(t, text=text, bg="#333333", fg="#ffffff",
                     font=(UI_FONT, 9), padx=8, pady=3).pack()
            t.geometry(f"+{e.x_root + 12}+{e.y_root + 16}")
            state["tip"] = t

        def hide(_e):
            if state["tip"]:
                state["tip"].destroy()
                state["tip"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _position_popup(self, win):
        """定位到桌宠正上方, 永不遮挡; 顶部放不下就挪下方/侧面. 并裁圆角."""
        win.update_idletasks()
        w, h = self.PANEL_W, win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = min(max(8, px + pw - w), sw - w - 8)
        y = py - h - 8
        if y < 8:
            y = py + ph + 8
        if y + h > sh - 48:
            x = px - w - 8 if px - w - 8 > 8 else px + pw + 8
            y = min(max(8, py), sh - h - 48)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.update_idletasks()
        self._round_corners(win, w, h)
        self._decorate_border(win, w, h)
        win.geometry(f"+{x}+{y}")  # SetWindowRgn 可能在未映射时重置位置, 再钉一次

    def _follow_popups(self):
        for win in (self.popup, self.sess_popup):
            if win and win.winfo_exists():
                self._position_popup(win)

    def _animate_in(self, win):
        """开面板入场动效: 从下方 12px 上滑到位 (与菜单一致, TB 风格)."""
        win.update_idletasks()
        x, y = win.winfo_x(), win.winfo_y()
        steps = 8
        win.geometry(f"+{x}+{y + 12}")

        def anim(i=1):
            if not win.winfo_exists():
                return
            t = i / steps
            try:
                win.geometry(f"+{x}+{y + int((1 - t) * 12)}")
            except tk.TclError:
                return
            if i < steps:
                win.after(14, lambda: anim(i + 1))

        anim()

    def _bind_outside_close(self, win, closer):
        """点击面板/桌宠以外的区域(焦点丢失且指针不在两者内)时自动关闭."""
        win.focus_force()

        def on_focus_out(_e):
            try:
                px, py = win.winfo_pointerxy()
            except tk.TclError:
                return
            for w in (self.root, win):
                try:
                    if (w.winfo_rootx() <= px < w.winfo_rootx() + w.winfo_width()
                            and w.winfo_rooty() <= py
                            < w.winfo_rooty() + w.winfo_height()):
                        return
                except tk.TclError:
                    pass
            closer()

        win.bind("<FocusOut>", on_focus_out)

    # ---------- 会话状态面板 ----------

    def _toggle_sessions(self):
        if self.sess_popup and self.sess_popup.winfo_exists():
            self._close_sessions()
        else:
            self._close_popup()
            self._open_sessions()

    def _close_sessions(self):
        if self.sess_popup:
            self.sess_popup.destroy()
            self.sess_popup = None

    def _open_sessions(self):
        self.sess_popup = tk.Toplevel(self.root)
        self.sess_popup.overrideredirect(True)
        self.sess_popup.attributes("-topmost", True)
        self.sess_popup.configure(bg=self.P_BG, highlightthickness=0)
        self.sess_popup.bind("<Escape>", lambda e: self._close_sessions())
        self._refresh_sessions()
        self._animate_in(self.sess_popup)
        self._bind_outside_close(self.sess_popup, self._close_sessions)

    @staticmethod
    def _read_sessions():
        try:
            with open(SESS_FILE, encoding="utf-8") as f:
                sess = json.load(f)
            if isinstance(sess, dict):
                return sess
        except Exception:
            pass
        return {}

    def _transcript_info(self, path):
        """从 transcript 提取 (会话标题, context窗口文本). 按文件大小缓存.

        标题 = ai-title 记录的 aiTitle; context = 最后一条 assistant 消息
        usage 的 input+cache_read+cache_creation, 窗口按模型名 [1m] 判 1M/200k.
        """
        if not path:
            return None, None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None, None
        cached = self._tr_cache.get(path)
        if cached and cached[0] == size:
            return cached[1], cached[2]

        title, ctx_text, model = None, None, ""
        try:
            with open(path, "rb") as f:
                head = f.read(512 * 1024).decode("utf-8", "ignore")
                tail = head
                if size > 2_000_000:
                    f.seek(size - 2_000_000)
                    tail = f.read().decode("utf-8", "ignore")
            ctx = None
            for chunk in (head, tail):
                for line in chunk.splitlines():
                    if '"ai-title"' in line:
                        try:
                            title = json.loads(line).get("aiTitle") or title
                        except ValueError:
                            pass
            for line in reversed(tail.splitlines()):
                if '"usage"' not in line or '"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("isSidechain"):
                    continue
                usage = (rec.get("message") or {}).get("usage") or {}
                if usage.get("input_tokens") is None:
                    continue
                ctx = (usage.get("input_tokens", 0)
                       + usage.get("cache_read_input_tokens", 0)
                       + usage.get("cache_creation_input_tokens", 0))
                model = (rec.get("message") or {}).get("model") or ""
                break
            if ctx is not None:
                # transcript 里模型 ID 不带 [1m] 后缀, 结合 settings 配置判窗口
                is_1m = ("[1m]" in model or ctx > 200_000
                         or (self._settings_1m_base
                             and model.startswith(self._settings_1m_base)))
                win_size = 1_000_000 if is_1m else 200_000
                win_label = "1M" if is_1m else "200k"
                frac = ctx / win_size
                ctx_text = (f"{ctx / 1000:.0f}k / {win_label}", frac)
        except OSError:
            pass
        self._tr_cache[path] = (size, title, ctx_text)
        return title, ctx_text

    def _refresh_sessions(self):
        win = self.sess_popup
        if not (win and win.winfo_exists()):
            return
        for w in win.winfo_children():
            w.destroy()
        win._border_size = None  # 边框控件已随子控件销毁, 需重建
        bg, fg, dim = self.P_BG, self.P_FG, self.P_DIM

        head = tk.Frame(win, bg=bg)
        head.pack(fill="x", padx=20, pady=(14, 4))
        tk.Label(head, text="会话状态", bg=bg, fg=fg,
                 font=(UI_FONT, 11, "bold")).pack(side="left")
        close = tk.Label(head, text="✕", bg=bg, fg=dim, font=(UI_FONT, 10))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self._close_sessions())

        sess = self._read_sessions()
        items = sorted(sess.items(), key=lambda kv: kv[1].get("ts", 0),
                       reverse=True)
        if not items:
            tk.Label(win, text="当前没有活跃会话", bg=bg, fg=dim,
                     font=(UI_FONT, 9)).pack(padx=20, pady=(6, 16))
        body = tk.Frame(win, bg=bg)
        body.pack(fill="x", padx=20)
        body.grid_columnconfigure(1, minsize=92)
        body.grid_columnconfigure(2, weight=1)
        self._sess_imgs = []
        for idx, (sid, info) in enumerate(items[:12]):
            r = idx * 2
            state = info.get("state", "idle")
            verb, color = SESSION_STATES.get(state, (state, dim))
            cwd = (info.get("cwd") or "").replace("/", "\\")
            tail = "\\".join(cwd.rstrip("\\").split("\\")[-2:]) if cwd else sid[:8]
            title, ctx = self._transcript_info(info.get("transcript"))
            full_name = title or tail
            # 按像素宽度截断 (中文按字符数截会溢出单元格, 顶歪布局)
            import tkinter.font as tkfont
            name_font = tkfont.Font(family=UI_FONT, size=9)
            sess_name = full_name
            if name_font.measure(sess_name) > 150:
                while sess_name and name_font.measure(sess_name + "…") > 150:
                    sess_name = sess_name[:-1]
                sess_name += "…"
            tk.Label(body, text="●", bg=bg, fg=color, font=(UI_FONT, 10)
                     ).grid(row=r, column=0, sticky="w", pady=(8, 0))
            tk.Label(body, text=verb, bg=bg, fg=fg, font=(UI_FONT, 10)
                     ).grid(row=r, column=1, sticky="w", padx=(6, 0),
                            pady=(8, 0))
            name_lbl = tk.Label(body, text=sess_name, bg=bg, fg=dim,
                                anchor="w", font=(UI_FONT, 9))
            name_lbl.grid(row=r, column=2, sticky="w", padx=(10, 12),
                          pady=(8, 0))
            if sess_name != full_name:
                self._add_tooltip(name_lbl, full_name)
            tk.Label(body, text=fmt_ago(info.get("ts")), bg=bg, fg=dim,
                     font=(UI_FONT, 9)).grid(row=r, column=3, sticky="e",
                                             pady=(8, 0))
            if ctx:
                # context 行: 文本对齐状态动词列, 进度条对齐会话名称列, 右侧 x% used
                ctx_text, frac = ctx
                tk.Label(body, text=ctx_text, bg=bg, fg=dim, anchor="w",
                         font=(UI_FONT, 9)).grid(row=r + 1, column=1,
                                                 sticky="w", padx=(6, 0),
                                                 pady=(2, 0))
                self._draw_bar(body, frac, frac >= 0.8,
                               store=self._sess_imgs).grid(
                    row=r + 1, column=2, sticky="w", padx=(10, 12),
                    pady=(2, 0))
                tk.Label(body, text=f"{round(frac * 100)}% used", bg=bg,
                         fg=dim, font=(UI_FONT, 9)).grid(row=r + 1, column=3,
                                                         sticky="e",
                                                         pady=(2, 0))

        tk.Frame(win, bg=bg, height=12).pack()
        self._position_popup(win)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
        self._follow_popups()

    # ---------- 动画 ----------

    def _animate(self):
        frames = self.gifs[STATE_GIF[self.display_state]]
        self.frame_idx %= len(frames)
        photo, duration = frames[self.frame_idx]
        self.label.configure(image=photo)
        self.frame_idx += 1
        self.anim_job = self.root.after(duration, self._animate)

    def _set_display(self, state):
        if state == self.display_state:
            return
        self.display_state = state
        self.frame_idx = 0

    # ---------- 状态 ----------

    @staticmethod
    def _effective_state(raw, age):
        if raw == "done":
            if age < DONE_HOLD_S:
                return "done"
            raw, age = "idle", age - DONE_HOLD_S
        if raw in ("working", "thinking", "waiting", "error") and age > STALE_S:
            raw, age = "idle", age - STALE_S
        if raw == "idle" and age > IDLE_TO_STANDBY_S:
            return "standby"
        return raw

    def _poll_state(self):
        raw, ts = "idle", 0.0
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("state", "idle")
            ts = float(data.get("ts", 0))
        except Exception:
            pass
        if raw not in STATE_GIF:
            raw = "idle"
        self._set_display(self._effective_state(raw, time.time() - ts))
        self.root.after(POLL_MS, self._poll_state)

    # ---------- 用量条 ----------

    def _poll_usage(self):
        # 优先用 API 直查的新鲜数据, 失效则退回 statusline 落盘数据
        if self._api_windows and time.time() - self._api_ts < USAGE_API_FRESH_S:
            windows = self._api_windows
        else:
            windows = []
            try:
                with open(USAGE_FILE, encoding="utf-8") as f:
                    windows = extract_usage(json.load(f))
            except Exception:
                pass
        self._refresh_popup()
        self._refresh_sessions()
        self.root.after(15000, self._poll_usage)

    def run(self):
        self.root.mainloop()


def run_diagnostics():
    """--diag: 逐项检查用量数据链路, 用 python (而非 pythonw) 运行看输出."""
    print("== ClaudePetLeiMi usage diagnostics ==")
    # 1. Claude Code 凭证
    if os.path.exists(CRED_FILE):
        try:
            with open(CRED_FILE, encoding="utf-8") as f:
                cred = json.load(f)
            oauth = cred.get("claudeAiOauth") or cred
            token = oauth.get("accessToken")
            exp = (oauth.get("expiresAt") or 0) / 1000
            state = "valid" if exp > time.time() else "EXPIRED"
            print(f"[1] credentials: found | subscriptionType="
                  f"{oauth.get('subscriptionType')}"
                  f" | token={'yes' if token else 'NO'} | {state}"
                  f" ({(exp - time.time()) / 3600:.1f}h left)")
            if not oauth.get("subscriptionType"):
                print("    -> not a claude.ai subscription login; no usage windows")
        except Exception as e:
            print("[1] credentials parse FAILED:", e)
    else:
        print("[1] credentials file NOT FOUND:", CRED_FILE)
        print("    -> Claude Code not logged in on this machine, or not"
              " logged in with a claude.ai subscription account")
    # 2. 用量接口直查
    try:
        result = fetch_usage_api()
        if result:
            print("[2] usage API: OK ->", result[0])
        else:
            print("[2] usage API: SKIPPED (no token or expired; start a"
                  " Claude Code session so it refreshes the token, then retry)")
    except urllib.error.HTTPError as e:
        print(f"[2] usage API HTTP {e.code} {e.reason}")
        if e.code in (401, 403):
            print("    -> token invalid, or not a subscription account")
        elif e.code == 429:
            print("    -> rate limited, retry later")
    except Exception as e:
        print(f"[2] usage API network FAILED: {type(e).__name__}: {e}")
        print("    -> check this machine can reach api.anthropic.com"
              " (a proxy that does not cover python, or a firewall,"
              " will cause this)")
    # 3. statusline 兜底
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            age = (time.time() - d.get("_saved_at", 0)) / 60
            print(f"[3] statusline dump: found | updated {age:.0f} min ago"
                  f" | rate_limits="
                  f"{'yes' if d.get('rate_limits') else 'NO'}")
        except Exception as e:
            print("[3] statusline dump parse FAILED:", e)
    else:
        print("[3] statusline dump NOT FOUND")
        print("    -> no Claude Code session has run since install, or"
              " statusLine not configured / python not on PATH")
    # 4. hooks 状态文件
    print(f"[4] hook state files:"
          f" state={'yes' if os.path.exists(STATE_FILE) else 'NO'}"
          f" | sessions={'yes' if os.path.exists(SESS_FILE) else 'NO'}")
    if not os.path.exists(STATE_FILE):
        print("    -> hooks not firing: check ~/.claude/settings.json has"
              " cc_pet_hook entries, and restart Claude Code sessions"
              " after install")


if __name__ == "__main__":
    if "--diag" in sys.argv:
        run_diagnostics()
        sys.exit(0)
    register_app_identity()
    replace_existing_instance()
    pet = Pet()
    if "--popup" in sys.argv:      # 调试: 启动即开用量面板
        pet.root.after(1500, pet._open_popup)
    if "--sessions" in sys.argv:   # 调试: 启动即开会话面板
        pet.root.after(1500, pet._open_sessions)
    if "--menu" in sys.argv:       # 调试: 启动即在桌宠左上弹菜单
        pet.root.after(1500, lambda: pet._show_menu(
            pet.root.winfo_x() - 40, pet.root.winfo_y() - 60))
    if "--test-notify" in sys.argv:  # 调试: 发一条测试通知看归属名
        pet.root.after(4000, lambda: pet._notify(
            "用量告急，天才程序员即将陨落！\n5h 已用 83%，1h20m后重置",
            "Claude 用量提醒"))
    pet.run()
