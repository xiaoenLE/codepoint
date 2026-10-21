"""窗口几何：居中与"夹回可视区"。

刻意做成**纯函数**（只吃数字，不碰任何 API）：窗口跑到屏幕外这种事，
一旦发生用户就彻底找不回程序，而判断逻辑必须能被单测钉死。
平台相关的"工作区是多少"由 platform_win/screen.py 负责，这里只做算术。
"""

from __future__ import annotations

from typing import Tuple

#: 窗口至少要露这么多像素，否则用户会以为它没出来
MIN_VISIBLE = 120

#: 窗口本身的最小尺寸（比这还小就没法用了）
MIN_WIDTH = 200
MIN_HEIGHT = 160

Area = Tuple[int, int, int, int]        # (x, y, width, height)
Rect = Tuple[int, int, int, int]        # (x, y, width, height)


def clamp(area: Area, x: int, y: int, width: int, height: int) -> Rect:
    """把窗口矩形夹进工作区，返回 ``(x, y, width, height)``。

    三条规则，缺一个都会出事：
    1. **比工作区还大的窗口先缩小** —— 不缩的话"夹取"会把左上角顶到负无穷，
       窗口反而彻底飞出去（这是"拖不出来"最常见的成因）；
    2. 左上角不许越过工作区右下边界；
    3. 万一工作区参数异常、算出来一个像素都看不见，强行拉回
       ``MIN_VISIBLE``，保证用户抓得到。
    """
    ax, ay, aw, ah = (int(v) for v in area)
    aw = max(MIN_WIDTH, aw)
    ah = max(MIN_HEIGHT, ah)

    width = max(MIN_WIDTH, min(int(width), aw))
    height = max(MIN_HEIGHT, min(int(height), ah))

    x = min(max(int(x), ax), ax + aw - width)
    y = min(max(int(y), ay), ay + ah - height)

    if x + width <= ax + MIN_VISIBLE:
        x = ax + min(MIN_VISIBLE, max(0, aw - width))
    if y + height <= ay + MIN_VISIBLE:
        y = ay + min(MIN_VISIBLE, max(0, ah - height))
    return x, y, width, height


def centered(area: Area, width: int, height: int) -> Rect:
    """在工作区里居中放一个窗口（略微偏上，视觉重心更舒服）。"""
    ax, ay, aw, ah = (int(v) for v in area)
    width = max(MIN_WIDTH, min(int(width), aw))
    height = max(MIN_HEIGHT, min(int(height), ah))
    x = ax + (aw - width) // 2
    y = ay + max(0, (ah - height) // 3)
    return clamp(area, x, y, width, height)


def is_visible_enough(area: Area, x: int, y: int, width: int, height: int,
                      min_visible: int = MIN_VISIBLE) -> bool:
    """窗口在工作区里露出的部分够不够"抓得住"。

    只判断"露多少"，不判断"有没有被别的窗口盖住"（那是 z 序的事，
    交给 raise/lift，别在这里混）。
    """
    ax, ay, aw, ah = (int(v) for v in area)
    ox = max(0, min(x + width, ax + aw) - max(x, ax))
    oy = max(0, min(y + height, ay + ah) - max(y, ay))
    return ox >= min_visible and oy >= min_visible


__all__ = ["clamp", "centered", "is_visible_enough", "MIN_VISIBLE"]
