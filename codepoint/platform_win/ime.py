"""输入法（IME / imm32）状态查询。

只做**只读**查询，不改输入法状态。任何一步失败都退化为 core.events.ImeSnapshot(available=False)，
绝不让调用方拿到半真半假的状态。

用途：在"组字/候选列表已打开"时不要抢标点键——否则会打断选字
（很多输入法用 , . - = 翻页/选词）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.events import UNAVAILABLE_IME, ImeSnapshot
from .winapi import (
    DWORD,
    GCS_COMPSTR,
    HWND,
    IME_CMODE_FULLSHAPE,
    IME_CMODE_KATAKANA,
    IME_CMODE_NATIVE,
    IME_CMODE_SYMBOL,
    POINT,
    byref,
    gui_thread_info,
    imm32,
    user32,
)


_UNAVAILABLE = UNAVAILABLE_IME


def query(hwnd: int) -> ImeSnapshot:
    """查询指定窗口的输入法状态。跨线程/跨进程查询失败时返回 unavailable。"""
    if not hwnd or imm32 is None:
        return _UNAVAILABLE
    himc = None
    try:
        himc = imm32.ImmGetContext(HWND(hwnd))
        if not himc:
            return _UNAVAILABLE

        is_open = bool(imm32.ImmGetOpenStatus(himc))
        conv = DWORD(0)
        sentence = DWORD(0)
        ok = imm32.ImmGetConversionStatus(himc, byref(conv), byref(sentence))
        value = conv.value if ok else 0

        # 组字串长度 > 0 表示正在拼字
        composing = False
        try:
            n = int(imm32.ImmGetCompositionStringW(himc, GCS_COMPSTR, None, 0))
            composing = n > 0
        except Exception:      # noqa: BLE001
            composing = False

        return ImeSnapshot(
            available=True,
            open=is_open,
            native=bool(value & IME_CMODE_NATIVE),
            fullshape=bool(value & IME_CMODE_FULLSHAPE),
            symbol=bool(value & IME_CMODE_SYMBOL),      # ← 中文标点
            katakana=bool(value & IME_CMODE_KATAKANA),
            composing=composing,
        )
    except Exception:      # noqa: BLE001
        return _UNAVAILABLE
    finally:
        if himc:
            try:
                imm32.ImmReleaseContext(HWND(hwnd), himc)
            except Exception:      # noqa: BLE001
                pass


def is_composing(hwnd: int) -> bool:
    """便捷判断：该窗口是否正在组字。拿不到状态时返回 False（放行）。"""
    return query(hwnd).composing


# ------------------------------------------------------------------ 插入符位置


@dataclass(frozen=True)
class CaretPoint:
    x: int = 0
    y: int = 0
    valid: bool = False


def caret_point(fallback_to_cursor: bool = True) -> CaretPoint:
    """取当前文本插入符的屏幕坐标，用于把 Tab 呼吸灯放在光标旁边。

    取不到插入符（很多现代编辑器不暴露）时，退化为鼠标位置。
    """
    try:
        tid = 0
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            tid = int(user32.GetWindowThreadProcessId(HWND(hwnd), None) or 0)
        info = gui_thread_info(tid) if tid else None
        if info is not None and info.hwndCaret:
            rc = info.rcCaret
            # rcCaret 是相对焦点窗口的客户区坐标，需要换算到屏幕
            pt = POINT(rc.left, rc.bottom)
            user32.ClientToScreen(HWND(info.hwndCaret), byref(pt))
            return CaretPoint(x=pt.x, y=pt.y, valid=True)
    except Exception:      # noqa: BLE001
        pass

    if fallback_to_cursor:
        try:
            pt = POINT()
            if user32.GetCursorPos(byref(pt)):
                return CaretPoint(x=pt.x, y=pt.y, valid=True)
        except Exception:      # noqa: BLE001
            pass
    return CaretPoint()


__all__ = ["CaretPoint", "query", "is_composing", "caret_point"]
