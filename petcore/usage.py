"""petcore.usage — 用量接口: 凭证读取 / OAuth 用量拉取 / 限额行解析."""
import json
import sys
import time
import urllib.request

from .common import CLAUDE_UA, CRED_FILE, USAGE_API, parse_reset


def read_oauth():
    """读 Claude Code 登录凭证 -> claudeAiOauth dict.

    Windows/Linux 在 ~/.claude/.credentials.json; macOS 默认存 Keychain
    (service = "Claude Code-credentials"), 文件不存在时走 security 命令.
    """
    try:
        with open(CRED_FILE, encoding="utf-8") as f:
            cred = json.load(f)
        return cred.get("claudeAiOauth") or cred
    except (OSError, ValueError):
        pass
    if sys.platform == "darwin":
        import subprocess
        try:
            out = subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                cred = json.loads(out.stdout)
                return cred.get("claudeAiOauth") or cred
        except Exception:
            pass
    return None


def fetch_usage_api():
    """直查 Anthropic OAuth 用量接口 (同 /usage 命令数据源).

    token 过期就放弃 (Claude Code 跑起来会自己刷新), 调用方降级用
    statusline 文件. 返回 ([(标签, pct, reset_epoch), ...], 完整响应) 或 None.
    """
    oauth = read_oauth()
    if not oauth:
        return None
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
                        parse_reset(item.get("resets_at"))))
    if not out:
        return None
    return out, data


def limit_rows(data):
    """从 API 完整响应提取详情面板行: [(名称, pct, reset_epoch, 时长秒, 分组, 激活), ...]."""
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
        # percent 已按该限额自身配额归一化为 0-100 (实测 Fable 70% 时
        # weekly_all 才 36%, 若是全局口径不可能倒挂), 不做任何折算
        rows.append((name, float(lim["percent"]),
                     parse_reset(lim.get("resets_at")), duration, group,
                     bool(lim.get("is_active"))))
    return rows


def extract_usage(data):
    """从 statusline JSON 提取 [(标签, 百分比0-100, 重置epoch), ...] (API 失效兜底)."""
    rl = data.get("rate_limits") or {}
    out = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        item = rl.get(key)
        if isinstance(item, dict) and item.get("used_percentage") is not None:
            out.append((label, float(item["used_percentage"]),
                        parse_reset(item.get("resets_at"))))
    return out
