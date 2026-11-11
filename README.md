# MultiRDP · Windows 多用户远程桌面管理

[English](README.en.md) | **简体中文**

在 Windows 10/11 上快速创建多个互相隔离的本地账号，每个账号一键打开独立的远程桌面窗口，
并通过**系统托盘常驻**的小工具统一管理。适合在一台电脑上同时跑多个隔离环境（游戏多开、
测试环境、给家人/同事开独立桌面等）的场景。

![Web UI 截图](docs/screenshot_webui.png)

> 界面右上角「English / 中文」可实时切换语言（英文界面效果见
> [docs/screenshot_webui_en.png](docs/screenshot_webui_en.png)）。

> 界面说明：网格第一张卡片就是「新建账号」，点加号（或卡片里的「创建账号」）自动生成随机账号 +
> 强密码并保存；每张账号卡片的「登录」用系统自带 mstsc 连本机
> 打开独立远程桌面；「重置」注销会话（画面卡死时用）；「删除用户」彻底清理
> 账号、用户目录和注册表残留。

## 功能

- **一键创建用户**：随机用户名（`p` + 6 位，避开易混字符）、14 位强密码，自动加入
  `Remote Desktop Users`，可选加入 `Administrators`（UAC 只需点「是」）
- **一键登录**：用系统自带的 `mstsc.exe` 连接本机，每个用户一个独立窗口；窗口建好后会自动
  翻到最上层并激活（客户端由后台进程拉起，没有前台权限，不主动置前就会躲在别的窗口后面）；
  用户已在运行时点「登录」等同于「把那个窗口带到前台」；
  mstsc 不能从命令行收密码，程序用 `cmdkey` 把账号密码写进凭据库，再给每个账号分配一个
  固定的回环别名（`127.0.0.x`）把凭据隔离开，退出时清理；
  分辨率可设置（自动窗口化：挑能完整放进屏幕的最大常用档，1080p 屏上是 1600x900；
  也可手动选具体档位，塞不进屏幕会自动等比缩小；只有明确选「全屏」才真全屏）
- **会话保持（窗口关掉、会话仍激活）**：点「隐藏窗口」只是把远程窗口藏起来，RDP 连接不断，
  所以会话一直是 `Active`——里面的程序照常渲染、继续持有焦点，任务栏和 Alt+Tab 里都看不到；
  点「显示窗口」原样回来。直接点窗口右上角的 X 会真的断开（会话变 `Disc`，此时没有输入通道，
  窗口拿不到真实焦点），程序会自动在后台重连一次把会话拉回 `Active`（保持窗口原来的显示状态，
  可见的还可见、隐藏的仍隐藏；`config.json` 里
  `keep_alive: false` 可关闭）。只有「重置」才会注销会话、结束所有程序。窗口归属和隐藏
  状态记在 `clients.json`，管理程序重启后会认回原来的窗口，「显示窗口」照样能用
- **会话重置**：注销该用户会话，解决黑屏/卡死；重置后重新登录即全新桌面
- **彻底删除用户**：注销会话 → 删除账号 → WMI 删用户配置 → 强删残留目录 →
  清理 `ProfileList` 注册表 → 校验结果；删不干净会保留条目并标记「残留」，可重试
- **卡片式面板 + 画面预览**：每个账号一张卡片，卡片上半部是当前远程桌面的缩略图。不做实时
  推流——只在网页处于前台时每 6 秒抓一帧（`PrintWindow`，窗口被别的窗口挡住也能截到），
  切到别的标签页就自动停，几乎不占 CPU。点缩略图即可把那个远程窗口切到前台；窗口隐藏时
  卡片显示「窗口已隐藏 · 点一下打开」
- **孤儿目录扫描**：本工具生成的随机用户名目录若残留，会重新列在界面上供清理
- **修复登录白屏**：检测 OpenStreamMS 是否劫持了 RDP 图形管线
  （`RDP-Tcp\LoadableProtocol_Object`），一键还原为微软原版协议
