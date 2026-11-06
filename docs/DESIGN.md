# 超星学习通刷课工具 · 实现思路

> 版本：v0 设计稿 ｜ 目标：单机 CLI + 本地 Web 面板，多账号、可续跑、可观测、低风控。

---

## 0. 结论先行

| 项 | 决策 | 原因 |
|---|---|---|
| 语言/运行时 | Python 3.14（现成 3.14.2） | 生态齐、开发快，现有环境已装 `httpx curl_cffi lxml fonttools rich loguru playwright` |
| HTTP 栈 | `httpx`(async) 为主，`curl_cffi` 处理需要 TLS 指纹的接口 | 纯 `requests` 的 JA3 与浏览器差异大，超星风控对 TLS 指纹有区分 |
| 兜底 | Playwright（chromium 已装） | 扫码登录、滑块、`enc` 版本变更时用真实浏览器抓包直接拿 URL |
| 解析 | `lxml` + `bs4` | 课程/章节页是 HTML 结构，任务点藏在 `mArg={...};` 的 JSON 里 |
| 存储 | SQLite（`data/cx.db`）+ WAL | 断点续跑、题库、审计日志一体 |
| 并发模型 | asyncio：账号级并发 ≤3，账号内串行课程/章节 | 超星对同账号高频并发敏感 |
| 加密 | AES(登录) + MD5(enc) 全 Python 实现，Node 仅作算法对拍 | 少一个运行时依赖 |

一句话架构：**Playwright 拿 cookie → httpx 抓章节 → 任务点分派到 4 个 worker（video/audio、document、read、quiz）→ 每次状态落库 → 失败按退避重试**。

---

## 1. 能力边界（先对齐预期，避免做到一半发现做不了）

| 任务点类型 | 可行性 | 实现方式 | 备注 |
|---|---|---|---|
| 视频 Video | ✅ | `multimedia/log` 心跳上报 | 有 `videoFaceCaptureEnc` 的课程会卡，需跳过 |
| 音频 Audio | ✅ | 同上，`dtype=Audio` | 视频失败的兜底通道，成功率往往更高 |
| 文档 Document | ✅ | `GET /ananas/job/document` | 一次请求即完成 |
| 阅读 Read | ✅ | `GET /ananas/job/readv2` | 同上 |
| 章节测验 Work | ✅ | `/mooc-ans/api/work` 取题 → 题库匹配 → `addStudentWorkNew` | 依赖题库覆盖率，需人工/LLM 补题 |
| 直播 Live | ⚠️ | 需 `liveId/streamName`，实际结算依赖观看时长心跳 | 接口不稳定，先留桩 |
| 签到 / 讨论 / 作业批阅 | ❌ | — | 与点播接口不同源，不纳入 v1 |
| 考试 | ❌ | — | 有人脸+监考，不碰 |

---

## 2. 总体架构

```
                    ┌────────────────────────────┐
                    │  cli / web (rich / FastAPI) │
                    └──────────────┬─────────────┘
                                   │
                    ┌──────────────▼─────────────┐
                    │        Engine 调度器         │
                    │ 账号队列 → 课程队列 → 章节队列 │
                    │ 限速器 / 退避 / 熔断 / 恢复   │
                    └──┬────────┬────────┬────────┬┘
                       │        │        │        │
              ┌────────▼──┐ ┌───▼────┐ ┌─▼─────┐ ┌▼──────┐
              │ VideoTask │ │ DocTask│ │ReadTask│ │QuizTask│
              └────┬──────┘ └───┬────┘ └─┬─────┘ └┬──────┘
                   └────────────┴────────┴────────┘
                                   │
                    ┌──────────────▼─────────────┐
                    │    Session（httpx 封装）     │
                    │ cookie jar / 指纹 / 重试 / 日志│
                    └──────────────┬─────────────┘
                                   │
                    ┌──────────────▼─────────────┐
                    │   Store（SQLite，WAL）       │
                    │ account/course/chapter/task │
                    │ /quiz/runs                  │
                    └────────────────────────────┘
```

分层原则：
1. **Session 层不写业务**，只负责「带 cookie 发请求 + 限速 + 重试 + 落审计日志」。
2. **Task 层不直接发裸请求**，全部走 Session，便于统一注入 Referer/UA/`_t`。
3. **Engine 层不关心加密**，只消费 Task 的 `TaskResult(ok, retryable, detail)`。

