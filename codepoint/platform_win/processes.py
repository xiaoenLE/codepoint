"""进程 / 窗口枚举 + exe 图标提取（纯 ctypes，零第三方依赖）。

**为什么要这个模块**：让用户自己敲进程名的做法很难用 —— 敲错一个字母就
永远不生效，而且完全看不出错在哪。这里改成：

1. 列出**所有有可见窗口的进程**（进程名 / 完整路径 / 窗口标题），用户在界面上
   直接挑，不用输任何字；
2. 顺手把 exe 的图标取出来给界面画上，一眼就能认出是哪个程序。

图标提取链路：SHGetFileInfoW(路径 → HICON) → GetIconInfo → GetDIBits(32bpp BGRA)
→ 交给 ``pngwriter`` 编码成 PNG 字节 → 界面用 PhotoImage 显示。
全程只碰 gdi32/user32/shell32 的只读接口，不写文件、不改系统状态。
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from ctypes import byref
from typing import Dict, List, Optional

from .winapi import (
    BI_RGB,
    BITMAP,
    BITMAPINFO,
    BITMAPINFOHEADER,
    DIB_RGB_COLORS,
    DWORD,
    GW_OWNER,
    HANDLE,
    HICON,
    HWND,
    ICONINFO,
    LPARAM,
    PROCESS_QUERY_LIMITED_INFORMATION,
    SHFILEINFOW,
    SHGFI_ICON,
    SHGFI_LARGEICON,
    WS_EX_TOOLWINDOW,
    c_int,
    gdi32,
    kernel32,
    shell32,
    user32,
)

GWL_EXSTYLE = -20

_ENUM_PROC = ctypes.WINFUNCTYPE(c_int, HWND, LPARAM)


# ------------------------------------------------------------------ 数据结构


@dataclass(frozen=True)
class RunningApp:
    """一个"看得见"的运行中程序。"""

    pid: int = 0
    process_name: str = ""      # 小写文件名，如 codesys.exe
    display_name: str = ""      # 窗口标题里最像"程序名"的那段
    path: str = ""              # 完整可执行文件路径
    title: str = ""             # 代表性窗口标题
    hwnd: int = 0

    @property
    def label(self) -> str:
        return self.display_name or self.process_name or self.path or f"PID {self.pid}"

    def matches(self, text: str) -> bool:
        """界面搜索框用。"""
        t = (text or "").strip().lower()
        if not t:
            return True
        return (t in self.process_name.lower()
                or t in self.path.lower()
                or t in self.title.lower()
                or t in self.display_name.lower())


# ------------------------------------------------------------------ 进程信息


def process_path(pid: int) -> str:
    """取进程的完整镜像路径（拿不到返回空串，绝不抛异常）。"""
    if pid <= 0:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def process_name_of(pid: int) -> str:
    return os.path.basename(process_path(pid)).lower()


# ------------------------------------------------------------------ 窗口枚举


def _plain_exe_window(hwnd: int) -> bool:
    """只挑"像正常程序窗口"的那些：可见、有标题、不是工具窗、没有属主。"""
    if not user32.IsWindow(HWND(hwnd)):
        return False
    if not user32.IsWindowVisible(HWND(hwnd)):
        return False
    n = int(user32.GetWindowTextLengthW(HWND(hwnd)) or 0)
    if n <= 0:
        return False
    if user32.GetWindowLongW(HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    owner = user32.GetWindow(HWND(hwnd), GW_OWNER)
    if owner:
        return False          # 有属主的通常是对话框/浮窗，不是主窗口
    return True


def list_windows() -> List[Dict[str, object]]:
    """枚举所有可见的顶层窗口。返回顺序不保证，调用方自己排。"""
    found: List[Dict[str, object]] = []

    def _cb(hwnd, _lparam):
        try:
            h = int(hwnd) if hwnd else 0
            if h and _plain_exe_window(h):
                n = int(user32.GetWindowTextLengthW(HWND(h)) or 0) + 1
                buf = ctypes.create_unicode_buffer(max(n, 8))
                user32.GetWindowTextW(HWND(h), buf, len(buf))
                pid = DWORD(0)
                user32.GetWindowThreadProcessId(HWND(h), byref(pid))
                found.append({"hwnd": h, "pid": int(pid.value), "title": buf.value})
        except Exception:      # noqa: BLE001 —— 枚举回调里绝不能抛
            pass
        return 1

    try:
        user32.EnumWindows(_ENUM_PROC(_cb), 0)
    except Exception:      # noqa: BLE001
        pass
    return found


def list_running_apps(exclude_pids: Optional[List[int]] = None) -> List[RunningApp]:
    """列出"有可见窗口"的进程，按 pid 去重（每个程序一行）。

    去重规则：同一个 pid 的多个窗口只保留一个，优先保留标题最长的那个
    （主窗口标题通常最长），这样界面上不会出现同一个程序很多行。
    """
    skip = set(exclude_pids or ())
    by_pid: Dict[int, Dict[str, object]] = {}
    for w in list_windows():
        pid = int(w["pid"])
        if pid <= 0 or pid in skip:
            continue
        old = by_pid.get(pid)
        if old is None or len(str(w["title"])) > len(str(old["title"])):
            by_pid[pid] = w

    out: List[RunningApp] = []
    for pid, w in by_pid.items():
        path = process_path(pid)
        name = os.path.basename(path).lower()
        title = str(w["title"])
        out.append(RunningApp(
            pid=pid,
            process_name=name,
            display_name=_guess_display_name(title, name),
            path=path,
            title=title,
            hwnd=int(w["hwnd"]),
        ))
    out.sort(key=lambda a: (a.display_name.lower(), a.process_name))
    return out


def _guess_display_name(title: str, process_name: str) -> str:
    """从窗口标题里猜一个"程序叫什么"。

    标题通常是 ``文档 - 程序名`` 或 ``程序名 - 文档``，取最长的那段做名字；
    取不到就退回进程名（去掉 .exe）。
    """
    if title:
        parts = [p.strip() for p in title.replace("—", "-").replace("–", "-").split("-")]
        parts = [p for p in parts if p]
        if parts:
            # 去掉明显的"文档名/文件名"段
            good = [p for p in parts if not p.lower().endswith(
                (".st", ".txt", ".doc", ".docx", ".prj", ".project", "project"))]
            return (good or parts)[0][:48]
    return process_name[:-4] if process_name.lower().endswith(".exe") else process_name


# ------------------------------------------------------------------ 图标


def exe_icon_rgba(path: str, size: int = 32):
    """取某个 exe 文件的图标像素。失败返回 None。"""
    if not path or not os.path.exists(path):
        return None
    info = SHFILEINFOW()
    try:
        flags = SHGFI_ICON | SHGFI_LARGEICON
        ret = shell32.SHGetFileInfoW(
            path, 0, byref(info), ctypes.sizeof(info), flags
        )
        if not ret or not info.hIcon:
            return None
        return hicon_rgba(int(info.hIcon), size)
    except Exception:      # noqa: BLE001
        return None
    finally:
        if info.hIcon:
            try:
                user32.DestroyIcon(HICON(info.hIcon))
            except Exception:      # noqa: BLE001
                pass


def hicon_rgba(hicon: int, size: int = 32):
    """HICON → ``[[(r, g, b, a), ...], ...]`` 像素矩阵（尺寸 size×size）。

    步骤：GetIconInfo 拿到颜色位图 → GetObject 问尺寸/位深 → GetDIBits 拉 32bpp
    BGRA → 转成 RGBA。某些老图标没有 alpha 通道（全 0），这时用掩码位图补上。
    """
    if not hicon:
        return None
    ii = ICONINFO()
    if not user32.GetIconInfo(HICON(hicon), byref(ii)):
        return None
    try:
        hbm = ii.hbmColor or ii.hbmMask
        if not hbm:
            return None
        bm = BITMAP()
        if not gdi32.GetObjectW(HANDLE(hbm), ctypes.sizeof(bm), byref(bm)):
            return None
        w = int(bm.bmWidth) or 0
        h = int(bm.bmHeight) or 0
        if w <= 0 or h <= 0:
            w = h = size

        hdc = gdi32.CreateCompatibleDC(None)
        if not hdc:
            return None
        try:
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h          # 负值 = 自上而下，省得再翻转
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB

            buf = (ctypes.c_ubyte * (w * h * 4))()
            got = gdi32.GetDIBits(HANDLE(hdc), HANDLE(hbm), 0, h, buf,
                                  byref(bmi), DIB_RGB_COLORS)
            if not got:
                return None
            rows = _to_rgba_rows(bytes(buf), w, h)
            # alpha 全 0 → 这个图标没带透明通道，用掩码补
            if not any(px[3] for row in rows for px in row):
                rows = _apply_mask(rows, ii, hdc, w, h)
            return _resize(rows, size)
        finally:
            gdi32.DeleteDC(HANDLE(hdc))
    except Exception:      # noqa: BLE001
        return None
    finally:
        for hbm in (ii.hbmColor, ii.hbmMask):
            if hbm:
                try:
                    gdi32.DeleteObject(HANDLE(hbm))
                except Exception:      # noqa: BLE001
                    pass


def _to_rgba_rows(raw: bytes, w: int, h: int):
    rows = []
    for y in range(h):
        row = []
        base = y * w * 4
        for x in range(w):
            i = base + x * 4
            b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            row.append((r, g, b, a))
        rows.append(row)
    return rows


def _apply_mask(rows, ii, hdc, w: int, h: int):
    """用 AND 掩码给没有 alpha 的图标补透明度（掩码为 1 的像素 = 透明）。"""
    hbm = ii.hbmMask
    if not hbm:
        return rows          # 没有掩码就保持原样（宁可没透明，也不能乱改像素）
    try:
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        # 只有掩码位图时位深是 1bpp，这里简单按 32bpp 读会错，
        # 所以直接问位图本身：拿不到就整体置为不透明（宁可没有透明也别乱）。
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if not gdi32.GetDIBits(HANDLE(hdc), HANDLE(hbm), 0, h, buf, byref(bmi),
                               DIB_RGB_COLORS):
            return rows
        raw = bytes(buf)
        out = []
        for y in range(h):
            row = []
            for x in range(w):
                r, g, b, _ = rows[y][x]
                i = (y * w + x) * 4
                # 掩码里非 0 = 该像素被"抠掉"
                transparent = (raw[i] or raw[i + 1] or raw[i + 2])
                row.append((r, g, b, 0 if transparent else 255))
            out.append(row)
        return out
    except Exception:      # noqa: BLE001
        return rows


def _resize(rows, size: int):
    """最近邻缩放（图标本身就是像素画，最近邻比插值更"脆"更好看）。"""
    h = len(rows)
    w = len(rows[0]) if h else 0
    if not h or not w or (w == size and h == size):
        return rows
    out = []
    for y in range(size):
        sy = min(h - 1, y * h // size)
        src = rows[sy]
        out.append([src[min(w - 1, x * w // size)] for x in range(size)])
    return out


__all__ = [
    "RunningApp",
    "list_windows",
    "list_running_apps",
    "process_path",
    "process_name_of",
    "exe_icon_rgba",
    "hicon_rgba",
]
