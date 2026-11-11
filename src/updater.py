# -*- coding: utf-8 -*-
"""自动升级：查 GitHub Release → 下载 exe → 校验 SHA256 → 替换自身并重启。

版本号只有一个来源：git tag（形如 v1.2.3）。打包时 scripts/make_version.py 把 tag
写进 exe 的版本资源，这里再从自己的版本资源里读回来，所以「程序认为自己是几版」
和资源管理器里看到的文件版本永远一致，不会出现两套数字打架。

几点取舍：
- 只认 `vMAJOR.MINOR.PATCH[-alpha|beta|rc.N]`。tag 名不对，构建阶段就报错，
  不会产出一个版本号解析不出来的发布包。
- 下载走 API 的 asset 接口（Accept: application/octet-stream）。它 302 到
  release-assets.githubusercontent.com，而 releases/download 那个地址在部分网络
  （比如挂了代理的环境）会被拦；API 域名通畅的概率更高。失败再退回浏览器地址。
- 校验 SHA256：拿同一次发布的 SHA256SUMS.txt 比对，对不上就删掉不装。程序没有
  代码签名，这是唯一能确认「下下来的就是 CI 打出来的那个文件」的手段。
- 替换用批处理：Windows 不允许覆盖正在运行的 exe，所以写一个 .bat，等本进程退出
  后把旧 exe 改名成 .old、新 exe 挪进去、再把自己拉起来。批处理里的路径用命令行
  参数传进去，不写死在文件里——中文用户名不会因为 cmd 的代码页变成乱码。
"""

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

REPO = 'jim-jiang-github/multi-rdp'
API = 'https://api.github.com'
UA = 'MultiRDP-updater'

# 单次读写超时（不是总时长）。下载十几 MB 时总时长不好预估，卡死在这里就等于
# 永远下不完，所以只限制「多久没有数据」。
TIMEOUT = 25
CHUNK = 128 * 1024
CHECK_INTERVAL = 6 * 3600          # 自动检查的间隔

# v1.2.3 / v1.2.3-rc.1 / v1.2.3-beta2 都认；v1.2、1.2.3.4、feature-xxx 不认
TAG_RE = re.compile(r'^v(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)[.\-]?(\d+))?$', re.I)

_lock = threading.Lock()
_busy = None                       # 'check' / 'download' 正在跑时挡住重复请求
_ver_cache = None

_state = {
    'phase': 'idle',               # idle/dev/checking/uptodate/found/downloading/ready/applying/error
    'error': '',
    'current': '',                 # 本机版本，如 0.1.0
    'version': '',                 # 远端版本，如 0.2.0
    'tag': '',
    'notes': '',
    'page': '',
    'published': '',
    'pct': 0,
    'got': 0,
    'total': 0,
    'checked_at': 0,
    'path': '',                    # 已下载好的 exe
    'asset_id': 0,
    'sums_id': 0,
    'download_url': '',
}


# ---------------------------------------------------------------- 版本号

def parse_tag(tag):
    """tag → (major, minor, patch, rank)。

    rank 用来让预发布排在同号正式版前面：1.2.3-rc.1 小于 1.2.3。
    不是合法 tag 返回 None。
    """
    m = TAG_RE.match((tag or '').strip())
    if not m:
        return None
    v = tuple(int(m.group(i)) for i in (1, 2, 3))
    return v + ((0,) if m.group(4) else (1,))


def fmt(v):
    if not v:
        return ''
    return '.'.join(str(x) for x in v[:3])