---

## 3. 目录结构

```
FuckChaoXing/
├─ docs/
│  └─ DESIGN.md               # 本文档
├─ chaoxing/
│  ├─ __init__.py
│  ├─ config.py               # pydantic-settings，读 config.toml / 环境变量
│  ├─ session.py              # AsyncSession：cookie、限速、重试、审计
│  ├─ auth.py                 # cookie 注入 / fanyalogin / 扫码 / 会话校验
│  ├─ engine.py               # 账号→课程→章节→任务点 调度与断点续跑
│  ├─ api/
│  │  ├─ course.py            # 课程列表、章节树、任务点卡片
│  │  ├─ video.py             # /ananas/status + /multimedia/log 心跳
│  │  ├─ document.py          # /ananas/job/document、/ananas/job/readv2
│  │  └─ quiz.py              # api/work 取题、addStudentWorkNew 提交
│  ├─ crypto/
│  │  ├─ aes.py               # AES-128-CBC/PKCS7（登录用）
│  │  ├─ enc.py               # 视频 enc / attDurationEnc 生成 + 版本探测
│  │  └─ font.py              # cxSecretStyle 字体混淆解码
│  ├─ task/
│  │  ├─ base.py              # BaseTask / TaskResult / 重试语义
│  │  ├─ video.py
│  │  ├─ document.py
│  │  └─ quiz.py
│  ├─ store/
│  │  ├─ schema.sql
│  │  └─ repo.py              # 纯 SQL 封装，无 ORM
│  └─ web/
│     └─ app.py               # 可选：FastAPI + SSE 实时进度
├─ scripts/
│  ├─ probe.py                # 单接口探针：给定 cookie 打一发看响应
│  └─ sniff_enc.py            # Playwright 打开播放页，hook XHR 抓真实上报 URL
├─ tests/
├─ data/
│  ├─ cx.db
│  └─ fonts/                  # 字体映射表（glyph hash → 字符）
├─ config.toml
└─ pyproject.toml
```

---

## 4. 认证层（`auth.py`）

三条路径，按可靠性排序，全部把结果写入统一的 cookie 结构：

### 4.1 Cookie 注入（首选）
用户从浏览器 F12 导出 `.chaoxing.com` 域 cookie 文本，直接灌进 `httpx.Cookies`。
必须包含：`_uid`、`UID`、`_d`、`fid`、`vc3`（有则带）、`uf`（有则带）。

校验：
```
GET https://sso.chaoxing.com/apis/login/userLogin4Uname.do
→ {"result": 1, "msg": {"puid": <uid>, ...}}   # puid 即 userid，后续 enc 要用
```
`result != 1` 即失效，提示重新导出。

### 4.2 密码登录
```
POST https://passport2.chaoxing.com/fanyalogin
     fid=-1
     uname=<AES(username)>
     password=<AES(password)>
     refer=https%3A%2F%2Fi.chaoxing.com
     t=true
     forbidotherlogin=0
     validate=<滑块票据，可为空>
     doubleFactorLogin=0
     independentId=0
→ {"status": true, "msg": "登录成功"} / {"status": false, "msg2": "..."}
```
AES 规格（见 `crypto/aes.py`）：

| 参数 | 值 |
|---|---|
| 算法 | AES-128-CBC |
| key | `u2oh6Vu^HWe4_AES`（16 字节 ASCII） |
| iv | 同 key |
| padding | PKCS7 |
| 输出 | base64 |

`msg2` 含「验证码」字样时走 4.3。

### 4.3 Playwright 扫码兜底
```
GET  https://passport2.chaoxing.com/createqr?uuid=<uuid>      # 取二维码图
GET  https://passport2.chaoxing.com/getauthstatus?uuid=<uuid> # 轮询，成功后 set-cookie
```
实现更省事的版本：直接 `page.goto("https://i.chaoxing.com")`，人工扫码，`context.storage_state()` 落盘 `data/state/<account>.json`，后续每次启动 `new_context(storage_state=...)` 读 cookie。**这也是 `enc` 算法变更时抓真实请求的同一套工具。**

---

## 5. 数据抓取层（`api/course.py`）

