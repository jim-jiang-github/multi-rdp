# -*- coding: utf-8 -*-
"""简易用户管理 Web UI：注册 / 登录(RDP) / 删除。仅监听 127.0.0.1。"""
import json, os, random, re, shutil, string, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8123
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, 'users.json')
CONFIG_FILE = os.path.join(HERE, 'config.json')
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

def admin_default():
    """新建用户默认是否授予管理员权限（界面上的勾选项，持久化到 config.json）。"""
    return bool(_load_cfg().get('admin_default', True))

def set_admin_default(on):
    cfg = _load_cfg()
    cfg['admin_default'] = bool(on)
    _save_cfg(cfg)
    return cfg['admin_default']

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
        return None, '需要管理员权限：请关掉本窗口，右键 start.bat 选「以管理员身份运行」'
    with _lock:
        db = _load()
        existing = _all_local_users()
        name = (name or '').strip()
        if not name:
            name = rand_name(existing | set(db.keys()))
        if not NAME_RE.match(name):
            return None, '用户名只能用字母开头，含字母/数字/_/-，最长20位'
        if name.lower() in RESERVED or name.lower() in existing:
            return None, f'用户名 {name} 已存在或不可用'
        password = rand_password()
        rc, out = _run(['net', 'user', name, password, '/add',
                        '/passwordchg:no', '/expires:never', '/active:yes'])
        if rc != 0:
            tail = out.strip().splitlines()
            return None, '创建失败: ' + (tail[-1] if tail else '未知错误')
        rc, out = _run(['net', 'localgroup', RDP_GROUP, name, '/add'])
        if rc != 0:
            _run(['net', 'user', name, '/delete'])
            tail = out.strip().splitlines()
            return None, '加入远程用户组失败: ' + (tail[-1] if tail else '未知错误')
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
        return False, '需要管理员权限：请关掉本窗口，右键 start.bat 选「以管理员身份运行」'
    with _lock:
        db = _load()
        info = db.get(name.lower())
        real = info['name'] if info else (name or '').strip()
        if not real:
            return False, '用户不存在'
        if real.lower() in _group_members(ADMIN_GROUP):
            return True, None
        rc, out = _run(['net', 'localgroup', ADMIN_GROUP, real, '/add'])
        if rc != 0:
            if real.lower() in _group_members(ADMIN_GROUP):
                return True, None
            tail = [l for l in out.strip().splitlines() if l.strip()]
            return False, '加入管理员组失败: ' + (tail[-1] if tail else '未知错误')
        return True, None

