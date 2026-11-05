#!/usr/bin/env python3
"""生成观测舱桌面图标（液态玻璃 · 观测核心），输出多尺寸 ICO + PNG 预览。

设计沿用观测舱自己的视觉语言（`gui-v3/index.html` 里的 data-URI favicon 配色
`#5ac8fa → #5856d6`，加上 hero 图的深色底 `#0B1220`）：

- 底：深色玻璃圆角方（超椭圆 squircle）+ 内高光描边，与观测舱默认深色外观一致
- 玻璃层：对角折射亮带 + 左上柔光，模拟 iOS 26 液态玻璃的镜面反射
- 主体：发光核心球（青→靛渐变）+ 外发光 + 高光点 = 被"观测"的记忆核心
- 轨道环：一道倾斜椭圆轨道 + 小卫星，暗示"生态 / 生命周期"运转

深底方案不是审美偏好：16px 下浅底渐变会糊成一块蓝，深底 + 高对比发光体
才能在任务栏/桌面小尺寸里一眼认出（实测两版对比过）。

为什么单独跑脚本而不是内置图标：仓库要开源，二进制图标不入库；
本机部署时跑一次即可（`python make_app_icon.py`），产出 `生态观测舱.ico` 供快捷方式引用。

用法:
  python make_app_icon.py            # 生成 生态观测舱.ico + 预览 生态观测舱-256.png
  python make_app_icon.py --out DIR  # 指定输出目录
"""
from __future__ import annotations

import argparse
import io
import math
import pathlib

from PIL import Image, ImageDraw, ImageFilter

HERE = pathlib.Path(__file__).resolve().parent

# 采样尺寸：先大画后缩，边缘才干净（16px 也不糊成一团）
SS = 1024

CYAN = (0x5A, 0xC8, 0xFA)
INDIGO = (0x58, 0x56, 0xD6)
DEEP = (0x0B, 0x12, 0x20)


def squircle_mask(size: int, inset: float, power: float = 5.0) -> Image.Image:
    """超椭圆（iOS 圆角方）蒙版：|x|^n + |y|^n = 1，比圆角矩形更"苹果"。"""
    m = Image.new("L", (size, size), 0)
    px = m.load()
    c = size / 2.0
    r = c - inset
    # 只扫描内部包围盒，边缘用 4x 子采样抗锯齿
    step = 3
    for y in range(0, size, step):
        dy = (y + step / 2 - c) / r
        if abs(dy) > 1.0:
            continue
        span = (1.0 - abs(dy) ** power) ** (1.0 / power) * r
        x0 = int(c - span)
        x1 = int(c + span)
        for x in range(max(0, x0), min(size, x1 + 1)):
            px[x, y] = 255
        # 竖直方向补齐被 step 跳过的行（简单块填充，缩放时看不出来）
        if step > 1:
            for yy in range(y, min(size, y + step)):
                for x in range(max(0, x0), min(size, x1 + 1)):
                    px[x, yy] = 255
    # 抗锯齿：轻微模糊后重新二值化，得到柔边
    return m.filter(ImageFilter.GaussianBlur(radius=size * 0.0018))


def lerp(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def glass_backdrop(size: int) -> Image.Image:
    """深色玻璃底：垂直渐变 + 左上受光 + 右下压暗 + 对角折射亮带。"""
    s = size
    g = Image.new("RGB", (s, s))
    px = g.load()
    top, bot = (0x16, 0x20, 0x38), (0x08, 0x0D, 0x18)
    for y in range(s):
        row = lerp(top, bot, (y / s) ** 0.85)
        for x in range(s):
            # 极淡的角度偏移，避免纯平的死板
            warm = 10 * (1.0 - x / s) * (1.0 - y / s)
            px[x, y] = (
                min(255, int(row[0] + warm)),
                min(255, int(row[1] + warm * 1.2)),
                min(255, int(row[2] + warm * 1.6)),
            )
    base = g.convert("RGBA")

    # 左上柔光（玻璃受光面）
    glow = radial_glow(s, s * 0.26, s * 0.16, s * 0.62, 150)
    base = Image.composite(Image.new("RGBA", (s, s), (120, 190, 255, 255)), base,
                           glow.point(lambda v: int(v * 0.30)))

    # 对角折射亮带：液态玻璃标志性的斜向镜面反光
    band = Image.new("L", (s, s), 0)
    bd = ImageDraw.Draw(band)
    bd.polygon([(0, s * 0.02), (s * 0.52, -s * 0.1), (s * 0.72, -s * 0.1), (0, s * 0.42)],
               fill=255)
    band = band.filter(ImageFilter.GaussianBlur(radius=s * 0.075))
    base = Image.composite(Image.new("RGBA", (s, s), (170, 220, 255, 255)), base,
                           band.point(lambda v: int(v * 0.16)))
    return base


def radial_glow(size: int, cx: float, cy: float, radius: float, opacity: int) -> Image.Image:
    """柔光斑：左上光源 / 内部折射亮区。"""
    layer = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=opacity)
    return layer.filter(ImageFilter.GaussianBlur(radius=radius * 0.55))


