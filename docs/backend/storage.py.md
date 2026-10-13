# `backend/storage.py`

## 职责

`Database` 是 JSON 数据的唯一写入入口。`Database.base` 指向 `webpage/database/`；赛事、项目、阶段分别写入其下的 `<赛事>/meta.json`、`<赛事>/<项目>/meta.json`、`<赛事>/<项目>/<阶段>/{metadata,data}.json`。根 `index.json` 仅保存赛事英文标识列表。公开页直接按此层级读取，不需要赛事、项目的汇总索引或另一份数据副本。

每次修改只写受影响的文件，JSON 内容未变时保留其修改时间。写入使用同目录临时文件与 `os.replace`，避免中途截断 JSON；对应的 `docs/webpage/database/` 说明在保存时更新。后台保存后直接同步 `webpage/` 即可发布，无需构建或复制整库。

`DataError` 表示可以返回给管理员的校验或查找错误，并携带 HTTP 状态码。`server.py` 为数据库读取与写入加锁，再调用此文件的方法。

创建赛事时可以省略 `slug`，自动生成 `competition-开始日期-随机后缀`。勾选自定义文件名的前端会明确传入 `slug`：必须以小写英文字母开头，其后只允许小写字母、数字及分隔用连字符；空值、路径字符、扩展名、类型错误和 `con`、`nul`、`com1` 等系统保留名称均拒绝，不会改用自动命名。已有目录或索引同名返回 409。创建后 `slug` 不可修改，名称等其他信息仍可更新。

初始化时兼容旧目录：旧根 `database/` 是原始数据时，将它移动到 `webpage/database/`，替换旧生成的公开副本，保留原始库中后台暂存的空比分。已规范化的两份不同实时库会报错而不自动合并。初始化还将旧赛事索引转为标识列表，并将旧项目目录 `<赛事>-<性别>-<形式>` 改为 `<性别>-<形式>`，同步赛事和项目元数据及文档目录；迁移前检查短名称冲突，也可接续中断的迁移。随后补齐小组及循环赛计划与场次裁判，生成并保存已完赛小组及循环赛的排名和排名抽签排列，派生旧淘汰赛的铜牌赛及结果，并移除赛事、项目层的重复汇总索引。循环赛升级保持原始选手顺序、场次顺序、比分和场次信息。

每个小组加淘汰赛项目的全部阶段规范化后，初始化调用现有 `_sync_knockout_after_group_change` 同步出线元数据，以免保留旧规则的虚拟种子或名单。新旧资格成员集合相同则只更新级别与顺序，已有签表、比分保留；新排名改变资格成员时清空旧签表与比分，等待管理员重新抽签。

## 数据模型

- 赛事元数据：`slug`、`name`、`start_date`、`end_date`、`venue`、`events`。日期为 `YYYY-MM-DD`。
- 项目元数据：`slug`、`discipline` (`men`/`women`/`mixed`)、`format` (`single`/`double`/`team`)、`name`、`schedule` (`group-knockout`/`knockout`/`round-robin`)、`stages`。
- 阶段元数据：`type` 和 `entrants`；选手条目为 `{participant,seed}`。小组和循环赛 CSV 种子级别是非负整数，独立淘汰赛 CSV 另接受 `-1`。淘汰赛正数表示种子（数字越大优先级越高），`0` 表示非种子小组第一，`-1` 表示其余选手。淘汰赛轮空位置为 `{kind:"bye",members:[],team:"",seed:-1}`。
- `participant` 为 `{kind,members,team}`；`kind` 是 `player`、`pair`、`team`、`undecided` 或 `bye`。
- 场次为 `{id,a,b,finished,games,table,time,referee}`。循环赛与小组赛的 `a`/`b` 是该选手列表中的从零开始的下标；淘汰赛的 `a`/`b` 是选手对象。`games` 是 `[A得分,B得分]` 的列表，任一得分可为非负整数、`"wo"` 或 `null`；`table`/`time` 是文本或 `null`，`referee` 初始为空字符串。淘汰赛场次还含从零开始的 `round`、`index`。
- 小组赛 `data`：`drawn`、`groups`；每组包含 `id`、`size`、`qualifiers`、`scheduled_time`、`scheduled_table`、`scheduled_referee`、`participants`、`matches`、`standings`、`ranking_draw_order`。三个计划字段初始为空字符串；`standings` 在未完赛时为 `null`。`ranking_draw_order` 是首次完赛生成的选手索引排列，保存后用于稳定同分抽签。未抽签时选手为 `undecided`，但场次结构已生成。
- 淘汰赛 `data`：`drawn`、`rounds`、`slots`、`matches`、`bronze_match`、`results`。主赛的 `matches` 保持完整二叉签表；独立的 `bronze_match` 场次 ID 为 `bronze`，至少 2 轮才存在，1 轮时为 `null`。`results` 未全部决出时为 `null`，完赛后为最多 8 条 `{rank,participant}`。未抽签时保留完整树，所有位置为 `undecided`。
- 循环赛 `data`：`count`、`participants`、`matches`、`standings`、`ranking_draw_order`；创建项目时直接生成所有两两对阵。排名与小组赛使用同一数据结构：`standings` 包含按名次排序的 `order` 与 `rows`，每行的 `index` 指向原始 `participants`。没有单独复制的 `results` 字段，页面从排名和选手列表生成最终结果。未全部完赛时 `standings` 为 `null`；`ranking_draw_order` 首次完赛生成，此后保持固定。

