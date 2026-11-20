"""通过 Windows 编辑控件消息或 UI Automation 只读获取上下文。"""

from __future__ import annotations

import ctypes as ct
import logging
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Optional

from . import focus_state
from .winapi import user32

log = logging.getLogger(__name__)
_PTR = ct.c_void_p
_INT = ct.c_int
_HRESULT = ct.c_long
_GUID = ct.c_byte * 16
_AUTOMATION_CLASS = _GUID.from_buffer_copy(uuid.UUID("e22ad333-b25f-460c-83d0-0581107395c9").bytes_le)
_AUTOMATION_IID = _GUID.from_buffer_copy(uuid.UUID("34723aff-0c9d-49d0-9896-7ab52df8cd8a").bytes_le)
_TEXT_IID = _GUID.from_buffer_copy(uuid.UUID("32eba289-3583-42c9-9c59-3b6d9a1e9b6a").bytes_le)
_TEXT2_IID = _GUID.from_buffer_copy(uuid.UUID("506a921a-fcc9-409f-b23b-37eb74106872").bytes_le)
_TEXT_PATTERN = 10014
_TEXT_PATTERN2 = 10024
_START, _END = 0, 1
_CHARACTER = 0
_EDIT_TEXT_LIMIT = 1_000_000

_send = user32.SendMessageTimeoutW
_send.argtypes = [_PTR, ct.c_uint, ct.c_size_t, ct.c_ssize_t,
                  ct.c_uint, ct.c_uint, ct.POINTER(ct.c_size_t)]
_send.restype = ct.c_ssize_t


@dataclass(frozen=True)
class EditorContext:
    selection: str = ""
    prefix: str = ""
    suffix: str = ""


def _nearby(text: str, endpoint: int, lines: int, chars: int) -> str:
    if not lines or not chars:
        return ""
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if endpoint == _START:
        return "\n".join(parts[-(lines + 1):])[-chars:]
    return "\n".join(parts[:lines + 1])[:chars]


def _edit_message(hwnd: int, message: int, wparam: int = 0, lparam: int = 0) -> Optional[int]:
    value = ct.c_size_t()
    if _send(hwnd, message, wparam, lparam, 0x0003, 200, ct.byref(value)):
        return value.value
    return None


def _edit_selection(hwnd: int) -> Optional[tuple[int, int]]:
    start, end = ct.c_uint32(), ct.c_uint32()
    if _edit_message(hwnd, 0x00B0, ct.addressof(start), ct.addressof(end)) is None:  # EM_GETSEL
        return None
    return start.value, end.value


def _read_edit(hwnd: int, before: int, after: int,
               prefix_chars: int, suffix_chars: int) -> Optional[EditorContext]:
    if user32.GetWindowLongW(hwnd, -16) & 0x20:  # GWL_STYLE / ES_PASSWORD
        return EditorContext()
    selection = _edit_selection(hwnd)
    size = _edit_message(hwnd, 0x000E)  # WM_GETTEXTLENGTH
    if selection is None or size is None or size > _EDIT_TEXT_LIMIT:
        return None
    buffer = ct.create_unicode_buffer(size + 1)
    copied = _edit_message(hwnd, 0x000D, size + 1, ct.addressof(buffer))  # WM_GETTEXT
    start, end = selection
    if copied is None or not (0 <= start <= end <= copied <= size) or _edit_selection(hwnd) != selection:
        return None
    # 标准编辑框的偏移用 UTF-16 码元计数，不能直接用来切 Python 字符串。
    text = ct.string_at(ct.addressof(buffer), copied * 2)
    if start != end:
        return EditorContext(selection=text[2 * start:2 * end].decode("utf-16-le", "replace"))
    prefix = text[:2 * start].decode("utf-16-le", "replace")
    suffix = text[2 * end:].decode("utf-16-le", "replace")
    return EditorContext(prefix=_nearby(prefix, _START, before, prefix_chars),
                         suffix=_nearby(suffix, _END, after, suffix_chars))


def _method(ptr, slot: int, *args, result=_HRESULT):
    table = ct.cast(ptr, ct.POINTER(ct.POINTER(_PTR)))[0]
    return ct.WINFUNCTYPE(result, _PTR, *args)(table[slot])


def _release(ptr) -> None:
    _method(ptr, 2, result=ct.c_ulong)(ptr)


def _object(stack: ExitStack, ptr, slot: int, *args, types=()):
    value = _PTR()
    status = _method(ptr, slot, *types, ct.POINTER(_PTR))(ptr, *args, ct.byref(value))
    if value:
        stack.callback(_release, value)
    return value if status >= 0 and value else None


def _range_text(text_range, free_string, max_length: int = -1) -> str:
    value = _PTR()
    status = _method(text_range, 12, _INT, ct.POINTER(_PTR))(
        text_range, max_length, ct.byref(value))  # GetText
    try:
        return ct.wstring_at(value) if status >= 0 and value else ""
    finally:
        if value:
            free_string(value)


