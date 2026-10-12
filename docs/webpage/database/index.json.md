# `webpage/database/index.json`

赛事索引，保存赛事英文标识列表。

这是后台与公开页面共用的唯一数据文件，采用 UTF-8 JSON。后台直接增量保存；发布时同步 `webpage/` 即可，无需生成或构建。
