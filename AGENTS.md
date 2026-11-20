# CodePoint 打包约定

- 打包新版本前，按变更更新 `codepoint/__init__.py` 的 `__version__`，并同步 `build/version_info.txt` 中的 Windows 版本信息。重复构建同一版本时沿用原版本号。
- 安装包和对外交付的便携版文件名须带版本号，例如 `CodePoint-1.6.5-Setup-x64.exe`。交付时明确写出版本号和对应文件。