- **托盘常驻**：`MultiRDP.exe` 后台运行，托盘菜单提供「打开 Web UI / 新用户默认授予管理员 / 退出」
- **中英双语界面**：右上角一键切换，选择保存在 `config.json`（`lang`），接口返回的错误提示同步切换

## 快速开始

环境要求：Windows 10/11 x64、Python 3.9+、管理员权限。

### 方式一：双击脚本运行（调试用）

```text
scripts\run.bat
```

自动请求管理员权限，以控制台模式启动 Web UI 并打开 <http://127.0.0.1:8123>。
关掉窗口即停止服务，日志直接显示在控制台。

### 方式二：打包成单个 exe（托盘常驻）

```text
scripts\build.bat
```

产物为 `dist\MultiRDP.exe`（PyInstaller，单文件、无控制台窗口）。远程客户端用的是 Windows
自带的 `mstsc.exe`，不需要额外复制任何文件。首次使用：

1. 双击 `dist\MultiRDP.exe`（自动请求管理员权限），程序驻留托盘
2. 托盘右键 → 打开 Web UI

图标由 `scripts/make_icon.py` 矢量绘制（超采样后降采样，16px 不发糊），`build.bat`
每次打包前自动重新生成 `assets/multirdp.ico` 与 `assets/tray.png`；构建参数集中在
`MultiRDP.spec`。想换配色或改格子数量，改脚本里的常量重跑即可。

### 方式三：直接跑 Python

```powershell
python src\webui\server.py
```

## 项目结构

```text
multi-rdp/
├── src/
│   ├── tray_app.py          # 托盘程序入口（pystray）
│   └── webui/
│       ├── server.py        # Web UI + 用户管理核心（127.0.0.1:8123）
│       ├── screencap.py     # 纯 ctypes GDI 截屏小工具
│       ├── thumb.py         # 抓远程窗口画面 → JPEG 缩略图（卡片预览用）
│       ├── config.json      # 界面配置（admin_default / lang / resolution）
│       └── users.json.example  # 用户库模板（真实 users.json 不入库）
├── 3rd/                     # 预编译组件（直接可用，无需本地编译）
│   ├── TermWrap.dll         # 多会话补丁（423 KB）
│   ├── patchtest.exe        # 离线补丁自检（27 特征码验证）
│   └── patchlive.exe        # 活体补丁器（不重启给 TermService 打补丁）
├── scripts/
│   ├── run.bat              # 双击运行/调试
│   ├── build.bat            # 打包单 exe（图标生成 → PyInstaller）
│   ├── make_icon.py         # 矢量绘制应用图标，产出 assets/*.ico|png
│   ├── fix_termwrap.bat     # 设置 ServiceDll 持久化（重启后自动生效）
│   ├── rollback_termwrap.bat# 回滚 ServiceDll 到原值
│   └── check_termwrap.ps1   # 检查 TermWrap 加载状态与并发会话开关
├── assets/
│   ├── multirdp.ico         # 多尺寸应用图标（16/24/32/48/64/128/256）
│   ├── multirdp.png         # 1024px 主图
│   └── tray.png             # 托盘专用简化版（16px 可读）
├── MultiRDP.spec            # PyInstaller 构建入口（挂图标 + 内嵌 tray.png）
└── docs/                    # 截图等文档资源
```

## 多会话补丁（TermWrap）

Windows 客户端版默认只允许一个远程会话。`TermWrap.dll` 通过运行时给 `termsrv.dll`
打补丁绕过该限制，实现**多个用户同时在线**的多会话远程桌面。

- 预编译 dll：`3rd/TermWrap.dll`，直接可用，**无需本地编译**（源码不随本仓库分发）

使用方式（二选一）：

| 方式 | 操作 | 特点 |
| --- | --- | --- |
| 持久化 | `scripts\fix_termwrap.bat` | 把 TermService 的 `ServiceDll` 指向 TermWrap.dll，重启后自动生效；`rollback_termwrap.bat` 可回滚 |
| 活体补丁 | `3rd\patchlive.exe` | 不重启、不停服务，直接给运行中的 TermService 进程内存打补丁 |

安装后可用 `scripts/check_termwrap.ps1` 检查：ServiceDll 值、TermService 进程加载的模块、
`EnableConcurrentSessions` 开关以及当前会话列表。