### 5.1 课程列表
```
POST https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata
Referer: https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans
Body: courseType=1&courseFolderId=0&query=&superstarClass=0
```
解析 `div.course`：
- `attrs["id"]` / `attrs["info"]` / `attrs["roleid"]`
- `input.clazzId.value` → `clazzId`
- `input.courseId.value` → `courseId`
- 首个 `<a href>` 里 `cpi=(.*?)&` → `cpi`
- `span.course-name["title"]` → 课程名；`p.color3["title"]` → 教师
- 跳过 `a.not-open-tip`（未开放）

课程文件夹：`GET /mooc2-ans/visit/interaction` → `ul.file-list>li[fileid]`，再按 `courseFolderId=<fileid>` 重放上面的 POST。

### 5.2 章节树
```
GET https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse
    ?courseid={courseId}&clazzid={clazzId}&cpi={cpi}&ut=s
```
解析 `div.chapter_unit > li > div#cur<knowledgeId>`：
- `a.clicktitle` 文本 → 标题
- `input.knowledgeJobCount.value` → 任务点数量
- `span.bntHoverTips` 含「已完成」→ skip；含「解锁」→ 锁定章节，记录后跳过

### 5.3 任务点卡片（精华）
```
GET https://mooc1.chaoxing.com/mooc-ans/knowledge/cards
    ?clazzid=&courseid=&knowledgeid=&ut=s&cpi=&v=2025-0424-1038-3&mooc2=1&num=0
```
`num` 需要遍历 `0..6`（一个知识点可能有多张卡片），按 `jobid` 去重。

响应里抓 `mArg={...};`（先 `html.replace(" ", "")` 再正则），`json.loads("{"+捕获+"}")`：

```
defaults: { ktoken, mtEnc, reportTimeInterval(=60), defenc, cardid, cpi, qnenc, knowledgeid }
attachments: [ { jobid, job, mid, objectId, otherInfo, playTime, jtoken, enc, isPassed,
                 attDuration, attDurationEnc, videoFaceCaptureEnc,
                 type, property: { name, rt, objectid, resourceType, ... } } ]
```
分派规则：
- 文本含「章节未开放」→ `notOpen`
- `isPassed == true` → 跳过
- `job is None` 且 `type == "read"` → 阅读任务
- `job is None` 其它 → 空页打卡（`studentstudyAjax`）
- `type == "video"` → 视频；`document` → 文档；`workid` → 测验
- `property` 里出现 `liveId/streamName/vdoid` 或含 `live` 关键字 → 直播（v1 留桩）
- 无 `mid` 字段 → 转码失败视频，跳过
- `otherInfo` 先按 `&` 切断，只保留 `nodeId_xxx-y` 段

---

## 6. 任务执行层

### 6.1 视频 / 音频（`task/video.py`）

**Step 1 取播放参数**
```
GET https://mooc1.chaoxing.com/ananas/status/{objectId}?k={fid}&flag=normal
Referer: https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842
→ { "status": "success", "dtoken": "...", "duration": 1234, ... }
```
音频对应 `.../ananas/modules/audio/index_new.html?v=...`。

**Step 2 上报**

```
GET https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/{cpi}/{dtoken}
    clazzId      = {clazzId}
    courseId     = {courseId}
    jobid        = {jobid}
    objectId     = {objectId}
    otherInfo    = {otherInfo}          # nodeId_{knowledgeId}-{idx}
    userid       = {puid}
    playingTime  = {秒, 整数, 严格递增, ≤ duration}
    duration     = {总秒数}
    clipTime     = 0_{duration}
    isdrag       = 3
    view         = pc
    dtype        = Video | Audio
    rt           = 0.9 | 1               # 倍速标记
    _t           = {13位毫秒}
    enc          = md5(...)
    [attDuration, attDurationEnc, videoFaceCaptureEnc]   # 卡片里有就带上
→ { "isPassed": true/false }
```

**enc 算法（2025 现行版）**

```python
SALT = "d_yHJ!$pdA~5"
enc = md5(f"[{clazzId}][{userid}][{jobid}][{objectId}]"
          f"[{playingTime*1000}][{SALT}]"
          f"[{duration*1000}][0_{duration}]").hexdigest()
```

老版（无 salt / 无 duration 段）：
```python
enc = md5(f"[{clazzId}][{userid}][{jobid}][{objectId}]"
          f"[{playingTime*1000}][0_{duration}]").hexdigest()
```

