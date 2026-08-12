# ClaudePetLeiMi — Claude Code 状态桌宠

桌面上的小蕾米埃尔，实时演出你的 Claude Code 工作状态，顺便帮你盯着用量。

## 功能

### 状态桌宠

Claude Code 在干什么，桌宠就演什么（透明置顶小窗，任意拖动）：

| 表情 | 状态 | 什么时候出现 |
|:---:|---|---|
| <img src="gifs/02.gif" width="88"> | 干活中 | Claude 正在执行工具（读写文件、跑命令）|
| <img src="gifs/01.gif" width="88"> | 思考中 | 正在生成回复、或两次工具调用之间 |
| <img src="gifs/03.gif" width="88"> | 完成 | 回合结束（展示 10 秒）；空闲超 3 分钟也用这张待机 |
| <img src="gifs/04.gif" width="88"> | 等你回复 | Claude 提问或请求授权，在等你 |
| <img src="gifs/05.gif" width="88"> | 出错了 | 工具执行失败，或 API 报错（连接中断 / 重试失败，从 transcript 补判）|
| <img src="gifs/06.gif" width="88"> | 待机 | 没有活跃会话 |

新开 Claude Code 会话时桌宠自动启动，多会话并发时跟随最近有动静的那个。

### 用量详情面板

复刻 Claude Desktop 的 Usage 页：5 小时窗 / 周限额（含各模型单独限额）/ 额外用量，
每项带进度条、重置时间和百分比，用量烧到 80% 进度条变红并弹系统通知。
数据直连官方用量接口（180 秒刷新），底部 ⟳ 可手动刷新。
底部 Projection 行按最近一小时的消耗速率外推：预计某限额会在重置前烧完时
红字标出撞线时刻（如 `Fable runs out ~6:30 PM`），能撑到重置则提示 On pace，
帮你把每个刷新窗口用满（挂机没消耗或采样不足 10 分钟时不显示）。

### 会话状态面板

列出所有活跃的 Claude Code 会话：状态动词（Doodling… / Pondering… / Waiting…）、
会话标题、context window 用量进度条、最后活动时间。

### 托盘与菜单

- **托盘左键**：新建一个 Claude Desktop 对话
- **托盘右键 / 桌宠右键**：仿亚克力菜单 —— Show App（打开 Claude Desktop）/
  用量详情 / 会话状态 / 检查更新 / 卸载 / 退出
- **双击桌宠**：打开用量详情
- 面板点击空白处自动关闭

### 自动更新

每 24 小时自动检查新版本，静默更新后自动重启；菜单里也可手动"检查更新"。

## 安装

### Windows

前提：Windows 10/11 + [Python 3.10+](https://www.python.org/downloads/)（勾选 Add to PATH）+ Claude Code。

```powershell
irm https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/install.ps1 | iex
```

装完桌宠自动出现在右下角；正在运行的 Claude Code 会话需重启后才会驱动桌宠。
重复运行同一命令即为升级。

### macOS（实验性，未经真机测试）

> ⚠️ macOS 版目前**没有在真实 Mac 上测试过**：核心逻辑与面板渲染已在
> 开发机验证，但窗口/菜单栏/通知等原生交互属于盲写，遇到跑不起来
> 属正常，请带着 `--diag` 输出提 issue 或反馈。

前提：macOS 12+ + python3 + Claude Code。

```bash
curl -fsSL https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/install.sh | bash
```

自动装依赖（pyobjc/pillow）、合并 hooks、配置 LaunchAgent 开机自启。
与 Windows 版共享 petcore 核心逻辑，平台差异：右键菜单是原生 NSMenu、
托盘换成菜单栏徽章、通知走 osascript、凭证支持从 Keychain 读取
（Claude Code 在 mac 上默认存 Keychain，首次访问系统可能弹授权）。

## 卸载

右键菜单点「卸载」（有二次确认），或运行：

```powershell
irm https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/uninstall.ps1 | iex
```

自动停止桌宠、清理 `~/.claude/settings.json` 里的相关配置（其余配置不动）、
删除快捷方式和安装目录。

## 使用小抄

| 操作 | 效果 |
|---|---|
| 左键拖动桌宠 | 移动位置（会记住）|
| 双击桌宠 | 用量详情 |
| 右键桌宠 / 托盘 | 菜单 |
| 托盘左键 | 新建 Claude Desktop 对话 |
| 替换 `gifs/01~06.gif` 后重启 | 换皮肤 |

## 遇到问题

用量拿不到数据时，跑一下诊断并按提示处理：

```powershell
# Windows
python "$env:LOCALAPPDATA\ClaudePetLeiMi\claude_pet.pyw" --diag
```

```bash
# macOS
python3 "$HOME/Library/Application Support/ClaudePetLeiMi/claude_pet_mac.py" --diag
```

它会逐项检查：Claude Code 登录凭证 → 用量接口连通性 → statusline 兜底数据 → hooks 是否生效。
