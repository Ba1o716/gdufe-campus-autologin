"""校园网开机自动登录（Windows 本地程序）。

设计目标：
  * 轻量：核心逻辑只依赖 Python 标准库 + requests；
  * 稳定：所有网络请求都有超时，所有异常都被捕获并记录日志；
  * 可维护：校园网认证方式通过 Adapter（适配器）隔离，接口变化只改适配器；
  * 安全：密码只保存在 Windows 凭据管理器 / DPAPI 中，永不写入源码、配置、日志。
"""

from __future__ import annotations

APP_NAME = "CampusLogin"
APP_DISPLAY_NAME = "校园网自动登录"
APP_VERSION = "1.1.0"

__all__ = ["APP_NAME", "APP_DISPLAY_NAME", "APP_VERSION"]
