# -*- coding: utf-8 -*-
"""抓取某个远程桌面窗口的画面，压成缩略图（JPEG）。

用途：Web UI 的用户卡片上显示「这一台现在长什么样」，几秒刷一帧就够，
不追求实时。

实现要点：
  * PrintWindow(PW_RENDERFULLCONTENT) 抓窗口 —— 窗口被别人挡住也能拿到内容；
    失败再退回从窗口 DC 直接 BitBlt。
  * 只截客户区（去掉标题栏和边框），缩略图里是干净的远程桌面。
  * 全程加锁：GDI 句柄和 DC 不是线程安全的，HTTP 又是多线程的。
"""
import ctypes
import ctypes.wintypes as wt
import io
import threading
import time

from PIL import Image

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
_user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
_user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.POINT)]
_user32.GetWindowDC.argtypes = [ctypes.c_void_p]
_user32.GetWindowDC.restype = ctypes.c_void_p
_user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
_user32.IsWindow.argtypes = [ctypes.c_void_p]
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.IsIconic.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                          wt.DWORD]
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
_gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wt.UINT]


class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
                ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [('bmiHeader', _BMIH), ('bmiColors', wt.DWORD * 3)]


_lock = threading.Lock()
_cache = {}                 # key -> (ts, jpeg_bytes, state)
_cache_lock = threading.Lock()
_gate = threading.Semaphore(2)      # 同时最多两个 GDI 抓屏，避免一屏 30 张卡把 CPU 打满


def _grab(hwnd):
    """抓窗口客户区，返回 PIL.Image（RGB）或 None。"""
    wr, cr = wt.RECT(), wt.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return None
    if not _user32.GetClientRect(hwnd, ctypes.byref(cr)):
        return None
    cw, ch = cr.right - cr.left, cr.bottom - cr.top
    if cw <= 0 or ch <= 0:
        return None
    if cw < 64 or ch < 64:              # 最小化后客户区会缩成 0/很小
        return None

    origin = wt.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(origin))
    ox, oy = origin.x - wr.left, origin.y - wr.top   # 客户区在窗口内的偏移

    ww, wh = wr.right - wr.left, wr.bottom - wr.top
    src_dc = _user32.GetWindowDC(hwnd)
    if not src_dc:
        return None
    mem_dc = _gdi32.CreateCompatibleDC(src_dc)
    bmp = _gdi32.CreateCompatibleBitmap(src_dc, ww, wh)
    if not mem_dc or not bmp:
        _user32.ReleaseDC(hwnd, src_dc)
        return None
    old = _gdi32.SelectObject(mem_dc, bmp)
    try:
        ok = _user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not ok:
            _gdi32.BitBlt(mem_dc, 0, 0, ww, wh, src_dc, 0, 0, SRCCOPY)

        bi = _BMI()
        bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bi.bmiHeader.biWidth = ww
        bi.bmiHeader.biHeight = -wh          # 负数 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        size = ww * wh * 4
        buf = ctypes.create_string_buffer(size)
        if not _gdi32.GetDIBits(mem_dc, bmp, 0, wh, buf, ctypes.byref(bi),
                                DIB_RGB_COLORS):
            return None
        img = Image.frombuffer('RGB', (ww, wh), buf.raw, 'raw', 'BGRX', 0, 1)
        # 裁掉标题栏和边框，只留远程桌面
        box = (ox, oy, min(ox + cw, ww), min(oy + ch, wh))
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            return None
        return img.crop(box)
    finally:
        _gdi32.SelectObject(mem_dc, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(hwnd, src_dc)


def _trim_letterbox(img, tol=16, max_frac=0.34):
    """削掉 mstsc 画在客户区里的信箱黑边。

    窗口被拉得比远程分辨率大的时候，mstsc 不会放大画面，而是把它居中、四周填黑。
    抓客户区就会连这圈黑边一起抓下来，卡片上看着就是「中间一小块画面，剩下都是
    黑的」——明明窗口是铺满的。

    按行/列亮度从四边往里削；最多削 max_frac，而且削完必须还剩一半以上的面积，
    否则宁可不动（黑壁纸、锁屏、刚连上还没画出来的黑屏都会满足「整行全黑」）。
    """
    w, h = img.size
    px = img.load()
    sx = max(1, w // 48)
    sy = max(1, h // 48)

    def row_black(y):
        return all(max(px[x, y][:3]) <= tol for x in range(0, w, sx))

    def col_black(x):
        return all(max(px[x, y][:3]) <= tol for y in range(0, h, sy))

    top = 0
    while top < h * max_frac and row_black(top):
        top += 1
    bot = h
    while h - bot < h * max_frac and row_black(bot - 1):
        bot -= 1
    left = 0
    while left < w * max_frac and col_black(left):
        left += 1
    right = w
    while w - right < w * max_frac and col_black(right - 1):
        right -= 1

    if (left, top, right, bot) == (0, 0, w, h):
        return img
    if right - left < w * 0.5 or bot - top < h * 0.5:
        return img
    return img.crop((left, top, right, bot))


def _looks_blank(img):
    """全黑/几乎无内容：拿不到真实画面（窗口刚创建、最小化、渲染还没起来）。"""
    small = img.convert('L').resize((32, 20))
    px = list(small.getdata())
    lo, hi = min(px), max(px)
    return hi <= 12 or (hi - lo) <= 3


def render(name, hwnd, max_w=520, quality=68, ttl=3.0):
    """返回 (jpeg_bytes, state)。

    state: 'ok' 有画面 / 'hidden' 窗口被藏起来了 / 'none' 抓不到
    """
    if not hwnd or not _user32.IsWindow(hwnd):
        return None, 'none'
    visible = bool(_user32.IsWindowVisible(hwnd))
    minimised = bool(_user32.IsIconic(hwnd))

    key = (name.lower(), int(hwnd))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], hit[2]

    if minimised:
        return None, 'hidden'
    if not visible:
        # 藏起来的窗口 Windows 不再给它画，抓出来只会是黑的
        return None, 'hidden'

    with _gate:
        with _lock:
            img = _grab(hwnd)
    if img is None:
        return None, 'none'
    if _looks_blank(img):
        return None, 'none'

    img = _trim_letterbox(img)

    w, h = img.size
    if w > max_w:
        img = img.resize((max_w, max(1, round(h * max_w / w))), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=quality, optimize=False, progressive=False)
    data = buf.getvalue()
    with _cache_lock:
        _cache[key] = (now, data, 'ok')
        if len(_cache) > 64:                 # 别让缓存无限长
            for k in list(_cache)[:-48]:
                _cache.pop(k, None)
    return data, 'ok'


def drop(name):
    """用户被删/窗口换了：把它的缓存清掉。"""
    low = name.lower()
    with _cache_lock:
        for k in [k for k in _cache if k[0] == low]:
            _cache.pop(k, None)