def _freerdp_exe():
    """定位客户端：优先 sdl3-freerdp（画面渲染正常），再 wfreerdp。"""
    dirs = (os.path.join(HERE, 'FreeRDP'),
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
        return False, '用户不存在'
    prev = _clients.get(key)
    if prev is not None and prev.poll() is None:
        return True, None          # 窗口已经开着，不重复弹
    if not info.get('password'):
        return False, '该用户没有保存密码，无法一键登录（可删除后重新注册）'
    pwd = info['password']
    exe = _freerdp_exe()
    if exe:
        # mstsc 连本机回环会立刻断开会话（reason code 17）；旧的 wfreerdp 画面全黑，
        # 只有 sdl3-freerdp 能正常渲染，所以按优先级选客户端。
        args = [exe, '/v:127.0.0.1', '/u:' + info['name'],
                '/d:' + os.environ.get('COMPUTERNAME', '.'),
                '/p:' + pwd, '/cert:ignore',
                '/w:1280', '/h:800', '/bpp:32', '/network:lan',
                '/audio-mode:redirect', '/log-level:ERROR']
        try:
            logf = open(os.path.join(HERE, 'client.log'), 'ab')
            proc = subprocess.Popen(
                args, cwd=os.path.dirname(exe), stdout=logf, stderr=logf,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            return False, '启动远程窗口失败: ' + str(e)
        time.sleep(3.0)
        if proc.poll() is not None:
            return False, '远程窗口启动后立即退出，详见 client.log'
        _clients[key] = proc
        return True, None
    # 兜底：没有 wfreerdp 就用 mstsc
    _run(['cmdkey', '/generic:TERMSRV/127.0.0.2', '/user:' + info['name'],
          '/pass:' + pwd])
    try:
        subprocess.Popen(['mstsc', '/v:127.0.0.2', '/w:1280', '/h:800'],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        return False, '启动远程窗口失败: ' + str(e)
    return True, None

def reset_session(name):
    """把该用户的会话彻底注销（画面卡死/黑屏时用），下次登录会建全新会话。"""
    if not _is_admin():
        return False, '需要管理员权限：请关掉本窗口，右键 start.bat 选「以管理员身份运行」'
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
        return False, '注销会话失败: ' + (out.strip() or '未知错误')
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
        left.append('账号仍然存在')
    if _user_dir_exists(name):
        left.append('用户目录 C:\\Users\\%s 没能删掉' % name)
    return left

def delete_user(name):
    if not _is_admin():
        return False, '需要管理员权限：请关掉本窗口，右键 start.bat 选「以管理员身份运行」'
    with _lock:
        db = _load()
        info = db.get(name.lower())
        real = info['name'] if info else name
        if real.lower() in RESERVED and real.lower() != 'afk':
            return False, '系统用户不可删除'
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
                return False, '删除失败: ' + (out.strip().splitlines()[-1] if out.strip() else '未知错误')
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
            return False, '、'.join(left) + '。该用户已保留在列表中，可再点「删除」重试清理'
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
        return False, '需要管理员权限：请关掉本窗口，右键 start.bat 选「以管理员身份运行」'
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RDP_TCP_KEY,
                           0, winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError as e:
        return False, '打开注册表失败: ' + str(e)
    try:
        try:
            cur, _ = winreg.QueryValueEx(k, 'LoadableProtocol_Object')
        except OSError:
            return False, '注册表里没有 LoadableProtocol_Object，环境异常，不处理'
        if str(cur).lower() != _OSMS_PROVIDER_CLSID.lower():
            return False, 'RDP 图形管线当前没有被劫持，无需修复'
        # 还原为 OSMS 安装时备份的原始协议对象，找不到就用微软原版 CLSID
        try:
            inner, _ = winreg.QueryValueEx(k, 'OsmsInnerProtocol_Object')
            inner = str(inner)
        except OSError:
            inner = _MS_RDP_CORE_CLSID
        # 先备份，便于回滚
        try:
            with open(os.path.join(HERE, 'rdp_repair_backup.json'), 'w', encoding='utf-8') as f:
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

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>用户管理</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7fb;--card:#fff;--line:#e5e7ef;--txt:#1c1e28;--sub:#7a7f99;--brand:#534ab7;--ok:#0f6e56;--bad:#a32d2d}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;display:flex;justify-content:center;padding:40px 16px}
.wrap{width:100%;max-width:640px}
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
</style></head><body>
<div class="wrap">
<h1>用户管理</h1>
<div class="sub">点一下自动创建一个独立账号，密码由程序随机生成并保存</div>
<div id="banner"></div>
<div class="card">
<div class="row">
<button class="primary wide" id="btnNew" onclick="register()">+ 创建用户（自动生成账号密码）</button>
<button class="danger" id="btnFix" style="display:none" onclick="fixRdp()">修复画面</button>
</div>
<label class="chk" title="勾选后新账号加入管理员组：启动需要管理员的程序（如游戏启动器）时 UAC 只需点「是」，不用输管理员密码">
<input type="checkbox" id="chkAdmin" onchange="saveCfg()"><span>新用户授予管理员权限（启动需要管理员的程序时不用输密码）</span></label>
<div id="list"></div>
<div id="msg"></div>
</div>
</div>
<script>
async function api(path,body){
const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
return await r.json();
}
function msg(t,cls){const m=document.getElementById('msg');m.textContent=t||'';m.className=cls||''}
async function checkEnv(){
try{
const s=await (await fetch('/api/status')).json();
if(!s.admin){const b=document.getElementById('banner');b.style.display='block';
b.textContent='⚠ 当前没有管理员权限，创建/删除会失败。请关掉本窗口，右键 start.bat 选「以管理员身份运行」。'}
const fix=document.getElementById('btnFix');
if(s.rdp_hijacked){fix.style.display='inline-block'}else{fix.style.display='none'}
document.getElementById('chkAdmin').checked = !!s.admin_default;
}catch(e){}
}
async function saveCfg(){
const on=document.getElementById('chkAdmin').checked;
try{
await api('config',{admin_default:on});
msg(on?'已保存：新创建的用户会自动授予管理员权限':'已保存：新创建的用户为普通用户（启动需要管理员的程序时要输管理员密码）',on?'ok':'');
}catch(e){msg('保存设置失败：'+e,'err')}
}
async function refresh(){
const users=await (await fetch('/api/users')).json();
const el=document.getElementById('list');
if(!users.length){el.innerHTML='<div class="empty">还没有用户，点上面按钮创建一个</div>';return}
el.innerHTML='<table><tr><th>用户</th><th>状态</th><th style="text-align:right">操作</th></tr>'+
users.map(u=>{
if(u.pending){
return `<tr><td>${u.name}</td><td><span class="tag disc" title="账号或文件没删干净，点右侧按钮继续清理">残留</span></td>
<td><div class="acts"><button class="danger" onclick="del('${u.name}')">删除残留</button></div></td></tr>`;
}
return `<tr><td>${u.name}${u.admin?' <span class="tag adm" title="已在管理员组：UAC 弹窗只需点一下「是」，不用输密码">管理员</span>':''}</td><td><span class="tag ${u.online?'on':(u.connected?'disc':'off')}">${u.online?'在线':(u.connected?'已断开':'离线')}</span></td>
<td><div class="acts"><button class="ghost" onclick="login('${u.name}')">登录</button>
<button class="ghost" onclick="reset('${u.name}')">重置</button>
${u.admin?'':`<button class="ghost" onclick="grant('${u.name}')">授予管理员</button>`}
<button class="danger" onclick="del('${u.name}')">删除用户</button></div></td></tr>`;
}).join('')+'</table>';
}
async function register(){
const btn=document.getElementById('btnNew');btn.disabled=true;
msg('正在创建…');
try{
const r=await api('register',{admin:document.getElementById('chkAdmin').checked});
if(r.ok)msg('已创建用户 '+r.user.name+(r.user.admin?'（管理员权限：启动需要管理员的程序只需点「是」）':'（普通用户：启动需要管理员的程序时要输密码）')+'，点「登录」即可进入','ok');
else msg(r.error,'err');
}catch(e){msg('请求失败：'+e,'err')}
btn.disabled=false;
refresh();
}
async function login(name){
msg('正在打开远程窗口…');
const r=await api('login',{name:name});
msg(r.ok?'已发起登录，请留意弹出的远程窗口':'登录失败：'+r.error,r.ok?'ok':'err');
setTimeout(refresh,1500);
}
async function reset(name){
if(!confirm('重置会把 '+name+' 的会话注销（里面的程序会关闭），确认？'))return;
msg('正在重置会话…');
const r=await api('reset',{name:name});
msg(r.ok?'会话已重置，现在点「登录」会进入全新桌面':'重置失败：'+r.error,r.ok?'ok':'err');
refresh();
}
async function grant(name){
if(!confirm('把 '+name+' 加入管理员组？\\n\\n之后它启动需要管理员权限的程序（比如游戏启动器）时，UAC 弹窗只需点一下「是」，不用再输管理员密码。\\n\\n注意：已经登录的会话要注销重登才生效（先点「重置」再点「登录」）。'))return;
msg('正在授予管理员权限…');
const r=await api('grant',{name:name});
msg(r.ok?(name+' 已加入管理员组，点「重置」再「登录」后生效'):('授予失败：'+r.error),r.ok?'ok':'err');
refresh();
}
async function del(name){
if(!confirm('确定删除用户 '+name+' ？账号和文件都会清理掉。'))return;
msg('正在删除 '+name+' …');
const r=await api('delete',{name:name});
msg(r.ok?('已删除 '+name+(r.error?'（'+r.error+'）':'')):'删除失败：'+r.error,r.ok?(r.error?'err':'ok'):'err');
refresh();
}
async function fixRdp(){
if(!confirm('检测到 OpenStreamMS 接管了 RDP 图形管线（登录后白屏的原因）。\\n\\n修复会把 RDP 协议还原为 Windows 原版，影响：\\n· 修复后 OpenStreamMS 的串流功能将不可用（可随时用它的安装脚本恢复）\\n· 修复完成后请点「登录」验证画面；如果仍白屏，重启一次电脑即可生效\\n\\n确认修复？'))return;
msg('正在还原 RDP 协议…');
const r=await api('rdp_repair',{});
if(r.ok){msg('已还原。现在点「登录」试试画面；如果还是白屏，重启电脑后即可生效','ok')}
else msg(r.error,'err');
checkEnv();
}
checkEnv();refresh();setInterval(refresh,8000);
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
            self._json({'admin': _is_admin(), 'rdp_hijacked': rdp_hijacked(),
                        'admin_default': admin_default()})
        else:
            self.send_error(404)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._json({'ok': False, 'error': '请求格式错误'}, 400)
        p = self.path
        if p == '/api/register':
            user, err = register_user(body.get('name'), body.get('admin'))
        elif p == '/api/config':
            self._json({'ok': True, 'admin_default': set_admin_default(body.get('admin_default'))})
            self._json({'ok': not err, 'user': user, 'error': err})
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
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', sys.executable,
            '"%s"' % os.path.abspath(__file__), HERE, 1)
        return rc > 32
    except Exception:
        return False

if __name__ == '__main__':
    admin = _is_admin()
    if not admin:
        print('Not administrator - requesting elevation (UAC)...')
        if _try_elevate():
            sys.exit(0)
        print('!! Elevation was declined: create/delete will fail.')
        print('   Close this window, right-click start.bat -> Run as administrator.')
    try:
        server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
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