## CSV 校验

`parse_entrants(csv_text, format_name, *, allow_negative_one=False)` 接收浏览器解码后的文本，使用 Python 的 `csv` 模块解析引号、逗号、BOM 和常见中英文表头。若文本含替换字符 `�`，拒绝创建并提示使用 UTF-8 或 GB18030 重新上传。单打需三列（姓名、队伍、种子级别）；双打需四列（选手一、选手二、队伍、种子级别）；团体至少三列（队伍、种子级别、至少一名成员）。空值、重复条目、非整数种子级别均报错；默认只接受非负整数。`create_event` 仅在独立 `knockout` 赛程开启 `allow_negative_one`，额外允许 `-1`，仍拒绝 `-2` 等更小负数。小组和循环赛不允许负级别。

`create_event` 检查分组总人数等于 CSV 人数，并要求淘汰赛人数满足 `2^(轮数-1) < 人数 <= 2^轮数`，以便不足满签时轮空。原需求所写 `2^d-1 < 人数` 与其轮空要求冲突，此处采用能容纳轮空的区间。

## 赛程联动

`update_group(competition, event, group_id, payload)` 接收正整数小组 ID 与至少一个 `scheduled_time`、`scheduled_table`、`scheduled_referee` 文字字段，去除首尾空白，空字符串表示清空。每个提交字段直接覆盖本组全部场次对应的 `time`、`table`、`referee`；未提交字段保留。单独编辑场次实际时间、桌号、裁判不会反向修改小组计划。计划在抽签前可设置，重新抽签时保留并复制到新场次。

小组赛重新抽签会清空后续淘汰赛抽签与赛果。小组场次每次更改后，重新计算并保存 `standings`，按其 `order` 取前 `qualifiers` 名；未全部结束时淘汰赛不能抽签。原小组报名中正种子人数与小组数的较小值为 `n_s`：第 `g` 组第一名在 `g <= n_s` 时获得 `n_s + 1 - g`，其他组第一为 `0`，其余出线选手为 `-1`。出线**选手集合**改变时清空原淘汰赛对阵与赛果。仅组内排名改变而出线人选不变时，更新淘汰赛元数据中的级别并保留已有签表、比分；再次主动抽签才使用新级别。淘汰赛场次更改后调用 `logic.refresh_knockout_bracket` 推导后续轮次、铜牌赛和排名；更早场次改变选手时，后续不适用的比分会清空，赛程与裁判仍保留。

初始化会持久化旧淘汰赛缺少的铜牌赛及排名字段。初始化之后若外部再写入旧 JSON，`get_event()` 只在返回值中派生而不改写；`mutate_match()` 在查找场次前也先派生，所以旧抽签可以直接通过 `/stages/knockout/matches/bronze` 路由录入铜牌赛。保存时写入派生字段，已有主赛抽签和比分继续保留。

小组与独立循环赛排名均由 `logic.group_standings` 按积分、同分者内部积分、局胜负比、球分胜负比、抽签生成，同时保存各比较步骤。所有比值中的 `wo` 按 `0` 计算。只有全阶段完赛才生成 `standings`；从完赛改回未完赛会清空展示排名，保留 `ranking_draw_order`，再次完赛仍使用原排列。计划或场次信息修改、读取和重启不会改变抽签名次。

`_refresh_round_robin_standings` 在创建循环赛、启动旧数据升级、每次局比分添加/编辑/删除、场次保存时刷新排名。它要求场次数为 `n*(n-1)/2`，并且完整覆盖选手列表中每一对不同选手，无重复或非法索引；只有所有这些场次均标记完赛后才调用排名算法。漏场次、重复场次、无效引用或任何未完赛场次都保持 `standings: null`。单名选手的循环赛没有场次，创建时即可生成第一名。有效的历史抽签排列会保留，无效排列首次刷新时修复；整个过程不排序或改写 `participants`、`matches`。

`add_game` 和 `edit_game` 均要求提交 `a`、`b`，允许非负整数、`null` 或大小写不限且可带空白的 `wo`，保存时统一为 `"wo"`。只填一方 `wo` 且另一方为空时，空分立即规范为 `0`；普通数值不允许平分，`wo:wo` 允许。含普通空值的局暂存且不统计，`delete_game` 直接删局，`edit_match` 保存时移除普通未完整局。`edit_match` 还接受实际 `table`、`time`、`referee`。是否完赛由管理员明确设置，普通完赛须至少一局完整且胜局数不等；单方弃权由对侧胜，双方弃权是合法完赛且无胜者。后续修改使普通胜负不明时自动恢复未完赛。

淘汰赛双方弃权转为已知轮空继续传播，避免后续轮次永远未决；结果表不为这个轮空分配选手名次。单方弃权仅判该场负，不取消其后续铜牌赛资格。

唯一数据库内可以保留尚未保存的临时空比分，公开页由共享页面代码过滤这些局，不为公示创建另一份数据库。兼容检查入口也不删除临时局。
