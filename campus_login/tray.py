"""极轻量 Windows 系统托盘（只用标准库 ctypes 调用 Shell_NotifyIcon）。

不依赖 pystray / PyQt / Pillow，打包体积小、内存占用低。
菜单：
    校园网自动登录
    ● 已认证
    ──────────
    立即检查 / 立即登录 / 打开登录页面 / 查看日志 / 设置 / 开机自动运行 ✓ / 退出
"""

from __future__ import annotations

import ctypes
import sys
import threading
import traceback
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import icon_path

# ---------------------------------------------------------------- 常量
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_APP_STATUS = WM_APP + 1
WM_TRAYICON = WM_APP + 20

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_NONE = 0x00000000
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003

MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_CHECKED = 0x00000008
MF_SEPARATOR = 0x00000800

TPM_RIGHTBUTTON = 0x0002
TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

CMD_STATUS = 1000
CMD_CHECK = 1001
CMD_LOGIN = 1002
CMD_OPEN_PORTAL = 1003
CMD_VIEW_LOG = 1004
CMD_SETTINGS = 1005
CMD_AUTOSTART = 1006
CMD_EXIT = 1007

# 托盘窗口的固定标题：让 --quit 能找到正在运行的实例
TRAY_WINDOW_TITLE = "CampusLoginTrayWindow"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


HICON = getattr(wintypes, "HICON", wintypes.HANDLE)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", HICON),
    ]


@dataclass
class MenuItem:
    id: int
    label: str
    checked: bool = False
    enabled: bool = True
    separator: bool = False


def hide_console_window() -> None:
    """隐藏当前进程的控制台窗口（用 python.exe 启动时也能安静地待在后台）。"""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


