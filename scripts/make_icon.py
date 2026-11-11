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
             bezel_ratio=0.14, stand=True):
    """画一个显示器字形（屏幕 + 底座），返回 RGBA，边长 size。

    stand=False 时不画底座：小尺寸（32px 以下）底座只剩 1px，画了反而糊。
    """
    w = h = size
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    body_h = int(h * 0.80) if stand else h - 1   # 屏幕本体高度，底部留给底座
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

    if not stand:
        return img

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


def _grid(size, cols, rows, mon_frac, gap_x_frac, gap_y_frac, stand=True,
          glow=False):
    """画 cols x rows 的显示器矩阵，输出 size x size 的 RGBA。

    mon_frac 是单个显示器宽度占画布的比例；各行的最后一台高亮成青绿色
    （代表当前活跃的那个会话）。
    """
    S = size * SS
    img = _vgrad((S, S), BG_TOP, BG_BOT).convert('RGBA')

    if glow:                        # 顶部柔光（只有大尺寸才需要，小图标会被糊掉）
        g = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([-S * 0.25, -S * 0.55, S * 1.25, S * 0.45],
                                  fill=(90, 150, 220, 46))
        img = Image.alpha_composite(img, g.filter(ImageFilter.GaussianBlur(S * 0.06)))

    mon_w = int(S * mon_frac)
    gap_x = int(S * gap_x_frac)
    gap_y = int(S * gap_y_frac)
    grid_w = cols * mon_w + (cols - 1) * gap_x
    x0 = (S - grid_w) // 2
    cell_h = mon_w
    grid_h = rows * cell_h + (rows - 1) * gap_y
    y0 = (S - grid_h) // 2

    for row in range(rows):
        for col in range(cols):
            active = (row == 0 and col == cols - 1)
            bezel = ACTIVE if active else BEZEL
            hi = ACTIVE_HI if active else BEZEL_HI
            mon = _monitor(mon_w, bezel, hi, SCREEN, SCREEN_HI, stand=stand)
            x = x0 + col * (mon_w + gap_x)
            y = y0 + row * (cell_h + gap_y)
            if active and glow:
                # 外发光
                halo = Image.new('RGBA', (S, S), (0, 0, 0, 0))
                halo.paste(mon, (x, y), mon)
                halo = halo.filter(ImageFilter.GaussianBlur(S * 0.022))
                halo = Image.eval(halo, lambda v: min(255, int(v * 1.25)))
                img = Image.alpha_composite(img, halo)
            img.alpha_composite(mon, (x, y))

    # 圆角裁切
    mask = _rounded_mask((S, S), int(S * 0.225))
    out = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def build_master():
    """主图：3x2 六显示器，右上那台高亮。48px 以上用这张。"""
    return _grid(MASTER, 3, 2, 0.272, 0.052, 0.058, stand=True, glow=True)


def build_quad(size):
    """32px：3x2 缩到 32px 会糊成六个点，改成 2x2、去掉底座。"""
    return _grid(size, 2, 2, 0.35, 0.09, 0.10, stand=False)


def build_small(size):
    """16/24px：只画一块屏，右上角点一颗绿点表示「活跃会话」。

    尺寸这么小时，元素越少越清楚——三行六屏在这个尺度上没有意义。
    """
    S = size * SS
    img = _vgrad((S, S), BG_TOP, BG_BOT).convert('RGBA')
    mask = _rounded_mask((S, S), int(S * 0.235))
    out = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    img = out

    d = ImageDraw.Draw(img)
    b = int(S * 0.10)                                 # 边框粗细
    r = int(S * 0.13)
    body = (S * 0.13, S * 0.20, S * 0.87, S * 0.62)
    d.rounded_rectangle(body, radius=r, fill=BEZEL + (255,))
    d.rounded_rectangle([body[0] + b, body[1] + b, body[2] - b, body[3] - b],
                        radius=int(r * 0.55), fill=SCREEN + (255,))
    # 底座
    d.rectangle([S * 0.46, body[3] - 1, S * 0.54, S * 0.72], fill=BEZEL + (255,))
    d.rounded_rectangle([S * 0.32, S * 0.70, S * 0.68, S * 0.78],
                        radius=int(S * 0.022), fill=BEZEL + (255,))
    # 右上角活跃指示灯（往里挪一点，别被圆角切掉）
    d.rounded_rectangle([S * 0.645, S * 0.135, S * 0.79, S * 0.275],
                        radius=int(S * 0.022), fill=ACTIVE + (255,))
    return img.resize((size, size), Image.LANCZOS)


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

    # 每个尺寸单独画：16/24 单屏、32 2x2、48 以上 3x2。
    # 关键点：小尺寸必须用 BMP(DIB) 编码——PNG 编码的 ICO 条目在资源管理器里
    # 渲染不出来，exe 会显示成系统的通用图标。
    ico = os.path.join(OUT, 'multirdp.ico')
    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = []
    for s in sizes:
        if s <= 24:
            frames.append(build_small(s))
        elif s <= 32:
            frames.append(build_quad(s))
        else:
            frames.append(master.resize((s, s), Image.LANCZOS))
    frames[0].save(ico, format='ICO', sizes=[(s, s) for s in sizes],
                   append_images=frames[1:], bitmap_format='bmp')
    print('wrote', ico, 'sizes', sizes)

    tray = build_tray(64)
    tp = os.path.join(OUT, 'tray.png')
    tray.save(tp)
    print('wrote', tp, tray.size)


if __name__ == '__main__':
    main()
