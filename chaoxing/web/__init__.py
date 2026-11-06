"""``cx web`` 的网页面板：``cx.web`` 命名空间。

这里刻意不 import fastapi —— ``web`` 是可选依赖，``import chaoxing.web`` 必须
在没装 fastapi 的机器上也能成功，否则 ``cx doctor`` 会连带崩掉。
"""
