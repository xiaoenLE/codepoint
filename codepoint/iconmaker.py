"""纯 Python 生成界面用的图像（无需 Pillow）。

三件事：
1. 画程序图标（**蓝青渐变圆角方块 + 逗号/句点图案**），输出多尺寸 .ico 供 exe/托盘/窗口用；
2. 把任意 RGBA 像素矩阵编码成 **PNG 字节**——tkinter 8.6 的 PhotoImage 能直接吃
   base64 的 PNG，这样"把 exe 图标画到界面上"就不需要任何第三方库；
3. 从 HICON 拿到的像素（BGRA）也走同一套编码。

画法：在一个 4 倍超采样的画布上做"形状命中判断"，再降采样得到抗锯齿边缘。
图案全是几何体（圆 + 折线），所以不需要任何字体或图片资源。

⚠ **全项目只有一个 logo 画法：`render_logo()`（圆角方块版）。**
  exe 图标（build/icon.ico）、托盘/窗口图标（`ensure_icon`）、界面里的品牌 logo
  都必须调它 —— logo 只有一处定义，各处才长得一样。
  `render_rgba()` 是另一套**圆形**画法，当前没有任何地方在用。
"""

from __future__ import annotations

import base64
import math
import struct
import zlib
from pathlib import Path
from typing import List, Sequence, Tuple

Color = Tuple[int, int, int]      # RGB
Point = Tuple[float, float]

SS = 4                            # 超采样倍数
BRAND_TOP: Color = (0x33, 0x6B, 0xF6)      # 蓝
BRAND_BOTTOM: Color = (0x14, 0xA5, 0xC9)   # 青
GLYPH: Color = (0xFF, 0xFF, 0xFF)

#: 品牌 logo 的圆角半径（占边长比例）。**全项目唯一的图标形状**：
#: Windows 11 那种 App 图标的观感，比纯圆形更像"软件图标"。
LOGO_RADIUS = 0.235

#: ico 里超过这个边长就用 PNG 帧（Vista+ 支持）——
#: 256x256 的 BMP 帧要 270KB，PNG 帧只要十几 KB，而 exe 会把这些帧整个装进去。
ICO_PNG_FROM = 128


def _dist_to_segment(px: float, py: float, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dist_to_polyline(px: float, py: float, pts: Sequence[Point]) -> float:
    best = float("inf")
    for i in range(len(pts) - 1):
        d = _dist_to_segment(px, py, pts[i], pts[i + 1])
        if d < best:
            best = d
    return best


# 图标图案：一个逗号 + 一个句点 —— 直接点题"标点"。
#
# 全部用几何体拼：逗号 = 一个实心圆（头部）+ 一条粗折线（尾巴），句点 = 一个小圆。
# 这样不依赖任何字体（用文字画字形在纯 Python + 无 Pillow 的环境下没法保证效果）。
_COMMA_HEAD = (0.385, 0.330, 0.160)          # (圆心 x, 圆心 y, 半径)
_COMMA_TAIL: List[Point] = [
    (0.380, 0.445), (0.335, 0.565), (0.255, 0.665),
]
_COMMA_TAIL_W0 = 0.075                       # 尾巴根部半宽（和头部平滑衔接）
_COMMA_TAIL_W1 = 0.026                       # 尾巴尖端半宽（收细，才像逗号）
_DOT = (0.680, 0.700, 0.080)                 # 句点


def _in_circle(u: float, v: float, cx: float, cy: float, r: float) -> bool:
    dx, dy = u - cx, v - cy
    return dx * dx + dy * dy <= r * r


def _on_polyline_tapered(u: float, v: float, pts: Sequence[Point],
                         w0: float, w1: float) -> bool:
    """点在不在一条**粗细渐变**的折线上（逗号尾巴就是从粗到尖）。

    按累计弧长线性插值半宽 —— 不做渐变的话尾巴会变成一根"勺子"，
    这是纯几何绘图里最容易忽略的一步。
    """
    lengths = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(lengths)
    if total <= 1e-12:
        return False
    acc = 0.0
    for i, seg_len in enumerate(lengths):
        if seg_len <= 1e-12:
            continue
        mid = (acc + seg_len / 2.0) / total
        half = w0 + (w1 - w0) * mid
        if _dist_to_segment(u, v, pts[i], pts[i + 1]) <= half:
            return True
        acc += seg_len
    return False


def _glyph_hit(u: float, v: float) -> bool:
    """这个点是否落在图案（逗号 + 句点）上。"""
    cx, cy, r = _COMMA_HEAD
    if _in_circle(u, v, cx, cy, r):
        return True
    if _on_polyline_tapered(u, v, _COMMA_TAIL, _COMMA_TAIL_W0, _COMMA_TAIL_W1):
        return True
    dx, dy, dr = _DOT
    return _in_circle(u, v, dx, dy, dr)


def _gradient(v: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, v))
    return (int(BRAND_TOP[0] + (BRAND_BOTTOM[0] - BRAND_TOP[0]) * t),
            int(BRAND_TOP[1] + (BRAND_BOTTOM[1] - BRAND_TOP[1]) * t),
            int(BRAND_TOP[2] + (BRAND_BOTTOM[2] - BRAND_TOP[2]) * t))


