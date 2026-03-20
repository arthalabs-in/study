"""
Secure storage helpers for small at-rest secrets and user content.

Uses Windows DPAPI when available. On other platforms, uses a Fernet key stored
in the OS keyring when possible, with a local owner-only key file fallback.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from ctypes import POINTER, Structure, byref, cast
from ctypes import wintypes

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover - optional import during partial installs
    Fernet = None
    InvalidToken = Exception


_DPAPI_PREFIX = "dpapi:"
_FERNET_PREFIX = "fernet:"
_KEY_SERVICE = "study-tui"
_KEY_NAME = "content-encryption-key"
_KEY_FILE = Path.home() / ".study-tui" / "content.key"


class DATA_BLOB(Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(wintypes.BYTE))]


def _bytes_to_blob(data: bytes) -> DATA_BLOB:
    if not data:
        return DATA_BLOB(0, POINTER(wintypes.BYTE)())
    buffer = (wintypes.BYTE * len(data))(*data)
    return DATA_BLOB(len(data), cast(buffer, POINTER(wintypes.BYTE)))


def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
    if not blob.cbData:
        return b""
    return bytes(cast(blob.pbData, POINTER(wintypes.BYTE * blob.cbData)).contents)


def _crypt_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")

    from ctypes import windll

    crypt32 = windll.crypt32
    kernel32 = windll.kernel32
    in_blob = _bytes_to_blob(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(byref(in_blob), None, None, None, None, 0, byref(out_blob)):
        raise OSError("CryptProtectData failed")
    try:
        return _blob_to_bytes(out_blob)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def _crypt_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")

    from ctypes import windll

    crypt32 = windll.crypt32
    kernel32 = windll.kernel32
    in_blob = _bytes_to_blob(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(byref(in_blob), None, None, None, None, 0, byref(out_blob)):
        raise OSError("CryptUnprotectData failed")
    try:
        return _blob_to_bytes(out_blob)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def encrypt_text(value: str) -> str:
    if not value:
        return value
    if sys.platform == "win32":
        try:
            protected = _crypt_protect(value.encode("utf-8"))
            return _DPAPI_PREFIX + base64.b64encode(protected).decode("ascii")
        except Exception:
            pass

    fernet = _get_fernet()
    if fernet is None:
        return value
    try:
        token = fernet.encrypt(value.encode("utf-8"))
    except Exception:
        return value
    return _FERNET_PREFIX + token.decode("ascii")


def decrypt_text(value: str) -> str:
    if not value:
        return value
    if value.startswith(_DPAPI_PREFIX):
        if sys.platform != "win32":
            return ""
        try:
            payload = base64.b64decode(value[len(_DPAPI_PREFIX):])
            return _crypt_unprotect(payload).decode("utf-8")
        except Exception:
            return ""
    if value.startswith(_FERNET_PREFIX):
        fernet = _get_fernet()
        if fernet is None:
            return ""
        try:
            payload = value[len(_FERNET_PREFIX):].encode("ascii")
            return fernet.decrypt(payload).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            return ""
        except Exception:
            return ""
    return value


def _get_keyring():
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring


def _read_key_file() -> bytes | None:
    try:
        raw = _KEY_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return raw.encode("ascii") if raw else None


def _write_key_file(key: bytes) -> None:
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_text(key.decode("ascii"), encoding="utf-8")
    try:
        os.chmod(_KEY_FILE, 0o600)
    except Exception:
        pass


def _load_or_create_fernet_key() -> bytes | None:
    if Fernet is None:
        return None

    keyring = _get_keyring()
    if keyring is not None:
        try:
            stored = keyring.get_password(_KEY_SERVICE, _KEY_NAME) or ""
        except Exception:
            stored = ""
        if stored:
            return stored.encode("ascii")

    file_key = _read_key_file()
    if file_key:
        return file_key

    key = Fernet.generate_key()
    if keyring is not None:
        try:
            keyring.set_password(_KEY_SERVICE, _KEY_NAME, key.decode("ascii"))
            return key
        except Exception:
            pass

    try:
        _write_key_file(key)
        return key
    except Exception:
        return None


def _get_fernet():
    key = _load_or_create_fernet_key()
    if not key or Fernet is None:
        return None
    try:
        return Fernet(key)
    except Exception:
        return None