def core_orb(size: int, r_frac: float = 0.185) -> Image.Image:
    """发光核心球：径向渐变（中心亮青 → 边缘深靛）+ 左上受光。"""
    s = size
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    px = layer.load()
    c = s / 2.0
    r = s * r_frac
    for y in range(s):
        for x in range(s):
            dx, dy = (x - c) / r, (y - c) / r
            dist = math.hypot(dx, dy)
            if dist > 1.0:
                continue
            # 球体明暗：光源在左上
            lit = max(0.0, 1.0 - math.hypot(dx + 0.42, dy + 0.46) / 1.75)
            t = min(1.0, dist ** 1.5)
            col = lerp((0xB6, 0xF2, 0xFF), INDIGO, t * 0.92)
            # 加亮
            k = 0.62 + 0.72 * lit
            col = tuple(min(255, int(v * k)) for v in col)
            a = 255 if dist < 0.985 else int(255 * (1.0 - (dist - 0.985) / 0.015))
            px[x, y] = col + (max(0, a),)
    return layer


def rim_from_mask(size: int, mask: Image.Image, thickness: float) -> Image.Image:
    """由 squircle 蒙版直接派生内描边（别用 rounded_rectangle 近似——形状对不上会歪）。"""
    inner = mask.filter(ImageFilter.MinFilter(int(thickness) * 2 + 1))
    rim = Image.new("L", (size, size), 0)
    rim.paste(mask)
    rim = Image.eval(rim, lambda v: v)
    # 环 = 外蒙版 - 内缩蒙版
    import PIL.ImageChops as chops
    ring = chops.subtract(mask, inner)
    return ring.filter(ImageFilter.GaussianBlur(radius=max(1.0, size * 0.003)))


