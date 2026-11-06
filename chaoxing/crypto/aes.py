"""超星登录用 AES：AES-128-CBC / PKCS7 / base64，key 与 iv 相同。"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_KEY = b"u2oh6Vu^HWe4_AES"


def _cipher() -> Cipher:
    return Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY))


def encrypt(plaintext: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = _cipher().encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")


def decrypt(ciphertext_b64: str) -> str:
    raw = base64.b64decode(ciphertext_b64)
    dec = _cipher().decryptor()
    data = dec.update(raw) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode("utf-8")
