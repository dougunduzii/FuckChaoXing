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
- [配置文件说明](#配置文件说明)
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
cd E:\desktop\FuckChaoXing
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
| `python -m chaoxing.web.app` | 启动网页控制面板（默认 http://127.0.0.1:8765） |

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

任何兼容 OpenAI 的接口都能用（OpenAI、DeepSeek、通义、本地 vLLM/Ollama…）：

```toml
[ai]
enabled  = true
base_url = "https://api.deepseek.com/v1"
api_key  = "sk-xxxxxxxx"
model    = "deepseek-chat"
```

先验证通不通：

```powershell
cx ai test
```

答题链路是：**题库命中 → AI 作答 → 都不行就留空跳过（绝不瞎猜）**。AI 给出的答案会自动存进题库，下次同一道题不再花 token。

### `[risk]` —— 风控保险

```toml
daily_cap_hours  = 6.0   # 每账号每天最多跑多少小时
max_lanes        = 8     # 同时在飞的通道上限，超了会被夹住
```

---

## 常见问题

### Q1. 突然所有请求都返回 202 / 弹「操作异常，请输入图片中的验证码」

风控触发了。典型原因：并发开得太高、同一账号开了两个进程、或者短时间碰了太多任务点。

处理：

```powershell
cx face --url "你刚才在学习通网页里看到验证码的那个地址"
```

用浏览器打开那个页面，把验证码填进去，验证通过后脚本会把新 cookie 导回数据库。也可以手动：

```powershell
cx login me --browser     # 重新登录一次，效果相同
```

之后把并发降下来：

```powershell
cx run me --task-concurrency 2
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
│   └── web/app.py         FastAPI 网页面板
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

当前 **52 个用例**，其中 `tests/test_engine.py` 用 `httpx.MockTransport` 把整套超星接口假起来，能在完全离线的情况下跑通「扫描 → 播放上报 → 文档打卡 → 测验提交」全流程。

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
