"""加密常量运行时解析的离线单测：解析顺序、缓存、探测与校验。"""

from __future__ import annotations

import httpx
import pytest

from chaoxing.crypto import aes, enc, keys

LOGIN_PAGE = (
    "<html><head>"
    '<script src="https://passport2-static.chaoxing.com/js/fanya/login.js?v=20261013"></script>'
    "</head><body>login</body></html>"
)
LOGIN_JS = 'var transferKey = "abcdefgh12345678";function encryptByAES(pwd,key){}'
FAKE_SALT = "Aa1!bb_Xx9"
BUNDLE = (
    "var arr=['8px','video_html5_api','sp_ppt_pic_fullScreen1','" + FAKE_SALT + "',"
    "'a.sp_video_pic_dele2'];Ext['String']['format'](tpl,1,2,3,4,5,6,'" + FAKE_SALT + "');"
)
AMBIGUOUS_BUNDLE = "var arr=['Aa1!bb_Xx9','Zz9$cc~2y']"


def _client(routes: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        for needle, body in routes.items():
            if needle in str(request.url):
                return httpx.Response(200, text=body)
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _store(tmp_path) -> keys.KeyStore:
    return keys.KeyStore(tmp_path)


def _clear_env(monkeypatch) -> None:
    for env in keys.ENV_VARS.values():
        monkeypatch.delenv(env, raising=False)


# ---------------------------------------------------------------- 纯函数
def test_mask():
    assert keys.mask("") == ""
    assert keys.mask("abc") == "a***"
    assert keys.mask("0123456789abcdef") == "01***ef"


def test_extract_transfer_key_from_js():
    assert keys.extract_transfer_key(LOGIN_JS) == "abcdefgh12345678"
    assert keys.extract_transfer_key("var x='nothing here';") is None


def test_salt_candidates_skips_selectors():
    found = keys.salt_candidates(BUNDLE)
    assert found == [FAKE_SALT]


def test_salt_candidates_dedup_and_empty():
    assert keys.salt_candidates("") == []
    assert keys.salt_candidates("var a=['video_html5_api','8px'];") == []


# ---------------------------------------------------------------- 解析顺序
def test_env_wins(monkeypatch, tmp_path):
    store = _store(tmp_path)
    store.set(keys.ENC_SALT, "cache-salt-1")
    assert store.peek(keys.ENC_SALT) == "test-salt-9"
    assert store.source(keys.ENC_SALT) == "env"
    assert store.get(keys.ENC_SALT) == "test-salt-9"


def test_cache_used_without_env(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    store.set(keys.ENC_SALT, "cache-salt-1", source="manual")
    assert store.source(keys.ENC_SALT) == "cache"
    assert store.get(keys.ENC_SALT) == "cache-salt-1"
    assert store.get(keys.ENC_SALT, allow_probe=False) == "cache-salt-1"


def test_missing_raises_with_commands(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    with pytest.raises(keys.KeyMissing) as info:
        store.get(keys.ENC_SALT, allow_probe=False)
    message = str(info.value)
    assert "cx crypto probe" in message
    assert "cx crypto set enc_salt" in message
    assert "CX_ENC_SALT" in message


def test_corrupt_cache_is_dropped(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"enc_salt": {"value": "   "}}', encoding="utf-8")
    with pytest.raises(keys.KeyMissing):
        store.get(keys.ENC_SALT, allow_probe=False)


# ---------------------------------------------------------------- 写入与校验
def test_set_then_clear(tmp_path):
    store = _store(tmp_path)
    store.set(keys.AES_KEY, "0011223344556677")
    assert store.cached(keys.AES_KEY) == "0011223344556677"
    assert store.clear(keys.AES_KEY) == [keys.AES_KEY]
    assert store.cached(keys.AES_KEY) is None
    assert store.clear() == []


def test_set_rejects_bad_lengths(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(keys.BadSecret):
        store.set(keys.AES_KEY, "too-short")
    with pytest.raises(keys.BadSecret):
        store.set(keys.ENC_SALT, "x" * 40)
    with pytest.raises(keys.BadSecret):
        store.set("nope", "value")


def test_report_masks_values(tmp_path):
    store = _store(tmp_path)
    report = store.report()
    assert report[keys.AES_KEY]["present"] is True
    assert report[keys.AES_KEY]["masked"] != "test-aes-key-000"
    assert report[keys.AES_KEY]["source"] == "env"


# ---------------------------------------------------------------- 探测
def test_probe_aes_from_login_js(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    client = _client({"/js/fanya/login.js": LOGIN_JS, "passport2.chaoxing.com": LOGIN_PAGE})
    assert store.probe(keys.AES_KEY, client=client) == "abcdefgh12345678"


def test_probe_salt_from_bundle(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    client = _client({"videojs-ext": BUNDLE})
    assert store.probe(keys.ENC_SALT, client=client) == FAKE_SALT


def test_probe_salt_ambiguous_lists_candidates(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    client = _client({"videojs-ext": AMBIGUOUS_BUNDLE})
    with pytest.raises(keys.KeyMissing) as info:
        store.probe(keys.ENC_SALT, client=client)
    assert "候选" in str(info.value)


def test_probe_failure_reports_http_error(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    client = _client({})
    with pytest.raises(keys.KeyMissing):
        store.probe(keys.AES_KEY, client=client)


def test_get_caches_probe_result(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = _store(tmp_path)
    client = _client({"videojs-ext": BUNDLE})
    monkeypatch.setattr(store, "probe", lambda name, client=None: FAKE_SALT)
    assert store.get(keys.ENC_SALT) == FAKE_SALT
    assert store.cached(keys.ENC_SALT) == FAKE_SALT
    assert store.source(keys.ENC_SALT) == "cache"


# ---------------------------------------------------------------- AES / enc 集成
def test_aes_uses_resolved_key():
    assert aes.key() == b"0123456789abcdef"
    assert aes.decrypt(aes.encrypt("p@ssw0rd!中文")) == "p@ssw0rd!中文"


def test_aes_explicit_secret_skips_resolver():
    secret = b"ffffffffffffffff"
    assert aes.decrypt(aes.encrypt("x", secret=secret), secret=secret) == "x"


def test_enc_uses_resolved_salt():
    got = enc.enc_v2("1234", "88", "job-1", "obj-1", 60, 600)
    expect = enc.md5("[1234][88][job-1][obj-1][60000][test-salt-9][600000][0_600]")
    assert got == expect


def test_enc_salt_override_changes_result():
    default = enc.enc_v2("1", "2", "3", "4", 10, 100)
    other = enc.enc_v2("1", "2", "3", "4", 10, 100, salt="other-salt")
    assert default != other
    assert enc.candidate_encs("1", "2", "3", "4", 10, 100)["v2"] == default
