#!/usr/bin/env python3
"""claude_pet_mac.py — ClaudePetLeiMi macOS 壳.

与 Windows 壳 (claude_pet.pyw) 共享 petcore 核心, 平台层全部换成原生实现:
  桌宠 GIF     -> 无边框透明 NSWindow + NSImageView (真 alpha, 无需键色抠图)
  右键菜单     -> 原生 NSMenu (永远盖在最上, 无 z 序问题)
  用量/会话面板 -> PIL 按 2x 渲染成整图贴 NSWindow (圆角/进度条与 Windows 同款)
  菜单栏徽章   -> NSStatusItem, 上 5h 下 7d 数字
  通知        -> osascript display notification
  单例        -> fcntl 文件锁 (抢不到就杀旧进程重试)
  自动更新     -> petcore.update 下载覆盖后 execv 重启

依赖: pip3 install pyobjc-framework-Cocoa pillow
运行: python3 claude_pet_mac.py           (LaunchAgent 由 install.sh 配置)
诊断: python3 claude_pet_mac.py --diag
"""
import io
import json
import os
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from petcore import (CRED_FILE, POLL_MS, SESS_FILE, SESSION_STATES,
                     STATE_FILE, STATE_GIF, UPDATE_CHECK_S, USAGE_API_FRESH_S,
                     USAGE_API_POLL_S, USAGE_FILE, AlertTracker,
                     CreditsTracker, PctHistory, PET_SIZE, download_and_apply,
                     draw_badge, effective_state, extra_amount, extract_usage,
                     fetch_usage_api, fmt_ago, fmt_resets_in,
                     fmt_resets_weekday, fmt_when, last_api_error_ts,
                     limit_rows, local_version, read_oauth, read_sessions,
                     remote_version, settings_1m_base, transcript_info)

from PIL import Image, ImageDraw, ImageFont, ImageSequence

GIF_DIR = os.path.join(BASE, "gifs")
ASSET_DIR = os.path.join(BASE, "assets")
CFG_FILE = os.path.join(BASE, "pet_config_mac.json")
LOCK_FILE = os.path.join(BASE, "pet-mac.lock")
PID_FILE = os.path.join(BASE, "pet-mac.pid")

# ---------- 面板样式 (与 Windows 壳同款) ----------
P_BG = "#ffffff"
P_FG = "#1a1a1a"
P_DIM = "#757575"
P_TRACK = "#d7e4f9"
P_FILL = "#2760cf"
P_OVER = "#e5484d"
P_BORDER = "#d9d9d9"
PANEL_W = 396
BAR_W, BAR_H = 130, 8
RADIUS = 10
FIG_INSET = 68
S = 2  # 面板整图按 2x 渲染, NSImage 缩回逻辑尺寸 -> retina 清晰

# ---------- pyobjc ----------
try:
    import objc
    from Foundation import NSData, NSMakeRect, NSMakeSize, NSObject, NSTimer
    import AppKit
    from AppKit import (NSApplication, NSColor, NSEvent, NSImage,
                        NSImageView, NSMenu, NSMenuItem, NSScreen,
                        NSStatusBar, NSWindow)
    from PyObjCTools import AppHelper

    def NSApp():
        # 不能 from AppKit import NSApp: 它在 import 时求值, 彼时 app 还没创建
        return NSApplication.sharedApplication()
except ImportError as e:
    if "--diag" not in sys.argv:
        sys.stderr.write(
            f"缺少依赖 ({e}); 先执行: pip3 install pyobjc-framework-Cocoa pillow\n")
        sys.exit(1)
    objc = None

# AppKit 常量 (个别老版本 pyobjc 不导出, 用数值兜底)
def _ak(name, default):
    return getattr(AppKit, name, default) if objc else default

BORDERLESS = _ak("NSWindowStyleMaskBorderless", 0)
BACKING = _ak("NSBackingStoreBuffered", 2)
LEVEL_STATUS = _ak("NSStatusWindowLevel", 25)
BEHAVIOR = (_ak("NSWindowCollectionBehaviorCanJoinAllSpaces", 1 << 0)
            | _ak("NSWindowCollectionBehaviorFullScreenAuxiliary", 1 << 8))
MASK_LDOWN = _ak("NSEventMaskLeftMouseDown", 1 << 1)
MASK_RDOWN = _ak("NSEventMaskRightMouseDown", 1 << 3)
EV_RUP = _ak("NSEventTypeRightMouseUp", 4)
ACCESSORY = _ak("NSApplicationActivationPolicyAccessory", 1)


