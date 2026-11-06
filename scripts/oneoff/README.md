# 一次性排障脚本

这里放的是开发过程中针对真实页面临时写的探针，用来回答「这个页面到底长什么样」「服务端到底认不认」这类问题。

它们**不是稳定接口**：文件名以 `_` 开头，参数和输出格式随时会变，也可能依赖当时库里的账号、课程 id 和 `data/debug/` 里的抓取结果。留着是因为排障时比重新写一遍快。

| 脚本 | 当时用来查什么 |
|---|---|
| `_cards_probe.py` | 把 cards 页原始 `mArg` 抓下来，看 attachments 的真实字段 |
| `_diag_cards.py` | 统计 mArg 出现次数、attachments 数量 |
| `_diag_expect.py` | 对比「按目录 job_count 提前收摊」与「扫满 num=0..6」的差异 |
| `_diag_scan.py` | 复现 `cx scan` 的章节→任务点链，定位哪一步丢数据 |
| `_diag_types.py` | 逐条打印卡片类型，确认 PDF/PPT 被错判成视频 |
| `_doc_*.py` | 文档打卡接口的返回体、回执字段、HTML 结构 |
| `_face_probe.py` | 播放页在什么时机弹人脸框 |
| `_probe_instant.py` | 直接上报 `playingTime=duration` 能不能秒过（结论：不能） |
| `_show_reports.py` | 按 task 分组打印上报序列，检查节拍与 enc |
| `_truth.py` | 以服务端为准，导出每个任务点的真实 isPassed |

正式的工具在上一级目录：`probe.py`、`sniff_enc.py`、`rate_probe.py`、`speed_probe.py`、`concurrency_probe.py`、`verify_browser.py`、`verify_captcha.py`、`font_lab.py`。
