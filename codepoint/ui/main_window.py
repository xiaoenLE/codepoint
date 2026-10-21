"""主窗口 —— 顶部导航 + 单列内容。

    ┌────────────────────────────────────────────────────────────────┐
    │  ◔ 码点助手     程序列表  标点符号  快捷输入  常规设置  关于    [● 开] │ ← 顶栏
    ├────────────────────────────────────────────────────────────────┤
    │            ┌────────── 内容（定宽居中，最长 860）──────────┐          │
    │            │                                              │          │
    │            │            当前页面                          │          │
    │            │                                              │          │
    │            └──────────────────────────────────────────────┘          │
    │                                                        V1.0.0  │
    └────────────────────────────────────────────────────────────────┘

设计取向：
  · 导航放顶上、内容单列居中 —— 长列表（程序卡片、规则表）都在一列里，
    视线不用左右横跳；窗口拉宽时内容不会散架，只会两侧留白。
  · 导航项本身就是"页面"，没有二级 Tab，层级只有一层。
  · 全局开关常驻右上角，任何时候一眼能看到"现在是开还是关"。

它只负责"把页面摆好、把事件转发到位"，业务一律通过 ``AppFacade`` 调用。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, Optional, Tuple

from .. import APP_DISPLAY_NAME, __author__, __version__
from ..iconmaker import brand_png_base64
from . import theme, widgets
from ..core import window_geom as geom
from .bridge import UiBridge
from .card import CardLook, CardWindow
from .pages.about_page import AboutPage
from .pages.general_page import GeneralPage
from .pages.home_page import HomePage
from .pages.quick_page import QuickActionPage
from .pages.rules_page import PunctuationPage
from .ports import AppFacade
from .styles import install_styles

# 导航项 = 页面（顺序即顶栏顺序）
PAGE_HOME = "home"
PAGE_RULES = "rules"
PAGE_QUICK = "quick"
PAGE_GENERAL = "general"
PAGE_ABOUT = "about"

NAV_ITEMS = (
    (PAGE_HOME, "程序列表"),
    (PAGE_RULES, "标点符号"),
    (PAGE_QUICK, "快捷输入"),
    (PAGE_GENERAL, "常规设置"),
    (PAGE_ABOUT, "关于"),
)

_PUMP_INTERVAL_MS = 30
_SYNC_INTERVAL_MS = 4000


class MainWindow:
    """主窗口。构造后调用 ``run()`` 进入消息循环。"""

    def __init__(
        self,
        facade: AppFacade,
        bridge: UiBridge,
        *,
        positioner: Optional[Callable[[], Tuple[int, int]]] = None,
        icon_path: Optional[str] = None,
        start_hidden: bool = False,
        area_fn: Optional[Callable[[], Tuple[int, int, int, int]]] = None,
    ) -> None:
        self._facade = facade
        self._bridge = bridge
        self._positioner = positioner
        self._start_hidden = start_hidden
        # 主窗口该放哪儿：由组装根注入一个"当前工作区矩形"，居中和夹取
        # 用 core.window_geom 的纯函数算（可单测）。
        # **绝不能拿呼吸灯那个 positioner 来定位主窗口** —— 它返回的是
        # 光标/插入符坐标，窗口会跟着鼠标跑到屏幕角落，甚至被算到可视区外
        # （用户报障原话："到右下角，有时不能拖出来"）。
        self._area_fn = area_fn
        self._card: Optional[CardWindow] = None
        self._pump_job: Optional[str] = None
        self._sync_job: Optional[str] = None
        self._nav: Dict[str, _NavButton] = {}
        self._pages: Dict[str, tk.Frame] = {}
        self._page = PAGE_HOME

        self.root = tk.Tk()
        self.root.title(APP_DISPLAY_NAME)
        theme.apply(self.root)                 # 选字体（必须在建控件之前）
        self.root.configure(bg=theme.BG)
        self.root.minsize(720, 520)
        self._geometry()
        self._set_icon(icon_path)

        install_styles(self.root)
        self._logo = tk.PhotoImage(data=brand_png_base64(26))
        self._footer_logo = tk.PhotoImage(data=brand_png_base64(14))
        self._build()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-q>", lambda _e: self._facade.quit())

    # ------------------------------------------------------------ 窗口

    def _geometry(self) -> None:
        """把窗口放到**鼠标所在那块屏幕的正中间**，并夹进工作区。

        - 居中目标用 ``area_fn`` 问出来的显示器工作区，不用
          ``winfo_screenwidth``（那个只报主屏且不减任务栏）；
        - 位置和尺寸一律过一遍夹取：比工作区大就缩小，越界就拉回来。
          "窗口不见了 / 拖不出来"全是漏了这一步的结果。
        """
        w, h = 960, 680
        area = self._area()
        if area is not None:
            x, y, w, h = geom.centered(area, w, h)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            return
        # 兜底：按 tkinter 知道的那块屏居中（单显示器下也够用）
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = min(w, sw), min(h, sh)
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _area(self):
        """当前工作区矩形，问不到就 None。"""
        if self._area_fn is None:
            return None
        try:
            x, y, w, h = self._area_fn()
            return (int(x), int(y), int(w), int(h))
        except Exception:      # noqa: BLE001
            return None

    def ensure_visible(self) -> None:
        """窗口已经被挪到可视区外时，把它拉回工作区中间。

        ``show()`` 之前调一次即可 —— 用户自己拖到哪儿都尊重，
        只有当它**快看不见了**才出手（否则"拖不出来"只能靠结束进程解决）。
        """
        area = self._area()
        if area is None:
            return
        try:
            self.root.update_idletasks()
            x = int(self.root.winfo_x())
            y = int(self.root.winfo_y())
            w = max(geom.MIN_WIDTH, int(self.root.winfo_width()))
            h = max(geom.MIN_HEIGHT, int(self.root.winfo_height()))
        except Exception:      # noqa: BLE001
            return
        if geom.is_visible_enough(area, x, y, w, h):
            return
        nx, ny, nw, nh = geom.centered(area, w, h)
        self.root.geometry(f"{nw}x{nh}+{nx}+{ny}")

    def _set_icon(self, icon_path: Optional[str]) -> None:
        if not icon_path:
            return
        try:
            self.root.iconbitmap(icon_path)
        except Exception:      # noqa: BLE001
            pass

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        self._build_topbar()
        tk.Frame(self.root, bg=theme.BORDER, height=1).pack(fill="x")

        # 单列内容：宽度定死 + 居中，窗口再宽内容也不散
        self.stage = tk.Frame(self.root, bg=theme.BG, width=theme.CONTENT_MAX_WIDTH)
        self.stage.pack(side="top", fill="both", expand=True)
        self.stage.pack_propagate(False)
        self._column = tk.Frame(self.root, bg=theme.BG, width=theme.CONTENT_MAX_WIDTH)
        self._column.place(in_=self.stage, relx=0.5, rely=0, anchor="n",
                           width=theme.CONTENT_MAX_WIDTH, relheight=1.0)

        self._footer = _Footer(self.root, logo=self._footer_logo)
        self._footer.pack(side="bottom", fill="x")

        self._build_pages()
        self._sync_switch()
        self.show_page(PAGE_HOME)

    def _build_topbar(self) -> None:
        bar = tk.Frame(self.root, bg=theme.TOPBAR_BG, height=theme.TOPBAR_H)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # 左：品牌
        brand = tk.Frame(bar, bg=theme.TOPBAR_BG, cursor="hand2")
        brand.pack(side="left", padx=(16, 22))
        logo = tk.Canvas(brand, width=26, height=26, bg=theme.TOPBAR_BG,
                         highlightthickness=0, bd=0)
        logo.pack(side="left")
        logo.create_image(13, 13, image=self._logo)
        name = tk.Label(brand, text=APP_DISPLAY_NAME, bg=theme.TOPBAR_BG,
                        fg=theme.TEXT, font=theme.F_BRAND)
        name.pack(side="left", padx=(8, 0))
        for w in (brand, logo, name):
            w.bind("<Button-1>", lambda _e: self.show_page(PAGE_HOME))

        # 中：导航
        self._nav_holder = tk.Frame(bar, bg=theme.TOPBAR_BG)
        self._nav_holder.pack(side="left", fill="y")
        for key, text in NAV_ITEMS:
            btn = _NavButton(self._nav_holder, text=text,
                             command=lambda k=key: self.show_page(k))
            btn.pack(side="left", padx=(0, 4))
            self._nav[key] = btn

        # 右：全局开关
        right = tk.Frame(bar, bg=theme.TOPBAR_BG)
        right.pack(side="right", padx=(0, 18))
        self.switch = widgets.ToggleSwitch(
            right, value=self._facade.is_global_enabled(),
            command=self._on_global_switch, bg=theme.TOPBAR_BG)
        self.switch.pack(side="right")
        self.switch_label = tk.Label(right, text="", bg=theme.TOPBAR_BG,
                                     fg=theme.TEXT_MUTED, font=theme.F_BODY)
        self.switch_label.pack(side="right", padx=(0, 8))

    def _build_pages(self) -> None:
        self._pages = {
            PAGE_HOME: HomePage(self._column, self._facade),
            PAGE_RULES: PunctuationPage(self._column, self._facade,
                                        title="标点符号表"),
            PAGE_QUICK: QuickActionPage(self._column, self._facade,
                                        title="快捷输入列表"),
            PAGE_GENERAL: GeneralPage(self._column, self._facade),
            PAGE_ABOUT: AboutPage(self._column, self._facade),
        }
        for page in self._pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            page.place_forget()

    # ------------------------------------------------------------ 页面切换

    def show_page(self, page: str) -> None:
        if page not in self._pages:
            page = PAGE_HOME
        self._page = page
        for key, btn in self._nav.items():
            btn.set_active(key == page)

        target = self._pages[page]
        for widget in self._pages.values():
            widget.place_forget()
        target.place(relx=0, rely=0, relwidth=1, relheight=1)
        if hasattr(target, "refresh"):
            try:
                target.refresh()
            except Exception:      # noqa: BLE001
                pass

        # 离开主页就停掉它的前台轮询（省 CPU）
        home = self._pages.get(PAGE_HOME)
        if isinstance(home, HomePage) and page != PAGE_HOME:
            home.stop()

    def refresh_all(self) -> None:
        for page in self._pages.values():
            if hasattr(page, "refresh"):
                try:
                    page.refresh()      # noqa: PERF203
                except Exception:      # noqa: BLE001
                    pass
        self._sync_switch()

    # ------------------------------------------------------------ 开关

    def _on_global_switch(self, value: bool) -> None:
        self._facade.set_global_enabled(value)
        self._facade.get_settings().global_enabled = value
        self._facade.save()
        self._sync_switch()

    def _sync_switch(self) -> None:
        value = self._facade.is_global_enabled()
        self.switch.set_value(value)
        self.switch_label.configure(text="已开启" if value else "已暂停",
                                    fg=theme.SUCCESS if value else theme.TEXT_MUTED)

    # ------------------------------------------------------------ 生命周期

    def run(self) -> None:
        self._pump()
        self._periodic_sync()
        if self._start_hidden or self._facade.get_settings().start_hidden:
            self.root.withdraw()
        self.root.mainloop()

    def show(self) -> None:
        """把窗口显示出来并顶到最前。

        用户可能通过三种方式把它叫回来：点托盘图标、再双击一次程序、
        点托盘菜单的"打开"。三种都走这里。
        """
        try:
            self.ensure_visible()          # 先确认它还看得见，再谈置顶
            self.root.deiconify()
            if self.root.state() == "iconic":
                self.root.state("normal")
            self.root.lift()
            # Windows 会拦住"后台进程抢焦点"，短暂置顶一下最稳（200ms 后撤掉，
            # 不然后面正常切窗口时它会一直压着别人）
            self.root.attributes("-topmost", True)
            self.root.after(200, self._drop_topmost)
            self.root.focus_force()
        except Exception:      # noqa: BLE001
            pass

    def _drop_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except Exception:      # noqa: BLE001
            pass

    def hide(self) -> None:
        try:
            self.root.withdraw()
        except Exception:      # noqa: BLE001
            pass

    def destroy(self) -> None:
        for job in (self._pump_job, self._sync_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:      # noqa: BLE001
                    pass
        home = self._pages.get(PAGE_HOME)
        if isinstance(home, HomePage):
            home.stop()
        try:
            self.root.destroy()
        except Exception:      # noqa: BLE001
            pass

    def _pump(self) -> None:
        for event in self._bridge.poll():
            try:
                self._handle_event(event)
            except Exception:      # noqa: BLE001
                pass
        try:
            self._pump_job = self.root.after(_PUMP_INTERVAL_MS, self._pump)
        except Exception:      # noqa: BLE001
            self._pump_job = None

    def _periodic_sync(self) -> None:
        """定期把界面开关和后台实际状态对齐（例如热键改了开关）。"""
        self._sync_switch()
        try:
            self._sync_job = self.root.after(_SYNC_INTERVAL_MS, self._periodic_sync)
        except Exception:      # noqa: BLE001
            self._sync_job = None

    def _handle_event(self, event) -> None:
        kind = event.kind
        if kind == UiBridge.CARD_SHOW:
            self._show_card(event.get("card"))
        elif kind == UiBridge.CARD_STREAM:
            if self._card is not None:
                self._card.update_stream(
                    event.get("body", ""),
                    label=event.get("label", ""),
                )
        elif kind == UiBridge.CARD_HIDE:
            self._hide_card()
        elif kind == UiBridge.SETTINGS_CHANGED:
            self.refresh_all()
        elif kind == UiBridge.SHOW_WINDOW:
            # 托盘图标、托盘菜单、或者第二次启动的实例发来的：
            # 把窗口显示出来，别让用户以为程序没了
            self.show()
        elif kind == UiBridge.HIDE_WINDOW:
            self.hide()
        elif kind == UiBridge.QUIT:
            self.destroy()

    # ------------------------------------------------------------ 补全预览窗

    def _show_card(self, card) -> None:
        if card is None:
            return
        settings = self._facade.get_settings()
        # ⚠ 这里**不能**再看「Tab 补全提示（呼吸灯）」开关 —— 它只管快捷输入的
        # 提示（已在 IndicatorNotifier.show 里挡掉）。以前这里也挡一道，结果
        # 关掉呼吸灯时 AI 预览窗一起消失，而结果还挂在那等 Tab —— 用户看到
        # "关了就什么都不显示"，按个键这份结果还作废（用户报障）。
        # 要不要弹预览由「显示预览窗」（ai_card_enabled）在 app 里决定。
        if self._card is None:
            self._card = CardWindow(self.root, positioner=self._positioner,
                                    on_expire=self._on_card_expired)
        # 外观每次显示都重读一遍：用户在设置页改完颜色/透明度，
        # 下一次触发就该看到新样子，不用重启。
        self._card.set_look(CardLook.from_settings(settings))
        self._card.show_with(card)

    def _hide_card(self) -> None:
        if self._card is not None:
            self._card.hide()

    def _on_card_expired(self) -> None:
        """倒计时到点了：把挂起的结果作废，别让它还能被 Tab 插进去。"""
        try:
            self._facade.cancel_pending()
        except Exception:      # noqa: BLE001
            pass

    # ------------------------------------------------------------ 关闭

    def _on_close(self) -> None:
        if self._facade.get_settings().minimize_to_tray:
            self._facade.hide_to_tray()
            # 一定要说一声"我还活着，在托盘里" —— 否则用户看到窗口消失、
            # 又找不到托盘图标（Win11 默认把新图标折叠起来），
            # 只能再双击一次程序，然后被告知"已经在运行了"，彻底懵
            self._facade.notify("码点助手仍在运行",
                                "窗口已收到托盘区。再双击一次本程序，"
                                "或点托盘图标即可重新打开。")
        else:
            self._facade.quit()


# ------------------------------------------------------------------ 部件


class _NavButton(tk.Frame):
    """顶栏导航项。选中：深色字 + 底部蓝色横线；悬停：浅灰底。"""

    PAD_X = 14

    def __init__(self, master, *, text: str, command: Callable[[], None]) -> None:
        super().__init__(master, bg=theme.TOPBAR_BG, cursor="hand2")
        self._label = tk.Label(self, text=text, bg=theme.TOPBAR_BG,
                               fg=theme.TEXT_MUTED, font=theme.F_TAB)
        self._label.pack(padx=self.PAD_X, expand=True)
        self._underline = tk.Frame(self, bg=theme.PRIMARY, height=2)
        self._active = False
        for w in (self, self._label):
            w.bind("<Button-1>", lambda _e: command())
            w.bind("<Enter>", lambda _e: self._hover(True))
            w.bind("<Leave>", lambda _e: self._hover(False))

    def _hover(self, on: bool) -> None:
        if self._active:
            return
        self._label.configure(fg=theme.TEXT_BODY if on else theme.TEXT_MUTED)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._label.configure(fg=theme.TEXT if active else theme.TEXT_MUTED,
                              font=theme.F_TAB)
        if active:
            self._underline.pack(fill="x", side="bottom")
        else:
            self._underline.pack_forget()


class _Footer(tk.Frame):
    """底部一行：版本 + 一句话说明。"""

    def __init__(self, master, *, logo: tk.PhotoImage) -> None:
        super().__init__(master, bg=theme.BG, height=30)
        row = tk.Frame(self, bg=theme.BG)
        row.pack(pady=(0, 8))
        holder = tk.Canvas(row, width=14, height=14, bg=theme.BG,
                           highlightthickness=0, bd=0)
        holder.pack(side="left", padx=(0, 6))
        holder.create_image(7, 7, image=logo)
        tk.Label(row, text=f"V{__version__}   ·   由 {__author__} 制作",
                 bg=theme.BG, fg=theme.TEXT_MUTED,
                 font=theme.F_SMALL).pack(side="left")


__all__ = ["MainWindow", "NAV_ITEMS", "PAGE_HOME", "PAGE_RULES", "PAGE_QUICK",
           "PAGE_GENERAL", "PAGE_ABOUT"]