def _sample(u: float, v: float) -> Tuple[int, int, int, int]:
    """**圆形**版图标：在归一化坐标 (u,v) ∈ [0,1]² 上采样，返回 RGBA。

    ⚠ 当前没有任何地方在用它画图标 —— 图标一律走 ``render_logo()``（圆角方块）。
    留着它是因为"圆形"在某些场合（水印、头像位）可能还有用。
    """
    if math.hypot(u - 0.5, v - 0.5) > 0.5:
        return 0, 0, 0, 0                      # 圆外透明（边缘靠超采样抗锯齿）
    if _glyph_hit(u, v):
        return GLYPH[0], GLYPH[1], GLYPH[2], 255
    r, g, b = _gradient(v)
    return r, g, b, 255


def render_rgba(size: int) -> List[List[Tuple[int, int, int, int]]]:
    """渲染一张 size×size 的 RGBA 位图（圆形版，已降采样）。

    ⚠ 图标/logo 请用 ``render_logo()``；这个函数当前没有调用方。"""
    big = size * SS
    # 先算大图
    big_rows: List[List[Tuple[int, int, int, int]]] = []
    for by in range(big):
        row = []
        for bx in range(big):
            u = (bx + 0.5) / big
            v = (by + 0.5) / big
            row.append(_sample(u, v))
        big_rows.append(row)

    # 盒式降采样
    out: List[List[Tuple[int, int, int, int]]] = []
    inv = 1.0 / (SS * SS)
    for y in range(size):
        row = []
        for x in range(size):
            ar = ag = ab = aa = 0
            for j in range(SS):
                src = big_rows[y * SS + j]
                for i in range(SS):
                    r, g, b, a = src[x * SS + i]
                    ar += r * a
                    ag += g * a
                    ab += b * a
                    aa += a
            if aa == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((int(ar / aa), int(ag / aa), int(ab / aa), int(aa * inv)))
        out.append(row)
    return out


def _ico_image(size: int, radius: float = LOGO_RADIUS,
               png_from: int = ICO_PNG_FROM) -> bytes:
    """单张 ICO 图像块。

    - 小尺寸走经典 BMP 块：``BITMAPINFOHEADER + BGRA 倒序像素 + AND 掩码``；
    - ``size >= png_from`` 走 **PNG 帧**（Vista 起支持）—— 256x256 的 BMP 帧要
      270KB，PNG 帧只要十几 KB，而这些字节会被原样装进 exe。
    """
    rgba = render_logo(size, radius)
    if size >= png_from:
        return png_bytes(rgba)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,                 # biSize
        size,               # biWidth
        size * 2,           # biHeight（ICO 约定：XOR + AND 两张图高度之和）
        1,                  # biPlanes
        32,                 # biBitCount
        0,                  # biCompression = BI_RGB
        size * size * 4,    # biSizeImage
        0, 0, 0, 0,
    )

    # XOR 位图：自下而上
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = rgba[y][x]
            xor += bytes((b, g, r, a))

    # AND 掩码：每行按 4 字节对齐；透明像素置 1
    row_bytes = ((size + 31) // 32) * 4
    masks = bytearray()
    for y in range(size - 1, -1, -1):
        bits = bytearray(row_bytes)
        for x in range(size):
            if rgba[y][x][3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        masks += bits

    return header + bytes(xor) + bytes(masks)


#: exe 图标默认包含的尺寸。**必须带 256**：少了它，Windows 在大图标视图
#: 和"属性→图标预览"里只能把 48x48 硬放大，看着糊得像个假图标。
ICON_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)


def build_ico(path: str | Path, sizes: Sequence[int] = (16, 32, 48), *,
              radius: float = LOGO_RADIUS, png_from: int = ICO_PNG_FROM) -> bool:
    """生成多尺寸 .ico（**圆角方块 logo**）。成功返回 True。"""
    try:
        images = [(s, _ico_image(s, radius, png_from)) for s in sorted(sizes)]
        count = len(images)
        out = bytearray(struct.pack("<HHH", 0, 1, count))
        offset = 6 + 16 * count
        entries = bytearray()
        body = bytearray()
        for size, data in images:
            entries += struct.pack(
                "<BBBBHHII",
                size if size < 256 else 0,
                size if size < 256 else 0,
                0, 0, 1, 32, len(data), offset,
            )
            body += data
            offset += len(data)
        out += entries + body
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(out))
        return True
    except Exception:      # noqa: BLE001
        return False


