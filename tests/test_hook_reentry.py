"""钩子回调的重入安全 —— "decide 绝不能握着锁"。

**背景（真机死机换来的）**：decide 会触发按键注入（标点替换、快捷补全都要
SendInput），而 SendInput 会在**同一个调用栈里**把注入的按键重新喂回低级钩子，
也就是同一个线程二次进入 ``_hook_proc``。

如果第一层还握着 ``self._lock``，第二次 acquire 同一个非可重入 Lock 就
**自己把自己锁死**。卡住的是消息线程，而钩子、托盘、热键、剪贴板全寄生在它
上面，于是表现为：

    "第一次标点替换之后，所有功能一起失效" + "退出时要卡很久"

这两个症状看着毫不相干，其实是同一次死锁的两面。所以这里钉死两条规则：

1. decide 在锁外调用（重入安全）；
2. 回调里的异常必须落日志，不许静默 pass（这次故障之所以难查，
   就是因为异常被吞得一点痕迹都没有）。
"""

from __future__ import annotations

import ctypes
import threading
import time
import unittest

from codepoint.platform_win import keyboard_hook as kh
from codepoint.platform_win.winapi import (
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    WM_KEYDOWN,
)

VK_A = 0x41
VK_COMMA = 0xBC


class _FakeMessageWindow:
    """同步执行的消息窗口替身（不真的建窗口）。"""

    def call(self, fn, timeout=None):
        return fn()


# ⚠ 必须持有结构体对象的引用：``byref(info)`` 拿到的指针不会因为 info 出了
#   作用域就失效，但**它指向的内存会**——函数一返回，结构体就被回收，
#   回调再 deref 读到的是垃圾（踩过：vkCode 读出来是 0，回调直接走异常分支，
#   测试表现为"decide 从未被调用"，查了半天差点去改产品代码）。
_KEEP: list = []


def _lp(vk: int, flags: int = 0) -> int:
    """构造一个 KBDLLHOOKSTRUCT 指针，等价于系统传给钩子的 lParam。"""
    info = KBDLLHOOKSTRUCT()
    info.vkCode = vk
    info.scanCode = 0
    info.flags = flags
    info.time = 0
    info.dwExtraInfo = 0
    _KEEP.append(info)
    return ctypes.cast(ctypes.byref(info), ctypes.c_void_p).value


def make_hook(decide) -> kh.KeyboardHook:
    return kh.KeyboardHook(_FakeMessageWindow(), decide)


class TestDecideIsReentrant(unittest.TestCase):
    """decide 里做注入（SendInput）时，回调必须还能再进来一次。"""

    def test_injecting_inside_decide_does_not_deadlock(self):
        hook = make_hook(lambda _event: False)
        seen: list = []

        def decide(event):
            seen.append(event.vk)
            if len(seen) == 1:
                # 模拟 SendInput 带来的重入：注入事件会再次进入同一个回调
                hook._proc(0, WM_KEYDOWN, _lp(VK_A, LLKHF_INJECTED))
            return True

        hook._decide = decide

        done = threading.Event()

        def run() -> None:
            hook._proc(0, WM_KEYDOWN, _lp(VK_COMMA))
            done.set()

        worker = threading.Thread(target=run, daemon=True, name="reentry")
        worker.start()
        self.assertTrue(
            done.wait(5.0),
            "回调死锁了 —— decide 大概又被放进 `with self._lock:` 里了。"
            "这条一挂，真机上就是'第一次替换后所有功能失效 + 退出卡顿'。",
        )
        self.assertEqual(seen, [VK_COMMA])

    def test_decide_holds_no_lock_while_running(self):
        """更直接的一条：decide 正在跑的时候，锁必须是可获取的。"""
        hook = make_hook(lambda _event: False)
        result: dict = {}

        def decide(_event):
            # 锁若在手，这里的 acquire(timeout) 会一直等到超时返回 False
            got = hook._lock.acquire(timeout=0.5)
            result["got"] = got
            result["ran"] = True
            try:
                return False
            finally:
                if got:
                    hook._lock.release()

        hook._decide = decide
        hook._proc(0, WM_KEYDOWN, _lp(VK_COMMA))
        self.assertTrue(result.get("ran"), "decide 根本没被调用")
        self.assertTrue(result.get("got"), "decide 执行期间不应持有 self._lock")


    def test_sanity_the_lock_really_is_not_reentrant(self):
        """反向验证：``self._lock`` 确实会自锁死。

        否则上面两条测试就是"假绿" —— 换个可重入锁（RLock）它们照样通过，
        但那只是把死锁从"卡死"变成"状态被重入污染"，一样不能接受。
        """
        lock = threading.Lock()
        outcome: list = []

        def inner() -> None:
            outcome.append(lock.acquire(timeout=0.3))

        def outer() -> None:
            with lock:
                inner()

        threading.Thread(target=outer, daemon=True).start()
        time.sleep(0.6)
        self.assertEqual(outcome, [False], "换成 RLock 了？那重入就不该被允许")


class TestCallbackNeverSwallowsExceptionsSilently(unittest.TestCase):
    """回调异常必须留下痕迹。"""

    def test_exception_is_logged(self):
        hook = make_hook(lambda _event: False)

        def boom(_event):
            raise RuntimeError("业务炸了")

        hook._decide = boom
        with self.assertLogs("codepoint.hook", level="ERROR"):
            hook._proc(0, WM_KEYDOWN, _lp(VK_COMMA))


if __name__ == "__main__":
    unittest.main()
