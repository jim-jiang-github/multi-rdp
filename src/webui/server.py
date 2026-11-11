# -*- coding: utf-8 -*-
"""简易用户管理 Web UI：注册 / 登录(RDP) / 删除。仅监听 127.0.0.1。"""
import atexit, ctypes, json, os, random, re, shutil, string, subprocess, sys, threading, time, urllib.parse, webbrowser, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from . import thumb                      # 作为包导入（托盘程序走这条）
    import updater
except ImportError:                          # python src/webui/server.py 直接跑
    import thumb
    # src/ 不在 sys.path 里（直接跑本文件时 sys.path[0] 是 src/webui），补一下
    _SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    import updater

PORT = 8123
HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, 'frozen', False)
# exe 所在目录（打包态）/ 仓库 src 目录（开发态）。只用来找随包资源、当子进程的 cwd，
# 不放用户数据。
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE


def _data_dir():
    """用户数据目录：%LOCALAPPDATA%\\MultiRDP。

    以前是放在 exe 同目录的，两个问题：exe 装到 Program Files 这类只读位置就写不进去；
    升级时换个 exe 容易连 users.json 一起删掉。开发态（python src/webui/server.py）
    还是就地读写仓库，调试时不用去 AppData 里翻文件。
    """
    if not FROZEN:
        return HERE
    base = os.environ.get('LOCALAPPDATA') or os.path.join(
        os.path.expanduser('~'), 'AppData', 'Local')
    return os.path.join(base, 'MultiRDP')


BASE_DIR = _data_dir()
try:
    os.makedirs(BASE_DIR, exist_ok=True)
except OSError:
    pass
USERS_FILE = os.path.join(BASE_DIR, 'users.json')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
# 卡片上的「最后画面」落盘，程序重启后卡片照样有图
thumb.set_cache_dir(os.path.join(BASE_DIR, 'thumbs'))
RDP_GROUP = 'Remote Desktop Users'
ADMIN_GROUP = 'Administrators'
RESERVED = {'administrator', 'guest', 'defaultaccount', 'wdagutilityaccount',
            'jimjiang', 'afk', 'public', 'system'}
NAME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,19}$')

# 排除会被 cmd / net user 解析出问题的字符
_PW_SETS = [string.ascii_lowercase, string.ascii_uppercase, string.digits, '!@#*-_=']
_PW_ALL = ''.join(_PW_SETS)

_lock = threading.Lock()

def _is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def rand_name(taken=()):
    """随机用户名，形如 p7k3mq（字母开头，6 位随机，撞了就重生成）。"""
    taken_l = {t.lower() for t in taken}
    alphabet = 'abcdefghijkmnopqrstuvwxyz23456789'   # 去掉易混的 l/1/0/o
    while True:
        name = 'p' + ''.join(random.choice(alphabet) for _ in range(6))
        if name not in taken_l:
            return name

def rand_password(length=14):
    """随机强密码，保证大小写/数字/符号各至少一个。"""
    while True:
        pw = [random.choice(s) for s in _PW_SETS]
        pw += [random.choice(_PW_ALL) for _ in range(length - len(pw))]
        random.shuffle(pw)
        p = ''.join(pw)
        if (any(c.islower() for c in p) and any(c.isupper() for c in p)
                and any(c.isdigit() for c in p) and any(not c.isalnum() for c in p)):
            return p

def _run(args, timeout=20):
    """run a command, return (rc, output)."""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        out = (p.stdout or b'') + (p.stderr or b'')
        for enc in ('gbk', 'utf-8'):
            try:
                text = out.decode(enc)
                break
            except UnicodeDecodeError:
                text = out.decode('utf-8', 'replace')
        return p.returncode, text
    except Exception as e:
        return 1, str(e)

def _load():
    try:
        with open(USERS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save(d):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def _load_cfg():
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}

def _save_cfg(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def _lang():
    """界面/接口语言：zh（默认）或 en，持久化在 config.json 的 lang 字段。"""
    v = _load_cfg().get('lang')
    return v if v in ('zh', 'en') else 'zh'

def set_lang(lang):
    if lang not in ('zh', 'en'):
        return _lang()
    cfg = _load_cfg()
    cfg['lang'] = lang
    _save_cfg(cfg)
    return lang

def _t(zh, en):
    """按当前语言返回文案。"""
    return zh if _lang() == 'zh' else en

ADMIN_ZH = '需要管理员权限：请以管理员身份重新运行本程序'
ADMIN_EN = 'Administrator rights required: please run this program as administrator'

def admin_default():
    """新建用户默认是否授予管理员权限（界面上的勾选项，持久化到 config.json）。"""
    return bool(_load_cfg().get('admin_default', True))

def set_admin_default(on):
    cfg = _load_cfg()
    cfg['admin_default'] = bool(on)
    _save_cfg(cfg)
    return cfg['admin_default']


def auto_update():
    """是否自动检查更新（config.json 的 auto_update，默认开）。

    只影响「启动后自动去查」，界面上手动点「检查更新」永远可用。
    """
    return bool(_load_cfg().get('auto_update', True))


def set_auto_update(on):
    cfg = _load_cfg()
    cfg['auto_update'] = bool(on)
    _save_cfg(cfg)
    return cfg['auto_update']

# 远程桌面分辨率：auto = 跟随主显示器，fullscreen = 全屏，其余为 WxH 固定值
_RES_RE = re.compile(r'^\d{2,5}x\d{2,5}$')

def _valid_res(v):
    return v in ('auto', 'fullscreen') or bool(_RES_RE.match(v or ''))

def resolution():
    v = _load_cfg().get('resolution', 'auto')
    return v if _valid_res(v) else 'auto'

def set_resolution(v):
    if not _valid_res(v):
        return resolution()
    cfg = _load_cfg()
    cfg['resolution'] = v
    _save_cfg(cfg)
    return cfg['resolution']

class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ('dmDeviceName', ctypes.c_wchar * 32),
        ('dmSpecVersion', ctypes.c_ushort),
        ('dmDriverVersion', ctypes.c_ushort),
        ('dmSize', ctypes.c_ushort),
        ('dmDriverExtra', ctypes.c_ushort),
        ('dmFields', ctypes.c_uint),
        ('dmPositionX', ctypes.c_int),
        ('dmPositionY', ctypes.c_int),
        ('dmDisplayOrientation', ctypes.c_uint),
        ('dmDisplayFixedOutput', ctypes.c_uint),
        ('dmColor', ctypes.c_short),
        ('dmDuplex', ctypes.c_short),
        ('dmYResolution', ctypes.c_short),
        ('dmTTOption', ctypes.c_short),
        ('dmCollate', ctypes.c_short),
        ('dmFormName', ctypes.c_wchar * 32),
        ('dmLogPixels', ctypes.c_ushort),
        ('dmBitsPerPel', ctypes.c_uint),
        ('dmPelsWidth', ctypes.c_uint),
        ('dmPelsHeight', ctypes.c_uint),
        ('dmDisplayFlags', ctypes.c_uint),
        ('dmDisplayFrequency', ctypes.c_uint),
    ]

def _screen_size():
    """主显示器的当前物理分辨率（不受系统缩放比例虚拟化影响）。"""
    try:
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        # ENUM_CURRENT_SETTINGS = -1
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return dm.dmPelsWidth, dm.dmPelsHeight
    except Exception:
        pass
    try:
        u = ctypes.windll.user32
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 0, 0

def _available_resolutions():
    """枚举显示器支持的所有显示模式，按像素数从大到小去重返回 'WxH' 列表。"""
    try:
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        modes = set()
        i = 0
        while ctypes.windll.user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
            if dm.dmPelsWidth >= 640 and dm.dmPelsHeight >= 480:
                modes.add('%dx%d' % (dm.dmPelsWidth, dm.dmPelsHeight))
            i += 1
        return sorted(modes,
                      key=lambda s: int(s.split('x')[0]) * int(s.split('x')[1]),
                      reverse=True)
    except Exception:
        return []

# 常用桌面分辨率：远程会话的桌面大小由客户端决定，不必等于显示器支持的模式，
# 所以即使驱动只报告当前模式（虚拟显示/远程环境常见），也提供这些档位。
COMMON_RES = ('3840x2160', '2560x1600', '2560x1440', '1920x1200', '1920x1080',
              '1680x1050', '1600x900', '1440x900', '1366x768', '1280x1024',
              '1280x800', '1280x720', '1024x768')

def _common_resolutions(modes):
    """常用分辨率里去掉本机已枚举到的，避免下拉框重复。"""
    return [m for m in COMMON_RES if m not in modes]

def _window_fit_limits():
    """窗口化尺寸上限：留出标题栏/任务栏和一圈边距，保证窗口可拖动、看得出是窗口。"""
    sw, sh = _screen_size()
    if not sw or not sh:
        return 0, 0
    return max(800, sw - 100), max(600, sh - 140)

def _rdp_geometry():
    """按配置算出客户端的 /w /h（fullscreen 模式不需要）。

    一律按「窗口化」处理：auto 从常用档位里挑能完整放进屏幕的最大尺寸
    （1080p 屏上是 1600x900），手动选的尺寸塞不进屏幕就等比缩小——
    尺寸正好等于屏幕大小的窗口会被 Windows 顶成最大化，看起来就是全屏。
    """
    res = resolution()
    if res == 'fullscreen':
        return None, None
    max_w, max_h = _window_fit_limits()
    if res == 'auto':
        if max_w and max_h:
            for m in COMMON_RES:
                w, h = map(int, m.split('x'))
                if w <= max_w and h <= max_h:
                    return w, h
            return max_w // 4 * 4, max_h // 4 * 4   # 小屏：没有合适的常用档就按可用区域来
        return 1280, 800          # 探测失败时的兜底
    w, h = res.split('x')
    w, h = int(w), int(h)
    if max_w and max_h and (w > max_w or h > max_h):
        ratio = min(max_w / w, max_h / h)
        w, h = int(w * ratio) // 4 * 4, int(h * ratio) // 4 * 4
    return w, h

def _all_local_users():
    rc, out = _run(['net', 'user'])
    if rc != 0:
        return set()
    # output has two columns of names between the dashed lines
    lines = out.splitlines()
    names = set()
    in_block = False
    for ln in lines:
        if '---' in ln and not in_block:
            in_block = True
            continue
        if in_block:
            if '---' in ln or '命令完成' in ln or 'completed' in ln:
                break
            for tok in ln.split():
                names.add(tok)
    return names

def _group_members(group=RDP_GROUP):
    rc, out = _run(['net', 'localgroup', group])
    members = set()
    if rc != 0:
        return members
    lines = out.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if '----' in ln:
            start = i + 1
            break
    if start is None:
        return members
    for ln in lines[start:]:
        s = ln.strip()
        if not s or '----' in s or '命令完成' in s or 'completed' in s:
            break
        members.add(s)
    return members

def _sessions():
    """{username.lower(): {'id': '3', 'state': 'active'}}

    坑一：`query session` 即使完整输出也会返回 exit code 1，不能拿 rc 判断失败。
    坑二：没有用户名的行（services / rdp-tcp 监听）用 split() 会整行错位，所以优先按列切。
    """
    rc, out = _run(['query', 'session'])
    result = {}
    lines = out.splitlines()
    hdr = None
    for i, ln in enumerate(lines):
        if 'SESSIONNAME' in ln.upper():
            hdr = i
            break
    if hdr is None:
        # 本地化输出兜底：按 token 找 "名字 数字" 的模式
        for ln in lines:
            toks = ln.replace('>', ' ').split()
            for i, t in enumerate(toks[:-1]):
                if toks[i + 1].isdigit() and t.lower() != 'services' and not t.isdigit():
                    result[t.lower()] = {'id': toks[i + 1],
                                         'state': _state_norm(toks[i + 2] if len(toks) > i + 2 else '')}
                    break
        return result
    head = lines[hdr]
    ucol = head.upper().find('USERNAME')
    icol = head.upper().find('ID')
    scol = head.upper().find('STATE')
    if ucol < 0 or icol < 0 or scol <= icol:
        return result
    for ln in lines[hdr + 1:]:
        if ln.startswith('>'):
            ln = ' ' + ln[1:]          # 当前会话行以 > 开头，替换成空格保持列对齐
        if len(ln) <= icol:
            continue
        name = ln[ucol:icol].strip()
        sid = ln[icol:scol].strip()
        state = ln[scol:].strip().split()
        if not name or not sid.isdigit():
            continue
        if name.lower() in ('services', '会话名', '用户名') or name.isdigit():
            continue
        result[name.lower()] = {'id': sid, 'state': _state_norm(state[0] if state else '')}
    return result

def _state_norm(s):
    """把 query session 的 STATE 归一化（不同语言/版本的输出不一致）。"""
    s = (s or '').strip().lower()
    if s.startswith('disc') or '断开' in s:
        return 'disc'
    if s.startswith('active') or '运行' in s or '活动' in s:
        return 'active'
    return s

def _sync_users():
    """merge group members into managed db (imported, no password)."""
    with _lock:
        db = _load()
        for m in _group_members():
            key = m.lower()
            if key not in db:
                db[key] = {'name': m, 'password': None, 'imported': True}
        _save(db)
    return db

def register_user(name=None, admin=None):
    if not _is_admin():
        return None, _t(ADMIN_ZH, ADMIN_EN)
    with _lock:
        db = _load()
        existing = _all_local_users()
        name = (name or '').strip()
        if not name:
            name = rand_name(existing | set(db.keys()))
        if not NAME_RE.match(name):
            return None, _t('用户名只能用字母开头，含字母/数字/_/-，最长20位',
                            'Username must start with a letter and contain only letters/digits/_/- (max 20 chars)')
        if name.lower() in RESERVED or name.lower() in existing:
            return None, _t('用户名 %s 已存在或不可用' % name,
                            'Username %s already exists or is unavailable' % name)
        password = rand_password()
        rc, out = _run(['net', 'user', name, password, '/add',
                        '/passwordchg:no', '/expires:never', '/active:yes'])
        if rc != 0:
            tail = out.strip().splitlines()
            return None, _t('创建失败: ', 'Create failed: ') + (tail[-1] if tail else _t('未知错误', 'unknown error'))
        rc, out = _run(['net', 'localgroup', RDP_GROUP, name, '/add'])
        if rc != 0:
            _run(['net', 'user', name, '/delete'])
            tail = out.strip().splitlines()
            return None, _t('加入远程用户组失败: ', 'Failed to join Remote Desktop Users: ') + (
                tail[-1] if tail else _t('未知错误', 'unknown error'))
        # 按界面勾选决定是否进管理员组：UAC 从“输入管理员密码”变成“点一下是”
        if admin is None:
            admin = admin_default()
        is_admin_user = False
        if admin:
            rc2, _ = _run(['net', 'localgroup', ADMIN_GROUP, name, '/add'])
            is_admin_user = rc2 == 0
        db[name.lower()] = {'name': name, 'password': password, 'imported': False,
                            'admin': is_admin_user}
        _save(db)
        return db[name.lower()], None