def ensure_icon(directory: str | Path, tag: str = "") -> str | None:
    """确保目录里有一份**与当前版本对应**的图标；返回路径或 None（供托盘/窗口加载）。

    文件名带版本号：图标内容会随代码变，而配置目录里的文件不会自己更新 ——
    带版本号 = 升级后自动换新，老文件留着不占地方也不影响运行。

    只生成 16/32/48 三个尺寸（0.3 秒左右）—— 这是**启动路径**，不能为了
    256x256 花 6 秒。大尺寸只有 exe 图标需要，那是打包时用 tools/make_icon.py 生成的。
    """
    name = f"icon-{tag}.ico" if tag else "icon.ico"
    p = Path(directory) / name
    if p.exists() and p.stat().st_size > 0:
        return str(p)
    return str(p) if build_ico(p) else None


# ------------------------------------------------------------------ PNG 编码
#
# 为什么自己写：整个工程零依赖，而 tkinter 的 PhotoImage 只认 GIF/PNG/PPM。
# PNG 是唯一能"带透明通道"的，且 Tk 8.6 支持直接给 base64 数据。
# 手写一个最小 PNG 编码器只要 30 行，换掉的是对 Pillow 的整条依赖链。


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(rows: Sequence[Sequence[Tuple[int, int, int, int]]]) -> bytes:
    """把 RGBA 像素矩阵编码成 PNG 字节。rows[y][x] = (r, g, b, a)。"""
    if not rows or not rows[0]:
        return b""
    h = len(rows)
    w = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)                       # 每行的过滤器类型：0 = None
        for px in row:
            r, g, b, a = px
            raw += bytes((r & 0xFF, g & 0xFF, b & 0xFF, a & 0xFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)   # 8bit / RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _png_chunk(b"IEND", b""))


def png_base64(rows) -> str:
    """tkinter 用：``PhotoImage(data=...)`` 接受 base64 的 PNG。"""
    data = png_bytes(rows)
    return base64.b64encode(data).decode("ascii") if data else ""


def brand_png_base64(size: int = 28) -> str:
    """界面里的品牌 logo（顶栏 / 页脚，PNG base64）。

    和 exe 图标、托盘图标同源（都走 `render_logo`），所以形状永远一致。
    """
    return png_base64(render_logo(size))


def logo_tile_png_base64(size: int = 120, radius: float = LOGO_RADIUS) -> str:
    """关于页用的大 logo（圆角方块 + 白色逗号句点）。"""
    return png_base64(render_logo(size, radius))


def render_logo(size: int, radius: float = LOGO_RADIUS):
    """**全项目唯一的 logo 渲染入口**：圆角方块 + 蓝青渐变 + 逗号句点。

    exe 图标、托盘/窗口图标、界面品牌 logo、关于页大 logo 全部调它 ——
    想换图标就改这里（以及 `tools/make_icon.py` 重新生成 ico）。
    """
    return _render_tile(size, radius)


def _render_tile(size: int, radius: float):
    """圆角方块版（App 图标那种观感）。"""
    big = size * SS
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            ar = ag = ab = aa = 0
            for j in range(SS):
                for i in range(SS):
                    u = (x * SS + i + 0.5) / big
                    v = (y * SS + j + 0.5) / big
                    r, g, b, a = _sample_tile(u, v, radius)
                    ar += r * a
                    ag += g * a
                    ab += b * a
                    aa += a
            if aa == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((int(ar / aa), int(ag / aa), int(ab / aa),
                            int(aa / (SS * SS))))
        out.append(row)
    return out


def _sample_tile(u: float, v: float, radius: float):
    """圆角方块版图标（App 图标那种观感）。"""
    if not _inside_rounded(u, v, radius):
        return 0, 0, 0, 0
    if _glyph_hit(u, v):
        return GLYPH[0], GLYPH[1], GLYPH[2], 255
    r, g, b = _gradient(v)
    return r, g, b, 255


def _inside_rounded(u: float, v: float, radius: float) -> bool:
    """点是否在圆角方块内（角上按圆弧判定）。"""
    r = max(0.0, min(0.5, radius))
    if r <= 0.0:
        return 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0
    cx = min(max(u, r), 1.0 - r)
    cy = min(max(v, r), 1.0 - r)
    return math.hypot(u - cx, v - cy) <= r + 1e-9


__all__ = [
    "LOGO_RADIUS",
    "ICON_SIZES",
    "render_logo",
    "render_rgba",
    "build_ico",
    "ensure_icon",
    "png_bytes",
    "png_base64",
    "brand_png_base64",
    "logo_tile_png_base64",
]
