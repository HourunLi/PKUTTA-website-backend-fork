# `generator/build.py`

`build_site(root=None)` 保留旧代码和命令行的兼容入口，返回可直接发布的 `webpage/` 路径。它调用 `Database` 初始化已有数据库，必要时迁移旧根数据库并规范化旧 JSON，再检查 `webpage/index.html`、`webpage/app.js`、`webpage/style.css` 三个共享页面文件是否存在；缺失时抛出 `FileNotFoundError`。

该函数不生成页面、不复制数据库、不创建赛事或项目汇总索引，也不删除后台临时空比分。后台保存直接增量修改 `webpage/database/`，发布只需同步 `webpage/`，无需调用本入口。兼容检查命令仍为 `python -m generator.build`，可通过 `--root` 指定项目根目录。
