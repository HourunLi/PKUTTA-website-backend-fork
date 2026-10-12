# `tests/test_revision5_group_schedule.py`

验证小组预定时间和预定桌号的保存与单向同步：抽签前可设置；设置后覆盖本组所有场次；单场实际时间、桌号修改不影响小组计划；重新抽签保留计划并同步到新场次。另覆盖文字字段清空、部分字段修改、无效字段和无效小组 ID，以及 HTTP 路由将正整数小组 ID 传给数据库方法。

测试使用临时目录内独立数据库，运行命令为 `python -B -m unittest discover -s tests -p test_revision5_group_schedule.py -v`。
