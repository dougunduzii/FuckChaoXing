# 超星学习通 · 任务点自动化工具

一个用纯 Python 写的命令行工具：登录超星学习通 → 抓出每门课的章节和任务点 → 自动完成**视频**、**文档/PPT 阅读**、**章节测验**。

它不是浏览器插件，也不需要你一直开着网页。所有状态存在本地 SQLite 里，随时可以中断、重跑、查进度。

---

## 目录

- [能做和不能做的](#能做和不能做的)
- [准备工作](#准备工作)
- [安装（虚拟环境）](#安装虚拟环境)
- [五分钟跑通](#五分钟跑通)
- [命令速查](#命令速查)
- [网页面板](#网页面板)
- [配置文件说明](#配置文件说明)
- [AI 答题（可选）](#ai-答题可选)
- [常见问题](#常见问题)
- [配合使用：签到交给 ChaoxingSignFaker](#配合使用签到交给-chaoxingsignfaker)
- [数据存在哪里](#数据存在哪里)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)

---

## 能做和不能做的

| 任务点类型 | 状态 | 说明 |
|---|---|---|
| 视频 / 音频 | ✅ | 按倍速推进进度并按时上报心跳，服务端 `isPassed` 为真才算完成 |
| 文档、PPT、PDF | ✅ | 打阅读卡，以服务端回执「考核点已经完成」为准 |
| 阅读（read） | ✅ | 同上 |
| 空章节 | ✅ | 点一次「学生看书」页，让服务端记一次访问 |
| 章节测验 | ✅ | 题库命中 → AI 作答 → **答不全就不提交**，只存草稿 |
| 直播 | ❌ | 需要真实进入直播流，不做 |
| 签到 / 签退 | ❌ | 实时事件，需要手机在场、位置或现场扫码 → 用 [ChaoxingSignFaker](#配合使用签到交给-chaoxingsignfaker) |
| 讨论 / 考试 | ❌ | 不在本工具范围 |

> 重要原则：**没拿到服务端确认，就不写"已完成"**。文档看回执、视频看 `isPassed`、测验看提交返回，任何一步没确认都会留在待办里。

---

## 准备工作

1. **Python 3.12 或更高**（3.13 / 3.14 都实测可用）。
   - Windows：从 [python.org](https://www.python.org/downloads/) 下载安装包，安装时**务必勾选 `Add python.exe to PATH`**。
   - macOS：`brew install python@3.12`
   - 验证：打开终端执行

     ```powershell
     python -V
     ```

     能看到 `Python 3.12.x` 以上即可。若提示找不到命令，换 `py -V`（Windows）或 `python3 -V`（macOS/Linux）。

2. **一个学习通账号**（手机号 + 密码），或者一台已经登录过学习通的浏览器。

3. （可选）**Chrome 或 Edge**：只有"过验证码""刷脸""扫码登录"这几个功能需要浏览器。脚本会优先用系统里已经装好的 Chrome/Edge，所以**通常不需要额外下载浏览器内核**。

---

## 安装（虚拟环境）

推荐用 Python 自带的 `venv`，把依赖装进项目目录，不污染系统环境。

### 1. 进入项目目录

```powershell
git clone https://github.com/dougunduzii/FuckChaoXing.git
cd FuckChaoXing
```

### 2. 创建虚拟环境

**Windows（PowerShell / CMD / Git Bash 通用）**

```powershell
python -m venv .venv
```

**macOS / Linux**

```bash
python3 -m venv .venv
```

执行完会多出一个 `.venv` 文件夹，里面是一份独立的 Python。

### 3. 激活虚拟环境

**Windows PowerShell**

```powershell
.\.venv\Scripts\Activate.ps1
```

如果报「禁止运行脚本」，先执行一次（只影响当前用户）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Windows CMD**

```bat
.venv\Scripts\activate.bat
```

**macOS / Linux**

```bash
source .venv/bin/activate
```

激活成功的标志：命令行提示符前面多了 `(.venv)`。

> 不想激活也行：后面所有 `cx` 命令都换成 `.\.venv\Scripts\python.exe -m chaoxing`（macOS/Linux 用 `.venv/bin/python -m chaoxing`），效果完全一样。

### 4. 安装依赖

```powershell
pip install -r requirements.txt
```

国内网络慢的话换镜像：

```powershell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 5. 把项目本身装进去

```powershell
pip install -e . --no-deps
```

这一步只是为了让 `cx` 这个短命令可用（`--no-deps` 是因为依赖上一步已经装好了）。

### 6. 自检

```powershell
cx doctor
```

会打印依赖状态、数据库位置、账号数量、浏览器可用情况。**看到一片 OK 就说明装好了**。

> 如果「内置 chromium」那一行显示缺失，但「系统 chrome」「系统 msedge」有 OK，没关系——验证码和刷脸会走系统浏览器。

---

## 五分钟跑通

### 第 1 步：生成配置

```powershell
cx init
```

会在项目根目录生成 `config.toml`，在 `data/` 下建好数据库和日志目录。

### 第 2 步：登录

三种方式，任选一种。

**方式 A（推荐）：弹浏览器手动登录**

```powershell
cx login me --browser
```

`me` 是你给这个账号起的备注名，随便起。浏览器打开后正常输入账号密码（可能要滑块/扫码），登录成功、页面跳进学习通后，回到终端按回车，cookie 会自动存进数据库。

**方式 B：账号密码**

```powershell
cx login me --password --uname 13800000000
```

随后会提示输入密码（输入时不显示字符）。这条路遇到验证码会失败，失败就改用方式 A。

**方式 C：从浏览器复制 cookie**

1. 用 Chrome 打开并登录 `https://i.chaoxing.com`
2. 按 `F12` → `Network`（网络）标签 → 刷新页面 → 点第一个请求
3. 在 `Request Headers` 里找到 `Cookie:`，把后面一长串复制下来
4. 粘贴进命令：

```powershell
cx login me --cookie "_uid=123456;_d=xxx;fid=1234;uf=xxx;..."
```

也可以把 cookie 存成文件再导入：`cx login me --cookie-file .\cookie.txt`

验证是否成功：

```powershell
cx account list
```

看到账号和 `puid` 就对了。

### 第 3 步：抓课程和任务点

```powershell
cx scan me
```

输出类似：

```
[me] 抓取完成：1 课 / 25 章 / 124 任务点 / 跳过锁定 0 / 人脸任务点 90
```

这一步只读不写，随时可以重复跑。看到「0 任务点」通常是课程还没开始或 cookie 过期了。

### 第 4 步：看进度

```powershell
cx status
```

```
┌──────────┬─────────┬──────┐
│ 类型     │ 状态    │ 数量 │
├──────────┼─────────┼──────┤
│ document │ done    │ 34   │
│ video    │ done    │ 90   │
└──────────┴─────────┴──────┘
```

### 第 5 步：开跑

先空跑一次，确认要处理哪些东西（**不会发任何上报请求**）：

```powershell
cx run me --dry-run --limit 3
```

确认没问题就正式跑：

```powershell
cx run me
```

想快一点：

```powershell
cx run me --task-concurrency 3 --speed 2.0
```

`Ctrl+C` 随时可以停，进度都在数据库里，下次 `cx run me` 接着来。

---

## 网页面板

不想盯着黑框框？`cx web` 起一个本地控制台，扫描、刷课、看日志、翻请求记录都在网页里点。

### 怎么打开

```powershell
cx web
```

它会：

1. 在 `127.0.0.1:8765` 起服务（端口被占会自动往后顺延，见下面「端口和重复启动」）；
2. 等 1.2 秒把默认浏览器打开；
3. 在终端打印本机地址、局域网地址和退出方式。

```text
────────────────────────────
  cx 网页面板
────────────────────────────
  本机：http://127.0.0.1:8765/
  监听：127.0.0.1:8765   日志级别：info
  Ctrl+C 退出
```

浏览器没弹出来就自己复制地址。`Ctrl+C` 停面板：正在跑的任务是同一进程里的 asyncio 任务，会跟着一起中断，但进度都已落库，下次 `cx run` 接着来。

> `cx web` 提示 `No such command`，说明项目版本旧了或第 5 步 `pip install -e . --no-deps` 没做。退路：
> ```powershell
> python -m chaoxing.web.app
> ```
> 两条路走的是同一段代码。

### 参数

| 参数 | 说明 |
|---|---|
| `--host 127.0.0.1` | 监听地址。填 `0.0.0.0` 让同一局域网的手机/平板也能开（会自动开访问口令） |
| `--port 8765` | 端口。`--port 0` 让系统分配一个空闲端口 |
| `--port-auto` / `--no-port-auto` | 端口被占时是否自动顺延，默认顺延；`--no-port-auto` 直接报错退出 |
| `--token <口令>` | 访问口令。绑非回环地址又没给口令时自动生成，并写进打印出来的 URL |
| `--open` / `--no-open` | 是否自动开浏览器，默认开 |
| `--reload` | 改代码自动重载，开发时用 |
| `--log-level info` | uvicorn 日志级别（`debug` / `info` / `warning` / `error`） |

### 五个视图

| 视图 | 内容 |
|---|---|
| 总览 | 状态徽标、当前/最近任务、进度条、课程表（章节数、完成/总数、失败数）、按类型进度、今日已用时长 |
| 任务点 | 全部任务点，按状态 / 类型 / 课程过滤，支持标题关键字搜索与分页 |
| 日志 | 和终端同一份 loguru 输出，实时推送；可按关键字过滤，自动滚到底 |
| 请求记录 | 最近 50 条上报（方法、路径、状态码、耗时、片段），排查 9010 的第一现场 |
| 设置 | 账号增删与登录（贴 cookie / 弹浏览器）、运行默认值（倍速、并发、类型）、enc 版本切换、题库统计 |

### 面板上能做什么

| 面板操作 | 等价命令 |
|---|---|
| 抓取课程结构 | `cx scan [名字]` |
| 开始刷课（可选账号 / 课程 / 类型 / 倍速 / 并发 / 上限 / 试跑） | `cx run [名字] --types video --course 1 --limit 5 --dry-run` |
| 停止当前任务 | 终端 `Ctrl+C`，或面板上的「停止」（只取消当前 job） |
| 重置任务点（可按课程 / 类型） | `cx reset [名字] --course 1 --types video` |
| 切换 enc 版本 | `cx enc v2` |
| 添加账号 / 粘贴 cookie / 浏览器登录 | `cx login <名字> --cookie "..."` / `cx login <名字> --browser` |
| 查看任务点明细 | `cx status`（面板多了过滤和分页） |

运行中再点「刷课」会被后端挡回来并提示「已有任务在跑：…」——同一时刻只允许一个扫描/刷课任务，避免自己跟自己抢同一个账号。

### 实时性怎么来的

面板用 SSE（`/api/events`）推日志和任务状态，断线自动重连（`retry: 2000`），15 秒没消息发一次心跳；SSE 不可用时页面自动退回轮询。所以日志是「刷出来」的，不是靠 2 秒定时器憋出来的。

### 换端口 / 让别的设备访问

```powershell
cx web --port 9000              # 换端口
cx web --host 0.0.0.0           # 局域网内其它设备也能开
cx web --host 0.0.0.0 --token "自己设的口令"   # 指定口令
cx web --no-open                # 不自动开浏览器
```

绑到非回环地址（`0.0.0.0`、内网 IP）时，面板会强制要求访问口令：没给 `--token` 就随机生成一个，并把 `?token=...` 拼在打印出来的地址里。口令认三个地方——URL 的 `?token=`、请求头 `X-CX-Token`、以及首次带 token 访问后写下的 `cx_token` cookie（7 天）。`/api/health` 和静态资源不需要口令，其余 `/api/*` 一律校验。

> 面板能操作你的账号会话：谁能打开这个地址，谁就能替你刷课、看日志。只在 `127.0.0.1` 上用；确实要局域网访问，务必带上 `--token`，别在公共 WiFi 下开。

### 端口和重复启动

- 端口上是**本面板的另一个实例**（`/api/health` 回 `chaoxing-web`）→ 不再起第二份，只打印地址并打开浏览器。两个进程抢同一个 `data/cx.db` 是最容易触发 9010 的操作之一，这里直接堵掉。
- 端口被**别的程序**占着 → 自动往后找空闲端口（最多 20 个），打印实际用的地址。
- 想让它直接失败：`cx web --no-port-auto`；想自己看谁占着：

```powershell
Get-NetTCPConnection -LocalPort 8765 | Select-Object OwningProcess
```

### 和命令行同时用

面板和命令行读同一份 `data/cx.db`，可以同时开：一个终端里 `cx run me` 在跑，页面上照样能看到进度和日志。但**不要两边同时跑同一个账号**，那等于自己给自己叠并发。

### 第一次打开是空的

正常。面板只读数据库，先按「五分钟跑通」做完 `cx init` / `cx login` / `cx scan`，刷新就有数据。没有任何账号时，点「刷课」会提示「还没有账号，先在「设置」里添加并登录」。

---

## 命令速查

| 命令 | 作用 |
|---|---|
| `cx init [--force]` | 生成 `config.toml` 和 `data/` 目录（`--force` 覆盖已有配置） |
| `cx doctor` | 环境自检：依赖、数据库、账号、浏览器 |
| `cx login <名字> --browser` | 弹浏览器登录并保存 cookie（推荐） |
| `cx login <名字> --password --uname <手机号>` | 账号密码登录 |
| `cx login <名字> --cookie "<cookie>"` | 直接粘贴 cookie |
| `cx login <名字> --cookie-file <文件>` | 从文件读取 cookie |
| `cx account list` | 列出所有账号 |
| `cx account remove <名字>` | 删除账号及其课程、任务点 |
| `cx scan [名字]` | 抓课程 → 章节 → 任务点，写进数据库 |
| `cx status` | 查看任务点总进度 |
| `cx run [名字] [选项]` | 执行待完成任务点 |
| `cx reset [名字] [--course N] [--types video]` | 把任务点重置为待执行，下次 `scan` 重新对齐服务端 |
| `cx cookies [名字] [--prune]` | 查看会话 cookie；`--prune` 清除风控/埋点 cookie |
| `cx enc [v1\|v2]` | 查看或切换视频上报签名版本 |
| `cx face [名字] [--url ...]` | 人工过刷脸/扫码，成功后自动把新 cookie 导回 |
| `cx quiz stats / export / import / add / pull` | 题库管理 |
| `cx ai test` / `cx ai ask <题目.json>` | 验证 AI 接口 / 离线试答 |
| `cx web [--port N] [--host 0.0.0.0] [--token 口令]` | 启动网页控制面板（默认 127.0.0.1:8765，端口被占自动顺延；参数详见 [网页面板](#网页面板)） |

### `cx run` 的常用参数

| 参数 | 说明 | 建议值 |
|---|---|---|
| `--types video,document` | 只跑某几类任务点 | 按需 |
| `--course 1` | 只跑 `cx status` 里 id = 1 的那门课 | 按需 |
| `--limit 5` | 本次最多处理 5 个任务点 | 试跑用 |
| `--dry-run` | 空跑，不真正上报 | 每次大改配置后跑一次 |
| `--speed 2.0` | 本次覆盖倍速 | `1.5 ~ 2.0`，别超 2.5 |
| `--task-concurrency 3` | 同一账号内并行处理几个任务点 | `1 ~ 4` |
| `--retries 2` | 单个任务失败重试次数 | 默认 |
| `--concurrency 3` | **多个账号**之间的并发 | 多账号才用 |

---

## 配置文件说明

`cx init` 生成的 `config.toml` 已经带注释，这里挑重点解释：

### `[http]` —— 请求节奏

```toml
timeout      = 30      # 单次请求超时（秒）
retries      = 3       # 网络错误重试次数
min_gap      = 0.6     # 同一通道两次请求的最小间隔（秒）
global_rate  = 4.0     # 全账号每秒最多几个请求（总闸）
global_burst = 6.0     # 允许的短时突发
proxy        = ""      # 代理，形如 "http://127.0.0.1:7890"；单账号不用填
audit        = true    # 记录每次请求，排障用：cx status 里能查到
```

**`global_rate` 不要调大。** 触发风控的不是请求速率，而是「短时间内碰了多少个不同的任务点」——实测 2.2 请求/秒也可能被拦。

### `[video]` —— 视频推进

```toml
speed          = 2.0   # 本地推进倍速：每 60 秒真实时间，进度前进 120 秒
interval       = 60    # 上报间隔（秒），卡片里给了 reportTimeInterval 时以卡片为准
jitter         = 0.3   # 间隔随机抖动 ±30%，避免整齐划一的机器人节奏
instant_skip   = false # 直接报到片尾。实测无效，别开
audio_fallback = true  # 视频通道失败时改用音频通道重试
```

实测有效倍速：`speed=2.0` 约 2.25 倍墙钟，`3` 路并发聚合约 3.9 倍。**超过 2.5 容易进度不涨。**

### `[quiz]` —— 章节测验

```toml
submit_threshold  = 0.8  # 覆盖率低于 80% 只存草稿，不提交
count_ai_as_known = true # AI 给出的答案也算"已知"
```

### `[ai]` —— 让 AI 帮你答题（可选）

留空就是关闭。完整字段：

```toml
[ai]
enabled     = false                          # 改成 true 才启用
base_url    = "https://api.openai.com/v1"    # 任何兼容 OpenAI 的接口
api_key     = ""                             # 你的 key，别提交到 git
model       = "gpt-4o-mini"
temperature = 0.2                            # 答题要稳，别调高
timeout     = 60.0
retries     = 2
batch_size  = 12                             # 一次送几道题
max_tokens  = 1600
json_mode   = "schema"                       # schema | object | text，服务端不支持会自动降级
```

填完先跑 `cx ai test` 验证。完整操作步骤、各家接口怎么填、费用和注意事项见下面的 [AI 答题](#ai-答题可选)。

### `[risk]` —— 风控保险

```toml
daily_cap_hours  = 6.0   # 每账号每天最多跑多少小时
max_lanes        = 8     # 同时在飞的通道上限，超了会被夹住
```

---

## AI 答题（可选）

章节测验如果只有题库，一旦遇到题库里没有的题就只能留空。接一个模型就能把剩下的题也答上。

**不需要就别开**：`[ai] enabled = false` 时，脚本完全不联网调模型，题库里没有的题一律留空跳过——这一点不会变。

### 第 1 步：准备一个 key

任何兼容 OpenAI `/v1/chat/completions` 的服务都行，选一个填进 `config.toml`：

| 服务商 | `base_url` | `model` 举例 | 备注 |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | 便宜，中文稳，最省心 |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | 国内直连快 |
| 月之暗面 | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | 长题目友好 |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | 需要能连外网 |
| 本机 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` | 白嫖、数据不出本机，`api_key` 随便填 `ollama` |

拿到 key 后**别直接贴在聊天里、别提交到 git**。`config.toml` 已经在 `.gitignore` 里，放这里就行。

### 第 2 步：写进配置

```toml
[ai]
enabled  = true
base_url = "https://api.deepseek.com/v1"
api_key  = "sk-你的key"
model    = "deepseek-chat"
```

保存，不用重启任何东西。

### 第 3 步：先验证通不通

```powershell
cx ai test
```

正常会打印类似：

```
接口：https://api.deepseek.com/v1/chat/completions
模型：deepseek-chat
OK 1834 ms｜我是 DeepSeek，一个乐于助人的助手。
tokens: prompt 18 / completion 12
```

看到报错就按表处理：

| 报错 | 意思 | 怎么办 |
|---|---|---|
| `HTTP 401` / `403` | key 不对或没权限 | 重新复制 key，注意别多空格 |
| `HTTP 404` | `base_url` 多了或少了 `/v1` | 按上表逐字对齐 |
| `json_mode=schema 不支持` | 该服务没有 `response_format` 的 json_schema | 保留默认即可，脚本会自动降级到 `object`、再到 `text` |
| `连接超时` | 网络到不了 | 换国内服务商，或给本机代理：`$env:HTTPS_PROXY="http://127.0.0.1:10809"` |

### 第 4 步：离线试答一次（推荐）

先看看它答得准不准，再让它上场：

```powershell
cx quiz pull me            # 把本课程所有题面导成 data/quiz-pull.json
cx ai ask data/quiz-pull.json --out data/ai-answers.json
```

`cx quiz pull` 只读接口，不会提交任何东西；`cx ai ask` 把题目喂给模型，输出每题的答案和置信度。打开 `data/ai-answers.json` 抽查几道，觉得靠谱再进下一步。

答案满意的话直接导回题库，之后正式跑就是"题库命中"：

```powershell
cx quiz import data/ai-answers.json
```

### 第 5 步：正式跑

```powershell
cx run me --types work
```

日志里会看到每份测验的处理结果：

```
[me] 测验「第3章 章节测验」12 题
[me] 覆盖率 100%（题库 8 / AI 4 / 空白 0 / 共 12）→ 提交
[me] (5/12) work 第3章 章节测验 -> OK 覆盖率 100% 已提交 [0.8min]

[me] 测验「第5章 章节测验」12 题
[me] 覆盖率 58%（题库 2 / AI 5 / 空白 5 / 共 12）→ 只存草稿
[me] (6/12) work 第5章 章节测验 -> OK 覆盖率 58% 只存草稿 [0.6min]
```

### 它是怎么决策的

```
题库命中 → AI 作答 → 还有空的就留空跳过
```

- **绝不猜**。答不出来就是答不出来，日志里会明说"AI 未启用"或"AI 调用失败"，不会给你蒙一个字母上去。
- **答不全就不提交**。空白题占比超过阈值（`[quiz] submit_threshold`，默认 0.2）时，只用 `pyFlag=1` 存草稿，不点提交——留着你手动补。
- **AI 的答案会回写题库**（`source="ai"`），同一道题不会重复花 token。
- **题库永远优先**。AI 只在题库没命中时才被调用。

### 费用和隐私

- 一次测验十来道题，`batch_size = 12` 就是一次请求；一章的量通常几分钱以内。
- 题目文本会发到你自己配置的那个服务商。介意的话用本机 Ollama，数据不出机器。
- 不启用 AI 时，除学习通本身外不会向任何第三方发请求。

---

## 常见问题

### Q1. 突然所有请求都返回 202 / 弹「操作异常，请输入图片中的验证码」

风控触发了。典型原因：并发开得太高、同一账号开了两个进程、或者短时间碰了太多任务点。

处理分两步。

**第一步：在真浏览器里过验证码**（HTTP 直接提交过不了，必须在浏览器里点）：

```powershell
python scripts/verify_browser.py
```

脚本会打开 Chrome、停在验证页，把图截到 `data/browser-captcha.png`；你打开图片看清 4 个字符，写进 `data/code.txt`（例如 `xCsm`），脚本读到后自动填进去、提交，成功后把新 cookie 导出到 `data/cookies.json`。

**第二步：把新 cookie 导回数据库**：

```powershell
cx login me --cookie-file data\cookies.json
```

看到 `登录成功 puid=…` 就恢复了。之后把并发降下来：

```powershell
cx run me --task-concurrency 2
```

如果想要的是一整个浏览器窗口自己操作（比如当场点别的页面），用：

```powershell
cx login me --browser     # 重新登录一次，效果相同
```

### Q2. 提示找不到 `cx` 命令

虚拟环境没激活，或者第 5 步没执行：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e . --no-deps
```

或者干脆不用短命令，直接：

```powershell
.\.venv\Scripts\python.exe -m chaoxing --help
```

### Q3. 提示 Playwright 找不到浏览器

```powershell
python -m playwright install chromium
```

**或者更省事**：机器上装了 Chrome / Edge 就行，脚本会自动退到系统浏览器。`cx doctor` 能看到具体用了哪个。

### Q4. 视频跑完了，`cx status` 还是 pending

服务端没认。按顺序检查：

1. 上报返回里 `isPassed` 是不是 `true`（`data/cx.log` 里搜 `isPassed`）
2. 是不是开了 `instant_skip`——它实测无效，请保持 `false`
3. `speed` 是不是调太高（回到 2.0 试试）
4. cookie 是否过期：`cx account list` 看状态，或重新 `cx login me --browser`

对不上服务端的时候，用这个把本地状态对齐一遍：

```powershell
cx scan me        # 会按卡片上的 isPassed 重新标记
cx status
```

### Q5. 文档任务点一直不完成

文档**没有** `isPassed` 字段，唯一判据是打卡回执 `{"msg":"考核点已经完成","status":true}`。如果本地显示 pending 但网页上已经绿了：

```powershell
cx reset me --types document
cx scan me
cx run me --types document
```

补打一次卡几乎立刻返回（几十个文档通常 1 分钟内完成）。

### Q6. 学习通弹人脸识别

```powershell
cx face me
```

会打开播放页并自动点播放，等弹窗出现后**你去手机上扫码 / 刷脸**，完成后自动把 cookie 导回。

如果这个页面根本不出弹窗，用 `--url` 指定你亲眼看到弹窗的那个地址：

```powershell
cx face me --url "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards?..."
```

> 补充：多数课程只是**带着**人脸凭证字段上报，服务端并不按次强校验——这类视频不刷脸也能通过。真正强制时日志会明确写「服务端要求人脸识别/活体」。

### Q7. 想先看看会跑哪些东西，不发请求

```powershell
cx run me --dry-run --limit 5
```

`--dry-run` 完全不发上报请求，也不改本地状态。

### Q8. 进度跑了一半想重来

```powershell
cx reset me                  # 全部重置
cx reset me --types video    # 只重置视频
```

重置只改本地库，服务端已完成的会靠 `cx scan` 重新对齐。

### Q9. 换电脑 / 备份

所有状态都在 `data/` 目录：

- `data/cx.db` —— 账号 cookie、课程、任务点、题库、请求审计
- `data/cx.log` —— 日志
- `data/fonts/map.json` —— 混淆字体映射（有测验字体混淆时才有）

把整个 `data/` 拷走就是完整备份。**注意它含 cookie，别外传。**

---

## 配合使用：签到交给 ChaoxingSignFaker

本项目只解决**「课程进度」**（视频、文档、测验）——这些是长跑型任务，几天里慢慢推进就行。
而**签到**是完全不同的性质：老师发起后往往只有几分钟窗口，还可能要位置、拍照、现场扫二维码、人脸。这类事必须由一台连着网、能拿在手里的设备去响应，命令行工具天生不适合。

所以推荐两个工具搭配用：

| | 本项目（FuckChaoXing） | [ChaoxingSignFaker](https://github.com/aquamarine5/ChaoxingSignFaker)（随地大小签） |
|---|---|---|
| 形态 | Windows/Mac/Linux 命令行 + 网页面板 | Android App（Kotlin） |
| 负责 | 视频进度、文档打卡、章节测验 | 签到、签退 |
| 运行方式 | 挂着跑几小时到几天 | 收到通知后用手机点一下 |
| 状态存储 | 本地 SQLite（`data/cx.db`） | App 内部 |
| 两者冲突 | 无。它自己登录、自己存 cookie，不会碰本项目的会话 | |

### 它能做什么

- 一台安卓手机登录多个账号签到（扫码、链接等方式添加账号，可无限代签）
- **自定义位置**完成位置签到
- 从图库选图完成**拍照签到**
- **二维码签到**（一台现场设备扫码，理论上可为任意多用户签）
- 点击签到、自动过滑动验证码
- 签到之后的**签退**
- 手势签到、签到码签到（不破解手势码/签到码，需要你知道码）
- 从群聊消息里取签到
- 自动获取人脸照片完成**人脸识别签到**
- 学习通校园定制版的签到

### 怎么装

1. 手机要求 **Android 8.0 及以上**（`minSdk 26`）。iPhone、iPad、鸿蒙 NEXT 不支持——但可以用任意一台安卓手机在「代签」页用账号密码登录，替你的账号签到。
2. 下载：官网分发 [cdn.aquamarine5.fun](http://cdn.aquamarine5.fun)，或 [Releases](https://github.com/aquamarine5/ChaoxingSignFaker/releases) 里的 apk。
3. 装好后用学习通账号登录（或扫码/链接添加），在首页等老师发起签到即可。

### 两个注意事项

- 它是 **AGPL-3.0** 开源项目，作者明确表示不欢迎换皮、二次分发和商业化。自己用就好。
- 签到能否成功，最终还是看老师那边是否**当场开、当场关**。工具只是把「响应速度」这一环做到最快，别指望它能补签已经结束的签到。

> 两个工具同时用没有问题：刷课走本项目，签到走手机 App，互不干扰。唯一要避免的是**同一门课同时开两个刷课进程**（见 [Q8](#q8-进度跑了一半想重来)）。

---

## 数据存在哪里

```
data/
├── cx.db           SQLite（WAL），账号 / 课程 / 任务点 / 题库 / 审计
├── cx.log          运行日志（5MB 轮转）
├── cookies.json    浏览器登录导出的一次性 cookie（用完可删）
├── fonts/          字体映射与字形对照图
└── state/          临时状态
```

想直接查数据库（只读）：

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/cx.db');print(c.execute('select type,state,count(*) from task_point group by 1,2').fetchall())"
```

---

## 项目结构

```
FuckChaoXing/
├── chaoxing/              主包
│   ├── cli.py             命令行入口（cx 的所有子命令）
│   ├── config.py          配置读取与默认值
│   ├── session.py         异步 HTTP 会话、节流、令牌桶、风控识别
│   ├── auth.py            登录、cookie 导入、浏览器启动
│   ├── engine.py          扫描与执行调度
│   ├── ai.py              AI 答题客户端（OpenAI 兼容）
│   ├── api/               超星各接口：course / video / document / quiz
│   ├── task/              任务处理器：video / document / quiz + 基类
│   ├── crypto/            AES 登录加密、上报签名 enc、混淆字体解码
│   ├── store/             SQLite schema 与仓储层
│   └── web/               网页面板（可选依赖：fastapi / uvicorn）
│       ├── app.py        FastAPI 应用：REST + SSE + 口令中间件
│       ├── hub.py        任务队列、日志环、SSE 订阅分发
│       ├── serve.py      启动器：端口挑选、已在运行检测、局域网地址
│       ├── queries.py    面板专属查询（状态聚合、任务分页）
│       └── static/       index.html / app.js / style.css
├── scripts/               排障与压测脚本（见下方）
├── tests/                 单元测试 + 端到端离线联调（MockTransport）
├── docs/DESIGN.md         设计说明：接口清单、算法、风控、踩坑记录
├── requirements.txt       运行依赖
├── requirements-dev.txt   开发依赖（pytest）
├── config.toml            本地配置（已 gitignore）
└── cx.cmd                 Windows 双击即可用的包装脚本
```

`scripts/` 里的东西平时用不到，排障时很有用：

| 脚本 | 用途 |
|---|---|
| `probe.py chapters/cards/urls` | 抓页面存到 `data/debug/`，看真实 DOM 长什么样 |
| `sniff_enc.py` | 用真浏览器抓一次心跳 URL，反推当前 enc 算法版本 |
| `verify_browser.py` | 手动过 9010 验证码并导出 cookie |
| `rate_probe.py --lanes 2,4,8` | 逐级压测，找到账号的真实并发上限 |
| `speed_probe.py` / `concurrency_probe.py` | 实测倍速与聚合倍速 |
| `font_lab.py` | 生成混淆字体的字形对照图，用来补 `data/fonts/map.json` |

---

## 开发与测试

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

当前 **81 个用例**，其中 `tests/test_engine.py` 用 `httpx.MockTransport` 把整套超星接口假起来，能在完全离线的情况下跑通「扫描 → 播放上报 → 文档打卡 → 测验提交」全流程。`tests/test_web.py` 用 `TestClient` 覆盖面板接口契约（状态聚合、任务生命周期与并发 409、SSE、口令三通道），`tests/test_web_serve.py` 覆盖启动器的端口挑选与已在运行检测。

代码改动后建议先跑：

```powershell
python -m pytest -q
cx run me --dry-run --limit 3
```

---

## 使用边界

- 所有课程完成状态以**服务端确认**为准，本工具不伪造客户端展示值。
- 测验答不全时只保存草稿，不会提交半成品。
- 同一账号**不要同时开两个进程**跑，会互相踩心跳并触发风控。
- 签到 / 签退不在本项目范围，用 [ChaoxingSignFaker](https://github.com/aquamarine5/ChaoxingSignFaker) 那台手机去做。
- 线上风控策略会变。`cx enc`、`cx scan`、`cx status` 就是给你在这时候核对用的：先看真实页面，再改代码。

---

## 许可证

[MIT](LICENSE) © 2026 dougunduzii

随便用、随便改、随便分发，包括商用；保留版权声明即可。代码按现状提供，不附带任何担保。

