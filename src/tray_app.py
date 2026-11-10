# -*- coding: utf-8 -*-
"""MultiRDP 托盘程序。

后台运行用户管理 Web UI（server.py），在系统托盘提供图标菜单：
  - 打开 Web UI
  - 新用户默认授予管理员（勾选项，写入 config.json）
  - 退出

打包：scripts/build.bat 会用 PyInstaller 把本文件打成单 exe（MultiRDP.exe）。
"""
import os
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
# 开发态把 src/ 加进 sys.path，才能以 `webui` 包导入；
# 打包态（PyInstaller）webui 包已在 sys.path 中，无需处理。
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, HERE)

from webui import server as server_mod   # noqa: E402


def _elevate_and_exit():
    """没有管理员权限时弹 UAC 重启自己；用户拒绝则弹窗提示后继续运行。"""
    import ctypes
    if server_mod._is_admin():
        return
    if getattr(sys, 'frozen', False):
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', sys.executable, None,
            os.path.dirname(os.path.abspath(sys.executable)), 1)
    else:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', sys.executable,
            '"%s"' % os.path.abspath(__file__), HERE, 1)
    if rc > 32:
        sys.exit(0)
    ctypes.windll.user32.MessageBoxW(
        None,
        '未授予管理员权限：创建/删除用户等功能将不可用。\n'
        '如需完整功能，请以管理员身份重新运行本程序。',
        'MultiRDP', 0x48)   # MB_ICONWARNING | MB_TOPMOST


def _icon_image():
    """程序生成的托盘图标：紫色圆角屏幕 + 白色窗口。"""
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 6, 60, 44], radius=7, fill=(83, 74, 183, 255))
    d.rectangle([12, 14, 52, 34], fill=(255, 255, 255, 255))
    d.rectangle([12, 14, 52, 19], fill=(126, 117, 214, 255))
    d.polygon([(28, 44), (36, 44), (38, 52), (26, 52)], fill=(83, 74, 183, 255))
    d.rounded_rectangle([20, 52, 44, 57], radius=2, fill=(83, 74, 183, 255))
    return img


def _open_webui(icon=None, item=None):
    webbrowser.open('http://127.0.0.1:%d' % server_mod.PORT)


def _admin_default_checked(item):
    return bool(server_mod.admin_default())


def _toggle_admin_default(icon, item):
    server_mod.set_admin_default(not server_mod.admin_default())


def _on_exit(icon, item):
    global _srv
    try:
        _srv.shutdown()
    except Exception:
        pass
    icon.stop()


_srv = None


def main():
    _elevate_and_exit()
    global _srv
    _srv = server_mod.start_server()
    threading.Thread(target=_srv.serve_forever, daemon=True).start()

    import pystray
    menu = pystray.Menu(
        pystray.MenuItem('打开 Web UI', _open_webui, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('新用户默认授予管理员', _toggle_admin_default,
                         checked=_admin_default_checked),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出', _on_exit),
    )
    icon = pystray.Icon('MultiRDP', _icon_image(), 'MultiRDP - 多用户 RDP 管理', menu)
    icon.run()


if __name__ == '__main__':
    # --selftest：只验证打包是否完整（webui.server 可导入、配置可读），
    # 不启动托盘、不请求管理员权限，用退出码报告结果（0 = 正常）。
    if '--selftest' in sys.argv:
        try:
            server_mod._load_cfg()
            server_mod._lang()
            server_mod._freerdp_exe()
            sys.exit(0)
        except Exception:
            sys.exit(1)
    main()
