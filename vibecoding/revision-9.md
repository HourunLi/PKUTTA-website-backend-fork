循环赛也像小组赛卡片那样允许在外部和每一场设置预定时间和台号。

整理数据库和 url。url 中不要显示 /competitions，/events。生成 event 名字时，不要再带上前面的比赛代码。比如说现在女双比赛的 url 是 ``/competitions/xsb26/events/xsb26-women-double``，整理之后应该变成 ``/xsb26/women-double``。对维护数据库的代码、数据库本身以及前端都做以上整理，保持一致性。