def grant_admin(name):
    """把用户加入管理员组：UAC 弹窗从“输入管理员密码”变成“点一下是”。

    注意：组权限在登录令牌里，已登录的会话必须注销重登（点「重置」再「登录」）才生效。
    """
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    with _lock:
        db = _load()
        info = db.get(name.lower())
        real = info['name'] if info else (name or '').strip()
        if not real:
            return False, _t('用户不存在', 'User not found')
        if real.lower() in _group_members(ADMIN_GROUP):
            return True, None
        rc, out = _run(['net', 'localgroup', ADMIN_GROUP, real, '/add'])
        if rc != 0:
            if real.lower() in _group_members(ADMIN_GROUP):
                return True, None
            tail = [l for l in out.strip().splitlines() if l.strip()]
            return False, _t('加入管理员组失败: ', 'Failed to join Administrators: ') + (
                tail[-1] if tail else _t('未知错误', 'unknown error'))
        return True, None

def _client_exe():
    """定位远程客户端：用系统自带的 mstsc.exe（不在 PATH 里时按 System32 拼）。"""
    p = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'mstsc.exe')
    if os.path.isfile(p):
        return p
    return shutil.which('mstsc.exe')

# ------------------------------------------------------------------ 客户端窗口
# 会话是 Active 还是 Disc，只取决于「还有没有 RDP 客户端连着」：
#   · 客户端进程退出（点了窗口的 X）→ 连接断掉 → 会话立刻变 Disc
#   · Disc 状态没有输入通道，DWM 也不再给这个会话出画面：里面的窗口虽然还记着
#     自己的前台窗口，但拿不到真正的焦点，截图/键鼠注入都会失真
# 所以「窗口关掉了、后台还得是激活的」只有一个做法：进程别退出，只把窗口藏起来
# （ShowWindow(SW_HIDE)，任务栏和 Alt+Tab 里都看不到）。连接还在 → 会话始终 Active。
_clients = {}   # username.lower() -> {'proc','hwnd','hidden','intent','auto','t0'}

SW_HIDE, SW_SHOW, SW_RESTORE, SW_MINIMIZE = 0, 1, 9, 6
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
HWND_TOPMOST, HWND_NOTOPMOST = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
GW_OWNER = 4
_CLIENT_EXES = ('mstsc.exe',)

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_user32.GetWindow.restype = ctypes.c_void_p
_user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
_user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.IsWindow.argtypes = [ctypes.c_void_p]
_user32.IsWindow.restype = ctypes.c_bool
_user32.IsIconic.argtypes = [ctypes.c_void_p]
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_kernel32.WaitForSingleObject.restype = ctypes.c_ulong
_user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
# 类名/子控件/发消息：mstsc 那边要点掉「自签名证书」提示框，靠这几个原生调用。
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
_user32.SendMessageW.restype = ctypes.c_void_p
_user32.EnumChildWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]


_exe_cache = {}      # pid -> (exe 名, 取的时间)，状态接口每 2s 轮询一次，别每次都去查进程


def _exe_of(pid):
    """进程 exe 名（小写），失败返回空串。"""
    now = time.time()
    hit = _exe_cache.get(pid)
    if hit and now - hit[1] < 20:
        return hit[0]
    name = ''
    h = _kernel32.OpenProcess(0x1000, 0, pid)    # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            n = ctypes.c_ulong(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                name = os.path.basename(buf.value).lower()
        finally:
            _kernel32.CloseHandle(h)
    if len(_exe_cache) > 400:                    # 别无限长
        for k in [k for k, v in _exe_cache.items() if now - v[1] > 60]:
            _exe_cache.pop(k, None)
    _exe_cache[pid] = (name, now)
    return name


def _log(msg):
    """把窗口相关的动作记到 client.log，出问题时能直接看。"""
    try:
        with open(os.path.join(BASE_DIR, 'client.log'), 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


# 升级时「谁来把进程停掉」由调用方决定：托盘程序要顺手把图标收走，
# 直接跑 server.py 的场景只能硬退。
_quit_hook = None


def set_quit_hook(fn):
    global _quit_hook
    _quit_hook = fn


def exit_for_update():
    """新 exe 已经就位，退出让批处理把它换上去。"""
    _log('更新：退出进程，交给替换脚本重启')
    try:
        if _quit_hook:
            _quit_hook()
            return
    except Exception as e:
        _log('更新：退出回调失败 %s' % e)
    os._exit(0)


# 老版本写在 exe 旁边的那批数据；换到 %LOCALAPPDATA% 之后第一次启动搬过去
_LEGACY_FILES = ('users.json', 'config.json', 'aliases.json', 'clients.json',
                 'client.log', 'rdp_repair_backup.json', 'termwrap_backup.json')


def _migrate_legacy():
    """把 exe 旁边的旧数据搬到新的用户数据目录。

    只搬「目标不存在」的文件，已有的绝不覆盖（否则新数据会被旧数据盖掉）；
    move 不成就 copy，copy 也不成就跳过——不能让用户因为迁移失败打不开程序。
    """
    if BASE_DIR == EXE_DIR:
        return
    moved = []
    for n in _LEGACY_FILES:
        src = os.path.join(EXE_DIR, n)
        dst = os.path.join(BASE_DIR, n)
        if not os.path.isfile(src) or os.path.exists(dst):
            continue
        try:
            shutil.move(src, dst)
            moved.append(n)
        except OSError:
            try:
                shutil.copy2(src, dst)
                moved.append(n + '(copy)')
            except OSError:
                pass

    src_dir = os.path.join(EXE_DIR, 'thumbs')
    dst_dir = os.path.join(BASE_DIR, 'thumbs')
    if os.path.isdir(src_dir):
        try:
            os.makedirs(dst_dir, exist_ok=True)
        except OSError:
            dst_dir = ''
        if dst_dir:
            for f in os.listdir(src_dir):
                s = os.path.join(src_dir, f)
                d = os.path.join(dst_dir, f)
                if os.path.isfile(s) and not os.path.exists(d):
                    try:
                        shutil.move(s, d)
                        moved.append('thumbs/' + f)
                    except OSError:
                        pass

    if moved:
        _log('运行数据迁移到 %s（%d 个文件）：%s'
             % (BASE_DIR, len(moved), ', '.join(moved[:10])))


if FROZEN:
    _migrate_legacy()


def _pid_running(pid):
    """进程还活着吗（不依赖进程名，防被沙箱/过滤驱动返回假路径）。"""
    h = _kernel32.OpenProcess(0x00100000, 0, pid)      # SYNCHRONIZE
    if not h:
        return False
    try:
        return _kernel32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(h)


_looks_cache = {}    # pid -> (是不是客户端, 时间)，枚举窗口挺贵的，别反复来


def _looks_client(pid):
    """这个 pid 是不是远程客户端：exe 名对上，或者窗口标题里带 Remote Desktop。

    标题这条路很重要：某些环境（安全软件、沙箱）下 QueryFullProcessImageNameW
    会返回一个假路径，光看 exe 名会把真客户端判成别的程序。mstsc 的标题形如
    「127.0.0.101 - Remote Desktop Connection」（中文系统是「远程桌面连接」）。
    """
    now = time.time()
    hit = _looks_cache.get(pid)
    if hit and now - hit[1] < 15:
        return hit[0]
    ok = _exe_of(pid) in _CLIENT_EXES
    if not ok:
        try:
            for _h, _p, t in _top_windows(pid):
                low = (t or '').lower()
                if 'remote desktop' in low or '远程桌面' in low:
                    ok = True
                    break
        except Exception:
            pass
    _looks_cache[pid] = (ok, now)
    return ok


# clients.json：把「哪个进程是哪个用户的远程窗口」「窗口是不是藏着」记在盘上。
# 不然程序一重启，内存里的映射就没了：窗口明明还藏着（进程活着），界面却当成
# 可见的，只能点「隐藏窗口」，再也点不回来。
CLIENTS_FILE = os.path.join(BASE_DIR, 'clients.json')


def _pid_of(ent):
    """条目对应的 pid（自己拉起的用 Popen.pid，接管的用记录里的 pid）。"""
    if not ent:
        return 0
    proc = ent.get('proc')
    return proc.pid if proc is not None else int(ent.get('pid') or 0)


def _alive(ent):
    """客户端进程是否还活着（窗口暂时不在也算活着，别把记录丢了）。"""
    if not ent:
        return False
    proc = ent.get('proc')
    if proc is not None:
        return proc.poll() is None
    pid = int(ent.get('pid') or 0)
    return bool(pid) and _pid_running(pid)


def _save_clients():
    try:
        data = {}
        for k, v in list(_clients.items()):
            if _alive(v):
                data[k] = {'pid': _pid_of(v), 'hidden': bool(v.get('hidden'))}
        tmp = CLIENTS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CLIENTS_FILE)
    except Exception:
        pass


def _adopt_clients():
    """启动时把上一轮留下的客户端进程接回来（含隐藏状态），pid 已失效的丢弃。"""
    try:
        with open(CLIENTS_FILE, encoding='utf-8') as f:
            saved = json.load(f)
    except Exception:
        return
    if not isinstance(saved, dict):
        return
    for key, info in saved.items():
        key = str(key).lower()
        if key in _clients or not isinstance(info, dict):
            continue
        pid = int(info.get('pid') or 0)
        if not pid or not _pid_running(pid) or not _looks_client(pid):
            _log('adopt %s: pid %s 已失效，丢弃' % (key, pid))
            continue
        _clients[key] = {'proc': None, 'pid': pid, 'hwnd': 0,
                         'hidden': bool(info.get('hidden')),
                         'intent': '', 'auto': False, 't0': 0}
        _log('adopt %s: 接管 pid %s（hidden=%s）' % (key, pid, info.get('hidden')))
    _save_clients()


def _adopt_by_title():
    """启动时把「远程还开着、但 pid 记录对不上」的窗口接回来。

    _adopt_clients 只认 clients.json 里记的 pid：mstsc 被关掉重开、程序换过目录、
    配置被清掉，pid 就全废了，卡片会一直显示「未连接」——即使远程窗口好好开着。
    这里按标题里的回环地址再扫一遍，能救回来的顺手把记录补上。
    """
    try:
        alias = _load_aliases()
    except Exception:
        return
    if not alias:
        return
    try:
        wins = [(h, p, t or '') for h, p, t in _top_windows()
                if (_class_of(h) or '').lower() == 'tscshellcontainerclass']
    except Exception:
        return
    if not wins:
        return
    for key, n in list(alias.items()):
        key = str(key).lower()
        ent = _clients.get(key)
        if _alive(ent) and ent.get('hwnd') and _user32.IsWindow(ent['hwnd']):
            continue
        try:
            pat = re.compile(r'127\.0\.0\.%d(?!\d)' % int(n))
        except Exception:
            continue
        for hwnd, pid, title in wins:
            if not pat.search(title):
                continue
            if ent is None:
                ent = {'proc': None, 'pid': pid, 'hwnd': hwnd,
                       'hidden': not bool(_user32.IsWindowVisible(hwnd)),
                       'intent': '', 'auto': False, 't0': 0}
                _clients[key] = ent
                _log('adopt %s: 按标题接管 hwnd %s（pid %s）' % (key, hwnd, pid))
            else:
                ent['hwnd'] = hwnd
                if not ent.get('proc'):
                    ent['pid'] = pid
                _log('adopt %s: 按标题补回 hwnd %s' % (key, hwnd))
            break


_adopted = False


def _ensure_adopted():
    global _adopted
    if not _adopted:
        _adopted = True
        _adopt_clients()
        _adopt_by_title()


