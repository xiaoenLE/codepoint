"""数据模型：纯 dataclass，不含任何行为副作用，可安全序列化。

三张表各管一件事：
    AppItem          受管程序（生效范围）
    PunctuationRule  一条"中文标点 → 英文标点"映射
    QuickAction      一条快捷输入规则
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

# ---------------------------------------------------------------- 常量

DETECT_WORD = "word"   # 整词检测：匹配后弹预览卡，按 Tab 补全
DETECT_CHAR = "char"   # 单字符检测：匹配到立即触发

#: 配置结构版本。**只在字段语义变化、需要迁移旧配置时 +1**；
#: 迁移逻辑在 config.merge_with_defaults 里，靠这个数字保证只跑一次
#: （否则用户手动改过的设置会在每次启动时被迁移逻辑改回去）。
#:
#: v5：删掉「前缀 + 参数（AI）」这条触发方式（`//ai xxx` + Tab）。
#:     它和「选中一段 + 热键」功能重复，而且致命伤是中文提示词只能到拼音
#:     （钩子看不到输入法候选）。留着的旧规则要清掉，否则界面上会挂着
#:     一条永远不触发的死规则。
SCHEMA_VERSION = 5

DETECT_LABELS = {
    DETECT_WORD: "整词检测",
    DETECT_CHAR: "单字符",
}

# 生效范围：决定「程序列表」是白名单还是排除名单
MATCH_WHITELIST = "whitelist"   # 仅列表内的程序生效（默认，最安全）
MATCH_ALL = "all"               # 所有程序都生效，列表里的程序被排除

MATCH_LABELS = {
    MATCH_WHITELIST: "仅列表内的程序",
    MATCH_ALL: "所有程序（列表作为排除）",
}

# 生成好的内容怎么塞进去（见 Settings.ai_insert_mode）
INSERT_PASTE = "paste"    # 剪贴板 + Ctrl+V，一个动作搞定（默认）
INSERT_TYPE = "type"      # 逐字符注入，等于真人一个个敲

INSERT_MODES = (INSERT_PASTE, INSERT_TYPE)

INSERT_LABELS = {
    INSERT_PASTE: "整段粘贴（快，推荐）",
    INSERT_TYPE: "逐字符写入（兼容老软件）",
}


def _get(d: Dict[str, Any], key: str, default: Any) -> Any:
    """宽容取值：缺键、None 都回落到默认值。"""
    v = d.get(key, default)
    return default if v is None else v


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _color(value: Any, default: str) -> str:
    """只认 ``#RRGGBB``。写错了回默认值 —— 界面颜色配错不该让程序起不来。"""
    text = str(value or "").strip()
    return text if _COLOR_RE.match(text) else default


# ---------------------------------------------------------------- 白名单条目


@dataclass
class AppItem:
    """一个受管的"目标程序"。

    在 ``MATCH_WHITELIST`` 模式下：只有列表里的程序才生效。
    在 ``MATCH_ALL`` 模式下：列表变成"排除名单"，列表里的程序不生效。
    """

    process_name: str = ""          # 可执行文件名，如 tiaportal.exe（不区分大小写）
    window_title: str = ""          # 可选：窗口标题包含的子串（不区分大小写）
    path: str = ""                  # 可选：完整路径（展示用，也参与同名校验）
    display_name: str = ""          # 可选：界面上显示的友好名（空则显示进程名）
    enabled: bool = True

    def matches(self, process_name: str, window_title: str = "") -> bool:
        """匹配规则：
        - process_name 为空 → 视为通配（此时必须靠 window_title 命中，否则不匹配）
        - process_name 非空 → 必须完全相等（忽略大小写）
        - window_title 非空 → 标题需包含该子串（忽略大小写）
        """
        if not self.enabled:
            return False
        pn = (self.process_name or "").strip().lower()
        wt = (self.window_title or "").strip().lower()
        if not pn and not wt:
            return False
        if pn and pn != (process_name or "").strip().lower():
            return False
        if wt and wt not in (window_title or "").lower():
            return False
        return True

    def title(self) -> str:
        """界面上这行显示什么名字。"""
        return (self.display_name or self.process_name or self.window_title or "(未命名)").strip()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AppItem":
        return cls(
            process_name=str(_get(d, "process_name", "")),
            window_title=str(_get(d, "window_title", "")),
            path=str(_get(d, "path", "")),
            display_name=str(_get(d, "display_name", "")),
            enabled=bool(_get(d, "enabled", True)),
        )


