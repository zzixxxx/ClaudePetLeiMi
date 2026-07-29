"""Claude Code statusLine 脚本.

Claude Code 把会话/用量 JSON 从 stdin 喂进来:
1. 原样存到 ~/.claude/cc-pet-usage.json (给桌宠读)
2. 打印一行紧凑状态栏 (显示在 CLI 底部)
"""
import json
import os
import sys
import time

USAGE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "cc-pet-usage.json")


def fmt_reset(ts):
    """把重置时间戳(秒)转成 '1h23m' 形式的剩余时长."""
    if not ts:
        return "?"
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


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception:
        data = {}
    data["_saved_at"] = time.time()
    tmp = USAGE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, USAGE_FILE)
    except OSError:
        pass

    parts = []
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(model)
    rl = data.get("rate_limits") or {}
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        item = rl.get(key)
        if isinstance(item, dict) and item.get("used_percentage") is not None:
            seg = f"{label} {round(float(item['used_percentage']))}%"
            reset = item.get("resets_at")
            if reset:
                seg += f" ↺{fmt_reset(reset)}"
            parts.append(seg)
    print(" | ".join(parts) if parts else "...")


if __name__ == "__main__":
    main()
