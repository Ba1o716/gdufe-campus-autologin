"""设置界面（tkinter，Python 标准库，无需额外依赖）。

包含：账号密码（安全保存）、校园网接口参数、重试策略、开机自动运行开关。
界面只负责读写配置与凭据，不直接参与登录流程。
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import APP_DISPLAY_NAME, APP_VERSION
from .adapters import ADAPTERS
from .autostart import AutostartError, AutostartManager
from .config import Config, load_config, save_config
from .credentials import Credential, create_store, save_credential
from .logging_setup import get_logger, redactor, setup_logging
from .network import build_session, check_authentication
from .paths import config_path, log_path

ADAPTER_LABELS = {
    "generic": "通用网页认证（推荐，先抓包再填参数）",
    "eportal": "ePortal 网页认证（深澜 / 城市热点，result=1 即成功）",
    "gdufe": "广东财经大学佛山校区（等待真实接口信息）",
    "mock_success": "模拟：登录成功（调试用）",
    "mock_failure": "模拟：登录失败（调试用）",
    "mock_offline": "模拟：网络不可用（调试用）",
    "mock_captcha": "模拟：需要验证码（调试用）",
}

PASSWORD_TRANSFORMS = {
    "none": "不处理（明文提交）",
    "md5": "MD5 散列",
    "sha1": "SHA1 散列",
    "sha256": "SHA256 散列",
}

CREDENTIAL_BACKENDS = {
    "auto": "自动（优先 Windows 凭据管理器）",
    "credman": "Windows 凭据管理器",
    "dpapi": "DPAPI 加密文件",
}

AUTOSTART_BACKENDS = {
    "task": "任务计划程序（推荐）",
    "registry": "注册表 HKCU\\...\\Run",
}


class SettingsWindow:
    def __init__(self) -> None:
        self.config = load_config()
        self.log = get_logger("settings")
        self.log_file = setup_logging(self.config)
        self.store, self.store_description = create_store(self.config.credential_backend, self.log)
        self.autostart = AutostartManager(self.config, logger=self.log)
        self.root = tk.Tk()
        self.root.title(f"{APP_DISPLAY_NAME} 设置  v{APP_VERSION}")
        self.root.minsize(660, 620)
        self.vars: dict[str, tk.Variable] = {}
        self.texts: dict[str, tk.Text] = {}
        self._build()
        self._load_form()
        self._refresh_status()
        self._refresh_credential_status()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        basic = ttk.Frame(notebook)
        advanced = ttk.Frame(notebook)
        notebook.add(basic, text="基本设置")
        notebook.add(advanced, text="接口与高级选项")

        self._build_basic(basic)
        self._build_advanced(advanced)

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=10)
        ttk.Button(bottom, text="保存", command=self.on_save).pack(side="left")
        ttk.Button(bottom, text="保存并立即检测", command=lambda: self.on_save(check=True)).pack(
            side="left", padx=6
        )
        ttk.Button(bottom, text="立即检测", command=self.on_check).pack(side="left")
        ttk.Button(bottom, text="打开登录页面", command=self.on_open_portal).pack(side="left", padx=6)
        ttk.Button(bottom, text="查看日志", command=self.on_open_log).pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.on_close).pack(side="right")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.credential_var = tk.StringVar(value="")
        ttk.Label(
            self.root,
            textvariable=self.credential_var,
            foreground="#b45309",
            wraplength=640,
            justify="left",
        ).pack(fill="x", padx=12, pady=(6, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.status_var, foreground="#0a5").pack(
            fill="x", padx=12, pady=(0, 4)
        )
        ttk.Label(
            self.root,
            text=f"配置文件：{config_path()}    日志：{self.log_file}    凭据存储：{self.store_description}",
            foreground="#666",
        ).pack(fill="x", padx=12, pady=(0, 8))

    # ------------------------------------------------------------------
    def _row(self, parent, row: int, label: str, widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
        widget.grid(row=row, column=1, sticky="ew", pady=3)

    def _entry(self, parent, row: int, key: str, label: str, width: int = 42):
        var = tk.StringVar()
        self.vars[key] = var
        entry = ttk.Entry(parent, textvariable=var, width=width)
        self._row(parent, row, label, entry)
        return entry

    def _combo(self, parent, row: int, key: str, label: str, values: list[str]):
        var = tk.StringVar()
        self.vars[key] = var
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly")
        self._row(parent, row, label, combo)
        return combo

    def _text_area(self, parent, row: int, key: str, label: str, height: int = 4):
        frame = ttk.Frame(parent)
        text = tk.Text(frame, height=height, width=48, wrap="none")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        self.texts[key] = text
        self._row(parent, row, label, frame)
        return text

    def _check(self, parent, row: int, key: str, label: str):
        var = tk.BooleanVar(value=False)
        self.vars[key] = var
        widget = ttk.Checkbutton(parent, text=label, variable=var)
        self._row(parent, row, "", widget)
        return widget

    def _build_basic(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        # 适配器
        adapter_labels = [self._adapter_label(name) for name in ADAPTERS]
        self.adapter_by_label = {self._adapter_label(name): name for name in ADAPTERS}
        self._combo(parent, 0, "adapter", "认证适配器", adapter_labels)
        self._entry(parent, 1, "portal_url", "校园网登录页面 URL")
        self._entry(parent, 2, "login_url", "登录请求地址（login_url）")
        self._entry(parent, 3, "check_url", "认证状态检测地址（可留空）")
        self._entry(parent, 4, "username", "校园网账号")
        self._entry(parent, 5, "password", "校园网密码（留空＝不修改）")
        self.entry_password = self.vars["password"]

        options = ttk.LabelFrame(parent, text="运行方式")
        options.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        options.columnconfigure(1, weight=1)

        var_auto = tk.BooleanVar(value=False)
        self.vars["auto_start_on_boot"] = var_auto
        ttk.Checkbutton(options, text="开机自动运行（不需要管理员权限）", variable=var_auto).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=4
        )

        var_proxy = tk.BooleanVar(value=False)
        self.vars["use_system_proxy"] = var_proxy
        ttk.Checkbutton(options, text="使用系统代理访问校园网（一般不需要勾选）", variable=var_proxy).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=6, pady=4
        )

        self._entry(options, 2, "startup_delay_seconds", "启动延迟（秒）", 12)
        self._entry(options, 3, "max_retries", "最大重试次数", 12)
        self._entry(options, 4, "retry_schedule_seconds", "重试间隔（秒，逗号分隔）", 32)
        self._entry(options, 5, "check_interval_seconds", "后台巡检间隔（秒）", 12)
        self._combo(
            options, 6, "autostart_backend", "开机启动方式",
            list(AUTOSTART_BACKENDS.values()),
        )
        self.autostart_by_label = {v: k for k, v in AUTOSTART_BACKENDS.items()}

    def _build_advanced(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        self._combo(parent, 0, "request_method", "请求方法", ["POST", "GET"])
        self._combo(parent, 1, "content_type", "参数格式", ["form", "json"])
        self._entry(parent, 2, "username_field", "账号参数字段名")
        self._entry(parent, 3, "password_field", "密码参数字段名")
        self._entry(parent, 4, "username_prefix", "账号前缀（ePortal 常见为 ,0, 留空则用适配器默认）")
        self._entry(parent, 5, "service_field", "服务类型的字段名（可留空）")
        self._entry(parent, 6, "service_value", "服务类型值（例如 校园网 / 电信）")
        transforms = list(PASSWORD_TRANSFORMS.values())
        self._combo(parent, 7, "password_transform", "密码提交方式", transforms)
        self.transform_by_label = {v: k for k, v in PASSWORD_TRANSFORMS.items()}
        self._entry(parent, 8, "password_salt_suffix", "散列时追加的常量（可留空）")
        self._entry(parent, 9, "ca_bundle", "自签名证书 PEM 路径（可留空）")
        self._combo(
            parent, 10, "credential_backend", "账号密码保存位置",
            list(CREDENTIAL_BACKENDS.values()),
        )
        self.cred_by_label = {v: k for k, v in CREDENTIAL_BACKENDS.items()}
        self._entry(parent, 11, "check_timeout_seconds", "认证检测超时（秒）", 12)
        self._entry(parent, 12, "request_timeout_seconds", "登录请求超时（秒）", 12)
        self._entry(parent, 13, "network_wait_seconds", "等待网络最长时间（秒）", 12)
        self._text_area(parent, 14, "extra_fields", "附加固定参数（每行 key=value）", 4)
        self._text_area(parent, 15, "captcha_keywords", "需要人工处理的页面关键词（每行一个）", 3)
        self._text_area(parent, 16, "invalid_credential_keywords", "账号密码错误关键词", 3)
        self._entry(parent, 17, "internet_check_url", "互联网连通性检测地址（可留空）")
        self._check(
            parent, 18, "captcha_precheck", "提交密码前先检查登录页面有没有验证码输入框（推荐开启）"
        )

    @staticmethod
    def _adapter_label(name: str) -> str:
        return ADAPTER_LABELS.get(name, name)

    # ------------------------------------------------------------------
    def _load_form(self) -> None:
        cfg = self.config
        self.vars["adapter"].set(self._adapter_label(cfg.adapter))
        for key in (
            "portal_url",
            "login_url",
            "check_url",
            "username",
            "username_prefix",
            "username_field",
            "password_field",
            "service_field",
            "service_value",
            "password_salt_suffix",
            "ca_bundle",
            "internet_check_url",
        ):
            self.vars[key].set(getattr(cfg, key, ""))
        self.vars["password"].set("")
        self.vars["request_method"].set(cfg.request_method)
        self.vars["content_type"].set(cfg.content_type)
        self.vars["password_transform"].set(
            PASSWORD_TRANSFORMS.get(cfg.password_transform, PASSWORD_TRANSFORMS["none"])
        )
        self.vars["auto_start_on_boot"].set(
            self.autostart.is_installed() or bool(cfg.auto_start_on_boot)
        )
        self.vars["use_system_proxy"].set(bool(cfg.use_system_proxy))
        self.vars["captcha_precheck"].set(bool(cfg.captcha_precheck))
        self.vars["startup_delay_seconds"].set(_num(cfg.startup_delay_seconds))
        self.vars["max_retries"].set(str(cfg.max_retries))
        self.vars["retry_schedule_seconds"].set(
            ", ".join(_num(x) for x in cfg.retry_schedule_seconds)
        )
        self.vars["check_interval_seconds"].set(_num(cfg.check_interval_seconds))
        self.vars["check_timeout_seconds"].set(_num(cfg.check_timeout_seconds))
        self.vars["request_timeout_seconds"].set(_num(cfg.request_timeout_seconds))
        self.vars["network_wait_seconds"].set(_num(cfg.network_wait_seconds))
        self.vars["autostart_backend"].set(
            AUTOSTART_BACKENDS.get(cfg.autostart_backend, AUTOSTART_BACKENDS["task"])
        )
        self.vars["credential_backend"].set(
            CREDENTIAL_BACKENDS.get(cfg.credential_backend, CREDENTIAL_BACKENDS["auto"])
        )
        self.texts["extra_fields"].insert("1.0", _dict_to_text(cfg.extra_fields))
        self.texts["captcha_keywords"].insert("1.0", "\n".join(cfg.captcha_keywords))
        self.texts["invalid_credential_keywords"].insert(
            "1.0", "\n".join(cfg.invalid_credential_keywords)
        )

    def _collect(self) -> Config:
        cfg = self.config
        cfg.adapter = self.adapter_by_label.get(self.vars["adapter"].get(), "generic")
        for key in (
            "portal_url",
            "login_url",
            "check_url",
            "username",
            "username_prefix",
            "username_field",
            "password_field",
            "service_field",
            "service_value",
            "password_salt_suffix",
            "ca_bundle",
            "internet_check_url",
        ):
            setattr(cfg, key, str(self.vars[key].get()).strip())
        cfg.request_method = self.vars["request_method"].get().strip().upper()
        cfg.content_type = self.vars["content_type"].get().strip().lower()
        cfg.password_transform = self.transform_by_label.get(
            self.vars["password_transform"].get(), "none"
        )
        cfg.autostart_backend = self.autostart_by_label.get(
            self.vars["autostart_backend"].get(), "task"
        )
        cfg.credential_backend = self.cred_by_label.get(
            self.vars["credential_backend"].get(), "auto"
        )
        cfg.use_system_proxy = bool(self.vars["use_system_proxy"].get())
        cfg.captcha_precheck = bool(self.vars["captcha_precheck"].get())
        cfg.auto_start_on_boot = bool(self.vars["auto_start_on_boot"].get())
        cfg.startup_delay_seconds = _to_float(self.vars["startup_delay_seconds"].get(), 10.0)
        cfg.max_retries = int(_to_float(self.vars["max_retries"].get(), 5))
        cfg.check_interval_seconds = _to_float(self.vars["check_interval_seconds"].get(), 300.0)
        cfg.check_timeout_seconds = _to_float(self.vars["check_timeout_seconds"].get(), 8.0)
        cfg.request_timeout_seconds = _to_float(self.vars["request_timeout_seconds"].get(), 10.0)
        cfg.network_wait_seconds = _to_float(self.vars["network_wait_seconds"].get(), 120.0)
        schedule = [
            _to_float(part, 0.0)
            for part in str(self.vars["retry_schedule_seconds"].get()).replace("，", ",").split(",")
            if part.strip()
        ]
        cfg.retry_schedule_seconds = [x for x in schedule if x > 0] or [5, 10, 20, 30, 60]
        cfg.extra_fields = _text_to_dict(self.texts["extra_fields"].get("1.0", "end"))
        for key in ("captcha_keywords", "invalid_credential_keywords"):
            values = [
                line.strip()
                for line in self.texts[key].get("1.0", "end").splitlines()
                if line.strip()
            ]
            setattr(cfg, key, values)
        cfg.normalize()
        return cfg

    # ------------------------------------------------------------------
    def on_save(self, check: bool = False) -> None:
        try:
            cfg = self._collect()
        except Exception as exc:
            messagebox.showerror("保存失败", f"配置内容有误：{exc}", parent=self.root)
            return

        username = str(self.vars["username"].get()).strip()
        password = str(self.vars["password"].get())
        messages: list[str] = []

        if username and password:
            try:
                self.store = save_credential(self.store, Credential(username, password))
                redactor().add_secret(password)
                cfg.username = username
                self.vars["password"].set("")
                messages.append(
                    f"账号密码已保存到 {getattr(self.store, 'description', self.store.name)}"
                )
            except Exception as exc:
                messagebox.showerror("保存账号密码失败", str(exc), parent=self.root)
                return
        elif password and not username:
            # 以前这里会静默跳过，导致用户以为保存了其实没保存
            messagebox.showwarning(
                "账号没填，没有保存",
                "你在密码框里填了内容，但「校园网账号」是空的，所以账号密码不会被保存。\n\n"
                "请把学号填到「校园网账号」里，然后再点一次「保存」。",
                parent=self.root,
            )
            messages.append("⚠️ 账号为空，账号密码未保存")
        elif username:
            existing = self.store.load()
            if existing and existing.password:
                cfg.username = username
                if existing.username != username:
                    self.store = save_credential(
                        self.store, Credential(username, existing.password)
                    )
                    messages.append("已更新账号（沿用原密码）")
            else:
                messagebox.showwarning(
                    "还没有保存密码",
                    "只填了账号、没有填密码，所以这次不会保存账号密码。\n\n"
                    "请把密码也填上，再点一次「保存」。",
                    parent=self.root,
                )
                messages.append("⚠️ 只填了账号，没有保存密码")

        try:
            path = save_config(cfg)
            messages.append(f"配置已保存到 {path.name}")
        except Exception as exc:
            messagebox.showerror("保存配置失败", str(exc), parent=self.root)
            return

        # 同步开机自动运行
        try:
            installed = self.autostart.is_installed()
            if cfg.auto_start_on_boot and not installed:
                self.autostart.install()
                messages.append("已开启开机自动运行")
            elif not cfg.auto_start_on_boot and installed:
                self.autostart.uninstall()
                messages.append("已关闭开机自动运行")
        except AutostartError as exc:
            messagebox.showwarning("开机自动运行设置失败", str(exc), parent=self.root)

        self.config = cfg
        self._refresh_status()
        self._refresh_credential_status()
        self.status_var.set("；".join(messages))
        if check:
            self.on_check()

    def on_check(self) -> None:
        cfg = self._collect()
        self.status_var.set("正在检测认证状态…")

        def worker() -> None:
            try:
                session = build_session(cfg)
                state = check_authentication(cfg, session=session)
                text = f"检测结果：{state.describe()}"
                if state.portal_url:
                    text += f"（门户：{state.portal_url}）"
            except Exception as exc:
                text = f"检测失败：{type(exc).__name__} {exc}"
            self.root.after(0, lambda: self.status_var.set(text))

        threading.Thread(target=worker, daemon=True).start()

    def on_open_portal(self) -> None:
        url = str(self.vars["portal_url"].get()).strip() or str(self.vars["login_url"].get()).strip()
        if not url:
            messagebox.showinfo("提示", "请先填写校园网登录页面 URL 或登录请求地址。", parent=self.root)
            return
        try:
            webbrowser.open(url)
        except Exception as exc:
            messagebox.showerror("打开失败", f"{url}\n{exc}", parent=self.root)

    def on_open_log(self) -> None:
        try:
            import os

            os.startfile(str(self.log_file))  # noqa: S606
        except Exception:
            messagebox.showinfo("日志文件", str(self.log_file), parent=self.root)

    def _refresh_status(self) -> None:
        try:
            installed = self.autostart.is_installed()
        except Exception:
            installed = False
        self.vars["auto_start_on_boot"].set(installed or bool(self.config.auto_start_on_boot))
        self.root.title(
            f"{APP_DISPLAY_NAME} 设置  v{APP_VERSION}  —  开机自动运行：{'已开启' if installed else '未开启'}"
        )

    def _refresh_credential_status(self) -> None:
        """明确显示“账号密码到底存上了没有”，避免填了却没保存。"""
        try:
            credential = self.store.load()
        except Exception:
            credential = None
        label = getattr(self.store, "description", self.store.name)
        if credential and credential.complete:
            self.credential_var.set(
                f"✅ 已保存的账号：{credential.username}　（存储位置：{label}）"
            )
        else:
            self.credential_var.set(
                "⚠️ 还没有保存账号密码：填好上面的「校园网账号」和「校园网密码」后，"
                "一定要点「保存」按钮（填完直接关窗口不会保存）"
            )

    def on_close(self) -> None:
        """关闭窗口前提醒未保存的密码。"""
        if str(self.vars["password"].get()).strip():
            answer = messagebox.askyesnocancel(
                "密码还没保存",
                "你在密码框里填了内容，但还没点「保存」。\n\n"
                "是：现在保存并关闭\n否：不保存直接关闭",
                parent=self.root,
            )
            if answer is None:
                return
            if answer:
                self.on_save()
                if str(self.vars["password"].get()).strip():
                    return  # 保存失败，窗口不关，避免把内容丢了
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _num(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else str(number)


def _to_float(text: str, default: float) -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return float(default)


def _dict_to_text(data: dict) -> str:
    return "\n".join(f"{k}={v}" for k, v in (data or {}).items())


def _text_to_dict(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def main() -> int:
    window = SettingsWindow()
    return window.run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