# ---------------------------------------------------------------- 标点映射


@dataclass
class PunctuationRule:
    """一条"全角中文标点 → 半角英文标点"映射。"""

    fullwidth: str = ""     # 中文全角字符，如 '，'
    halfwidth: str = ""     # 英文半角字符，如 ','
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PunctuationRule":
        return cls(
            fullwidth=str(_get(d, "fullwidth", "")),
            halfwidth=str(_get(d, "halfwidth", "")),
            enabled=bool(_get(d, "enabled", True)),
        )


# ---------------------------------------------------------------- 快捷输入


@dataclass
class QuickAction:
    """一条快捷输入规则：键入 trigger 后自动补全为 content。"""

    trigger: str = ""                        # 检测字符串（必须是可键入的英文）
    detect_mode: str = DETECT_WORD           # DETECT_WORD / DETECT_CHAR
    content: str = ""                        # 补全内容
    enabled: bool = True

    def normalized_trigger(self) -> str:
        return self.trigger or ""

    def backspace_count(self) -> int:
        """替换掉 trigger 需要发送多少次退格。"""
        return len(self.normalized_trigger())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QuickAction":
        mode = str(_get(d, "detect_mode", DETECT_WORD))
        if mode not in (DETECT_WORD, DETECT_CHAR):
            mode = DETECT_WORD
        return cls(
            trigger=str(_get(d, "trigger", "")),
            detect_mode=mode,
            content=str(_get(d, "content", "")),
            enabled=bool(_get(d, "enabled", True)),
        )


# ---------------------------------------------------------------- 设置聚合


