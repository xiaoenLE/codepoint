"""services —— 业务逻辑层。

铁律：**本包不得 import platform_win，也不得 import ui。**
外部世界通过 ports.py 里的 Protocol 注入进来，因此这一层可以在
没有 Windows、没有界面的环境里跑单元测试。
"""
