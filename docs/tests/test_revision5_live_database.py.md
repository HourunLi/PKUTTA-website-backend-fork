# `tests/test_revision5_live_database.py`

验证 `webpage/database` 是后台与公开页面共用的唯一数据库。测试比较文件内容与修改时间，确保普通比分编辑只写目标阶段的 `data.json`，赛事改名只写对应 `meta.json`。

迁移用例同时构造旧原始数据库和过期公开副本，验证以原始比赛记录为准，保留包括临时空比分局在内的所有记录，并删除原目录和重复汇总索引。另验证已部署目录只依赖自身 HTML、CSS、JavaScript 与 JSON，再次整理不会损坏数据；非法赛事或项目标识不能越过数据库路径。

运行：`python -m unittest discover -s tests -p test_revision5_live_database.py -v`。
