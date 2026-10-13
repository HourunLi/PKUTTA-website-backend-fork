# `tests/test_revision4_frontend_ranking.cjs`

使用 Node 的 `vm` 运行共享页面文件 `webpage/app.js` 中保留的历史 `rankRoundRobin()`，验证未结束场次、获胜局、小分、并列选手间重算及姓名排序。也验证未填完整的局不计入排名。revision-8 后页面直接读取后台持久化排名，该函数仅为历史调用保留；当前循环赛页面使用后台排名的测试在 `test_revision8_frontend.cjs`。运行：`node tests/test_revision4_frontend_ranking.cjs`。
