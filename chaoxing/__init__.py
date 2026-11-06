"""超星学习通任务点自动化工具。

模块划分：
    config   配置加载
    session  HTTP 会话（限速 / 重试 / 审计）
    auth     登录与会话校验
    api.*    接口层（纯解析 + 请求拼装）
    crypto.* 加密与混淆解码
    task.*   任务执行器
    engine   调度与断点续跑
    store    持久化
"""

__version__ = "0.1.0"
