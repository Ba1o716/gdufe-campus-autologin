"""Windows 凭据管理器（Credential Manager）封装，仅使用标准库 ctypes。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168

_advapi32 = None


class CredentialError(OSError):
    """凭据管理器调用失败。"""


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def available() -> bool:
    return os.name == "nt"


def _lib():
    global _advapi32
    if _advapi32 is None:
        if not available():
            raise CredentialError("Windows 凭据管理器仅适用于 Windows")
        _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        _advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        _advapi32.CredWriteW.restype = wintypes.BOOL
        _advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        _advapi32.CredReadW.restype = wintypes.BOOL
        _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        _advapi32.CredDeleteW.restype = wintypes.BOOL
        _advapi32.CredFree.argtypes = [ctypes.c_void_p]
        _advapi32.CredFree.restype = None
    return _advapi32


def write(target: str, username: str, secret: bytes, comment: str = "") -> None:
    advapi32 = _lib()
    buffer = ctypes.create_string_buffer(secret, len(secret))
    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = comment
    cred.CredentialBlobSize = len(secret)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = username or "CampusLogin"
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise CredentialError("CredWriteW 失败", ctypes.get_last_error())


def read(target: str):
    """返回 (用户名, 二进制内容)，不存在时返回 None。"""
    advapi32 = _lib()
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise CredentialError("CredReadW 失败", error)
    try:
        cred = pointer.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return (cred.UserName or "", blob)
    finally:
        advapi32.CredFree(pointer)


def delete(target: str) -> bool:
    advapi32 = _lib()
    if advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == ERROR_NOT_FOUND:
        return False
    raise CredentialError("CredDeleteW 失败", error)