def porthole(size: int, cx: float, cy: float, r_outer: float, r_inner: float) -> Image.Image:
    """舷窗（观测舱的"舱"）：玻璃厚环 + 内侧暗窗 + 顶部受光弧 + 底部回光。

    环体不是纯灰白：用青→靛渐变填充环带，才有液态玻璃的折射色，而不是塑料圈。
    """
    s = size
    # 先画一个"大圆减内圆"的环带蒙版（4x 超采样，边缘干净）
    ss = 2
    big = Image.new("L", (s * ss, s * ss), 0)
    bd = ImageDraw.Draw(big)
    bd.ellipse([(cx - r_outer) * ss, (cy - r_outer) * ss,
                (cx + r_outer) * ss, (cy + r_outer) * ss], fill=255)
    bd.ellipse([(cx - r_inner) * ss, (cy - r_inner) * ss,
                (cx + r_inner) * ss, (cy + r_inner) * ss], fill=0)
    ring_mask = big.resize((s, s), Image.LANCZOS)

    # 环带渐变：左上青白 → 右下靛蓝
    grad = Image.new("RGB", (s, s))
    gp = grad.load()
    for y in range(s):
        for x in range(s):
            t = min(1.0, max(0.0, (x / s * 0.42 + y / s * 0.58)))
            gp[x, y] = lerp((0xE8, 0xF6, 0xFF), (0x6E, 0x7A, 0xD8), t)
    ring = grad.convert("RGBA")
    ring.putalpha(ring_mask.point(lambda v: int(v * 0.94)))

    lay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    lay = Image.alpha_composite(lay, ring)

    d = ImageDraw.Draw(lay)
    # 内侧暗窗（压在环内沿，制造厚度与"凹进去"的观感）
    d.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner],
              fill=(8, 13, 24, 255))
    # 窗内沿细环：一点冷光，避免死黑
    d.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner],
              outline=(96, 160, 220, 120), width=max(1, int(s * 0.005)))
    # 环外沿高光弧（左上受光）
    d.arc([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
          start=170, end=300, fill=(255, 255, 255, 210),
          width=max(2, int(s * 0.010)))
    # 环内沿反光弧（右下回光）
    d.arc([cx - r_inner - s * 0.004, cy - r_inner - s * 0.004,
           cx + r_inner + s * 0.004, cy + r_inner + s * 0.004],
          start=10, end=110, fill=(150, 205, 255, 105),
          width=max(1, int(s * 0.006)))
    return lay


def draw_icon(size: int = SS) -> Image.Image:
    s = size
    c = s / 2

    base = glass_backdrop(s)

    # ── 舷窗（"舱"的识别点）────────────────────────────────
    r_out = s * 0.355
    r_in = s * 0.268
    base = Image.alpha_composite(base, porthole(s, c, c, r_out, r_in))

    halo = radial_glow(s, c, c, s * 0.30, 200)
    base = Image.composite(Image.new("RGBA", (s, s), (0x5A, 0xC8, 0xFA, 255)), base,
                           halo.point(lambda v: int(v * 0.38)))

    # ── 轨道环（倾斜椭圆 + 小卫星，全部收在窗口内）─────────
    orbit = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    od = ImageDraw.Draw(orbit)
    rw = max(2, int(s * 0.010))
    rx, ry = s * 0.205, s * 0.078
    od.ellipse([c - rx, c - ry, c + rx, c + ry],
               outline=(160, 225, 255, 165), width=rw)
    # 卫星：轨道右上方的小亮点
    ang = math.radians(-40)
    sx, sy = c + rx * math.cos(ang), c + ry * math.sin(ang)
    sr = s * 0.018
    od.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(240, 252, 255, 255))
    # 卫星柔光
    sat_glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(sat_glow).ellipse(
        [sx - sr * 3, sy - sr * 3, sx + sr * 3, sy + sr * 3],
        fill=(140, 215, 255, 110))
    sat_glow = sat_glow.filter(ImageFilter.GaussianBlur(radius=s * 0.016))
    orbit = Image.alpha_composite(sat_glow, orbit)
    # 轨道绕到核心后面：先画后半段 → 核心 → 前半段
    half_back = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    half_back.paste(orbit.crop((0, 0, s, int(c))), (0, 0))
    base = Image.alpha_composite(base, half_back)

    # ── 核心球 ────────────────────────────────────────────
    base = Image.alpha_composite(base, core_orb(s, r_frac=0.155))

    # 球体高光（小而锐，越大越像贴纸）
    d = ImageDraw.Draw(base)
    r_h = s * 0.026
    hx, hy = c - s * 0.052, c - s * 0.057
    d.ellipse([hx - r_h, hy - r_h, hx + r_h, hy + r_h], fill=(255, 255, 255, 235))

    # 轨道前半段（压在核心上）
    front_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    front_layer.paste(orbit.crop((0, int(c), s, s)), (0, int(c)))
    base = Image.alpha_composite(base, front_layer)

    # ── 外缘：内高光描边（iOS 图标质感）───────────────────
    rim_mask = squircle_mask(s, inset=s * 0.035)
    ring = rim_from_mask(s, rim_mask, max(2, s * 0.006))
    edge_tint = Image.new("RGBA", (s, s), (170, 222, 255, 255))
    edge_tint.putalpha(ring.point(lambda v: int(v * 0.55)))
    base = Image.alpha_composite(base, edge_tint)

    # ── 裁成圆角方（squircle）────────────────────────────
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(base, (0, 0), rim_mask)
    return out