def _top_windows(pid=None):
    """[(hwnd, pid, title)]，含隐藏窗口；带 owner 的弹层/对话框跳过。"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lp):
        p = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if pid and p.value != pid:
            return 1
        if _user32.GetWindow(hwnd, GW_OWNER):
            return 1
        n = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append((hwnd, p.value, buf.value))
        return 1

    _user32.EnumWindows(_cb, None)
    return out


def _hwnd_by_title(name):
    """按窗口标题里的回环地址，找出这个账号的远程窗口 → (hwnd, pid)。

    mstsc 的标题形如「127.0.0.119 - Remote Desktop Connection」，而每个账号连哪个
    回环地址在 aliases.json 里是定死的，所以标题本身就够认出是谁。这条路不依赖
    进程表：程序重启过、mstsc 被关掉重开、clients.json 丢了，pid 全对不上也能认回来。
    """
    key = (name or '').lower()
    try:
        n = _load_aliases().get(key)
    except Exception:
        return 0, 0
    if not n:
        return 0, 0
    try:
        pat = re.compile(r'127\.0\.0\.%d(?!\d)' % int(n))
        for hwnd, pid, title in _top_windows():
            if (_class_of(hwnd) or '').lower() == 'tscshellcontainerclass' \
                    and pat.search(title or ''):
                return hwnd, pid
    except Exception:
        pass
    return 0, 0


def _client_window(name, key=None):
    """定位某个用户的远程窗口，返回 (hwnd, pid)；找不到返回 (0, 0)。

    优先用记着的 pid（程序重启后会从 clients.json 接回来）；万一没有记录，
    再退回按窗口标题认人（mstsc 的标题里只有回环地址，认不出是哪个账号，
    所以整机只有一个客户端在连时才认它）。
    """
    _ensure_adopted()
    key = (key or name).lower()
    ent = _clients.get(key)
    if _alive(ent):
        pid = _pid_of(ent)
        if ent.get('hwnd') and _user32.IsWindow(ent['hwnd']):
            return ent['hwnd'], pid
        wins = _top_windows(pid)
        # 一个 mstsc 进程会建近 20 个顶层窗口（容器、进度框、输入法、声音、剪贴板…）。
        # 优先挑 TscShellContainerClass（真正显示远程桌面的那个），其次才是可见的。
        def _rank(w):
            cls = (_class_of(w[0]) or '').lower()
            return (0 if cls == 'tscshellcontainerclass' else 1,
                    0 if _user32.IsWindowVisible(w[0]) else 1)
        wins.sort(key=_rank)
        if wins:
            return wins[0][0], pid
        return 0, pid
    # 记录里的 pid 已经失效（程序重启过、mstsc 被关掉重开）：按标题里的回环地址再认一次
    thr_hwnd, thr_pid = _hwnd_by_title(key)
    if thr_hwnd:
        if ent is not None:
            ent['hwnd'] = thr_hwnd
            if not ent.get('proc'):
                ent['pid'] = thr_pid
        return thr_hwnd, thr_pid

    low = name.lower()
    hit = [(h, p) for h, p, title in _top_windows()
           if low in title.lower() and _looks_client(p)]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        # 标题里认不出人（mstsc 的标题是「127.0.0.x - Remote Desktop Connection」）。
        # 只统计真正承载远程画面的 TscShellContainerClass，整机只有一个客户端时才认它。
        # 不能按「所有顶层窗口」数——一个 mstsc 进程会顺带建近 20 个辅助窗口
        # （输入法、声音、剪贴板、弹窗宿主…），那样永远凑不出「唯一」。
        main = [(h, p) for h, p, _t in _top_windows()
                if (_class_of(h) or '').lower() == 'tscshellcontainerclass']
        if len({p for _h, p in main}) == 1:
            return main[0]
    return 0, 0


def _owned_hwnd(name):
    """定位「这个账号」的远程窗口，比 _client_window 更挑剔。

    缩略图和「点画面切过去」必须严格对号：mstsc 的标题里只有回环地址，认不出
    是哪个账号，_client_window 的「整机只有一个客户端」兜底会把同一个窗口算到
    别人头上——那就成了把 A 的桌面贴到 B 的卡片上。

    所以兜底只在「这个账号当前确实有 Active 会话」时放行，并且排除已被别的
    账号占用的窗口；其它情况一律返回 0（前端显示占位图）。
    """
    _ensure_adopted()
    key = (name or '').lower()
    ent = _clients.get(key)
    if _alive(ent):
        if ent.get('hwnd') and _user32.IsWindow(ent['hwnd']):
            return ent['hwnd']
        hwnd, _pid = _client_window(name)
        if hwnd:
            ent['hwnd'] = hwnd
            return hwnd
    # pid 记录对不上（程序重启过 / mstsc 重开过 / clients.json 丢了）：
    # 按标题里的回环地址认，这条路不看进程表
    hwnd, pid = _hwnd_by_title(key)
    if hwnd:
        if ent is not None:
            ent['hwnd'] = hwnd
            if not ent.get('proc'):
                ent['pid'] = pid
        return hwnd
    st = _sessions().get(key) or {}
    if st.get('state') != 'active':
        return 0
    hwnd, _pid = _client_window(name)
    if not hwnd:
        return 0
    taken = {e.get('hwnd') for k, e in _clients.items() if k != key and _alive(e)}
    return 0 if hwnd in taken else hwnd


def _raise_window(hwnd):
    """把窗口翻到最上层（不需要前台权限）：临时置顶再取消置顶。"""
    _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def _activate_window(hwnd):
    """把窗口拉到前台并激活（拿到输入焦点）。

    Windows 有「前台锁」：不是前台进程直接调 SetForegroundWindow 会被拒绝——
    我们的客户端正是由后台的 MultiRDP 拉起的，所以窗口会「创建成功但压在别人后面」，
    看起来就像根本没有弹出来。按顺序试，哪级成了就停：
      1. 直接 SetForegroundWindow
      2. 临时置顶再取消置顶（不需要前台权限，至少翻到最上层）
      3. 模拟一次 ALT 按下/松开，骗过前台锁再调
      4. AttachThreadInput 借来目标线程的输入队列再调
      5. SwitchToThisWindow（Alt+Tab 内部用的原语）
    """
    if not hwnd:
        return False
    _user32.ShowWindow(hwnd, SW_RESTORE if _user32.IsIconic(hwnd) else SW_SHOW)
    if _user32.GetForegroundWindow() == hwnd:
        return True
    if _user32.SetForegroundWindow(hwnd) and _user32.GetForegroundWindow() == hwnd:
        return True
    _raise_window(hwnd)
    if _user32.GetForegroundWindow() == hwnd:
        return True
    # ALT 按下又松开，骗过前台锁（系统会以为用户刚按过键）
    _user32.keybd_event(0x12, 0, 0, 0)              # ALT down
    ok = bool(_user32.SetForegroundWindow(hwnd))
    _user32.keybd_event(0x12, 0, 2, 0)              # ALT up (KEYEVENTF_KEYUP)
    if ok and _user32.GetForegroundWindow() == hwnd:
        return True
    # 借 AttachThreadInput 挂到前台线程的输入队列上，绕开前台锁。
    # 一定要成对拆开：挂上之后不拆，目标线程的输入队列会一直跟着我们的线程走，
    # 我们这边一阻塞，它就会「未响应」。
    this_t = _kernel32.GetCurrentThreadId()
    tgt_t = _user32.GetWindowThreadProcessId(hwnd, None)
    fg = _user32.GetForegroundWindow()
    fg_t = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    ok = False
    attached = []
    try:
        if _user32.AttachThreadInput(this_t, tgt_t, True):
            attached.append(tgt_t)
        if fg_t and fg_t != tgt_t and _user32.AttachThreadInput(this_t, fg_t, True):
            attached.append(fg_t)
        _user32.BringWindowToTop(hwnd)
        ok = bool(_user32.SetForegroundWindow(hwnd))
    finally:
        for t in attached:
            _user32.AttachThreadInput(this_t, t, False)
    if ok and _user32.GetForegroundWindow() == hwnd:
        return True
    # 最后兜底：Alt+Tab 同款
    try:
        _user32.SwitchToThisWindow(hwnd, True)
        return _user32.GetForegroundWindow() == hwnd
    except AttributeError:
        return False


def _set_visible(hwnd, visible, activate=True):
    """显示 / 隐藏窗口。显示时默认同时拉到前台激活；隐藏失败就退回最小化
    （最小化同样保持连接、会话仍 Active）。"""
    if visible:
        if activate:
            return _activate_window(hwnd)
        _user32.ShowWindow(hwnd, SW_RESTORE if _user32.IsIconic(hwnd) else SW_SHOW)
        return True
    _user32.ShowWindow(hwnd, SW_HIDE)
    if _user32.IsWindowVisible(hwnd):
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
    return not _user32.IsWindowVisible(hwnd)


def _hide_when_ready(key, proc):
    """后台重连的窗口一露头就藏起来（SDL 不一定理 STARTUPINFO 的 SW_HIDE）。"""
    for _ in range(40):                      # 最多等 10s
        if proc.poll() is not None:
            return
        if _client_window(key, key)[0]:
            _clients.get(key, {})['hidden'] = True
            hide_client(key)
            return
        time.sleep(0.25)


# ------------------------------------------------------------------ mstsc 后端
# 客户端固定用系统自带的 mstsc.exe。早期版本用 FreeRDP，但 3.32 在 Windows 26H1
# 上收到「脏」的快路径位图会崩（上游 #13883，3.33 才修），而 /relax-order-checks
# 那个开关更糟：跳过坏数据时不推进读指针，同一条更新被无限重解析（实测 1 分钟
# 1.3 万条日志、窗口彻底卡死）。mstsc 是微软自家解码器，不踩这套路径。
#
# mstsc 不能从命令行收账号密码，只能：
#   1) 先把密码写进 Windows 凭据库：cmdkey /generic:TERMSRV/<host> /user /pass
#   2) 再 mstsc /v:<host> /w /h
# 凭据是按「主机名」存的，所有账号都连 127.0.0.1 会互相覆盖，所以每个账号要分到
# 一个自己的回环别名（127.0.0.0/8 整段都通到本机，效果与 127.0.0.1 相同）。
ALIASES_FILE = os.path.join(BASE_DIR, 'aliases.json')
_alias_lock = threading.Lock()


def _load_aliases():
    try:
        with open(ALIASES_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return {str(k).lower(): int(v) for k, v in d.items()}
    except Exception:
        return {}


def _mstsc_host(name):
    """给账号分配一个固定的回环别名，映射落盘，重启后不变。

    不能让 crc32 直接取模了事：两个账号撞上同一个别名时，凭据会互相覆盖，
    表现就是「登录后进了别人的桌面」。这里改成线性探测 + 落盘保证唯一。
    """
    key = name.lower()
    with _alias_lock:
        d = _load_aliases()
        n = d.get(key)
        if not n:
            used = set(d.values())
            start = 2 + zlib.crc32(key.encode('utf-8')) % 253
            for i in range(253):
                cand = 2 + (start - 2 + i) % 253        # 2..254
                if cand not in used:
                    n = cand
                    break
            if not n:                                    # 253 个位置全占满（几乎不可能）
                n = 2 + len(used) % 253
            d[key] = n
            try:
                with open(ALIASES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
            except Exception:
                pass
        return '127.0.0.%d' % n


def _purge_credentials():
    """退出时删掉自己写进凭据库的 RDP 凭据。

    不删的话密码会一直躺在 Windows 凭据管理器里，同一个用户下任何程序都能读出来。
    """
    try:
        for key, n in _load_aliases().items():
            _run(['cmdkey', '/delete:TERMSRV/127.0.0.%d' % n])
    except Exception:
        pass


atexit.register(_purge_credentials)


def _class_of(hwnd):
    if not hwnd:
        return ''
    buf = ctypes.create_unicode_buffer(128)
    _user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _all_windows(pid):
    """和 _top_windows 一样，但**不跳过有 owner 的窗口**——提示框都是 owner 窗口。"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lp):
        p = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            out.append((hwnd, p.value, ''))
        return 1

    _user32.EnumWindows(_cb, None)
    return out


def _child_windows(hwnd):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(h, _lp):
        n = _user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(h, buf, n + 1)
        out.append((h, buf.value))
        return True

    _user32.EnumChildWindows(hwnd, _cb, None)
    return out


def _mstsc_login(host, name, pwd):
    """把账号密码写进凭据库；同一个 host 重复写会覆盖，所以每个账号要用不同 host。"""
    rc, out = _run(['cmdkey', '/generic:TERMSRV/' + host, '/user:' + name, '/pass:' + pwd])
    if rc != 0:
        _log('mstsc 写凭据失败 %s: %s' % (host, out.strip()[:120]))
    return rc == 0


def _click_cert_dialog(pid, seconds=50):
    """点掉 mstsc 的「证书不可信」提示：先勾「不再询问」，再点「是」。

    只认 mstsc 自己进程里、且文字确实提到 certificate 的 #32770 对话框，
    别的窗口一律不动，避免误点。
    """
    BM_CLICK, BM_GETCHECK = 0x00F5, 0x00F0
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(1.0)
        try:
            for h, _p, _t in _all_windows(pid):
                if _class_of(h) != '#32770':
                    continue
                low = [(c, (t or '').lower()) for c, t in _child_windows(h)]
                if not any('certificat' in t for _c, t in low):
                    continue
                dont = yes = 0
                for c, t in low:
                    flat = t.replace('&', '')
                    if flat.startswith("don't ask me again"):
                        dont = c
                    elif flat in ('yes', '是'):
                        yes = c
                if dont:
                    try:
                        if _user32.SendMessageW(dont, BM_GETCHECK, 0, 0) == 0:
                            _user32.SendMessageW(dont, BM_CLICK, 0, 0)
                            time.sleep(0.2)
                    except Exception:
                        pass
                if yes:
                    _user32.SendMessageW(yes, BM_CLICK, 0, 0)
                    _log('mstsc 证书提示已确认（pid %s）' % pid)
                    return True
        except Exception:
            pass
    return False


