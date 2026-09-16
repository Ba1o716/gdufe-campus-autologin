"""ePortal 网页认证适配器（深澜 Dr.COM / 城市热点等，接口路径为 /eportal/portal/login）。

真实抓包确认的接口形态：

    GET http://192.0.2.10:801/eportal/portal/login
    响应：dr1003({"result":1,"msg":"Portal协议认证成功！"});

判定规则（由 BaseAdapter.classify_json 实现）：
  * result == 1 → 认证服务器已接受登录 → Portal 认证成功
  * result != 1 → 再看 msg：
        msg 含"密码错误/用户不存在" → 账号密码错误（不再快速重试）
        msg 含"验证码/短信/二次认证" → 转人工处理
        msg 含"已在线/已登录" → 视为认证成功
        其它                        → 认证失败，并把 msg 原样写进日志

只需要在设置里填 login_url（登录请求地址），参数名按你的抓包结果填写
（ePortal 一般是 user_account / user_password，附加参数放在 extra_fields）。
"""

from __future__ import annotations

from .generic_portal import GenericPortalAdapter


class EPortalAdapter(GenericPortalAdapter):
    name = "eportal"
    display_name = "ePortal 网页认证（深澜 / 城市热点）"
    description = "按 ePortal 协议解析 JSONP 返回值：result=1 即认证成功。"

    LOGIN_URL = ""                    # 例：http://192.0.2.10:801/eportal/portal/login
    CHECK_URL = ""                    # 留空则使用内置连通性探测地址
    USERNAME_FIELD = "user_account"
    PASSWORD_FIELD = "user_password"
    SERVICE_FIELD = ""
    # ePortal 的账号参数通常带 ",0," 前缀（抓包确认：user_account=,0,账号）
    USERNAME_PREFIX = ",0,"
    # 门户页面里取不到 wlanacip 时使用的备用 AC 地址（各个学校可按需填写）
    DEFAULT_AC_IP = ""
    # 抓包确认的公共参数（ePortal 4.x）：
    #   {local_ip} 会在每次登录时自动替换成“校园网网卡”的本机 IP（跳过代理/VPN 虚拟网卡）
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

    def __init__(self, config, logger=None, session=None) -> None:
        super().__init__(config, logger, session)
        for key, value in self.DEFAULT_EXTRA_FIELDS.items():
            self.config.extra_fields.setdefault(key, value)
        if not (self.config.username_prefix or "").strip() and self.USERNAME_PREFIX:
            self.config.username_prefix = self.USERNAME_PREFIX
