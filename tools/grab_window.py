"""按窗口标题截图 —— 用来肉眼检查界面。

纯 ctypes + 自研 PNG 编码器，零依赖。因为"界面好不好看"这件事只能看，
不能靠读代码判断；改完界面必须截一张下来自己看一眼。

    python tools/grab_window.py 标题关键字 输出.png [等待秒数]

做法：按标题找到窗口 → 置顶 → BitBlt 它的屏幕区域 → BGRA 转 RGBA → PNG。
（用屏幕区域而不是 PrintWindow：Tk 的窗口用 PrintWindow 常拿到全黑。）
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import POINTER, byref, c_int
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codepoint import iconmaker                                    # noqa: E402
from codepoint.platform_win.winapi import (                        # noqa: E402
    BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, DWORD, HANDLE,
    HWND, LPARAM, RECT, SW_RESTORE, c_void_p, gdi32, kernel32, user32,
)

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002     # PrintWindow 的第 3 个参数（Win8.1+）
_ENUM = ctypes.WINFUNCTYPE(c_int, HWND, LPARAM)


def find_window(keyword: str):
    """按标题子串找窗口，返回 (hwnd, title)。优先可见窗口。"""
    hits = []

    def _cb(hwnd, _lparam):
        try:
            h = int(hwnd) if hwnd else 0
            if not h or not user32.IsWindowVisible(HWND(h)):
                return 1
            n = int(user32.GetWindowTextLengthW(HWND(h)) or 0)
            if n <= 0:
                return 1
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(HWND(h), buf, len(buf))
            if keyword.lower() in buf.value.lower():
                hits.append((h, buf.value))
        except Exception:      # noqa: BLE001
            pass
        return 1

    user32.EnumWindows(_ENUM(_cb), 0)
    return hits[0] if hits else (0, "")


def _is_blank(rows) -> bool:
    """整张图几乎全黑/全白 → 认为没抓到内容（PrintWindow 对某些窗口会这样）。"""
    total = sum(len(r) for r in rows) or 1
    varied = 0
    for row in rows[::4]:
        for px in row[::4]:
            if px[0] > 24 or px[1] > 24 or px[2] > 24:
                varied += 1
    return varied / max(1, total / 16) < 0.02


def grab(hwnd: int, out: Path, mode: str = "auto") -> bool:
    """抓一个窗口。``mode``: auto / print / screen。

    · **print** 用 ``PrintWindow`` 让窗口自己渲染一份 —— 不怕被别的窗口挡住，
      这是最可靠的方式（用屏幕 BitBlt 时只要窗口在后台就会抓到别人的画面）。
    · **screen** 用 ``BitBlt`` 抓屏幕对应区域，作为 PrintWindow 返回空图时的回退。
    """
    rect = RECT()
    if not user32.GetWindowRect(HWND(hwnd), byref(rect)):
        print("取窗口位置失败")
        return False
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False

    screen = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(HANDLE(screen))
    bitmap = gdi32.CreateCompatibleBitmap(HANDLE(screen), w, h)
    gdi32.SelectObject(HANDLE(memdc), HANDLE(bitmap))

    if mode in ("auto", "print"):
        ok = user32.PrintWindow(HWND(hwnd), HANDLE(memdc), PW_RENDERFULLCONTENT)
    else:
        ok = gdi32.BitBlt(HANDLE(memdc), 0, 0, w, h, HANDLE(screen),
                          rect.left, rect.top, SRCCOPY)
    if not ok:
        print("抓图失败（PrintWindow/BitBlt）")
        return False

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h        # 上到下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf = (ctypes.c_ubyte * (w * h * 4))()
    if not gdi32.GetDIBits(HANDLE(memdc), HANDLE(bitmap), 0, h, buf,
                           byref(bmi), DIB_RGB_COLORS):
        print("GetDIBits 失败")
        return False

    raw = bytes(buf)
    rows = []
    for y in range(h):
        row = []
        base = y * w * 4
        for x in range(w):
            i = base + x * 4
            row.append((raw[i + 2], raw[i + 1], raw[i], 255))    # BGRA → RGBA
        rows.append(row)

    if mode == "auto" and _is_blank(rows):
        gdi32.DeleteObject(HANDLE(bitmap))
        gdi32.DeleteDC(HANDLE(memdc))
        user32.ReleaseDC(None, HANDLE(screen))
        print("  PrintWindow 拿到空图，回退到屏幕抓取")
        return grab(hwnd, out, mode="screen")

    gdi32.DeleteObject(HANDLE(bitmap))
    gdi32.DeleteDC(HANDLE(memdc))
    user32.ReleaseDC(None, HANDLE(screen))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(iconmaker.png_bytes(rows))
    print(f"已保存 {out}  ({w}x{h})  [mode={mode}]")
    return True


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    keyword, output = sys.argv[1], sys.argv[2]
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    hwnd, title = find_window(keyword)
    if not hwnd:
        print(f"没找到标题包含 {keyword!r} 的窗口")
        return 1
    print(f"目标窗口：{title}  (hwnd={hwnd})")
    try:
        user32.ShowWindow(HWND(hwnd), SW_RESTORE)
        user32.SetForegroundWindow(HWND(hwnd))
    except Exception:      # noqa: BLE001
        pass
    time.sleep(wait)          # 等窗口重绘完
    return 0 if grab(hwnd, Path(output)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
