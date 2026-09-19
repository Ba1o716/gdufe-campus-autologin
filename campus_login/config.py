"""配置读写。

配置文件位于 %APPDATA%\\CampusLogin\\config.json。
**密码不写入配置文件**，只保存在 Windows 凭据管理器 / DPAPI 加密文件中。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import config_path

log = logging.getLogger("campus_login.config")

# 默认的“认证状态探测”地址。
# 说明：这些地址被国内手机厂商 / 操作系统用于判断“是否需要网页认证”，
# 在校园网被劫持时会返回 302 跳转到认证页面，是判断是否需要认证的可靠信号。
# 使用 http（而不是 https），因为门户劫持通常发生在 80 端口。
DEFAULT_PROBE_URLS = [
    "http://connect.rom.miui.com/generate_204",
    "http://connectivitycheck.platform.hicloud.com/generate_204",
    "http://wifi.vivo.com.cn/generate_204",
    "http://captive.apple.com/hotspot-detect.html",
    "http://www.msftconnecttest.com/connecttest.txt",
]

# 页面/响应中出现这些关键词 → 需要人工处理（验证码、短信验证、二次认证）。
DEFAULT_CAPTCHA_KEYWORDS = [
    "验证码",
    "短信验证",
    "动态口令",
    "二次认证",
    "双因子",
    "captcha",
    "verification code",
    "recaptcha",
]

# 判定“认证成功”的提示关键词（仅作提示，最终由认证状态探测复核）。
DEFAULT_SUCCESS_KEYWORDS = [
    "登录成功",
    "登陆成功",
    "认证成功",
    "已成功登录",
    "success",
    '"code":0',
    '"result":0',
    "'code':0",
]

# 判定“认证失败”的提示关键词（仅作提示）。
DEFAULT_FAILURE_KEYWORDS = [
    "登录失败",
    "登陆失败",
    "认证失败",
    '"success":false',
    '"code":1',
]

# 明确指向“账号或密码错误”，命中后不做快速重试。
DEFAULT_INVALID_CREDENTIAL_KEYWORDS = [
    "密码错误",
    "帐号或密码错误",
    "账号或密码错误",
    "用户名或密码错误",
    "用户不存在",
    "invalid password",
    "wrong password",
    "authentication failed",
]

VALID_ADAPTERS = (
    "generic",
    "eportal",
    "gdufe",
    "mock_success",
    "mock_failure",
    "mock_offline",
    "mock_captcha",
    "mock_invalid",
    "mock_unknown",
)
VALID_PASSWORD_TRANSFORMS = ("none", "md5", "sha1", "sha256")
VALID_AUTOSTART_BACKENDS = ("task", "registry")
VALID_CREDENTIAL_BACKENDS = ("auto", "credman", "dpapi", "memory")


@dataclass
class Config:
    """全部可调参数。字段默认值即为“没有校园网接口信息时也能安全运行”的取值。"""

    # ---- 适配器与接口 ----
    adapter: str = "generic"
    portal_url: str = ""          # 校园网登录页面（用于“打开登录页面”/人工认证）
    login_url: str = ""           # 认证请求地址（POST/GET 的目标）
    check_url: str = ""           # 认证状态检测地址（留空则使用内置探测列表）
    internet_check_url: str = ""  # 独立的“互联网连通性检测”地址（留空则不检测）
    captcha_precheck: bool = True # 提交密码前先检查登录页面是否有验证码输入框
    request_method: str = "POST"  # POST / GET
    content_type: str = "form"    # form / json
    username: str = ""            # 账号（仅账号，不含密码）
    username_prefix: str = ""     # 账号前缀（ePortal 常见为 ",0,"）
    username_field: str = "username"
    password_field: str = "password"
    service_field: str = "service"
    service_value: str = ""       # 运营商 / 服务类型，例如 校园网、电信、联通、移动
    extra_fields: dict[str, str] = field(default_factory=dict)
    password_transform: str = "none"      # none / md5 / sha1 / sha256
    password_salt_suffix: str = ""        # 散列前追加的常量（部分校园网需要）
    password_digest_upper: bool = False   # 散列结果是否转大写
    success_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_SUCCESS_KEYWORDS))
    failure_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_FAILURE_KEYWORDS))
    invalid_credential_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_INVALID_CREDENTIAL_KEYWORDS)
    )
    captcha_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_CAPTCHA_KEYWORDS))

    # ---- 网络 / 重试 ----
    network_wait_seconds: float = 120.0     # 等待网卡/网络就绪的最长时间
    startup_delay_seconds: float = 10.0     # 程序启动后的延迟，等待系统网络栈就绪
    max_retries: int = 5                    # 单轮登录的最大尝试次数
    retry_schedule_seconds: list[float] = field(default_factory=lambda: [5, 10, 20, 30, 60])
    retry_interval_seconds: float = 60.0    # 超出重试序列后的固定间隔
    check_interval_seconds: float = 300.0   # 后台巡检间隔
    check_timeout_seconds: float = 8.0      # 认证状态探测超时
    request_timeout_seconds: float = 10.0   # 登录请求超时
    use_system_proxy: bool = False          # 默认直连（校园网门户一般不允许走代理）
    ca_bundle: str = ""                     # 自签名证书时指向 PEM 文件，而不是关闭校验

    # ---- 集成 ----
    auto_start_on_boot: bool = False
    autostart_prompted: bool = False   # 是否已经问过用户“要不要开机自动运行”
    autostart_backend: str = "task"         # task（任务计划程序）/ registry（HKCU Run）
    credential_backend: str = "auto"        # auto / credman / dpapi / memory

    # ---- 日志 ----
    log_max_bytes: int = 512 * 1024
    log_backup_count: int = 3
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        unknown = set(data or {}) - known
        if unknown:
            log.warning("配置文件中有 %d 个无法识别的字段，已忽略", len(unknown))
        cfg = cls(**clean)
        cfg.normalize()
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def normalize(self) -> None:
        """修正非法取值，避免因为手写配置导致程序崩溃。"""
        self.adapter = str(self.adapter or "generic").strip().lower()
        if self.adapter not in VALID_ADAPTERS:
            log.warning("未知适配器 %r，回退为 generic", self.adapter)
            self.adapter = "generic"

        self.request_method = str(self.request_method or "POST").strip().upper()
        if self.request_method not in ("POST", "GET"):
            self.request_method = "POST"

        self.content_type = str(self.content_type or "form").strip().lower()
        if self.content_type not in ("form", "json"):
            self.content_type = "form"

        self.password_transform = str(self.password_transform or "none").strip().lower()
        if self.password_transform not in VALID_PASSWORD_TRANSFORMS:
            log.warning("未知密码散列方式 %r，回退为 none", self.password_transform)
            self.password_transform = "none"

        self.autostart_backend = str(self.autostart_backend or "task").strip().lower()
        if self.autostart_backend not in VALID_AUTOSTART_BACKENDS:
            self.autostart_backend = "task"

        self.credential_backend = str(self.credential_backend or "auto").strip().lower()
        if self.credential_backend not in VALID_CREDENTIAL_BACKENDS:
            self.credential_backend = "auto"

        self.network_wait_seconds = _clamp(self.network_wait_seconds, 0.0, 3600.0, 120.0)
        self.startup_delay_seconds = _clamp(self.startup_delay_seconds, 0.0, 3600.0, 10.0)
        self.max_retries = int(_clamp(self.max_retries, 1, 100, 5))
        self.retry_interval_seconds = _clamp(self.retry_interval_seconds, 5.0, 3600.0, 60.0)
        self.check_interval_seconds = _clamp(self.check_interval_seconds, 15.0, 86400.0, 300.0)
        self.check_timeout_seconds = _clamp(self.check_timeout_seconds, 1.0, 120.0, 8.0)
        self.request_timeout_seconds = _clamp(self.request_timeout_seconds, 1.0, 120.0, 10.0)
        self.log_max_bytes = int(_clamp(self.log_max_bytes, 16 * 1024, 64 * 1024 * 1024, 512 * 1024))
        self.log_backup_count = int(_clamp(self.log_backup_count, 1, 20, 3))

        schedule: list[float] = []
        for item in self.retry_schedule_seconds or []:
            try:
                value = float(item)
            except (TypeError, ValueError):
                continue
            schedule.append(_clamp(value, 1.0, 3600.0, 30.0))
        self.retry_schedule_seconds = schedule or [5.0, 10.0, 20.0, 30.0, 60.0]

        if not isinstance(self.extra_fields, dict):
            self.extra_fields = {}
        self.extra_fields = {str(k): str(v) for k, v in self.extra_fields.items()}

        for name in (
            "success_keywords",
            "failure_keywords",
            "invalid_credential_keywords",
            "captcha_keywords",
        ):
            value = getattr(self, name)
            if isinstance(value, str):
                value = [line.strip() for line in value.splitlines() if line.strip()]
            if not isinstance(value, list):
                value = []
            setattr(self, name, [str(item) for item in value if str(item).strip()])

        self.log_level = str(self.log_level or "INFO").strip().upper()
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level = "INFO"


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:  # NaN
        return float(default)
    return max(low, min(high, number))


def probe_urls(config: Config) -> list[str]:
    """返回本次用于判断认证状态的探测地址列表。"""
    if config.check_url.strip():
        return [config.check_url.strip()]
    return list(DEFAULT_PROBE_URLS)


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        cfg = Config()
        cfg.normalize()
        return cfg
    try:
        # utf-8-sig：兼容记事本 / PowerShell 写入的带 BOM 的 JSON
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("配置文件顶层必须是 JSON 对象")
        return Config.from_dict(raw)
    except Exception as exc:  # 配置损坏不能让程序无法启动
        backup = path.with_suffix(path.suffix + ".bad")
        try:
            os.replace(path, backup)
        except OSError:
            pass
        log.error("配置文件无法解析（%s），已备份为 %s，改用默认配置", exc, backup.name)
        cfg = Config()
        cfg.normalize()
        return cfg


def save_config(config: Config, path: Path | None = None) -> Path:
    """原子写入配置文件。"""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config.normalize()
    data = json.dumps(config.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)
    return path
