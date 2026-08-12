"""petcore.common — 双端共享的路径 / 常量 / 格式化 / 状态机.

Windows 壳 (claude_pet.pyw) 与 macOS 壳 (claude_pet_mac.py) 都从这里取,
平台相关的东西 (窗口/托盘/通知) 一律不进这个包.
"""
import json
import os

CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
STATE_FILE = os.path.join(CLAUDE_DIR, "cc-pet-state.json")
SESS_FILE = os.path.join(CLAUDE_DIR, "cc-pet-sessions.json")
SESS_REG_DIR = os.path.join(CLAUDE_DIR, "sessions")
USAGE_FILE = os.path.join(CLAUDE_DIR, "cc-pet-usage.json")
CRED_FILE = os.path.join(CLAUDE_DIR, ".credentials.json")
CREDITS_FILE = os.path.join(CLAUDE_DIR, "cc-pet-credits.json")   # 消费历史 [[ts, 美元], ...]
PCT_HIST_FILE = os.path.join(CLAUDE_DIR, "cc-pet-usage-hist.json")  # 限额采样 {名称: [[ts, pct], ...]}

USAGE_API = "https://api.anthropic.com/api/oauth/usage"
USAGE_API_POLL_S = 180      # 接口限流较狠, 官方 UA + 180s 是安全间隔
USAGE_API_FRESH_S = 600     # API 数据 10 分钟内算新鲜, 否则退回 statusline 文件
CLAUDE_UA = "claude-code/2.1.220"

PET_SIZE = 200                 # 桌宠显示尺寸(像素)
POLL_MS = 500                  # 状态文件轮询间隔
DONE_HOLD_S = 10               # done 展示时长, 之后转 idle
IDLE_TO_STANDBY_S = 180        # idle 超 3 分钟 -> standby(03)
STALE_S = 15 * 60              # 状态文件太久没更新视为会话已死 -> idle

# 会话状态 -> (Claude Code 风格动词, 颜色点)
SESSION_STATES = {
    "working": ("Doodling…", "#2760cf"),
    "thinking": ("Pondering…", "#7c5cd6"),
    "waiting": ("Waiting…", "#d29922"),
    "done": ("Done", "#3fb950"),
    "error": ("Error", "#e5484d"),
    "idle": ("Idle", "#9a9aa5"),
}

STATE_GIF = {
    "working": "02",
    "thinking": "01",
    "done": "03",
    "waiting": "04",
    "error": "05",
    "idle": "06",
    "standby": "03",
}


def effective_state(raw, age):
    """hook 原始状态 + 距最后写入秒数 -> 实际展示状态."""
    if raw == "done":
        if age < DONE_HOLD_S:
            return "done"
        raw, age = "idle", age - DONE_HOLD_S
    if raw in ("working", "thinking", "waiting", "error") and age > STALE_S:
        raw, age = "idle", age - STALE_S
    if raw == "idle" and age > IDLE_TO_STANDBY_S:
        return "standby"
    return raw


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def parse_reset(v):
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


def extra_amount(extra):
    """extra_usage -> 美元金额. used_credits 是最小货币单位, 按 decimal_places 换算."""
    if not extra or extra.get("used_credits") is None:
        return None
    dp = extra.get("decimal_places")
    return float(extra["used_credits"]) / (10 ** dp if dp else 1)


def fmt_remain(ts):
    import time
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
    import time
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


def fmt_when(ts):
    """预测时刻措辞: 当天 '6:30 PM', 跨天 'Tue 6:30 PM'."""
    from datetime import datetime
    dt = datetime.fromtimestamp(ts)
    hour12 = dt.hour % 12 or 12
    clock = f"{hour12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"
    if dt.date() == datetime.now().date():
        return clock
    return f"{dt:%a} {clock}"


def fmt_ago(ts):
    """'<1 min ago' / 'x min ago' / 'x hr ago'."""
    import time
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