# ---------- 字体 ----------
_FONT_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
]
_font_cache = {}


def ui_font(size, bold=False):
    """CJK 可用的 UI 字体; bold 尽力 (PingFang.ttc 常见布局 Medium 在 index 4)."""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    for path, idx in _FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        for index in ([4, 5, idx] if bold else [idx]):
            try:
                f = ImageFont.truetype(path, size, index=index)
                if f.getmask("测").getbbox():  # 粗验 CJK 覆盖
                    font = f
                    break
            except OSError:
                continue
        if font:
            break
    if font is None:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ---------- PIL <-> NSImage ----------
def pil_to_nsimage(img, logical_w=None, logical_h=None):
    buf = io.BytesIO()
    img.save(buf, "png")
    raw = buf.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))
    ns = NSImage.alloc().initWithData_(data)
    if logical_w:
        ns.setSize_(NSMakeSize(logical_w, logical_h))
    return ns


def load_frames(path):
    """GIF -> [(NSImage, duration_ms)]. mac 窗口支持真 alpha, 不做键色抠图."""
    im = Image.open(path)
    frames = []
    for frame in ImageSequence.Iterator(im):
        rgba = frame.convert("RGBA")
        rgba.thumbnail((PET_SIZE, PET_SIZE), Image.LANCZOS)
        duration = max(frame.info.get("duration", 70), 20)
        frames.append((pil_to_nsimage(rgba, *rgba.size), duration))
    return frames


# ---------- 通知 ----------
def notify(message, title="ClaudePetLeiMi"):
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    try:
        subprocess.Popen(["osascript", "-e",
                          f'display notification "{esc(message)}" '
                          f'with title "{esc(title)}"'])
    except Exception:
        pass


# ---------- 单例 ----------
_lock_fh = None


def acquire_singleton():
    """fcntl 锁独占; 抢不到 -> 按 pid 文件杀旧实例, 等 5s 再抢."""
    global _lock_fh
    import fcntl
    import signal
    _lock_fh = open(LOCK_FILE, "w")
    for attempt in range(10):
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fh.write(str(os.getpid()))
            _lock_fh.flush()
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
            return True
        except OSError:
            if attempt == 0:
                try:
                    with open(PID_FILE) as f:
                        os.kill(int(f.read().strip()), signal.SIGTERM)
                except Exception:
                    pass
            time.sleep(0.5)
    return False


# ---------- 面板 PIL 渲染 ----------
def _rounded_panel(w, h):
    """圆角白底 + 描边的空面板 (2x 像素), 返回 (img, draw)."""
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius=RADIUS * S,
                        fill=P_BG, outline=P_BORDER, width=S)
    return img, d


def _draw_bar(d, x, y, frac, over):
    """圆头进度条, 与 Windows 壳同款配色. 坐标为逻辑值."""
    x, y, w, h = x * S, y * S, BAR_W * S, BAR_H * S
    d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=h // 2,
                        fill=P_TRACK)
    fw = int(w * min(max(frac, 0.0), 1.0))
    if fw >= h:
        d.rounded_rectangle((x, y, x + fw - 1, y + h - 1), radius=h // 2,
                            fill=P_OVER if over else P_FILL)


def _draw_close(d, x, y, size=8, color=P_DIM):
    """矢量 ✕ (字体里未必有该字形, 直接画)."""
    x, y, s = x * S, y * S, size * S
    d.line([(x, y), (x + s, y + s)], fill=color, width=S)
    d.line([(x + s, y), (x, y + s)], fill=color, width=S)


def _draw_refresh(d, x, y, size=11, color=P_DIM):
    """矢量 ⟳: 开口圆弧 + 箭头."""
    import math
    x, y, s = x * S, y * S, size * S
    d.arc((x, y, x + s, y + s), start=-60, end=210, fill=color, width=S)
    cx, cy, r = x + s / 2, y + s / 2, s / 2
    ex = cx + r * math.cos(math.radians(-60))
    ey = cy + r * math.sin(math.radians(-60))
    d.polygon([(ex - 2 * S, ey - 2 * S), (ex + 3 * S, ey + S),
               (ex - 2 * S, ey + 3 * S)], fill=color)


def _text(d, x, y, s, size, color, bold=False, anchor="la"):
    d.text((x * S, y * S), s, font=ui_font(size * S, bold), fill=color,
           anchor=anchor)


