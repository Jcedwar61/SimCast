"""API keys at rest: encrypted for this Windows account with DPAPI (CryptProtectData), so config.json alone (a
backup, a copied profile folder, a file shared by mistake) doesn't give the keys away. Only this Windows user on
this PC can decrypt them.

protect(key) -> "dpapi:<base64>" on Windows; elsewhere (Linux tests, a Python without ctypes) the key unchanged.
unprotect(stored) -> the key: a "dpapi:" value decrypted, anything else as it is (settings from before 1.21, where
keys were stored as plain text, keep working and are encrypted at the next save). A value that can't be decrypted
(the settings copied from another PC or account) reads as no key, so SimCast asks for it again.
"""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"
_ENTROPY = b"SimCast API key"


def _crypt(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src, _keep = blob(data)
    ent, _keep2 = blob(_ENTROPY)
    out = BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if encrypt:
        ok = fn(ctypes.byref(src), "SimCast", ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.GetLastError(), "DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return hasattr(ctypes, "windll")
    except Exception:
        return False


def protect(key) -> str:
    key = str(key or "")
    if not key or key.startswith(PREFIX) or not available():
        return key
    try:
        return PREFIX + base64.b64encode(_crypt(key.encode("utf-8"), True)).decode("ascii")
    except Exception:
        return key                       # never lose a key: keep it as it was rather than not at all


def unprotect(stored) -> str:
    s = str(stored or "")
    if not s.startswith(PREFIX):
        return s
    if not available():
        return ""
    try:
        return _crypt(base64.b64decode(s[len(PREFIX):]), False).decode("utf-8")
    except Exception:
        return ""
