"""窗口几何：居中 / 夹回可视区（"窗口跑到右下角、拖不出来"的回归测试）。

**背景**：主窗口曾经错误地用"呼吸灯定位函数"来定位 —— 那个函数返回的是
光标/插入符坐标，于是窗口跟着鼠标跑到屏幕角落；遇到副屏（坐标可为负）
还会被算到可视区外，用户只能结束进程。

修法分三层，各测各的：
- ``core/window_geom.py`` 纯算术（这里测）；
- ``platform_win/screen.py`` 只负责问出工作区；
- ``ui/main_window.py`` 负责在 show 之前调 ``ensure_visible``。
"""

from __future__ import annotations

import unittest

from codepoint.core import window_geom as geom

PRIMARY = (0, 0, 1920, 1040)          # 1920×1080 减掉 40px 任务栏
SECOND_LEFT = (-1920, 0, 1920, 1040)  # 副屏在主屏左边 → x 是负的
TINY = (0, 0, 800, 600)


class TestCentered(unittest.TestCase):
    def test_centered_on_primary(self):
        x, y, w, h = geom.centered(PRIMARY, 960, 680)
        self.assertEqual((w, h), (960, 680))
        self.assertEqual(x, (1920 - 960) // 2)
        self.assertGreater(y, 0)

    def test_centered_on_negative_coordinate_monitor(self):
        """副屏在左侧（x 为负）时也必须落在那块屏里 —— 这是"拖不出来"的根源。"""
        x, y, w, h = geom.centered(SECOND_LEFT, 960, 680)
        self.assertGreaterEqual(x, SECOND_LEFT[0])
        self.assertLessEqual(x + w, SECOND_LEFT[0] + SECOND_LEFT[2])
        self.assertTrue(geom.is_visible_enough(SECOND_LEFT, x, y, w, h))

    def test_window_bigger_than_area_is_shrunk(self):
        x, y, w, h = geom.centered(TINY, 1600, 1200)
        self.assertEqual((w, h), (800, 600))
        self.assertEqual((x, y), (0, 0))


class TestClamp(unittest.TestCase):
    def test_far_away_window_is_pulled_back(self):
        x, y, _w, _h = geom.clamp(PRIMARY, 5000, 4000, 960, 680)
        self.assertLessEqual(x + 960, 1920)
        self.assertLessEqual(y + 680, 1040)
        self.assertTrue(geom.is_visible_enough(PRIMARY, x, y, 960, 680))

    def test_negative_position_is_pulled_back(self):
        x, y, w, h = geom.clamp(SECOND_LEFT, -9000, -2000, 960, 680)
        self.assertGreaterEqual(x, -1920)
        self.assertTrue(geom.is_visible_enough(SECOND_LEFT, x, y, w, h))

    def test_oversized_window_never_exceeds_area(self):
        x, y, w, h = geom.clamp(PRIMARY, 100, 100, 5000, 4000)
        self.assertEqual((w, h), (1920, 1040))
        self.assertEqual((x, y), (0, 0))

    def test_broken_area_still_keeps_window_grabbable(self):
        """工作区参数异常（全 0 / 极小）时也不能把窗口算没。"""
        x, y, w, h = geom.clamp((0, 0, 0, 0), 10, 10, 960, 680)
        self.assertGreaterEqual(w, geom.MIN_WIDTH)
        self.assertGreaterEqual(h, geom.MIN_HEIGHT)


class TestVisibility(unittest.TestCase):
    def test_window_just_off_the_edge_is_not_visible_enough(self):
        # 只剩 10px 露在工作区里 —— 用户抓不住
        self.assertFalse(geom.is_visible_enough(PRIMARY, 1910, 100, 960, 680))

    def test_mostly_visible_window_is_left_alone(self):
        self.assertTrue(geom.is_visible_enough(PRIMARY, 100, 100, 960, 680))


if __name__ == "__main__":
    unittest.main()
