"""Windows 网卡信息（GetAdaptersAddresses 的 ctypes 封装）。

用途：ePortal 的登录参数里有 wlan_user_ip（本机在校园网里的 IP）。如果电脑上装了
Clash / FlClash 这类 TUN 模式代理，默认路由会被虚拟网卡接管，取到的“本机 IP”会变成
198.18.x.x 这种虚拟地址，填进 wlan_user_ip 会导致认证失败。

所以这里枚举网卡类型，优先选择“以太网 / Wi-Fi”这类真实网卡的地址。

只读取网卡列表，不修改任何系统设置，也不需要管理员权限。
"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
from dataclasses import dataclass, field
from ctypes import wintypes

log = logging.getLogger("campus_login.winiface")

AF_INET = 2
AF_UNSPEC = 0
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008
ERROR_BUFFER_OVERFLOW = 111

IF_OPER_STATUS_UP = 1
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_ETHERNET = 6
IF_TYPE_WIFI = 71
IF_TYPE_PPP = 23
IF_TYPE_PROP_VIRTUAL = 53   # Clash / FlClash 等 TUN 网卡的类型
IF_TYPE_TUNNEL = 131


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class SOCKADDR_IN(ctypes.Structure):
    _fields_ = [
        ("sin_family", ctypes.c_ushort),
        ("sin_port", ctypes.c_ushort),
        ("sin_addr", ctypes.c_ubyte * 4),
        ("sin_zero", ctypes.c_ubyte * 8),
    ]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_ulong),
]


@dataclass
class Interface:
    index: int
    name: str
    description: str
    addresses: list[str] = field(default_factory=list)
    if_type: int = 0
    up: bool = False

    @property
    def is_loopback(self) -> bool:
        return self.if_type == IF_TYPE_SOFTWARE_LOOPBACK

    @property
    def is_tunnel(self) -> bool:
        return self.if_type == IF_TYPE_TUNNEL

    @property
    def is_virtual(self) -> bool:
        """虚拟网卡（TUN 代理等）：里面的地址不是校园网真实地址。"""
        return self.if_type in (IF_TYPE_PROP_VIRTUAL, IF_TYPE_TUNNEL)

    @property
    def is_physical(self) -> bool:
        """真实网卡：以太网 / Wi-Fi。"""
        return self.if_type in (IF_TYPE_ETHERNET, IF_TYPE_WIFI)


_iphlpapi = None


def available() -> bool:
    return os.name == "nt"


def _lib():
    global _iphlpapi
    if _iphlpapi is None:
        if not available():
            raise OSError("网卡枚举仅支持 Windows")
        _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        _iphlpapi.GetAdaptersAddresses.argtypes = [
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.POINTER(IP_ADAPTER_ADDRESSES),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        _iphlpapi.GetAdaptersAddresses.restype = ctypes.c_ulong
    return _iphlpapi


def _unicast_ipv4(address) -> str | None:
    if not address.lpSockaddr:
        return None
    sockaddr = ctypes.cast(address.lpSockaddr, ctypes.POINTER(SOCKADDR_IN)).contents
    if sockaddr.sin_family != AF_INET:
        return None
    return socket.inet_ntoa(bytes(sockaddr.sin_addr))


def list_interfaces() -> list[Interface]:
    """列出本机所有网卡（含虚拟网卡）。失败时返回空列表，绝不抛异常给调用方。"""
    if not available():
        return []
    try:
        api = _lib()
        flags = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER
        size = ctypes.c_ulong(15000)
        buffer = ctypes.create_string_buffer(size.value)
        result = api.GetAdaptersAddresses(
            AF_INET, flags, None, ctypes.cast(buffer, ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
            ctypes.byref(size),
        )
        if result == ERROR_BUFFER_OVERFLOW:
            buffer = ctypes.create_string_buffer(size.value)
            result = api.GetAdaptersAddresses(
                AF_INET, flags, None,
                ctypes.cast(buffer, ctypes.POINTER(IP_ADAPTER_ADDRESSES)), ctypes.byref(size),
            )
        if result != 0:
            log.debug("GetAdaptersAddresses 返回错误码 %s", result)
            return []

        interfaces: list[Interface] = []
        current = ctypes.cast(buffer, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
        while current:
            adapter = current.contents
            item = Interface(
                index=int(adapter.IfIndex),
                name=adapter.FriendlyName or "",
                description=adapter.Description or "",
                if_type=int(adapter.IfType),
                up=int(adapter.OperStatus) == IF_OPER_STATUS_UP,
            )
            address = adapter.FirstUnicastAddress
            while address:
                ip = _unicast_ipv4(address.contents.Address)
                if ip:
                    item.addresses.append(ip)
                address = address.contents.Next
            interfaces.append(item)
            current = adapter.Next
        return interfaces
    except Exception as exc:  # 任何异常都不能影响程序运行
        log.debug("枚举网卡失败：%s", type(exc).__name__)
        return []


def interface_index_for_ip(address: str) -> int | None:
    """返回拥有该 IPv4 地址的网卡接口索引。"""
    if not address:
        return None
    for item in list_interfaces():
        if address in item.addresses:
            return item.index
    return None


def physical_ipv4_addresses() -> list[str]:
    """返回所有“真实网卡（以太网 / Wi-Fi）”上的 IPv4 地址。"""
    addresses: list[str] = []
    for item in list_interfaces():
        if not item.up or item.is_loopback or not item.is_physical:
            continue
        for address in item.addresses:
            if address not in addresses:
                addresses.append(address)
    return addresses
