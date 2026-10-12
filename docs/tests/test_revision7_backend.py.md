# `tests/test_revision7_backend.py`

使用临时工作目录验证 revision-7 的自定义赛事文件名和共享 JPG 图标。

- 省略 `slug` 时自动生成文件名；明确传入时，空值、类型错误、路径分隔符、非法字符及系统保留名称均拒绝，不会悄然退回自动命名。
- 合法自定义文件名写入对应目录；重复文件名返回 409 且保留原记录；创建后禁止重命名。
- 通过实际本地 HTTP 请求检查 `/icon.jpg` 的字节、`image/jpeg`、长度和 `nosniff` 响应头；后台页面三个入口均指向同一图标。
- 静态路由不开放任意文件或目录；创建 API 明确空文件名返回 400。

测试只初始化临时数据库，不执行 Git，也不修改实际赛事数据。运行：`python -B -m unittest discover -s tests -p test_revision7_backend.py -v`。