def exe_version(path):
    """读 exe 的版本资源，返回 (a, b, c, d)；读不到返回 None。"""
    try:
        from ctypes import wintypes

        class _VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [('dwSignature', wintypes.DWORD),
                        ('dwStrucVersion', wintypes.DWORD),
                        ('dwFileVersionMS', wintypes.DWORD),
                        ('dwFileVersionLS', wintypes.DWORD),
                        ('dwProductVersionMS', wintypes.DWORD),
                        ('dwProductVersionLS', wintypes.DWORD),
                        ('dwFileFlagsMask', wintypes.DWORD),
                        ('dwFileFlags', wintypes.DWORD),
                        ('dwFileOS', wintypes.DWORD),
                        ('dwFileType', wintypes.DWORD),
                        ('dwFileSubtype', wintypes.DWORD),
                        ('dwFileDateMS', wintypes.DWORD),
                        ('dwFileDateLS', wintypes.DWORD)]

        ver = ctypes.WinDLL('version')
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL

        dummy = wintypes.DWORD()
        size = ver.GetFileVersionInfoSizeW(path, ctypes.byref(dummy))
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        n = wintypes.UINT()
        if not ver.VerQueryValueW(buf, '\\', ctypes.byref(ptr), ctypes.byref(n)):
            return None
        if not ptr.value:
            return None
        fi = ctypes.cast(ptr, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        ms, ls = fi.dwFileVersionMS, fi.dwFileVersionLS
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


def current_version():
    """本程序的版本号（四段元组）。开发态或没打版本资源时返回 None。"""
    global _ver_cache
    if _ver_cache is None:
        _ver_cache = exe_version(sys.executable) if getattr(sys, 'frozen', False) else None
    return _ver_cache


def is_dev():
    """没版本号的构建（本地 build.bat / 直接跑源码）：不参与自动升级。"""
    v = current_version()
    return not v or v[:3] == (0, 0, 0)


# ---------------------------------------------------------------- 状态

def snapshot():
    with _lock:
        s = dict(_state)
    s['current'] = fmt(current_version())
    s['dev'] = is_dev()
    return s


def _set(**kw):
    with _lock:
        _state.update(kw)


def _base_dir():
    """和数据目录同一套规则：打包态 %LOCALAPPDATA%\\MultiRDP，开发态就是 src/。"""
    if getattr(sys, 'frozen', False):
        base = os.environ.get('LOCALAPPDATA') or os.path.join(
            os.path.expanduser('~'), 'AppData', 'Local')
        return os.path.join(base, 'MultiRDP')
    return os.path.dirname(os.path.abspath(__file__))


def _update_dir():
    d = os.path.join(_base_dir(), 'update')
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 网络

def _get(path, accept, timeout=TIMEOUT):
    req = urllib.request.Request(API + path)
    req.add_header('User-Agent', UA)
    req.add_header('Accept', accept)
    return urllib.request.urlopen(req, timeout=timeout)


def check():
    """查最新 Release 并更新状态。返回 snapshot。"""
    global _busy
    with _lock:
        if _busy:
            busy = True
        else:
            busy = False
            _busy = 'check'
            _state.update(phase='checking', error='')
    if busy:                       # 已经在查/在下了，直接把当前状态给出去
        return snapshot()
    try:
        with _get('/repos/%s/releases/latest' % REPO, 'application/vnd.github+json') as r:
            rel = json.loads(r.read().decode('utf-8'))
        tag = rel.get('tag_name') or ''
        remote = parse_tag(tag)
        assets = {a.get('name'): a for a in (rel.get('assets') or [])}
        exe_a = assets.get('MultiRDP.exe') or {}
        sums_a = assets.get('SHA256SUMS.txt') or {}

        cur = current_version()
        cur_full = None
        if cur and not is_dev():
            cur_full = (cur[0], cur[1], cur[2], 1)

        base = dict(current=fmt(cur), version=fmt(remote), tag=tag,
                    notes=(rel.get('body') or '')[:4000],
                    page=rel.get('html_url') or '',
                    published=(rel.get('published_at') or '')[:10],
                    asset_id=int(exe_a.get('id') or 0),
                    sums_id=int(sums_a.get('id') or 0),
                    download_url=exe_a.get('browser_download_url') or '',
                    got=0, total=0, pct=0, path='',
                    checked_at=int(time.time()))

        if is_dev():
            _set(phase='dev', **base)
        elif remote is None:
            _set(phase='error', error='发布包的 tag 格式不正确：%s' % tag, **base)
        elif cur_full and remote > cur_full:
            _set(phase='found', **base)
        else:
            _set(phase='uptodate', **base)
    except urllib.error.HTTPError as e:
        msg = 'HTTP %d' % e.code
        if e.code == 404:
            msg = '没有找到发布版本（仓库私有或还没发布过）'
        elif e.code in (403, 429):
            msg = 'GitHub 接口被限流，稍后再试'
        _set(phase='error', error=msg, checked_at=int(time.time()))
    except Exception as e:
        _set(phase='error', error=str(e), checked_at=int(time.time()))
    finally:
        with _lock:
            _busy = None
    return snapshot()


def check_async():
    threading.Thread(target=check, daemon=True).start()


def maybe_check(force=False):
    """自动检查：到点（6 小时）才真的发请求。"""
    if is_dev():
        _set(phase='dev')
        return
    with _lock:
        last = _state.get('checked_at') or 0
        busy = _busy
    if busy:
        return
    if force or time.time() - last > CHECK_INTERVAL:
        check_async()


# ---------------------------------------------------------------- 下载

def _fetch_sums(asset_id):
    """取同一次发布的 SHA256SUMS.txt，返回 {文件名: sha256}。"""
    if not asset_id:
        return {}
    try:
        with _get('/repos/%s/releases/assets/%d' % (REPO, asset_id),
                  'application/octet-stream') as r:
            text = r.read().decode('utf-8', 'replace')
    except Exception:
        return {}
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            out[parts[1].lstrip('*')] = parts[0].lower()
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def download():
    """把已发现的版本下到 update 目录，校验后状态变 ready。"""
    global _busy
    with _lock:
        if _busy:
            busy = True
        elif _state['phase'] not in ('found', 'error', 'ready'):
            busy = True            # 没有可下载的目标，别乱下
        elif not _state['asset_id'] and not _state['download_url']:
            busy = True
        else:
            busy = False
            _busy = 'download'
            _state.update(phase='downloading', error='', pct=0, got=0)
            version = _state['version']
            asset_id = _state['asset_id']
            fallback = _state['download_url']
            sums_id = _state['sums_id']
    if busy:
        return snapshot()
    if not asset_id and not fallback:
        _set(phase='error', error='发布包里没有 MultiRDP.exe')
        return snapshot()

    dest = os.path.join(_update_dir(), 'MultiRDP-%s.exe' % (version or 'new'))
    part = dest + '.part'
    try:
        try:
            _stream('/repos/%s/releases/assets/%d' % (REPO, asset_id),
                    'application/octet-stream', part)
        except Exception as first:
            if not fallback:
                raise
            # API 下不动时退回浏览器地址（换条路，不是重试同一个地址）
            _stream(fallback, None, part, absolute=True)

        sums = _fetch_sums(sums_id)
        want = sums.get('MultiRDP.exe')
        if want:
            got = _sha256(part)
            if got != want:
                raise ValueError('SHA256 校验不通过（期望 %s，实际 %s）' % (want[:12], got[:12]))
        os.replace(part, dest)
        _set(phase='ready', path=dest, pct=100)
    except Exception as e:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
        _set(phase='error', error='下载失败：%s' % e, pct=0)
    finally:
        with _lock:
            _busy = None
    return snapshot()


def _stream(path, accept, dest, absolute=False):
    url = path if absolute else API + path
    req = urllib.request.Request(url)
    req.add_header('User-Agent', UA)
    if accept:
        req.add_header('Accept', accept)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        total = int(r.headers.get('Content-Length') or 0)
        _set(total=total)
        got = 0
        with open(dest, 'wb') as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                pct = int(got * 100 / total) if total else 0
                _set(got=got, pct=pct)


def download_async():
    threading.Thread(target=download, daemon=True).start()


# ---------------------------------------------------------------- 安装
#
# 不用批处理：Windows 允许给正在运行的 exe 改名（只是不许删/覆盖），所以替换
# 可以就地做完——
#   1) 把当前 exe 改名成 *.old
#   2) 把新 exe 拷到原路径（原路径此刻是空的，写得进去）
#   3) 起一个新进程，带上 --after-update <本进程 pid>，它自己等我们退出再干活
# 好处是不依赖 cmd 的任何外部命令（tasklist/find/timeout 在带 Git PATH 的机器上
# 会被劫持，编码也容易出问题），也不需要单独的脚本文件。

def _wait_pid(pid, timeout=60):
    """等某个进程结束（最多 timeout 秒）。"""
    try:
        k32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        h = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not h:
            return
        try:
            k32.WaitForSingleObject(h, int(timeout * 1000))
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass


def apply():
    """把已下载的版本换上去并重启。成功后调用方必须立刻退出。

    返回 (ok, error)。
    """
    s = snapshot()
    new = s.get('path') or ''
    if s['phase'] != 'ready' or not os.path.isfile(new):
        return False, '还没有下载好新版本'
    if not getattr(sys, 'frozen', False):
        return False, '开发态不支持自动替换'

    tgt = os.path.abspath(sys.executable)
    old = tgt + '.old'
    try:
        if os.path.exists(old) and os.path.getsize(old) < 1 << 20:
            os.remove(old)          # 上次留下的空壳，删不掉也无所谓
    except OSError:
        pass

    try:
        os.replace(tgt, old)        # 运行中的 exe 可以改名
    except OSError as e:
        return False, '无法给当前程序改名（可能被占用）：%s' % e

    try:
        shutil.copy2(new, tgt)
    except OSError as e:
        try:
            os.replace(old, tgt)    # 铺不进去就退回去，别把程序弄没了
        except OSError:
            pass
        return False, '写入新版本失败：%s' % e

    _set(phase='applying')
    flags = 0x00000008              # DETACHED_PROCESS
    try:
        subprocess.Popen([tgt, '--after-update', str(os.getpid())],
                         cwd=os.path.dirname(tgt), close_fds=True,
                         creationflags=flags)
    except Exception as e:
        # 文件已经是新的了，只是没能自动拉起来；下次启动就是新版
        return True, '新版本已就位，但自动重启失败：%s' % e
    return True, ''


def cleanup():
    """启动时收尾：清掉半截下载、暂存的 exe、上次替换留下的 .old。"""
    try:
        d = _update_dir()
        for name in os.listdir(d):
            if name.endswith('.part') or name.lower().endswith('.exe'):
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
    except OSError:
        pass
    if not getattr(sys, 'frozen', False):
        return
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for name in ('_update.bat', os.path.basename(sys.executable) + '.old'):
        p = os.path.join(exe_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def handled_startup():
    """处理 --after-update：等旧进程退出（它可能还占着 8123），再删掉 .old。

    返回 True 表示这次启动是「升级后重启」，调用方继续正常启动即可。
    """
    argv = sys.argv
    if '--after-update' not in argv:
        return False
    try:
        i = argv.index('--after-update')
        pid = int(argv[i + 1])
    except (ValueError, IndexError):
        return False
    _wait_pid(pid, timeout=60)
    time.sleep(0.4)                 # 让旧进程把 socket 关干净
    try:
        old = os.path.abspath(sys.executable) + '.old'
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        pass
    return True
