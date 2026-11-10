# -*- coding: utf-8 -*-
"""MultiRDP Web UI 包。

把 server.py 放在包里（而不是裸模块）是为了让 PyInstaller 的静态分析能顺着
`from webui import server` 找到它，从而正确打进单文件 exe。
"""