`crypto/enc.py` 里做**版本探测**：首次运行调用 `scripts/sniff_enc.py`（Playwright 打开真实播放页，hook `XMLHttpRequest.prototype.open`/`fetch`，抓 1 条真实上报 URL），把 URL 参数与本地两种算法对拍，命中者记为当前版本，写入 `data/probe.json`。这样超星改算法时不用改代码，只需重跑探针。

**Step 3 进度循环（状态机）**

```
log(playingTime = 已有playTime)          # 注册连接
log(playingTime = duration)              # 天真秒过，命中就收工
if not passed:
    while not passed:
        sleep(jitter(reportTimeInterval ±30%))   # 默认 60s，实际 42~78s
        playingTime = min(duration, playingTime + elapsed * local_speed)
        passed, code = log(playingTime)
        if code == 403:      refresh dtoken → 重试（≤2 次）
        elif code == 200:    continue
        elif code == 0/网络:  指数退避 2s,4s,8s（≤5 次）
        if playingTime >= duration:
            # 满进度但未结算：每 5 次刷新一次 dtoken，间隔 3~8s，直到 isPassed
```
`local_speed` 建议 1.0–4.0。**注意**：`playingTime` 的推进速度不能显著超过墙上时间 × 合理倍速，超星会比对「相邻两次上报的时间差」与「playingTime 增量」，1.0–2.0 最稳，4.0 附近容易出现进度不涨。

**音频兜底**：视频失败（403/不涨进度）→ 用 `dtype=Audio` 重走一遍，成功率常高于视频通道。

### 6.2 文档（`task/document.py`）
```
GET https://mooc1.chaoxing.com/ananas/job/document
    ?jobid=&knowledgeid=&courseid=&clazzid=&jtoken={card.jtoken}&_dc={毫秒}
```
`knowledgeid` 从 `otherInfo` 的 `nodeId_(.*?)-` 提取。

### 6.3 阅读（`task/document.py`）
```
GET https://mooc1.chaoxing.com/ananas/job/readv2
    ?jobid=&knowledgeid={defaults.knowledgeid}&jtoken=&courseid=&clazzid=
```

### 6.4 章节测验（`task/quiz.py`）

**取题**
```
GET https://mooc1.chaoxing.com/mooc-ans/api/work
    ?api=1&workId={jobid去掉work-前缀}&jobid=&originJobId=&needRedirect=true&skipHeader=true
    &knowledgeid={defaults.knowledgeid}&ktoken={defaults.ktoken}&cpi={defaults.cpi}
    &ut=s&clazzId=&type=&enc={card.enc}&mooc2=1&courseid=
```
返回 HTML：
- 文本含「教师未创建完成该测验」→ 视为通过
- `<form>` 里所有非 `answer*` 的 `input[name]` 收集为表单字段
- 题块：`div.singleQuesId[data=题目id]`，题型码在 `div.TiMu[data]`
- 题型码：`0 单选 / 1 多选 / 2 填空 / 3 判断 / 4 简答`
- 题干 `div.Zy_TItle`、选项 `ul > li`（优先 `li[aria-label]`，兼容 `img` 题干）
- **字体混淆**：存在 `<style id="cxSecretStyle">` 时，CSS 里 `@font-face` 内嵌 base64 TTF，需 `crypto/font.py` 解码

**字体解码原理**
1. 从 `cxSecretStyle` 里 base64 解出 TTF → `fontTools.ttLib.TTFont`。
2. 遍历 `cmap` 得到 `(codepoint, glyphName)`；每个 glyph 取轮廓点序列，做「相对首点平移 + 归一化」后 hash。
3. 用 `data/fonts/map.json` 把 hash 映射到真实字符（映射表由历史样本累积标注，首次遇到未收录 hash 时输出到 `data/fonts/unresolved/` 供人工补）。
4. 对题干/选项字符串逐字符替换。
> 第一步先做「直接读取 + 未命中就原样保留 + 落 unknown 队列」，不要让字体模块阻塞主链路。

**答题**
1. 题干归一化：去空白/去标点/全角转半角 → `md5` 作 `qkey`。
2. 命中 `quiz` 表 → 取 `answer`；未命中且配置了 LLM/题库 API → 请求；仍无 → 随机策略（单选随机、多选按 2~4 项加权、判断随机）。
3. 答案落到选项字母：把答案文本与选项做子序列匹配 → 取选项首字母；多选题结果排序拼接。
4. 覆盖率 `= 命中数 / 总题数`：
   - `≥ config.quiz.submit_threshold`（默认 0.8）→ `pyFlag=""` 提交
   - 否则 `pyFlag=1` 仅保存（不提交，避免拉低正确率）