def _text_w(s, size, bold=False):
    box = ui_font(size * S, bold).getbbox(s)
    return (box[2] - box[0]) / S if box else 0


def render_usage_panel(data, api_ts, credits_today_fn, projection_fn,
                       suite_on, refreshing=False):
    """用量详情面板整图. 返回 (PIL图, 逻辑高度, 点击区域 {名称: (x0,y0,x1,y1)})."""
    rows = limit_rows(data) if data else []
    session_rows = [r for r in rows if r[4] == "session"]
    weekly_rows = [r for r in rows if r[4] != "session"]
    extra = (data or {}).get("extra_usage") or {}
    has_credits = extra.get("is_enabled") and extra.get(
        "used_credits") is not None
    proj = projection_fn(rows) if rows else None

    top = (FIG_INSET - 10 if suite_on and os.path.exists(
        os.path.join(ASSET_DIR, "panel_banner.png")) else 0)
    h = top + 14 + 22 + 6          # 标题区
    if not rows:
        h += 40
    h += len(session_rows) * 34
    if weekly_rows:
        h += 16 + 22 + len(weekly_rows) * 34
    if has_credits:
        h += 14 + 20
    if proj:
        h += 14 + 20
    h += 12 + 20 + 10              # footer

    img, d = _rounded_panel(PANEL_W, h)
    regions = {}
    y = top + 14

    # 标题
    _text(d, 20, y, "Your usage limits", 11, P_FG, bold=True)
    sub_type = (data or {}).get("_subscription") or ""
    if sub_type:
        _text(d, 20 + _text_w("Your usage limits", 11, True) + 8, y + 1,
              sub_type.capitalize(), 10, P_DIM)
    _draw_close(d, PANEL_W - 28, y + 3)
    regions["close"] = (PANEL_W - 40, y - 6, PANEL_W - 8, y + 20)
    y += 22 + 6

    if not rows:
        _text(d, 20, y + 8, "No data yet (waiting for first fetch)", 9, P_DIM)
        y += 40

    def limit_row(y, name, sub, pct, active):
        frac = pct / 100
        if sub:
            _text(d, 20, y + 2, name, 10, P_FG, bold=active)
            _text(d, 20, y + 18, sub, 9, P_DIM)
        else:
            _text(d, 20, y + 10, name, 10, P_FG, bold=active)
        _draw_bar(d, 20 + 134 + 12, y + 12, frac, frac >= 0.8)
        _text(d, PANEL_W - 20, y + 10, f"{round(pct)}% used", 9, P_DIM,
              anchor="ra")
        return y + 34

    for name, pct, reset, _dur, _g, active in session_rows:
        y = limit_row(y, name, fmt_resets_in(reset), pct, active)

    if weekly_rows:
        y += 16
        _text(d, 20, y, "Weekly limits", 11, P_FG, bold=True)
        y += 22
        for name, pct, reset, _dur, _g, active in weekly_rows:
            y = limit_row(y, name, fmt_resets_weekday(reset), pct, active)

    if has_credits:
        y += 14
        _text(d, 20, y, "Usage credits", 10, P_FG)
        cur = extra.get("currency") or "USD"
        sym = "$" if cur == "USD" else cur + " "
        amount = extra_amount(extra) or 0.0
        today = credits_today_fn(amount)
        text = f"{sym}{amount:.2f} spent"
        if today >= 0.01:
            text += f" · today +{sym}{today:.2f}"
        _text(d, PANEL_W - 20, y + 1, text, 9,
              P_OVER if today >= 0.01 else P_DIM, anchor="ra")
        y += 20

    if proj:
        y += 14
        pname, hit, before_reset = proj
        _text(d, 20, y, "Projection", 10, P_FG)
        if before_reset:
            ptext, pcolor = f"{pname} runs out ~{fmt_when(hit)}", P_OVER
        else:
            ptext, pcolor = "On pace to last until reset", P_DIM
        _text(d, PANEL_W - 20, y + 1, ptext, 9, pcolor, anchor="ra")
        y += 20

    y += 12
    upd = "Refreshing…" if refreshing else f"Last updated: {fmt_ago(api_ts)}"
    _text(d, 20, y, upd, 9, P_DIM)
    rx = 20 + _text_w(upd, 9) + 10
    _draw_refresh(d, rx, y + 1)
    regions["refresh"] = (rx - 6, y - 6, rx + 22, y + 20)
    return img, h, regions


