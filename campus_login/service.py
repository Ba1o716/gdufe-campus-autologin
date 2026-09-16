"""核心流程：等待网络 → 检测认证状态 → 自动登录 → 复核 → 后台巡检。

状态机：
    等待网络 → 检查认证状态 → （已认证）→ 后台巡检
                            → （需要认证）→ 登录 → 复核认证状态
                                                  ├─ 成功 → 后台巡检
                                                  ├─ 失败 → 按重试序列重试
                                                  └─ 需要验证码 → 转人工，停止自动尝试
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import network as network_module
from .adapters import BaseAdapter, LoginResult, LoginStatus
from .config import Config
from .credentials import Credential, CredentialStore
from .logging_setup import get_logger, redactor
from .network import AuthState, verify_for
from .retry import RetryPolicy, Sleeper


class Status(str, Enum):
    IDLE = "idle"
    WAITING_NETWORK = "waiting_network"
    CHECKING = "checking"
    LOGGING_IN = "logging_in"
    AUTHENTICATED = "authenticated"   # 认证服务器已接受登录（Portal 认证成功）
    ONLINE = "online"                 # 已确认可以访问互联网（独立连通性检测通过）
    FAILED = "failed"
    NEEDS_MANUAL = "needs_manual"
    STOPPED = "stopped"


STATUS_TEXT = {
    Status.IDLE: "空闲",
    Status.WAITING_NETWORK: "正在等待网络",
    Status.CHECKING: "正在检查认证状态",
    Status.LOGGING_IN: "正在登录",
    Status.AUTHENTICATED: "认证成功",
    Status.ONLINE: "已连接互联网",
    Status.FAILED: "认证失败",
    Status.NEEDS_MANUAL: "需要人工验证",
    Status.STOPPED: "已停止",
}

# 视为“认证已经完成”的状态
SUCCESS_STATUSES = (Status.AUTHENTICATED, Status.ONLINE)


@dataclass
class ServiceEvent:
    status: Status
    message: str = ""
    important: bool = False


class CampusLoginService:
    """校园网自动登录服务（可被 CLI / 托盘 / GUI 复用）。"""

    def __init__(
        self,
        config: Config,
        store: CredentialStore,
        adapter: BaseAdapter,
        network=network_module,
        sleeper: Sleeper | None = None,
        on_event: Callable[[ServiceEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.adapter = adapter
        self.network = network or network_module
        self.log = get_logger("service")
        self._stop_flag = _StopFlag()
        self.sleeper = sleeper or Sleeper(self._stop_flag.event)
        self.on_event = on_event
        self.status = Status.IDLE
        self.message = ""
        self.last_state: AuthState | None = None
        self.last_result: LoginResult | None = None
        self._login_blocked: str = ""
        self.attempts_total = 0

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop_flag.set()
        self.sleeper.stop()

    @property
    def stopped(self) -> bool:
        return self._stop_flag.is_set()

    def blocked_reason(self) -> str:
        return self._login_blocked

    def reset_block(self) -> None:
        """允许再次自动登录（用户在托盘/GUI 中手动点击“立即登录”时调用）。"""
        self._login_blocked = ""

    def _emit(self, status: Status, message: str = "", important: bool = False) -> None:
        self.status = status
        self.message = message or STATUS_TEXT.get(status, "")
        text = f"{STATUS_TEXT.get(status, status.value)}{('：' + message) if message else ''}"
        self.log.info(text)
        if self.on_event:
            try:
                self.on_event(ServiceEvent(status, self.message, important))
            except Exception as exc:
                self.log.debug("状态回调出错（忽略）：%s", type(exc).__name__)

    def load_credential(self) -> Credential | None:
        try:
            credential = self.store.load()
        except Exception as exc:
            self.log.error("读取凭据失败：%s", type(exc).__name__)
            return None
        if credential and credential.password:
            redactor().add_secret(credential.password)
        return credential

    # ------------------------------------------------------------------
    # 单步动作
    # ------------------------------------------------------------------
    def check(self) -> AuthState:
        self._emit(Status.CHECKING, "检查校园网认证状态")
        try:
            state = self.network.check_authentication(
                self.config,
                session=self.adapter.session,
                timeout=self.config.check_timeout_seconds,
            )
        except Exception as exc:
            self.log.error("认证状态检测异常：%s", type(exc).__name__)
            state = AuthState(reason=f"认证状态检测异常（{type(exc).__name__}）")
        self.last_state = state
        if state.authenticated:
            self.log.info("当前已认证")
        elif state.network_down:
            self.log.warning("网络不可用：%s", state.reason)
        elif state.needs_manual:
            self.log.warning("需要人工验证：%s", state.reason)
        else:
            self.log.info("当前未认证：%s", state.reason)
        return state

    def login_once(self, credential: Credential) -> LoginResult:
        self._emit(Status.LOGGING_IN, "开始校园网认证")
        try:
            result = self.adapter.login(credential)
        except Exception as exc:
            self.log.error("登录过程出现异常：%s", type(exc).__name__)
            result = LoginResult(
                LoginStatus.NETWORK_ERROR, f"登录过程出现异常（{type(exc).__name__}）"
            )
        self.attempts_total += 1
        self.last_result = result
        if result.status is LoginStatus.SUCCESS:
            self.log.info("登录成功：%s", result.message)
        elif result.status is LoginStatus.INVALID_CREDENTIALS:
            self.log.warning("登录失败：%s", result.message)
        elif result.status is LoginStatus.CAPTCHA_REQUIRED:
            self.log.warning("需要人工验证：%s", result.message)
        elif result.status is LoginStatus.NOT_CONFIGURED:
            self.log.warning("缺少校园网接口信息：%s", result.message)
        else:
            self.log.warning("登录失败：%s", result.message)
        return result

    def open_portal_url(self) -> str:
        return self.adapter.portal_url

    def check_internet(self) -> bool | None:
        """独立的互联网连通性检测（与“认证成功”分开，只做记录和提示）。

        返回 True=连通，False=不连通，None=未配置（跳过）。
        无论结果如何，都不会把已经成功的 Portal 认证判定为失败。
        """
        url = (self.config.internet_check_url or "").strip()
        if not url:
            self.log.info("未配置互联网连通性检测地址，跳过互联网连通性检测")
            return None
        probe = getattr(self.network, "probe", None)
        if probe is None:
            self.log.warning("当前网络实现不支持连通性探测，跳过互联网连通性检测")
            return None
        try:
            result = probe(
                url,
                self.adapter.session,
                self.config.check_timeout_seconds,
                verify=verify_for(self.config),
            )
        except TypeError:
            result = probe(url, self.adapter.session, self.config.check_timeout_seconds)
        except Exception as exc:
            self.log.warning("互联网连通性检测异常：%s", type(exc).__name__)
            return False
        if getattr(result, "online", False):
            self.log.info("互联网连通性检测成功（%s）", url)
            return True
        self.log.warning(
            "互联网连通性检测失败（%s）：%s",
            url,
            getattr(result, "error", "") or getattr(result, "kind", "未知原因"),
        )
        return False

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def ensure_online(self, ignore_block: bool = False, wait_network: bool = True) -> Status:
        """把网络从“未认证”推进到“已认证”。返回最终状态。"""
        config = self.config
        policy = RetryPolicy.from_config(config)
        failures = 0

        if self.stopped:
            return Status.STOPPED

        if wait_network:
            self._emit(Status.WAITING_NETWORK, "等待网络连接")
            ready = self.network.wait_for_local_network(
                config,
                sleeper=self.sleeper,
                on_wait=lambda: self._emit(Status.WAITING_NETWORK, "网络不可用，等待中"),
            )
            if not ready:
                self._emit(Status.FAILED, "网络不可用（未获取到可用的本地 IP）", important=True)
                return Status.FAILED
            self.log.info("网络连接正常")

        while not self.stopped:
            state = self.check()

            if state.authenticated:
                self._login_blocked = ""
                self._emit(Status.ONLINE, "校园网已连接/认证成功")
                return Status.ONLINE

            if state.needs_manual:
                self._login_blocked = "需要人工完成认证（验证码 / 二次认证）"
                self._emit(Status.NEEDS_MANUAL, state.reason, important=True)
                return Status.NEEDS_MANUAL

            if self._login_blocked and not ignore_block:
                self.log.info("已暂停自动登录：%s（可通过托盘“立即登录”或设置界面重新启用）", self._login_blocked)
                self._emit(Status.FAILED, self._login_blocked)
                return Status.FAILED

            if not state.network_down:
                credential = self.load_credential()
                if credential is None or not credential.complete:
                    self._login_blocked = "尚未保存校园网账号和密码，请先运行设置界面填写"
                    self.log.warning(self._login_blocked)
                    self._emit(Status.FAILED, self._login_blocked, important=True)
                    return Status.FAILED

                result = self.login_once(credential)

                if result.status is LoginStatus.CAPTCHA_REQUIRED:
                    self._login_blocked = "需要人工完成认证（验证码 / 二次认证）"
                    self._emit(Status.NEEDS_MANUAL, result.message, important=True)
                    return Status.NEEDS_MANUAL

                if result.status is LoginStatus.NOT_CONFIGURED:
                    self._login_blocked = result.message
                    self._emit(Status.FAILED, result.message, important=True)
                    return Status.FAILED

                if result.status is LoginStatus.INVALID_CREDENTIALS:
                    self._login_blocked = "校园网账号或密码可能错误，请检查配置"
                    self._emit(Status.FAILED, self._login_blocked, important=True)
                    return Status.FAILED

                # 认证服务器明确返回“认证成功” → 直接认定认证成功。
                # 不再用“访问是否被重定向到登录页”去否决这个结果。
                if result.status is LoginStatus.SUCCESS:
                    self._login_blocked = ""
                    self.log.info("Portal 认证成功")
                    self._emit(Status.AUTHENTICATED, result.message or "Portal 认证成功")
                    if self.check_internet() is True:
                        self._emit(Status.ONLINE, "互联网连通性检测成功")
                        return Status.ONLINE
                    return Status.AUTHENTICATED

                # 只有“响应无法识别”时，才用认证状态探测作为成功的正向证据
                if result.status is LoginStatus.BAD_RESPONSE:
                    self.log.warning("认证结果无法确认，用认证状态探测复核")
                    verify_state = self.check()
                    if verify_state.authenticated:
                        self._login_blocked = ""
                        self._emit(Status.ONLINE, "校园网已连接/认证成功")
                        return Status.ONLINE
                    if verify_state.needs_manual:
                        self._login_blocked = "需要人工完成认证（验证码 / 二次认证）"
                        self._emit(Status.NEEDS_MANUAL, verify_state.reason, important=True)
                        return Status.NEEDS_MANUAL

            # ---- 失败处理 ----
            failures += 1
            if not policy.can_retry(failures):
                self._emit(
                    Status.FAILED,
                    f"已尝试 {failures} 次仍未通过认证，暂停自动重试",
                    important=True,
                )
                return Status.FAILED
            delay = policy.delay_for(failures)
            self.log.info("正在重试（%d 秒后）", int(delay))
            self._emit(Status.CHECKING, f"认证未通过，{int(delay)} 秒后重试")
            if not self.sleeper.sleep(delay):
                return Status.STOPPED

        return Status.STOPPED

    def run_forever(self) -> Status:
        """开机模式：启动延迟 → 完成认证 → 后台低频巡检。"""
        self.log.info("程序启动")
        delay = float(self.config.startup_delay_seconds)
        if delay > 0:
            self.log.info("启动延迟 %.0f 秒（等待系统网络就绪）", delay)
            if not self.sleeper.sleep(delay):
                self._emit(Status.STOPPED)
                return Status.STOPPED

        first = True
        while not self.stopped:
            status = self.ensure_online(ignore_block=False, wait_network=True)
            first = False
            if status is Status.STOPPED or self.stopped:
                break
            interval = float(self.config.check_interval_seconds)
            if status is Status.IDLE:
                interval = 30.0
            self.log.debug("后台巡检：%.0f 秒后再次检查认证状态", interval)
            if not self.sleeper.sleep(interval):
                break

        self._emit(Status.STOPPED)
        return Status.STOPPED


class _StopFlag:
    """内部停止标志（threading.Event 的轻量包装，便于注入测试）。"""

    def __init__(self) -> None:
        import threading

        self.event = threading.Event()

    def set(self) -> None:
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set()
