# `generator/__init__.py`

以延迟导入方式导出 `build_site` 兼容检查入口，避免运行 `python -m generator.build` 时重复导入命令行模块。该入口初始化旧数据并验证 `webpage/` 文件；后台保存和网站发布无需调用。
