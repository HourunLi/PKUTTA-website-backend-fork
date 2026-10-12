# `tests/test_revision5_frontend.cjs`

使用 Node `vm` 执行实际共享页面 `webpage/app.js`，验证公开页直接读取分层数据库且使用 `no-store`，不读取赛事、项目汇总索引。覆盖中文路径编码、小组计划读取、对称单元格打开同一场次、比分反转、完赛高亮整格、矩阵无排名列，以及出线和结果“选手”列名。

另验证公开详情不展示不完整临时局，后台计划按钮、编辑表单与 PATCH 请求提交时间、桌号及裁判。运行：`node tests/test_revision5_frontend.cjs`。revision 6 的独立排名表、弃权、叶子编号、弹窗按钮关闭及 Submit 行为另由 `test_revision6_frontend.cjs` 覆盖；正方形尺寸和手机居中另由浏览器检查。
