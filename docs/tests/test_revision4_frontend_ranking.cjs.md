# `tests/test_revision4_frontend_ranking.cjs`

使用 Node 的 `vm` 运行共享页面文件 `webpage/app.js` 中实际交付给浏览器的 `rankRoundRobin()`，验证未结束场次、获胜局、小分、并列选手间重算及姓名排序。也验证未填完整的局不计入排名。运行：`node tests/test_revision4_frontend_ranking.cjs`。
