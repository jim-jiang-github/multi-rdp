# -*- coding: utf-8 -*-
"""简易用户管理 Web UI：注册 / 登录(RDP) / 删除。仅监听 127.0.0.1。"""
import ctypes, json, os, random, re, shutil, string, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8123
HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller 打包成单 exe 后，users.json / config.json / FreeRDP 等数据文件
# 放在 exe 同目录下（可写），而不是解包临时目录里。
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = HERE
USERS_FILE = os.path.join(BASE_DIR, 'users.json')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
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

def _rdp_geometry():
    """按配置算出 FreeRDP 的 /w /h（fullscreen 模式不需要）。"""
    res = resolution()
    if res == 'fullscreen':
        return None, None
    if res == 'auto':
        w, h = _screen_size()
        if w and h:
            return w, h
        return 1280, 800          # 探测失败时的兜底
    w, h = res.split('x')
    return int(w), int(h)

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
                                         'state': toks[i + 2].lower() if len(toks) > i + 2 else ''}
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
        result[name.lower()] = {'id': sid, 'state': state[0].lower() if state else ''}
    return result

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

def _freerdp_exe():
    """定位客户端：优先 sdl3-freerdp（画面渲染正常），再 wfreerdp。"""
    dirs = (os.path.join(BASE_DIR, 'FreeRDP'),
            os.path.join(HERE, 'FreeRDP'),
            os.path.join(HERE, '..', '..', '3rd', 'FreeRDP'),   # 仓库 3rd/ 目录
            r'C:\Program Files\OpenStreamMS\FreeRDP')
    for name in ('sdl3-freerdp.exe', 'wfreerdp.exe'):
        for d in dirs:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None

_clients = {}   # username.lower() -> Popen（已打开的远程窗口）

