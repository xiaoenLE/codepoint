"""Win32 底层声明的唯一集中地。

设计原则：**全工程只有这个文件写 ctypes 声明。**
其它模块一律 ``from .winapi import user32, kernel32, ...`` 直接调用函数，
避免 ctypes 细节散落各处（这是"高内聚"的关键一条）。

⚠ 64 位下的两个必须做的事：
  1. 所有返回句柄/指针的函数都要显式 ``restype``，否则 ctypes 默认按 c_int
     截断高 32 位——这是 Windows + ctypes 最常见的隐形崩溃源。
  2. ``WPARAM/LPARAM/LRESULT`` 按平台宽度定义，不要用 c_int。
"""

from __future__ import annotations

import ctypes
from ctypes import (
    POINTER,
    Structure,
    Union,
    WINFUNCTYPE,
    byref,
    c_int,
    c_size_t,
    c_ssize_t,
    c_void_p,
    c_wchar_p,
)

# ==================================================================== 基础别名

HWND = c_void_p
HANDLE = c_void_p
HMODULE = c_void_p
HINSTANCE = c_void_p
HICON = c_void_p
HCURSOR = c_void_p
HBRUSH = c_void_p
HKL = c_void_p
HIMC = c_void_p
HMENU = c_void_p
HGLOBAL = c_void_p

ATOM = ctypes.c_ushort
WORD = ctypes.c_ushort
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint
LONG = ctypes.c_int32
BOOL = ctypes.c_int
BYTE = ctypes.c_ubyte

# 指针宽度随平台变化
ULONG_PTR = c_size_t
WPARAM = c_size_t
LPARAM = c_ssize_t
LRESULT = c_ssize_t
UINT_PTR = c_size_t

LPWSTR = c_wchar_p
LPCWSTR = c_wchar_p

# ==================================================================== DLL

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
try:
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
except OSError:  # pragma: no cover - 极简系统
    imm32 = None  # type: ignore[assignment]

# ==================================================================== 常量

# —— 钩子 ——
WH_KEYBOARD_LL = 13
WH_KEYBOARD = 2

LLKHF_EXTENDED = 0x01
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_CHAR = 0x0102

# —— SendInput ——
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# —— 热键 ——
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# —— 窗口消息 ——
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CLIPBOARDUPDATE = 0x031D
WM_APP = 0x8000
WM_NULL = 0x0000
WM_SETTINGCHANGE = 0x001A

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
GA_ROOT = 2

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOW = 5
SW_RESTORE = 9

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
LR_SHARED = 0x8000

# —— 托盘 ——
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NOTIFYICON_VERSION_4 = 4

# —— 菜单 ——
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_GRAYED = 0x00000001
MF_DISABLED = 0x00000002
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

# —— 进程 ——
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400

# —— 剪贴板 ——
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# —— IME ——
GCS_COMPSTR = 0x0008
GCS_COMPREADSTR = 0x0001
GCS_RESULTSTR = 0x0800
IME_CMODE_ALPHANUMERIC = 0x0000
IME_CMODE_NATIVE = 0x0001
IME_CMODE_SYMBOL = 0x0040      # 中文标点
IME_CMODE_KATAKANA = 0x0002
IME_CMODE_LANGUAGE = 0x0003
IME_CMODE_FULLSHAPE = 0x0008
IME_CMODE_ROMAN = 0x0010
IME_CMODE_CHARCODE = 0x0020
# —— 其它 ——
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
INFINITE = 0xFFFFFFFF

# ==================================================================== 结构体


