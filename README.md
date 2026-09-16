# 校园网自动登录（广东财经大学佛山校区）

Windows 上的**开机自动登录校园网**小工具。开机后它会：

1. 等电脑网络连上；
2. 判断现在是不是还需要网页认证（已认证就什么都不做）；
3. 需要认证就用你自己保存的账号自动完成认证；
4. 之后常驻托盘，掉线 / 过期时自动重新登录。

已经按**佛山校区**的 ePortal 接口（`/eportal/portal/login`）配置好参数，选一下
适配器、填一次学号密码就能用。其它学校也能用，见第 15 节（改适配器）。

## ⚠️ 使用须知

* 只用于**本人有权限使用的账号**完成**正常的校园网登录**。
* **不绕过**验证码、短信验证、二次认证、计费限制或任何访问控制。遇到验证码会**停下来**
  提示你手动处理。
* 请在**自己的电脑、自己的账号**上使用，遵守学校和运营商的网络使用规定。
* **不要在 Issue / 讨论区粘贴**你的账号、密码，或包含密码的完整请求 URL。
* 程序按"现状"提供，使用风险自负。

## 快速开始（3 步）

**第 1 步：装 Python**
到 [python.org](https://www.python.org/downloads/) 下载 Python 3.9 以上版本，
安装时**务必勾选 `Add python.exe to PATH`**。

**第 2 步：下载并运行**
把本仓库下载到电脑（Code → Download ZIP，或 `git clone`），解压后**双击 `install.bat`**。
它会自动装依赖、设置开机自动运行，并打开设置界面。

**第 3 步：填账号密码**
在设置界面里：

1. 「认证适配器」选 **广东财经大学佛山校区（ePortal 认证）**；
2. 「校园网账号」填学号，「校园网密码」填密码（密码会存进 Windows 凭据管理器，不会写进任何文件）；
3. 点「保存」。

这样就完成了。右下角托盘会出现蓝色 Wi-Fi 图标，之后开机就会自动登录。
想先测一下：`python campus_login_main.py --login`，然后看日志。

> 不想装 Python 也能用：双击 `build_exe.bat` 打包出 `CampusLogin.exe`（需要一个能联网的环境装 PyInstaller）。

---

## 0. 当前状态（重要，先看这里）

| 部分 | 状态 |
| --- | --- |
| 通用认证框架（适配器架构） | ✅ 已完成 |
| 网络等待 / 认证状态检测 / 自动重试 | ✅ 已完成 |
| 账号密码安全保存（Windows 凭据管理器 / DPAPI） | ✅ 已完成，已实测 |
| 开机自动运行（任务计划程序 + 注册表双通道） | ✅ 已完成，幂等（重复安装不会产生两个启动项） |
| 系统托盘 + 设置界面（tkinter） | ✅ 已完成（托盘不可用时自动降级为后台运行） |
| 命令行（--status / --login / --install / --discover / --selftest ...） | ✅ 已完成 |
| 自动化测试 163 项 | ✅ 全部通过（`run_tests.bat`） |
| 离线自检 14 项场景 | ✅ 全部通过（`--selftest`，不联网、不需要密码） |
| **广东财经大学佛山校区接口** | ✅ 已按实测抓包配置好（见第 18 节） |
| 打包成 `CampusLogin.exe` | ✅ 脚本已就绪（`build_exe.bat`，需要联网装 PyInstaller） |

**程序不猜接口**：缺少必要参数时，它会在日志里写清楚"这里需要真实校园网请求信息
才能继续"，同时保证不崩溃、不乱发请求、不提交密码。

---

## 1. 项目介绍

* 开机后自动等待网络 → 判断是否需要认证 → 需要就自动登录 → 登录后常驻后台巡检。
* 已经认证：不重复登录。
* 认证过期或断线重连：自动重新登录。
* 遇到验证码 / 短信验证 / 二次认证：**停止自动尝试**，托盘弹提示，交给人工处理。
* 密码只保存在 Windows 凭据管理器（或 DPAPI 加密文件）里，**不会**出现在源码、
  配置文件、日志、README、Git 中。
* 轻量：核心只依赖 Python 标准库 + `requests`；托盘用系统 API 实现，不需要
  pystray / PyQt / Pillow。

---

## 2. 工作原理

```
程序启动（开机自动运行 / 双击 / 命令行）
        │
        ├─ 启动延迟（默认 10 秒，等系统网络栈就绪）
        │
        ├─ 等待网卡 / DHCP 就绪（拿到可用的本机 IPv4，最多等 120 秒）
        │
        ├─ 认证状态检测 check_authentication()
        │     访问连通性检测地址（默认：小米 / 华为 / vivo / 苹果 / 微软的 204 地址）
        │       * 得到 204 或期望内容  → 已认证 → 什么都不做，进入后台巡检
        │       * 得到 302 跳转         → 被门户劫持 → 需要认证
        │       * 返回 200 但内容被替换 → 被门户劫持 → 需要认证
        │       * 页面含"验证码"等关键词 → 需要人工处理
        │       * 全部超时 / 连不上     → 网络不可用 → 稍后重试
        │     不使用 ping 判断（校园网常常通 ICMP/DNS 但必须 Web 认证）
        │
        ├─ 需要认证 → Authentication Adapter（适配器）构造登录请求
        │      提交前先看一眼登录页面：如果页面要求验证码 → 不提交密码，直接转人工
        │
        ├─ 复核：再次调用 check_authentication()
        │       * 真的能上网了 → 认证成功
        │       * 仍然不能上网 → 按重试序列重试（5s → 10s → 20s → 30s → 60s）
        │
        └─ 后台巡检（默认每 300 秒一次）
               * 掉线/过期 → 自动重新登录
               * 已认证 → 静默
```

重试间隔、最大次数、巡检间隔、超时时间**全部是配置项**，不会高频无限请求。

---

## 3. 文件结构

```
CampusLogin/
├── campus_login_main.py         ← 源码运行入口（开机启动项指向它）
├── campus_login/                ← 程序本体
│   ├── cli.py                   命令行
│   ├── app.py                   托盘模式组装（服务线程 + 托盘 + 命令队列）
│   ├── settings_gui.py          设置界面（tkinter）
│   ├── service.py               核心状态机（等待网络→检测→登录→复核→重试）
│   ├── network.py               网络就绪判断 + 认证状态检测
│   ├── adapters/                认证适配器（校园网接口都在这里）
│   │   ├── base.py              适配器基类（参数构造、响应分类、脱敏）
│   │   ├── generic_portal.py    通用网页认证（Web Portal / HTTP 表单 / JSON）
│   │   ├── eportal.py           ePortal（深澜 / 城市热点）认证
│   │   ├── gdufe.py             广东财经大学佛山校区适配器（已配置好）
│   │   └── mock.py              模拟适配器（离线自检用）
│   ├── credentials.py           账号密码保存（凭据管理器 / DPAPI）
│   ├── credman.py / dpapi.py    两个 Windows 安全存储的 ctypes 封装
│   ├── winiface.py              网卡枚举（避开 Clash 等 TUN 虚拟网卡）
│   ├── config.py                配置读写 + 默认值 + 校验
│   ├── autostart.py             开机自动运行（任务计划程序 / 注册表）
│   ├── tray.py                  系统托盘（纯 ctypes Shell_NotifyIcon）
│   ├── logging_setup.py         日志 + 密码脱敏 + 自动轮转
│   ├── devserver.py             本机"假校园网门户"（自检/测试用）
│   └── simulation.py            模拟网络（离线测试用）
│   └── assets/                  托盘图标（tray.ico）与预览图
├── tests/                       163 项自动化测试（unittest，不需要联网）
├── tools/make_icon.py           重新生成图标（可选，需要 Pillow）
├── .github/workflows/tests.yml  GitHub Actions：推送时自动跑测试
├── LICENSE                      MIT 许可证
├── install.bat                  安装：设置开机自动运行（幂等）
├── uninstall.bat                卸载：取消开机自动运行
├── run_tray.bat                 立即启动托盘程序
├── selftest.bat                 离线自检（不联网、不需要密码）
├── run_tests.bat                跑自动化测试
├── build_exe.bat                打包成 CampusLogin.exe / CampusLoginCLI.exe
└── CampusLogin.spec             PyInstaller 打包配置
```

配置 / 凭据 / 日志保存在你的用户目录里，**不在项目文件夹**：

```
%APPDATA%\CampusLogin\
├── config.json          接口参数、重试策略（不含密码）
├── credentials.bin      仅在使用 DPAPI 后端时才存在的加密凭据
└── logs\app.log         日志（自动轮转，最大 512KB × 3）
```

---

## 4. 安装

### 4.1 直接使用源码（推荐先用这个跑通）

1. 安装 Python 3.9 或以上版本（安装时勾选 **Add python.exe to PATH**）。
2. 打开 PowerShell / CMD，进入本项目目录：

   ```powershell
   cd C:\你的路径\CampusLogin
   pip install -r requirements.txt
   ```

3. 先做一次离线自检（**不需要校园网、不需要密码**）：

   ```powershell
   python campus_login_main.py --selftest
   ```

   看到 `自检结果：11/11 项通过` 就说明程序本体没问题。

4. 设置开机自动运行并填写账号密码：

   ```powershell
   python campus_login_main.py --install      # 设置开机自动运行（幂等，重复执行不会重复添加）
   python campus_login_main.py --credential   # 输入账号密码（密码不回显）
   ```

   或者双击 `install.bat`（会依次完成上面两步）。

### 4.2 打包成 EXE（可选）

联网状态下双击 `build_exe.bat`，会生成：

* `dist\CampusLogin.exe` —— 无控制台窗口，双击即可在托盘后台运行（开机启动用这个）；
* `dist\CampusLoginCLI.exe` —— 带控制台窗口，方便用 `--status`、`--login` 等命令。

打包依赖 PyInstaller（只有打包时需要联网安装），运行 EXE 的电脑**不需要** Python。

---

## 5. 首次配置

### 5.1 图形界面（推荐）

```powershell
python campus_login_main.py --settings
```

界面里能设置：认证适配器、校园网登录页面、登录请求地址、检测地址、账号、密码、
请求方法、参数字段名、运营商/服务类型、附加参数、重试次数与间隔、开机自动运行。

"保存"会把**接口参数**写进 `%APPDATA%\CampusLogin\config.json`，
把**账号密码**写进 Windows 凭据管理器（或 DPAPI 加密文件）。

界面底部三个按钮：`立即检测`（测试当前认证状态）、`打开登录页面`、`查看日志`。

### 5.2 命令行配置

```powershell
python campus_login_main.py --credential      # 只设置账号密码
python campus_login_main.py --config          # 查看当前配置（不会显示密码）
```

也可以直接编辑 `%APPDATA%\CampusLogin\config.json`（字段说明见下一节）。

---

## 6. 如何填写校园网参数（以及如何获取这些参数）

### 6.1 需要哪些参数

| 参数 | 说明 | 举例 |
| --- | --- | --- |
| `adapter` | 用哪个适配器 | `generic`（通用）/ `eportal`（ePortal）/ `gdufe`（广财） |
| `portal_url` | 校园网登录页面地址（用于"打开登录页面"和人工认证） | `http://10.0.0.1/portal` |
| `login_url` | **登录请求的地址**（点"登录"时浏览器真正请求的那个 URL） | `http://100.64.13.17:801/eportal/portal/login` |
| `request_method` | 请求方法 | `POST` 或 `GET` |
| `content_type` | 参数格式 | `form`（表单）或 `json` |
| `username_field` | 账号参数的**名字** | `username` / `account` / `userid` |
| `password_field` | 密码参数的**名字** | `password` / `pwd` |
| `username_prefix` | 账号前缀（ePortal 会在账号前加 `,0,`） | `,0,` |
| `service_field` / `service_value` | 运营商 / 服务类型参数（有的学校有） | `service` = `校园网` |
| `extra_fields` | 其它固定参数 | `nasip=10.0.0.1` |
| `password_transform` | 密码是否需要先做散列 | `none` / `md5` / `sha1` / `sha256` |
| `check_url` | 认证状态检测地址，**用来判断"现在需不需要登录"**（留空则用内置 204 检测地址） | `http://10.0.0.1/generate_204` |
| `internet_check_url` | **独立的**互联网连通性检测地址（留空＝不做这项检测） | `http://www.baidu.com/robots.txt` |
| `captcha_precheck` | 提交密码前是否先看登录页面有没有验证码输入框 | `true` / `false` |

> 只要 `login_url` 和两个字段名填对，绝大多数校园网就能自动登录。

### 6.2 用 Chrome / Edge 开发者工具抓取登录请求（不会暴露密码）

```
1. 断开校园网认证（或换个没认证的设备），让浏览器打开任意网页
2. 页面会自动跳到校园网登录页
3. 按 F12 打开开发者工具
4. 切到 Network（网络）面板
5. 勾选 Preserve log（保留日志），点一下清空按钮
6. 在页面上输入账号密码 → 点击"登录"
7. 在 Network 列表里找到提交登录的那个请求：
      * 一般叫 login / auth / portal / Logon / dispatch 之类
      * 类型通常是 document / xhr / fetch
8. 点开它，看三个地方：
      Headers  → 请求 URL、Request Method（GET/POST）、Content-Type
      Payload  → 参数名（例如 username=xxx&password=xxx&service=xxx）
      Response/Preview → 登录成功或失败时返回了什么
9. 把这些**非敏感信息**发给我：
      * 请求 URL（域名可以打码）
      * 请求方法
      * 参数名列表（值可以全部写 xxx）
      * 成功时返回内容示例、失败时返回内容示例
      * 登录成功后浏览器地址栏跳到了哪里
```

**不要发密码**。密码请你自己在设置界面或 `--credential` 里输入，
程序会把它存进 Windows 凭据管理器。

### 6.3 让程序帮你分析登录页面

```powershell
python campus_login_main.py --discover http://10.0.0.1/portal
```

它会拉取登录页 HTML，列出所有表单、字段名，并直接给出一段
`config.json` 建议（不会提交任何密码）。如果页面是 JavaScript 提交的，
它会明确告诉你"需要去 Network 面板抓包"。

### 6.4 ePortal（深澜 / 城市热点）认证：result=1 就是认证成功

很多学校的认证接口是 ePortal 协议，特征很明确：

```
GET http://100.64.13.17:801/eportal/portal/login
    ?callback=dr1003 & login_method=1 & user_account=,0,账号 & user_password=密码
    & wlan_user_ip=10.20.30.40 & wlan_user_ipv6= & wlan_user_mac=000000000000
    & wlan_ac_ip=100.64.13.18 & wlan_ac_name= & jsVersion=4.1.3
    & terminal_type=1 & lang=zh-cn & v=1779
响应：dr1003({"result":1,"msg":"Portal协议认证成功！"});
```

程序对它的处理：

1. 自动解析 `dr1003({...})` 这层 JSONP 包装，取出里面的 JSON；
2. `result == 1` → **认证服务器已接受登录** → 状态直接变成"认证成功"；
3. `result != 1` → 再看 `msg`：
   * 含"密码错误 / 用户不存在" → 账号密码错误，**不再快速重试**；
   * 含"验证码 / 短信验证 / 二次认证" → 转人工处理；
   * 含"已在线 / 已登录" → 视为认证成功；
   * 其它 → 认证失败，并把 `msg` 原文写进日志；
4. 登录地址里含 `eportal` 时自动改用 **GET**（这个接口就是 GET），避免参数位置错误；
5. **不会**再用"访问网页时是否被重定向到登录页"去否决已经成功的认证。

两个容易漏掉的细节（佛山校区实测）：

* **账号要带 `,0,` 前缀**：实测抓包里是 `user_account=%2C0%2C<你的学号>`（即 `,0,` + 学号）。
  程序会自动拼上前缀，你在设置里只需要填干净的账号。前缀可以在"账号前缀"里改。
* **`wlan_user_ip` 必须是你当前的校园网 IP**，每次可能不一样。程序用 `{local_ip}`
  占位符自动填（并会跳过 Clash/FlClash 的 `198.18.x.x` 虚拟网卡地址）。

两个状态是分开的，互不干扰：

| 状态 | 含义 | 怎么来的 |
| --- | --- | --- |
| **认证成功** | 认证服务器明确告诉你"登录成功" | 登录接口返回 `result=1`（或响应里出现"登录成功/认证成功"） |
| **已连接互联网** | 认证成功 **并且** 真的能访问外网 | 需要你填 `internet_check_url`，程序会额外探一次；没填就跳过 |

也就是说：即使刚登录完的一瞬间网络还处于"半放行"状态（探测被 302 重定向），
也不会再把正确的登录判成失败，只会把"互联网连通性检测失败"作为单独一条信息记录。

对应的日志长这样：

```
INFO 登录请求已发送（GET http://100.64.13.17:801/eportal/portal/login，HTTP 200）
INFO 登录成功：Portal 认证成功：Portal协议认证成功！
INFO Portal 认证成功
INFO 认证成功：Portal 认证成功：Portal协议认证成功！
INFO 未配置互联网连通性检测地址，跳过互联网连通性检测
```

### 6.5 关于代理 / VPN（Clash、FlClash 等）

如果你在用 TUN 模式的代理，注意三件事（`python campus_login_main.py --status` 会提示你）：

1. **本机 IP 会被换成虚拟地址**（例如 `198.18.0.1`）。程序已经会自动跳过虚拟网卡，
   仍然取真实网卡上的地址填到 `wlan_user_ip`，所以这一条不用你操心。
2. **认证请求可能被代理转发到远端服务器**，校园网门户根本收不到你的登录请求。
3. **连通性探测可能被代理"代答"成功**，于是程序误以为"已经认证"，实际并没认证。

所以最省事的做法是：**认证期间不要开 TUN 模式代理**（开机自动运行同理）。
如果不想关代理，就在代理规则里给校园网网段加直连：

```yaml
rules:
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve   # 校园网门户 / AC
  - IP-CIDR,172.31.0.0/16,DIRECT,no-resolve   # 你的校园网地址段
```

---

## 7. 账号密码是怎么保存的

程序**不会**把密码写进 `.py` / `.json` / `.toml` / `.ini` / README / Git / 日志。

保存顺序（自动选择，不需要管理员权限）：

1. **Windows 凭据管理器**（默认）
   `控制面板 → 用户账户 → 凭据管理器 → Windows 凭据` 里可以看到：
   * 名称：`CampusLogin/Credentials`
   * 内容是一段经过处理的 JSON（账号 + 密码）
2. **DPAPI 加密文件**（凭据管理器不可用时自动退回）
   `%APPDATA%\CampusLogin\credentials.bin`，用 `CryptProtectData` 加密，
   **只有同一台电脑上的同一个 Windows 用户**能解密（复制到别的电脑/别的账户打不开）。
3. 内存（仅当上面两种都不可用时，仅存在于本次运行，重启失效）。

想确认密码没有被明文保存，可以搜索你的用户目录：

```powershell
Select-String -Path "$env:APPDATA\CampusLogin\*" -Pattern "你的密码" -SimpleMatch
```

没有任何输出就说明配置文件/凭据文件里没有明文密码。

---

## 8. 如何开启开机自动登录

### 方式一：命令行 / 批处理（推荐）

```powershell
python campus_login_main.py --install
```

或者双击 `install.bat`。它会：

1. 在 **任务计划程序**里创建一个当前用户的计划任务（任务名 `CampusLogin`），
   触发条件 = "登录时"，不需要管理员权限，不会弹黑窗口；
2. 如果任务计划程序不可用（被组策略/安全软件拦了），自动改用
   **注册表** `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`；
3. **幂等**：重复执行只会有一个启动项，不会重复添加。

### 方式二：图形界面

打开 `--settings`，勾选 **开机自动运行**，保存即可。

### 方式三：托盘菜单

右键托盘图标 → `开机自动运行`（可勾选/取消，立即生效）。

### 验证是否设置成功

```powershell
python campus_login_main.py --status
# 会打印：开机自动运行：已开启

# 或手动查看任务计划程序
schtasks /Query /TN CampusLogin
```

程序启动后会在 `%APPDATA%\CampusLogin\logs\app.log` 写下完整过程；你也可以在
任务管理器的"启动"标签页里看到注册表方式的启动项。

---

## 9. 如何关闭开机自动登录

```powershell
python campus_login_main.py --uninstall
```

或者双击 `uninstall.bat`（它会：取消开机自启动 → 结束后台进程 → 询问是否删除
已保存的账号密码和配置）。托盘菜单里取消勾选 `开机自动运行` 也一样。

`--uninstall` 同样是**幂等**的：启动项已经被删掉时再执行一次不会报错，
并且会同时清理任务计划程序和注册表两种方式留下的残留。

---

## 10. 如何手动登录 / 手动查看状态

```powershell
python campus_login_main.py --status     # 看一眼现在是否已认证（不会登录）
python campus_login_main.py --check      # 立即检测一次（不会登录）
python campus_login_main.py --login      # 立即执行一次完整登录流程
python campus_login_main.py --quit       # 让正在后台运行的程序退出
python campus_login_main.py --settings   # 打开设置界面
python campus_login_main.py --tray       # 启动托盘后台（等价于不带参数）
```

托盘右键菜单里也有：`立即检查`、`立即登录`、`打开登录页面`、`查看日志`、`设置`、
`开机自动运行`、`退出`；托盘状态行会显示：正在等待网络 / 正在检查认证状态 /
正在登录 / 已认证 / 认证失败 / 需要人工验证。

需要人工处理时（例如出现验证码），托盘会弹气泡提示，你可以点"打开登录页面"
自己在浏览器里完成认证；完成后程序会自动识别到"已认证"，回到正常巡检状态。

### 退出码（方便脚本判断）

| 退出码 | 含义 |
| --- | --- |
| 0 | 已认证 / 操作成功 |
| 1 | 未认证 / 认证失败 |
| 2 | 需要人工处理（验证码、二次认证） |
| 3 | 配置或参数错误（例如没填登录地址、开机启动设置失败） |

---

## 11. 如何查看日志

* 文件位置：`%APPDATA%\CampusLogin\logs\app.log`
* 命令行查看：

  ```powershell
  python campus_login_main.py --log-path      # 打印日志文件路径
  python campus_login_main.py --tail 50       # 打印最后 50 行
  ```

* 托盘菜单 → `查看日志` 会用记事本打开。

日志长这样：

```
2026-09-15 08:01:03 INFO 程序启动
2026-09-15 08:01:08 INFO 网络连接正常
2026-09-15 08:01:09 INFO 检查校园网认证状态
2026-09-15 08:01:10 INFO 当前未认证：访问被重定向到校园网认证页面
2026-09-15 08:01:10 INFO 开始校园网认证
2026-09-15 08:01:11 INFO 登录成功：认证接口返回成功标志
2026-09-15 08:01:11 INFO 认证成功：校园网已连接/认证成功
```

**日志里绝不会出现**：密码、Cookie、Token、完整 Authorization 头。
程序内置了脱敏过滤器（既替换真实密码，也会把 `password=xxx`、
`Authorization: xxx`、`Cookie: xxx`、`JSESSIONID=xxx` 这类内容自动打码）。
日志自动轮转：单文件最大 512KB，保留 3 份，不会无限增长。

---

## 12. 常见错误与处理

| 现象 / 日志 | 原因 | 处理 |
| --- | --- | --- |
| `尚未保存校园网账号和密码` | 还没输入账号密码 | `--credential` 或设置界面填写 |
| `这里需要真实校园网请求信息才能继续：尚未填写"登录请求地址"` | 还没填校园网接口 | 见第 6 节，先用 `--discover` 分析登录页 |
| `校园网账号或密码可能错误，请检查配置` | 密码错 / 账号错 / 服务类型选错 / 密码需要散列 | 检查账号密码；确认 `service_value`；确认是否需要 `password_transform` |
| `需要人工完成认证（验证码 / 二次认证）` | 学校启用了验证码、短信、二次认证 | 按提示手动打开登录页完成认证；程序不会绕过它 |
| `网络不可用（未获取到可用的本地 IP）` | Wi-Fi 没连上 / 网线没插 / DHCP 没完成 | 检查网络；程序默认最多等 120 秒 |
| `校园网认证服务器错误（HTTP 5xx）` | 学校服务器临时故障 | 程序会按重试序列自动重试 |
| `认证接口响应格式发生变化` | 学校改了接口 / 页面结构 | 重新按第 6 节抓一次请求，更新配置或适配器 |
| `设置开机自动运行失败：拒绝访问` | 安全软件/组策略拦截 | 已自动尝试另一种方式；仍失败可手动把快捷方式放进 `shell:startup` |
| 托盘图标不见了 | 资源管理器重启 | 程序会自动重新添加图标（监听 TaskbarCreated） |
| 托盘右键"退出"没反应、进程关不掉 | 旧版本退出逻辑的 bug（WM_CLOSE 死循环） | 已修复；旧版本用 `python campus_login_main.py --quit`，或任务管理器结束 pythonw.exe |
| 开机后看不到托盘图标 | 开机瞬间资源管理器还没准备好 | 程序会自动重试 3 次（每次隔 5 秒）；仍失败则转为后台运行，可用 `--status` 查看、`--quit` 关闭 |
| 日志目录/配置文件写不进去 | 权限异常、磁盘满、安全软件拦截 | 程序会自动改用其它可用目录，不会因此启动失败 |
| `无法显示托盘图标` 弹窗 | 当前会话没有桌面 Shell | 程序会退化为后台运行，功能不受影响，用 `--status` / 日志查看 |
| `--status` 提示"检测到代理/VPN 虚拟网卡" | 开着 Clash / FlClash 等 **TUN 模式**代理 | 认证期间建议关闭它；或在代理里给 `100.64.0.0/10`、`172.31.0.0/16` 加 `DIRECT` 规则 |
| `wlan_user_ip` 填成了 `198.18.x.x` | 同上，虚拟网卡抢占了默认路由 | 程序已会自动跳过虚拟网卡，选真实网卡地址；`--status` 会提示你 |

---

## 13. 如何重新配置

```powershell
python campus_login_main.py --settings     # 改任何参数
python campus_login_main.py --credential   # 只改账号密码
python campus_login_main.py --config       # 查看当前配置（不含密码）
```

* 改完账号密码后**不需要**重新安装开机启动。
* 托盘程序在检测到配置文件变化时会自动重新加载（不用退出重启）。
* 想彻底重置：删掉 `%APPDATA%\CampusLogin\` 目录，然后重新 `--credential` 和
  `--settings`。（凭据管理器里的条目可在"凭据管理器 → Windows 凭据"里删除
  `CampusLogin/Credentials`）

---

## 14. 如何卸载

1. 取消开机自动运行：

   ```powershell
   python campus_login_main.py --uninstall
   ```

   或双击 `uninstall.bat`。

2. 退出正在运行的程序：托盘菜单 → `退出`（或 `uninstall.bat` 里会自动结束进程）。
3. 删除程序文件夹（解压出来/复制过来的整个 `CampusLogin` 目录）。
4. 删除用户数据（可选）：`%APPDATA%\CampusLogin`，以及凭据管理器中的
   `CampusLogin/Credentials`。

---

## 15. 如何针对其它校园网修改 / 新增 Adapter

程序把"怎么登录"和"什么时候登录"彻底分开了：接口变了、换学校了，只动适配器。

### 15.1 只换参数（不改代码）

大多数学校只要在设置里填好 `login_url`、字段名、`service` 就能用，
适配器继续用 `generic`。

### 15.2 新增一个适配器

1. 复制 `campus_login/adapters/generic_portal.py` 或 `gdufe.py`：

   ```python
   from .generic_portal import GenericPortalAdapter

   class MySchoolAdapter(GenericPortalAdapter):
       name = "myschool"
       display_name = "某某大学校园网"
       LOGIN_URL = "http://10.1.1.1/login"        # 真实抓包结果
       CHECK_URL = "http://10.1.1.1/generate_204"
       USERNAME_FIELD = "username"
       PASSWORD_FIELD = "password"
       SERVICE_FIELD = "service"
       DEFAULT_EXTRA_FIELDS = {"nasip": "10.1.1.1"}
   ```

2. 在 `campus_login/adapters/__init__.py` 的 `ADAPTERS` 字典里注册，
   在 `campus_login/config.py` 的 `VALID_ADAPTERS` 里加上名字。
3. 如果登录前需要先 GET 一次门户页拿 Cookie/Token，重写 `login()`：
   先 `self.session.get(portal)`，再从响应里取出需要的参数，再提交。
4. 如果响应格式很特殊，重写 `classify(status_code, body, location)` 返回
   `LoginStatus`；**最终是否成功仍由"能不能真的上网"复核**，所以不用担心误判。

### 15.3 如果必须执行 JavaScript 才能登录

优先想办法用纯 HTTP 完成（抓包看真实的 POST 参数）。只有在接口确实依赖
JS 运行时（例如用 WebAssembly/加密脚本生成 Token）时，才考虑浏览器自动化
（Selenium/Playwright 驱动 Edge/Chrome），这时程序只在"登录"这一步调用浏览器，
其它逻辑不变。**注意：仍然不会去绕过任何验证码。**

---

## 16. 安全说明

* 密码只存在 Windows 凭据管理器 / DPAPI 加密文件中，源码、配置、日志里都没有。
* 不要关闭 HTTPS 证书校验：程序默认开启校验；如果学校门户用自签名证书，
  请把证书导出成 PEM 并在设置里填 `ca_bundle`，而不是关闭校验。
* 日志自动脱敏 + 自动轮转，避免密码泄漏和磁盘占用无限增长。
* 所有网络请求都有超时（默认检测 8 秒 / 登录 10 秒），所有异常都被捕获，
  不会因为学校接口异常把程序搞崩。
* 不使用高频无限请求：失败后按 5/10/20/30/60 秒的序列重试，之后每 60 秒一次。
* 只做正常的账号登录；检测到验证码、短信验证、二次认证时**主动停止**并提示人工处理。
* `.gitignore` 已经排除 `config.json`、`credentials.bin`、`logs/`，
  即使你把这个目录放进 Git 也不会把凭据提交上去。

---

## 17. 测试

不需要联网、不需要真实账号，全部用本机模拟校园网门户（`campus_login/devserver.py`）：

```powershell
python -m unittest discover -s tests -t .   # 163 项测试
python campus_login_main.py --selftest      # 20 秒内跑完的离线自检（11 项场景）
```

覆盖内容（对应需求里的测试清单）：

* 配置读取 / 保存 / 非法值修正 / 配置文件损坏恢复
* 凭据保存与读取（内存 / DPAPI 真实加解密 / Windows 凭据管理器真实读写）
* 日志格式、轮转、密码与 Cookie 脱敏
* 网络检测（本机 IP、网卡未就绪、等待网络）
* 认证状态检测（已认证 / 需要认证 / 内容被替换 / 网络不可用 / 验证码）
* 登录成功（表单、JSON、302 跳转、自定义字段名、运营商参数、MD5 密码）
* 登录失败（密码错误、服务器 500 重试、未知响应格式）
* 网络断开、重试机制、重试间隔序列、退出打断
* 开机启动安装 / 卸载 / 幂等性 / 失败自动降级
* 托盘结构与窗口消息分发

真实的校园网登录**不会**写进自动化测试（测试只打本机 `127.0.0.1`）。

---

## 18. 广东财经大学佛山校区接口说明

下面是实际抓包（2026-09）得到的接口形态，已经写进 `campus_login/adapters/gdufe.py`，
选这个适配器就能直接用，不需要你再去抓包。

| 项目 | 值 |
| --- | --- |
| 协议 | ePortal（深澜 / 城市热点）JSONP |
| 登录接口 | `GET http://100.64.13.17:801/eportal/portal/login` |
| 门户页面 | `http://100.64.13.17/a79.htm?wlanacip=100.64.13.18` |
| 账号参数 | `user_account`，值 = `,0,` + 账号 |
| 密码参数 | `user_password`，**明文**提交（未做散列） |
| 固定参数 | `callback=dr1003`、`login_method=1`、`wlan_user_mac=000000000000`、`wlan_ac_ip=100.64.13.18`、`jsVersion=4.1.3`、`terminal_type=1`、`lang=zh-cn` |
| 动态参数 | `wlan_user_ip` = 本机当前校园网 IP（`{local_ip}` 自动填）、`wlan_ac_ip` = 你当前所连的 AC（`{portal_ac_ip}` 自动从门户页面取）、`v` = 随机数（`{random}`） |
| 成功响应 | `dr1003({"result":1,"msg":"Portal协议认证成功！"})` |
| 失败响应 | `dr1003({"result":0,"msg":"用户名或密码错误"})` |

教学楼 / 宿舍楼连的 AC 可能不是同一台，所以程序优先从你浏览器里看到的门户页面
（`a79.htm?wlanacip=xxx`）自动取 AC 地址；取不到时用上面的默认值兜底。

如果以后学校改了地址或参数，优先在设置界面覆盖：
`login_url` / 各字段名 / `username_prefix` / `附加固定参数` 都可以改，不必改代码。
改完确认能用了，欢迎提 Issue 或 Pull Request 让其他同学少踩坑。

几个已知限制：

1. 认证期间请**关闭 TUN 模式代理**（Clash / FlClash），否则登录请求可能到不了校园网；`--status` 会提示你。
2. 如果学校启用了验证码 / 短信验证 / 二次认证，程序只会提示人工处理（不会去绕过）。
3. 如果学校有"认证状态查询"接口，填到「认证状态检测地址」会比通用的 204 探测更准确。

有问题请联系我
wx：zyffwazqs
