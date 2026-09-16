"""单实例保护：避免重复启动出现多个托盘图标 / 多次登录。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """基于 Windows 命名互斥体（Mutex）的单实例锁。"""

    def __init__(self, name: str = "CampusLogin.SingleInstance") -> None:
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        """返回 True 表示本进程是第一个实例。"""
        if os.name != "nt":
            return True
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.SetLastError(0)
            handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
            if not handle:
                return True
            self._handle = handle
            return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
        except Exception:
            return True

    def release(self) -> None:
        if self._handle and os.name == "nt":
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.ReleaseMutex(self._handle)
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