@dataclass
class Settings:
    """全部可持久化配置。"""

    # —— 配置结构版本（写进 settings.json，用于一次性迁移）——
    schema: int = SCHEMA_VERSION

    # —— 总开关 ——
    global_enabled: bool = True
    enable_clipboard_replace: bool = False   # 剪贴板净化默认**关**（宁少动一处是一处）
    enable_auto_startup: bool = True         # 真实状态以注册表为准，启动时会校准（见 app.py）
    start_hidden: bool = True                # 自启后直接收进托盘

    # —— 标点替换 ——
    punctuation_rules: List[PunctuationRule] = field(default_factory=list)
    require_ime: bool = False                # 仅当检测到输入法处于中文/全角状态时才替换
    smart_guard_compose: bool = True         # 组字/候选列表打开时不介入（强烈建议开）

    # —— 快捷输入 ——
    quick_actions: List[QuickAction] = field(default_factory=list)
    quick_action_enabled: bool = True
    indicator_enabled: bool = True           # Tab 呼吸灯

    # —— AI 补全（``//ai 提示词`` + Tab）——
    # 默认 provider 是 mock：返回一段写死的示例代码，用来验证链路。
    # 目的是让"敲 → Tab → 生成中 → 代码落进去"这条链路可以先跑通，
    # 等确认没毛病，再把地址改成真模型（llama.cpp / Ollama / CodeGeeX / DeepSeek）。
    ai_enabled: bool = True
    ai_provider: str = "mock"                # mock / openai（OpenAI 兼容协议）
    ai_base_url: str = "http://127.0.0.1:8080/v1"
    ai_model: str = "qwen2.5-coder-1.5b"
    ai_api_key: str = ""                     # 明文存在 settings.json 里，本机自用
    #: 请求超时（秒）。为什么默认 60 而不是 8：推理模型（MiMo、deepseek-flash、r1）
    #: 首字延迟实测能到 18~24 秒，8 秒必被掐断 —— 表现成"连不上"，其实接口是通的。
    ai_timeout: float = 60.0
    ai_temperature: float = 0.2              # 代码补全要确定性，别调高
    ai_max_tokens: int = 400
    #: 关掉推理模型的"思考"。**默认开** —— 实测 MiMo 补全任务：不关思考
    #: 首字 9.7s，关掉后 1.7s（快 5 倍以上）。补全要的是"接着写几行"，
    #: 不需要模型先想 300 字。发的是 `thinking: {"type": "disabled"}`；
    #: 服务端不认识这个字段时自动去掉重试，不影响老服务。
    ai_disable_thinking: bool = True
    #: 「选中一段 + 热键」那条路。空串表示不注册。
    #: 为什么需要它：钩子只能看到按键，中文提示词到不了程序（看到的是拼音），
    #: 所以中文只能靠"把话写在选区里，让程序去复制"。
    ai_hotkey: str = "Ctrl+Alt+G"

    # —— 补全预览窗（按热键/命中触发后浮出来的那块）——
    # 关掉的话：生成完直接插进去，不给你确认的机会。默认开。
    ai_card_enabled: bool = True
    ai_card_bg: str = "#1B1F27"       # 背景色 #RRGGBB
    ai_card_fg: str = "#E8ECF4"       # 文字色
    ai_card_alpha: float = 0.94       # 整体不透明度 0.2 ~ 1.0
    ai_card_font_size: int = 11
    ai_card_max_lines: int = 14       # 正文最多显示几行，超出折叠
    ai_card_width: int = 460          # 最大宽度（像素），长行会截断
    #: 预览窗自动关闭的秒数；0 = 一直等（只在按 Tab/Esc 时关）。
    #: 倒计时会显示在窗口标题栏右侧，用户随时可以按 Esc 提前关掉。
    ai_card_timeout: float = 15.0

    # —— 上下文 ——
    #: 没有选中内容时，用「光标前 N 行」当上下文去预测接下来写什么。
    #: 抓法是 Shift+Up 选中 → Ctrl+C → Right 归位（不靠 UIA，CODESYS 也能用）。
    #: 没选区时向上抓几行当上下文。0 = 关掉预测。
    #: 为什么是 6：补全要的是"你正在写的这一段"，不是整个文件 —— 行数越多
    #: 越慢（prefill 时间随上下文变长），而且模型容易跑偏去改更早的代码。
    #: 上限 30：再往上就该用「选中一段」那条路了。
    ai_context_lines: int = 6

    # —— FIM（Fill-in-the-Middle）——
    #: 打开后不只看"光标之前"，还会抓"光标之后"一起发给模型 —— 模型知道
    #: 后面已经写了什么，就不会把那些内容再编一遍。1.5B 级小模型上差别很大：
    #: 只给前半段会自己另起一段跑飞，给了后半段就精确补上缺的那行。
    #: 抓法是 Shift+Down → Ctrl+C → Left 归位，和抓上文一样靠剪贴板。
    ai_fim_enabled: bool = True
    #: server（服务端 suffix 参数，快）/ token（手拼 FIM 特殊 token，兼容好）/ auto
    ai_fim_strategy: str = "auto"
    #: FIM 特殊 token 的族。auto = 按模型名猜（qwen / deepseek / codellama …）。
    #: 猜错不会报错，只会补出空行或者跑飞，所以留了手动指定。
    ai_fim_tokens: str = "auto"
    #: 抓光标后面几行当 suffix。0 = 不抓（等于退回只看上文的老行为）。
    #: 默认 3：够覆盖"这两行后面就是 return / END_IF"这种场景，再多人也看不清，
    #: 而且每多抓一行就多一次按键+复制，是要用户等着的。
    ai_suffix_lines: int = 3
    #: 发给模型的上下文**字符**上限。光限行数不够 —— 一行超长的 IO 映射语句
    #: 能把 6 行撑到几千字符，prefill 明显变慢，小模型还容易忘记该补哪一行。
    #: 截断方向都朝光标收敛：prefix 留尾部，suffix 留头部。
    ai_prefix_max_chars: int = 1200
    ai_suffix_max_chars: int = 600

    #: 生成好的内容怎么塞进编辑器：
    #:
    #: · ``paste``（默认）—— 塞剪贴板 + 一次 Ctrl+V，然后把原剪贴板还原。
    #:   快，而且**只有一个动作**：自动补全、自动缩进、代码索引那些功能来不及
    #:   在中间插一脚（用户报障原话："有的软件他写入会索引其他的，会导致写入
    #:   不正常"）。
    #: · ``type`` —— 逐字符 Unicode 注入，跟真人一个键一个键敲一样。
    #:   兼容性最好（不碰剪贴板），但在"边敲边联想"的编辑器里容易被打断。
    ai_insert_mode: str = INSERT_PASTE

    #: AI 补全要不要**也**遵守「生效范围」（默认遵守）。
    #:
    #: 生效范围本来是给"逐键标点替换"定的；AI 热键是全局注册的，不管它的话，
    #: 在没进列表的程序里照样能按热键、照样往人家窗口里抓内容/插代码
    #: （用户报障："设置生效范围是列表内程序了，但是在没有的程序里面还是可以
    #: 使用快捷键调用 ai"）。
    #:
    #: 关掉 = AI 不看生效范围，哪儿都能用（比如临时想在别的编辑器里补一段）。
    #: 但"窗口切走了"和"焦点在菜单上"这两道闸**始终有效**，不受这里影响。
    ai_scope_limited: bool = True

    # —— 程序列表 / 生效范围 ——
    app_items: List[AppItem] = field(default_factory=list)
    match_mode: str = MATCH_WHITELIST        # whitelist（默认） / all

    # —— 热键（可读字符串，如 "Ctrl+Alt+A"，空串表示不注册）——
    toggle_hotkey: str = "Ctrl+Alt+A"

    # —— 界面 ——
    minimize_to_tray: bool = True

    # ------------------------------------------------------------ 归一化

    def normalize(self) -> "Settings":
        """把"用户可能填错的值"夹回合法范围。

        界面输入框能填出 0、负数、0.1 这类值 —— 与其在保存时弹个错，
        不如静默夹到最近的有效值。
        """
        # AI 参数：界面输入框能填出 0、负数、99999，一律夹回能用的范围。
        # 超时下限 1s（再短必然失败，纯属骗自己），上限 60s（钩子那头等着呢）。
        try:
            timeout = float(self.ai_timeout)
        except (TypeError, ValueError):
            timeout = 8.0
        self.ai_timeout = max(1.0, min(timeout, 300.0))

        try:
            temperature = float(self.ai_temperature)
        except (TypeError, ValueError):
            temperature = 0.2
        self.ai_temperature = max(0.0, min(temperature, 2.0))

        try:
            max_tokens = int(self.ai_max_tokens)
        except (TypeError, ValueError):
            max_tokens = 400
        self.ai_max_tokens = max(32, min(max_tokens, 4096))

        self.ai_provider = (self.ai_provider or "mock").strip().lower()
        if self.ai_provider not in ("mock", "openai"):
            self.ai_provider = "mock"
        self.ai_base_url = (self.ai_base_url or "").strip()
        self.ai_model = (self.ai_model or "").strip()
        self.ai_api_key = (self.ai_api_key or "").strip()
        self.ai_hotkey = (self.ai_hotkey or "").strip()

        # 预览窗外观：颜色必须是 #RRGGBB（tkinter 认这个格式，也认颜色名，
        # 但颜色名太杂、存进 json 不好看），写错了就直接回默认值而不是崩在界面上。
        self.ai_card_bg = _color(self.ai_card_bg, "#1B1F27")
        self.ai_card_fg = _color(self.ai_card_fg, "#E8ECF4")
        try:
            alpha = float(self.ai_card_alpha)
        except (TypeError, ValueError):
            alpha = 0.94
        self.ai_card_alpha = max(0.2, min(alpha, 1.0))
        try:
            size = int(self.ai_card_font_size)
        except (TypeError, ValueError):
            size = 11
        self.ai_card_font_size = max(8, min(size, 24))
        try:
            lines = int(self.ai_card_max_lines)
        except (TypeError, ValueError):
            lines = 14
        self.ai_card_max_lines = max(3, min(lines, 60))
        try:
            width = int(self.ai_card_width)
        except (TypeError, ValueError):
            width = 460
        self.ai_card_width = max(260, min(width, 1000))

        try:
            timeout = float(self.ai_card_timeout)
        except (TypeError, ValueError):
            timeout = 15.0
        self.ai_card_timeout = max(0.0, min(timeout, 300.0))

        try:
            ctx_lines = int(self.ai_context_lines)
        except (TypeError, ValueError):
            ctx_lines = 12
        self.ai_context_lines = max(0, min(ctx_lines, 30))

        # FIM：策略和 token 族都是有限枚举，写错了就回 auto —— 宁可让它自己猜，
        # 也别把一个不认识的值带到请求里去（表现成"补全是空的"，很难查）。
        self.ai_fim_enabled = bool(self.ai_fim_enabled)
        self.ai_fim_strategy = (self.ai_fim_strategy or "auto").strip().lower()
        if self.ai_fim_strategy not in ("auto", "server", "token"):
            self.ai_fim_strategy = "auto"
        self.ai_fim_tokens = (self.ai_fim_tokens or "auto").strip().lower()
        if self.ai_fim_tokens not in (
                "auto", "qwen", "codegemma", "deepseek", "codellama", "starcoder"):
            self.ai_fim_tokens = "auto"
        try:
            suffix_lines = int(self.ai_suffix_lines)
        except (TypeError, ValueError):
            suffix_lines = 3
        # 上限 10：抓下文是"按着 Shift 一行行选"，多了又慢又没意义
        self.ai_suffix_lines = max(0, min(suffix_lines, 10))
        try:
            prefix_chars = int(self.ai_prefix_max_chars)
        except (TypeError, ValueError):
            prefix_chars = 1200
        # 下限 200：再小连"前两行"都装不下，补出来必然跑偏
        self.ai_prefix_max_chars = max(200, min(prefix_chars, 8000))
        try:
            suffix_chars = int(self.ai_suffix_max_chars)
        except (TypeError, ValueError):
            suffix_chars = 600
        self.ai_suffix_max_chars = max(0, min(suffix_chars, 4000))
        mode = str(self.ai_insert_mode or "").strip().lower()
        self.ai_insert_mode = mode if mode in INSERT_MODES else INSERT_PASTE
        return self

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "global_enabled": self.global_enabled,
            "enable_clipboard_replace": self.enable_clipboard_replace,
            "enable_auto_startup": self.enable_auto_startup,
            "start_hidden": self.start_hidden,
            "punctuation_rules": [r.to_dict() for r in self.punctuation_rules],
            "require_ime": self.require_ime,
            "smart_guard_compose": self.smart_guard_compose,
            "quick_actions": [q.to_dict() for q in self.quick_actions],
            "quick_action_enabled": self.quick_action_enabled,
            "indicator_enabled": self.indicator_enabled,
            "ai_enabled": self.ai_enabled,
            "ai_provider": self.ai_provider,
            "ai_base_url": self.ai_base_url,
            "ai_model": self.ai_model,
            "ai_api_key": self.ai_api_key,
            "ai_timeout": self.ai_timeout,
            "ai_temperature": self.ai_temperature,
            "ai_max_tokens": self.ai_max_tokens,
            "ai_disable_thinking": self.ai_disable_thinking,
            "ai_hotkey": self.ai_hotkey,
            "ai_card_enabled": self.ai_card_enabled,
            "ai_card_bg": self.ai_card_bg,
            "ai_card_fg": self.ai_card_fg,
            "ai_card_alpha": self.ai_card_alpha,
            "ai_card_font_size": self.ai_card_font_size,
            "ai_card_max_lines": self.ai_card_max_lines,
            "ai_card_width": self.ai_card_width,
            "ai_card_timeout": self.ai_card_timeout,
            "ai_context_lines": self.ai_context_lines,
            "ai_fim_enabled": self.ai_fim_enabled,
            "ai_fim_strategy": self.ai_fim_strategy,
            "ai_fim_tokens": self.ai_fim_tokens,
            "ai_suffix_lines": self.ai_suffix_lines,
            "ai_prefix_max_chars": self.ai_prefix_max_chars,
            "ai_suffix_max_chars": self.ai_suffix_max_chars,
            "ai_insert_mode": self.ai_insert_mode,
            "ai_scope_limited": self.ai_scope_limited,
            "app_items": [a.to_dict() for a in self.app_items],
            "match_mode": self.match_mode,
            "toggle_hotkey": self.toggle_hotkey,
            "minimize_to_tray": self.minimize_to_tray,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Settings":
        s = cls()
        try:
            s.schema = int(_get(d, "schema", SCHEMA_VERSION))
        except (TypeError, ValueError):
            s.schema = SCHEMA_VERSION
        s.global_enabled = bool(_get(d, "global_enabled", s.global_enabled))
        s.enable_clipboard_replace = bool(_get(d, "enable_clipboard_replace", s.enable_clipboard_replace))
        s.enable_auto_startup = bool(_get(d, "enable_auto_startup", s.enable_auto_startup))
        s.start_hidden = bool(_get(d, "start_hidden", s.start_hidden))
        s.require_ime = bool(_get(d, "require_ime", s.require_ime))
        s.smart_guard_compose = bool(_get(d, "smart_guard_compose", s.smart_guard_compose))
        s.quick_action_enabled = bool(_get(d, "quick_action_enabled", s.quick_action_enabled))
        s.indicator_enabled = bool(_get(d, "indicator_enabled", s.indicator_enabled))
        s.ai_enabled = bool(_get(d, "ai_enabled", s.ai_enabled))
        s.ai_provider = str(_get(d, "ai_provider", s.ai_provider))
        s.ai_base_url = str(_get(d, "ai_base_url", s.ai_base_url))
        s.ai_model = str(_get(d, "ai_model", s.ai_model))
        s.ai_api_key = str(_get(d, "ai_api_key", s.ai_api_key))
        s.ai_disable_thinking = bool(_get(d, "ai_disable_thinking", s.ai_disable_thinking))
        try:
            s.ai_timeout = float(_get(d, "ai_timeout", s.ai_timeout))
        except (TypeError, ValueError):
            s.ai_timeout = 60.0
        try:
            s.ai_temperature = float(_get(d, "ai_temperature", s.ai_temperature))
        except (TypeError, ValueError):
            s.ai_temperature = 0.2
        try:
            s.ai_max_tokens = int(_get(d, "ai_max_tokens", s.ai_max_tokens))
        except (TypeError, ValueError):
            s.ai_max_tokens = 400
        s.ai_hotkey = str(_get(d, "ai_hotkey", s.ai_hotkey))
        s.ai_card_enabled = bool(_get(d, "ai_card_enabled", s.ai_card_enabled))
        s.ai_card_bg = str(_get(d, "ai_card_bg", s.ai_card_bg))
        s.ai_card_fg = str(_get(d, "ai_card_fg", s.ai_card_fg))
        try:
            s.ai_card_alpha = float(_get(d, "ai_card_alpha", s.ai_card_alpha))
        except (TypeError, ValueError):
            s.ai_card_alpha = 0.94
        try:
            s.ai_card_font_size = int(_get(d, "ai_card_font_size", s.ai_card_font_size))
        except (TypeError, ValueError):
            s.ai_card_font_size = 11
        try:
            s.ai_card_max_lines = int(_get(d, "ai_card_max_lines", s.ai_card_max_lines))
        except (TypeError, ValueError):
            s.ai_card_max_lines = 14
        try:
            s.ai_card_width = int(_get(d, "ai_card_width", s.ai_card_width))
        except (TypeError, ValueError):
            s.ai_card_width = 460
        try:
            s.ai_card_timeout = float(_get(d, "ai_card_timeout", s.ai_card_timeout))
        except (TypeError, ValueError):
            s.ai_card_timeout = 15.0
        try:
            s.ai_context_lines = int(_get(d, "ai_context_lines", s.ai_context_lines))
        except (TypeError, ValueError):
            s.ai_context_lines = 6
        # FIM：老 settings.json 里没有这几个键，用 dataclass 的默认值即可
        s.ai_fim_enabled = bool(_get(d, "ai_fim_enabled", s.ai_fim_enabled))
        s.ai_fim_strategy = str(_get(d, "ai_fim_strategy", s.ai_fim_strategy))
        s.ai_fim_tokens = str(_get(d, "ai_fim_tokens", s.ai_fim_tokens))
        try:
            s.ai_suffix_lines = int(_get(d, "ai_suffix_lines", s.ai_suffix_lines))
        except (TypeError, ValueError):
            s.ai_suffix_lines = 3
        try:
            s.ai_prefix_max_chars = int(
                _get(d, "ai_prefix_max_chars", s.ai_prefix_max_chars))
        except (TypeError, ValueError):
            s.ai_prefix_max_chars = 1200
        try:
            s.ai_suffix_max_chars = int(
                _get(d, "ai_suffix_max_chars", s.ai_suffix_max_chars))
        except (TypeError, ValueError):
            s.ai_suffix_max_chars = 600
        s.ai_insert_mode = str(_get(d, "ai_insert_mode", s.ai_insert_mode))
        s.ai_scope_limited = bool(_get(d, "ai_scope_limited", s.ai_scope_limited))
        s.minimize_to_tray = bool(_get(d, "minimize_to_tray", s.minimize_to_tray))
        s.toggle_hotkey = str(_get(d, "toggle_hotkey", s.toggle_hotkey))
        # 性能监控已移除（v1.6.3）。老 settings.json 里的 perf_* 键直接忽略，
        # 不读也不报错 —— 下次保存时会自然消失。
        mode = str(_get(d, "match_mode", s.match_mode))
        s.match_mode = mode if mode in (MATCH_WHITELIST, MATCH_ALL) else MATCH_WHITELIST

        rules = d.get("punctuation_rules")
        s.punctuation_rules = [PunctuationRule.from_dict(x) for x in rules] if isinstance(rules, list) else []
        acts = d.get("quick_actions")
        s.quick_actions = [QuickAction.from_dict(x) for x in acts] if isinstance(acts, list) else []
        apps = d.get("app_items")
        s.app_items = [AppItem.from_dict(x) for x in apps] if isinstance(apps, list) else []
        return s
