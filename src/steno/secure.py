"""Encryption at rest for biometric data (voice profiles, session embeddings).

On Windows the data is protected with DPAPI (``CryptProtectData``) in the
*current user* scope, so only the same Windows account on the same machine can
read it back. ``CRYPTPROTECT_LOCAL_MACHINE`` is deliberately never used. An
app-specific entropy blob is mixed in, so another program running as the same
user cannot decrypt the files by simply calling DPAPI.

Other platforms have no DPAPI. There ``protect`` raises ``SecureStorageError``
unless ``STENO_INSECURE_TEST=1`` is set, which stores plaintext behind a
distinct magic header (meant for tests and CI only). Plaintext files are never
accepted without that variable, even on Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Application-specific entropy for DPAPI. Not a secret; it scopes the blobs to Steno.
_ENTROPY = b"steno.voiceprint.v1"
_MAGIC_DPAPI = b"STENO-DPAPI1\n"
_MAGIC_PLAIN = b"STENO-PLAIN1\n"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1  # no LOCAL_MACHINE flag on purpose: current-user scope


class SecureStorageError(RuntimeError):
    """Encryption is unavailable or a protected file cannot be read."""


def insecure_allowed() -> bool:
    return os.environ.get("STENO_INSECURE_TEST") == "1"


# --------------------------------------------------------------------------- DPAPI


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    pblob = ctypes.POINTER(DataBlob)
    if encrypt:
        fn = crypt32.CryptProtectData
        fn.argtypes = [pblob, wintypes.LPCWSTR, pblob, ctypes.c_void_p, ctypes.c_void_p,
                       wintypes.DWORD, pblob]
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [pblob, ctypes.c_void_p, pblob, ctypes.c_void_p, ctypes.c_void_p,
                       wintypes.DWORD, pblob]
    fn.restype = wintypes.BOOL

    def make_blob(raw: bytes) -> tuple[DataBlob, object]:
        buf = ctypes.create_string_buffer(raw, len(raw))  # keep a reference alive
        return DataBlob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    blob_in, _keep_in = make_blob(data)
    blob_entropy, _keep_ent = make_blob(_ENTROPY)
    blob_out = DataBlob()
    if encrypt:
        ok = fn(ctypes.byref(blob_in), "Steno", ctypes.byref(blob_entropy), None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        ok = fn(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        err = ctypes.get_last_error()
        verb = "encrypt" if encrypt else "decrypt"
        raise SecureStorageError(
            f"Windows DPAPI could not {verb} the data (error {err}). Protected files only "
            "open for the Windows user and machine that created them."
        )
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# --------------------------------------------------------------------------- public API


def protect(data: bytes) -> bytes:
    """Encrypt ``data`` for the current user (or tag it plaintext in insecure-test mode)."""
    if insecure_allowed():
        return _MAGIC_PLAIN + data
    if sys.platform != "win32":
        raise SecureStorageError(
            "Encrypted storage needs Windows DPAPI. On other platforms set "
            "STENO_INSECURE_TEST=1 to store biometric data unencrypted (tests only)."
        )
    return _MAGIC_DPAPI + _dpapi(data, encrypt=True)


def unprotect(blob: bytes) -> bytes:
    if blob.startswith(_MAGIC_DPAPI):
        if sys.platform != "win32":
            raise SecureStorageError("This file was encrypted with Windows DPAPI.")
        return _dpapi(blob[len(_MAGIC_DPAPI):], encrypt=False)
    if blob.startswith(_MAGIC_PLAIN):
        if not insecure_allowed():
            raise SecureStorageError(
                "Refusing to read an unencrypted biometric file (STENO_INSECURE_TEST is not set)."
            )
        return blob[len(_MAGIC_PLAIN):]
    raise SecureStorageError("Not a Steno protected file (unknown header).")


def write_protected(path: Path, data: bytes) -> None:
    """Encrypt and write atomically (temp file + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(protect(data))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_protected(path: Path) -> bytes:
    return unprotect(Path(path).read_bytes())
