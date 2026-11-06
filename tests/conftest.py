"""公共夹具：给加密常量固定值，单测不联网、也不碰本机缓存。"""

from __future__ import annotations

import pytest

from chaoxing.crypto import keys

TEST_AES_KEY = "0123456789abcdef"
TEST_ENC_SALT = "test-salt-9"


@pytest.fixture(autouse=True)
def crypto_env(monkeypatch):
    monkeypatch.setenv(keys.ENV_VARS[keys.AES_KEY], TEST_AES_KEY)
    monkeypatch.setenv(keys.ENV_VARS[keys.ENC_SALT], TEST_ENC_SALT)
    keys.reset_store()
    yield
    keys.reset_store()
