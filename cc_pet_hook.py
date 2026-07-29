"""Claude Code hook -> 写桌宠状态文件.

用法: python cc_pet_hook.py <event>
event: prompt | pretool | posttool | posttoolfail | notify | stop | sessionstart | sessionend
Claude Code 会把事件 JSON 从 stdin 传进来.
状态文件: ~/.claude/cc-pet-state.json  {"state": ..., "ts": ..., "session_id": ...}
sessionstart 时若桌宠没在跑会自动拉起.
"""
import ctypes
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "cc-pet-state.json")
SESS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "cc-pet-sessions.json")
PID_FILE = os.path.join(BASE, "pet.pid")
PET_SCRIPT = os.path.join(BASE, "claude_pet.pyw")
SESS_MAX_AGE_S = 4 * 3600  # 超 4 小时没动静的会话视为已死, 从列表剔除


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def update_sessions(event, state, data):
    """按 session_id 记录每个会话的状态, 供桌宠"会话状态"面板展示."""
    sid = data.get("session_id") or ""
    if not sid:
        return
    try:
        with open(SESS_FILE, encoding="utf-8") as f:
            sess = json.load(f)
        if not isinstance(sess, dict):
            sess = {}
    except Exception:
        sess = {}
    now = time.time()
    if event == "sessionend":
        sess.pop(sid, None)
    else:
        # 会话名/上下文用量由桌宠从 transcript 读 (ai-title / usage), 这里存路径
        sess[sid] = {"state": state, "ts": now, "cwd": data.get("cwd", ""),
                     "transcript": data.get("transcript_path", "")}
    sess = {k: v for k, v in sess.items()
            if now - v.get("ts", 0) < SESS_MAX_AGE_S}
    try:
        atomic_write(SESS_FILE, sess)
    except OSError:
        pass


def pet_is_running():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    alive = False
    buf = ctypes.create_unicode_buffer(512)
    size = ctypes.c_ulong(512)
    if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
        alive = "python" in buf.value.lower()
    kernel32.CloseHandle(h)
    return alive


def ensure_pet_running():
    if pet_is_running():
        return
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [pythonw, PET_SCRIPT],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        cwd=BASE,
    )


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception:
        data = {}

    state = None
    if event == "prompt":
        state = "thinking"
    elif event == "pretool":
        # AskUserQuestion = 在等用户回答
        state = "waiting" if data.get("tool_name") == "AskUserQuestion" else "working"
    elif event == "posttool":
        state = "thinking"
    elif event == "posttoolfail":
        state = "error"
    elif event == "notify":
        # bypassPermissions 下权限通知很少见, 只把权限类通知当 waiting, 其余忽略
        msg = str(data.get("message", "")).lower()
        if "permission" in msg or "授权" in msg:
            state = "waiting"
    elif event == "stop":
        state = "done"
    elif event in ("session", "sessionstart", "sessionend"):
        state = "idle"
        if event in ("session", "sessionstart"):
            try:
                ensure_pet_running()
            except Exception:
                pass

    if not state:
        return

    payload = {
        "state": state,
        "ts": time.time(),
        "session_id": data.get("session_id", ""),
    }
    try:
        atomic_write(STATE_FILE, payload)
    except OSError:
        pass
    update_sessions(event, state, data)


if __name__ == "__main__":
    main()