**提交**
```
POST https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest
Origin: https://mooc1.chaoxing.com
Body: 原表单字段 + answer{id}=..&answertype{id}=..&pyFlag=..&answerwqbid=id1,id2,
→ {"status": true, "msg": "..."}
```

---

## 7. 风控与反检测（`session.py`）

| 维度 | 策略 |
|---|---|
| 连接 | 单账号长连接复用；`httpx.Limits(max_connections=8, max_keepalive=4)` |
| UA | 固定一组真实 Chrome/Edge UA，**同一账号全程不变** |
| 头 | 每类请求补 `Referer`（视频/音频/测验各自的来源页），`Sec-Fetch-*` 与真实浏览器一致 |
| 节流 | 全局令牌桶：普通请求 ≥0.6s 间隔；视频上报按 `reportTimeInterval` 抖动 ±30%；测验答题每题 0.5–1.5s |
| IP | 同账号必须同出口；代理池只用于多账号分发，**不做账号内轮换** |
| 会话 | `_d` cookie 变化视为会话刷新，正常；`result != 1` 才判定失效 |
| 退避 | 429/403 → 2/4/8s；连续 5 次 → 该账号冷却 10 分钟；连续 3 次冷却 → 停止并告警 |
| 时长 | 单账号单日累计 ≤6h（可配），夜间 00:00–07:00 优先 |
| 熔断 | 出现「验证码」「异常登录」「请稍后」→ 立即停该账号，落 `runs` 表并飞书/控制台告警 |

**审计**：每次请求写 `runs(account, task, ts, method, url_path, status, elapsed, snippet[:200])`，出问题时直接查库，不靠翻日志。

---

## 8. 数据模型（`store/schema.sql`）

```sql
CREATE TABLE account(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, puid TEXT, fid TEXT,
  cookies TEXT,            -- json
  status TEXT DEFAULT 'ok',-- ok / cooldown / need_login / banned
  last_login_ts INTEGER, note TEXT
);

CREATE TABLE course(
  id INTEGER PRIMARY KEY, account_id INTEGER, course_id TEXT, clazz_id TEXT,
  cpi TEXT, title TEXT, teacher TEXT, status TEXT DEFAULT 'pending',
  UNIQUE(account_id, course_id, clazz_id)
);

CREATE TABLE chapter(
  id INTEGER PRIMARY KEY, course_pk INTEGER, knowledge_id TEXT, title TEXT,
  job_count INTEGER, state TEXT DEFAULT 'pending', -- pending/done/skip_locked/skip_closed
  UNIQUE(course_pk, knowledge_id)
);

CREATE TABLE task_point(
  id INTEGER PRIMARY KEY, chapter_pk INTEGER, job_id TEXT UNIQUE, object_id TEXT,
  type TEXT,               -- video/document/read/work
  other_info TEXT, jtoken TEXT, enc TEXT,
  duration INTEGER DEFAULT 0, play_time INTEGER DEFAULT 0,
  state TEXT DEFAULT 'pending', -- pending/running/done/failed/skip
  attempts INTEGER DEFAULT 0, last_err TEXT, updated_ts INTEGER
);

CREATE TABLE quiz(
  qkey TEXT PRIMARY KEY, title TEXT, options TEXT, qtype TEXT,
  answer TEXT, source TEXT, -- manual/llm/api/random
  hit INTEGER DEFAULT 0, ok INTEGER DEFAULT 0, updated_ts INTEGER
);
CREATE VIRTUAL TABLE quiz_fts USING fts5(title, qkey UNINDEXED);

CREATE TABLE runs(
  id INTEGER PRIMARY KEY, account_id INTEGER, task_pk INTEGER, ts INTEGER,
  method TEXT, path TEXT, status INTEGER, elapsed_ms INTEGER, snippet TEXT
);
```

**幂等与续跑**：任务点 `UNIQUE(job_id)`，启动时只捞 `state IN ('pending','running','failed')`；`running` 视为上次异常退出，重置为 `pending` 并把 `play_time` 保留（视频从断点继续上报，更自然）。

---

## 9. CLI 设计

