"""petcore — ClaudePetLeiMi 双端 (Windows/macOS) 共享核心.

用量接口 / transcript 解析 / 告警与预测状态机 / 格式化 / 徽章绘制.
窗口、托盘、通知等平台相关实现在各壳 (claude_pet.pyw / claude_pet_mac.py).
"""
from .common import (CLAUDE_DIR, CRED_FILE, CREDITS_FILE, DONE_HOLD_S,
                     IDLE_TO_STANDBY_S, PCT_HIST_FILE, PET_SIZE, POLL_MS,
                     SESS_FILE, SESS_REG_DIR, SESSION_STATES, STALE_S,
                     STATE_FILE, STATE_GIF, USAGE_API, USAGE_API_FRESH_S,
                     USAGE_API_POLL_S, USAGE_FILE, atomic_write,
                     effective_state, extra_amount, fmt_ago, fmt_remain,
                     fmt_resets_in, fmt_resets_weekday, fmt_when,
                     parse_reset, usage_color)
from .badge import draw_badge
from .trackers import AlertTracker, CreditsTracker, PctHistory
from .transcript import (last_api_error_ts, read_sessions, settings_1m_base,
                         transcript_info)
from .update import (UPDATE_CHECK_S, download_and_apply, local_version,
                     remote_version)
from .usage import extract_usage, fetch_usage_api, limit_rows, read_oauth
