"""把 ClaudePetLeiMi 的 hooks / statusLine 合并进 ~/.claude/settings.json.

幂等: 重复运行会先移除旧的 cc_pet_hook 条目(路径可能不同)再写入当前路径.
不破坏用户已有的其他 hooks; 已有自定义 statusLine 时不覆盖.
用法: python install_hooks.py [settings.json 路径(默认 ~/.claude/settings.json)]
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(BASE, "cc_pet_hook.py")
STATUSLINE = os.path.join(BASE, "cc_statusline.py")

EVENTS = {
    "UserPromptSubmit": "prompt",
    "PreToolUse": "pretool",
    "PostToolUse": "posttool",
    "PostToolUseFailure": "posttoolfail",
    "Notification": "notify",
    "Stop": "stop",
    "SessionStart": "sessionstart",
    "SessionEnd": "sessionend",
}


def is_pet_hook(h):
    blob = str(h.get("command", "")) + " ".join(map(str, h.get("args") or []))
    return "cc_pet_hook.py" in blob


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, ValueError):
        cfg = {}

    hooks = cfg.setdefault("hooks", {})
    for event, arg in EVENTS.items():
        groups = hooks.setdefault(event, [])
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not is_pet_hook(h)]
        groups[:] = [g for g in groups if g.get("hooks")]
        groups.append({"hooks": [{
            "type": "command",
            "command": "python",
            "args": [HOOK, arg],
            "async": True,
            "timeout": 10,
        }]})

    sl = cfg.get("statusLine") or {}
    if not sl or "cc_statusline.py" in str(sl.get("command", "")):
        cfg["statusLine"] = {
            "type": "command",
            "command": f'python "{STATUSLINE}"',
            "refreshInterval": 60,
        }
        print("statusLine 已配置")
    else:
        print("检测到已有自定义 statusLine, 未覆盖 (如需用量兜底可手动整合 cc_statusline.py)")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print("hooks 已写入:", path)


if __name__ == "__main__":
    main()
