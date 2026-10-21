"""探测：能不能读到目标程序里的文本（AI 补全的上下文来源）。

只读、无副作用、不装钩子。纯 ctypes 调 UI Automation，零第三方依赖。

为什么要这个脚本
    "AI 补全"的第一个前提是**知道光标附近写的什么**。外部程序没法直接问
    编辑器，只能靠 UI Automation / MSAA 这类无障碍接口去"读"。
    读得到读不到，取决于目标程序有没有实现 UIA provider —— 不同程序差别极大，
    唯一的办法是实测。

用法
    python tools/probe_context.py                 # 探测所有可见顶层窗口
    python tools/probe_context.py Invtmatic       # 只测标题含该串的窗口
    python tools/probe_context.py --caret         # 额外打印光标的屏幕坐标

输出
    每个窗口一行结论：
        TEXT   ✅ 拿到了正文（会打印字符数与前 60 字）
        NOPAT  ❌ 找到了控件，但没有 TextPattern（拿不到正文）
        NOTGT  ❌ 没找到可读的 Document/Edit 控件
        ERR    调用失败（附 HRESULT）

注意
    Chromium 系（VS Code / Electron）平时不开无障碍树，第一次被 UIA 查询时
    才临时构建，**可能卡 10~30 秒**，属正常现象。所以本脚本对每个窗口都打
    一行"正在探测"，卡住时能看出卡在哪个窗口。
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_ulong, c_ushort, c_void_p, c_wchar_p

user32 = ctypes.WinDLL("user32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)

HRESULT = c_long
HWND = c_void_p
S_OK = 0
COINIT_APARTMENTTHREADED = 0x2

# ---------------------------------------------------------------- GUID / VARIANT


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str) -> None:
        u = uuid.UUID(text)
        super().__init__(
            u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:])
        )


CLSID_CUIAutomation = GUID("FF48DBA4-60EF-4201-AA87-54103EEF594E")
IID_IUIAutomation = GUID("30CBE57D-D9D0-452A-AB13-7AC5AC4825EE")

VT_I4 = 3
VT_BSTR = 8
VT_BOOL = 11


class VARIANT(ctypes.Structure):
    """x64 下 24 字节。只用到 vt + 联合体的头 8 字节，够读 I4/BSTR。"""

    _fields_ = [
        ("vt", c_ushort),
        ("r1", c_ushort),
        ("r2", c_ushort),
        ("r3", c_ushort),
        ("value", ctypes.c_longlong),
        ("pad", ctypes.c_longlong),
    ]

    def as_int(self) -> int:
        return int(self.value)

    def as_bool(self) -> bool:
        return bool(self.value & 0xFFFF)


def variant_i4(value: int) -> VARIANT:
    v = VARIANT()
    v.vt = VT_I4
    v.value = value
    return v


# ---------------------------------------------------------------- COM 调用


def method(ptr: int, index: int, restype, *argtypes):
    """取出 COM 对象 vtable 里的第 index 个方法，包装成可调用对象。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


def release(ptr) -> None:
    if not ptr:
        return
    try:
        value = ptr.value if hasattr(ptr, "value") else ptr
        method(value, 2, c_ulong)(value)
    except Exception:      # noqa: BLE001 —— 释放失败不影响探测结论
        pass