def render_sessions_panel(sess_items, suite_on):
    """会话状态面板整图. sess_items: [(状态, 显示名, ago文本, ctx或None)]."""
    top = (FIG_INSET - 10 if suite_on and os.path.exists(
        os.path.join(ASSET_DIR, "panel_banner.png")) else 0)
    h = top + 14 + 22 + 8
    if not sess_items:
        h += 34
    for _s, _n, _a, ctx in sess_items:
        h += 30 + (22 if ctx else 0)
    h += 14

    img, d = _rounded_panel(PANEL_W, h)
    regions = {}
    y = top + 14
    _text(d, 20, y, "会话状态", 11, P_FG, bold=True)
    _draw_close(d, PANEL_W - 28, y + 3)
    regions["close"] = (PANEL_W - 40, y - 6, PANEL_W - 8, y + 20)
    y += 22 + 8

    if not sess_items:
        _text(d, 20, y, "当前没有活跃会话", 9, P_DIM)

    for state, name, ago, ctx in sess_items:
        verb, color = SESSION_STATES.get(state, (state, P_DIM))
        _text(d, 20, y + 2, "●", 10, color)
        _text(d, 20 + 16, y + 2, verb, 10, P_FG)
        disp = name
        while disp and _text_w(disp + "…", 9) > 150:
            disp = disp[:-1]
        if disp != name:
            disp += "…"
        _text(d, 20 + 16 + 92, y + 3, disp, 9, P_DIM)
        _text(d, PANEL_W - 20, y + 3, ago, 9, P_DIM, anchor="ra")
        y += 30
        if ctx:
            ctx_text, frac = ctx
            _draw_bar(d, 20 + 16 + 92, y, frac, frac >= 0.8)
            _text(d, PANEL_W - 20, y - 2,
                  f"{min(round(frac * 100), 999)}% used", 9, P_DIM,
                  anchor="ra")
            _text(d, 20 + 16, y - 2, ctx_text, 8, P_DIM)
            y += 22
    return img, h, regions