class TrayIcon:
    """系统托盘图标。run() 会阻塞在消息循环里（应在主线程调用）。"""

    def __init__(
        self,
        title: str = "校园网自动登录",
        icon: str | Path | None = None,
        menu_provider: Callable[[], list[MenuItem]] | None = None,
        on_command: Callable[[int], None] | None = None,
    ) -> None:
        self.title = title
        self.icon_file = Path(icon) if icon else icon_path()
        self.menu_provider = menu_provider
        self.on_command = on_command
        self.hwnd: int | None = None
        self._tooltip = title
        self._icon_handle = None
        self._owns_icon = False
        self._ready = threading.Event()
        self._thread_id: int | None = None
        self._user32 = None
        self._kernel32 = None
        self._taskbar_created = 0
        self._class_name = f"CampusLoginTray_{id(self)}"
        self._wnd_proc = WNDPROC(self._on_message)
        self._nid = NOTIFYICONDATAW()
        self._stored_balloon: tuple[str, str, int] | None = None

    # ------------------------------------------------------------------
    def _libs(self):
        if self._user32 is None:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            u = self._user32
            u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
            u.RegisterClassExW.restype = wintypes.ATOM
            u.CreateWindowExW.argtypes = [
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
            ]
            u.CreateWindowExW.restype = wintypes.HWND
            u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            u.DefWindowProcW.restype = ctypes.c_ssize_t
            u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            u.GetMessageW.restype = ctypes.c_int
            u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            u.DispatchMessageW.restype = ctypes.c_ssize_t
            u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            u.PostQuitMessage.argtypes = [ctypes.c_int]
            u.DestroyWindow.argtypes = [wintypes.HWND]
            u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            u.LoadImageW.restype = wintypes.HANDLE
            u.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
            u.LoadIconW.restype = HICON
            u.CreatePopupMenu.restype = wintypes.HMENU
            u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
            u.TrackPopupMenu.argtypes = [
                wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
            ]
            u.TrackPopupMenu.restype = ctypes.c_int
            u.DestroyMenu.argtypes = [wintypes.HMENU]
            u.SetForegroundWindow.argtypes = [wintypes.HWND]
            u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
            u.RegisterWindowMessageW.restype = wintypes.UINT

            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            self._shell32 = shell32

            self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            self._kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        return self._user32, self._shell32

    # ------------------------------------------------------------------
    def _load_icon(self):
        user32, _ = self._libs()
        if self.icon_file and Path(self.icon_file).exists():
            handle = user32.LoadImageW(
                None, str(self.icon_file), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            if handle:
                self._owns_icon = True
                return handle
            log.warning("托盘图标文件存在但加载失败（%s），改用系统默认图标", self.icon_file)
        else:
            # 这条日志能帮上大忙：打包后如果图标路径不对，托盘就会显示系统默认图标
            log.warning("未找到托盘图标文件（%s），改用系统默认图标", self.icon_file)
        return user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

    def _create_window(self) -> bool:
        user32, _ = self._libs()
        hinstance = self._kernel32.GetModuleHandleW(None)
        self._icon_handle = self._load_icon()

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wnd_proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = self._icon_handle
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self._class_name
        wc.hIconSm = self._icon_handle
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            # 已注册（理论上不会发生，类名带进程内唯一后缀）
            pass
        hwnd = user32.CreateWindowExW(
            0, self._class_name, TRAY_WINDOW_TITLE, 0, 0, 0, 0, 0, None, None, hinstance, None
        )
        if not hwnd:
            return False
        self.hwnd = hwnd
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        return True

    # ------------------------------------------------------------------
    def _fill_nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = self._nid
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAYICON
        if flags & NIF_ICON and self._icon_handle:
            nid.hIcon = self._icon_handle
        if flags & NIF_TIP:
            nid.szTip = (self._tooltip or self.title)[:127]
        return nid

    def _add_icon(self) -> bool:
        _, shell32 = self._libs()
        nid = self._fill_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            # 图标可能已存在（例如资源管理器重启后），先删再加
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        return True

    def _remove_icon(self) -> None:
        try:
            _, shell32 = self._libs()
            nid = self._fill_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def set_tooltip(self, text: str) -> None:
        """线程安全地更新托盘提示文字。"""
        self._tooltip = (text or self.title)[:127]
        self._notify_modify()

    def _notify_modify(self) -> None:
        try:
            _, shell32 = self._libs()
            if not self.hwnd:
                return
            nid = self._fill_nid(NIF_TIP)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def notify(self, title: str, text: str, level: int = NIIF_INFO) -> None:
        """弹出气泡通知（线程安全）。"""
        try:
            _, shell32 = self._libs()
            if not self.hwnd:
                self._stored_balloon = (title, text, level)
                return
            nid = self._fill_nid(NIF_INFO)
            nid.szInfoTitle = (title or self.title)[:63]
            nid.szInfo = (text or "")[:255]
            nid.dwInfoFlags = level
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _show_menu(self) -> None:
        user32, _ = self._libs()
        items = self.menu_provider() if self.menu_provider else _default_menu()
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for item in items:
                if item.separator:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING
                if item.checked:
                    flags |= MF_CHECKED
                if not item.enabled:
                    flags |= MF_GRAYED
                user32.AppendMenuW(menu, flags, item.id, item.label)
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self.hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON | TPM_LEFTALIGN | TPM_BOTTOMALIGN,
                point.x,
                point.y,
                0,
                self.hwnd,
                None,
            )
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
        if command:
            self._dispatch(command)

    def _dispatch(self, command: int) -> None:
        if command in (CMD_STATUS, 0):
            return
        if self.on_command is None:
            return
        try:
            self.on_command(int(command))
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------
    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if self._taskbar_created and msg == self._taskbar_created:
                self._add_icon()
                return 0
            if msg == WM_TRAYICON:
                event = lparam & 0xFFFF
                if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif event == WM_LBUTTONDBLCLK:
                    self._dispatch(CMD_CHECK)
                return 0
            if msg == WM_APP_STATUS:
                self._notify_modify()
                return 0
            if msg == WM_COMMAND:
                self._dispatch(wparam & 0xFFFF)
                return 0
            if msg == WM_CLOSE:
                # 真正销毁窗口 → 触发 WM_DESTROY（删除托盘图标 + 结束消息循环）。
                # 注意：这里绝对不能再去 PostMessage(WM_CLOSE)，否则会陷入死循环，
                # 表现就是“点了退出但程序关不掉”。
                if not self._user32.DestroyWindow(hwnd):
                    self._user32.PostQuitMessage(0)
                return 0
            if msg == WM_DESTROY:
                self._remove_icon()
                self.hwnd = None
                self._user32.PostQuitMessage(0)
                return 0
        except Exception:
            traceback.print_exc()
        return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------------
    def run(
        self,
        ready: threading.Event | None = None,
        icon_retries: int = 3,
        icon_retry_interval: float = 5.0,
    ) -> None:
        """创建托盘图标并进入消息循环（阻塞）。

        开机时资源管理器可能还没准备好，导致第一次 Shell_NotifyIcon 失败，
        所以这里会重试几次（默认 3 次、每次隔 5 秒）。
        """
        import time as _time

        try:
            self._libs()
        except Exception as exc:
            raise RuntimeError(f"当前环境不支持系统托盘：{exc}") from exc
        if not self._create_window():
            raise RuntimeError("创建托盘窗口失败")
        if not self._add_icon():
            added = False
            for _ in range(max(0, int(icon_retries))):
                _time.sleep(max(0.5, float(icon_retry_interval)))
                if self._add_icon():
                    added = True
                    break
            if not added:
                raise RuntimeError(
                    "无法创建托盘图标（Shell_NotifyIcon 失败）："
                    "可能是资源管理器未运行，或当前进程不在交互式桌面上"
                )
        if self._stored_balloon:
            title, text, level = self._stored_balloon
            self._stored_balloon = None
            self.notify(title, text, level)
        if ready:
            ready.set()

        msg = wintypes.MSG()
        while True:
            result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))
        self._remove_icon()

    def stop(self) -> None:
        """通知消息循环退出（线程安全）。"""
        try:
            if self.hwnd and self._user32:
                self._user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass

    def pump_until_close(self) -> None:
        """在当前线程上泵消息，直到窗口被销毁。

        用于“托盘图标创建失败（例如资源管理器没运行）时降级为后台运行”的场景，
        这样 --quit 依然能把这个进程关掉。
        """
        if not self.hwnd or not self._user32:
            return
        message = wintypes.MSG()
        while True:
            result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result in (0, -1):
                break
            self._user32.TranslateMessage(ctypes.byref(message))
            self._user32.DispatchMessageW(ctypes.byref(message))


def _default_menu() -> list[MenuItem]:
    return [
        MenuItem(CMD_STATUS, "校园网自动登录", enabled=False),
        MenuItem(CMD_EXIT, "退出"),
    ]


def running_window_handle() -> int | None:
    """找到正在运行的托盘窗口（用于 --quit）。"""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        handle = user32.FindWindowW(None, TRAY_WINDOW_TITLE)
        return int(handle) if handle else None
    except Exception:
        return None


def request_quit() -> bool:
    """通知正在运行的托盘程序退出。返回 True 表示找到了实例。"""
    handle = running_window_handle()
    if not handle:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        return bool(user32.PostMessageW(wintypes.HWND(handle), WM_CLOSE, 0, 0))
    except Exception:
        return False
