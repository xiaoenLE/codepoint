"""码点助手 —— 中文标点自动转英文 + 代码片段快捷补全（Windows 桌面工具）。

分层约定（高内聚 / 低耦合）：

    core/          纯数据与配置，零外部依赖，不 import 其它层
    services/      业务逻辑，只依赖 core + ports 抽象接口，**不碰 Win32**
    platform_win/  一切 Win32/ctypes 细节，只依赖 core
    ui/            tkinter 界面，通过 bridge 与后台通信
    app.py         组装根：唯一负责把各层拼起来的地方

依赖方向严格单向：  ui ──▶ services ──▶ core
                              ▲
                     platform_win ──┘（通过 ports 实现接口）
"""

__version__ = "1.6.13"
__author__ = "xiaoen"
APP_NAME = "CodePoint"                  # 配置目录 / 互斥体名（ASCII，避免路径编码问题）
APP_DISPLAY_NAME = "码点助手"            # 界面上显示的名字