def _spawn_mstsc(info, hidden=False):
    """拉起 mstsc 连本机回环。"""
    exe = _client_exe()
    if not exe:
        return None
    w, h = _rdp_geometry()
    host = _mstsc_host(info['name'])
    _mstsc_login(host, info['name'], info['password'])
    args = [exe, '/v:' + host]
    if w and h:
        args += ['/w:%d' % w, '/h:%d' % h]
    si = None
    if hidden:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
    logf = open(os.path.join(BASE_DIR, 'client.log'), 'ab')
    try:
        proc = subprocess.Popen(
            args, cwd=os.path.dirname(exe),
            stdout=logf, stderr=logf, startupinfo=si,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    finally:
        logf.close()            # 句柄已复制给子进程，父进程这份要关掉
    threading.Thread(target=_click_cert_dialog, args=(proc.pid,), daemon=True).start()
    _log('mstsc 启动 %s -> %s (%sx%s)' % (info['name'], host, w or 0, h or 0))
    return proc


def _spawn_client(info, hidden=False):
    """拉起远程客户端，返回 Popen（失败返回 None）。"""
    return _spawn_mstsc(info, hidden=hidden)


def _connect(info, hidden=False, auto=False, reveal=False):
    """连上（或重连）该用户的桌面。hidden=True 时窗口不显示，但连接建立、会话 Active。

    reveal=True：重连完成后把窗口请回桌面并激活（原来的窗口是可见的，不该悄悄藏掉）。
    """
    key = info['name'].lower()
    try:
        proc = _spawn_client(info, hidden=hidden)
    except Exception as e:
        return False, _t('启动远程窗口失败: ', 'Failed to launch remote window: ') + str(e)
    if proc is None:
        return False, _t('找不到 mstsc.exe（系统缺少远程桌面客户端）',
                         'mstsc.exe not found (remote desktop client missing)')
    time.sleep(3.0)
    if proc.poll() is not None:
        return False, _t('远程窗口启动后立即退出，详见 client.log',
                         'Remote window exited immediately after launch, see client.log')
    _clients[key] = {'proc': proc, 'pid': proc.pid, 'hwnd': 0, 'hidden': bool(hidden),
                     'intent': '', 'auto': auto, 't0': time.time()}
    _save_clients()
    if hidden:
        threading.Thread(target=_hide_when_ready, args=(key, proc), daemon=True).start()
    elif reveal:
        threading.Thread(target=_show_when_ready, args=(key, proc), daemon=True).start()
    threading.Thread(target=_watch_client, args=(key, proc, info, bool(hidden)), daemon=True).start()
    return True, None


def _kill_client(key):
    """主动关掉客户端进程（删除用户 / 切换会话时用）。"""
    ent = _clients.pop(key, None)
    if not ent:
        return
    ent['intent'] = 'app'
    proc = ent.get('proc')
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
    else:
        pid = _pid_of(ent)          # 接管来的进程没有 Popen，只能按 pid 结束
        if pid and _pid_running(pid):
            _run(['taskkill', '/PID', str(pid), '/T', '/F'])
    _save_clients()


def _show_when_ready(key, proc):
    """重连 / 登录后把窗口请到桌面上并带到前台。

    窗口刚建好时不一定抢得到前台（我们的进程在后台），所以多试几次，每次间隔
    0.8s；结果写进 client.log，方便现场判断是「没找到窗口」还是「找到了但没抢到前台」。
    """
    for _ in range(40):                      # 最多等 10s 让窗口出现
        if proc.poll() is not None:
            return
        hwnd, _pid = _client_window(key, key)
        if hwnd:
            if key in _clients:
                _clients[key]['hidden'] = False
            got = False
            for _i in range(4):
                got = _set_visible(hwnd, True)
                if _user32.GetForegroundWindow() == hwnd:
                    break
                time.sleep(0.8)
            _log('置前 %s: %s' % (key, '已激活到前台' if _user32.GetForegroundWindow() == hwnd
                                  else ('已翻到最上层但没拿到焦点' if got else '置前失败')))
            return
        time.sleep(0.25)
    _log('置前 %s: 没找到客户端窗口' % key)


def _watch_client(key, proc, info, was_hidden=True):
    """盯着客户端进程，退出后把记录清掉。

    只清理、不保活：用户点 X 关掉窗口就是关掉了（想回去点「登录」），客户端崩了
    也不再自动拉起。之前那套「退出后悄悄在后台重连」会让关掉的窗口复活，用户
    明确不要。
    """
    proc.wait()
    with _lock:
        ent = _clients.get(key)
        if not ent or ent.get('proc') is not proc:
            return
        _clients.pop(key, None)
    _save_clients()
    _log('%s 客户端退出，记录已清（会话还在，可再点登录）' % info['name'])


def login_user(name):
    db = _sync_users()
    key = name.lower()
    info = db.get(key)
    if not info:
        return False, _t('用户不存在', 'User not found')
    ent = _clients.get(key)
    if ent and _alive(ent):
        hwnd, _pid = _client_window(name)
        # 收进后台了，或者记录说「可见」其实窗口被藏起来了：都按「请回来」处理
        if ent.get('hidden') or not hwnd or not _user32.IsWindowVisible(hwnd):
            ok, err = show_client(name)
            return (True, None) if ok else (False, err)
        # 窗口在，但很可能被别的窗口压着（客户端是后台进程拉起的，没有前台权限）
        _activate_window(hwnd)
        return True, None
    if not info.get('password'):
        return False, _t('该用户没有保存密码，无法一键登录（可删除后重新注册）',
                         'This user has no saved password, one-click login is unavailable (delete and re-create it)')
    if not _client_exe():
        return False, _t('找不到 mstsc.exe（系统缺少远程桌面客户端）',
                         'mstsc.exe not found (remote desktop client missing)')
    # reveal：窗口一建好就翻到前台并激活，否则它会悄悄躲在别的窗口后面
    return _connect(info, reveal=True)


def _track(name, hwnd, pid, hidden):
    """记住「这个窗口属于这个用户」，并落盘，重启后还能认回来。"""
    key = name.lower()
    ent = _clients.get(key)
    if ent is None and pid:
        ent = _clients[key] = {'proc': None, 'pid': pid, 'hwnd': hwnd,
                               'hidden': bool(hidden), 'intent': '', 'auto': False, 't0': 0}
    if ent:
        ent['hwnd'] = hwnd
        ent['hidden'] = bool(hidden)
    _save_clients()


def hide_client(name):
    """把远程窗口收进后台：窗口隐藏、连接不断、会话保持 Active。"""
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    hwnd, pid = _client_window(name)
    if not hwnd:
        return False, _t('没有找到该用户的远程窗口', 'No remote window found for this user')
    _set_visible(hwnd, False)
    _track(name, hwnd, pid, True)
    return True, None


def show_client(name):
    """把收进后台的窗口请回来（激活到前台，会话一直在线，桌面原样恢复）。"""
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    hwnd, pid = _client_window(name)
    if not hwnd:
        return False, _t('没有找到该用户的远程窗口', 'No remote window found for this user')
    _set_visible(hwnd, True)
    _track(name, hwnd, pid, False)
    return True, None


def restore_hidden_clients():
    """管理程序退出前把藏起来的窗口放回桌面（只显示、不抢前台焦点）。"""
    for key in list(_clients.keys()):
        try:
            if _clients[key].get('hidden'):
                hwnd, _pid = _client_window(key)
                if hwnd:
                    _set_visible(hwnd, True, activate=False)
                _clients[key]['hidden'] = False
        except Exception:
            pass
    _save_clients()


atexit.register(restore_hidden_clients)

def reset_session(name):
    """把该用户的会话彻底注销（画面卡死/黑屏时用），下次登录会建全新会话。"""
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    db = _load()
    info = db.get(name.lower())
    real = info['name'] if info else name
    _kill_client(name.lower())
    sess = _sessions().get(real.lower())
    sid = sess['id'] if sess else None
    if not sid:
        return True, None
    rc, out = _run(['logoff', sid])
    if rc != 0:
        return False, _t('注销会话失败: ', 'Failed to sign out session: ') + (
            out.strip() or _t('未知错误', 'unknown error'))
    time.sleep(1.5)
    return True, None

def disconnect_session(name):
    """真正断开该用户的会话（tsdiscon）：会话变 Disc，程序仍在后台但拿不到焦点/画面。

    界面上默认不给这个按钮 —— 「关掉窗口」用的是 hide_client（藏窗口、不断连，
    会话保持 Active）。这个函数留给需要彻底释放显示资源时用。
    """
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    db = _load()
    info = db.get(name.lower())
    real = info['name'] if info else name
    _kill_client(name.lower())
    sess = _sessions().get(real.lower())
    if not sess:
        return True, None
    if sess['state'] != 'active':
        return True, None          # 已经是断开状态，无需再断
    rc, out = _run(['tsdiscon', sess['id']])
    if rc != 0:
        return False, _t('断开失败: ', 'Failed to disconnect: ') + (
            out.strip() or _t('未知错误', 'unknown error'))
    time.sleep(1.0)
    return True, None

def _force_rmtree(path, timeout=120):
    """尽力删除目录（含只读/ACL 顽固文件）。删干净返回 True。"""
    import stat as _stat

    def clear_readonly(p):
        for root, dirs, files in os.walk(p):
            for n in dirs + files:
                try:
                    os.chmod(os.path.join(root, n), _stat.S_IWRITE)
                except OSError:
                    pass

    if not os.path.isdir(path):
        return True
    clear_readonly(path)
    try:
        shutil.rmtree(path)
        return True
    except Exception:
        pass
    # 顽固情况：拿所有权 + 授予管理员完全控制，再删一次
    _run(['takeown', '/f', path, '/r', '/d', 'y'], timeout=timeout)
    _run(['icacls', path, '/grant', 'administrators:F', '/t', '/c', '/q'], timeout=timeout)
    clear_readonly(path)
    try:
        shutil.rmtree(path)
    except Exception:
        pass
    return not os.path.isdir(path)

# 本工具生成的随机用户名特征（p + 6 位，去易混字符），用于识别残留的孤儿目录
_ORPHAN_DIR_RE = re.compile(r'^p[a-kmnp-z2-9]{6}$')

def _user_dir_exists(name):
    r"""C:\Users\<name> 目录是否还存在（大小写不敏感）。"""
    try:
        entries = os.listdir('C:\\Users')
    except OSError:
        return False
    low = name.lower()
    return any(e.lower() == low for e in entries)

def _user_leftovers(name):
    """删除后仍残留的内容。返回描述列表，全部删干净返回 []。"""
    left = []
    if name.lower() in _all_local_users():
        left.append(_t('账号仍然存在', 'account still exists'))
    if _user_dir_exists(name):
        left.append(_t('用户目录 C:\\Users\\%s 没能删掉', 'profile folder C:\\Users\\%s could not be removed') % name)
    return left

def delete_user(name):
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    with _lock:
        db = _load()
        info = db.get(name.lower())
        real = info['name'] if info else name
        if real.lower() in RESERVED and real.lower() != 'afk':
            return False, _t('系统用户不可删除', 'System accounts cannot be deleted')
        # 0. close the remote window opened by us (else files stay locked)
        _kill_client(name.lower())
        thumb.drop(name)
        # 1. logoff any session, and wait until it really goes away
        sid_session = (_sessions().get(real.lower()) or {}).get('id')
        if sid_session:
            _run(['logoff', sid_session])
            for _ in range(10):
                time.sleep(1)
                if not (_sessions().get(real.lower())):
                    break
        # 2. find profile SID before deleting the account
        profile_sid, profile_dir = _find_profile(real)
        if not profile_dir:
            cand = os.path.join('C:\\Users', real)
            if os.path.isdir(cand):
                profile_dir = cand
        # 3. delete account（上次删除只删了一半时账号已不存在，直接跳过进入目录清理）
        if real.lower() in _all_local_users():
            rc, out = _run(['net', 'user', real, '/delete'])
            if rc != 0:
                return False, _t('删除失败: ', 'Delete failed: ') + (
                    out.strip().splitlines()[-1] if out.strip() else _t('未知错误', 'unknown error'))
        # 4. remove profile dir (WMI 以 SYSTEM 身份删，能解掉残留的 NTUSER.DAT 占用)
        if profile_dir and os.path.isdir(profile_dir) and profile_dir.lower().startswith('c:\\users\\'):
            ps = ("Get-CimInstance Win32_UserProfile | Where-Object {{$_.LocalPath -eq '{0}'}} | "
                  "ForEach-Object {{$_.Delete()}}").format(profile_dir)
            _run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], timeout=180)
        # 4b. ProfileList 键已删过的重试场景，profile_dir 会是 None，按用户名兜底强删
        if _user_dir_exists(real):
            _force_rmtree(os.path.join('C:\\Users', real))
        # 5. remove ProfileList registry key
        if profile_sid:
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList',
                    0, winreg.KEY_ALL_ACCESS)
                try:
                    winreg.DeleteKey(key, profile_sid)
                finally:
                    key.Close()
            except OSError:
                pass
        # 6. 验证：账号和目录都真正消失，才把用户从列表移除（删除按钮随之消失）；
        #    有残留就保留在列表中并标记 pending，用户可以再点删除重试
        left = _user_leftovers(real)
        if left:
            key = name.lower()
            if key in db:
                db[key]['pending'] = True
            else:
                db[key] = {'name': real, 'password': None, 'imported': True, 'pending': True}
            _save(db)
            return False, _t('、'.join(left) + '。该用户已保留在列表中，可再点「删除」重试清理',
                             '; '.join(left) + '. The user is kept in the list — click Delete again to retry')
        db.pop(name.lower(), None)
        _save(db)
        return True, None

def _find_profile(name):
    """return (sid, dir) of a local user's profile by matching dir name."""
    import winreg
    base = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList'
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return None, None
    target = 'c:\\users\\' + name.lower()
    result = (None, None)
    try:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                sk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + '\\' + sub)
                path, _ = winreg.QueryValueEx(sk, 'ProfileImagePath')
                sk.Close()
                if str(path).lower() == target:
                    result = (sub, str(path))
                    break
            except OSError:
                continue
    finally:
        key.Close()
    return result

# OpenStreamMS RemoteIdd 对 RDP 协议栈的改动（根因：它把 WinStations\RDP-Tcp 的
# LoadableProtocol_Object 换成了自己的 Provider COM 类，普通 RDP 客户端因此拿不到画面）
_RDP_TCP_KEY = r'SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
_OSMS_PROVIDER_CLSID = '{8B0F3C51-2D7A-4F6E-A1C9-5E4D2B7F0A36}'
_MS_RDP_CORE_CLSID = '{5828227c-20cf-4408-b73f-73ab70b8849f}'

def rdp_hijacked():
    """返回 OpenStreamMS 是否正在劫持 RDP 图形管线。"""
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RDP_TCP_KEY)
        try:
            cur, _ = winreg.QueryValueEx(k, 'LoadableProtocol_Object')
        finally:
            k.Close()
        return str(cur).lower() == _OSMS_PROVIDER_CLSID.lower()
    except OSError:
        return False

def rdp_repair():
    """把 RDP-Tcp 的协议对象还原为微软原版（rdpcorets），恢复标准图形管线。
    不重启 TermService（OSMS 源码注明强停该服务会挂死）；若新连接仍不生效需重启电脑。"""
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RDP_TCP_KEY,
                           0, winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError as e:
        return False, _t('打开注册表失败: ', 'Failed to open registry key: ') + str(e)
    try:
        try:
            cur, _ = winreg.QueryValueEx(k, 'LoadableProtocol_Object')
        except OSError:
            return False, _t('注册表里没有 LoadableProtocol_Object，环境异常，不处理',
                             'LoadableProtocol_Object missing from registry; environment looks abnormal, skipping')
        if str(cur).lower() != _OSMS_PROVIDER_CLSID.lower():
            return False, _t('RDP 图形管线当前没有被劫持，无需修复',
                             'The RDP graphics pipeline is not hijacked, nothing to repair')
        # 还原为 OSMS 安装时备份的原始协议对象，找不到就用微软原版 CLSID
        try:
            inner, _ = winreg.QueryValueEx(k, 'OsmsInnerProtocol_Object')
            inner = str(inner)
        except OSError:
            inner = _MS_RDP_CORE_CLSID
        # 先备份，便于回滚
        try:
            with open(os.path.join(BASE_DIR, 'rdp_repair_backup.json'), 'w', encoding='utf-8') as f:
                json.dump({'LoadableProtocol_Object': str(cur), 'restored': inner}, f, indent=2)
        except Exception:
            pass
        winreg.SetValueEx(k, 'LoadableProtocol_Object', 0, winreg.REG_SZ, inner)
        for v in ('OsmsInnerProtocol_Object', 'OsmsRefreshHz', 'OsmsRemoteIddAllConnections',
                  'OsmsSeatWidth', 'OsmsSeatHeight', 'OsmsScalePercent'):
            try:
                winreg.DeleteValue(k, v)
            except OSError:
                pass
    finally:
        k.Close()
    return True, None

