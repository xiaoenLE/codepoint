"""配色与字体 —— 全工程唯一写颜色字面量的地方。

一套"工具类软件"的克制配色：以蓝色为主色，绿色只用于"已开启"，
红色只用于删除，其余都是中性灰阶。所有颜色都集中在这里，
**其它模块一律 ``theme.XXX`` 引用**，不要再写 ``"#1B7CE8"`` 这种字面量 ——
想换主题只改这一个文件。
"""

from __future__ import annotations

# ------------------------------------------------------------------ 品牌色

BRAND_TOP = "#336BF6"          # logo 渐变上（蓝）
BRAND_BOTTOM = "#14A5C9"       # logo 渐变下（青）
BRAND = "#2F6BF6"              # 主品牌色

PRIMARY = "#1B7CE8"            # 主蓝：主按钮、导航选中、勾选框
PRIMARY_HOVER = "#1668C6"
PRIMARY_ACTIVE = "#1257A6"
PRIMARY_SOFT = "#EAF2FF"       # 主蓝的浅底（侧栏选中、状态徽章）
PRIMARY_SOFT_HOVER = "#DCEAFF"

DANGER = "#F5483B"             # 删除红叉
DANGER_HOVER = "#DC3627"
DANGER_SOFT = "#FDECEA"        # 危险色的浅底（垃圾桶按钮底色）

WARNING = "#F5891F"            # 取消/次要动作橙
WARNING_HOVER = "#DC760C"

SUCCESS = "#2FB344"            # 开关打开
SUCCESS_HOVER = "#28993A"      # 开关打开 + 鼠标悬停
SUCCESS_SOFT = "#E8F7EA"

SWITCH_OFF = "#D9DDE3"         # 开关关闭时的底槽
SWITCH_OFF_HOVER = "#C6CBD3"
SWITCH_DISABLED = "#E7EAEF"    # 开关被禁用（例如功能不支持）

# ------------------------------------------------------------------ 灰阶

BG = "#FFFFFF"                 # 主背景
SIDEBAR_BG = "#FFFFFF"
BORDER = "#E9ECF1"             # 分隔线/卡片边
BORDER_STRONG = "#D7DCE4"
HOVER = "#F4F6F9"              # 行/条目 hover
SUNKEN = "#F7F8FA"             # 输入框底（浅灰填充）
SUNKEN_HOVER = "#F1F3F7"

TEXT = "#1F2329"               # 主文字
TEXT_BODY = "#3D444D"
TEXT_MUTED = "#8A919C"         # 次要文字（路径、表头）
TEXT_FAINT = "#AEB4BD"         # 更弱的提示（空状态）
TEXT_ON_PRIMARY = "#FFFFFF"

# ------------------------------------------------------------------ 尺寸

TOPBAR_H = 54                  # 顶栏高度
TOPBAR_BG = "#FFFFFF"
CONTENT_MAX_WIDTH = 860        # 内容定宽居中：窗口拉宽时两侧留白，内容不散

CARD_RADIUS = 8
CARD_PAD = 12
ROW_HEIGHT = 40

# ------------------------------------------------------------------ 字体
#
# 用法：主窗口创建 Tk 之后调一次 ``theme.apply(root)``，
# 之后所有模块用 ``theme.F_BODY`` 这样的常量。**必须调 apply**，
# 否则 family 还是占位值，中文会掉成方框。

FAMILY_CANDIDATES = (
    "Microsoft YaHei UI",
    "微软雅黑",
    "Microsoft YaHei",
    "Segoe UI",
    "SimHei",
    "sans-serif",
)

_FAMILY = FAMILY_CANDIDATES[0]

F_BRAND = (_FAMILY, 15, "bold")     # 顶栏品牌名
F_NAV = (_FAMILY, 11)
F_TAB = (_FAMILY, 11)
F_H1 = (_FAMILY, 12, "bold")        # 页面标题（英文标点符号列表）
F_BODY = (_FAMILY, 10)
F_SMALL = (_FAMILY, 9)
F_CARD_NAME = (_FAMILY, 11, "bold")
F_CARD_PATH = (_FAMILY, 9)
F_BTN = (_FAMILY, 10)
F_EMPTY = (_FAMILY, 11)
F_ICON = (_FAMILY, 12)


def pick_family(root) -> str:
    """挑一个系统里真的存在的字体（tkinter 不认字体名会静默退化，很难查）。"""
    try:
        from tkinter import font as tkfont

        available = {f.lower() for f in tkfont.families(root)}
    except Exception:      # noqa: BLE001
        available = set()
    for name in FAMILY_CANDIDATES:
        if name.lower() in available:
            return name
    return "TkDefaultFont"


def apply(root) -> str:
    """选定字体并刷新所有字体常量。返回选中的字体名。"""
    global _FAMILY, F_BRAND, F_NAV, F_TAB, F_H1, F_BODY, F_SMALL
    global F_CARD_NAME, F_CARD_PATH, F_BTN, F_EMPTY, F_ICON
    _FAMILY = pick_family(root)
    F_BRAND = (_FAMILY, 14, "bold")
    F_NAV = (_FAMILY, 11)
    F_TAB = (_FAMILY, 11)
    F_H1 = (_FAMILY, 12, "bold")
    F_BODY = (_FAMILY, 10)
    F_SMALL = (_FAMILY, 9)
    F_CARD_NAME = (_FAMILY, 11, "bold")
    F_CARD_PATH = (_FAMILY, 9)
    F_BTN = (_FAMILY, 10)
    F_EMPTY = (_FAMILY, 11)
    F_ICON = (_FAMILY, 12)
    return _FAMILY


def family() -> str:
    return _FAMILY