```bash
cx login --qr                      # Playwright 扫码，落 storage_state
cx login --cookie cookies.txt      # 手工 cookie
cx account list / rm <name>
cx scan  --account all             # 拉课程/章节/任务点，只入库不执行
cx run   --account all --course all \
         --types video,doc,read,quiz \
         --speed 1.5 --interval 60 \
         --daily-cap 6h --dry-run
cx status [--watch]                # rich 表格：课程 → 章节 → 任务点进度
cx quiz  export > bank.jsonl       # 题库备份
cx quiz  import bank.jsonl
cx probe enc                       # 重跑 enc 版本探针
cx web  --port 8420                # 本地面板（可选，SSE 推进度）
```

`--dry-run` 必须实现：只解析、打印将要发的请求（含 enc 计算值），不真正上报。开发期全靠它验证参数拼装。

---

## 10. 里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| M0 骨架 | config / session / store / CLI 空壳 + `--dry-run` | `cx scan` 能打印课程树（真实 cookie） |
| M1 认证 | cookie 注入 + fanyalogin + 会话校验 | `cx login` 后 `cx account list` 显示 puid |
| M2 抓取 | 课程/章节/任务点入库 | `cx scan` 后库里课程数=网页数，任务点类型正确 |
| M3 静态任务点 | document + read + 空页打卡 | 单门课文档/阅读任务点全部置绿 |
| M4 视频 | status + log + enc + 进度循环 + 音频兜底 | 单个 10 分钟视频跑完 `isPassed=true`，`play_time` 曲线正常 |
| M5 编排 | 断点续跑 / 退避 / 冷却 / 审计 | kill -9 后重启不重复、不丢进度 |
| M6 测验 | 取题 / 字体 / 题库 / 覆盖率门限 / 提交 | 一套章节测验得分 ≥80% |
| M7 面板 | rich 表格 + 可选 FastAPI | 实时看 3 个账号进度 |

---

## 11. 实战坑清单（写代码前先记住）

1. **`isdrag=3` 不是装饰**：改成 1/2 会被判为拖拽，进度不结算。
2. **`clipTime` 必须是 `0_{duration}`**，拖动场景才用 `{start}_{end}`。
3. **`playingTime` 只能严格递增**。回退或跳变会触发校验失败；从库里读断点续跑时要注意别倒退。
4. **满进度 ≠ 通过**。`playingTime == duration` 后仍需多次上报直到 `isPassed`，此时刷新 `dtoken` 很关键。
5. **`dtoken` 会过期**，403 优先刷 `dtoken` 而不是刷 cookie。
6. **`attDuration` / `attDurationEnc` / `videoFaceCaptureEnc`** 卡片里有就必须带上，否则部分课程不结算；有 face capture 的直接跳过（除非做图像注入，v1 不做）。
7. **`otherInfo` 里带 `&` 的必须截断**，否则 URL 参数错位（这是超星自己的历史包袱）。
8. **章节未开放**（文本「章节未开放」）和**锁定章节**（提示「解锁」）要区分：前者可能稍后可刷，后者要等前序任务完成，别死循环。
9. **转码失败的视频没有 `mid`**，解析时 `KeyError`，必须 try/except 后 skip，否则整章中断。
10. **空章节**要用 `studentstudyAjax` 打一次卡（`courseId/clazzid/chapterId/cpi/verificationcode=`），否则该章进度不绿。
11. **课程列表有 `not-open-tip`** 的课不要碰。
12. **测验「保存 vs 提交」**：覆盖率不够时提交会拿 0 分并占掉一次机会；`pyFlag=1` 保存更安全。
13. **字体混淆只在测验页出现**（`cxSecretStyle`），且 hash 表需要持续积累，第一门课大概率解不全——接受它，别停下来。
14. **同一账号别开并发课程**：章节上报与 cookie 刷新会互相干扰，`_d` 抖动导致偶发 403。
15. **别用 `requests` 硬刚**：部分接口对 JA3 敏感，`curl_cffi` 的 `impersonate="chrome"` 能省掉一半玄学 403。

---

## 12. 待确认（开发前定 3 个数）

| 项 | 默认 | 说明 |
|---|---|---|
| 视频推进倍速 `local_speed` | 1.5 | >2.5 有进度不涨风险 |
| 上报间隔 `interval` | 60s（卡片 `reportTimeInterval` 优先） | 抖 ±30% |
| 测验提交阈值 `submit_threshold` | 0.8 | 低于则仅保存 |
