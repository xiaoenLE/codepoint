"""隐藏窗口 + 消息循环线程。

它是 Win32 侧所有"需要消息泵"功能的**共同宿主**：
低级键盘钩子、RegisterHotKey、Shell_NotifyIcon、AddClipboardFormatListener
都要求调用线程跑消息循环，用同一个线程既省资源也避免互相抢消息。

对外只暴露三件事（低耦合）：
    start()/stop()                 生命周期
    call(fn)                       在消息线程上同步执行任意函数
    post(fn)                       在消息线程上异步执行
    add_handler(msg, fn)           注册窗口消息处理器
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import byref, c_void_p
from typing import Callable, Dict, Optional

from .winapi import (
    HINSTANCE,
    HWND,
    LPARAM,
    MSG,
    UINT,
    WNDCLASSW,
    WNDPROC,
    WPARAM,
    WM_APP,
    WM_CLOSE,
    WM_DESTROY,
    error_text,
    kernel32,
    last_error,
    user32,
)

# 自定义"请在消息线程上跑任务"的唤醒消息
WM_RUN_TASK = WM_APP + 100
# "把你自己的窗口显示出来" —— 第二次启动时用它去唤醒正在运行的实例
WM_APP_ACTIVATE = WM_APP + 101

#: 隐藏消息窗口的类名前缀。**末尾的 pid 让它天然唯一**，
#: 但前缀是固定的 —— 这样第二个实例能靠"前缀匹配"把第一个实例的窗口找出来
#: （EnumWindows 能枚举到隐藏的顶层窗口，也就能枚举到它）。
CLASS_PREFIX = "CodePointMsg"

Task = Callable[[], None]
Handler = Callable[[int, int, int], None]   # (message, wParam, lParam) -> None


def find_peer_window() -> int:
    """找**别的进程**里的隐藏消息窗口，返回 hwnd；没有则 0。

    为什么需要它：默认"关闭窗口时收进托盘"，而 Win11 默认把新托盘图标
    藏进折叠区 —— 用户关掉窗口后就再也找不回来了，再双击程序却只得到
    一句"已经在运行了"，等于被锁在门外。
    """
    import ctypes as _ctypes

    if os.name != "nt":
        return 0

    mine = os.getpid()
    found = []

    enum_proc = _ctypes.WINFUNCTYPE(_ctypes.c_int, _ctypes.c_void_p,
                                    _ctypes.c_ssize_t)

    def _cb(hwnd, _lparam):
        buf = _ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(HWND(hwnd), buf, 256):
            name = buf.value
            if name.startswith(f"{CLASS_PREFIX}_"):
                suffix = name[len(CLASS_PREFIX) + 1:]
                if suffix.isdigit() and int(suffix) != mine:
                    found.append(int(hwnd))
        return 1

    try:
        user32.EnumWindows(enum_proc(_cb), 0)
    except Exception:      # noqa: BLE001
        return 0
    return found[0] if found else 0


def activate_peer_window(timeout: float = 0.0) -> bool:
    """请已经在运行的实例把窗口显示出来。成功返回 True。

    用 ``PostMessage`` 而不是 ``SendMessage``：对方在消息循环里处理，
    这边不必等待（等待会有卡死风险，对方若是无响应的进程就麻烦了）。
    """
    hwnd = find_peer_window()
    if not hwnd:
        return False
    try:
        return bool(user32.PostMessageW(HWND(hwnd), WM_APP_ACTIVATE, 0, 0))
    except Exception:      # noqa: BLE001
        return False


class MessageWindow:
    """隐藏窗口 + 消息循环线程。线程安全。"""

    def __init__(self, name: str = CLASS_PREFIX) -> None:
        self._class_name = f"{name}_{os.getpid()}"
        self._hwnd: Optional[int] = None
        self._tid: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._tasks: "queue.Queue[Task]" = queue.Queue()
        self._handlers: Dict[int, Handler] = {}
        self._wndproc = WNDPROC(self._wnd_proc)   # 必须持引用，否则被 GC
        self._start_error: Optional[str] = None
        self._mutex = threading.Lock()
        # 跨线程调用的默认等待上限。
        # 消息线程是所有后台能力的宿主，它一旦卡住（例如钩子回调死锁），
        # 每个 call() 都会干等到这里。3 个卸载动作 × 6 秒 = 退出卡 18 秒，
        # 用户体感就是"点退出没反应"。2 秒对正常的微秒级任务绰绰有余，
        # 而退出阶段 App 会再把它压到 1 秒。
        self._default_timeout = 2.0

    def set_default_timeout(self, seconds: float) -> None:
        self._default_timeout = seconds

    # ------------------------------------------------------------ 属性

    @property
    def hwnd(self) -> Optional[int]:
        return self._hwnd

    @property
    def thread_id(self) -> Optional[int]:
        return self._tid

    @property
    def running(self) -> bool:
        return self._hwnd is not None and not self._stopped.is_set()

    @property
    def start_error(self) -> Optional[str]:
        return self._start_error

    # ------------------------------------------------------------ 生命周期

    def start(self, timeout: float = 6.0) -> bool:
        with self._mutex:
            if self._thread and self._thread.is_alive():
                return True
            self._ready.clear()
            self._stopped.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run, name=self._class_name, daemon=True
            )
            self._thread.start()
        if not self._ready.wait(timeout):
            self._start_error = self._start_error or "消息线程启动超时"
            return False
        return self._start_error is None

    def stop(self, timeout: float = 3.0) -> None:
        hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(HWND(hwnd), WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout)
        self._thread = None
        self._hwnd = None
        self._tid = None

    # ------------------------------------------------------------ 任务调度

    def post(self, fn: Task) -> None:
        """在消息线程上异步执行 fn（fire-and-forget）。"""
        self._tasks.put(fn)
        hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(HWND(hwnd), WM_RUN_TASK, 0, 0)

    def call(self, fn: Callable[[], object], timeout: Optional[float] = None):
        """在消息线程上同步执行 fn 并返回结果。fn 抛异常会在调用方抛出。"""
        if timeout is None:
            timeout = self._default_timeout
        if self._tid is None:
            raise RuntimeError("消息线程未启动")
        if threading.get_ident() == self._tid:
            return fn()          # 已经在消息线程里，直接跑，避免自己等自己
        done = threading.Event()
        box: Dict[str, object] = {}

        def wrapper() -> None:
            try:
                box["result"] = fn()
            except BaseException as exc:      # noqa: BLE001
                box["error"] = exc
            finally:
                done.set()

        self.post(wrapper)
        if not done.wait(timeout):
            raise TimeoutError("消息线程任务超时")
        if "error" in box:
            raise box["error"]                        # type: ignore[misc]
        return box.get("result")

    def add_handler(self, message: int, handler: Handler) -> None:
        """注册窗口消息处理器（在消息线程上被调用）。"""
        self._handlers[message] = handler

    def remove_handler(self, message: int) -> None:
        self._handlers.pop(message, None)

    def drain_tasks(self) -> None:
        while True:
            try:
                task = self._tasks.get_nowait()
            except queue.Empty:
                return
            try:
                task()
            except Exception:      # noqa: BLE001 —— 单个任务出错不能拖垮消息循环
                pass

    # ------------------------------------------------------------ 内部

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_CLOSE:
                user32.DestroyWindow(HWND(hwnd))
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_RUN_TASK:
                self.drain_tasks()
                return 0
            handler = self._handlers.get(msg)
            if handler is not None:
                handler(msg, int(wparam), int(lparam))
                return 0
        except Exception:          # noqa: BLE001
            pass
        return user32.DefWindowProcW(HWND(hwnd), UINT(msg), WPARAM(wparam), LPARAM(lparam))

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        try:
            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc, c_void_p).value or 0
            wc.hInstance = HINSTANCE(hinst)
            wc.lpszClassName = self._class_name
            if not user32.RegisterClassW(byref(wc)):
                code = last_error()
                # 类已存在（上一次没清干净）不算致命，继续用
                if code != 1410:      # ERROR_CLASS_ALREADY_EXISTS
                    self._start_error = f"RegisterClassW 失败：{error_text(code)}"
                    self._ready.set()
                    return

            hwnd = user32.CreateWindowExW(
                0, self._class_name, "CodePoint", 0,
                0, 0, 0, 0, None, None, HINSTANCE(hinst), None,
            )
            if not hwnd:
                code = last_error()
                self._start_error = f"CreateWindowExW 失败：{error_text(code)}"
                self._ready.set()
                return

            self._hwnd = int(hwnd)
            self._tid = kernel32.GetCurrentThreadId()
            self._ready.set()

            msg = MSG()
            while True:
                ctypes.set_last_error(0)
                ret = user32.GetMessageW(byref(msg), None, 0, 0)
                if ret == 0:
                    break
                if ret == -1:
                    break
                # 线程消息（PostThreadMessage）hwnd 为空，直接处理
                if not msg.hwnd:
                    if msg.message == WM_RUN_TASK:
                        self.drain_tasks()
                    continue
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
        except Exception as exc:      # noqa: BLE001
            self._start_error = f"消息线程异常：{exc}"
            self._ready.set()
        finally:
            # 清场：把队列里剩下的任务丢掉并唤醒等待者，避免 call() 挂死
            self.drain_tasks()
            self._stopped.set()
            try:
                user32.UnregisterClassW(self._class_name, HINSTANCE(hinst))
            except Exception:
                pass
            self._hwnd = None
            self._ready.set()
