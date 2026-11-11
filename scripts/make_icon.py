# -*- coding: utf-8 -*-
"""生成 MultiRDP 的应用图标（.ico / .png）与托盘图标。

设计：深蓝渐变圆角方块 + 3x2 共六个显示器，右上角一个为青绿高亮（代表当前
活跃会话）。全部为矢量式绘制（超采样后降采样），因此 16px 仍然干净、没有
AI 水印。

用法：
    python scripts/make_icon.py
输出：
    assets/multirdp.ico     多尺寸图标，供 PyInstaller / 资源管理器使用
    assets/multirdp.png     1024px 主图
    assets/tray.png         托盘专用（单屏简化版，16px 可读）
"""

import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'assets')

MASTER = 1024      # 主图边长
SS = 4             # 超采样倍数

# 调色板
BG_TOP = (30, 45, 71)
BG_BOT = (11, 20, 35)
BEZEL = (79, 192, 232)
BEZEL_HI = (140, 216, 245)
SCREEN = (24, 40, 60)
SCREEN_HI = (34, 56, 82)
ACTIVE = (61, 230, 168)
ACTIVE_HI = (150, 246, 210)


def _vgrad(size, top, bot):
    """竖向渐变。"""
    w, h = size
    img = Image.new('RGB', (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
    return img.resize((w, h), Image.BILINEAR)


def _rounded_mask(size, radius):
    m = Image.new('L', size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m


def _monitor(size, bezel, hi, screen, screen_hi, radius_ratio=0.235,
             bezel_ratio=0.14):
    """画一个显示器字形（屏幕 + 底座），返回 RGBA，边长 size。"""
    w = h = size
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    body_h = int(h * 0.80)                      # 屏幕本体高度，底部留给底座
    r = int(w * radius_ratio)
    bw = max(2, int(w * bezel_ratio))           # 边框粗细
    d.rounded_rectangle([0, 0, w - 1, body_h], radius=r, fill=bezel + (255,))

    # 边框内高光（左上略亮）
    d.rounded_rectangle([0, 0, w - 1, body_h], radius=r, outline=hi + (110,),
                        width=max(1, bw // 5))

    # 内屏：浅浅的竖向渐变，避免看着像个空心环
    ir = max(1, int(r * 0.60))
    inner_box = (bw, bw, w - 1 - bw, body_h - bw)
    iw, ih = inner_box[2] - inner_box[0] + 1, inner_box[3] - inner_box[1] + 1
    grad = _vgrad((iw, ih), screen_hi, screen).convert('RGBA')
    gm = Image.new('L', (iw, ih), 0)
    ImageDraw.Draw(gm).rounded_rectangle([0, 0, iw - 1, ih - 1], radius=ir,
                                         fill=255)
    img.paste(grad, (inner_box[0], inner_box[1]), gm)

    # 底座：短颈 + 底板（比之前更宽更实）
    neck_w = int(w * 0.13)
    neck_h = int(h * 0.05)
    cx = w // 2
    d.rectangle([cx - neck_w // 2, body_h - 1, cx + neck_w // 2,
                 body_h + neck_h], fill=bezel + (255,))
    base_w = int(w * 0.42)
    base_h = max(4, int(h * 0.062))
    base_r = max(2, base_h // 2)
    d.rounded_rectangle([cx - base_w // 2, body_h + neck_h - 1,
                         cx + base_w // 2, body_h + neck_h + base_h],
                        radius=base_r, fill=bezel + (255,))
    return img


def build_master():
    S = MASTER * SS
    # 背景
    img = _vgrad((S, S), BG_TOP, BG_BOT).convert('RGBA')

    # 顶部柔光
    glow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-S * 0.25, -S * 0.55, S * 1.25, S * 0.45],
                                 fill=(90, 150, 220, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.06))
    img = Image.alpha_composite(img, glow)

    # 网格几何
    cols, rows = 3, 2
    mon_w = int(S * 0.272)          # 单个显示器宽度（含底座高度约 1.1 倍）
    gap_x = int(S * 0.052)
    gap_y = int(S * 0.058)
    grid_w = cols * mon_w + (cols - 1) * gap_x
    x0 = (S - grid_w) // 2
    cell_h = int(mon_w * 1.0)       # 屏幕+底座的视觉高度
    grid_h = rows * cell_h + (rows - 1) * gap_y
    y0 = (S - grid_h) // 2

    for row in range(rows):
        for col in range(cols):
            active = (row == 0 and col == 2)
            bezel = ACTIVE if active else BEZEL
            hi = ACTIVE_HI if active else BEZEL_HI
            mon = _monitor(mon_w, bezel, hi, SCREEN, SCREEN_HI)
            x = x0 + col * (mon_w + gap_x)
            y = y0 + row * (cell_h + gap_y)
            if active:
                # 外发光
                halo = Image.new('RGBA', (S, S), (0, 0, 0, 0))
                halo.paste(mon, (x, y), mon)
                halo = halo.filter(ImageFilter.GaussianBlur(S * 0.022))
                # 提高发光亮度
                halo = Image.eval(halo, lambda v: min(255, int(v * 1.25)))
                img = Image.alpha_composite(img, halo)
            img.alpha_composite(mon, (x, y))

    # 圆角裁切
    radius = int(S * 0.225)
    mask = _rounded_mask((S, S), radius)
    out = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((MASTER, MASTER), Image.LANCZOS)


def build_tray(size=64):
    """托盘图标：单屏 + 两个叠层小窗，16px 也能辨认，配色与主图一致。"""
    S = size * SS
    img = _vgrad((S, S), BG_TOP, BG_BOT).convert('RGBA')
    mask = _rounded_mask((S, S), int(S * 0.24))
    out = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    img = out

    d = ImageDraw.Draw(img)
    # 主屏
    b = int(S * 0.09)
    r = int(S * 0.13)
    d.rounded_rectangle([S * 0.16, S * 0.22, S * 0.84, S * 0.62], radius=r,
                        fill=BEZEL + (255,))
    d.rounded_rectangle([S * 0.16 + b, S * 0.22 + b, S * 0.84 - b, S * 0.62 - b],
                        radius=int(r * 0.6), fill=SCREEN + (255,))
    # 底座
    d.rectangle([S * 0.46, S * 0.62, S * 0.54, S * 0.72], fill=BEZEL + (255,))
    d.rounded_rectangle([S * 0.33, S * 0.70, S * 0.67, S * 0.78],
                        radius=int(S * 0.02), fill=BEZEL + (255,))
    # 右上叠层小窗（活跃）
    d.rounded_rectangle([S * 0.52, S * 0.13, S * 0.90, S * 0.40],
                        radius=int(S * 0.05), fill=ACTIVE + (255,))
    d.rounded_rectangle([S * 0.52 + int(S * 0.05), S * 0.13 + int(S * 0.05),
                         S * 0.90 - int(S * 0.05), S * 0.40 - int(S * 0.05)],
                        radius=int(S * 0.03), fill=SCREEN + (255,))
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    master = build_master()
    png = os.path.join(OUT, 'multirdp.png')
    master.save(png)
    print('wrote', png, master.size)

    ico = os.path.join(OUT, 'multirdp.ico')
    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(ico, format='ICO', sizes=[(s, s) for s in sizes])
    print('wrote', ico, 'sizes', sizes)

    tray = build_tray(64)
    tp = os.path.join(OUT, 'tray.png')
    tray.save(tp)
    print('wrote', tp, tray.size)


if __name__ == '__main__':
    main()
