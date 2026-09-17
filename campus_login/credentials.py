"""账号密码的安全保存与读取。

优先 Windows 凭据管理器（Credential Manager），失败时退回 DPAPI 加密文件。
两者都不需要管理员权限，且只有当前 Windows 用户可以解密。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from . import credman, dpapi
from .paths import credential_file

log = logging.getLogger("campus_login.credentials")

CRED_TARGET = "CampusLogin/Credentials"
_ENTROPY = b"CampusLogin-v1"


@dataclass
class Credential:
    username: str = ""
    password: str = ""

    def __post_init__(self) -> None:
        self.username = self.username or ""
        self.password = self.password or ""

    @property
    def complete(self) -> bool:
        return bool(self.username.strip() and self.password)

    def __repr__(self) -> str:  # 防止密码意外出现在日志 / 异常里
        return f"Credential(username={self.username!r}, password=***)"

    __str__ = __repr__

    def redact(self, text: str | None) -> str | None:
        """把文本中出现的密码替换为 ***。"""
        if not text:
            return text
        if self.password and len(self.password) >= 3:
            return text.replace(self.password, "***")
        return text


class CredentialStore:
    """凭据存储接口。"""

    name = "base"
    description = "凭据存储"

    def is_available(self) -> bool:
        return True

    def save(self, credential: Credential) -> None:
        raise NotImplementedError

    def load(self) -> Credential | None:
        raise NotImplementedError

    def delete(self) -> bool:
        raise NotImplementedError


class MemoryCredentialStore(CredentialStore):
    """仅用于测试 / 无法使用安全存储时的临时方案。"""

    name = "memory"
    description = "内存（仅测试，重启后失效）"

    def __init__(self, credential: Credential | None = None) -> None:
        self._credential = credential

    def save(self, credential: Credential) -> None:
        self._credential = Credential(credential.username, credential.password)

    def load(self) -> Credential | None:
        return self._credential

    def delete(self) -> bool:
        existed = self._credential is not None
        self._credential = None
        return existed


class DpapiCredentialStore(CredentialStore):
    """DPAPI 加密文件（%APPDATA%\\CampusLogin\\credentials.bin）。"""

    name = "dpapi"
    description = "DPAPI 加密文件"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or credential_file()

    def is_available(self) -> bool:
        return dpapi.available()

    def save(self, credential: Credential) -> None:
        payload = json.dumps(
            {"username": credential.username, "password": credential.password},
            ensure_ascii=False,
        ).encode("utf-8")
        blob = dpapi.protect(payload, _ENTROPY)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self.path)

    def load(self) -> Credential | None:
        if not self.path.exists():
            return None
        try:
            raw = dpapi.unprotect(self.path.read_bytes(), _ENTROPY)
            data = json.loads(raw.decode("utf-8"))
            return Credential(str(data.get("username", "")), str(data.get("password", "")))
        except Exception as exc:
            log.error("无法解密已保存的凭据（%s），请重新设置账号密码", type(exc).__name__)
            return None

    def delete(self) -> bool:
        if self.path.exists():
            try:
                self.path.unlink()
                return True
            except OSError as exc:
                log.warning("删除凭据文件失败：%s", exc)
        return False


class WindowsCredentialManagerStore(CredentialStore):
    """Windows 凭据管理器。"""

    name = "credman"
    description = "Windows 凭据管理器"

    def __init__(self, target: str = CRED_TARGET) -> None:
        self.target = target

    def is_available(self) -> bool:
        return credman.available()

    def save(self, credential: Credential) -> None:
        payload = json.dumps(
            {"username": credential.username, "password": credential.password},
            ensure_ascii=False,
        ).encode("utf-8")
        credman.write(self.target, credential.username or "CampusLogin", payload, "校园网自动登录")

    def load(self) -> Credential | None:
        try:
            found = credman.read(self.target)
        except credman.CredentialError as exc:
            log.error("读取凭据管理器失败：%s", exc)
            return None
        if not found:
            return None
        username, blob = found
        try:
            data = json.loads(blob.decode("utf-8"))
        except Exception:
            # 兼容“只存了密码”的情况
            return Credential(username, blob.decode("utf-8", errors="replace"))
        return Credential(str(data.get("username") or username), str(data.get("password", "")))

    def delete(self) -> bool:
        try:
            return credman.delete(self.target)
        except credman.CredentialError as exc:
            log.warning("删除凭据管理器条目失败：%s", exc)
            return False


def create_store(backend: str = "auto", logger: logging.Logger | None = None):
    """按配置选择凭据后端。返回 (store, 中文说明)。"""
    logger = logger or log
    backend = (backend or "auto").lower()

    if backend == "memory":
        store = MemoryCredentialStore()
        return store, store.description
    if backend == "dpapi":
        store = DpapiCredentialStore()
        return store, store.description
    if backend == "credman":
        if credman.available():
            store = WindowsCredentialManagerStore()
            return store, store.description
        logger.warning("当前系统不支持 Windows 凭据管理器，改用 DPAPI 加密文件")
        store = DpapiCredentialStore()
        return store, store.description

    # auto
    if not credman.available() and not dpapi.available():
        logger.warning("当前系统没有可用的安全凭据存储，暂用内存存储（重启后失效）")
        store = MemoryCredentialStore()
        return store, store.description
    store = AutoCredentialStore(logger)
    return store, store.description


class AutoCredentialStore(CredentialStore):
    """自动模式：写入优先用 Windows 凭据管理器，读取时两个地方都看一眼。

    这样可以避免“某次保存写进了 DPAPI 文件、另一次却只从凭据管理器读”导致
    明明保存过却说没保存的情况。
    """

    name = "auto"
    description = "Windows 凭据管理器（读取时也会检查 DPAPI 加密文件）"

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.log = logger or log
        self.primary: CredentialStore = WindowsCredentialManagerStore()
        self.secondary: CredentialStore = DpapiCredentialStore()

    def is_available(self) -> bool:
        return credman.available() or dpapi.available()

    def save(self, credential: Credential) -> None:
        # 凭据管理器写失败时会自动退回 DPAPI 文件
        save_credential(self.primary, credential)

    def load(self) -> Credential | None:
        for store in (self.primary, self.secondary):
            try:
                found = store.load()
            except Exception as exc:
                self.log.debug("从 %s 读取凭据失败：%s", store.name, type(exc).__name__)
                continue
            if found and found.complete:
                return found
        return None

    def delete(self) -> bool:
        removed = False
        for store in (self.primary, self.secondary):
            try:
                removed = store.delete() or removed
            except Exception:
                continue
        return removed


def save_credential(store: CredentialStore, credential: Credential) -> CredentialStore:
    """保存凭据；凭据管理器写入失败时自动退回 DPAPI。"""
    try:
        store.save(credential)
        return store
    except Exception as exc:
        if isinstance(store, WindowsCredentialManagerStore) and dpapi.available():
            log.warning("写入 Windows 凭据管理器失败（%s），改用 DPAPI 加密文件", type(exc).__name__)
            fallback = DpapiCredentialStore()
            fallback.save(credential)
            return fallback
        raise