class POINT(Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MSG(Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", DWORD),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(Structure):
    _fields_ = [
        ("vkCode", DWORD),
        ("scanCode", DWORD),
        ("flags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class LASTINPUTINFO(Structure):
    """最近一次用户输入（键盘/鼠标）的时间戳，单位与 GetTickCount 相同。

    用途：钩子只会看到**键盘**事件。想知道"用户这会儿在动、钩子却一声不吭"
    （= 钩子可能已经被系统摘掉），就得靠它 —— 它把鼠标也算了进来。
    """

    _fields_ = [("cbSize", UINT), ("dwTime", DWORD)]


class GUITHREADINFO(Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("flags", DWORD),
        ("hwndActive", HWND),
        ("hwndFocus", HWND),
        ("hwndCapture", HWND),
        ("hwndMenuOwner", HWND),
        ("hwndMoveSize", HWND),
        ("hwndCaret", HWND),
        ("rcCaret", RECT),
    ]


class WNDCLASSW(Structure):
    _fields_ = [
        ("style", UINT),
        ("lpfnWndProc", c_void_p),          # WNDPROC，实例化时替换
        ("cbClsExtra", c_int),
        ("cbWndExtra", c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR),
    ]


class GUID(Structure):
    _fields_ = [
        ("Data1", DWORD),
        ("Data2", WORD),
        ("Data3", WORD),
        ("Data4", BYTE * 8),
    ]


class NOTIFYICONDATAW(Structure):
    """Vista+ 完整版（cbSize 填 sizeof 本结构）。"""

    _fields_ = [
        ("cbSize", DWORD),
        ("hWnd", HWND),
        ("uID", UINT),
        ("uFlags", UINT),
        ("uCallbackMessage", UINT),
        ("hIcon", HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", DWORD),
        ("dwStateMask", DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        # 下面这个字段在 C 里是 union { UINT uTimeout; UINT uVersion; }，宽度相同
        ("uVersion", UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", HICON),
    ]


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


# ==================================================================== 回调类型

HOOKPROC = WINFUNCTYPE(LRESULT, c_int, WPARAM, LPARAM)
WNDPROC = WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

# ==================================================================== 函数签名

# ------------------------------ user32：钩子
user32.SetWindowsHookExW.argtypes = [c_int, HOOKPROC, HINSTANCE, DWORD]
user32.SetWindowsHookExW.restype = HANDLE
user32.UnhookWindowsHookEx.argtypes = [HANDLE]
user32.UnhookWindowsHookEx.restype = BOOL
user32.CallNextHookEx.argtypes = [HANDLE, c_int, WPARAM, LPARAM]
user32.CallNextHookEx.restype = LRESULT

# ------------------------------ user32：消息
user32.GetMessageW.argtypes = [POINTER(MSG), HWND, UINT, UINT]
user32.GetMessageW.restype = BOOL
user32.PeekMessageW.argtypes = [POINTER(MSG), HWND, UINT, UINT, UINT]
user32.PeekMessageW.restype = BOOL
user32.TranslateMessage.argtypes = [POINTER(MSG)]
user32.TranslateMessage.restype = BOOL
user32.DispatchMessageW.argtypes = [POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostQuitMessage.argtypes = [c_int]
user32.PostQuitMessage.restype = None
user32.PostThreadMessageW.argtypes = [DWORD, UINT, WPARAM, LPARAM]
user32.PostThreadMessageW.restype = BOOL
user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.PostMessageW.restype = BOOL
user32.SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.SendMessageW.restype = LRESULT

# ------------------------------ user32：窗口
user32.RegisterClassW.argtypes = [POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ATOM
user32.UnregisterClassW.argtypes = [LPCWSTR, HINSTANCE]
user32.UnregisterClassW.restype = BOOL
user32.CreateWindowExW.argtypes = [
    DWORD, LPCWSTR, LPCWSTR, DWORD, c_int, c_int, c_int, c_int,
    HWND, HMENU, HINSTANCE, c_void_p,
]
user32.CreateWindowExW.restype = HWND
user32.DestroyWindow.argtypes = [HWND]
user32.DestroyWindow.restype = BOOL
user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = HWND
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = BOOL
user32.GetWindowTextW.argtypes = [HWND, LPWSTR, c_int]
user32.GetWindowTextW.restype = c_int
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextLengthW.restype = c_int
user32.IsWindow.argtypes = [HWND]
user32.IsWindow.restype = BOOL
user32.GetDC.argtypes = [HWND]
user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HWND, HANDLE]
user32.ReleaseDC.restype = c_int
user32.PrintWindow.argtypes = [HWND, HANDLE, UINT]
user32.PrintWindow.restype = BOOL
user32.IsWindowVisible.argtypes = [HWND]
user32.IsWindowVisible.restype = BOOL
user32.GetWindowThreadProcessId.argtypes = [HWND, POINTER(DWORD)]
user32.GetWindowThreadProcessId.restype = DWORD
user32.GetGUIThreadInfo.argtypes = [DWORD, POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = BOOL
user32.SetWindowLongW.argtypes = [HWND, c_int, LONG]
user32.SetWindowLongW.restype = LONG
user32.GetWindowLongW.argtypes = [HWND, c_int]
user32.GetWindowLongW.restype = LONG
user32.GetAncestor.argtypes = [HWND, UINT]
user32.GetAncestor.restype = HWND
user32.ShowWindow.argtypes = [HWND, c_int]
user32.ShowWindow.restype = BOOL
user32.GetDesktopWindow.argtypes = []
user32.GetDesktopWindow.restype = HWND
user32.LoadIconW.argtypes = [HINSTANCE, c_void_p]
user32.LoadIconW.restype = HICON
user32.LoadImageW.argtypes = [HINSTANCE, LPCWSTR, UINT, c_int, c_int, UINT]
user32.LoadImageW.restype = HANDLE
user32.DestroyIcon.argtypes = [HICON]
user32.DestroyIcon.restype = BOOL
user32.GetKeyState.argtypes = [c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.GetKeyboardState.argtypes = [ctypes.c_char * 256]
user32.GetKeyboardState.restype = BOOL
user32.MapVirtualKeyW.argtypes = [UINT, UINT]
user32.MapVirtualKeyW.restype = UINT
user32.GetKeyboardLayout.argtypes = [DWORD]
user32.GetKeyboardLayout.restype = HKL
user32.ToUnicodeEx.argtypes = [
    UINT, UINT, ctypes.c_char * 256, LPWSTR, c_int, UINT, HKL
]
user32.ToUnicodeEx.restype = c_int
user32.GetAsyncKeyState.argtypes = [c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetLastInputInfo.argtypes = [POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = BOOL

# ------------------------------ user32：输入注入
user32.SendInput.argtypes = [UINT, POINTER(INPUT), c_int]
user32.SendInput.restype = UINT

# ------------------------------ user32：热键
user32.RegisterHotKey.argtypes = [HWND, c_int, UINT, UINT]
user32.RegisterHotKey.restype = BOOL
user32.UnregisterHotKey.argtypes = [HWND, c_int]
user32.UnregisterHotKey.restype = BOOL

# ------------------------------ user32：剪贴板
user32.AddClipboardFormatListener.argtypes = [HWND]
user32.AddClipboardFormatListener.restype = BOOL
user32.RemoveClipboardFormatListener.argtypes = [HWND]
user32.RemoveClipboardFormatListener.restype = BOOL
user32.OpenClipboard.argtypes = [HWND]
user32.OpenClipboard.restype = BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = BOOL
user32.GetClipboardData.argtypes = [UINT]
user32.GetClipboardData.restype = HANDLE
user32.SetClipboardData.argtypes = [UINT, HANDLE]
user32.SetClipboardData.restype = HANDLE
user32.IsClipboardFormatAvailable.argtypes = [UINT]
user32.IsClipboardFormatAvailable.restype = BOOL
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = DWORD

# ------------------------------ user32：菜单
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = HMENU
user32.DestroyMenu.argtypes = [HMENU]
user32.DestroyMenu.restype = BOOL
user32.AppendMenuW.argtypes = [HMENU, UINT, UINT_PTR, LPCWSTR]
user32.AppendMenuW.restype = BOOL
user32.TrackPopupMenu.argtypes = [
    HMENU, UINT, c_int, c_int, c_int, HWND, c_void_p
]
user32.TrackPopupMenu.restype = BOOL
user32.TrackPopupMenuEx.argtypes = [HMENU, UINT, c_int, c_int, HWND, c_void_p]
user32.TrackPopupMenuEx.restype = BOOL
user32.GetCursorPos.argtypes = [POINTER(POINT)]
user32.GetCursorPos.restype = BOOL
user32.ClientToScreen.argtypes = [HWND, POINTER(POINT)]
user32.ClientToScreen.restype = BOOL
user32.ScreenToClient.argtypes = [HWND, POINTER(POINT)]
user32.ScreenToClient.restype = BOOL
user32.GetSystemMetrics.argtypes = [c_int]
user32.GetSystemMetrics.restype = c_int
user32.SetMenuDefaultItem.argtypes = [HMENU, UINT, UINT]
user32.SetMenuDefaultItem.restype = BOOL

# ------------------------------ user32：窗口枚举（用于"自动获取进程名"）
user32.EnumWindows.argtypes = [c_void_p, LPARAM]        # WNDENUMPROC, lParam
user32.EnumWindows.restype = BOOL
user32.GetWindow.argtypes = [HWND, UINT]
user32.GetWindow.restype = HWND
user32.IsIconic.argtypes = [HWND]
user32.IsIconic.restype = BOOL
user32.GetWindowRect.argtypes = [HWND, POINTER(RECT)]
user32.GetWindowRect.restype = BOOL
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = BOOL
user32.ShowWindow.argtypes = [HWND, c_int]
user32.ShowWindow.restype = BOOL
user32.GetClassNameW.argtypes = [HWND, LPWSTR, c_int]
user32.GetClassNameW.restype = c_int
user32.GetIconInfo.argtypes = [HICON, c_void_p]          # POINTER(ICONINFO)
user32.GetIconInfo.restype = BOOL

# ------------------------------ kernel32
kernel32.GetModuleHandleW.argtypes = [LPCWSTR]
kernel32.GetModuleHandleW.restype = HMODULE
kernel32.GetTickCount.argtypes = []
kernel32.GetTickCount.restype = DWORD
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = HANDLE
kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = DWORD
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenProcess.restype = HANDLE
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, LPWSTR, POINTER(DWORD)]
kernel32.QueryFullProcessImageNameW.restype = BOOL
kernel32.CreateMutexW.argtypes = [c_void_p, BOOL, LPCWSTR]
kernel32.CreateMutexW.restype = HANDLE
kernel32.GlobalAlloc.argtypes = [UINT, c_size_t]
kernel32.GlobalAlloc.restype = HGLOBAL
kernel32.GlobalFree.argtypes = [HGLOBAL]
kernel32.GlobalFree.restype = HGLOBAL
kernel32.GlobalLock.argtypes = [HGLOBAL]
kernel32.GlobalLock.restype = c_void_p
kernel32.GlobalUnlock.argtypes = [HGLOBAL]
kernel32.GlobalUnlock.restype = BOOL
kernel32.GetConsoleWindow.argtypes = []
kernel32.GetConsoleWindow.restype = HWND
kernel32.SetConsoleTitleW.argtypes = [LPCWSTR]
kernel32.SetConsoleTitleW.restype = BOOL

# ------------------------------ shell32
shell32.Shell_NotifyIconW.argtypes = [DWORD, POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = BOOL
# "任务栏重建好了" —— explorer 启动/崩溃重启时会广播它。
# 开机自启的程序必须听这个消息，否则托盘图标会永久丢失。
user32.RegisterWindowMessageW.argtypes = [LPCWSTR]
user32.RegisterWindowMessageW.restype = UINT
WM_TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")
# 取 exe 图标：给一个路径，换回 HICON
shell32.SHGetFileInfoW.argtypes = [LPCWSTR, DWORD, c_void_p, UINT, UINT]
shell32.SHGetFileInfoW.restype = UINT_PTR

# ------------------------------ gdi32（把图标拆成像素）
gdi32.GetObjectW.argtypes = [HANDLE, c_int, c_void_p]
gdi32.GetObjectW.restype = c_int
gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, UINT, UINT, c_void_p, c_void_p, UINT]
gdi32.GetDIBits.restype = c_int
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.DeleteDC.argtypes = [HANDLE]
gdi32.DeleteDC.restype = BOOL
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteObject.restype = BOOL

# ------------------------------ advapi32（权限）
advapi32.OpenProcessToken.argtypes = [HANDLE, DWORD, POINTER(HANDLE)]
advapi32.OpenProcessToken.restype = BOOL
advapi32.GetTokenInformation.argtypes = [
    HANDLE, c_int, c_void_p, DWORD, POINTER(DWORD)
]
advapi32.GetTokenInformation.restype = BOOL
advapi32.ConvertStringSidToSidW.argtypes = [LPCWSTR, POINTER(c_void_p)]
advapi32.ConvertStringSidToSidW.restype = BOOL

# ------------------------------ imm32（可能缺失）
if imm32 is not None:
    imm32.ImmGetContext.argtypes = [HWND]
    imm32.ImmGetContext.restype = HIMC
    imm32.ImmReleaseContext.argtypes = [HWND, HIMC]
    imm32.ImmReleaseContext.restype = BOOL
    imm32.ImmGetOpenStatus.argtypes = [HIMC]
    imm32.ImmGetOpenStatus.restype = BOOL
    imm32.ImmGetConversionStatus.argtypes = [HIMC, POINTER(DWORD), POINTER(DWORD)]
    imm32.ImmGetConversionStatus.restype = BOOL
    imm32.ImmGetCompositionStringW.argtypes = [HIMC, DWORD, c_void_p, DWORD]
    imm32.ImmGetCompositionStringW.restype = LONG
    imm32.ImmIsIME.argtypes = [HKL]
    imm32.ImmIsIME.restype = BOOL

# ------------------------------ 进程/图标相关结构
#
# 为什么用 ctypes 手写 BITMAPINFOHEADER 而不是用现成库：
# 整个工程是零依赖的，而"把 exe 图标画到界面上"只需要这四个结构 + GetDIBits。

GW_OWNER = 4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001
DIB_RGB_COLORS = 0
BI_RGB = 0


class SHFILEINFOW(Structure):
    _fields_ = [
        ("hIcon", HICON),
        ("iIcon", c_int),
        ("dwAttributes", DWORD),
        ("szDisplayName", ctypes.c_wchar * 260),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


class ICONINFO(Structure):
    _fields_ = [
        ("fIcon", BOOL),
        ("xHotspot", DWORD),
        ("yHotspot", DWORD),
        ("hbmMask", HANDLE),
        ("hbmColor", HANDLE),
    ]


class BITMAP(Structure):
    _fields_ = [
        ("bmType", LONG),
        ("bmWidth", LONG),
        ("bmHeight", LONG),
        ("bmWidthBytes", LONG),
        ("bmPlanes", WORD),
        ("bmBitsPixel", WORD),
        ("bmBits", c_void_p),
    ]


class BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize", DWORD),
        ("biWidth", LONG),
        ("biHeight", LONG),
        ("biPlanes", WORD),
        ("biBitCount", WORD),
        ("biCompression", DWORD),
        ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG),
        ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD),
        ("biClrImportant", DWORD),
    ]


class RGBQUAD(Structure):
    _fields_ = [
        ("rgbBlue", BYTE), ("rgbGreen", BYTE),
        ("rgbRed", BYTE), ("rgbReserved", BYTE),
    ]


class BITMAPINFO(Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


user32.IsWindow.argtypes = [HWND]
user32.IsWindow.restype = BOOL
user32.GetDC.argtypes = [HWND]
user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HWND, HANDLE]
user32.ReleaseDC.restype = c_int
user32.PrintWindow.argtypes = [HWND, HANDLE, UINT]
user32.PrintWindow.restype = BOOL

# ==================================================================== 辅助


def last_error() -> int:
    return ctypes.get_last_error()


def error_text(code: int | None = None) -> str:
    code = ctypes.get_last_error() if code is None else code
    buf = ctypes.c_wchar_p()
    flags = 0x00001000 | 0x00000200  # FORMAT_MESSAGE_FROM_SYSTEM | IGNORE_INSERTS
    n = kernel32.FormatMessageW(flags, None, code, 0, byref(buf), 0, None)
    if not n or not buf.value:
        return f"错误码 {code}"
    return buf.value.strip()


# 上面 FormatMessageW 未声明，这里补上（避免顺序依赖）
kernel32.FormatMessageW.argtypes = [
    DWORD, c_void_p, DWORD, DWORD, POINTER(c_wchar_p), DWORD, c_void_p
]
kernel32.FormatMessageW.restype = DWORD


def gui_thread_info(thread_id: int) -> GUITHREADINFO | None:
    """取指定线程的 GUI 信息（含焦点窗口、插入符矩形）。"""
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(DWORD(thread_id), byref(info)):
        return info
    return None


# ---------------------------------------------------------------- 性能采样
#
# 全部走 Win32 原生接口（不引入 psutil），保持整个工程零第三方依赖。

psapi = ctypes.WinDLL("psapi", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", DWORD),
        ("cntThreads", DWORD),
        ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", LONG),
        ("dwFlags", DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    """最后一个字段 ``PrivateUsage`` 就是任务管理器"内存"列那个数。"""

    _fields_ = [
        ("cb", DWORD),
        ("PageFaultCount", DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class SYSTEM_INFO(ctypes.Structure):
    _fields_ = [
        ("wProcessorArchitecture", WORD),
        ("wReserved", WORD),
        ("dwPageSize", DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.c_size_t),
        ("dwNumberOfProcessors", DWORD),
        ("dwProcessorType", DWORD),
        ("dwAllocationGranularity", DWORD),
        ("wProcessorLevel", WORD),
        ("wProcessorRevision", WORD),
    ]


# ---------------------------------------------------------------- 显示器 / 工作区

MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTOPRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),      # 去掉任务栏之后的可用区域
        ("dwFlags", DWORD),
    ]


user32.GetCursorPos.argtypes = [POINTER(POINT)]
user32.GetCursorPos.restype = BOOL
user32.MonitorFromPoint.argtypes = [POINT, DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p          # HMONITOR
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = BOOL
user32.SystemParametersInfoW.argtypes = [UINT, UINT, ctypes.c_void_p, UINT]
user32.SystemParametersInfoW.restype = BOOL


kernel32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
kernel32.CreateToolhelp32Snapshot.restype = HANDLE
kernel32.Process32FirstW.argtypes = [HANDLE, POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = BOOL
kernel32.Process32NextW.argtypes = [HANDLE, POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = BOOL
kernel32.GetProcessTimes.argtypes = [
    HANDLE, POINTER(FILETIME), POINTER(FILETIME),
    POINTER(FILETIME), POINTER(FILETIME),
]
kernel32.GetProcessTimes.restype = BOOL
kernel32.GetSystemTimes.argtypes = [
    POINTER(FILETIME), POINTER(FILETIME), POINTER(FILETIME)
]
kernel32.GetSystemTimes.restype = BOOL
kernel32.GetProcessHandleCount.argtypes = [HANDLE, POINTER(DWORD)]
kernel32.GetProcessHandleCount.restype = BOOL
kernel32.GetSystemInfo.argtypes = [POINTER(SYSTEM_INFO)]
kernel32.GetSystemInfo.restype = None
psapi.GetProcessMemoryInfo.argtypes = [
    HANDLE, POINTER(PROCESS_MEMORY_COUNTERS_EX), DWORD
]
psapi.GetProcessMemoryInfo.restype = BOOL


__all__ = [
    "user32", "kernel32", "shell32", "gdi32", "advapi32", "imm32",
    "HWND", "HANDLE", "HINSTANCE", "HICON", "HKL", "HIMC", "HMENU", "HGLOBAL",
    "DWORD", "UINT", "WORD", "BOOL", "BYTE", "LONG", "ULONG_PTR",
    "WPARAM", "LPARAM", "LRESULT", "UINT_PTR", "LPCWSTR", "LPWSTR",
    "POINT", "RECT", "MSG", "KBDLLHOOKSTRUCT", "LASTINPUTINFO", "GUITHREADINFO", "WNDCLASSW",
    "GUID", "NOTIFYICONDATAW", "INPUT", "KEYBDINPUT", "MOUSEINPUT", "HARDWAREINPUT",
    "SHFILEINFOW", "ICONINFO", "BITMAP", "BITMAPINFO", "BITMAPINFOHEADER", "RGBQUAD",
    "GW_OWNER", "PROCESS_QUERY_LIMITED_INFORMATION",
    "SHGFI_ICON", "SHGFI_LARGEICON", "SHGFI_SMALLICON", "DIB_RGB_COLORS", "BI_RGB",
    "HOOKPROC", "WNDPROC",
    "last_error", "error_text", "gui_thread_info", "c_void_p", "byref", "c_int",
    # 性能采样
    "psapi", "TH32CS_SNAPPROCESS", "PROCESSENTRY32W", "FILETIME",
    "PROCESS_MEMORY_COUNTERS_EX", "SYSTEM_INFO", "MAX_PATH",
    # 显示器 / 工作区
    "MONITORINFO", "MONITOR_DEFAULTTONEAREST", "MONITOR_DEFAULTTOPRIMARY",
    "MONITOR_DEFAULTTONULL",
]