def list_users():
    _ensure_adopted()
    for k in [k for k, e in list(_clients.items()) if not _alive(e)]:
        _clients.pop(k, None)          # 进程已经没了，别拿旧记录污染状态
    with _lock:
        db = _load()
        # 1) 远程用户组的成员拉进 db（imported，无密码）
        for m in _group_members():
            k = m.lower()
            if k not in db:
                db[k] = {'name': m, 'password': None, 'imported': True}
        # 2) 孤儿目录：本工具生成的随机名目录、账号已删但目录残留（比如条目曾被
        #    错误移除）→ 重新列出来，保证界面上始终有删除入口
        try:
            dirs = os.listdir('C:\\Users')
        except OSError:
            dirs = []
        existing = _all_local_users()
        for d in dirs:
            if not _ORPHAN_DIR_RE.match(d.lower()):
                continue
            k = d.lower()
            if k in existing or k in db:
                continue
            db[k] = {'name': d, 'password': None, 'imported': True, 'pending': True}
        # 3) 复查 pending 条目：账号 + 目录都真正消失才移除（删除按钮随之消失）
        for k in [k for k, v in db.items() if v.get('pending')]:
            if not _user_leftovers(db[k]['name']):
                db.pop(k, None)
        _save(db)
    sess = _sessions()
    admins = {a.lower() for a in _group_members(ADMIN_GROUP)}

    # 程序重启（或上次崩溃）后内存里没有 pid 记录，但客户端进程可能还活着、
    # 窗口还藏着。这时候用「只有一个用户缺窗口 + 系统里只有一个空闲客户端窗口」
    # 来认领，避免多个会话之间张冠李戴。
    waiting = [v['name'] for v in db.values()
               if (sess.get(v['name'].lower()) or {}).get('state') == 'active'
               and not _alive(_clients.get(v['name'].lower()))]
    if waiting:
        known = {_pid_of(e) for e in _clients.values() if _alive(e)}
        free = [(h, p, bool(_user32.IsWindowVisible(h))) for h, p, _t in _top_windows()
                if p not in known and _looks_client(p)]
        if len(free) == 1 and len(waiting) == 1:
            h, p, vis = free[0]
            _clients[waiting[0].lower()] = {'proc': None, 'pid': p, 'hwnd': h,
                                            'hidden': not vis, 'intent': '',
                                            'auto': False, 't0': 0}
            _save_clients()
            _log('认领 %s: pid %s hwnd %s 可见=%s' % (waiting[0], p, h, vis))
        elif free:
            _log('无法归属客户端窗口：待认领用户=%s，空闲窗口=%s' % (waiting, free))

    out = []
    for k, v in db.items():
        st = sess.get(v['name'].lower())
        ent = _clients.get(v['name'].lower())
        # window: none 没窗口 / visible 窗口在桌面上 / hidden 收进后台（会话仍 Active）
        # 有客户端就按窗口真实可见性校正一次：程序重启、上次异常退出后，
        # 记录里的 hidden 可能已经过时，不校正就会出现「明明藏着却只能点隐藏」。
        if _alive(ent):
            hwnd = ent.get('hwnd') or _client_window(v['name'], v['name'].lower())[0]
            if hwnd:
                ent['hwnd'] = hwnd
                ent['hidden'] = not bool(_user32.IsWindowVisible(hwnd))
            win = 'hidden' if ent.get('hidden') else 'visible'
        else:
            win = 'visible' if (st and st['state'] == 'active') else 'none'
        out.append({
            'name': v['name'],
            'online': bool(st) and st['state'] == 'active',
            'connected': bool(st),
            'window': win,
            'state': st['state'] if st else '',
            'managed': not v.get('imported'),
            'pending': bool(v.get('pending')),
            'admin': v['name'].lower() in admins,
        })
    out.sort(key=lambda x: (not x['pending'], not x['managed'], x['name'].lower()))
    return out

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>用户管理</title>
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAIfElEQVR42rWXW4wc1RGGvzrdPT2zs/f12t61WbK2WV9jbASIJJCEe3AkBAiDDFjCSOGS2MoDL4l4yBPwEqIEoRAhFCUixAIERIRI4RIkA4odRZaJwSy+xWJjr7322rve2Znp6T7nVB56vPYG8xRzpFG3RtVVdar++qtKAFi/PuCVV9zQdzbMCUzLZpQ7FF2CahEQ/r+jiCSCHEB4zfnaM/u2bR0/Y1POvCy9ZuPNJoieFRMMqneoelAuzBEQMYgJUO8OeZc9sveDF95i/fpAAC65euMNYVh4W1Hx1joRDIhwQY+qKt6EYSCIWpvetP/DF96VZddv7PGp7DYm7PfOOkQCvsqj6kwQBs7b0aCgq0OXsiUIo35vMyeBCc6XQvXKTD5EEDFfotuDnisnX8iiIIFX64Iw6ndpuiVE9W51Vl1SE6xtYk7PPk2AKZbyhIjg0wxtJHA+1XERE4UAeGvx1Tp4PxvGCoShRK1lRfXuUFUX+bQhvWsuk9ahZeD1rKQJSE8c48SO7VjnUJvROm8+PVdchRTiWbfVtMHJf+5geuwYiGBiz5zbVxAOdKBez15LINl51ExsG0HC4iLjatVCz6VrWLDpYWxrK2kYkMYRaaFAGghd627jotvuxNXrxO0dDD60hXDpchqiZHFEFkc0RAmXLmfwoS3Ebe24Wo15P7qSwr2rqVtPAtQFEq8kpYj2x75N1/UXk1WmC6FLqrStWMXxf3zAgV8+TlBuA/WIMdhqhd5rb2HRnRsRlJa+BVgRhp98DFetIE3IqHcE5TYuffxpSvP7SSaPEq3t48gT26i+9inSHueRNYKfauB+/j1ar1qIf+0TQps18NZip08jcUxQLqPeIcZgULJqBZ82ZlLoKlOoOoLWttkA9A43XcnfjaINi6vUoDNCWkPwigQGb1P8RBUtteNtSujSGqoen2XYpIoplWYccI06rlEHAe8tHo+qYus1TBTNcsBnGaoeRVHvQASXpLgkQSOdccDVE3xmUVFslmBs2sA16rQOrUCKRZzL8Orx3uG9pXVoBSYIcWmCq1eJ5/UR9y3Apkkupx6bJsR9CyjO7cPWq7gkxYSG+NL5uT5vcd7ishQtGeLVffhaiksbhEQRo+/8mVU/eYIrnvotjZMnQAyoEpRKlPsHOLz1eTKbMbl/mPrwx6z92S+ojhyaVQXlgUFqn33M5MFhsrplcutOFmy+mp4bl+GTDAkMaj1Bb5liOebzp99DA0HmLr5KXdqgpbOLjqUr6Vy5lt5vXsvU3j2c2rWDyt5PqYwdxUQx6j1hGNI+uJj2pSuZ843vAjC+fRtTez9h6tBBrLV5WdoGbUO9dFw/RMe61RQu6qb2rxEm3/iIyfcPUD08QRDHSO/glZrnO8EUCnStvhwJQjCGyv5hkrGjhOVWUJ9HBnBJnY7lX6fQ3QtAemqc08O7CYqlM4gEEbKpKtFAB+XLL8qx0XBMv38ArXmClrwyZM7XrlARyVnOZth6rRlaJYiLmLiYk4jXma6mqrh6De8sACYICUotOfU2u6gYQQV8LcElKdLsbUG5hClE4BVVJQRwWYpRT9zWQXFWeSlZdZosywjiGPWKT2sUym3E3T3n0LHg0gZptYIUiogRbJIQRRFxV+esxqrO06icRsVgwojQ2YyWjk5WbP4ppYFF+EaSf6CKhCHaaLDvuac4vucjAhNwyV33M//m23FJ/axiVYJiiWNvvc6BV1/EZo65K9cw9OCjSByjTVygiomL1Ef+zafPPEnt9CShq1YY2PAAxcXL2P/sUzP5A8E36vTftoElmzYztuU+OpavZuH6TRx6+fdUD+7DFOKcA9IG5cVDDN61iRM7d3ByeDdLNm2mOnGK0T9txcSlPFqqIIYljzzKwLo72PP8rwhRT6F7DuPbtzGx8+8Uuuag3iLGkFWnCd56g0X3bMIEhqizk/rYKGPvvokJZnfu6UP7mP+dG4k6OzGBwcRFxl7dytS+PUTlVtR7xISkE+OMb7+cQvccUE+oSg4mY5BiEaIIvAFjMHEMUYhai/cedQ68R+I8z+fQAOIVmjLe+zzsUdjUUcjbsglyG8bgnUUVQvUeE0ZoIyMZP5Y74z0YwVamKCe1s61XDBJGpKdPgnNwZn7xDoIACaMZEpNCjE1qJCeOEba1N5uRIZ0YR73HxFHOK2qE6UP7Gdj4Q2qjI/i0gc9STFRAnWXhrRtIjx/FWUt97AjF7h4WPfBjKvs+aQIrr5a2oVUUu3uojx3BWUt6/CiD9z1M1N6BBOGMTlOI6btuHSN/+DVqBOnoW6lhGDJ074N0rFyLbySUL15MMjaKz1KyyVPs/d0zVI6NgvcsvOY6Bm69B5c2KM3vB4T6sSMEhZiRN/7I4Q/eA2Nom9/P0vs3E3V2Y6ICxXkLqH5+ABMXOb1nF/tefA5rLdLRv1JVFTtdodTTy0XfX0/XqsuojY5w+K+vM7FnF0FbByYIAcUlCeIsfdetY941NwEw9uE7HP3bX9AgJCjmq4R3Flc5TdeKNSy85Q5a+geY2LOL/7z5MvWTJwhb2xARpH3+8oaIFJB8Eg/iIjap58TjHN5m52XCoFhCbZaDMIxwSf2LTNhkSQlDXCMhLLbgGkmOJ/Woairt85YNI2Zp81+j3s8YOUPR552Azx02FcSYLxvDZ3Rpc9ICyclG/V6j6EuAqHpVBcTkuGqO1NpU8L+/M2TVDMuXyp2rCzH5NfM5XxR9Sdr6l/Wo9btFTL+qdwJf6WKi4ERMoOpHJTSrBaA895IbBPM2IPk8hbkAS+l5bOObk6wq/qbq8f3vBkCQVU8dDFs6dwDfEpHuZnguqHVprlSqekhxG2onDrwDBEHTsyCrTeyPO+a8qNbVFXoEbQfCC7KeQ6LwGd7/RiLzg+rYgY/JU+3+C8B1pw5/BsSTAAAAAElFTkSuQmCC">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7fb;--card:#fff;--line:#e5e7ef;--txt:#1c1e28;--sub:#7a7f99;--brand:#534ab7;--ok:#0f6e56;--bad:#a32d2d}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;display:flex;justify-content:center;padding:40px 16px}
.wrap{width:100%;max-width:1180px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
h1{font-size:22px;font-weight:600;margin-bottom:4px}
.sub{color:var(--sub);font-size:13px;margin-bottom:6px}
.ver{color:var(--sub);font-size:11px;opacity:.62;margin-bottom:22px;font-variant-numeric:tabular-nums}
button{height:40px;border:none;border-radius:9px;padding:0 18px;font-size:14px;cursor:pointer;font-weight:500}
.primary{background:var(--brand);color:#fff}
.ghost{background:#eef0f8;color:var(--txt)}
.danger{background:#fceaea;color:var(--bad)}
button:disabled{opacity:.5;cursor:default}
.hint{color:var(--sub);font-size:13px;line-height:1.6}
#banner{display:none;background:#fdf3e3;border:1px solid #f2ddb8;color:#8a5a12;font-size:13px;padding:10px 14px;border-radius:10px;margin-bottom:16px;line-height:1.6}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(292px,1fr));gap:16px}
.ucard{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 1px 2px rgba(20,20,50,.05);display:flex;flex-direction:column;transition:box-shadow .18s,border-color .18s;position:relative}
/* 删除进行中：整卡轻微呼吸缩放 + 蒙层进度条 */
.ucard.deleting{animation:delpulse 1.6s ease-in-out infinite}
.ucard.deleting .shot,.ucard.deleting .cbody{opacity:.42;transition:opacity .35s}
.delover{position:absolute;inset:0;z-index:6;display:none;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:rgba(15,18,32,.42);backdrop-filter:blur(1.5px)}
.ucard.deleting .delover{display:flex}
.deltxt{font-size:12.5px;color:#fff;text-shadow:0 1px 4px rgba(0,0,0,.45)}
.delbar{width:64%;height:6px;border-radius:99px;background:rgba(255,255,255,.28);overflow:hidden}
.delbar i{display:block;height:100%;width:0;border-radius:99px;background:var(--brand);transition:width .25s linear}
.delbar.indet i{width:32%;animation:delind 1.1s linear infinite}
@keyframes delind{from{transform:translateX(-110%)}to{transform:translateX(330%)}}
@keyframes delpulse{50%{transform:scale(.982)}}
.ucard:hover{box-shadow:0 6px 18px rgba(20,20,50,.10);border-color:#d8dbe9}
/* 缩略图：整幅画面按比例缩进卡片，比例对不上就留黑边（contain），绝不裁剪/拉伸 */
.shot{position:relative;aspect-ratio:16/10;background:#0a0f19;cursor:pointer;overflow:hidden}
.shot img{width:100%;height:100%;object-fit:contain;display:block;opacity:0;transition:opacity .3s}
.shot img.ready{opacity:1}
.shot .ph{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#6b7590;font-size:12.5px;text-align:center;padding:0 18px;line-height:1.5}
.shot .ph svg{opacity:.5}
.shot .veil{position:absolute;inset:0;background:rgba(10,16,26,.58);display:none;align-items:center;justify-content:center;color:#dfe6f4;font-size:12.5px;text-align:center;padding:0 20px;line-height:1.5}
.shot.veil-on .veil{display:flex}
.shot.veil-on .ph{display:none}
.shot.is-dim img{filter:grayscale(.3) brightness(.8)}
.shot.is-dim .stamp{opacity:.45}
.shot .badge{position:absolute;left:9px;top:9px;font-size:11.5px;padding:3px 9px;border-radius:99px;background:rgba(12,18,30,.62);color:#dfe6f4;backdrop-filter:blur(4px)}
.shot .stamp{position:absolute;right:9px;bottom:8px;font-size:10.5px;color:rgba(230,236,248,.72);background:rgba(12,18,30,.5);padding:2px 7px;border-radius:99px}
.shot .stamp:empty{display:none}
.cbody{padding:12px 14px 14px;display:flex;flex-direction:column;gap:11px}
.cname{display:flex;align-items:center;gap:7px;font-weight:600;font-size:14px;min-width:0}
.cname span.nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.acts{display:flex;gap:6px;flex-wrap:wrap}
.acts button{height:30px;padding:0 11px;font-size:12.5px;border-radius:8px}
.tag{display:inline-block;font-size:12px;padding:2px 10px;border-radius:99px;flex:0 0 auto}
.on{background:#e1f5ee;color:var(--ok)}
.off{background:#eef0f8;color:var(--sub)}
.disc{background:#fdf3e3;color:#8a5a12}
.adm{background:#eee9fb;color:#534ab7;font-size:11px;padding:2px 8px}
#msg{margin-top:14px;font-size:13px;min-height:20px}
.ok{color:var(--ok)}.err{color:var(--bad)}
.empty{color:var(--sub);text-align:center;padding:28px 0;font-size:14px;grid-column:1/-1}
.newcard{border-style:dashed;border-color:#ccd3e6;background:rgba(255,255,255,.55)}
.newcard:hover{border-color:#9fa9c6;background:#fff;box-shadow:0 6px 18px rgba(20,20,50,.08)}
.newshot{width:100%;height:auto;flex:0 0 auto;aspect-ratio:16/10;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:0;background:transparent;border-radius:0;color:var(--sub);cursor:pointer}
.newshot .plus{width:62px;height:62px;border-radius:50%;border:2px dashed #b8c1d8;display:flex;align-items:center;justify-content:center;font-size:35px;font-weight:300;line-height:1;color:#8d97b4;padding-bottom:4px;transition:transform .18s,color .18s,border-color .18s}
.newcard:hover .plus{border-style:solid;border-color:var(--brand);color:var(--brand);transform:scale(1.06)}
.newlbl{font-size:13px;font-weight:500}
.newbody{gap:9px}
.newopt{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--sub);cursor:pointer;user-select:none;min-width:0}
.newopt input{width:15px;height:15px;accent-color:var(--brand);cursor:pointer;flex:0 0 auto}
.newopt select{height:28px;font-size:12.5px;border:1px solid var(--line);border-radius:7px;padding:0 6px;background:#fff;color:var(--txt);flex:1;min-width:0;cursor:pointer}
.newbtn{height:32px;font-size:13px;border-radius:8px;padding:0 12px}
.langbtn{height:32px;padding:0 12px;font-size:13px;flex:0 0 auto}
.upd{display:none;margin:0 0 14px;padding:12px 14px;border:1px solid #d9d3f4;background:#f4f2ff;border-radius:12px;color:#312c66}
.upd.on{display:block}
.updrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.upddot{width:8px;height:8px;border-radius:50%;background:var(--brand);flex:0 0 auto}
.updtxt{font-size:13.5px;font-weight:600}
.updsub{font-size:12.5px;color:#605a95}
.updsp{flex:1 1 auto}
.updbtn{height:30px;font-size:12.5px;padding:0 12px;flex:0 0 auto}
.updbtn:disabled{opacity:.55;cursor:default}
.updprog{display:none;height:4px;border-radius:2px;background:#e2ddf7;margin-top:10px;overflow:hidden}
.updprog.on{display:block}
.updprog i{display:block;height:100%;width:0;background:var(--brand);transition:width .25s}
.upda{color:var(--brand);text-decoration:none;font-size:12.5px;border-bottom:1px dotted currentColor}
</style></head><body>
<div class="wrap">
<div class="top">
<div><h1 id="ttl">用户管理</h1><div class="sub" id="sub"></div><div class="ver" id="ver"></div></div>
<button class="ghost langbtn" id="btnLang" onclick="toggleLang()">English</button>
</div>
<div id="banner"></div>
<div class="upd" id="upd">
<div class="updrow"><span class="upddot"></span><span class="updtxt" id="updTxt"></span><span class="updsub" id="updSub"></span><span class="updsp"></span><button class="primary updbtn" id="updGo" onclick="updGo()">立即升级</button><button class="ghost updbtn" id="updLater" onclick="updLater()">稍后</button></div>
<div class="updprog" id="updProg"><i id="updBar"></i></div>
</div>
<div id="msg"></div>
<div class="hint" id="shotHint" style="margin:0 0 12px 2px"></div>
<div id="grid" class="grid"><div class="ucard newcard" id="newCard">
<button class="newshot" id="btnNew" onclick="register()" title="点击创建新账号，账号和密码自动生成">
<span class="plus">+</span><span class="newlbl" id="newLbl">新建账号</span>
</button>
<div class="cbody newbody">
<label class="newopt" id="chkAdminLbl">
<input type="checkbox" id="chkAdmin" onchange="saveCfg()"><span id="chkAdminText">授予管理员权限</span></label>
<div class="newopt"><span id="resLabel">远程桌面分辨率</span><select id="resSel" onchange="saveRes()">
<option value="auto" id="resAuto">跟随本机屏幕</option>
<option value="fullscreen" id="resFull">全屏（无窗口边框）</option>
<optgroup label="本机支持" id="grpNative"></optgroup>
<optgroup label="常用分辨率" id="grpCommon"></optgroup>
</select></div>
<button class="primary newbtn" id="btnCreate" onclick="register()">创建账号</button>
<button class="danger newbtn" id="btnFix" style="display:none" onclick="fixRdp()">修复画面</button>
</div>
</div>
</div>
</div>
<script>
const I18N={
zh:{
 title:'用户管理',
 subtitle:'点一下自动创建一个独立账号，密码由程序随机生成并保存',
 btnNew:'新建账号',
 btnCreate:'创建账号',
 newTip:'点击创建新账号，账号和密码自动生成',
 chkAdminTip:'勾选后新账号加入管理员组：启动需要管理员的程序（如游戏启动器）时 UAC 只需点「是」，不用输管理员密码',
 btnFix:'修复画面',
 chkAdmin:'授予管理员权限',
 thUser:'用户', thStatus:'状态', thAct:'操作',
 empty:'还没有账号，点左边卡片上的 + 新建一个',
 online:'运行中', offline:'不在线', disconnected:'已断开 · 后台运行', pending:'残留', admin:'管理员',
 bg:'后台运行 · 会话激活',
 disconnect:'断开',
 disconnecting:'正在断开…',
 discOk:'已断开：桌面里的程序仍在后台运行，点「登录」可回到原来的桌面（不是重新开始）',
 discFail:'断开失败：',
 hide:'隐藏窗口',
 show:'显示窗口',
 hiding:'正在把窗口收进后台…',
 hideOk:'已收进后台：连接没断，会话保持激活（里面的窗口仍有焦点），点「显示窗口」随时回来',
 hideFail:'收进后台失败：',
 showOk:'窗口已恢复，桌面还是原来那个',
 showFail:'恢复窗口失败：',
 tagActiveTitle:'该用户正连接着一个远程桌面会话（占用一个会话名额）',
 tagBgTitle:'窗口已隐藏但连接还在：会话仍是 Active，程序继续渲染并持有焦点，只是画面看不到',
 tagDiscTitle:'会话仍然保留：桌面里的程序继续在后台运行，点「登录」接回原桌面',
 tagOffTitle:'该用户当前没有会话（从未登录，或已注销）',
 login:'登录', reset:'重置', grant:'授予管理员', del:'删除用户', delPending:'删除残留',
 tagAdmTitle:'已在管理员组：UAC 弹窗只需点一下「是」，不用输密码',
 tagPendTitle:'账号或文件没删干净，点右侧按钮继续清理',
shotNone:'未连接',
shotHidden:'窗口已隐藏 · 点一下打开',
shotOff:'会话已断开',
shotWait:'等待画面…',
shotTip:'点一下切到这个远程桌面',
shotLive:'画面在后台标签页时暂停刷新',
focusFail:'切换窗口失败：',
 bannerNoAdmin:'⚠ 当前没有管理员权限，创建/删除会失败。请以管理员身份重新运行本程序。',
 creating:'正在创建…',
 createdAdmin:'已创建用户 {name}（管理员权限：启动需要管理员的程序只需点「是」），点「登录」即可进入',
 createdNormal:'已创建用户 {name}（普通用户：启动需要管理员的程序时要输密码），点「登录」即可进入',
 opening:'正在打开远程窗口…',
 loginOk:'已发起登录，请留意弹出的远程窗口',
 loginFail:'登录失败：',
 resetConfirm:'重置会把 {name} 的会话注销（里面的程序会关闭），确认？',
 resetting:'正在重置会话…',
 resetOk:'会话已重置，现在点「登录」会进入全新桌面',
 resetFail:'重置失败：',
 grantConfirm:'把 {name} 加入管理员组？\n\n之后它启动需要管理员权限的程序（比如游戏启动器）时，UAC 弹窗只需点一下「是」，不用再输管理员密码。\n\n注意：已经登录的会话要注销重登才生效（先点「重置」再点「登录」）。',
 granting:'正在授予管理员权限…',
 grantOk:'{name} 已加入管理员组，点「重置」再「登录」后生效',
 grantFail:'授予失败：',
 delConfirm:'确定删除用户 {name} ？账号和文件都会清理掉。',
 deleting:'正在删除 {name} …',
 deleted:'已删除 {name}',
 delFail:'删除失败：',
 saveCfgOkAdmin:'已保存：新创建的用户会自动授予管理员权限',
 saveCfgOkNormal:'已保存：新创建的用户为普通用户（启动需要管理员的程序时要输管理员密码）',
 saveCfgFail:'保存设置失败：',
 fixConfirm:'检测到 OpenStreamMS 接管了 RDP 图形管线（登录后白屏的原因）。\n\n修复会把 RDP 协议还原为 Windows 原版，影响：\n· 修复后 OpenStreamMS 的串流功能将不可用（可随时用它的安装脚本恢复）\n· 修复完成后请点「登录」验证画面；如果仍白屏，重启一次电脑即可生效\n\n确认修复？',
 fixing:'正在还原 RDP 协议…',
 fixOk:'已还原。现在点「登录」试试画面；如果还是白屏，重启电脑后即可生效',
 requestFail:'请求失败：',
 netErr:'连不上后台服务（服务可能已退出，请重启程序）',
 badResp:'后台返回了无法解析的内容',
 resLabel:'远程桌面分辨率',
 resAuto:'自动（窗口化·挑合适尺寸）',
 resFull:'全屏（无窗口边框）',
 grpNative:'本机支持',
 grpCommon:'常用分辨率',
 langBtn:'English',
 buildLbl:'构建',
 devBuild:'开发版',
 updFound:'发现新版本 v{v}（当前 v{c}）',
 updChecking:'正在检查更新…',
 updUptodate:'已是最新版本 v{v}',
 updDown:'正在下载 v{v} · {p}%',
 updReady:'v{v} 已下载完成，重启后生效',
 updApplying:'正在重启以完成升级…',
 updFail:'更新失败：',
 updGo:'立即升级',
 updReboot:'立即重启',
 updLater:'稍后',
 updRetry:'重试',
 updNotes:'更新说明',
 updCfg:'自动检查更新'
},
en:{
 title:'User Management',
 subtitle:'Create an isolated local account in one click — the password is generated and saved automatically',
 btnNew:'New account',
 btnCreate:'Create',
 newTip:'Click to create a new account - username and password are generated for you',
 chkAdminTip:'Add new accounts to the Administrators group: programs that need elevation only ask for a Yes/No instead of an admin password',
 btnFix:'Fix display',
 chkAdmin:'Grant administrator',
 thUser:'User', thStatus:'Status', thAct:'Actions',
 empty:'No accounts yet - click + on the card to create one.',
 online:'Active', offline:'Offline', disconnected:'Disconnected · running', pending:'Leftover', admin:'Admin',
 bg:'Background · session active',
 disconnect:'Disconnect',
 disconnecting:'Disconnecting…',
 discOk:'Disconnected - programs keep running in the background; click Login to return to the same desktop',
 discFail:'Disconnect failed: ',
 hide:'Hide window',
 show:'Show window',
 hiding:'Hiding the window…',
 hideOk:'Moved to background: the connection stays up, so the session is still Active (its windows keep focus). Click Show window to bring it back',
 hideFail:'Failed to hide: ',
 showOk:'Window restored - same desktop as before',
 showFail:'Failed to show the window: ',
 tagActiveTitle:'This user is connected to a remote desktop session (uses one session slot)',
 tagBgTitle:'Window hidden but still connected: session stays Active, programs keep rendering and holding focus - just not visible',
 tagDiscTitle:'The session is kept: programs keep running. Click Login to reconnect to the same desktop',
 tagOffTitle:'No session for this user (never signed in, or signed out)',
 login:'Login', reset:'Reset', grant:'Grant admin', del:'Delete', delPending:'Clean up',
 tagAdmTitle:'Already in Administrators: UAC only needs a click, no password',
 tagPendTitle:'Account or files were not fully removed — click to continue cleaning up',
shotNone:'Not connected',
shotHidden:'Window hidden · click to open',
shotOff:'Session disconnected',
shotWait:'Waiting for the screen…',
shotTip:'Click to switch to this desktop',
shotLive:'Preview pauses while this tab is in the background',
focusFail:'Failed to switch window: ',
 bannerNoAdmin:'⚠ No administrator rights: creating/deleting users will fail. Please run this program as administrator.',
 creating:'Creating…',
 createdAdmin:'User {name} created (administrator: programs that need admin rights only require a click on UAC). Click Login to enter',
 createdNormal:'User {name} created (standard user: programs that need admin rights will ask for an admin password). Click Login to enter',
 opening:'Opening remote window…',
 loginOk:'Login started — look for the remote desktop window',
 loginFail:'Login failed: ',
 resetConfirm:'Resetting will sign {name} out (running programs will close). Continue?',
 resetting:'Resetting session…',
 resetOk:'Session reset — the next Login starts a fresh desktop',
 resetFail:'Reset failed: ',
 grantConfirm:'Add {name} to the Administrators group?\n\nPrograms that need admin rights (e.g. a game launcher) will then only require a click on the UAC prompt instead of an admin password.\n\nNote: an existing session must be signed out and back in for this to take effect (Reset, then Login).',
 granting:'Granting administrator rights…',
 grantOk:'{name} is now in Administrators — Reset then Login to apply',
 grantFail:'Grant failed: ',
 delConfirm:'Delete user {name}? The account and its files will be removed.',
 deleting:'Deleting {name} …',
 deleted:'Deleted {name}',
 delFail:'Delete failed: ',
 saveCfgOkAdmin:'Saved: new users are granted administrator rights automatically',
 saveCfgOkNormal:'Saved: new users are standard users (programs that need admin rights will ask for an admin password)',
 saveCfgFail:'Failed to save settings: ',
 fixConfirm:'OpenStreamMS has taken over the RDP graphics pipeline (the cause of the white screen after login).\n\nRepair restores the original Windows RDP protocol. Impact:\n· OpenStreamMS streaming will stop working (you can restore it with its installer at any time)\n· After repairing, click Login to verify; if the screen is still white, reboot once\n\nRepair now?',
 fixing:'Restoring the RDP protocol…',
 fixOk:'Restored. Click Login to test the screen; if it is still white, reboot the PC',
 requestFail:'Request failed: ',
 netErr:'Cannot reach the backend (it may have exited - restart the app)',
 badResp:'Backend returned an unreadable response',
 resLabel:'Remote desktop resolution',
 resAuto:'Auto (windowed, best fit)',
 resFull:'Fullscreen (borderless)',
 grpNative:'Supported by this display',
 grpCommon:'Common resolutions',
 langBtn:'中文',
 buildLbl:'build',
 devBuild:'dev build',
 updFound:'Update available: v{v} (you have v{c})',
 updChecking:'Checking for updates…',
 updUptodate:'You are on the latest version v{v}',
 updDown:'Downloading v{v} · {p}%',
 updReady:'v{v} downloaded — restart to apply',
 updApplying:'Restarting to finish the update…',
 updFail:'Update failed: ',
 updGo:'Update now',
 updReboot:'Restart now',
 updLater:'Later',
 updRetry:'Retry',
 updNotes:'Release notes',
 updCfg:'Check for updates automatically'
}
};
let LANG='zh';
function t(k,v){let s=(I18N[LANG]&&I18N[LANG][k])||I18N.zh[k]||k;if(v){for(const p in v){s=s.split('{'+p+'}').join(v[p])}}return s}
function applyI18n(){
document.documentElement.lang=(LANG==='zh'?'zh-CN':'en');
document.title=t('title');
document.getElementById('ttl').textContent=t('title');
document.getElementById('sub').textContent=t('subtitle');
document.getElementById('newLbl').textContent=t('btnNew');
document.getElementById('btnCreate').textContent=t('btnCreate');
document.getElementById('btnFix').textContent=t('btnFix');
document.getElementById('chkAdminText').textContent=t('chkAdmin');
document.getElementById('chkAdminLbl').title=t('chkAdminTip');
document.getElementById('btnNew').title=t('newTip');
document.getElementById('resLabel').textContent=t('resLabel');
document.getElementById('resAuto').textContent=t('resAuto');
document.getElementById('resFull').textContent=t('resFull');
document.getElementById('grpNative').label=t('grpNative');
document.getElementById('grpCommon').label=t('grpCommon');
document.getElementById('btnLang').textContent=t('langBtn');
document.getElementById('shotHint').textContent=t('shotLive');
}
async function api(path,body){
try{
const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
const txt=await r.text();
if(!txt)return {ok:false,error:t('netErr')+' (HTTP '+r.status+')'};
try{return JSON.parse(txt)}catch(e){return {ok:false,error:t('badResp')+' (HTTP '+r.status+')'}}
}catch(e){return {ok:false,error:t('netErr')+' ('+e.message+')'}}
}
function msg(t2,cls){const m=document.getElementById('msg');m.textContent=t2||'';m.className=cls||''}
function applyStatus(s){
const vb=document.getElementById('ver');
if(vb){const bits=[];if(s&&s.version)bits.push('v'+s.version);else if(s&&s.dev_build)bits.push(t('devBuild'));
if(s&&s.build)bits.push(t('buildLbl')+' '+s.build);vb.textContent=bits.join(' \u00b7 ')}
const b=document.getElementById('banner');
if(s&&s.admin===false){b.style.display='block';b.textContent=t('bannerNoAdmin')}else{b.style.display='none'}
const fix=document.getElementById('btnFix');
if(s&&s.rdp_hijacked){fix.style.display='inline-block'}else{fix.style.display='none'}
if(s){document.getElementById('chkAdmin').checked=!!s.admin_default;
const rs=document.getElementById('resSel');
function fillGroup(id,list){
const g=document.getElementById(id);if(!g)return;
g.innerHTML='';
for(const m of (list||[])){
const op=document.createElement('option');op.value=m;op.textContent=m.replace('x',' \u00d7 ');g.appendChild(op);
}}
fillGroup('grpNative',s.modes);
fillGroup('grpCommon',s.common);
if(s.resolution&&s.resolution!==rs.value)rs.value=s.resolution;
if(!rs.value)rs.value='auto';
}
}
async function saveCfg(){
const on=document.getElementById('chkAdmin').checked;
try{
await api('config',{admin_default:on});
msg(on?t('saveCfgOkAdmin'):t('saveCfgOkNormal'),on?'ok':'');
}catch(e){msg(t('saveCfgFail')+e,'err')}
}

/* ---------- 自动升级 ----------
   服务端自己去 GitHub 查版本（tag → exe 版本资源），这里只负责显示和触发。
   下载是服务端流式写盘，前端靠轮询 /api/update 拿百分比；进度快的时候 0.9 秒
   一次，闲下来 20 秒一次，切到后台标签页也不停（升级中途换标签别丢进度）。*/
let updState=null,updTimer=null,updManual=false,updSkip='';
function updRender(s){
if(!s)return;
updState=s;
const box=document.getElementById('upd');if(!box)return;
const ph=s.phase||'idle';
const txt=document.getElementById('updTxt'),sub=document.getElementById('updSub');
const go=document.getElementById('updGo'),later=document.getElementById('updLater');
const prog=document.getElementById('updProg'),bar=document.getElementById('updBar');
let show=true,main='',btn='',btnOff=false,laterOn=true,progOn=false,pct=0;
if(ph==='found'){
if(updSkip&&updSkip===s.version)show=false;
main=t('updFound',{v:s.version,c:s.current});
btn=t('updGo');
}else if(ph==='downloading'){
main=t('updDown',{v:s.version,p:s.pct||0});btn=t('updGo');btnOff=true;laterOn=false;
progOn=true;pct=s.pct||0;
}else if(ph==='ready'){
main=t('updReady',{v:s.version});btn=t('updReboot');
}else if(ph==='applying'){
main=t('updApplying');btnOff=true;laterOn=false;progOn=true;pct=100;
}else if(ph==='checking'){
show=updManual;main=t('updChecking');btnOff=true;laterOn=false;
}else if(ph==='uptodate'){
show=updManual;main=t('updUptodate',{v:s.current||'?'});btnOff=true;laterOn=false;
}else if(ph==='error'){
show=updManual||!!s.version;main=t('updFail')+(s.error||'');btn=t('updRetry');
}else{
show=false;
}
if(!show){box.classList.remove('on');return}
box.classList.add('on');
txt.textContent=main;
if(s.page&&s.page.indexOf('https://github.com/')===0){
sub.innerHTML=' '+(s.published?s.published+' · ':'')+'<a class="upda" href="'+s.page+'" target="_blank" rel="noopener">'+t('updNotes')+'</a>';
}else{sub.textContent=s.published?(' '+s.published):''}
go.textContent=btn||t('updGo');
go.style.display=btn?'':'none';
go.disabled=btnOff;
later.style.display=laterOn?'':'none';
prog.classList.toggle('on',progOn);
if(progOn)bar.style.width=(pct>0?pct:3)+'%';
}
async function updPoll(){
clearTimeout(updTimer);
try{
const r=await fetch('/api/update',{cache:'no-store'});
updRender(await r.json());
}catch(e){}
const ph=updState&&updState.phase;
const fast=(ph==='downloading'||ph==='applying'||ph==='checking');
updTimer=setTimeout(updPoll,fast?900:(updManual?5000:20000));
}
async function updGo(){
const s=updState||{};
if(s.phase==='applying'||s.phase==='downloading')return;
let action='check';
if(s.phase==='ready')action='apply';
else if(s.phase==='found'||s.phase==='error')action=s.version?'download':'check';
updManual=true;
if(action==='apply'){
const cur=(updState&&updState.current)||'';
const r=await api('update',{action});
const st=(r&&r.state)||{};
updRender(st);
if(r&&r.ok){
/* 进程马上会退出换 exe：连不上说明正在重启，等新实例报出一个不同的版本号再刷新，
   不然可能在旧进程退出前的空档刷回旧页面 */
let n=0;
const again=setInterval(async()=>{
n++;
try{
const rr=await fetch('/api/status',{cache:'no-store'});
if(rr.ok){
const s=await rr.json();
if(s.version&&s.version!==cur){clearInterval(again);location.reload()}
}
}catch(e){}
if(n>60)clearInterval(again);
},1500);
}
return;
}
await api('update',{action});
updPoll();
}
function updLater(){
updSkip=(updState&&updState.version)||'';
document.getElementById('upd').classList.remove('on');
updManual=false;
}
async function updToggleAuto(on){
try{await api('config',{auto_update:!!on})}catch(e){}
}
async function saveRes(){
try{await api('config',{resolution:document.getElementById('resSel').value})}catch(e){}
}
const USERS_MS=8000, SHOT_MS=6000;
let cards={};
const MON_SVG='<svg width="27" height="27" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="2.5" y="4" width="19" height="12.5" rx="2.2"/><path d="M9.2 20h5.6M12 16.6V20"/></svg>';

function createCard(name){
const el=document.createElement('div');
el.className='ucard';
el.innerHTML='<div class="shot" onclick="focusUser(\''+name+'\')" title="'+t('shotTip')+'">'+
'<img alt="">'+
'<div class="ph">'+MON_SVG+'<div class="pht"></div></div>'+
'<div class="veil"></div>'+
'<span class="badge"></span>'+
'<span class="stamp"></span></div>'+
'<div class="cbody"><div class="cname"><span class="nm"></span>'+
'<span class="tag adm" style="display:none">'+t('admin')+'</span></div>'+
'<div class="acts"></div></div>'+
'<div class="delover"><div class="deltxt"></div><div class="delbar"><i></i></div></div>';
const c={name:name,el:el,shot:el.querySelector('.shot'),img:el.querySelector('img'),
ph:el.querySelector('.ph'),pht:el.querySelector('.pht'),veil:el.querySelector('.veil'),
badge:el.querySelector('.badge'),stamp:el.querySelector('.stamp'),
acts:el.querySelector('.acts'),adm:el.querySelector('.adm'),nm:el.querySelector('.nm'),user:null,
delover:el.querySelector('.delover'),deltxt:el.querySelector('.deltxt'),
delbar:el.querySelector('.delbar'),delbarI:el.querySelector('.delbar i')};
cards[name]=c;
return c;
}

function updateCard(c,u){
c.user=u;
c.nm.textContent=u.name;
c.adm.textContent=t('admin');
c.adm.title=t('tagAdmTitle');
c.adm.style.display=u.admin?'':'none';
c.shot.title=t('shotTip');
const bg=u.online&&u.window==='hidden';
const noWin=u.window==='none';
const off=!u.connected;
const hasPic=!!c.img.dataset.url;
/* 画面留着：断开/隐藏只在上面压一层灰，绝不把图撤掉——断开前最后那一帧就是这个卡片的样子 */
const st=u.pending?t('pending'):(bg?t('bg'):(u.online?t('online'):(u.connected?t('disconnected'):t('offline'))));
const stTitle=bg?t('tagBgTitle'):(u.online?t('tagActiveTitle'):(u.connected?t('tagDiscTitle'):(u.pending?t('tagPendTitle'):t('tagOffTitle'))));
c.badge.textContent=st;
c.badge.title=stTitle;
c.badge.style.color=u.pending?'#f0c27b':(u.online?'#8ce8c4':(u.connected?'#f2d19b':'#cfd5e6'));
const phTxt=u.pending?t('pending'):(bg?t('shotHidden'):(noWin?(u.connected?t('shotOff'):t('shotNone')):(u.connected?t('shotWait'):t('shotOff'))));
c.pht.textContent=phTxt;
/* 没画面：占位符（图标+文字）；有画面但断开/隐藏：最后一帧压灰 + veil 提示 */
c.veil.textContent=(hasPic&&(bg||off))?(bg?t('shotHidden'):t('shotOff')):'';
let cls='shot';
if(bg||noWin||off)cls+=' is-dim';
if(hasPic&&(bg||off))cls+=' veil-on';
c.shot.className=cls;
if(hasPic){c.ph.style.display='none';c.img.classList.add('ready')}
else{c.ph.style.display='flex'}
const q='\''+u.name+'\'';
let inner='';
if(u.pending){
inner='<button class="danger" onclick="del('+q+')">'+t('delPending')+'</button>';
}else{
inner+=bg?'<button class="ghost" onclick="showWin('+q+')">'+t('show')+'</button>'
:(u.online?'<button class="ghost" onclick="hideWin('+q+')">'+t('hide')+'</button>'
:'<button class="ghost" onclick="login('+q+')">'+t('login')+'</button>');
inner+='<button class="ghost" onclick="reset('+q+')">'+t('reset')+'</button>';
if(!u.admin)inner+='<button class="ghost" onclick="grant('+q+')">'+t('grant')+'</button>';
inner+='<button class="danger" onclick="del('+q+')">'+t('del')+'</button>';
}
c.acts.innerHTML=inner;
}

async function refresh(){
if(document.visibilityState!=='visible')return;
let users;
try{users=await (await fetch('/api/users')).json()}catch(e){return}
if(!Array.isArray(users))return;
const grid=document.getElementById('grid');
let tip=document.getElementById('emptyTip');
if(!users.length){
for(const n in cards){cards[n].el.remove();delete cards[n]}
if(!tip){tip=document.createElement('div');tip.id='emptyTip';tip.className='empty';grid.appendChild(tip)}
tip.textContent=t('empty');
return;
}
if(tip)tip.remove();
const seen={};
for(const u of users){
seen[u.name]=1;
const c=cards[u.name]||createCard(u.name);
updateCard(c,u);
grid.appendChild(c.el);
}
for(const n of Object.keys(cards)){if(!seen[n]){cards[n].el.remove();delete cards[n]}}
const nc=document.getElementById('newCard');
if(nc)grid.appendChild(nc);
}

async function loadThumb(c){
if(!c||!c.user)return;
try{
const r=await fetch('/api/thumb?name='+encodeURIComponent(c.name)+'&t='+Date.now(),{cache:'no-store'});
const kind=r.headers.get('X-Thumb')||'';
if(r.status===200){
const blob=await r.blob();
const url=URL.createObjectURL(blob);
const prev=c.img.dataset.url;
c.img.src=url;
c.img.dataset.url=url;
c.img.dataset.stale=(kind==='ok')?'':'1';   // 断开/隐藏时给的是最后一帧，别再反复要
c.img.classList.add('ready');
c.ph.style.display='none';
c.shot.classList.remove('veil-on');
c.stamp.textContent=r.headers.get('X-Thumb-Time')||new Date().toLocaleTimeString();
c.stamp.style.display='';
if(prev)URL.revokeObjectURL(prev);
}else if(!c.img.dataset.url){
c.stamp.style.display='none';
c.pht.textContent=(kind==='hidden')?t('shotHidden'):t('shotNone');
}
}catch(e){}
}

let shotTimer=null;
async function shotTick(){
clearTimeout(shotTimer);
if(document.visibilityState==='visible'){
const jobs=[];
/* 断开的账号也要拉一次：服务端会把断开前最后一帧发过来，卡片上不会空一块 */
for(const n in cards){const c=cards[n];
if(!c.user||c.user.pending)continue;
if(c.img.dataset.stale&&!c.user.online)continue;
jobs.push(loadThumb(c))}
await Promise.all(jobs);
}
shotTimer=setTimeout(shotTick,SHOT_MS);
}

async function focusUser(name){
const r=await api('focus',{name:name});
if(r&&r.ok){msg('')}else{msg(t('focusFail')+((r&&r.error)||''),'err')}
refresh();
}

async function register(){
const btn=document.getElementById('btnNew'),btn2=document.getElementById('btnCreate');
btn.disabled=true;btn2.disabled=true;
msg(t('creating'));
try{
const r=await api('register',{admin:document.getElementById('chkAdmin').checked});
if(r.ok)msg(t(r.user.admin?'createdAdmin':'createdNormal',{name:r.user.name}),'ok');
else msg(r.error,'err');
}catch(e){msg(t('requestFail')+e,'err')}
btn.disabled=false;btn2.disabled=false;
refresh();
}
async function hideWin(name){
msg(t('hiding'));
const r=await api('hide',{name:name});
msg(r.ok?t('hideOk'):t('hideFail')+r.error,r.ok?'ok':'err');
refresh();
}
async function showWin(name){
const r=await api('show',{name:name});
msg(r.ok?t('showOk'):t('showFail')+r.error,r.ok?'ok':'err');
refresh();
}
async function login(name){
msg(t('opening'));
const r=await api('login',{name:name});
msg(r.ok?t('loginOk'):t('loginFail')+r.error,r.ok?'ok':'err');
setTimeout(refresh,1500);
}
async function reset(name){
if(!confirm(t('resetConfirm',{name:name})))return;
msg(t('resetting'));
const r=await api('reset',{name:name});
msg(r.ok?t('resetOk'):t('resetFail')+r.error,r.ok?'ok':'err');
refresh();
}
async function grant(name){
if(!confirm(t('grantConfirm',{name:name})))return;
msg(t('granting'));
const r=await api('grant',{name:name});
msg(r.ok?t('grantOk',{name:name}):t('grantFail')+r.error,r.ok?'ok':'err');
refresh();
}
/* 删除耗时记忆：第一次没数据→无限等待式进度条；之后按上次耗时推算进度。
   每个账号各记一份（文件多的账号删得慢），另存一份全局平均兜底 */
const DEL_KEY='multirdp_del_ms';
function delStats(){try{return JSON.parse(localStorage.getItem(DEL_KEY))||{}}catch(e){return{}}}
function delEstimate(name){
const s=delStats();
if(s.per&&s.per[name])return s.per[name];
return s.avg||0;
}
function delRemember(name,ms){
const s=delStats();
s.per=s.per||{};
s.per[name]=s.per[name]?Math.round(s.per[name]*.4+ms*.6):Math.round(ms);
s.avg=s.avg?Math.round(s.avg*.5+ms*.5):Math.round(ms);
try{localStorage.setItem(DEL_KEY,JSON.stringify(s))}catch(e){}
}
let delAnim=null;
function delStart(c,est){
c.el.classList.add('deleting');
c.deltxt.textContent=t('deleting',{name:c.name});
c.delbar.classList.toggle('indet',!(est>0));
c.delbarI.style.width=est>0?'2%':'32%';
if(est>0){
const t0=performance.now();
delAnim=setInterval(function(){
const el=performance.now()-t0;
if(el>est*1.35){   // 估算明显不准：降级成无限等待式
c.delbar.classList.add('indet');c.delbarI.style.width='32%';
clearInterval(delAnim);delAnim=null;return}
c.delbarI.style.width=(Math.min(el/est,0.94)*100).toFixed(1)+'%';
},120);
}
}
function delStop(c){
c.el.classList.remove('deleting');
if(delAnim){clearInterval(delAnim);delAnim=null}
c.delbarI.style.width='0';
}
async function del(name){
if(!confirm(t('delConfirm',{name:name})))return;
const c=cards[name]||null;
if(c)delStart(c,delEstimate(name));
const t0=performance.now();
msg(t('deleting',{name:name}));
const r=await api('delete',{name:name});
delRemember(name,Math.max(300,performance.now()-t0));
if(c)delStop(c);
msg(r.ok?(t('deleted',{name:name})+(r.error?'（'+r.error+'）':'')):t('delFail')+r.error,r.ok?(r.error?'err':'ok'):'err');
refresh();
}
async function fixRdp(){
if(!confirm(t('fixConfirm')))return;
msg(t('fixing'));
const r=await api('rdp_repair',{});
if(r.ok){msg(t('fixOk'),'ok')}
else msg(r.error,'err');
checkEnv();
}
async function checkEnv(){
try{applyStatus(await (await fetch('/api/status')).json())}catch(e){}
}
async function toggleLang(){
LANG=(LANG==='zh'?'en':'zh');
applyI18n();
checkEnv();          // 版本行/横幅跟着换语言
await refresh();     // 卡片上的徽标、按钮、tooltip 重建
try{await api('config',{lang:LANG})}catch(e){}
}
(async function init(){
try{
const s=await (await fetch('/api/status')).json();
if(s&&I18N[s.lang])LANG=s.lang;
applyI18n();
applyStatus(s);
}catch(e){applyI18n()}
await refresh();
shotTick();
setInterval(refresh,USERS_MS);
updPoll();
document.addEventListener('visibilitychange',function(){
if(document.visibilityState==='visible'){refresh();shotTick();updPoll()}
else{clearTimeout(shotTimer)}
});
})();
</script></body></html>"""

def _build_stamp():
    """这一份程序是什么时候打出来的。

    单文件 exe 的 mtime 就是打包时间；开发态退回源码文件的时间。界面上显示
    它，是为了让「改了没生效 / 是不是没更新」这类问题一眼能答——不用再翻
    exe 的时间戳去比对。
    """
    try:
        p = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        return time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(p)))
    except Exception:
        return ''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._get()
        except Exception as e:
            self._fail(e)

    def _get(self):
        if self.path in ('/', '/index.html'):
            body = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith('/api/thumb'):
            self._thumb()
        elif self.path == '/api/users':
            self._json(list_users())
        elif self.path == '/api/status':
            modes = _available_resolutions()
            self._json({'admin': _is_admin(), 'rdp_hijacked': rdp_hijacked(),
                        'admin_default': admin_default(), 'lang': _lang(),
                        'resolution': resolution(),
                        'modes': modes, 'common': _common_resolutions(modes),
                        'build': _build_stamp(),
                        'version': updater.fmt(updater.current_version()),
                        'dev_build': updater.is_dev(),
                        'auto_update': auto_update()})
        elif self.path == '/api/update':
            s = updater.snapshot()
            s['auto'] = auto_update()
            self._json(s)
        else:
            self.send_error(404)

    def _thumb(self):
        """一张缩略图。status 200=有画面，204=没画面（X-Thumb 说明原因）。

        X-Thumb 取值：ok 刚抓的一帧 / hidden 窗口被藏起来或最小化 /
        stale 会话断了，给的是断开前最后一帧 / none 从来没有过画面。
        断开也照样给图，卡片上就不会突然空一块。
        """
        q = urllib.parse.urlparse(self.path).query
        name = (urllib.parse.parse_qs(q).get('name') or [''])[0]
        data, state, ts = None, 'none', 0.0
        if name:
            try:
                hwnd = _owned_hwnd(name)
                data, state, ts = thumb.render(name, hwnd)
            except Exception as e:
                _log('缩略图失败 %s: %s' % (name, e))
        self.send_response(200 if data else 204)
        self.send_header('X-Thumb', state)
        if ts:
            self.send_header('X-Thumb-Time', time.strftime('%H:%M:%S', time.localtime(ts)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        if data:
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_POST(self):
        # 任何未预期异常都必须回一个 JSON，否则浏览器只能看到 "Failed to fetch"
        try:
            self._post()
        except Exception as e:
            self._fail(e)

    def _fail(self, e):
        try:
            self._json({'ok': False,
                        'error': _t('服务端异常：', 'Server error: ') + str(e)}, 500)
        except Exception:
            pass

    def _post(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._json({'ok': False, 'error': _t('请求格式错误', 'Bad request')}, 400)
        p = self.path
        if p == '/api/register':
            user, err = register_user(body.get('name'), body.get('admin'))
            self._json({'ok': not err, 'user': user, 'error': err})
        elif p == '/api/config':
            # 同时接受 admin_default（新用户默认管理员）、lang（界面语言）与
            # resolution（远程桌面分辨率）
            if 'admin_default' in body:
                set_admin_default(body.get('admin_default'))
            if 'lang' in body:
                set_lang(body.get('lang'))
            if 'resolution' in body:
                set_resolution(body.get('resolution'))
            if 'auto_update' in body:
                set_auto_update(body.get('auto_update'))
            self._json({'ok': True, 'admin_default': admin_default(), 'lang': _lang(),
                        'resolution': resolution(), 'auto_update': auto_update()})
        elif p == '/api/update':
            act = body.get('action')
            if act == 'check':
                updater.check_async()
            elif act == 'download':
                updater.download_async()
            elif act == 'apply':
                ok, err = updater.apply()
                s = updater.snapshot()
                s['auto'] = auto_update()
                self._json({'ok': ok, 'error': err, 'state': s})
                if ok:
                    # 给响应一点时间发出去，再退出（批处理在等本进程结束）
                    threading.Timer(1.0, exit_for_update).start()
                return
            elif act == 'auto':
                set_auto_update(bool(body.get('on')))
            s = updater.snapshot()
            s['auto'] = auto_update()
            self._json({'ok': True, 'state': s})
        elif p == '/api/login':
            ok, err = login_user(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/grant':
            ok, err = grant_admin(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/reset':
            ok, err = reset_session(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/hide':
            ok, err = hide_client(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/show':
            ok, err = show_client(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/focus':
            # 点卡片上的画面：藏着的先请回来，已经在桌面上的就拉到前台
            name = body.get('name', '')
            hwnd = _owned_hwnd(name)
            if not hwnd:
                self._json({'ok': False,
                            'error': _t('没有找到该用户的远程窗口',
                                        'No remote window found for this user')})
            elif not _user32.IsWindowVisible(hwnd):
                self._json({'ok': show_client(name)[0], 'error': None})
            else:
                _activate_window(hwnd)
                self._json({'ok': True, 'error': None})
        elif p == '/api/disconnect':
            ok, err = disconnect_session(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/rdp_repair':
            ok, err = rdp_repair()
            self._json({'ok': ok, 'error': err})
        elif p == '/api/delete':
            ok, err = delete_user(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        else:
            self.send_error(404)

def _try_elevate():
    """没有管理员权限时自动弹 UAC 重启自己（成功则本进程退出）。"""
    try:
        import ctypes
        if getattr(sys, 'frozen', False):
            # 打包成 exe 后：直接以管理员重启 exe 本身
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', sys.executable, None, EXE_DIR, 1)
        else:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', sys.executable,
                '"%s"' % os.path.abspath(__file__), HERE, 1)
        return rc > 32
    except Exception:
        return False

def start_server(port=PORT):
    """创建 HTTP server 对象（不阻塞、不弹浏览器、不提权），供托盘程序后台运行。"""
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    # 上次升级留下的半截文件 / _update.bat / .old，顺手清掉
    try:
        updater.cleanup()
    except Exception:
        pass
    threading.Thread(target=_update_watch, daemon=True).start()
    return srv


def _update_watch():
    """启动后查一次更新，之后每小时问一次（updater 内部按 6 小时节流）。

    放后台线程里，GitHub 慢或不通都不会拖住界面。
    """
    time.sleep(6)
    while True:
        try:
            if auto_update():
                updater.maybe_check()
        except Exception as e:
            _log('检查更新失败：%s' % e)
        time.sleep(3600)

def main():
    admin = _is_admin()
    if not admin:
        print('Not administrator - requesting elevation (UAC)...')
        if _try_elevate():
            sys.exit(0)
        print('!! Elevation was declined: create/delete will fail.')
        print('   Close this window, right-click the launcher -> Run as administrator.')
    try:
        server = start_server()
    except OSError as e:
        print(f'!! Cannot listen on port {PORT}: {e}')
        print('   Maybe another instance is already running. Close it and retry.')
        input('Press Enter to exit...')
        sys.exit(1)
    print(f'User manager running at http://127.0.0.1:{PORT}   (Ctrl+C to stop)')
    print(f'Administrator: {admin}')
    threading.Timer(1.0, lambda: webbrowser.open(f'http://127.0.0.1:{PORT}')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('stopped.')

if __name__ == '__main__':
    main()
