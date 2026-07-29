# ClaudePetLeiMi — Claude Code 状态桌宠

透明置顶小窗口，根据 Claude Code 实时工作状态播放对应 GIF。

## 一键安装

前提：Windows 10/11 + [Python 3.10+](https://www.python.org/downloads/)（安装时勾选 Add to PATH）+ Claude Code。

PowerShell 里执行：

```powershell
irm https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/install.ps1 | iex
```

脚本会自动：下载最新代码到 `%LOCALAPPDATA%\ClaudePetLeiMi` → 安装 Pillow/pystray →
把 hooks 与 statusLine **合并**进 `~/.claude/settings.json`（幂等，不影响你已有的
hooks；已有自定义 statusLine 时不覆盖）→ 创建开机自启与桌面快捷方式 → 启动桌宠。
重复运行即为升级。正在运行的 Claude Code 会话需重启后才会驱动桌宠。

卸载：删除安装目录和两个 `ClaudePetLeiMi.lnk` 快捷方式，从 `~/.claude/settings.json`
移除含 `cc_pet_hook.py` 的 hooks 条目与 statusLine。

> 为什么不提供 exe：hooks 在 Claude Code 每个事件时都要拉起一次，PyInstaller exe
> 冷启动 1~2s 会拖慢每次工具调用（`python xx.py` 只要几十毫秒），且单文件 exe
> 易被杀软误报。Python 是本工具的硬依赖。

## 状态机与 GIF 映射

| 状态 | GIF | 触发 |
|---|---|---|
| working 干活中 | 01 蚊香眼奋笔疾书 | PreToolUse |
| thinking 思考中 | 02 晕转思考 | UserPromptSubmit / PostToolUse |
| done 完成 | 03 举本子炫耀 | Stop（展示 10 秒后转 idle）|
| waiting 等你 | 04 停笔等待 | AskUserQuestion / 权限通知 |
| error 出错 | 05 诶? | PostToolUseFailure |
| idle 待机 | 06 抱本子 | SessionStart/End；idle 超 3 分钟切回 03 待机脸 |

## 架构

- `~/.claude/settings.json` 里的 hooks（async）在每个事件时运行 `cc_pet_hook.py <event>`，
  原子写 `~/.claude/cc-pet-state.json`。
- `claude_pet.pyw`（tkinter + Pillow）每 500ms 轮询状态文件切换动画。
- 状态文件超 15 分钟没更新视为会话已死，回 idle。
- 多个 Claude Code 会话并发时后写者覆盖（last-writer-wins）。
- SessionStart hook 检测到桌宠没在跑会自动拉起（开新会话桌宠自动出现）。

## 用量监控（5h / 7d 窗口）

数据源两级：

1. **API 直查（主）**：后台线程每 180s 用本地 Claude Code 凭证（`~/.claude/.credentials.json`
   的 accessToken）GET `https://api.anthropic.com/api/oauth/usage`（即 `/usage` 命令的数据源），
   头必须带 `anthropic-beta: oauth-2025-04-20` + `User-Agent: claude-code/x.y.z`（否则 429）。
   token 过期就跳过（Claude Code 运行时会自己刷新 token）。
2. **statusline 落盘（兜底）**：`cc_statusline.py` 注册为 statusLine，CLI 底部显示用量，
   同时把 `rate_limits` JSON 落盘 `~/.claude/cc-pet-usage.json`。API 数据超 10 分钟没刷新时用它。

展示两处（15s 刷新）：

- **托盘徽章**（pystray，单图标）：上半 5h、下半 7d，数字=百分比，
  底色绿(<50)黄(<80)红(≥80)，悬停显示重置倒计时。Win10 默认收进 `^` 溢出区，可拖出常驻。
- **详情面板**（功能参考 jens-duttke/usage-monitor-for-claude，UI 复刻 Claude Desktop
  设置里的 Usage 页）：左键托盘图标 / 双击桌宠 / 右键菜单"用量详情"打开，
  **锚定在桌宠正上方（不遮挡桌宠，顶部放不下自动挪下方/侧面）**。
  白底卡片：标题 `Your usage limits <订阅类型>`；行布局=左侧"标签+重置时间"两行块、
  中间圆角进度条（浅蓝轨道 #d7e4f9 / 深蓝填充 #2760cf，
  **消耗比例超过窗口已过时间比例时变红**）、右侧灰色 `x% used`；
  会话行（`Resets in x hr x min`）与 `Weekly limits` 分节（`Resets Tue 9:00 PM` 格式）；
  `Usage credits $x.xx spent` 行；底部 `Last updated: ... ⟳`，点 ⟳ 立即重新抓取 API。
  数据行覆盖 API `limits` 全部配额。打开时随 15s 轮询自动刷新，Esc/✕ 关闭。
- **阈值提醒**：5h/7d 首次越过 80% / 95% 时弹 Windows 通知，窗口重置后重新计。

（曾有桌宠 GIF 下用量条和任务栏小条两个展示，与面板冗余，已移除。）

## 会话状态面板

- 右键菜单"会话状态"打开：每行 `彩色状态点 + Claude Code 风格动词 + 会话名 + x min ago`。
  动词映射：working=Doodling… / thinking=Pondering… / waiting=Waiting… /
  done=Done / error=Error / idle=Idle。
- 会话名 = transcript 里的 `ai-title` 记录（Claude Code 自动生成的话题标题，如
  "Create Claude Code status monitor desktop widget"），取不到回退 cwd 尾部。
- 每行下方独立一行 context window 进度条 + `413k / 1M (41%)` 文本：
  取 transcript 最后一条主链 assistant 消息 usage 的 input+cache_read+cache_creation；
  窗口大小按 settings.json 模型是否带 [1m]（transcript 里模型 ID 不带该后缀）+
  用量>200k 兜底判 1M/200k；≥85% 进度条变红。按文件大小缓存，只在变化时重扫。
- 数据在 `~/.claude/cc-pet-sessions.json`，hook 按 session_id 维护；
  sessionend 移除、4 小时无动静自动剔除。

## UI 细节

- 面板/菜单字体：Segoe UI（曾按要求试过 assets/Impact.ttf 私有加载，用户看后决定回退；TTF 留在 assets 备用）。
- 面板窗口圆角：SetWindowRgn；**注意 SetWindowRgn 会在窗口未映射时重置位置，
  必须 geometry → update_idletasks → 裁圆角 → 再 geometry 钉一次**。
- 右键菜单 = **仿亚克力 flyout**（TranslucentTB 观感）：ImageGrab 截取菜单位置真实背景 →
  高斯模糊 14 + 主题色调(208 alpha) → PIL 整张渲染（圆角/描边/分隔线/文字/悬停态全部抗锯齿，
  四角外露真实背景截图，视觉上完美圆角）。字体 msyh.ttc index 1（Microsoft YaHei UI）14px，
  近全宽灰色分隔线(150,150,150,160)，悬停圆角高亮，上滑 10px 入场动效，跟随系统深浅色。
  **技术教训：真 DWM 亚克力（SetWindowCompositionAttribute）会把 tk 的 GDI 内容当全透明
  （文字消失），且 tk 的 -alpha 淡入（WS_EX_LAYERED）与 DWM 亚克力互斥——都别再试。**
  托盘图标右键同样弹这个 flyout（子类化 pystray Icon._on_notify 接管 WM_RBUTTONUP），
  托盘左键=开用量面板，tooltip 仅显示 ClaudePetLeiMi。
- 面板跟随桌宠拖动实时移动；用量面板与会话面板互斥（开一个关另一个）。
- GIF 边缘：alpha 阈值 128 二值化（去品红键色混边）。曾试过 MinFilter 腐蚀收边去白圈，
  效果不佳已回退——GIF 边缘白圈是作者对白底烘焙的，腐蚀会啃掉描边，接受现状。
- 调试参数：`claude_pet.pyw --popup` / `--sessions` / `--menu` 启动即开对应面板/菜单。
- 用量面板行都挂在同一个共享 grid（col0 minsize 134），否则各行列宽独立算、进度条不对齐。

## 操作

- 左键拖动（位置记忆在 `pet_config.json`），右键菜单退出。
- 开机自启：`shell:startup` 里的 `ClaudePetLeiMi.lnk`（pythonw 运行）。
- 手动启动：桌面 `ClaudePetLeiMi.lnk` / 双击 `claude_pet.pyw` / `pythonw claude_pet.pyw`。
- 单实例：重复启动时新实例按 `pet.pid` 自动顶替旧实例（换 GIF 后双击一下即等于重启）。
- 换 GIF：替换 `gifs/01~06.gif` 后重启即可；改映射编辑 `claude_pet.pyw` 的 `STATE_GIF`。

依赖：系统 Python 3.13 + Pillow（已装），无其他依赖。
