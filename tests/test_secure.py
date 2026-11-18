from __future__ import annotations

import sys

import pytest

from steno import secure


def test_roundtrip_current_platform(tmp_path):
    if sys.platform != "win32" and not secure.insecure_allowed():
        pytest.skip("needs Windows DPAPI or STENO_INSECURE_TEST=1")
    path = tmp_path / "x.bin"
    secure.write_protected(path, b"secret embedding bytes")
    raw = path.read_bytes()
    assert b"secret embedding bytes" in raw or sys.platform == "win32"
    assert secure.read_protected(path) == b"secret embedding bytes"
    assert not (tmp_path / "x.bin.tmp").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_really_encrypts_and_is_entropy_bound(monkeypatch):
    monkeypatch.delenv("STENO_INSECURE_TEST", raising=False)
    blob = secure.protect(b"voiceprint-bytes-0123456789")
    assert blob.startswith(b"STENO-DPAPI1")
    assert b"voiceprint-bytes" not in blob
    assert secure.unprotect(blob) == b"voiceprint-bytes-0123456789"
    # A blob made without Steno's entropy must not decrypt through our API.
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    data = b"no-entropy"
    buf = ctypes.create_string_buffer(data, len(data))
    inp = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    crypt32 = ctypes.WinDLL("crypt32")
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    assert crypt32.CryptProtectData(ctypes.byref(inp), "t", None, None, None, 1,
                                    ctypes.byref(out))
    foreign = b"STENO-DPAPI1\n" + ctypes.string_at(out.pbData, out.cbData)
    with pytest.raises(secure.SecureStorageError):
        secure.unprotect(foreign)


def test_plaintext_only_with_explicit_env(monkeypatch):
    monkeypatch.setenv("STENO_INSECURE_TEST", "1")
    blob = secure.protect(b"abc")
    assert blob.startswith(b"STENO-PLAIN1")
    assert secure.unprotect(blob) == b"abc"
    monkeypatch.delenv("STENO_INSECURE_TEST")
    with pytest.raises(secure.SecureStorageError, match="unencrypted"):
        secure.unprotect(blob)


def test_unknown_header_rejected():
    with pytest.raises(secure.SecureStorageError):
        secure.unprotect(b"hello")


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_non_windows_without_env_raises(monkeypatch):
    monkeypatch.delenv("STENO_INSECURE_TEST", raising=False)
    with pytest.raises(secure.SecureStorageError, match="DPAPI"):
        secure.protect(b"x")
