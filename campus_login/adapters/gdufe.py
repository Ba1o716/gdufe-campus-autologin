"""广东财经大学佛山校区适配器。

2026-09-16 由用户抓包确认的接口形态（ePortal 协议）：

    登录请求：GET http://100.64.13.17:801/eportal/portal/login
    响应：    dr1003({"result":1,"msg":"Portal协议认证成功！"});
    门户页：  http://100.64.13.17/a79.htm?wlanacip=100.64.13.18

因此本适配器直接继承 ePortal 适配器：result=1 即认定“认证服务器已接受登录”。
如果校园网的地址（100.64.13.17:801）以后发生变化，在设置界面的
“登录请求地址（login_url）”里覆盖即可，不用改代码。

⚠️ 账号 / 密码的参数名（user_account / user_password）是 ePortal 的常见默认值，
如果你的抓包显示不是这两个名字，请在设置界面改成真实字段名。
**不需要提供真实密码**：密码只保存在你本机的凭据管理器里。
"""

from __future__ import annotations

from .eportal import EPortalAdapter


class GuangDongUniversityOfFinanceAdapter(EPortalAdapter):
    name = "gdufe"
    display_name = "广东财经大学佛山校区（ePortal 认证）"
    description = "已填入抓包确认的 login_url；参数名如与抓包不符可在设置里修改。"

    # ---- 抓包确认的真实接口（config.login_url 非空时以配置为准） ----
    LOGIN_URL = "http://100.64.13.17:801/eportal/portal/login"
    CHECK_URL = ""                      # 留空 → 用内置探测地址判断“是否需要认证”
    USERNAME_FIELD = "user_account"
    PASSWORD_FIELD = "user_password"
    SERVICE_FIELD = ""
    # 佛山校区实测的 AC 地址（门户页面里取不到 wlanacip 时用它兜底）
    DEFAULT_AC_IP = "100.64.13.18"

    # 2026-09-16 抓包确认的完整参数（不含账号密码）：
    #   callback=dr1003 & login_method=1 & user_account=账号 & user_password=密码
    # 注意：抓包里账号是 user_account=%2C0%2C账号，即 ",0," + 账号，已由 USERNAME_PREFIX 处理
    #   & wlan_user_ip=10.20.30.40 & wlan_user_ipv6= & wlan_user_mac=000000000000
    #   & wlan_ac_ip=100.64.13.18 & wlan_ac_name= & jsVersion=4.1.3
    #   & terminal_type=1 & lang=zh-cn & v=1779 & lang=zh
    # wlan_user_ip 每次登录都不一样，用 {local_ip} 自动取当前校园网网卡地址；
    # v 是防缓存随机数，用 {random} 每次生成。
    DEFAULT_EXTRA_FIELDS: dict[str, str] = {
        "callback": "dr1003",
        "login_method": "1",
        "wlan_user_ip": "{local_ip}",
        "wlan_user_ipv6": "",
        "wlan_user_mac": "000000000000",
        "wlan_ac_ip": "{portal_ac_ip}",
        "wlan_ac_name": "",
        "jsVersion": "4.1.3",
        "terminal_type": "1",
        "lang": "zh-cn",
        "v": "{random}",
    }

    # 这些是“默认占位值”，会被替换成真实的 ePortal 参数名
    PLACEHOLDER_USERNAME_FIELDS = ("", "username", "user", "account", "userid", "loginname")
    PLACEHOLDER_PASSWORD_FIELDS = ("", "password", "pwd", "passwd")

    REQUIRED_INFO = (
        "登录请求地址（login_url）",
        "请求方法（GET / POST）",
        "账号参数名（例如 username / account / userid）",
        "密码参数名（例如 password / pwd）",
        "是否需要先对密码做 MD5 等散列",
        "运营商 / 服务类型参数（如果学校有）",
        "登录成功与失败时接口返回的内容示例",
    )

    def __init__(self, config, logger=None, session=None) -> None:
        super().__init__(config, logger, session)
        if (self.config.username_field or "").strip().lower() in self.PLACEHOLDER_USERNAME_FIELDS:
            self.config.username_field = self.USERNAME_FIELD
        if (self.config.password_field or "").strip().lower() in self.PLACEHOLDER_PASSWORD_FIELDS:
            self.config.password_field = self.PASSWORD_FIELD

    def validate(self) -> str | None:
        problem = super().validate()
        if problem:
            return problem + "（广东财经大学佛山校区适配器：还需要真实校园网请求信息才能继续）"
        return None
