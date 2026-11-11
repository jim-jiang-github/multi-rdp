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


def _resource(*parts):
    """定位随包资源：打包态取 _MEIPASS，开发态取仓库根目录。"""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base = os.path.dirname(HERE)
    return os.path.join(base, *parts)


def _icon_image():
    """托盘图标：assets/tray.png（单屏简化版，16px 可读）。

    读不到时退化为程序绘制的同配色简易图标，保证托盘不会空白。
    """
    from PIL import Image
    p = _resource('assets', 'tray.png')
    if os.path.isfile(p):
        try:
            return Image.open(p).convert('RGBA')
        except Exception:
            pass
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 61, 61], radius=15, fill=(23, 36, 57, 255))
    d.rounded_rectangle([10, 14, 53, 40], radius=9, outline=(79, 192, 232, 255), width=6)
    d.rectangle([30, 40, 33, 47], fill=(79, 192, 232, 255))
    d.rounded_rectangle([21, 46, 42, 51], radius=2, fill=(79, 192, 232, 255))
    d.rounded_rectangle([35, 8, 57, 26], radius=4, fill=(61, 230, 168, 255))
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
    try:
        _srv = server_mod.start_server()
    except OSError:
        # 端口已被占用：多半是已经有一个实例在跑，直接打开它的界面即可
        _open_webui()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                '检测到 MultiRDP 已经在运行，已为你打开它的界面。\n'
                '如果界面功能异常（例如创建用户报错），请先右键托盘图标退出旧实例，'
                '再重新启动本程序。',
                'MultiRDP', 0x40)   # MB_ICONINFORMATION
        except Exception:
            pass
        return
    threading.Thread(target=_srv.serve_forever, daemon=True).start()

    # 双击 exe 后自动打开浏览器（稍等片刻，确保服务已开始监听）
    threading.Timer(1.0, _open_webui).start()

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
            server_mod._client_exe()
            sys.exit(0)
        except Exception:
            sys.exit(1)
    main()
