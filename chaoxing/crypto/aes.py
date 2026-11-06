"""超星登录用 AES：AES-128-CBC / PKCS7 / base64，key 与 iv 相同。

key 不写死在源码里，由 :mod:`chaoxing.crypto.keys` 运行时解析
（环境变量 → 本地缓存 → 探测登录页 JS 里的 transferKey）。
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .keys import AES_KEY, get_secret


def key() -> bytes:
    """当前生效的 AES key（16 字节）。"""
    return get_secret(AES_KEY).encode("utf-8")


def _cipher(secret: bytes | None = None) -> Cipher:
    k = secret if secret is not None else key()
    return Cipher(algorithms.AES(k), modes.CBC(k))


def encrypt(plaintext: str, *, secret: bytes | None = None) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = _cipher(secret).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")


def decrypt(ciphertext_b64: str, *, secret: bytes | None = None) -> str:
    raw = base64.b64decode(ciphertext_b64)
    dec = _cipher(secret).decryptor()
    data = dec.update(raw) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode("utf-8")