def draw_icon_small(size: int) -> Image.Image:
    """≤24px 专用简化帧：厚环 + 发光核，去掉轨道与细描边。

    小尺寸不是"把大图缩小"就完事——缩到 16px 时轨道环、外缘描边、球体高光
    全部糊成噪点，反而比什么都看不清。这里按小图重新设计：只留两个最强识别点
    （舷窗厚环的形状 + 中心亮核），但核仍是径向渐变（平涂会变成"靶心"图标）。
    """
    s = size
    ss = 8  # 超采样倍数
    S = s * ss
    c = S / 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 深色圆角方底（带一点纵向渐变，避免死平）
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    px = base.load()
    for y in range(S):
        col = lerp((0x15, 0x1F, 0x36), (0x08, 0x0D, 0x18), y / S)
        for x in range(S):
            px[x, y] = col + (255,)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=int(S * 0.225), fill=255)
    img.paste(base, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # 舷窗厚环：小图要留出圆角呼吸空间，环不能顶到边（顶满在 16px 会变成方块）
    r_out = S * 0.325
    r_in = S * 0.195
    d.ellipse([c - r_out, c - r_out, c + r_out, c + r_out], fill=(0xC7, 0xDE, 0xFF, 255))
    # 内窗（深色）
    d.ellipse([c - r_in, c - r_in, c + r_in, c + r_in], fill=(0x08, 0x0D, 0x1A, 255))

    # 中心光核（径向渐变：亮青 → 靛），小图也要有"在发光"的观感
    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cp = core.load()
    r_core = S * 0.150
    for y in range(S):
        dy = (y - c) / r_core
        if abs(dy) > 1.0:
            continue
        for x in range(S):
            dx = (x - c) / r_core
            dist = math.hypot(dx, dy)
            if dist > 1.0:
                continue
            t = min(1.0, dist ** 1.4)
            col = lerp((0xCF, 0xF6, 0xFF), (0x4A, 0x63, 0xD8), t)
            a = 255 if dist < 0.96 else int(255 * (1.0 - (dist - 0.96) / 0.04))
            cp[x, y] = col + (max(0, a),)
    img = Image.alpha_composite(img, core)

    out = img.resize((s, s), Image.LANCZOS)
    # 缩完补一层淡淡的边，避免在浅色背景上"飘"
    ed = ImageDraw.Draw(out)
    ed.rounded_rectangle([0, 0, s - 1, s - 1], radius=max(1, int(s * 0.225)),
                         outline=(80, 130, 190, 100), width=1)
    return out


def write_ico(path: pathlib.Path, frames: list[tuple[int, Image.Image]]) -> None:
    """手写 ICO 容器，逐帧原样写入 PNG 数据。

    为什么不用 `img.save(..., sizes=[...])`：PIL 那条路会**用它自己的重采样**从
    最大帧生成所有尺寸，我们精心准备的小尺寸简化帧会被整个丢掉——生成的 .ico
    里 16px 还是"缩小的 256px"，糊成一团（实测踩到，2026-10-30）。
    这里自己拼 ICONDIR + ICONDIRENTRY，每帧就是传入的那张图。
    """
    import struct

    blobs = []
    for n, im in frames:
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, format="PNG", optimize=True)
        blobs.append((n, buf.getvalue()))

    out = io.BytesIO()
    # ICONDIR: reserved(0) type(1=icon) count
    out.write(struct.pack("<HHH", 0, 1, len(blobs)))
    offset = 6 + 16 * len(blobs)
    for n, data in blobs:
        # 宽高 0 表示 256；其余填实际值
        w = 0 if n >= 256 else n
        h = 0 if n >= 256 else n
        out.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for _, data in blobs:
        out.write(data)
    path.write_bytes(out.getvalue())


def build_frames() -> list[tuple[int, Image.Image]]:
    """按尺寸分派：≤24 走小图简化帧，其余走精细稿缩放（32px 实测精细稿更好看）。"""
    master = draw_icon(SS)
    frames = []
    for n in (256, 128, 96, 64, 48, 40, 32, 24, 20, 16):
        if n <= 24:
            frames.append((n, draw_icon_small(n)))
        else:
            frames.append((n, master.resize((n, n), Image.LANCZOS)))
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(description="生成观测舱桌面图标（多尺寸 ICO）")
    ap.add_argument("--out", type=pathlib.Path, default=HERE)
    ap.add_argument("--name", default="生态观测舱")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    frames = build_frames()
    ico_path = a.out / f"{a.name}.ico"
    write_ico(ico_path, frames)

    preview = a.out / f"{a.name}-256.png"
    dict(frames)[256].save(preview, format="PNG")

    # 预览sheet：用真实帧（不再由大图缩），所见即所得
    head = 16
    total_w = sum(n for n, _ in frames if n <= 256) + 16 * (len(frames) + 1)
    sheet = Image.new("RGBA", (total_w, 264), (24, 28, 38, 255))
    x = 16
    for n, im in sorted(frames, key=lambda t: t[0]):
        sheet.paste(im, (x, 256 - n + 4), im)
        x += n + 16
    small = a.out / f"{a.name}-preview-sheet.png"
    sheet.save(small, format="PNG")

    print(f"✅ {ico_path}  ({ico_path.stat().st_size} bytes, {len(frames)} 个尺寸)")
    print(f"🖼 {preview}")
    print(f"🧾 {small}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