> 关键注册表：`HKLM\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll`
> 原值备份在 `C:\Users\Public\termwrap_backup.json`（由 `fix_termwrap.ps1` 写出）。

## 关于远程客户端

客户端用的是 Windows 自带的 `mstsc.exe`，不需要附带任何文件，也不依赖第三方组件。

mstsc 不能从命令行接收账号密码，所以程序分两步拿凭据：

1. `cmdkey /generic:TERMSRV/<别名> /user:<账号> /pass:<密码>` 把密码写进当前用户的
   Windows 凭据库（DPAPI 加密，只有本机本用户能解密）
2. `mstsc /v:<别名> /w:<宽> /h:<高>`

凭据在 Windows 里是**按主机名**存的，所有账号都连 `127.0.0.1` 会互相覆盖，所以每个账号
分到一个固定的回环别名（`127.0.0.0/8` 整段都通到本机，效果与 `127.0.0.1` 相同）。别名
分配记在 `aliases.json`：既保证同一账号永远同一个别名，也保证不同账号绝不重复——早先用
`crc32 % 200` 直接算，10 个账号就有 20% 概率撞车，撞了就是「登录后进了别人的桌面」。

程序正常退出时用 `cmdkey /delete` 清掉自己写的凭据。如果程序是被强杀的，可以自查残留：

```text
cmdkey /list | findstr TERMSRV/127.0.0.
```

### 首次连接会有一次证书提示

第一次连本机回环时，mstsc 会弹「无法验证此远程计算机的身份」。程序会自动勾上「不再询问」
并点「是」，之后不再出现。万一系统语言或弹窗文案变了导致它点不掉，手动确认一次即可，
之后同样永久静默。

### 客户端进程退出 / 窗口消失

- 客户端进程退出（自己崩掉或被点 X 关掉）后自动重连，并且**保持原来的可见性**：
  窗口可见就重连后显示并激活，隐藏着就继续隐藏；5 分钟内反复崩会自动拉长重连间隔
- 相关动作都写进 `client.log`，带「mstsc 启动 / 客户端退出 / 保活重连」字样

### 为什么不用 FreeRDP（历史）

早期版本用 FreeRDP（`sdl3-freerdp.exe`），但 **3.32.0 在 Windows 10 26H1（build 28000）
上会稳定崩**：新版系统发出的快路径位图更新它解析不了，`client.log` 里固定是
`Fastpath update Bitmap [1] failed, status 0`，进程随后以 `0xc0000005` 退出，每次崩在
同一个偏移 `0x886e83`（上游 [issue #10067](https://github.com/FreeRDP/FreeRDP/issues/10067)、
[#13883](https://github.com/FreeRDP/FreeRDP/issues/13883)，3.32 之后才修）。

试过的规避手段都不行：

- `/gfx:AVC444`（走图形管线绕开位图）——更糟，崩溃从 5 分钟一次变成 40 秒一次
- `/relax-order-checks`（坏更新跳过而不是断开）——坏数据被跳过但读指针不前进，
  同一条更新被无限重解析，实测一分钟刷 1.3 万条日志、窗口彻底卡死

所以从这一版起换成 mstsc，`3rd/FreeRDP/` 已从仓库移除。要回退，`git log` 里
`0b756dc` 之前的版本就是 FreeRDP 实现。

## 安全提示

- `users.json` 保存了托管用户的**明文密码**（供一键登录使用），已被 `.gitignore` 排除，
  请勿提交或分享；仓库里只有 `users.json.example` 模板
- Web UI 只监听 `127.0.0.1`，不对局域网/公网开放
- 本工具会创建/删除本地账号、修改用户目录与注册表，**请仅在自己拥有管理权限的机器上使用**

## 技术栈

Python 3（标准库 `http.server` + `ctypes`/`winreg`）、pystray + Pillow（托盘）、
PyInstaller（打包）、mstsc（Windows 自带远程桌面客户端）；TermWrap 多会话补丁直接使用预编译 dll。

## License

[MIT](LICENSE)
