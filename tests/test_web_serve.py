"""启动器测试：端口挑选、已运行检测、URL 与口令生成。"""

from __future__ import annotations

import socket

import pytest

from chaoxing.web import serve


def test_is_loopback():
    assert serve.is_loopback("127.0.0.1")
    assert serve.is_loopback("localhost")
    assert serve.is_loopback("::1")
    assert not serve.is_loopback("0.0.0.0")
    assert not serve.is_loopback("192.168.1.10")


def test_port_free_and_pick_port_skips_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        busy.bind(("127.0.0.1", 0))
        taken = busy.getsockname()[1]
        busy.listen(1)
        assert not serve.port_free("127.0.0.1", taken)
        picked = serve.pick_port("127.0.0.1", taken)
        assert picked != taken
        assert serve.port_free("127.0.0.1", picked)

        with pytest.raises(OSError):
            serve.pick_port("127.0.0.1", taken, auto=False)


def test_pick_port_zero_returns_ephemeral():
    port = serve.pick_port("127.0.0.1", 0)
    assert 1024 < port <= 65535


def test_url_for_hides_wildcard_host():
    assert serve.url_for("0.0.0.0", 9000) == "http://127.0.0.1:9000/"
    assert serve.url_for("127.0.0.1", 9000, "abc") == "http://127.0.0.1:9000/?token=abc"


def test_new_token_is_random():
    a, b = serve.new_token(), serve.new_token()
    assert a != b and len(a) > 12


def test_probe_panel_returns_none_on_closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert serve.probe_panel("127.0.0.1", port, timeout=0.2) is None


def test_lan_urls_carry_token():
    urls = serve.lan_urls(9000, "tok")
    for url in urls:
        assert url.endswith(":9000/?token=tok")
        assert "127." not in url
