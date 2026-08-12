"""petcore.transcript — 会话 transcript 解析: API 错误探测 / 标题 / context 用量."""
import json
import os

from .common import SESS_FILE, parse_reset


def read_sessions():
    try:
        with open(SESS_FILE, encoding="utf-8") as f:
            sess = json.load(f)
        if isinstance(sess, dict):
            return sess
    except Exception:
        pass
    return {}


def last_api_error_ts(path, cache):
    """transcript 最后一条主链记录若是 API 错误, 返回其 epoch, 否则 None.

    hooks 对 API 报错/自动重试 (ECONNRESET / spend limit 等) 无事件,
    只能从 transcript 尾部补判: isApiErrorMessage 记录即 CLI 里那行红字.
    cache 是调用方持有的 {path: (size, err_ts)}, 文件没长就不重读.
    """
    if not path:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    cached = cache.get(path)
    if cached and cached[0] == size:
        return cached[1]
    err_ts = None
    try:
        with open(path, "rb") as f:
            if size > 65536:
                f.seek(size - 65536)
            tail = f.read().decode("utf-8", "ignore")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                break  # 尾行被截断/超长, 本轮放弃判断
            # system/turn_duration/permission-mode 等元数据会跟在错误
            # 记录后面, 不算恢复活动, 跳过继续往前找
            if (rec.get("isSidechain")
                    or rec.get("type") not in ("user", "assistant")):
                continue
            if rec.get("isApiErrorMessage"):
                err_ts = parse_reset(rec.get("timestamp"))
            break  # 只认最后一条主链 user/assistant 记录
    except OSError:
        pass
    cache[path] = (size, err_ts)
    return err_ts


def transcript_info(path, cache, settings_1m_base=""):
    """从 transcript 提取 (会话标题, (context文本, 占比) 或 None). 按文件大小缓存.

    标题 = ai-title 记录的 aiTitle; context = 最后一条 assistant 消息
    usage 的 input+cache_read+cache_creation, 窗口按模型名 [1m] 判 1M/200k.
    cache 是调用方持有的 {path: (size, title, ctx_text)}.
    """
    if not path:
        return None, None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, None
    cached = cache.get(path)
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
                     or (settings_1m_base
                         and model.startswith(settings_1m_base)))
            win_size = 1_000_000 if is_1m else 200_000
            win_label = "1M" if is_1m else "200k"
            frac = ctx / win_size
            ctx_text = (f"{ctx / 1000:.0f}k / {win_label}", frac)
    except OSError:
        pass
    cache[path] = (size, title, ctx_text)
    return title, ctx_text


def settings_1m_base():
    """settings.json 的 model 若带 [1m], 返回其基础名用于 1M 窗口判定."""
    try:
        from .common import CLAUDE_DIR
        with open(os.path.join(CLAUDE_DIR, "settings.json"),
                  encoding="utf-8") as f:
            model_cfg = json.load(f).get("model") or ""
        if "[1m]" in model_cfg:
            return model_cfg.split("[")[0]
    except Exception:
        pass
    return ""
