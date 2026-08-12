"""petcore.trackers — 告警 / 消费监控 / 限额采样预测三个状态机.

全部与 UI 解耦: 通知走构造时传入的 notify(message, title) 回调,
是否弹通知由调用方传 enabled 控制.
"""
import json
import os
import time

from .common import (CREDITS_FILE, PCT_HIST_FILE, atomic_write, extra_amount,
                     fmt_remain)
from .usage import limit_rows


class AlertTracker:
    """阈值提醒: percent 即各限额自身占比, 首次越过 80% 后每 +5% 通知一次.

    名称与面板同源 (limit_rows: Current session / All models / Fable...),
    没有完整响应时退回 windows 的 5h/7d 标签.
    窗口是否重置按 percent 回落判断 (resets_at 有秒级抖动, 按它对账
    会把已告警档位清零导致重复通知).
    """

    def __init__(self, notify):
        self._notify = notify
        self._state = {}

    def check(self, full, windows, enabled=True):
        if full:
            items = [(name, pct, reset) for name, pct, reset, _d, _g, _a
                     in limit_rows(full)]
        else:
            items = [(label, pct, reset) for label, pct, reset in windows]
        for label, pct, reset in items:
            prev_pct, prev_level = self._state.get(label, (None, 0))
            if prev_pct is not None and pct < prev_pct - 0.5:
                prev_level = 0  # 限额已重置, 重新武装告警
            # 越过 80% 告警线后, 每 +5% 通知一次 (80/85/90/95/100)
            level = int(pct // 5) * 5 if pct >= 80 else 0
            if level > prev_level and enabled:
                self._notify(
                    f"用量告急，天才程序员即将陨落！\n"
                    f"{label} 已用 {round(pct)}%，"
                    f"{fmt_remain(reset)}后重置", "Claude 用量提醒")
            self._state[label] = (pct, max(level, prev_level))


class CreditsTracker:
    """消费监控: used_credits 变化落盘历史; 检测到增长(=正在按量计费)
    立即弹扣费警告, 之后每多烧 $5 再提醒一次."""

    def __init__(self, notify):
        self._notify = notify
        self._alerted = None
        try:
            with open(CREDITS_FILE, encoding="utf-8") as f:
                self.hist = json.load(f)
            if not isinstance(self.hist, list):
                self.hist = []
        except Exception:
            self.hist = []

    def track(self, full, enabled=True):
        amount = extra_amount(full.get("extra_usage") or {})
        if amount is None:
            return
        hist = self.hist
        last = hist[-1][1] if hist else None
        if last is not None and abs(amount - last) < 0.005:
            return
        hist.append([time.time(), round(amount, 2)])
        cutoff = time.time() - 90 * 86400
        while len(hist) > 2000 or (hist and hist[0][0] < cutoff):
            hist.pop(0)
        try:
            atomic_write(CREDITS_FILE, hist)
        except OSError:
            pass
        if (last is not None and amount > last and enabled
                and (self._alerted is None or amount - self._alerted >= 5)):
            self._alerted = amount
            today = self.today_delta(amount)
            msg = f"Extra usage 正在计费！累计 ${amount:.2f}"
            if today >= 0.01:
                msg += f"，今日 +${today:.2f}"
            self._notify(msg, "Claude 扣费警告")

    def today_delta(self, current):
        """今日新增消费 = 当前值 - 今日零点前最后一条记录 (无则用最早记录)."""
        if not self.hist:
            return 0.0
        from datetime import datetime
        midnight = datetime.now().replace(hour=0, minute=0, second=0,
                                          microsecond=0).timestamp()
        baseline = None
        for ts, val in self.hist:
            if ts < midnight:
                baseline = val
            else:
                break
        if baseline is None:
            baseline = self.hist[0][1]
        return max(0.0, current - baseline)


class PctHistory:
    """限额 percent 采样 + 燃烧速率外推 (参考 Claude-Code-Usage-Monitor 口径)."""

    def __init__(self):
        try:
            with open(PCT_HIST_FILE, encoding="utf-8") as f:
                self.hist = json.load(f)
            if not isinstance(self.hist, dict):
                self.hist = {}
        except Exception:
            self.hist = {}

    def track(self, full):
        """记录各限额 percent 采样. 限额重置 (pct 回落) 时该行旧样本作废;
        采样最密 1 条/分钟, 只留 6 小时."""
        now = time.time()
        hist = self.hist
        seen = set()
        for name, pct, _reset, _dur, _grp, _act in limit_rows(full):
            seen.add(name)
            rows = hist.setdefault(name, [])
            if rows and pct < rows[-1][1] - 0.5:
                rows.clear()
            if not rows or now - rows[-1][0] >= 60:
                rows.append([round(now), round(pct, 2)])
            cutoff = now - 6 * 3600
            while rows and rows[0][0] < cutoff:
                rows.pop(0)
        for k in [k for k in hist if k not in seen]:
            del hist[k]
        try:
            atomic_write(PCT_HIST_FILE, hist)
        except OSError:
            pass

    def predict_depletion(self, name, pct):
        """按最近一小时消耗速率外推撞线 (100%) 时刻的 epoch.

        样本跨度不足 10 分钟或近一小时几乎没消耗时不预测, 返回 None.
        """
        rows = self.hist.get(name) or []
        now = time.time()
        recent = [r for r in rows if r[0] >= now - 3600]
        if len(recent) < 2:
            return None
        span = recent[-1][0] - recent[0][0]
        burned = recent[-1][1] - recent[0][1]
        if span < 600 or burned <= 0.1 or pct >= 100:
            return None
        return now + (100 - pct) / (burned / span)

    def projection(self, rows):
        """预测行数据: (限额名, 撞线epoch, 是否早于重置). 无可预测限额返回 None.

        优先报会撞线的限额里最早的一个; 都撑得到重置则报最紧的那个.
        """
        preds = []
        for name, pct, reset, _dur, _grp, _act in rows:
            hit = self.predict_depletion(name, pct)
            if hit:
                preds.append((hit, name, bool(reset and hit < reset)))
        if not preds:
            return None
        danger = [p for p in preds if p[2]]
        hit, name, before = min(danger or preds)
        return name, hit, before