# ---------- 主程序 ----------
if objc:
    class PetView(NSImageView):
        """桌宠视图: 手动拖动 / 双击开面板 / 右键菜单."""

        def acceptsFirstMouse_(self, event):
            return True

        def mouseDown_(self, event):
            self._drag0 = NSEvent.mouseLocation()
            f = self.window().frame()
            self._win0 = (f.origin.x, f.origin.y)

        def mouseDragged_(self, event):
            p = NSEvent.mouseLocation()
            self.window().setFrameOrigin_(
                (self._win0[0] + p.x - self._drag0.x,
                 self._win0[1] + p.y - self._drag0.y))

        def mouseUp_(self, event):
            app = self.window()._pet
            app.save_cfg()
            if event.clickCount() == 2:
                app.togglePopup_(None)

        def rightMouseDown_(self, event):
            app = self.window()._pet
            NSMenu.popUpContextMenu_withEvent_forView_(
                app.build_menu(), event, self)

    class PanelView(NSImageView):
        """面板视图: 点击区域分发 (close/refresh), Esc 关闭."""

        def acceptsFirstMouse_(self, event):
            return True

        def mouseDown_(self, event):
            pt = self.convertPoint_fromView_(event.locationInWindow(), None)
            h = self.window().frame().size.height
            x, y = pt.x, h - pt.y  # 转成面板图坐标 (左上原点)
            app = self.window()._pet
            for name, (x0, y0, x1, y1) in (self.window()._regions or {}).items():
                if x0 <= x <= x1 and y0 <= y <= y1:
                    app.panel_action(self.window(), name)
                    return

        def keyDown_(self, event):
            if event.keyCode() == 53:  # Esc
                self.window()._pet.close_panels()

    class KeyWindow(NSWindow):
        def canBecomeKeyWindow(self):
            return True

    class MacPet(NSObject):
        # ------ 初始化 ------
        def init(self):
            self = objc.super(MacPet, self).init()
            if self is None:
                return None
            self.cfg = {}
            try:
                with open(CFG_FILE, encoding="utf-8") as f:
                    self.cfg = json.load(f)
            except Exception:
                pass
            self.notify_alerts = self.cfg.get("notify_alerts", True)
            self.suite_on = self.cfg.get("suite_on", False)

            self._api_windows = None
            self._api_full = None
            self._api_ts = 0.0
            self._alerts = AlertTracker(notify)
            self._credits = CreditsTracker(notify)
            self._pct = PctHistory()
            self._tr_cache = {}
            self._err_cache = {}
            self._settings_1m_base = settings_1m_base()
            self._fetch_now = threading.Event()
            self._refreshing = False

            self.gifs = {name: load_frames(os.path.join(GIF_DIR, f"{name}.gif"))
                         for name in set(STATE_GIF.values())}
            self.display_state = "idle"
            self.frame_idx = 0

            self._make_pet_window()
            self._make_status_item()
            self.popup = None       # 用量面板 NSWindow
            self.sess_popup = None  # 会话面板
            self.fig_win = None     # 套件立绘
            self._monitors = []
            self._install_click_monitors()

            threading.Thread(target=self._api_loop, daemon=True).start()
            if not os.path.exists(os.path.join(BASE, ".git")):
                threading.Thread(target=self._update_loop, daemon=True).start()

            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                POLL_MS / 1000, self, "pollState:", None, True)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                15.0, self, "pollUsage:", None, True)
            self._animate()
            return self

        # ------ 桌宠窗口 ------
        def _make_pet_window(self):
            scr = NSScreen.mainScreen().visibleFrame()
            x = self.cfg.get("x", scr.origin.x + scr.size.width - PET_SIZE - 40)
            y = self.cfg.get("y", scr.origin.y + 40)
            win = KeyWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, PET_SIZE, PET_SIZE), BORDERLESS, BACKING, False)
            win.setOpaque_(False)
            win.setBackgroundColor_(NSColor.clearColor())
            win.setLevel_(LEVEL_STATUS)
            win.setCollectionBehavior_(BEHAVIOR)
            win.setHasShadow_(False)
            win._pet = self
            view = PetView.alloc().initWithFrame_(
                NSMakeRect(0, 0, PET_SIZE, PET_SIZE))
            view.setEditable_(False)
            win.setContentView_(view)
            win.orderFrontRegardless()
            self.pet_win, self.pet_view = win, view

        def save_cfg(self):
            f = self.pet_win.frame()
            self.cfg.update({"x": f.origin.x, "y": f.origin.y,
                             "notify_alerts": self.notify_alerts,
                             "suite_on": self.suite_on})
            try:
                with open(CFG_FILE, "w", encoding="utf-8") as fh:
                    json.dump(self.cfg, fh)
            except OSError:
                pass

        # ------ GIF 动画 (一次性定时器链, 支持逐帧时长) ------
        def _animate(self):
            frames = self.gifs[STATE_GIF[self.display_state]]
            img, duration = frames[self.frame_idx % len(frames)]
            self.pet_view.setImage_(img)
            self.frame_idx += 1
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                duration / 1000, self, "nextFrame:", None, False)

        def nextFrame_(self, timer):
            self._animate()

        def _set_display(self, state):
            if state != self.display_state:
                self.display_state = state
                self.frame_idx = 0

        # ------ 状态轮询 (与 Windows 壳同逻辑) ------
        def pollState_(self, timer):
            raw, ts, sid = "idle", 0.0, ""
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("state", "idle")
                ts = float(data.get("ts", 0))
                sid = data.get("session_id", "")
            except Exception:
                pass
            if raw not in STATE_GIF:
                raw = "idle"
            if raw in ("working", "thinking") and sid:
                path = read_sessions().get(sid, {}).get("transcript")
                err = last_api_error_ts(path, self._err_cache)
                if err and err > ts:
                    raw, ts = "error", err
            self._set_display(effective_state(raw, time.time() - ts))

        # ------ 用量轮询 / API 线程 ------
        def _api_loop(self):
            while True:
                try:
                    result = fetch_usage_api()
                    if result:
                        windows, full = result
                        self._api_windows, self._api_full = windows, full
                        self._api_ts = time.time()
                        self._alerts.check(full, windows, self.notify_alerts)
                        self._credits.track(full, self.notify_alerts)
                        self._pct.track(full)
                except Exception:
                    pass
                self._fetch_now.wait(timeout=USAGE_API_POLL_S)
                self._fetch_now.clear()

        def pollUsage_(self, timer):
            if self._api_windows and time.time() - self._api_ts < USAGE_API_FRESH_S:
                windows = self._api_windows
            else:
                windows = []
                try:
                    with open(USAGE_FILE, encoding="utf-8") as f:
                        windows = extract_usage(json.load(f))
                except Exception:
                    pass
            pcts = {label: pct for label, pct, _r in (windows or [])}
            self.status_item.button().setImage_(pil_to_nsimage(
                draw_badge(pcts.get("5h", 0), pcts.get("7d", 0)), 20, 20))
            self.refresh_panels()

        # ------ 菜单栏 ------
        def _make_status_item(self):
            bar = NSStatusBar.systemStatusBar()
            self.status_item = bar.statusItemWithLength_(-1)  # variable
            btn = self.status_item.button()
            btn.setImage_(pil_to_nsimage(draw_badge(0, 0), 20, 20))
            btn.setTarget_(self)
            btn.setAction_("statusClicked:")
            btn.sendActionOn_((1 << 2) | (1 << 4))  # LeftMouseUp | RightMouseUp

        def statusClicked_(self, sender):
            ev = NSApp().currentEvent()
            if ev and ev.type() == EV_RUP:
                self.status_item.popUpStatusItemMenu_(self.build_menu())
            else:
                subprocess.Popen(["open", "claude://new"])

        # ------ 右键菜单 (原生 NSMenu) ------
        def build_menu(self):
            menu = NSMenu.alloc().init()
            menu.setAutoenablesItems_(False)

            def add(m, title, action, state=None):
                it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, action, "")
                it.setTarget_(self)
                if state:
                    it.setState_(1)
                m.addItem_(it)
                return it

            add(menu, "Show App", "openClaude:")
            add(menu, "用量详情", "togglePopup:")
            add(menu, "会话状态", "toggleSessions:")
            menu.addItem_(NSMenuItem.separatorItem())
            adv = NSMenu.alloc().init()
            adv.setAutoenablesItems_(False)
            add(adv, "开启通知", "toggleNotify:", self.notify_alerts)
            add(adv, "切换主题", "toggleSuite:", self.suite_on)
            add(adv, "卸载", "uninstall:")
            adv_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "高级", None, "")
            menu.addItem_(adv_item)
            menu.setSubmenu_forItem_(adv, adv_item)
            menu.addItem_(NSMenuItem.separatorItem())
            add(menu, "检查更新", "manualUpdate:")
            add(menu, "退出", "quit:")
            return menu

        def openClaude_(self, sender):
            subprocess.Popen(["open", "claude://"])

        def toggleNotify_(self, sender):
            self.notify_alerts = not self.notify_alerts
            self.save_cfg()

        def toggleSuite_(self, sender):
            self.suite_on = not self.suite_on
            self.save_cfg()
            was_sessions = self.sess_popup is not None
            self.close_panels()
            if was_sessions:
                self.toggleSessions_(None)
            else:
                self.togglePopup_(None)

        def quit_(self, sender):
            self.save_cfg()
            AppHelper.stopEventLoop()
            os._exit(0)

        def uninstall_(self, sender):
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("是否不再成为蕾米埃尔的共犯？")
            img_path = os.path.join(ASSET_DIR, "uninstall.png")
            if os.path.exists(img_path):
                alert.setIcon_(NSImage.alloc().initWithContentsOfFile_(img_path))
            alert.addButtonWithTitle_("否")   # 第一个按钮 = 默认(挽留)
            alert.addButtonWithTitle_("是")
            NSApp().activateIgnoringOtherApps_(True)
            if alert.runModal() == 1001:      # NSAlertSecondButtonReturn
                script = os.path.join(BASE, "uninstall.sh")
                if os.path.exists(script):
                    subprocess.Popen(["bash", script])
                self.quit_(None)

        def manualUpdate_(self, sender):
            def run():
                try:
                    remote = remote_version()
                    local = local_version(BASE)
                    if not remote or remote == local:
                        notify(f"已是最新版本 v{local}")
                        return
                    download_and_apply(BASE)
                    subprocess.run([sys.executable,
                                    os.path.join(BASE, "install_hooks.py")],
                                   timeout=30)
                    notify(f"已更新到 v{remote}, 正在重启")
                    os.execv(sys.executable,
                             [sys.executable, os.path.join(BASE, __file__)])
                except Exception as e:
                    notify(f"检查更新失败: {e}")
            threading.Thread(target=run, daemon=True).start()

        def _update_loop(self):
            time.sleep(60)
            while True:
                try:
                    remote = remote_version()
                    if remote and remote != local_version(BASE):
                        download_and_apply(BASE)
                        subprocess.run(
                            [sys.executable,
                             os.path.join(BASE, "install_hooks.py")],
                            timeout=30)
                        notify(f"已更新到 v{remote}, 正在重启")
                        os.execv(sys.executable, [sys.executable,
                                 os.path.join(BASE, "claude_pet_mac.py")])
                except Exception:
                    pass
                time.sleep(UPDATE_CHECK_S)

        # ------ 面板 ------
        def togglePopup_(self, sender):
            if self.popup:
                self.close_panels()
            else:
                self.close_panels()
                self.popup = self._show_panel(*self._usage_render())

        def toggleSessions_(self, sender):
            if self.sess_popup:
                self.close_panels()
            else:
                self.close_panels()
                self.sess_popup = self._show_panel(*self._sessions_render())

        def _usage_render(self):
            return render_usage_panel(self._api_full, self._api_ts,
                                      self._credits.today_delta,
                                      self._pct.projection, self.suite_on,
                                      self._refreshing)

        def _sessions_render(self):
            items = []
            sess = read_sessions()
            ordered = sorted(sess.items(), key=lambda kv: kv[1].get("ts", 0),
                             reverse=True)
            for sid, info in ordered[:12]:
                state = info.get("state", "idle")
                if state in ("working", "thinking"):
                    err = last_api_error_ts(info.get("transcript"),
                                            self._err_cache)
                    if err and err > info.get("ts", 0):
                        state = "error"
                cwd = info.get("cwd") or ""
                tail = "/".join(cwd.rstrip("/").split("/")[-2:]) or sid[:8]
                title, ctx = transcript_info(info.get("transcript"),
                                             self._tr_cache,
                                             self._settings_1m_base)
                items.append((state, title or tail,
                              fmt_ago(info.get("ts")), ctx))
            return render_sessions_panel(items, self.suite_on)

        def _show_panel(self, img, h, regions):
            x, y = self._panel_pos(h)
            win = KeyWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, PANEL_W, h), BORDERLESS, BACKING, False)
            win.setOpaque_(False)
            win.setBackgroundColor_(NSColor.clearColor())
            win.setLevel_(LEVEL_STATUS)
            win.setCollectionBehavior_(BEHAVIOR)
            win.setHasShadow_(True)
            win._pet = self
            win._regions = regions
            view = PanelView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, h))
            view.setEditable_(False)
            view.setImage_(pil_to_nsimage(img, PANEL_W, h))
            win.setContentView_(view)
            NSApp().activateIgnoringOtherApps_(True)
            win.makeKeyAndOrderFront_(None)
            self._place_fig(win, h)
            return win

        def _panel_pos(self, h):
            """面板放桌宠上方 (Cocoa 原点在左下, 上方 = y 更大), 放不下换下方."""
            pf = self.pet_win.frame()
            scr = (self.pet_win.screen() or NSScreen.mainScreen()).visibleFrame()
            x = min(max(scr.origin.x + 8, pf.origin.x + pf.size.width - PANEL_W),
                    scr.origin.x + scr.size.width - PANEL_W - 8)
            y = pf.origin.y + pf.size.height + 8
            if y + h > scr.origin.y + scr.size.height - 8:
                y = pf.origin.y - h - 8
            y = max(scr.origin.y + 8, y)
            return x, y

        def _place_fig(self, panel, panel_h):
            path = os.path.join(ASSET_DIR, "panel_banner.png")
            if not (self.suite_on and os.path.exists(path)):
                return
            img = Image.open(path).convert("RGBA")
            fw, fh = img.size
            pf = panel.frame()
            x = pf.origin.x + (PANEL_W - fw) / 2
            y = pf.origin.y + panel_h - FIG_INSET  # 立绘底边压进面板顶部
            fig = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, fw, fh), BORDERLESS, BACKING, False)
            fig.setOpaque_(False)
            fig.setBackgroundColor_(NSColor.clearColor())
            fig.setLevel_(LEVEL_STATUS)
            fig.setCollectionBehavior_(BEHAVIOR)
            fig.setHasShadow_(False)
            fig.setIgnoresMouseEvents_(True)
            view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, fw, fh))
            view.setEditable_(False)
            view.setImage_(pil_to_nsimage(img, fw, fh))
            fig.setContentView_(view)
            fig.orderWindow_relativeTo_(1, panel.windowNumber())  # Above
            self.fig_win = fig

        def close_panels(self):
            for attr in ("popup", "sess_popup", "fig_win"):
                win = getattr(self, attr)
                if win:
                    win.orderOut_(None)
                    setattr(self, attr, None)

        def refresh_panels(self):
            if self.popup:
                img, h, regions = self._usage_render()
                self._relayout(self.popup, img, h, regions)
            if self.sess_popup:
                img, h, regions = self._sessions_render()
                self._relayout(self.sess_popup, img, h, regions)

        def _relayout(self, win, img, h, regions):
            x, y = self._panel_pos(h)
            win.setFrame_display_(NSMakeRect(x, y, PANEL_W, h), True)
            win._regions = regions
            win.contentView().setFrame_(NSMakeRect(0, 0, PANEL_W, h))
            win.contentView().setImage_(pil_to_nsimage(img, PANEL_W, h))
            if self.fig_win:
                self.fig_win.orderOut_(None)
                self.fig_win = None
            self._place_fig(win, h)

        def panel_action(self, win, name):
            if name == "close":
                self.close_panels()
            elif name == "refresh":
                self._refreshing = True
                self._refresh_ts0 = self._api_ts
                self._refresh_n = 0
                self._fetch_now.set()
                self.refresh_panels()
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.5, self, "refreshPoll:", None, True)

        def refreshPoll_(self, timer):
            self._refresh_n += 1
            if not self.popup:
                self._refreshing = False
                timer.invalidate()
                return
            if self._api_ts != self._refresh_ts0 or self._refresh_n >= 40:
                self._refreshing = False
                timer.invalidate()
                self.refresh_panels()

        # ------ 点外关闭 ------
        def _install_click_monitors(self):
            mask = MASK_LDOWN | MASK_RDOWN

            def outside(_event):
                self._check_outside_click()
                return _event

            self._monitors.append(
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    mask, lambda e: self._check_outside_click()))
            self._monitors.append(
                NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    mask, outside))

        def _check_outside_click(self):
            if not (self.popup or self.sess_popup):
                return
            p = NSEvent.mouseLocation()

            def inside(win):
                if not win:
                    return False
                f = win.frame()
                return (f.origin.x <= p.x <= f.origin.x + f.size.width
                        and f.origin.y <= p.y <= f.origin.y + f.size.height)

            if not any(inside(w) for w in
                       (self.popup, self.sess_popup, self.fig_win,
                        self.pet_win)):
                self.close_panels()


