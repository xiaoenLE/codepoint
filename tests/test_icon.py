"""图标一致性回归测试 —— 保证 build/icon.ico 和代码画出来的完全一致。

图标是代码画的（`iconmaker.render_logo`），而 `build/icon.ico` 是**提交进仓库的
二进制文件** —— 代码改了它不会自己变，两边一旦不同步，exe 上显示的图标就跟
界面里不是一回事。这类问题**在代码里完全看不出来**（一边是画出来的、一边是
仓库里的二进制），只能靠机器比对，所以有本文件。

跑得快是刻意的：只重渲染 16/24/32 三个小尺寸去比对；
大尺寸帧只检查"是不是合法 PNG、边长对不对"（重渲染 256x256 要 6 秒，放不进测试套件）。
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from codepoint import iconmaker as im

ROOT = Path(__file__).resolve().parents[1]
ICO = ROOT / "build" / "icon.ico"

# 与前两个尺寸比对就足以发现"忘了重新生成"（三种尺寸一起变）
CHECKED_SIZES = (16, 24, 32)


def parse_ico(data: bytes):
    """返回 [(边长, 字节偏移, 字节数)]。"""
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and kind == 1, "不是 ICO 文件"
    out = []
    for i in range(count):
        w, h, _c, _r, _p, _bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + i * 16:22 + i * 16])
        out.append((w or 256, offset, size))
    return out


class TestExeIcon(unittest.TestCase):
    def test_icon_file_exists(self):
        self.assertTrue(ICO.exists(), "build/icon.ico 不见了，跑 tools/make_icon.py")

    def test_sizes_include_256(self):
        """少了 256，Windows 大图标视图只能把 48 硬放大 —— 看着像假图标。"""
        sizes = [s for s, _, _ in parse_ico(ICO.read_bytes())]
        self.assertEqual(sizes, sorted(im.ICON_SIZES))
        self.assertIn(256, sizes)

    def test_small_frames_match_code(self):
        """仓库里的 ico 必须**就是当前代码画出来的样子**（这条抓住 v1.0.0 那种事故）。"""
        data = ICO.read_bytes()
        frames = {s: (off, size) for s, off, size in parse_ico(data)}
        for size in CHECKED_SIZES:
            off, length = frames[size]
            stored = data[off:off + length]
            fresh = im._ico_image(size)
            self.assertEqual(
                stored, fresh,
                f"{size}x{size} 帧与代码画出来的不一致 —— "
                f"改了 iconmaker 就要跑 tools/make_icon.py 重新生成",
            )

    def test_large_frames_are_png(self):
        data = ICO.read_bytes()
        for size, off, length in parse_ico(data):
            if size < im.ICO_PNG_FROM:
                continue
            blob = data[off:off + length]
            self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n",
                             f"{size}x{size} 应该是 PNG 帧（BMP 帧会白白撑大 exe）")
            w, h = struct.unpack(">II", blob[16:24])
            self.assertEqual((w, h), (size, size))

    def test_logo_is_rounded_tile_not_circle(self):
        """图标形状 = 圆角方块：四角透明、四边中点有色。

        （圆形画法会让**四边中点也透明**，所以这两条断言能把两种形状区分开。）
        """
        rows = im.render_logo(32)
        self.assertEqual(rows[0][0][3], 0, "左上角该是透明的（圆角）")
        self.assertEqual(rows[0][31][3], 0, "右上角该是透明的")
        self.assertGreater(rows[0][16][3], 200, "上边中点必须有内容（不是圆形）")
        self.assertGreater(rows[16][0][3], 200, "左边中点必须有内容（不是圆形）")

    def test_logo_colors_are_brand_gradient(self):
        """配色 = 上蓝(51,107,246) 下青(20,165,201)。"""
        rows = im.render_logo(32)
        top = rows[2][16]
        bottom = rows[29][16]
        self.assertLess(top[0], 90, "顶部该是品牌蓝：R 通道低")
        self.assertGreater(top[2], 200, "顶部该是品牌蓝：B 通道拉满")
        self.assertGreater(bottom[1], top[1], "底部该更青（G 通道更高）")


class TestRuntimeIcon(unittest.TestCase):
    def test_ensure_icon_uses_versioned_name(self):
        """文件名带版本号 —— 升级后托盘图标才会跟着换（否则旧的永远留着）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = im.ensure_icon(tmp, tag="9.9.9")
            self.assertIsNotNone(path)
            self.assertEqual(Path(path).name, "icon-9.9.9.ico")
            self.assertTrue(Path(path).exists())

    def test_ensure_icon_is_tile_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = im.ensure_icon(tmp, tag="1.2.3")
            frames = parse_ico(Path(path).read_bytes())
            self.assertEqual([s for s, _, _ in frames], [16, 32, 48])


class TestBrandPng(unittest.TestCase):
    def test_brand_png_matches_logo(self):
        """界面里的品牌 logo 与图标同源 —— 同一个 render_logo。"""
        self.assertEqual(im.brand_png_base64(26),
                         im.png_base64(im.render_logo(26)))

    def test_png_is_decodable(self):
        import base64
        blob = base64.b64decode(im.brand_png_base64(16))
        self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