def _read_side(stack: ExitStack, caret, endpoint: int, lines: int, chars: int,
               free_string) -> Optional[str]:
    if not lines or not chars:
        return ""
    text_range = _object(stack, caret, 3)  # Clone，移动副本的范围端点
    if text_range is None:
        return None
    moved = _INT()
    direction = -1 if endpoint == _START else 1
    status = _method(text_range, 14, _INT, _INT, _INT, ct.POINTER(_INT))(
        text_range, endpoint, _CHARACTER, direction * chars, ct.byref(moved))
    if status < 0:
        return None
    return _nearby(_range_text(text_range, free_string, chars * 2), endpoint, lines, chars)


def _read_element(stack: ExitStack, element, before: int, after: int,
                  prefix_chars: int, suffix_chars: int, free_string) -> Optional[EditorContext]:
    password = _INT()
    if _method(element, 35, ct.POINTER(_INT))(element, ct.byref(password)) >= 0 and password.value:
        return EditorContext()
    pattern_types = (_INT, ct.POINTER(_GUID))
    pattern2 = _object(stack, element, 14, _TEXT_PATTERN2, ct.byref(_TEXT2_IID), types=pattern_types)
    pattern = pattern2 or _object(stack, element, 14, _TEXT_PATTERN, ct.byref(_TEXT_IID),
                                  types=pattern_types)
    if pattern is None:
        return None

    ranges = _object(stack, pattern, 5)  # GetSelection
    selection = None
    if ranges is not None:
        count = _INT()
        if _method(ranges, 3, ct.POINTER(_INT))(ranges, ct.byref(count)) >= 0:
            if count.value > 1:
                return EditorContext()  # 多个不连续选区，不当作一段代码改写
            if count.value == 1:
                selection = _object(stack, ranges, 4, 0, types=(_INT,))

    if selection is not None:
        order = _INT()
        status = _method(selection, 5, _INT, _PTR, _INT, ct.POINTER(_INT))(
            selection, _START, selection, _END, ct.byref(order))
        if status < 0:
            return None
        if order.value < 0:
            return EditorContext(selection=_range_text(selection, free_string))

    caret = selection
    if pattern2 is not None:
        active = _INT()
        current = _object(stack, pattern2, 10, ct.byref(active), types=(ct.POINTER(_INT),))
        if current is not None and active.value:
            caret = current
    if caret is None:
        return None
    # 这里只移动 UIA 文本范围的副本，不调用 Select 或 ScrollIntoView。
    prefix = _read_side(stack, caret, _START, before, prefix_chars, free_string)
    suffix = _read_side(stack, caret, _END, after, suffix_chars, free_string)
    if prefix is None and not suffix:
        return None
    return EditorContext(prefix=prefix or "", suffix=suffix or "")


def read(focus: focus_state.FocusState, *, before: int, after: int,
         prefix_chars: int, suffix_chars: int) -> Optional[EditorContext]:
    """用当前焦点元素取上下文。返回 None 表示控件没有可读的文本范围。"""
    if not focus.hwnd or focus.menu_open or not focus.looks_editable:
        return None
    try:
        if focus.focus_hwnd and focus.focus_class.lower() == "edit":
            result = _read_edit(focus.focus_hwnd, before, after, prefix_chars, suffix_chars)
            if result is not None:
                return result if focus_state.query() == focus else None
        ole = ct.WinDLL("ole32")
        free_string = ct.WinDLL("oleaut32").SysFreeString
        free_string.argtypes = [_PTR]
        free_string.restype = None
        with ExitStack() as stack:
            ole.CoInitializeEx.argtypes = [_PTR, ct.c_uint32]
            ole.CoInitializeEx.restype = _HRESULT
            if ole.CoInitializeEx(None, 0) < 0:  # MTA，AI 后台线程
                return None
            stack.callback(ole.CoUninitialize)
            ole.CoCreateInstance.argtypes = [ct.POINTER(_GUID), _PTR, ct.c_uint32,
                                              ct.POINTER(_GUID), ct.POINTER(_PTR)]
            ole.CoCreateInstance.restype = _HRESULT
            automation = _PTR()
            if ole.CoCreateInstance(ct.byref(_AUTOMATION_CLASS), None, 1,
                                    ct.byref(_AUTOMATION_IID), ct.byref(automation)) < 0 or not automation:
                return None
            stack.callback(_release, automation)
            _method(automation, 61, ct.c_uint32)(automation, 300)  # ConnectionTimeout
            _method(automation, 63, ct.c_uint32)(automation, 500)  # TransactionTimeout
            element = _object(stack, automation, 8)  # GetFocusedElement
            if element is None or focus_state.query() != focus:
                return None
            result = _read_element(stack, element, before, after, prefix_chars, suffix_chars, free_string)
            return result if focus_state.query() == focus else None
    except (OSError, ValueError, ct.ArgumentError):
        log.debug("无法只读获取编辑器上下文", exc_info=True)
        return None