def login_user(name):
    db = _sync_users()
    key = name.lower()
    info = db.get(key)
    if not info:
        return False, _t('用户不存在', 'User not found')
    prev = _clients.get(key)
    if prev is not None and prev.poll() is None:
        return True, None          # 窗口已经开着，不重复弹
    if not info.get('password'):
        return False, _t('该用户没有保存密码，无法一键登录（可删除后重新注册）',
                         'This user has no saved password, one-click login is unavailable (delete and re-create it)')
    pwd = info['password']
    exe = _freerdp_exe()
    if exe:
        # mstsc 连本机回环会立刻断开会话（reason code 17）；旧的 wfreerdp 画面全黑，
        # 只有 sdl3-freerdp 能正常渲染，所以按优先级选客户端。
        w, h = _rdp_geometry()
        args = [exe, '/v:127.0.0.1', '/u:' + info['name'],
                '/d:' + os.environ.get('COMPUTERNAME', '.'),
                '/p:' + pwd, '/cert:ignore',
                '/bpp:32', '/network:lan',
                '/audio-mode:redirect', '/log-level:ERROR']
        if w and h:
            args += ['/w:%d' % w, '/h:%d' % h]
        else:
            args += ['/f']        # 全屏模式
        try:
            logf = open(os.path.join(BASE_DIR, 'client.log'), 'ab')
            proc = subprocess.Popen(
                args, cwd=os.path.dirname(exe), stdout=logf, stderr=logf,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            return False, _t('启动远程窗口失败: ', 'Failed to launch remote window: ') + str(e)
        time.sleep(3.0)
        if proc.poll() is not None:
            return False, _t('远程窗口启动后立即退出，详见 client.log',
                             'Remote window exited immediately after launch, see client.log')
        _clients[key] = proc
        return True, None
    # 兜底：没有 wfreerdp 就用 mstsc
    _run(['cmdkey', '/generic:TERMSRV/127.0.0.2', '/user:' + info['name'],
          '/pass:' + pwd])
    w, h = _rdp_geometry()
    geom = ['/w:%d' % w, '/h:%d' % h] if (w and h) else ['/f']
    try:
        subprocess.Popen(['mstsc', '/v:127.0.0.2'] + geom,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        return False, _t('启动远程窗口失败: ', 'Failed to launch remote window: ') + str(e)
    return True, None

def reset_session(name):
    """把该用户的会话彻底注销（画面卡死/黑屏时用），下次登录会建全新会话。"""
    if not _is_admin():
        return False, _t(ADMIN_ZH, ADMIN_EN)
    db = _load()
    info = db.get(name.lower())
    real = info['name'] if info else name
    prev = _clients.pop(name.lower(), None)
    if prev is not None and prev.poll() is None:
        try:
            prev.terminate()
            prev.wait(timeout=5)
        except Exception:
            pass
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
        prev = _clients.pop(name.lower(), None)
        if prev is not None and prev.poll() is None:
            try:
                prev.terminate()
                prev.wait(timeout=5)
            except Exception:
                pass
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
    out = []
    for k, v in db.items():
        st = sess.get(v['name'].lower())
        out.append({
            'name': v['name'],
            'online': bool(st) and st['state'] == 'active',
            'connected': bool(st),
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
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7fb;--card:#fff;--line:#e5e7ef;--txt:#1c1e28;--sub:#7a7f99;--brand:#534ab7;--ok:#0f6e56;--bad:#a32d2d}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;display:flex;justify-content:center;padding:40px 16px}
.wrap{width:100%;max-width:640px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
h1{font-size:22px;font-weight:600;margin-bottom:4px}
.sub{color:var(--sub);font-size:13px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;box-shadow:0 1px 2px rgba(20,20,50,.04)}
.row{display:flex;gap:10px;margin-bottom:20px;align-items:center}
button{height:40px;border:none;border-radius:9px;padding:0 18px;font-size:14px;cursor:pointer;font-weight:500}
.primary{background:var(--brand);color:#fff}
.ghost{background:#eef0f8;color:var(--txt)}
.danger{background:#fceaea;color:var(--bad)}
button:disabled{opacity:.5;cursor:default}
.wide{flex:1}
.hint{color:var(--sub);font-size:13px;line-height:1.6}
.chk{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--sub);margin:-6px 0 18px;cursor:pointer;user-select:none}
.chk input{width:16px;height:16px;accent-color:var(--brand);cursor:pointer;flex:0 0 auto}
#banner{display:none;background:#fdf3e3;border:1px solid #f2ddb8;color:#8a5a12;font-size:13px;padding:10px 14px;border-radius:10px;margin-bottom:16px;line-height:1.6}
table{width:100%;border-collapse:collapse;font-size:14px}
th{color:var(--sub);font-weight:500;text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:12px}
td{padding:12px 6px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;font-size:12px;padding:2px 10px;border-radius:99px}
.on{background:#e1f5ee;color:var(--ok)}
.off{background:#eef0f8;color:var(--sub)}
.disc{background:#fdf3e3;color:#8a5a12}
.adm{background:#eee9fb;color:#534ab7}
.acts{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}
.acts button{height:32px;padding:0 12px;font-size:13px}
#msg{margin-top:14px;font-size:13px;min-height:20px}
.ok{color:var(--ok)}.err{color:var(--bad)}
.empty{color:var(--sub);text-align:center;padding:28px 0;font-size:14px}
.langbtn{height:32px;padding:0 12px;font-size:13px;flex:0 0 auto}
</style></head><body>
<div class="wrap">
<div class="top">
<div><h1 id="ttl">用户管理</h1><div class="sub" id="sub"></div></div>
<button class="ghost langbtn" id="btnLang" onclick="toggleLang()">English</button>
</div>
<div id="banner"></div>
<div class="card">
<div class="row">
<button class="primary wide" id="btnNew" onclick="register()">+ 创建用户（自动生成账号密码）</button>
<button class="danger" id="btnFix" style="display:none" onclick="fixRdp()">修复画面</button>
</div>
<label class="chk" title="勾选后新账号加入管理员组：启动需要管理员的程序（如游戏启动器）时 UAC 只需点「是」，不用输管理员密码">
<input type="checkbox" id="chkAdmin" onchange="saveCfg()"><span id="chkAdminText">新用户授予管理员权限（启动需要管理员的程序时不用输密码）</span></label>
<div class="chk"><span id="resLabel">远程桌面分辨率</span>：
<select id="resSel" onchange="saveRes()">
<option value="auto" id="resAuto">跟随本机屏幕</option>
<option value="fullscreen" id="resFull">全屏（无窗口边框）</option>
<optgroup label="本机支持" id="grpNative"></optgroup>
<optgroup label="常用分辨率" id="grpCommon"></optgroup>
</select></div>
<div id="list"></div>
<div id="msg"></div>
</div>
</div>
<script>
const I18N={
zh:{
 title:'用户管理',
 subtitle:'点一下自动创建一个独立账号，密码由程序随机生成并保存',
 btnNew:'+ 创建用户（自动生成账号密码）',
 btnFix:'修复画面',
 chkAdmin:'新用户授予管理员权限（启动需要管理员的程序时不用输密码）',
 thUser:'用户', thStatus:'状态', thAct:'操作',
 empty:'还没有用户，点上面按钮创建一个',
 online:'在线', offline:'离线', disconnected:'已断开', pending:'残留', admin:'管理员',
 login:'登录', reset:'重置', grant:'授予管理员', del:'删除用户', delPending:'删除残留',
 tagAdmTitle:'已在管理员组：UAC 弹窗只需点一下「是」，不用输密码',
 tagPendTitle:'账号或文件没删干净，点右侧按钮继续清理',
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
 resAuto:'跟随本机屏幕',
 resFull:'全屏（无窗口边框）',
 grpNative:'本机支持',
 grpCommon:'常用分辨率',
 langBtn:'English'
},
en:{
 title:'User Management',
 subtitle:'Create an isolated local account in one click — the password is generated and saved automatically',
 btnNew:'+ Create user (auto username & password)',
 btnFix:'Fix display',
 chkAdmin:'Grant administrator rights to new users (no password prompt on UAC)',
 thUser:'User', thStatus:'Status', thAct:'Actions',
 empty:'No users yet — click the button above to create one.',
 online:'Online', offline:'Offline', disconnected:'Disconnected', pending:'Leftover', admin:'Admin',
 login:'Login', reset:'Reset', grant:'Grant admin', del:'Delete', delPending:'Clean up',
 tagAdmTitle:'Already in Administrators: UAC only needs a click, no password',
 tagPendTitle:'Account or files were not fully removed — click to continue cleaning up',
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
 resAuto:'Match this screen',
 resFull:'Fullscreen (borderless)',
 grpNative:'Supported by this display',
 grpCommon:'Common resolutions',
 langBtn:'中文'
}
};
let LANG='zh';
function t(k,v){let s=(I18N[LANG]&&I18N[LANG][k])||I18N.zh[k]||k;if(v){for(const p in v){s=s.split('{'+p+'}').join(v[p])}}return s}
function applyI18n(){
document.documentElement.lang=(LANG==='zh'?'zh-CN':'en');
document.title=t('title');
document.getElementById('ttl').textContent=t('title');
document.getElementById('sub').textContent=t('subtitle');
document.getElementById('btnNew').textContent=t('btnNew');
document.getElementById('btnFix').textContent=t('btnFix');
document.getElementById('chkAdminText').textContent=t('chkAdmin');
document.getElementById('resLabel').textContent=t('resLabel');
document.getElementById('resAuto').textContent=t('resAuto');
document.getElementById('resFull').textContent=t('resFull');
document.getElementById('grpNative').label=t('grpNative');
document.getElementById('grpCommon').label=t('grpCommon');
document.getElementById('btnLang').textContent=t('langBtn');
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
async function saveRes(){
try{await api('config',{resolution:document.getElementById('resSel').value})}catch(e){}
}
async function refresh(){
let users;
try{users=await (await fetch('/api/users')).json()}catch(e){return}
if(!Array.isArray(users))return;
const el=document.getElementById('list');
if(!users.length){el.innerHTML='<div class="empty">'+t('empty')+'</div>';return}
el.innerHTML='<table><tr><th>'+t('thUser')+'</th><th>'+t('thStatus')+'</th><th style="text-align:right">'+t('thAct')+'</th></tr>'+
users.map(u=>{
if(u.pending){
return '<tr><td>'+u.name+'</td><td><span class="tag disc" title="'+t('tagPendTitle')+'">'+t('pending')+'</span></td>'+
'<td><div class="acts"><button class="danger" onclick="del(\''+u.name+'\')">'+t('delPending')+'</button></div></td></tr>';
}
const st=u.online?t('online'):(u.connected?t('disconnected'):t('offline'));
const admTag=u.admin?' <span class="tag adm" title="'+t('tagAdmTitle')+'">'+t('admin')+'</span>':'';
const grantBtn=u.admin?'':'<button class="ghost" onclick="grant(\''+u.name+'\')">'+t('grant')+'</button>';
return '<tr><td>'+u.name+admTag+'</td><td><span class="tag '+(u.online?'on':(u.connected?'disc':'off'))+'">'+st+'</span></td>'+
'<td><div class="acts"><button class="ghost" onclick="login(\''+u.name+'\')">'+t('login')+'</button>'+
'<button class="ghost" onclick="reset(\''+u.name+'\')">'+t('reset')+'</button>'+
grantBtn+
'<button class="danger" onclick="del(\''+u.name+'\')">'+t('del')+'</button></div></td></tr>';
}).join('')+'</table>';
}
async function register(){
const btn=document.getElementById('btnNew');btn.disabled=true;
msg(t('creating'));
try{
const r=await api('register',{admin:document.getElementById('chkAdmin').checked});
if(r.ok)msg(t(r.user.admin?'createdAdmin':'createdNormal',{name:r.user.name}),'ok');
else msg(r.error,'err');
}catch(e){msg(t('requestFail')+e,'err')}
btn.disabled=false;
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
async function del(name){
if(!confirm(t('delConfirm',{name:name})))return;
msg(t('deleting',{name:name}));
const r=await api('delete',{name:name});
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
try{await api('config',{lang:LANG})}catch(e){}
await refresh();
}
(async function init(){
try{
const s=await (await fetch('/api/status')).json();
if(s&&I18N[s.lang])LANG=s.lang;
applyI18n();
applyStatus(s);
}catch(e){applyI18n()}
await refresh();
setInterval(refresh,8000);
})();
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
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
        elif self.path == '/api/users':
            self._json(list_users())
        elif self.path == '/api/status':
            modes = _available_resolutions()
            self._json({'admin': _is_admin(), 'rdp_hijacked': rdp_hijacked(),
                        'admin_default': admin_default(), 'lang': _lang(),
                        'resolution': resolution(),
                        'modes': modes, 'common': _common_resolutions(modes)})
        else:
            self.send_error(404)

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
            self._json({'ok': True, 'admin_default': admin_default(), 'lang': _lang(),
                        'resolution': resolution()})
        elif p == '/api/login':
            ok, err = login_user(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/grant':
            ok, err = grant_admin(body.get('name', ''))
            self._json({'ok': ok, 'error': err})
        elif p == '/api/reset':
            ok, err = reset_session(body.get('name', ''))
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
                None, 'runas', sys.executable, None, BASE_DIR, 1)
        else:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', sys.executable,
                '"%s"' % os.path.abspath(__file__), HERE, 1)
        return rc > 32
    except Exception:
        return False

def start_server(port=PORT):
    """创建 HTTP server 对象（不阻塞、不弹浏览器、不提权），供托盘程序后台运行。"""
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)

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