def run_diagnostics():
    print("== ClaudePetLeiMi (macOS) diagnostics ==")
    oauth = read_oauth()
    if oauth:
        exp = (oauth.get("expiresAt") or 0) / 1000
        src = ("file" if os.path.exists(CRED_FILE) else "Keychain")
        print(f"[1] credentials: {src} | subscriptionType="
              f"{oauth.get('subscriptionType')} | "
              f"{'valid' if exp > time.time() else 'EXPIRED'}"
              f" ({(exp - time.time()) / 3600:.1f}h left)")
    else:
        print("[1] credentials NOT FOUND (file 或 Keychain 都没有)")
        print("    -> 该机器没登录 Claude Code, 或 Keychain 访问被拒")
    try:
        result = fetch_usage_api()
        print("[2] usage API:", "OK ->" if result else "SKIPPED (token 过期)",
              result[0] if result else "")
    except Exception as e:
        print(f"[2] usage API FAILED: {type(e).__name__}: {e}")
    print(f"[3] statusline dump: "
          f"{'found' if os.path.exists(USAGE_FILE) else 'NOT FOUND'}")
    print(f"[4] hook state files:"
          f" state={'yes' if os.path.exists(STATE_FILE) else 'NO'}"
          f" | sessions={'yes' if os.path.exists(SESS_FILE) else 'NO'}")
    print("[5] deps: pyobjc", "OK" if objc else "MISSING",
          "| PIL OK", "| fonts:",
          next((p for p, _ in _FONT_CANDIDATES if os.path.exists(p)),
               "NONE (面板中文会变方块)"))
    for name in set(STATE_GIF.values()):
        p = os.path.join(GIF_DIR, f"{name}.gif")
        if not os.path.exists(p):
            print(f"[6] MISSING gif: {p}")


def main():
    if "--diag" in sys.argv:
        run_diagnostics()
        return
    if not acquire_singleton():
        sys.stderr.write("另一个实例仍持有锁, 退出\n")
        sys.exit(1)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(ACCESSORY)
    pet = MacPet.alloc().init()
    global _PET
    _PET = pet  # 持引用防 GC
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
