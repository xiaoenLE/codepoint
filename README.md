# 码点助手（CodePoint）

码点助手是面向 Windows 的中文输入辅助工具。在指定程序中输入代码时，它会把中文全角标点转成英文半角标点，也支持自定义代码片段和可选的 AI 补全。

## 下载

支持 Windows 10 / 11 x64。安装版安装后可从开始菜单启动，便携版可直接运行。

- [GitHub 下载](https://github.com/xiaoenLE/codepoint/releases)
- [Gitea 下载](http://124.221.183.24:3000/xiaoen/codepoint/releases)

## 快速上手

1. 在「程序列表」中添加需要生效的编辑器或编程软件。
2. 回到目标程序输入代码，中文标点会自动转为半角。使用自定义片段时，输入触发词后按 `Tab` 补全。
3. 按 `Ctrl+Alt+A` 暂停或恢复。需要 AI 补全时，在「常规设置」中配置 AI 服务。

## 从源码运行

使用带 tkinter 的 Python 3.9 或更新版本，在项目目录运行 `python main.py`。

## 许可

个人使用与二次开发均可。作者不对使用后果作担保。
