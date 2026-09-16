"""Windows DPAPI 封装（CryptProtectData / CryptUnprotectData）。

只使用标准库 ctypes，不引入 pywin32 / pycryptodome 等依赖。
加密后的数据只能被“同一台电脑上的同一个 Windows 用户”解密。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x01
CRYPTPROTECT_LOCAL_MACHINE = 0x04

_crypt32 = None
_kernel32 = None


class DpapiError(OSError):
    """DPAPI 调用失败。"""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return os.name == "nt"


def _libs():
    global _crypt32, _kernel32
    if _crypt32 is None:
        if not available():
            raise DpapiError("DPAPI 仅适用于 Windows")
        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_Blob),
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_Blob),
        ]
        _crypt32.CryptProtectData.restype = wintypes.BOOL
        _crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_Blob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_Blob),
        ]
        _crypt32.CryptUnprotectData.restype = wintypes.BOOL
        _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        _kernel32.LocalFree.restype = wintypes.HLOCAL
    return _crypt32, _kernel32


def _make_blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _read_blob(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect(data: bytes, entropy: bytes | None = None, machine_scope: bool = False) -> bytes:
    """用当前 Windows 用户（或本机）的密钥加密数据。"""
    crypt32, kernel32 = _libs()
    blob_in, _keep_in = _make_blob(data)
    if entropy:
        blob_entropy, _keep_entropy = _make_blob(entropy)
        entropy_ptr = ctypes.byref(blob_entropy)
    else:
        entropy_ptr = None
    blob_out = _Blob()
    flags = CRYPTPROTECT_UI_FORBIDDEN | (CRYPTPROTECT_LOCAL_MACHINE if machine_scope else 0)
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "CampusLogin credentials",
        entropy_ptr,
        None,
        None,
        flags,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise DpapiError("CryptProtectData 失败", ctypes.get_last_error())
    try:
        return _read_blob(blob_out)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def unprotect(data: bytes, entropy: bytes | None = None) -> bytes:
    """解密由 protect() 生成的数据。"""
    crypt32, kernel32 = _libs()
    blob_in, _keep_in = _make_blob(data)
    if entropy:
        blob_entropy, _keep_entropy = _make_blob(entropy)
        entropy_ptr = ctypes.byref(blob_entropy)
    else:
        entropy_ptr = None
    blob_out = _Blob()
    description = wintypes.LPWSTR()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        ctypes.byref(description),
        entropy_ptr,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise DpapiError("CryptUnprotectData 失败", ctypes.get_last_error())
    try:
        return _read_blob(blob_out)
    finally:
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