def create_uia():
    """CoCreateInstance(CLSID_CUIAutomation) → IUIAutomation*。"""
    ptr = c_void_p()
    hr = ole32.CoCreateInstance(
        byref(CLSID_CUIAutomation), None, 1, byref(IID_IUIAutomation), byref(ptr)
    )
    if hr != S_OK or not ptr:
        raise OSError(f"CoCreateInstance 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
    return ptr


# UIA 属性 / 控件类型常量
UIA_CONTROLTYPE_PROPERTY = 30003
UIA_ISTEXTPATTERN_AVAILABLE_PROPERTY = 30040
UIA_TEXTPATTERN_ID = 10014
UIA_DOCUMENT = 50030
UIA_EDIT = 50004

TREESCOPE_DESCENDANTS = 4


def element_from_hwnd(uia, hwnd: int):
    el = c_void_p()
    hr = method(uia, 6, HRESULT, HWND, POINTER(c_void_p))(uia, HWND(hwnd), byref(el))
    if hr != S_OK or not el:
        return None, hr
    return el, hr


def find_first_by_type(uia, root, control_type: int):
    """root 的后代里找第一个指定 ControlType 的元素。"""
    cond = c_void_p()
    hr = method(uia, 23, HRESULT, c_int, VARIANT, POINTER(c_void_p))(
        uia, UIA_CONTROLTYPE_PROPERTY, variant_i4(control_type), byref(cond)
    )
    if hr != S_OK or not cond:
        return None
    found = c_void_p()
    hr = method(root.value, 5, HRESULT, c_int, c_void_p, POINTER(c_void_p))(
        root, TREESCOPE_DESCENDANTS, cond, byref(found)
    )
    release(cond)
    if hr != S_OK or not found:
        return None
    return found


def element_text(uia, element) -> str:
    """取元素的 TextPattern 全文。失败返回空串。"""
    pattern = c_void_p()
    hr = method(element.value, 16, HRESULT, c_int, POINTER(c_void_p))(
        element, UIA_TEXTPATTERN_ID, byref(pattern)
    )
    if hr != S_OK or not pattern:
        return ""
    rng = c_void_p()
    hr = method(pattern.value, 7, HRESULT, POINTER(c_void_p))(pattern, byref(rng))
    if hr != S_OK or not rng:
        release(pattern)
        return ""
    bstr = c_wchar_p()
    hr = method(rng.value, 12, HRESULT, c_int, POINTER(c_wchar_p))(rng, -1, byref(bstr))
    text = bstr.value or "" if hr == S_OK else ""
    if bstr:
        oleaut32.SysFreeString(bstr)
    release(rng)
    release(pattern)
    return text


_CONTROL_TYPE_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}


def tree_summary(uia, hwnd: int, limit: int = 400) -> list[tuple[str, int, int]]:
    """枚举 UIA 后代，返回 [(控件类型名, 数量, 其中能读正文的数量)]。

    这个直方图能区分两种完全不同的失败：
      · 树能枚举出一堆 Button/Pane → UIA 链路是通的，只是**没有文本控件**
      · 一个元素都没有            → 该程序根本没实现 UIA provider
    """
    root, _hr = element_from_hwnd(uia, hwnd)
    if root is None:
        return []
    cond = c_void_p()
    if method(uia, 21, HRESULT, POINTER(c_void_p))(uia, byref(cond)) != S_OK or not cond:
        release(root)
        return []
    array = c_void_p()
    hr = method(root.value, 6, HRESULT, c_int, c_void_p, POINTER(c_void_p))(
        root, TREESCOPE_DESCENDANTS, cond, byref(array)
    )
    release(cond)
    release(root)
    if hr != S_OK or not array:
        return []

    total = 0
    tmp = c_int(0)
    if method(array.value, 3, HRESULT, POINTER(c_int))(array, byref(tmp)) == S_OK:
        total = min(int(tmp.value), limit)

    stats: dict[int, list[int]] = {}
    for index in range(total):
        el = c_void_p()
        hr = method(array.value, 4, HRESULT, c_int, POINTER(c_void_p))(array, index, byref(el))
        if hr != S_OK or not el:
            continue
        var = VARIANT()
        hr = method(el.value, 10, HRESULT, c_int, POINTER(VARIANT))(
            el, UIA_CONTROLTYPE_PROPERTY, byref(var)
        )
        ctype = var.as_int() if hr == S_OK else 0
        bucket = stats.setdefault(ctype, [0, 0])
        bucket[0] += 1
        if element_text(uia, el):
            bucket[1] += 1
        release(el)

    release(array)
    rows = [
        (_CONTROL_TYPE_NAMES.get(c, f"#{c}"), n, has_text)
        for c, (n, has_text) in stats.items()
    ]
    rows.sort(key=lambda r: -r[1])
    return rows


def probe_window(uia, hwnd: int) -> tuple[str, str]:
    """返回 (状态, 正文)。状态 ∈ TEXT / NOPAT / NOTGT / ERR。"""
    root, hr = element_from_hwnd(uia, hwnd)
    if root is None:
        return "ERR", f"ElementFromHandle hr=0x{hr & 0xFFFFFFFF:08X}"

    best = ""
    seen_any = False
    for control_type in (UIA_DOCUMENT, UIA_EDIT):
        el = find_first_by_type(uia, root, control_type)
        if el is None:
            continue
        seen_any = True
        text = element_text(uia, el)
        release(el)
        if len(text) > len(best):
            best = text

    release(root)
    if best:
        return "TEXT", best
    return ("NOPAT" if seen_any else "NOTGT"), ""


# ---------------------------------------------------------------- 窗口枚举

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, HWND, ctypes.c_void_p)


def visible_windows() -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = c_ulong()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        out.append((int(hwnd), buf.value, int(pid.value)))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), None)
    return out


def caret_point() -> tuple[int, int] | None:
    """光标的屏幕坐标（和 ui/indicator 用的是同一条路）。"""
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint32),
            ("flags", ctypes.c_uint32),
            ("hwndActive", HWND),
            ("hwndFocus", HWND),
            ("hwndCapture", HWND),
            ("hwndMenuOwner", HWND),
            ("hwndMoveSize", HWND),
            ("hwndCaret", HWND),
            ("rcCaret", ctypes.c_long * 4),
        ]

    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    hwnd = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(HWND(hwnd), None)
    if not user32.GetGUIThreadInfo(tid, byref(info)):
        return None
    point = ctypes.c_long * 2
    pt = point(info.rcCaret[0], info.rcCaret[1])
    user32.ClientToScreen(info.hwndCaret or HWND(hwnd), ctypes.cast(pt, POINTER(point)))
    return int(pt[0]), int(pt[1])


# ---------------------------------------------------------------- 主流程


def main(argv: list[str]) -> int:
    show_caret = "--caret" in argv
    show_tree = "--tree" in argv
    filters = [a for a in argv if not a.startswith("--")]

    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        uia = create_uia()
    except OSError as exc:
        print(f"初始化 UI Automation 失败：{exc}")
        return 1

    print("== 上下文可读性探测 ==")
    print(f"{'状态':<6} {'窗口标题':<52} 补充")
    print("-" * 96)

    read_ok = 0
    for hwnd, title, _pid in visible_windows():
        if filters and not any(f.lower() in title.lower() for f in filters):
            continue
        short = title if len(title) <= 50 else title[:47] + "..."
        print(f"      正在探测 {short}", end="\r")
        try:
            state, payload = probe_window(uia, hwnd)
        except Exception as exc:      # noqa: BLE001
            state, payload = "ERR", str(exc)
        if state == "TEXT":
            read_ok += 1
            body = " ".join(payload.split())[:60]
            print(f"{state:<6} {short:<52} {len(payload)} 字：{body}")
        else:
            print(f"{state:<6} {short:<52} {payload}")
        if show_tree:
            rows = tree_summary(uia, hwnd)
            if not rows:
                print("       └─ UIA 树为空：该程序没有实现无障碍接口")
            else:
                detail = "、".join(
                    f"{name} {n}" + (f"（可读 {t}）" if t else "") for name, n, t in rows[:8]
                )
                print(f"       └─ 控件直方图：{detail}")

    print("-" * 96)
    print(f"读到正文的窗口：{read_ok} 个")

    if show_caret:
        pt = caret_point()
        print(f"光标屏幕坐标：{pt if pt else '取不到'}")

    release(uia)
    ole32.CoUninitialize()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
